#!/usr/bin/env python3
"""
database-tools 统一入口
薄路由层 — 所有业务逻辑由 scripts/ 和 lib/ 模块实现
"""

import argparse
import os
import sys

# 确保 scripts/ 和 lib/ 可导入
TOOL_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, TOOL_DIR)
sys.path.insert(0, os.path.join(TOOL_DIR, "scripts"))


def add_connection_args(parser):
    """添加通用连接参数"""
    conn = parser.add_argument_group("连接参数")
    conn.add_argument("--profile", help="使用配置文件中的 profile 名称")
    conn.add_argument("--dsn", help="完整数据库连接字符串")
    conn.add_argument("--host", "-H", help="数据库主机")
    conn.add_argument("--port", "-p", help="数据库端口")
    conn.add_argument("--user", "-U", help="数据库用户")
    conn.add_argument("--password", "-W", help="数据库密码")
    conn.add_argument("--dbname", "-d", help="数据库名称")
    conn.add_argument("--env-file", help=".env 文件路径")


def get_connection_for(engine, args):
    """基于引擎类型和参数获取连接"""
    from lib.connection import from_args
    # 创建一个带 engine 属性的命名空间
    args.engine = engine
    return from_args(args, engine=engine)


# ============================================================
# PG 子命令
# ============================================================

def cmd_pg(args):
    from scripts import pg_inspector, pg_index_advisor

    profile = getattr(args, "profile", None)
    if profile:
        from lib.connection import from_profile
        conn = from_profile(profile, password=getattr(args, "password", None))
    else:
        conn = pg_inspector.get_connection(args)

    try:
        sub = args.pg_command

        if sub == "schemas":
            for s in pg_inspector.list_schemas(conn):
                print(s)

        elif sub == "tables":
            tables = pg_inspector.list_tables(conn, args.schema)
            for t in tables:
                flags = []
                if t["hasindexes"]:
                    flags.append("idx")
                if t["hastriggers"]:
                    flags.append("trg")
                flag_str = f" [{','.join(flags)}]" if flags else ""
                print(f"  {t['tablename']}{flag_str}")

        elif sub == "inspect":
            output = pg_inspector.export_schema_info(
                conn, args.schema, args.table, args.format
            )
            print(output)

        elif sub == "ddl":
            ddl = pg_inspector.generate_ddl(conn, args.schema, args.table)
            if args.output:
                with open(args.output, "w") as f:
                    f.write(ddl)
                print(f"DDL 已写入: {args.output}")
            else:
                print(ddl)

        elif sub == "report":
            report = pg_index_advisor.generate_report(conn, args.schema)
            if args.output:
                with open(args.output, "w") as f:
                    f.write(report)
                print(f"报告已写入: {args.output}")
            else:
                print(report)

        elif sub == "optimize":
            concurrently = not getattr(args, "no_concurrently", False)
            ddl = pg_index_advisor.generate_optimization_ddl(conn, args.schema, concurrently)
            if args.output:
                with open(args.output, "w") as f:
                    f.write(ddl)
                print(f"优化脚本已写入: {args.output}")
            else:
                print(ddl)

        else:
            print(f"未知 PG 子命令: {sub}", file=sys.stderr)
            sys.exit(1)

    finally:
        conn.close()


# ============================================================
# MySQL 子命令
# ============================================================

def cmd_mysql(args):
    from scripts import mysql_inspector, mysql_index_advisor

    profile = getattr(args, "profile", None)
    if profile:
        from lib.connection import from_profile
        conn = from_profile(profile, password=getattr(args, "password", None))
    else:
        conn = mysql_inspector.get_connection(args)

    try:
        sub = args.mysql_command

        if sub == "schemas":
            for s in mysql_inspector.list_schemas(conn):
                print(s)

        elif sub == "tables":
            tables = mysql_inspector.list_tables(conn, args.schema)
            for t in tables:
                engine = t.get("engine") or ""
                comment = t.get("table_comment") or ""
                extra = f" [{engine}]" if engine else ""
                if comment:
                    extra += f" -- {comment}"
                print(f"  {t['table_name']}{extra}")

        elif sub == "inspect":
            output = mysql_inspector.export_schema_info(
                conn, args.schema, args.table, args.format
            )
            print(output)

        elif sub == "ddl":
            mode = getattr(args, "mode", "show")
            if mode == "show":
                ddl = mysql_inspector.generate_ddl_show(conn, args.schema, args.table)
            else:
                ddl = mysql_inspector.generate_ddl(conn, args.schema, args.table)
            if args.output:
                with open(args.output, "w") as f:
                    f.write(ddl)
                print(f"DDL 已写入: {args.output}")
            else:
                print(ddl)

        elif sub == "report":
            report = mysql_index_advisor.generate_report(conn, args.schema)
            if args.output:
                with open(args.output, "w") as f:
                    f.write(report)
                print(f"报告已写入: {args.output}")
            else:
                print(report)

        elif sub == "optimize":
            ddl = mysql_index_advisor.generate_optimization_ddl(conn, args.schema)
            if args.output:
                with open(args.output, "w") as f:
                    f.write(ddl)
                print(f"优化脚本已写入: {args.output}")
            else:
                print(ddl)

        else:
            print(f"未知 MySQL 子命令: {sub}", file=sys.stderr)
            sys.exit(1)

    finally:
        conn.close()


# ============================================================
# Diff 子命令
# ============================================================

def cmd_diff(args):
    from lib.snapshot import load_snapshot, export_pg, export_mysql
    from lib.differ import SchemaDiffer, MigrationGenerator
    from lib.formatters import MarkdownFormatter
    from lib.connection import from_profile, connect_pg, connect_mysql

    def resolve_source(spec, password=None):
        """解析 source/target: profile 名, DSN, 或 JSON 文件路径"""
        # JSON 文件
        if spec.endswith(".json") and os.path.exists(spec):
            return load_snapshot(spec)

        # profile
        from lib.config import get_profile
        prof = get_profile(spec)
        if prof:
            conn = from_profile(spec, password=password)
            engine = prof["engine"]
            schema = getattr(args, "schema", None) or "public"
            try:
                if engine in ("pg", "postgresql"):
                    return export_pg(conn, schema)
                else:
                    return export_mysql(conn, schema)
            finally:
                conn.close()

        # DSN
        if spec.startswith("postgresql://") or spec.startswith("postgres://"):
            conn = connect_pg(dsn=spec)
            schema = getattr(args, "schema", None) or "public"
            try:
                return export_pg(conn, schema)
            finally:
                conn.close()

        if spec.startswith("mysql://"):
            conn = connect_mysql(dsn=spec)
            schema = getattr(args, "schema", None)
            if not schema:
                from urllib.parse import urlparse
                schema = urlparse(spec).path.lstrip("/")
            try:
                return export_mysql(conn, schema)
            finally:
                conn.close()

        raise ValueError(f"无法解析来源: {spec} (支持 profile 名、DSN 或 .json 文件)")

    source = resolve_source(args.source, getattr(args, "password", None))
    target = resolve_source(args.target, getattr(args, "password", None))

    differ = SchemaDiffer()
    diff_result = differ.diff(source, target)

    # 格式化输出
    report = MarkdownFormatter.diff_report(diff_result)
    print(report)

    # 如果同引擎，生成迁移 DDL
    if source.db_engine == target.db_engine:
        gen = MigrationGenerator(source.db_engine)
        migration = gen.generate(diff_result)
        if migration.strip():
            print("\n---\n")
            print("# 迁移 DDL\n")
            print(migration)
    else:
        print("\n> 跨引擎对比（PG ↔ MySQL），仅生成对照报告，不生成迁移 DDL。\n")


# ============================================================
# Snapshot 子命令
# ============================================================

def cmd_snapshot(args):
    from lib.snapshot import export_pg, export_mysql, save_snapshot
    from lib.connection import from_profile
    from lib.config import get_profile

    profile = args.profile
    prof = get_profile(profile)
    if not prof:
        print(f"未找到 profile: {profile}", file=sys.stderr)
        sys.exit(1)

    conn = from_profile(profile, password=getattr(args, "password", None))
    engine = prof["engine"]
    schema = getattr(args, "schema", None)

    try:
        if engine in ("pg", "postgresql"):
            snapshot = export_pg(conn, schema or "public")
        else:
            db = prof.get("connection", {}).get("database") or prof.get("connection", {}).get("dbname")
            snapshot = export_mysql(conn, schema or db)
    finally:
        conn.close()

    output = args.output or f"snapshot_{profile}_{snapshot.schema_name}.json"
    save_snapshot(snapshot, output)
    print(f"快照已保存: {output}")


# ============================================================
# Config 子命令
# ============================================================

def cmd_config(args):
    from lib import config

    sub = args.config_command

    if sub == "set":
        params = {}
        if args.host:
            params["host"] = args.host
        if args.port:
            params["port"] = args.port
        if args.user:
            params["user"] = args.user
        if args.dbname:
            params["dbname"] = args.dbname
        if args.dsn:
            params["dsn"] = args.dsn

        config.set_profile(args.name, args.engine, **params)
        print(f"Profile '{args.name}' 已保存")

    elif sub == "list":
        profiles = config.list_profiles()
        if not profiles:
            print("没有已配置的 profile")
            return
        for name, prof in profiles.items():
            eng = prof.get("engine", "?")
            conn_info = prof.get("connection", {})
            host = conn_info.get("host", "")
            port = conn_info.get("port", "")
            db = conn_info.get("dbname") or conn_info.get("database", "")
            print(f"  {name}: [{eng}] {host}:{port}/{db}")

    elif sub == "remove":
        if config.remove_profile(args.name):
            print(f"Profile '{args.name}' 已删除")
        else:
            print(f"未找到 profile: {args.name}", file=sys.stderr)
            sys.exit(1)


# ============================================================
# 主入口
# ============================================================

def main():
    parser = argparse.ArgumentParser(
        description="database-tools 统一入口 — PostgreSQL / MySQL 数据库工具集",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  %(prog)s pg schemas --dsn postgresql://user:pass@localhost/mydb
  %(prog)s pg inspect --schema public --profile dev
  %(prog)s mysql tables --schema mydb --host 127.0.0.1 --user root
  %(prog)s diff --source dev --target prod
  %(prog)s snapshot --profile dev -o dev_snapshot.json
  %(prog)s config set dev --engine pg --host localhost --dbname mydb
  %(prog)s config list
""",
    )

    sub = parser.add_subparsers(dest="command", help="可用命令组")

    # ---- pg ----
    pg_parser = sub.add_parser("pg", help="PostgreSQL 相关命令")
    add_connection_args(pg_parser)
    pg_sub = pg_parser.add_subparsers(dest="pg_command", help="PG 子命令")

    pg_sub.add_parser("schemas", help="列出所有用户 schema")

    p = pg_sub.add_parser("tables", help="列出指定 schema 下的表")
    p.add_argument("--schema", "-s", default="public")

    p = pg_sub.add_parser("inspect", help="查看表结构详情")
    p.add_argument("--schema", "-s", default="public")
    p.add_argument("--table", "-t", nargs="*")
    p.add_argument("--format", "-f", choices=["markdown", "json"], default="markdown")

    p = pg_sub.add_parser("ddl", help="生成 DDL 脚本")
    p.add_argument("--schema", "-s", default="public")
    p.add_argument("--table", "-t", nargs="*")
    p.add_argument("--output", "-o")

    p = pg_sub.add_parser("report", help="生成完整性能分析报告")
    p.add_argument("--schema", "-s", default="public")
    p.add_argument("--output", "-o")

    p = pg_sub.add_parser("optimize", help="生成优化 DDL 脚本")
    p.add_argument("--schema", "-s", default="public")
    p.add_argument("--no-concurrently", action="store_true")
    p.add_argument("--output", "-o")

    # ---- mysql ----
    mysql_parser = sub.add_parser("mysql", help="MySQL 相关命令")
    add_connection_args(mysql_parser)
    mysql_sub = mysql_parser.add_subparsers(dest="mysql_command", help="MySQL 子命令")

    mysql_sub.add_parser("schemas", help="列出所有用户数据库")

    p = mysql_sub.add_parser("tables", help="列出指定数据库下的表")
    p.add_argument("--schema", "-s", required=True)

    p = mysql_sub.add_parser("inspect", help="查看表结构详情")
    p.add_argument("--schema", "-s", required=True)
    p.add_argument("--table", "-t", nargs="*")
    p.add_argument("--format", "-f", choices=["markdown", "json"], default="markdown")

    p = mysql_sub.add_parser("ddl", help="生成 DDL 脚本")
    p.add_argument("--schema", "-s", required=True)
    p.add_argument("--table", "-t", nargs="*")
    p.add_argument("--output", "-o")
    p.add_argument("--mode", choices=["show", "build"], default="show")

    p = mysql_sub.add_parser("report", help="生成完整性能分析报告")
    p.add_argument("--schema", "-s", required=True)
    p.add_argument("--output", "-o")

    p = mysql_sub.add_parser("optimize", help="生成优化 DDL 脚本")
    p.add_argument("--schema", "-s", required=True)
    p.add_argument("--output", "-o")

    # ---- diff ----
    diff_parser = sub.add_parser("diff", help="跨数据库结构对比")
    diff_parser.add_argument("--password", "-W", help="数据库密码")
    diff_parser.add_argument("--source", required=True, help="源 (profile名 / DSN / snapshot.json)")
    diff_parser.add_argument("--target", required=True, help="目标 (profile名 / DSN / snapshot.json)")
    diff_parser.add_argument("--schema", "-s", help="Schema/数据库名称")

    # ---- snapshot ----
    snap_parser = sub.add_parser("snapshot", help="导出结构快照")
    snap_parser.add_argument("--profile", required=True, help="Profile 名称")
    snap_parser.add_argument("--password", "-W", help="数据库密码")
    snap_parser.add_argument("--schema", "-s", help="Schema/数据库名称")
    snap_parser.add_argument("--output", "-o", help="输出文件路径")

    # ---- config ----
    config_parser = sub.add_parser("config", help="配置管理")
    config_sub = config_parser.add_subparsers(dest="config_command", help="配置子命令")

    p_set = config_sub.add_parser("set", help="设置 profile")
    p_set.add_argument("name", help="Profile 名称")
    p_set.add_argument("--engine", required=True, choices=["pg", "mysql"])
    p_set.add_argument("--host")
    p_set.add_argument("--port")
    p_set.add_argument("--user")
    p_set.add_argument("--dbname")
    p_set.add_argument("--dsn")

    config_sub.add_parser("list", help="列出所有 profile")

    p_rm = config_sub.add_parser("remove", help="删除 profile")
    p_rm.add_argument("name", help="Profile 名称")

    # 解析
    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        sys.exit(1)

    if args.command == "pg":
        if not getattr(args, "pg_command", None):
            pg_parser.print_help()
            sys.exit(1)
        cmd_pg(args)

    elif args.command == "mysql":
        if not getattr(args, "mysql_command", None):
            mysql_parser.print_help()
            sys.exit(1)
        cmd_mysql(args)

    elif args.command == "diff":
        cmd_diff(args)

    elif args.command == "snapshot":
        cmd_snapshot(args)

    elif args.command == "config":
        if not getattr(args, "config_command", None):
            config_parser.print_help()
            sys.exit(1)
        cmd_config(args)


if __name__ == "__main__":
    main()
