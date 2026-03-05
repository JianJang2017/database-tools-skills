#!/usr/bin/env python3
"""
MySQL 数据库元数据检查器
功能：连接 MySQL，读取表结构信息，生成 DDL 和初始化脚本
"""

import argparse
import json
import os
import sys

try:
    import pymysql
    from pymysql.cursors import DictCursor
except ImportError:
    pymysql = None
    DictCursor = None


def get_connection(args):
    """
    获取 MySQL 数据库连接。
    优先级: 命令行参数 > 环境变量 > .env 文件
    """
    if pymysql is None:
        print("错误: 需要安装 pymysql")
        print("请运行: pip install pymysql")
        sys.exit(1)
    # 尝试从 .env 文件加载
    env_file = getattr(args, 'env_file', None) or '.env'
    if os.path.exists(env_file):
        with open(env_file) as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith('#') and '=' in line:
                    key, val = line.split('=', 1)
                    os.environ.setdefault(key.strip(), val.strip().strip('"').strip("'"))

    # 如果提供了完整连接字符串
    dsn = getattr(args, 'dsn', None) or os.environ.get('MYSQL_DSN')
    if dsn:
        from urllib.parse import urlparse
        parsed = urlparse(dsn)
        return pymysql.connect(
            host=parsed.hostname or 'localhost',
            port=parsed.port or 3306,
            user=parsed.username or 'root',
            password=parsed.password or '',
            database=parsed.path.lstrip('/') if parsed.path else None,
            charset='utf8mb4',
            cursorclass=DictCursor,
        )

    # 使用独立参数
    params = {
        'host': getattr(args, 'host', None) or os.environ.get('MYSQL_HOST', 'localhost'),
        'port': int(getattr(args, 'port', None) or os.environ.get('MYSQL_PORT', '3306')),
        'user': getattr(args, 'user', None) or os.environ.get('MYSQL_USER', 'root'),
        'password': getattr(args, 'password', None) or os.environ.get('MYSQL_PWD', ''),
        'database': getattr(args, 'dbname', None) or os.environ.get('MYSQL_DATABASE'),
        'charset': 'utf8mb4',
        'cursorclass': DictCursor,
    }
    # 移除 None 值
    params = {k: v for k, v in params.items() if v is not None}
    return pymysql.connect(**params)


# ============================================================
# 表结构查询
# ============================================================

SQL_LIST_SCHEMAS = """
SELECT SCHEMA_NAME AS schema_name
FROM information_schema.SCHEMATA
WHERE SCHEMA_NAME NOT IN ('information_schema', 'performance_schema', 'mysql', 'sys')
ORDER BY SCHEMA_NAME;
"""

SQL_LIST_TABLES = """
SELECT
    TABLE_SCHEMA AS table_schema,
    TABLE_NAME AS table_name,
    TABLE_TYPE AS table_type,
    ENGINE AS engine,
    TABLE_ROWS AS estimated_rows,
    TABLE_COMMENT AS table_comment
FROM information_schema.TABLES
WHERE TABLE_SCHEMA = %(schema)s
    AND TABLE_TYPE = 'BASE TABLE'
ORDER BY TABLE_NAME;
"""

SQL_TABLE_COLUMNS = """
SELECT
    COLUMN_NAME AS column_name,
    ORDINAL_POSITION AS ordinal_position,
    COLUMN_DEFAULT AS column_default,
    IS_NULLABLE AS is_nullable,
    DATA_TYPE AS data_type,
    COLUMN_TYPE AS column_type,
    CHARACTER_MAXIMUM_LENGTH AS character_maximum_length,
    NUMERIC_PRECISION AS numeric_precision,
    NUMERIC_SCALE AS numeric_scale,
    COLUMN_KEY AS column_key,
    EXTRA AS extra,
    COLUMN_COMMENT AS column_comment
FROM information_schema.COLUMNS
WHERE TABLE_SCHEMA = %(schema)s AND TABLE_NAME = %(table)s
ORDER BY ORDINAL_POSITION;
"""

SQL_TABLE_CONSTRAINTS = """
SELECT
    tc.CONSTRAINT_NAME AS constraint_name,
    tc.CONSTRAINT_TYPE AS constraint_type,
    kcu.COLUMN_NAME AS column_name,
    kcu.REFERENCED_TABLE_SCHEMA AS foreign_table_schema,
    kcu.REFERENCED_TABLE_NAME AS foreign_table_name,
    kcu.REFERENCED_COLUMN_NAME AS foreign_column_name,
    kcu.ORDINAL_POSITION AS ordinal_position
FROM information_schema.TABLE_CONSTRAINTS tc
JOIN information_schema.KEY_COLUMN_USAGE kcu
    ON tc.CONSTRAINT_NAME = kcu.CONSTRAINT_NAME
    AND tc.TABLE_SCHEMA = kcu.TABLE_SCHEMA
    AND tc.TABLE_NAME = kcu.TABLE_NAME
WHERE tc.TABLE_SCHEMA = %(schema)s AND tc.TABLE_NAME = %(table)s
ORDER BY tc.CONSTRAINT_TYPE, tc.CONSTRAINT_NAME, kcu.ORDINAL_POSITION;
"""

SQL_TABLE_INDEXES = """
SELECT
    INDEX_NAME AS index_name,
    GROUP_CONCAT(COLUMN_NAME ORDER BY SEQ_IN_INDEX) AS columns,
    NON_UNIQUE AS non_unique,
    INDEX_TYPE AS index_type,
    NULLABLE AS nullable,
    COMMENT AS comment
FROM information_schema.STATISTICS
WHERE TABLE_SCHEMA = %(schema)s AND TABLE_NAME = %(table)s
GROUP BY INDEX_NAME, NON_UNIQUE, INDEX_TYPE, NULLABLE, COMMENT
ORDER BY INDEX_NAME;
"""

SQL_TABLE_SIZE = """
SELECT
    TABLE_NAME AS table_name,
    TABLE_ROWS AS estimated_rows,
    CONCAT(ROUND(DATA_LENGTH / 1024 / 1024, 2), ' MB') AS table_size,
    CONCAT(ROUND(INDEX_LENGTH / 1024 / 1024, 2), ' MB') AS index_size,
    CONCAT(ROUND((DATA_LENGTH + INDEX_LENGTH) / 1024 / 1024, 2), ' MB') AS total_size,
    ENGINE AS engine,
    TABLE_COLLATION AS collation
FROM information_schema.TABLES
WHERE TABLE_SCHEMA = %(schema)s AND TABLE_NAME = %(table)s;
"""


def list_schemas(conn):
    with conn.cursor() as cur:
        cur.execute(SQL_LIST_SCHEMAS)
        return [r['schema_name'] for r in cur.fetchall()]


def list_tables(conn, schema):
    with conn.cursor() as cur:
        cur.execute(SQL_LIST_TABLES, {'schema': schema})
        return cur.fetchall()


def get_table_info(conn, schema, table):
    info = {'schema': schema, 'table': table}
    params = {'schema': schema, 'table': table}

    with conn.cursor() as cur:
        cur.execute(SQL_TABLE_COLUMNS, params)
        info['columns'] = cur.fetchall()

        cur.execute(SQL_TABLE_CONSTRAINTS, params)
        info['constraints'] = cur.fetchall()

        cur.execute(SQL_TABLE_INDEXES, params)
        info['indexes'] = cur.fetchall()

        cur.execute(SQL_TABLE_SIZE, params)
        size_row = cur.fetchone()
        info['size'] = size_row
        info['comment'] = None
        info['engine'] = size_row['engine'] if size_row else None
        info['collation'] = size_row['collation'] if size_row else None

    # 获取表注释
    with conn.cursor() as cur:
        cur.execute(
            "SELECT TABLE_COMMENT AS table_comment FROM information_schema.TABLES "
            "WHERE TABLE_SCHEMA = %(schema)s AND TABLE_NAME = %(table)s",
            params,
        )
        row = cur.fetchone()
        info['comment'] = row['table_comment'] if row and row['table_comment'] else None

    return info


def _col_type_str(col):
    """返回列的显示类型"""
    return col['column_type']


# ============================================================
# DDL 生成
# ============================================================

def generate_ddl_show(conn, schema, tables=None):
    """使用 SHOW CREATE TABLE 快速生成 DDL"""
    all_tables = list_tables(conn, schema)
    if tables:
        all_tables = [t for t in all_tables if t['table_name'] in tables]

    lines = []
    lines.append(f"-- ============================================================")
    lines.append(f"-- DDL for database: {schema}")
    lines.append(f"-- Generated by mysql_inspector.py (SHOW CREATE TABLE)")
    lines.append(f"-- ============================================================\n")

    lines.append(f"CREATE DATABASE IF NOT EXISTS `{schema}` DEFAULT CHARACTER SET utf8mb4;\n")
    lines.append(f"USE `{schema}`;\n")

    for tbl in all_tables:
        tname = tbl['table_name']
        with conn.cursor() as cur:
            cur.execute(f"SHOW CREATE TABLE `{schema}`.`{tname}`")
            row = cur.fetchone()
            if row:
                create_sql = row.get('Create Table', '')
                lines.append(f"{create_sql};\n")

    return "\n".join(lines)


def generate_ddl(conn, schema, tables=None):
    """自行拼装 DDL（用于 diff 兼容）"""
    all_tables = list_tables(conn, schema)
    if tables:
        all_tables = [t for t in all_tables if t['table_name'] in tables]

    lines = []
    lines.append(f"-- ============================================================")
    lines.append(f"-- DDL for database: {schema}")
    lines.append(f"-- Generated by mysql_inspector.py")
    lines.append(f"-- ============================================================\n")

    lines.append(f"CREATE DATABASE IF NOT EXISTS `{schema}` DEFAULT CHARACTER SET utf8mb4;\n")
    lines.append(f"USE `{schema}`;\n")

    for tbl in all_tables:
        tname = tbl['table_name']
        info = get_table_info(conn, schema, tname)

        # 表注释
        if info['comment']:
            lines.append(f"-- {info['comment']}")

        lines.append(f"CREATE TABLE `{tname}` (")
        col_defs = []

        for col in info['columns']:
            parts = [f"  `{col['column_name']}`"]
            parts.append(col['column_type'])
            if col['is_nullable'] == 'NO':
                parts.append("NOT NULL")
            if col['column_default'] is not None:
                default = col['column_default']
                if default == 'CURRENT_TIMESTAMP':
                    parts.append(f"DEFAULT {default}")
                else:
                    parts.append(f"DEFAULT '{default}'")
            if col['extra']:
                parts.append(col['extra'].upper())
            if col['column_comment']:
                parts.append(f"COMMENT '{col['column_comment']}'")
            col_defs.append(" ".join(parts))

        # Primary key
        pk_cols = []
        unique_constraints = {}
        fk_constraints = []

        for con in info['constraints']:
            if con['constraint_type'] == 'PRIMARY KEY':
                pk_cols.append(con['column_name'])
            elif con['constraint_type'] == 'UNIQUE':
                unique_constraints.setdefault(con['constraint_name'], []).append(con['column_name'])
            elif con['constraint_type'] == 'FOREIGN KEY':
                fk_constraints.append(con)

        if pk_cols:
            col_defs.append(f"  PRIMARY KEY ({', '.join(f'`{c}`' for c in pk_cols)})")

        for cname, ucols in unique_constraints.items():
            col_defs.append(f"  UNIQUE KEY `{cname}` ({', '.join(f'`{c}`' for c in ucols)})")

        # 非主键、非唯一的普通索引
        for idx in info['indexes']:
            if idx['index_name'] == 'PRIMARY':
                continue
            if not idx['non_unique']:
                continue  # 已在 unique 中处理
            idx_cols = ', '.join(f'`{c.strip()}`' for c in idx['columns'].split(','))
            col_defs.append(f"  KEY `{idx['index_name']}` ({idx_cols})")

        for fk in fk_constraints:
            col_defs.append(
                f"  CONSTRAINT `{fk['constraint_name']}` FOREIGN KEY (`{fk['column_name']}`) "
                f"REFERENCES `{fk['foreign_table_name']}` (`{fk['foreign_column_name']}`)"
            )

        lines.append(",\n".join(col_defs))

        # 表选项
        opts = []
        if info.get('engine'):
            opts.append(f"ENGINE={info['engine']}")
        if info.get('collation'):
            opts.append(f"COLLATE={info['collation']}")
        if info.get('comment'):
            opts.append(f"COMMENT='{info['comment']}'")

        opt_str = " " + " ".join(opts) if opts else ""
        lines.append(f"){opt_str};\n")

    return "\n".join(lines)


# ============================================================
# 结构信息输出（JSON / Markdown）
# ============================================================

def export_schema_info(conn, schema, tables=None, fmt='markdown'):
    """导出表结构信息"""
    all_tables = list_tables(conn, schema)
    if tables:
        all_tables = [t for t in all_tables if t['table_name'] in tables]

    result = []
    for tbl in all_tables:
        info = get_table_info(conn, schema, tbl['table_name'])
        result.append(info)

    if fmt == 'json':
        return json.dumps(result, indent=2, default=str)

    # Markdown 格式
    lines = []
    for info in result:
        lines.append(f"## {info['schema']}.{info['table']}")
        if info['comment']:
            lines.append(f"> {info['comment']}\n")

        size = info['size']
        if size:
            lines.append(
                f"**大小**: 表 {size['table_size']} / 索引 {size['index_size']} / "
                f"总计 {size['total_size']} | **引擎**: {size.get('engine', 'N/A')} | "
                f"**行数**: ~{size.get('estimated_rows', 'N/A')}\n"
            )

        # Columns
        lines.append("### 列定义\n")
        lines.append("| 列名 | 类型 | 可空 | 默认值 | 其他 | 备注 |")
        lines.append("|------|------|------|--------|------|------|")
        for col in info['columns']:
            nullable = 'YES' if col['is_nullable'] == 'YES' else 'NO'
            default = col['column_default'] or ''
            extra = col['extra'] or ''
            comment = col.get('column_comment') or ''
            lines.append(
                f"| {col['column_name']} | {_col_type_str(col)} | {nullable} | "
                f"{default} | {extra} | {comment} |"
            )

        # Constraints
        if info['constraints']:
            lines.append("\n### 约束\n")
            lines.append("| 约束名 | 类型 | 列 | 引用 |")
            lines.append("|--------|------|-----|------|")
            for con in info['constraints']:
                ref = ''
                if con['constraint_type'] == 'FOREIGN KEY':
                    ref = (
                        f"{con['foreign_table_schema']}.{con['foreign_table_name']}"
                        f"({con['foreign_column_name']})"
                    )
                lines.append(
                    f"| {con['constraint_name']} | {con['constraint_type']} | "
                    f"{con['column_name']} | {ref} |"
                )

        # Indexes
        if info['indexes']:
            lines.append("\n### 索引\n")
            for idx in info['indexes']:
                unique_flag = " [UNIQUE]" if not idx['non_unique'] else ""
                lines.append(
                    f"- `{idx['index_name']}` ({idx['index_type']}): "
                    f"`{idx['columns']}`{unique_flag}"
                )

        lines.append("\n---\n")

    return "\n".join(lines)


# ============================================================
# CLI
# ============================================================

def main():
    parser = argparse.ArgumentParser(description='MySQL 数据库元数据检查器')

    # 连接参数
    conn_group = parser.add_argument_group('连接参数')
    conn_group.add_argument('--dsn', help='完整数据库连接字符串 (mysql://user:pass@host:port/db)')
    conn_group.add_argument('--host', '-H', help='数据库主机 (MYSQL_HOST)')
    conn_group.add_argument('--port', '-p', help='数据库端口 (MYSQL_PORT)')
    conn_group.add_argument('--user', '-U', help='数据库用户 (MYSQL_USER)')
    conn_group.add_argument('--password', '-W', help='数据库密码 (MYSQL_PWD)')
    conn_group.add_argument('--dbname', '-d', help='数据库名称 (MYSQL_DATABASE)')
    conn_group.add_argument('--env-file', help='.env 文件路径 (默认: .env)')

    # 操作子命令
    sub = parser.add_subparsers(dest='command', help='可用命令')

    # schemas
    sub.add_parser('schemas', help='列出所有用户数据库')

    # tables
    p_tables = sub.add_parser('tables', help='列出指定数据库下的表')
    p_tables.add_argument('--schema', '-s', required=True, help='数据库名称')

    # inspect
    p_inspect = sub.add_parser('inspect', help='查看表结构详情')
    p_inspect.add_argument('--schema', '-s', required=True)
    p_inspect.add_argument('--table', '-t', nargs='*', help='表名（不指定则查看全部）')
    p_inspect.add_argument('--format', '-f', choices=['markdown', 'json'], default='markdown')

    # ddl
    p_ddl = sub.add_parser('ddl', help='生成 DDL 脚本')
    p_ddl.add_argument('--schema', '-s', required=True)
    p_ddl.add_argument('--table', '-t', nargs='*', help='表名（不指定则生成全部）')
    p_ddl.add_argument('--output', '-o', help='输出文件路径')
    p_ddl.add_argument('--mode', choices=['show', 'build'], default='show',
                       help='DDL 生成模式: show=SHOW CREATE TABLE, build=自行拼装')

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
        if args.command == 'schemas':
            schemas = list_schemas(conn)
            for s in schemas:
                print(s)

        elif args.command == 'tables':
            tables = list_tables(conn, args.schema)
            for t in tables:
                engine = t.get('engine') or ''
                rows = t.get('estimated_rows') or ''
                comment = t.get('table_comment') or ''
                extra = f" [{engine}]" if engine else ''
                if comment:
                    extra += f" -- {comment}"
                print(f"  {t['table_name']}{extra}")

        elif args.command == 'inspect':
            output = export_schema_info(conn, args.schema, args.table, args.format)
            print(output)

        elif args.command == 'ddl':
            mode = getattr(args, 'mode', 'show')
            if mode == 'show':
                ddl = generate_ddl_show(conn, args.schema, args.table)
            else:
                ddl = generate_ddl(conn, args.schema, args.table)

            if args.output:
                with open(args.output, 'w') as f:
                    f.write(ddl)
                print(f"DDL 已写入: {args.output}")
            else:
                print(ddl)

    finally:
        conn.close()


if __name__ == '__main__':
    main()
