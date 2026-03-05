#!/usr/bin/env python3
"""
PostgreSQL 索引性能分析与优化建议器
功能：检测缺失索引、冗余索引、未使用索引、慢查询分析、表膨胀检测、锁等待分析
"""

import argparse
import json
import os
import sys
from datetime import datetime

# Windows 控制台中文输出兼容
if sys.platform == "win32":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")

try:
    import psycopg2
    from psycopg2.extras import RealDictCursor
except ImportError:
    print("错误: 需要安装 psycopg2-binary")
    print("请运行: pip install psycopg2-binary")
    sys.exit(1)

# 复用 pg_inspector 的连接逻辑
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from pg_inspector import get_connection


# ============================================================
# 分析查询
# ============================================================

SQL_UNUSED_INDEXES = """
SELECT
    s.schemaname,
    s.relname AS tablename,
    s.indexrelname AS indexname,
    pg_size_pretty(pg_relation_size(s.indexrelid)) AS index_size,
    s.idx_scan AS index_scans,
    s.idx_tup_read,
    s.idx_tup_fetch
FROM pg_stat_user_indexes s
JOIN pg_index i ON s.indexrelid = i.indexrelid
WHERE s.idx_scan = 0
    AND NOT i.indisunique
    AND NOT i.indisprimary
    AND s.schemaname = %(schema)s
ORDER BY pg_relation_size(s.indexrelid) DESC;
"""

SQL_DUPLICATE_INDEXES = """
SELECT
    a.indrelid::regclass AS tablename,
    a.indexrelid::regclass AS index1,
    b.indexrelid::regclass AS index2,
    pg_size_pretty(pg_relation_size(a.indexrelid)) AS index1_size,
    pg_size_pretty(pg_relation_size(b.indexrelid)) AS index2_size
FROM pg_index a
JOIN pg_index b ON a.indrelid = b.indrelid
    AND a.indexrelid != b.indexrelid
    AND a.indkey::text = b.indkey::text
JOIN pg_class c ON a.indrelid = c.oid
JOIN pg_namespace n ON c.relnamespace = n.oid
WHERE n.nspname = %(schema)s
    AND a.indexrelid < b.indexrelid
ORDER BY pg_relation_size(a.indexrelid) DESC;
"""

SQL_REDUNDANT_INDEXES = """
WITH index_cols AS (
    SELECT
        i.indrelid,
        i.indexrelid,
        ic.relname AS indexname,
        array_agg(a.attname ORDER BY array_position(i.indkey, a.attnum)) AS columns,
        i.indisunique,
        i.indisprimary
    FROM pg_index i
    JOIN pg_class ic ON i.indexrelid = ic.oid
    JOIN pg_attribute a ON a.attrelid = i.indrelid AND a.attnum = ANY(i.indkey)
    JOIN pg_class c ON i.indrelid = c.oid
    JOIN pg_namespace n ON c.relnamespace = n.oid
    WHERE n.nspname = %(schema)s
    GROUP BY i.indrelid, i.indexrelid, ic.relname, i.indisunique, i.indisprimary
)
SELECT
    a.indrelid::regclass AS tablename,
    a.indexname AS redundant_index,
    a.columns AS redundant_columns,
    b.indexname AS covering_index,
    b.columns AS covering_columns,
    pg_size_pretty(pg_relation_size(a.indexrelid)) AS redundant_size
FROM index_cols a
JOIN index_cols b ON a.indrelid = b.indrelid
    AND a.indexrelid != b.indexrelid
    AND a.columns <@ b.columns
    AND array_length(a.columns, 1) < array_length(b.columns, 1)
    AND NOT a.indisprimary
    AND NOT a.indisunique
ORDER BY pg_relation_size(a.indexrelid) DESC;
"""

SQL_MISSING_FK_INDEXES = """
SELECT
    tc.table_schema,
    tc.table_name,
    kcu.column_name,
    tc.constraint_name
FROM information_schema.table_constraints tc
JOIN information_schema.key_column_usage kcu
    ON tc.constraint_name = kcu.constraint_name
    AND tc.table_schema = kcu.table_schema
WHERE tc.constraint_type = 'FOREIGN KEY'
    AND tc.table_schema = %(schema)s
    AND NOT EXISTS (
        SELECT 1
        FROM pg_index i
        JOIN pg_attribute a ON a.attrelid = i.indrelid AND a.attnum = ANY(i.indkey)
        JOIN pg_class c ON i.indrelid = c.oid
        JOIN pg_namespace n ON c.relnamespace = n.oid
        WHERE n.nspname = tc.table_schema
            AND c.relname = tc.table_name
            AND a.attname = kcu.column_name
            AND array_position(i.indkey, a.attnum) = 1
    )
ORDER BY tc.table_name;
"""

SQL_SEQ_SCAN_TABLES = """
SELECT
    schemaname,
    relname AS tablename,
    seq_scan,
    seq_tup_read,
    idx_scan,
    idx_tup_fetch,
    n_live_tup AS estimated_rows,
    CASE WHEN (seq_scan + idx_scan) > 0
        THEN round(100.0 * seq_scan / (seq_scan + idx_scan), 1)
        ELSE 0
    END AS seq_scan_pct
FROM pg_stat_user_tables
WHERE schemaname = %(schema)s
    AND seq_scan > 0
    AND n_live_tup > 1000
ORDER BY seq_tup_read DESC
LIMIT 20;
"""

SQL_SLOW_QUERIES = """
SELECT
    queryid,
    query,
    calls,
    round(total_exec_time::numeric, 2) AS total_time_ms,
    round(mean_exec_time::numeric, 2) AS avg_time_ms,
    round(max_exec_time::numeric, 2) AS max_time_ms,
    rows,
    round((100.0 * shared_blks_hit / NULLIF(shared_blks_hit + shared_blks_read, 0))::numeric, 2) AS hit_rate_pct
FROM pg_stat_statements
WHERE dbid = (SELECT oid FROM pg_database WHERE datname = current_database())
    AND query NOT LIKE '%%pg_stat%%'
    AND calls > 5
ORDER BY mean_exec_time DESC
LIMIT 20;
"""

SQL_TABLE_BLOAT = """
SELECT
    schemaname,
    tablename,
    pg_size_pretty(pg_total_relation_size(schemaname || '.' || tablename)) AS total_size,
    n_dead_tup,
    n_live_tup,
    CASE WHEN n_live_tup > 0
        THEN round(100.0 * n_dead_tup / (n_live_tup + n_dead_tup), 1)
        ELSE 0
    END AS dead_tup_pct,
    last_vacuum,
    last_autovacuum,
    last_analyze,
    last_autoanalyze
FROM pg_stat_user_tables
WHERE schemaname = %(schema)s
    AND n_dead_tup > 1000
ORDER BY n_dead_tup DESC
LIMIT 20;
"""

SQL_INDEX_BLOAT = """
SELECT
    schemaname,
    tablename,
    indexname,
    pg_size_pretty(pg_relation_size(indexrelid)) AS index_size,
    idx_scan,
    idx_tup_read,
    idx_tup_fetch
FROM pg_stat_user_indexes
WHERE schemaname = %(schema)s
ORDER BY pg_relation_size(indexrelid) DESC
LIMIT 30;
"""

SQL_LOCK_WAITS = """
SELECT
    blocked_locks.pid AS blocked_pid,
    blocked_activity.usename AS blocked_user,
    blocking_locks.pid AS blocking_pid,
    blocking_activity.usename AS blocking_user,
    blocked_activity.query AS blocked_query,
    blocking_activity.query AS blocking_query,
    blocked_activity.wait_event_type,
    now() - blocked_activity.query_start AS blocked_duration
FROM pg_catalog.pg_locks blocked_locks
JOIN pg_catalog.pg_stat_activity blocked_activity ON blocked_activity.pid = blocked_locks.pid
JOIN pg_catalog.pg_locks blocking_locks
    ON blocking_locks.locktype = blocked_locks.locktype
    AND blocking_locks.database IS NOT DISTINCT FROM blocked_locks.database
    AND blocking_locks.relation IS NOT DISTINCT FROM blocked_locks.relation
    AND blocking_locks.page IS NOT DISTINCT FROM blocked_locks.page
    AND blocking_locks.tuple IS NOT DISTINCT FROM blocked_locks.tuple
    AND blocking_locks.virtualxid IS NOT DISTINCT FROM blocked_locks.virtualxid
    AND blocking_locks.transactionid IS NOT DISTINCT FROM blocked_locks.transactionid
    AND blocking_locks.classid IS NOT DISTINCT FROM blocked_locks.classid
    AND blocking_locks.objid IS NOT DISTINCT FROM blocked_locks.objid
    AND blocking_locks.objsubid IS NOT DISTINCT FROM blocked_locks.objsubid
    AND blocking_locks.pid != blocked_locks.pid
JOIN pg_catalog.pg_stat_activity blocking_activity ON blocking_activity.pid = blocking_locks.pid
WHERE NOT blocked_locks.granted
ORDER BY blocked_duration DESC;
"""

SQL_CONNECTION_STATS = """
SELECT
    datname,
    numbackends AS active_connections,
    xact_commit,
    xact_rollback,
    blks_read,
    blks_hit,
    CASE WHEN (blks_hit + blks_read) > 0
        THEN round(100.0 * blks_hit / (blks_hit + blks_read), 2)
        ELSE 0
    END AS cache_hit_ratio,
    tup_returned,
    tup_fetched,
    tup_inserted,
    tup_updated,
    tup_deleted,
    deadlocks,
    temp_files,
    pg_size_pretty(temp_bytes) AS temp_bytes
FROM pg_stat_database
WHERE datname = current_database();
"""

SQL_TABLE_IO = """
SELECT
    schemaname,
    relname AS tablename,
    heap_blks_read,
    heap_blks_hit,
    CASE WHEN (heap_blks_hit + heap_blks_read) > 0
        THEN round(100.0 * heap_blks_hit / (heap_blks_hit + heap_blks_read), 2)
        ELSE 100
    END AS cache_hit_ratio,
    idx_blks_read,
    idx_blks_hit
FROM pg_statio_user_tables
WHERE schemaname = %(schema)s
ORDER BY heap_blks_read DESC
LIMIT 20;
"""


# ============================================================
# 分析逻辑
# ============================================================

def analyze_unused_indexes(conn, schema):
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(SQL_UNUSED_INDEXES, {'schema': schema})
        return cur.fetchall()


def analyze_duplicate_indexes(conn, schema):
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(SQL_DUPLICATE_INDEXES, {'schema': schema})
        return cur.fetchall()


def analyze_redundant_indexes(conn, schema):
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(SQL_REDUNDANT_INDEXES, {'schema': schema})
        return cur.fetchall()


def analyze_missing_fk_indexes(conn, schema):
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(SQL_MISSING_FK_INDEXES, {'schema': schema})
        return cur.fetchall()


def analyze_seq_scans(conn, schema):
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(SQL_SEQ_SCAN_TABLES, {'schema': schema})
        return cur.fetchall()


def analyze_slow_queries(conn):
    """分析慢查询，需要 pg_stat_statements 扩展"""
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        try:
            cur.execute(SQL_SLOW_QUERIES)
            return cur.fetchall()
        except psycopg2.errors.UndefinedTable:
            conn.rollback()
            return None  # pg_stat_statements 未启用


def analyze_table_bloat(conn, schema):
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(SQL_TABLE_BLOAT, {'schema': schema})
        return cur.fetchall()


def analyze_lock_waits(conn):
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(SQL_LOCK_WAITS)
        return cur.fetchall()


def analyze_connection_stats(conn):
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(SQL_CONNECTION_STATS)
        return cur.fetchone()


def analyze_table_io(conn, schema):
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(SQL_TABLE_IO, {'schema': schema})
        return cur.fetchall()


# ============================================================
# 报告生成
# ============================================================

def _section(title, level=2):
    return f"\n{'#' * level} {title}\n"


def generate_report(conn, schema):
    """生成完整的性能分析报告（Markdown 格式）"""
    lines = []
    lines.append(f"# PostgreSQL 索引与性能分析报告")
    lines.append(f"**Schema**: `{schema}` | **时间**: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")

    # ---- 数据库概览 ----
    lines.append(_section("数据库概览"))
    stats = analyze_connection_stats(conn)
    if stats:
        lines.append(f"- **缓存命中率**: {stats['cache_hit_ratio']}%")
        lines.append(f"- **活跃连接**: {stats['active_connections']}")
        lines.append(f"- **事务提交/回滚**: {stats['xact_commit']} / {stats['xact_rollback']}")
        lines.append(f"- **死锁次数**: {stats['deadlocks']}")
        lines.append(f"- **临时文件**: {stats['temp_files']} ({stats['temp_bytes']})")
        lines.append("")

    # ---- 未使用索引 ----
    lines.append(_section("未使用索引 (从未被扫描)"))
    unused = analyze_unused_indexes(conn, schema)
    if unused:
        lines.append("以下索引自上次统计重置以来从未被使用，建议评估后删除:\n")
        lines.append("| 表 | 索引 | 大小 | 扫描次数 |")
        lines.append("|-----|------|------|----------|")
        for idx in unused:
            lines.append(f"| {idx['tablename']} | {idx['indexname']} | {idx['index_size']} | {idx['index_scans']} |")
    else:
        lines.append("没有发现未使用的索引。")
    lines.append("")

    # ---- 重复索引 ----
    lines.append(_section("重复索引 (完全相同的列组合)"))
    dups = analyze_duplicate_indexes(conn, schema)
    if dups:
        lines.append("以下索引具有完全相同的列组合，保留一个即可:\n")
        lines.append("| 表 | 索引 1 | 索引 2 | 索引 1 大小 | 索引 2 大小 |")
        lines.append("|-----|--------|--------|------------|------------|")
        for d in dups:
            lines.append(f"| {d['tablename']} | {d['index1']} | {d['index2']} | {d['index1_size']} | {d['index2_size']} |")
    else:
        lines.append("没有发现重复索引。")
    lines.append("")

    # ---- 冗余索引 ----
    lines.append(_section("冗余索引 (被其他索引覆盖)"))
    redundant = analyze_redundant_indexes(conn, schema)
    if redundant:
        lines.append("以下索引的列被其他更大的复合索引完全覆盖:\n")
        lines.append("| 表 | 冗余索引 | 冗余列 | 覆盖索引 | 覆盖列 | 冗余大小 |")
        lines.append("|-----|----------|--------|----------|--------|----------|")
        for r in redundant:
            lines.append(
                f"| {r['tablename']} | {r['redundant_index']} | {r['redundant_columns']} "
                f"| {r['covering_index']} | {r['covering_columns']} | {r['redundant_size']} |"
            )
    else:
        lines.append("没有发现冗余索引。")
    lines.append("")

    # ---- 外键缺失索引 ----
    lines.append(_section("外键缺失索引"))
    missing = analyze_missing_fk_indexes(conn, schema)
    if missing:
        lines.append("以下外键列没有索引，可能导致 JOIN 和级联操作变慢:\n")
        lines.append("| 表 | 外键列 | 约束名 |")
        lines.append("|-----|--------|--------|")
        for m in missing:
            lines.append(f"| {m['table_name']} | {m['column_name']} | {m['constraint_name']} |")
    else:
        lines.append("所有外键列都已建立索引。")
    lines.append("")

    # ---- 顺序扫描热表 ----
    lines.append(_section("高频顺序扫描表"))
    seqs = analyze_seq_scans(conn, schema)
    if seqs:
        lines.append("以下大表存在大量顺序扫描（可能需要添加索引）:\n")
        lines.append("| 表 | 顺序扫描 | 索引扫描 | 顺序占比 | 估计行数 |")
        lines.append("|-----|----------|----------|----------|----------|")
        for s in seqs:
            lines.append(
                f"| {s['tablename']} | {s['seq_scan']} | {s['idx_scan']} "
                f"| {s['seq_scan_pct']}% | {s['estimated_rows']} |"
            )
    else:
        lines.append("没有发现异常的顺序扫描。")
    lines.append("")

    # ---- 表 I/O 分析 ----
    lines.append(_section("表 I/O 缓存命中分析"))
    tio = analyze_table_io(conn, schema)
    if tio:
        lines.append("| 表 | 堆读 | 堆命中 | 缓存率 | 索引读 | 索引命中 |")
        lines.append("|-----|------|--------|--------|--------|----------|")
        for t in tio:
            lines.append(
                f"| {t['tablename']} | {t['heap_blks_read']} | {t['heap_blks_hit']} "
                f"| {t['cache_hit_ratio']}% | {t['idx_blks_read']} | {t['idx_blks_hit']} |"
            )
    lines.append("")

    # ---- 表膨胀 ----
    lines.append(_section("表膨胀检测"))
    bloat = analyze_table_bloat(conn, schema)
    if bloat:
        lines.append("以下表存在大量死元组（可能需要 VACUUM）:\n")
        lines.append("| 表 | 总大小 | 死元组 | 活元组 | 死亡率 | 上次清理 |")
        lines.append("|-----|--------|--------|--------|--------|----------|")
        for b in bloat:
            last_vac = b['last_autovacuum'] or b['last_vacuum'] or 'N/A'
            if hasattr(last_vac, 'strftime'):
                last_vac = last_vac.strftime('%Y-%m-%d %H:%M')
            lines.append(
                f"| {b['tablename']} | {b['total_size']} | {b['n_dead_tup']} "
                f"| {b['n_live_tup']} | {b['dead_tup_pct']}% | {last_vac} |"
            )
    else:
        lines.append("没有发现严重的表膨胀。")
    lines.append("")

    # ---- 慢查询 ----
    lines.append(_section("慢查询分析 (pg_stat_statements)"))
    slow = analyze_slow_queries(conn)
    if slow is None:
        lines.append("**pg_stat_statements 扩展未启用**，无法分析慢查询。\n")
        lines.append("启用方法: `CREATE EXTENSION pg_stat_statements;`")
        lines.append("并在 `postgresql.conf` 中添加: `shared_preload_libraries = 'pg_stat_statements'`")
    elif slow:
        lines.append("| 查询 | 调用次数 | 平均(ms) | 最大(ms) | 总时间(ms) | 缓存率 |")
        lines.append("|------|----------|----------|----------|------------|--------|")
        for s in slow:
            query = s['query'][:80].replace('\r', '').replace('\n', ' ').replace('|', '\\|')
            lines.append(
                f"| {query}... | {s['calls']} | {s['avg_time_ms']} "
                f"| {s['max_time_ms']} | {s['total_time_ms']} | {s['hit_rate_pct']}% |"
            )
    else:
        lines.append("没有发现慢查询。")
    lines.append("")

    # ---- 锁等待 ----
    lines.append(_section("当前锁等待"))
    locks = analyze_lock_waits(conn)
    if locks:
        lines.append("**当前存在锁等待!**\n")
        for lk in locks:
            lines.append(f"- **被阻塞 PID**: {lk['blocked_pid']} (用户: {lk['blocked_user']})")
            lines.append(f"  阻塞查询: `{lk['blocked_query'][:100]}`")
            lines.append(f"  **阻塞者 PID**: {lk['blocking_pid']} (用户: {lk['blocking_user']})")
            lines.append(f"  阻塞者查询: `{lk['blocking_query'][:100]}`")
            lines.append(f"  等待时长: {lk['blocked_duration']}\n")
    else:
        lines.append("当前没有锁等待。")
    lines.append("")

    return "\n".join(lines)


# ============================================================
# 优化 DDL 生成
# ============================================================

def generate_optimization_ddl(conn, schema, concurrently=True):
    """基于分析结果生成优化 DDL 脚本"""
    lines = []
    lines.append(f"-- ============================================================")
    lines.append(f"-- PostgreSQL 索引优化脚本")
    lines.append(f"-- Schema: {schema}")
    lines.append(f"-- 生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    lines.append(f"-- ============================================================\n")

    conc = "CONCURRENTLY " if concurrently else ""
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
            lines.append(f"CREATE INDEX {conc}{idx_name} ON {schema}.{m['table_name']} ({m['column_name']});\n")

    # 删除未使用索引
    unused = analyze_unused_indexes(conn, schema)
    if unused:
        has_changes = True
        lines.append("-- ============================================================")
        lines.append("-- 2. 删除未使用的索引 (请确认后再执行)")
        lines.append("-- ============================================================\n")
        for idx in unused:
            lines.append(f"-- 表: {idx['tablename']}, 大小: {idx['index_size']}, 扫描次数: {idx['index_scans']}")
            lines.append(f"DROP INDEX {conc}{schema}.{idx['indexname']};\n")

    # 删除重复索引
    dups = analyze_duplicate_indexes(conn, schema)
    if dups:
        has_changes = True
        lines.append("-- ============================================================")
        lines.append("-- 3. 删除重复索引 (保留其中一个)")
        lines.append("-- ============================================================\n")
        for d in dups:
            lines.append(f"-- 表: {d['tablename']}")
            lines.append(f"-- 保留: {d['index1']} ({d['index1_size']})")
            lines.append(f"-- 删除: {d['index2']} ({d['index2_size']})")
            lines.append(f"DROP INDEX {conc}{d['index2']};\n")

    # 删除冗余索引
    redundant = analyze_redundant_indexes(conn, schema)
    if redundant:
        has_changes = True
        lines.append("-- ============================================================")
        lines.append("-- 4. 删除冗余索引 (已被其他索引覆盖)")
        lines.append("-- ============================================================\n")
        for r in redundant:
            lines.append(f"-- 表: {r['tablename']}")
            lines.append(f"-- 冗余: {r['redundant_index']} ({r['redundant_columns']})")
            lines.append(f"-- 覆盖: {r['covering_index']} ({r['covering_columns']})")
            lines.append(f"DROP INDEX {conc}{r['redundant_index']};\n")

    # VACUUM 建议
    bloat = analyze_table_bloat(conn, schema)
    if bloat:
        has_changes = True
        lines.append("-- ============================================================")
        lines.append("-- 5. 表维护 (VACUUM / ANALYZE)")
        lines.append("-- ============================================================\n")
        for b in bloat:
            lines.append(f"-- 表: {b['tablename']}, 死元组率: {b['dead_tup_pct']}%")
            lines.append(f"VACUUM ANALYZE {schema}.{b['tablename']};\n")

    if not has_changes:
        lines.append("-- 恭喜！当前没有发现需要优化的索引问题。")

    return "\n".join(lines)


# ============================================================
# CLI
# ============================================================

def main():
    parser = argparse.ArgumentParser(description='PostgreSQL 索引与性能分析器')

    # 连接参数
    conn_group = parser.add_argument_group('连接参数')
    conn_group.add_argument('--dsn', help='完整数据库连接字符串 (DATABASE_URL)')
    conn_group.add_argument('--host', '-H', help='数据库主机 (PGHOST)')
    conn_group.add_argument('--port', '-p', help='数据库端口 (PGPORT)')
    conn_group.add_argument('--user', '-U', help='数据库用户 (PGUSER)')
    conn_group.add_argument('--password', '-W', help='数据库密码 (PGPASSWORD)')
    conn_group.add_argument('--dbname', '-d', help='数据库名称 (PGDATABASE)')
    conn_group.add_argument('--env-file', help='.env 文件路径')

    # 操作子命令
    sub = parser.add_subparsers(dest='command', help='可用命令')

    # report
    p_report = sub.add_parser('report', help='生成完整性能分析报告')
    p_report.add_argument('--schema', '-s', default='public')
    p_report.add_argument('--output', '-o', help='报告输出文件')

    # optimize
    p_optimize = sub.add_parser('optimize', help='生成优化 DDL 脚本')
    p_optimize.add_argument('--schema', '-s', default='public')
    p_optimize.add_argument('--no-concurrently', action='store_true', help='不使用 CONCURRENTLY')
    p_optimize.add_argument('--output', '-o', help='DDL 输出文件')

    # 单项分析命令
    p_unused = sub.add_parser('unused-indexes', help='查看未使用索引')
    p_unused.add_argument('--schema', '-s', default='public')

    p_missing = sub.add_parser('missing-fk-indexes', help='查看外键缺失索引')
    p_missing.add_argument('--schema', '-s', default='public')

    p_bloat = sub.add_parser('bloat', help='查看表膨胀情况')
    p_bloat.add_argument('--schema', '-s', default='public')

    p_locks = sub.add_parser('locks', help='查看当前锁等待')

    p_slow = sub.add_parser('slow-queries', help='查看慢查询 (需 pg_stat_statements)')

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
                with open(args.output, 'w', encoding="utf-8") as f:
                    f.write(report)
                print(f"报告已写入: {args.output}")
            else:
                print(report)

        elif args.command == 'optimize':
            ddl = generate_optimization_ddl(conn, args.schema, not args.no_concurrently)
            if args.output:
                with open(args.output, 'w', encoding="utf-8") as f:
                    f.write(ddl)
                print(f"优化脚本已写入: {args.output}")
            else:
                print(ddl)

        elif args.command == 'unused-indexes':
            results = analyze_unused_indexes(conn, args.schema)
            print(json.dumps(results, indent=2, default=str, ensure_ascii=False))

        elif args.command == 'missing-fk-indexes':
            results = analyze_missing_fk_indexes(conn, args.schema)
            print(json.dumps(results, indent=2, default=str, ensure_ascii=False))

        elif args.command == 'bloat':
            results = analyze_table_bloat(conn, args.schema)
            print(json.dumps(results, indent=2, default=str, ensure_ascii=False))

        elif args.command == 'locks':
            results = analyze_lock_waits(conn)
            print(json.dumps(results, indent=2, default=str, ensure_ascii=False))

        elif args.command == 'slow-queries':
            results = analyze_slow_queries(conn)
            if results is None:
                print("pg_stat_statements 扩展未启用", file=sys.stderr)
                sys.exit(1)
            print(json.dumps(results, indent=2, default=str, ensure_ascii=False))

    finally:
        conn.close()


if __name__ == '__main__':
    main()
