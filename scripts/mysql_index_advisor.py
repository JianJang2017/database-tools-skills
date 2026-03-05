#!/usr/bin/env python3
"""
MySQL 索引性能分析与优化建议器
功能：检测冗余索引、未使用索引、全表扫描、慢查询分析、缓冲池命中率、表碎片
兼容 MySQL 5.7 和 8.0+
"""

import argparse
import json
import os
import sys
from datetime import datetime

try:
    import pymysql
    from pymysql.cursors import DictCursor
except ImportError:
    pymysql = None
    DictCursor = None

# 复用 mysql_inspector 的连接逻辑
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from mysql_inspector import get_connection


# ============================================================
# 版本检测
# ============================================================

def get_mysql_version(conn):
    """返回 MySQL 主版本号，如 (8, 0) 或 (5, 7)"""
    with conn.cursor() as cur:
        cur.execute("SELECT VERSION() AS ver")
        ver_str = cur.fetchone()['ver']
        parts = ver_str.split('.')
        return (int(parts[0]), int(parts[1]))


# ============================================================
# 分析查询
# ============================================================

# MySQL 8.0+ 使用 sys.schema_redundant_indexes
SQL_REDUNDANT_INDEXES_80 = """
SELECT
    table_schema,
    table_name,
    redundant_index_name,
    redundant_index_columns,
    dominant_index_name,
    dominant_index_columns,
    subpart_exists,
    sql_drop_index
FROM sys.schema_redundant_indexes
WHERE table_schema = %(schema)s
ORDER BY table_name;
"""

# MySQL 5.7 降级: 基于 information_schema.STATISTICS 手动分析冗余索引
SQL_REDUNDANT_INDEXES_57 = """
SELECT
    a.TABLE_SCHEMA AS table_schema,
    a.TABLE_NAME AS table_name,
    a.INDEX_NAME AS redundant_index_name,
    GROUP_CONCAT(DISTINCT a.COLUMN_NAME ORDER BY a.SEQ_IN_INDEX) AS redundant_index_columns,
    b.INDEX_NAME AS dominant_index_name,
    GROUP_CONCAT(DISTINCT b.COLUMN_NAME ORDER BY b.SEQ_IN_INDEX) AS dominant_index_columns
FROM information_schema.STATISTICS a
JOIN information_schema.STATISTICS b
    ON a.TABLE_SCHEMA = b.TABLE_SCHEMA
    AND a.TABLE_NAME = b.TABLE_NAME
    AND a.INDEX_NAME != b.INDEX_NAME
    AND a.COLUMN_NAME = b.COLUMN_NAME
    AND a.SEQ_IN_INDEX = b.SEQ_IN_INDEX
WHERE a.TABLE_SCHEMA = %(schema)s
    AND a.INDEX_NAME != 'PRIMARY'
GROUP BY a.TABLE_SCHEMA, a.TABLE_NAME, a.INDEX_NAME, b.INDEX_NAME
HAVING COUNT(*) = (
    SELECT COUNT(*)
    FROM information_schema.STATISTICS s
    WHERE s.TABLE_SCHEMA = a.TABLE_SCHEMA
        AND s.TABLE_NAME = a.TABLE_NAME
        AND s.INDEX_NAME = a.INDEX_NAME
)
AND COUNT(*) < (
    SELECT COUNT(*)
    FROM information_schema.STATISTICS s
    WHERE s.TABLE_SCHEMA = b.TABLE_SCHEMA
        AND s.TABLE_NAME = b.TABLE_NAME
        AND s.INDEX_NAME = b.INDEX_NAME
)
ORDER BY a.TABLE_NAME;
"""

SQL_UNUSED_INDEXES = """
SELECT
    object_schema AS table_schema,
    object_name AS table_name,
    index_name,
    count_star AS rows_accessed
FROM performance_schema.table_io_waits_summary_by_index_usage
WHERE object_schema = %(schema)s
    AND index_name IS NOT NULL
    AND index_name != 'PRIMARY'
    AND count_star = 0
ORDER BY object_name, index_name;
"""

SQL_FULL_TABLE_SCAN = """
SELECT
    object_schema AS table_schema,
    object_name AS table_name,
    count_read AS total_reads,
    count_fetch AS rows_fetched
FROM performance_schema.table_io_waits_summary_by_table
WHERE object_schema = %(schema)s
    AND count_read > 0
ORDER BY count_fetch DESC
LIMIT 20;
"""

SQL_MISSING_FK_INDEXES = """
SELECT
    tc.TABLE_SCHEMA AS table_schema,
    tc.TABLE_NAME AS table_name,
    kcu.COLUMN_NAME AS column_name,
    tc.CONSTRAINT_NAME AS constraint_name
FROM information_schema.TABLE_CONSTRAINTS tc
JOIN information_schema.KEY_COLUMN_USAGE kcu
    ON tc.CONSTRAINT_NAME = kcu.CONSTRAINT_NAME
    AND tc.TABLE_SCHEMA = kcu.TABLE_SCHEMA
    AND tc.TABLE_NAME = kcu.TABLE_NAME
WHERE tc.CONSTRAINT_TYPE = 'FOREIGN KEY'
    AND tc.TABLE_SCHEMA = %(schema)s
    AND NOT EXISTS (
        SELECT 1
        FROM information_schema.STATISTICS s
        WHERE s.TABLE_SCHEMA = tc.TABLE_SCHEMA
            AND s.TABLE_NAME = tc.TABLE_NAME
            AND s.COLUMN_NAME = kcu.COLUMN_NAME
            AND s.SEQ_IN_INDEX = 1
    )
ORDER BY tc.TABLE_NAME;
"""

SQL_SLOW_QUERIES = """
SELECT
    DIGEST AS query_digest,
    SUBSTRING(DIGEST_TEXT, 1, 200) AS query,
    COUNT_STAR AS calls,
    ROUND(SUM_TIMER_WAIT / 1000000000000, 2) AS total_time_sec,
    ROUND(AVG_TIMER_WAIT / 1000000000000, 4) AS avg_time_sec,
    ROUND(MAX_TIMER_WAIT / 1000000000000, 4) AS max_time_sec,
    SUM_ROWS_EXAMINED AS rows_examined,
    SUM_ROWS_SENT AS rows_sent,
    SUM_NO_INDEX_USED AS no_index_used,
    SUM_NO_GOOD_INDEX_USED AS no_good_index_used
FROM performance_schema.events_statements_summary_by_digest
WHERE SCHEMA_NAME = %(schema)s
    AND COUNT_STAR > 5
    AND DIGEST_TEXT NOT LIKE '%%performance_schema%%'
ORDER BY AVG_TIMER_WAIT DESC
LIMIT 20;
"""

SQL_INNODB_BUFFER_POOL = """
SELECT
    FORMAT(HIT_RATE, 2) AS hit_rate,
    POOL_SIZE AS pool_pages,
    FREE_BUFFERS AS free_pages,
    DATABASE_PAGES AS data_pages,
    OLD_DATABASE_PAGES AS old_pages,
    MODIFIED_DB_PAGES AS dirty_pages,
    PAGES_MADE_YOUNG AS pages_made_young,
    PAGES_NOT_MADE_YOUNG AS pages_not_made_young
FROM information_schema.INNODB_BUFFER_POOL_STATS;
"""

SQL_TABLE_FRAGMENTATION = """
SELECT
    TABLE_SCHEMA AS table_schema,
    TABLE_NAME AS table_name,
    ENGINE AS engine,
    TABLE_ROWS AS estimated_rows,
    CONCAT(ROUND(DATA_LENGTH / 1024 / 1024, 2), ' MB') AS data_size,
    CONCAT(ROUND(INDEX_LENGTH / 1024 / 1024, 2), ' MB') AS index_size,
    CONCAT(ROUND(DATA_FREE / 1024 / 1024, 2), ' MB') AS data_free,
    CASE WHEN DATA_LENGTH > 0
        THEN ROUND(100.0 * DATA_FREE / DATA_LENGTH, 1)
        ELSE 0
    END AS fragmentation_pct
FROM information_schema.TABLES
WHERE TABLE_SCHEMA = %(schema)s
    AND TABLE_TYPE = 'BASE TABLE'
    AND DATA_FREE > 0
ORDER BY DATA_FREE DESC
LIMIT 20;
"""

SQL_CONNECTION_STATS = """
SELECT
    @@hostname AS hostname,
    @@port AS port,
    @@version AS version,
    @@innodb_buffer_pool_size AS buffer_pool_size,
    (SELECT COUNT(*) FROM information_schema.PROCESSLIST) AS active_connections,
    (SELECT VARIABLE_VALUE FROM performance_schema.global_status
     WHERE VARIABLE_NAME = 'Uptime') AS uptime_seconds;
"""


# ============================================================
# 分析逻辑
# ============================================================

def analyze_redundant_indexes(conn, schema):
    version = get_mysql_version(conn)
    with conn.cursor() as cur:
        if version >= (8, 0):
            try:
                cur.execute(SQL_REDUNDANT_INDEXES_80, {'schema': schema})
                return cur.fetchall()
            except Exception:
                pass
        # 5.7 降级
        cur.execute(SQL_REDUNDANT_INDEXES_57, {'schema': schema})
        return cur.fetchall()


def analyze_unused_indexes(conn, schema):
    with conn.cursor() as cur:
        try:
            cur.execute(SQL_UNUSED_INDEXES, {'schema': schema})
            return cur.fetchall()
        except Exception:
            return None  # performance_schema 未启用


def analyze_full_table_scans(conn, schema):
    with conn.cursor() as cur:
        try:
            cur.execute(SQL_FULL_TABLE_SCAN, {'schema': schema})
            return cur.fetchall()
        except Exception:
            return None


def analyze_missing_fk_indexes(conn, schema):
    with conn.cursor() as cur:
        cur.execute(SQL_MISSING_FK_INDEXES, {'schema': schema})
        return cur.fetchall()


def analyze_slow_queries(conn, schema):
    with conn.cursor() as cur:
        try:
            cur.execute(SQL_SLOW_QUERIES, {'schema': schema})
            return cur.fetchall()
        except Exception:
            return None


def analyze_buffer_pool(conn):
    with conn.cursor() as cur:
        try:
            cur.execute(SQL_INNODB_BUFFER_POOL)
            return cur.fetchall()
        except Exception:
            return None


def analyze_fragmentation(conn, schema):
    with conn.cursor() as cur:
        cur.execute(SQL_TABLE_FRAGMENTATION, {'schema': schema})
        return cur.fetchall()


def analyze_connection_stats(conn):
    with conn.cursor() as cur:
        try:
            cur.execute(SQL_CONNECTION_STATS)
            return cur.fetchone()
        except Exception:
            return None


# ============================================================
# 报告生成
# ============================================================

def _section(title, level=2):
    return f"\n{'#' * level} {title}\n"


def generate_report(conn, schema):
    """生成完整的性能分析报告（Markdown 格式）"""
    version = get_mysql_version(conn)
    lines = []
    lines.append(f"# MySQL 索引与性能分析报告")
    lines.append(
        f"**数据库**: `{schema}` | **版本**: MySQL {version[0]}.{version[1]} | "
        f"**时间**: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
    )

    # ---- 服务器概览 ----
    lines.append(_section("服务器概览"))
    stats = analyze_connection_stats(conn)
    if stats:
        lines.append(f"- **主机**: {stats.get('hostname', 'N/A')}:{stats.get('port', 'N/A')}")
        lines.append(f"- **版本**: {stats.get('version', 'N/A')}")
        lines.append(f"- **活跃连接**: {stats.get('active_connections', 'N/A')}")
        buf_size = stats.get('buffer_pool_size', 0)
        if buf_size:
            lines.append(f"- **InnoDB 缓冲池**: {int(buf_size) // 1024 // 1024} MB")
        uptime = stats.get('uptime_seconds', 0)
        if uptime:
            days = int(uptime) // 86400
            hours = (int(uptime) % 86400) // 3600
            lines.append(f"- **运行时间**: {days}天 {hours}小时")
        lines.append("")

    # ---- InnoDB 缓冲池 ----
    lines.append(_section("InnoDB 缓冲池命中率"))
    bp = analyze_buffer_pool(conn)
    if bp:
        for pool in bp:
            lines.append(f"- **命中率**: {pool.get('hit_rate', 'N/A')}/1000")
            lines.append(f"- **总页数**: {pool.get('pool_pages', 'N/A')}")
            lines.append(f"- **空闲页**: {pool.get('free_pages', 'N/A')}")
            lines.append(f"- **数据页**: {pool.get('data_pages', 'N/A')}")
            lines.append(f"- **脏页**: {pool.get('dirty_pages', 'N/A')}")
    else:
        lines.append("无法获取缓冲池信息。")
    lines.append("")

    # ---- 冗余索引 ----
    lines.append(_section("冗余索引"))
    redundant = analyze_redundant_indexes(conn, schema)
    if redundant:
        lines.append("以下索引被其他索引完全覆盖:\n")
        lines.append("| 表 | 冗余索引 | 冗余列 | 覆盖索引 | 覆盖列 |")
        lines.append("|-----|----------|--------|----------|--------|")
        for r in redundant:
            lines.append(
                f"| {r['table_name']} | {r['redundant_index_name']} | "
                f"{r.get('redundant_index_columns', '')} | "
                f"{r['dominant_index_name']} | {r.get('dominant_index_columns', '')} |"
            )
    else:
        lines.append("没有发现冗余索引。")
    lines.append("")

    # ---- 未使用索引 ----
    lines.append(_section("未使用索引"))
    unused = analyze_unused_indexes(conn, schema)
    if unused is None:
        lines.append("**performance_schema 未启用**，无法分析未使用索引。")
    elif unused:
        lines.append("以下索引自服务器启动以来从未被访问:\n")
        lines.append("| 表 | 索引 | 访问行数 |")
        lines.append("|-----|------|----------|")
        for idx in unused:
            lines.append(f"| {idx['table_name']} | {idx['index_name']} | {idx['rows_accessed']} |")
    else:
        lines.append("没有发现未使用的索引。")
    lines.append("")

    # ---- 外键缺失索引 ----
    lines.append(_section("外键缺失索引"))
    missing = analyze_missing_fk_indexes(conn, schema)
    if missing:
        lines.append("以下外键列没有索引:\n")
        lines.append("| 表 | 外键列 | 约束名 |")
        lines.append("|-----|--------|--------|")
        for m in missing:
            lines.append(f"| {m['table_name']} | {m['column_name']} | {m['constraint_name']} |")
    else:
        lines.append("所有外键列都已建立索引。")
    lines.append("")

    # ---- 全表扫描 ----
    lines.append(_section("高频读取表"))
    scans = analyze_full_table_scans(conn, schema)
    if scans is None:
        lines.append("**performance_schema 未启用**，无法分析表访问。")
    elif scans:
        lines.append("| 表 | 总读取次数 | 扫描行数 |")
        lines.append("|-----|------------|----------|")
        for s in scans:
            lines.append(f"| {s['table_name']} | {s['total_reads']} | {s['rows_fetched']} |")
    else:
        lines.append("没有高频读取表。")
    lines.append("")

    # ---- 表碎片 ----
    lines.append(_section("表碎片检测"))
    frag = analyze_fragmentation(conn, schema)
    if frag:
        lines.append("以下表存在碎片空间（可通过 OPTIMIZE TABLE 回收）:\n")
        lines.append("| 表 | 引擎 | 行数 | 数据大小 | 索引大小 | 碎片空间 | 碎片率 |")
        lines.append("|-----|------|------|----------|----------|----------|--------|")
        for f in frag:
            lines.append(
                f"| {f['table_name']} | {f['engine']} | {f['estimated_rows']} | "
                f"{f['data_size']} | {f['index_size']} | {f['data_free']} | "
                f"{f['fragmentation_pct']}% |"
            )
    else:
        lines.append("没有发现严重的表碎片。")
    lines.append("")

    # ---- 慢查询 ----
    lines.append(_section("慢查询分析 (performance_schema)"))
    slow = analyze_slow_queries(conn, schema)
    if slow is None:
        lines.append("**performance_schema 未启用**，无法分析慢查询。")
    elif slow:
        lines.append("| 查询 | 调用次数 | 平均(s) | 最大(s) | 总时间(s) | 无索引 |")
        lines.append("|------|----------|---------|---------|-----------|--------|")
        for s in slow:
            query = str(s['query'])[:80].replace('\n', ' ').replace('|', '\\|')
            lines.append(
                f"| {query}... | {s['calls']} | {s['avg_time_sec']} | "
                f"{s['max_time_sec']} | {s['total_time_sec']} | {s['no_index_used']} |"
            )
    else:
        lines.append("没有发现慢查询。")
    lines.append("")

    return "\n".join(lines)


# ============================================================
# 优化 DDL 生成
# ============================================================

def generate_optimization_ddl(conn, schema):
    """基于分析结果生成优化 DDL 脚本"""
    lines = []
    lines.append(f"-- ============================================================")
    lines.append(f"-- MySQL 索引优化脚本")
    lines.append(f"-- 数据库: {schema}")
    lines.append(f"-- 生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    lines.append(f"-- ============================================================\n")

    has_changes = False

    # 外键添加索引
    missing = analyze_missing_fk_indexes(conn, schema)
    if missing:
        has_changes = True
        lines.append("-- ============================================================")
        lines.append("-- 1. 为外键列添加缺失的索引")
        lines.append("-- ============================================================\n")
        for m in missing:
            idx_name = f"idx_{m['table_name']}_{m['column_name']}"
            lines.append(f"-- 外键约束: {m['constraint_name']}")
            lines.append(
                f"ALTER TABLE `{schema}`.`{m['table_name']}` "
                f"ADD INDEX `{idx_name}` (`{m['column_name']}`);\n"
            )

    # 删除冗余索引
    redundant = analyze_redundant_indexes(conn, schema)
    if redundant:
        has_changes = True
        lines.append("-- ============================================================")
        lines.append("-- 2. 删除冗余索引 (被其他索引覆盖)")
        lines.append("-- ============================================================\n")
        for r in redundant:
            drop_sql = r.get('sql_drop_index')
            if drop_sql:
                lines.append(f"-- 覆盖索引: {r['dominant_index_name']} ({r.get('dominant_index_columns', '')})")
                lines.append(f"{drop_sql};\n")
            else:
                lines.append(
                    f"-- 冗余: {r['redundant_index_name']} ({r.get('redundant_index_columns', '')})")
                lines.append(
                    f"-- 覆盖: {r['dominant_index_name']} ({r.get('dominant_index_columns', '')})")
                lines.append(
                    f"ALTER TABLE `{schema}`.`{r['table_name']}` "
                    f"DROP INDEX `{r['redundant_index_name']}`;\n"
                )

    # 删除未使用索引
    unused = analyze_unused_indexes(conn, schema)
    if unused:
        has_changes = True
        lines.append("-- ============================================================")
        lines.append("-- 3. 删除未使用的索引 (请确认后再执行)")
        lines.append("-- ============================================================\n")
        for idx in unused:
            lines.append(
                f"-- 表: {idx['table_name']}, 索引: {idx['index_name']}, "
                f"访问行数: {idx['rows_accessed']}"
            )
            lines.append(
                f"ALTER TABLE `{schema}`.`{idx['table_name']}` "
                f"DROP INDEX `{idx['index_name']}`;\n"
            )

    # 表碎片整理
    frag = analyze_fragmentation(conn, schema)
    high_frag = [f for f in frag if f['fragmentation_pct'] and float(f['fragmentation_pct']) > 10]
    if high_frag:
        has_changes = True
        lines.append("-- ============================================================")
        lines.append("-- 4. 表碎片整理 (碎片率 > 10%)")
        lines.append("-- ============================================================\n")
        for f in high_frag:
            lines.append(
                f"-- 表: {f['table_name']}, 碎片率: {f['fragmentation_pct']}%, "
                f"碎片空间: {f['data_free']}"
            )
            lines.append(f"OPTIMIZE TABLE `{schema}`.`{f['table_name']}`;\n")

    if not has_changes:
        lines.append("-- 恭喜！当前没有发现需要优化的索引问题。")

    return "\n".join(lines)


# ============================================================
# CLI
# ============================================================

def main():
    parser = argparse.ArgumentParser(description='MySQL 索引与性能分析器')

    # 连接参数
    conn_group = parser.add_argument_group('连接参数')
    conn_group.add_argument('--dsn', help='完整数据库连接字符串')
    conn_group.add_argument('--host', '-H', help='数据库主机 (MYSQL_HOST)')
    conn_group.add_argument('--port', '-p', help='数据库端口 (MYSQL_PORT)')
    conn_group.add_argument('--user', '-U', help='数据库用户 (MYSQL_USER)')
    conn_group.add_argument('--password', '-W', help='数据库密码 (MYSQL_PWD)')
    conn_group.add_argument('--dbname', '-d', help='数据库名称 (MYSQL_DATABASE)')
    conn_group.add_argument('--env-file', help='.env 文件路径')

    # 操作子命令
    sub = parser.add_subparsers(dest='command', help='可用命令')

    # report
    p_report = sub.add_parser('report', help='生成完整性能分析报告')
    p_report.add_argument('--schema', '-s', required=True, help='数据库名称')
    p_report.add_argument('--output', '-o', help='报告输出文件')

    # optimize
    p_optimize = sub.add_parser('optimize', help='生成优化 DDL 脚本')
    p_optimize.add_argument('--schema', '-s', required=True, help='数据库名称')
    p_optimize.add_argument('--output', '-o', help='DDL 输出文件')

    # 单项分析命令
    p_unused = sub.add_parser('unused-indexes', help='查看未使用索引')
    p_unused.add_argument('--schema', '-s', required=True)

    p_missing = sub.add_parser('missing-fk-indexes', help='查看外键缺失索引')
    p_missing.add_argument('--schema', '-s', required=True)

    p_frag = sub.add_parser('fragmentation', help='查看表碎片')
    p_frag.add_argument('--schema', '-s', required=True)

    p_slow = sub.add_parser('slow-queries', help='查看慢查询')
    p_slow.add_argument('--schema', '-s', required=True)

    p_buffer = sub.add_parser('buffer-pool', help='查看 InnoDB 缓冲池状态')

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        sys.exit(1)

    try:
        conn = get_connection(args)
    except Exception as e:
        print(f"连接数据库失败: {e}", file=sys.stderr)
        sys.exit(1)

    try:
        if args.command == 'report':
            report = generate_report(conn, args.schema)
            if args.output:
                with open(args.output, 'w') as f:
                    f.write(report)
                print(f"报告已写入: {args.output}")
            else:
                print(report)

        elif args.command == 'optimize':
            ddl = generate_optimization_ddl(conn, args.schema)
            if args.output:
                with open(args.output, 'w') as f:
                    f.write(ddl)
                print(f"优化脚本已写入: {args.output}")
            else:
                print(ddl)

        elif args.command == 'unused-indexes':
            results = analyze_unused_indexes(conn, args.schema)
            if results is None:
                print("performance_schema 未启用", file=sys.stderr)
                sys.exit(1)
            print(json.dumps(results, indent=2, default=str))

        elif args.command == 'missing-fk-indexes':
            results = analyze_missing_fk_indexes(conn, args.schema)
            print(json.dumps(results, indent=2, default=str))

        elif args.command == 'fragmentation':
            results = analyze_fragmentation(conn, args.schema)
            print(json.dumps(results, indent=2, default=str))

        elif args.command == 'slow-queries':
            results = analyze_slow_queries(conn, args.schema)
            if results is None:
                print("performance_schema 未启用", file=sys.stderr)
                sys.exit(1)
            print(json.dumps(results, indent=2, default=str))

        elif args.command == 'buffer-pool':
            results = analyze_buffer_pool(conn)
            if results is None:
                print("无法获取缓冲池信息", file=sys.stderr)
                sys.exit(1)
            print(json.dumps(results, indent=2, default=str))

    finally:
        conn.close()


if __name__ == '__main__':
    main()
