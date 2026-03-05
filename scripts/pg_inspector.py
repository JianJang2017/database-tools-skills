#!/usr/bin/env python3
"""
PostgreSQL 数据库元数据检查器
功能：连接 PostgreSQL，读取表结构信息，生成 DDL 和初始化脚本
"""

import argparse
import json
import os
import sys

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


def get_connection(args):
    """
    获取数据库连接。
    优先级: 命令行参数 > 环境变量 > .env 文件
    """
    # 尝试从 .env 文件加载
    env_file = getattr(args, 'env_file', None) or '.env'
    if os.path.exists(env_file):
        with open(env_file, encoding="utf-8") as f:
            for line in f:
                line = line.strip().strip("\r")
                if line and not line.startswith('#') and '=' in line:
                    key, val = line.split('=', 1)
                    val = val.strip().strip("\r").strip('"').strip("'")
                    if val:  # 跳过空值
                        os.environ.setdefault(key.strip(), val)

    # 如果提供了完整连接字符串
    dsn = getattr(args, 'dsn', None) or os.environ.get('DATABASE_URL') or os.environ.get('PG_DSN')
    if dsn:
        return psycopg2.connect(dsn)

    # 使用独立参数
    params = {
        'host': getattr(args, 'host', None) or os.environ.get('PGHOST', 'localhost'),
        'port': getattr(args, 'port', None) or os.environ.get('PGPORT', '5432'),
        'user': getattr(args, 'user', None) or os.environ.get('PGUSER', 'postgres'),
        'password': getattr(args, 'password', None) or os.environ.get('PGPASSWORD', ''),
        'dbname': getattr(args, 'dbname', None) or os.environ.get('PGDATABASE', 'postgres'),
    }
    return psycopg2.connect(**params)


# ============================================================
# 表结构查询
# ============================================================

SQL_LIST_SCHEMAS = """
SELECT schema_name
FROM information_schema.schemata
WHERE schema_name NOT IN ('pg_catalog', 'information_schema', 'pg_toast')
ORDER BY schema_name;
"""

SQL_LIST_TABLES = """
SELECT schemaname, tablename, hasindexes, hastriggers
FROM pg_tables
WHERE schemaname = %(schema)s
ORDER BY tablename;
"""

SQL_TABLE_COLUMNS = """
SELECT
    c.column_name,
    c.data_type,
    c.udt_name,
    c.character_maximum_length,
    c.numeric_precision,
    c.numeric_scale,
    c.is_nullable,
    c.column_default,
    pgd.description AS column_comment
FROM information_schema.columns c
LEFT JOIN pg_catalog.pg_statio_all_tables st
    ON st.schemaname = c.table_schema AND st.relname = c.table_name
LEFT JOIN pg_catalog.pg_description pgd
    ON pgd.objoid = st.relid AND pgd.objsubid = c.ordinal_position
WHERE c.table_schema = %(schema)s AND c.table_name = %(table)s
ORDER BY c.ordinal_position;
"""

SQL_TABLE_CONSTRAINTS = """
SELECT
    tc.constraint_name,
    tc.constraint_type,
    kcu.column_name,
    ccu.table_schema AS foreign_table_schema,
    ccu.table_name AS foreign_table_name,
    ccu.column_name AS foreign_column_name
FROM information_schema.table_constraints tc
JOIN information_schema.key_column_usage kcu
    ON tc.constraint_name = kcu.constraint_name AND tc.table_schema = kcu.table_schema
LEFT JOIN information_schema.constraint_column_usage ccu
    ON ccu.constraint_name = tc.constraint_name AND ccu.table_schema = tc.table_schema
WHERE tc.table_schema = %(schema)s AND tc.table_name = %(table)s
ORDER BY tc.constraint_type, tc.constraint_name;
"""

SQL_TABLE_INDEXES = """
SELECT
    indexname,
    indexdef
FROM pg_indexes
WHERE schemaname = %(schema)s AND tablename = %(table)s
ORDER BY indexname;
"""

SQL_TABLE_COMMENT = """
SELECT obj_description(c.oid) AS table_comment
FROM pg_class c
JOIN pg_namespace n ON n.oid = c.relnamespace
WHERE n.nspname = %(schema)s AND c.relname = %(table)s;
"""

SQL_TABLE_SIZE = """
SELECT
    pg_size_pretty(pg_total_relation_size(quote_ident(%(schema)s) || '.' || quote_ident(%(table)s))) AS total_size,
    pg_size_pretty(pg_relation_size(quote_ident(%(schema)s) || '.' || quote_ident(%(table)s))) AS table_size,
    pg_size_pretty(pg_total_relation_size(quote_ident(%(schema)s) || '.' || quote_ident(%(table)s))
        - pg_relation_size(quote_ident(%(schema)s) || '.' || quote_ident(%(table)s))) AS index_size;
"""

SQL_SEQUENCES = """
SELECT
    s.sequence_name,
    s.data_type,
    s.start_value,
    s.increment,
    s.minimum_value,
    s.maximum_value
FROM information_schema.sequences s
WHERE s.sequence_schema = %(schema)s
ORDER BY s.sequence_name;
"""

SQL_ENUMS = """
SELECT
    t.typname AS enum_name,
    string_agg(e.enumlabel, ', ' ORDER BY e.enumsortorder) AS enum_values
FROM pg_type t
JOIN pg_enum e ON t.oid = e.enumtypid
JOIN pg_namespace n ON t.typnamespace = n.oid
WHERE n.nspname = %(schema)s
GROUP BY t.typname
ORDER BY t.typname;
"""

SQL_EXTENSIONS = """
SELECT extname, extversion FROM pg_extension ORDER BY extname;
"""

SQL_FUNCTIONS = """
SELECT
    p.proname AS function_name,
    pg_get_function_arguments(p.oid) AS arguments,
    pg_get_function_result(p.oid) AS return_type,
    CASE p.prokind
        WHEN 'f' THEN 'function'
        WHEN 'p' THEN 'procedure'
        WHEN 'a' THEN 'aggregate'
        WHEN 'w' THEN 'window'
    END AS kind,
    pg_get_functiondef(p.oid) AS definition
FROM pg_proc p
JOIN pg_namespace n ON p.pronamespace = n.oid
WHERE n.nspname = %(schema)s
    AND p.proname NOT LIKE 'pg_%%'
ORDER BY p.proname;
"""

SQL_TRIGGERS = """
SELECT
    trigger_name,
    event_manipulation,
    event_object_table,
    action_statement,
    action_timing
FROM information_schema.triggers
WHERE trigger_schema = %(schema)s
ORDER BY event_object_table, trigger_name;
"""


def list_schemas(conn):
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(SQL_LIST_SCHEMAS)
        return [r['schema_name'] for r in cur.fetchall()]


def list_tables(conn, schema):
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(SQL_LIST_TABLES, {'schema': schema})
        return cur.fetchall()


def get_table_info(conn, schema, table):
    info = {'schema': schema, 'table': table}
    params = {'schema': schema, 'table': table}

    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(SQL_TABLE_COMMENT, params)
        row = cur.fetchone()
        info['comment'] = row['table_comment'] if row else None

        cur.execute(SQL_TABLE_COLUMNS, params)
        info['columns'] = cur.fetchall()

        cur.execute(SQL_TABLE_CONSTRAINTS, params)
        info['constraints'] = cur.fetchall()

        cur.execute(SQL_TABLE_INDEXES, params)
        info['indexes'] = cur.fetchall()

        cur.execute(SQL_TABLE_SIZE, params)
        info['size'] = cur.fetchone()

    return info


def get_enums(conn, schema):
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(SQL_ENUMS, {'schema': schema})
        return cur.fetchall()


def get_sequences(conn, schema):
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(SQL_SEQUENCES, {'schema': schema})
        return cur.fetchall()


def get_extensions(conn):
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(SQL_EXTENSIONS)
        return cur.fetchall()


def get_functions(conn, schema):
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(SQL_FUNCTIONS, {'schema': schema})
        return cur.fetchall()


def get_triggers(conn, schema):
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(SQL_TRIGGERS, {'schema': schema})
        return cur.fetchall()


# ============================================================
# DDL 生成
# ============================================================

def _col_type_str(col):
    """根据列信息构建类型字符串"""
    udt = col['udt_name']
    # 常见类型映射
    type_map = {
        'int4': 'INTEGER',
        'int8': 'BIGINT',
        'int2': 'SMALLINT',
        'float4': 'REAL',
        'float8': 'DOUBLE PRECISION',
        'bool': 'BOOLEAN',
        'varchar': 'VARCHAR',
        'bpchar': 'CHAR',
        'text': 'TEXT',
        'timestamptz': 'TIMESTAMP WITH TIME ZONE',
        'timestamp': 'TIMESTAMP',
        'date': 'DATE',
        'time': 'TIME',
        'timetz': 'TIME WITH TIME ZONE',
        'uuid': 'UUID',
        'jsonb': 'JSONB',
        'json': 'JSON',
        'numeric': 'NUMERIC',
        'bytea': 'BYTEA',
        'serial': 'SERIAL',
        'bigserial': 'BIGSERIAL',
    }

    base_type = type_map.get(udt, col['data_type'].upper())

    if udt in ('varchar', 'bpchar') and col['character_maximum_length']:
        return f"{base_type}({col['character_maximum_length']})"
    if udt == 'numeric' and col['numeric_precision']:
        if col['numeric_scale']:
            return f"NUMERIC({col['numeric_precision']},{col['numeric_scale']})"
        return f"NUMERIC({col['numeric_precision']})"
    # 数组类型
    if udt.startswith('_'):
        inner = type_map.get(udt[1:], udt[1:].upper())
        return f"{inner}[]"
    return base_type


def generate_ddl(conn, schema, tables=None):
    """为指定 schema 生成完整的 DDL"""
    lines = []
    lines.append(f"-- ============================================================")
    lines.append(f"-- DDL for schema: {schema}")
    lines.append(f"-- Generated by pg_inspector.py")
    lines.append(f"-- ============================================================\n")

    # Schema
    if schema != 'public':
        lines.append(f"CREATE SCHEMA IF NOT EXISTS {schema};\n")

    # Extensions
    exts = get_extensions(conn)
    if exts:
        lines.append("-- Extensions")
        for ext in exts:
            lines.append(f"CREATE EXTENSION IF NOT EXISTS \"{ext['extname']}\";")
        lines.append("")

    # Enums
    enums = get_enums(conn, schema)
    if enums:
        lines.append("-- Enum Types")
        for enum in enums:
            vals = ", ".join(f"'{v.strip()}'" for v in enum['enum_values'].split(','))
            lines.append(f"CREATE TYPE {schema}.{enum['enum_name']} AS ENUM ({vals});")
        lines.append("")

    # Sequences
    seqs = get_sequences(conn, schema)
    if seqs:
        lines.append("-- Sequences")
        for seq in seqs:
            lines.append(f"CREATE SEQUENCE IF NOT EXISTS {schema}.{seq['sequence_name']};")
        lines.append("")

    # Tables
    all_tables = list_tables(conn, schema)
    if tables:
        all_tables = [t for t in all_tables if t['tablename'] in tables]

    for tbl in all_tables:
        tname = tbl['tablename']
        info = get_table_info(conn, schema, tname)

        # Table comment
        if info['comment']:
            lines.append(f"-- {info['comment']}")

        lines.append(f"CREATE TABLE {schema}.{tname} (")
        col_defs = []
        for col in info['columns']:
            parts = [f"    {col['column_name']}"]
            parts.append(_col_type_str(col))
            if col['is_nullable'] == 'NO':
                parts.append("NOT NULL")
            if col['column_default']:
                parts.append(f"DEFAULT {col['column_default']}")
            col_defs.append(" ".join(parts))

        # Primary key / unique constraints inline
        pk_cols = []
        unique_constraints = {}
        fk_constraints = []
        check_constraints = []

        for con in info['constraints']:
            if con['constraint_type'] == 'PRIMARY KEY':
                pk_cols.append(con['column_name'])
            elif con['constraint_type'] == 'UNIQUE':
                unique_constraints.setdefault(con['constraint_name'], []).append(con['column_name'])
            elif con['constraint_type'] == 'FOREIGN KEY':
                fk_constraints.append(con)

        if pk_cols:
            col_defs.append(f"    PRIMARY KEY ({', '.join(pk_cols)})")

        for cname, ucols in unique_constraints.items():
            col_defs.append(f"    CONSTRAINT {cname} UNIQUE ({', '.join(ucols)})")

        for fk in fk_constraints:
            col_defs.append(
                f"    CONSTRAINT {fk['constraint_name']} FOREIGN KEY ({fk['column_name']}) "
                f"REFERENCES {fk['foreign_table_schema']}.{fk['foreign_table_name']}({fk['foreign_column_name']})"
            )

        lines.append(",\n".join(col_defs))
        lines.append(");\n")

        # Column comments
        for col in info['columns']:
            if col.get('column_comment'):
                lines.append(
                    f"COMMENT ON COLUMN {schema}.{tname}.{col['column_name']} IS '{col['column_comment']}';"
                )

        if info['comment']:
            lines.append(f"COMMENT ON TABLE {schema}.{tname} IS '{info['comment']}';\n")

        # Indexes (skip pkey)
        for idx in info['indexes']:
            if '_pkey' not in idx['indexname']:
                lines.append(f"{idx['indexdef']};")

        lines.append("")

    # Functions
    funcs = get_functions(conn, schema)
    if funcs:
        lines.append("-- Functions and Procedures")
        for fn in funcs:
            lines.append(f"{fn['definition']};\n")

    # Triggers
    triggers = get_triggers(conn, schema)
    if triggers:
        lines.append("-- Triggers")
        for trg in triggers:
            lines.append(
                f"-- Trigger: {trg['trigger_name']} on {trg['event_object_table']} "
                f"({trg['action_timing']} {trg['event_manipulation']})"
            )
            lines.append(f"{trg['action_statement']};\n")

    return "\n".join(lines)


# ============================================================
# 结构信息输出（JSON / Markdown）
# ============================================================

def export_schema_info(conn, schema, tables=None, fmt='markdown'):
    """导出表结构信息"""
    all_tables = list_tables(conn, schema)
    if tables:
        all_tables = [t for t in all_tables if t['tablename'] in tables]

    result = []
    for tbl in all_tables:
        info = get_table_info(conn, schema, tbl['tablename'])
        result.append(info)

    if fmt == 'json':
        # 将 Decimal 等类型转换为字符串
        return json.dumps(result, indent=2, default=str, ensure_ascii=False)

    # Markdown 格式
    lines = []
    for info in result:
        lines.append(f"## {info['schema']}.{info['table']}")
        if info['comment']:
            lines.append(f"> {info['comment']}\n")

        size = info['size']
        if size:
            lines.append(f"**大小**: 表 {size['table_size']} / 索引 {size['index_size']} / 总计 {size['total_size']}\n")

        # Columns
        lines.append("### 列定义\n")
        lines.append("| 列名 | 类型 | 可空 | 默认值 | 备注 |")
        lines.append("|------|------|------|--------|------|")
        for col in info['columns']:
            nullable = 'YES' if col['is_nullable'] == 'YES' else 'NO'
            default = col['column_default'] or ''
            comment = col.get('column_comment') or ''
            lines.append(f"| {col['column_name']} | {_col_type_str(col)} | {nullable} | {default} | {comment} |")

        # Constraints
        if info['constraints']:
            lines.append("\n### 约束\n")
            lines.append("| 约束名 | 类型 | 列 | 引用 |")
            lines.append("|--------|------|-----|------|")
            for con in info['constraints']:
                ref = ''
                if con['constraint_type'] == 'FOREIGN KEY':
                    ref = f"{con['foreign_table_schema']}.{con['foreign_table_name']}({con['foreign_column_name']})"
                lines.append(f"| {con['constraint_name']} | {con['constraint_type']} | {con['column_name']} | {ref} |")

        # Indexes
        if info['indexes']:
            lines.append("\n### 索引\n")
            for idx in info['indexes']:
                lines.append(f"- `{idx['indexname']}`: `{idx['indexdef']}`")

        lines.append("\n---\n")

    return "\n".join(lines)


# ============================================================
# CLI
# ============================================================

def main():
    parser = argparse.ArgumentParser(description='PostgreSQL 数据库元数据检查器')

    # 连接参数
    conn_group = parser.add_argument_group('连接参数')
    conn_group.add_argument('--dsn', help='完整数据库连接字符串 (DATABASE_URL)')
    conn_group.add_argument('--host', '-H', help='数据库主机 (PGHOST)')
    conn_group.add_argument('--port', '-p', help='数据库端口 (PGPORT)')
    conn_group.add_argument('--user', '-U', help='数据库用户 (PGUSER)')
    conn_group.add_argument('--password', '-W', help='数据库密码 (PGPASSWORD)')
    conn_group.add_argument('--dbname', '-d', help='数据库名称 (PGDATABASE)')
    conn_group.add_argument('--env-file', help='.env 文件路径 (默认: .env)')

    # 操作子命令
    sub = parser.add_subparsers(dest='command', help='可用命令')

    # schemas
    sub.add_parser('schemas', help='列出所有用户 schema')

    # tables
    p_tables = sub.add_parser('tables', help='列出指定 schema 下的表')
    p_tables.add_argument('--schema', '-s', default='public', help='Schema 名称')

    # inspect
    p_inspect = sub.add_parser('inspect', help='查看表结构详情')
    p_inspect.add_argument('--schema', '-s', default='public')
    p_inspect.add_argument('--table', '-t', nargs='*', help='表名（不指定则查看全部）')
    p_inspect.add_argument('--format', '-f', choices=['markdown', 'json'], default='markdown')

    # ddl
    p_ddl = sub.add_parser('ddl', help='生成 DDL 脚本')
    p_ddl.add_argument('--schema', '-s', default='public')
    p_ddl.add_argument('--table', '-t', nargs='*', help='表名（不指定则生成全部）')
    p_ddl.add_argument('--output', '-o', help='输出文件路径')

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
                flags = []
                if t['hasindexes']:
                    flags.append('idx')
                if t['hastriggers']:
                    flags.append('trg')
                flag_str = f" [{','.join(flags)}]" if flags else ''
                print(f"  {t['tablename']}{flag_str}")

        elif args.command == 'inspect':
            output = export_schema_info(conn, args.schema, args.table, args.format)
            print(output)

        elif args.command == 'ddl':
            ddl = generate_ddl(conn, args.schema, args.table)
            if args.output:
                with open(args.output, 'w', encoding="utf-8") as f:
                    f.write(ddl)
                print(f"DDL 已写入: {args.output}")
            else:
                print(ddl)

    finally:
        conn.close()


if __name__ == '__main__':
    main()
