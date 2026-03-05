"""
结构快照导出/导入
支持从 PG / MySQL 连接导出 SchemaSnapshot，以及 JSON 序列化/反序列化
"""

import json
from datetime import datetime

from .schema_model import (
    SchemaSnapshot, Table, Column, Constraint, Index, DbEngine,
)


def export_pg(conn, schema="public"):
    """从 PostgreSQL 连接导出 SchemaSnapshot"""
    from psycopg2.extras import RealDictCursor

    snapshot = SchemaSnapshot(
        db_engine=DbEngine.POSTGRESQL.value,
        schema_name=schema,
        snapshot_time=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    )

    # 提取连接信息
    dsn_params = conn.get_dsn_parameters()
    snapshot.host = dsn_params.get("host", "")
    snapshot.port = int(dsn_params.get("port", 0) or 0)
    snapshot.database = dsn_params.get("dbname", "")

    # 类型映射（复用 pg_inspector 的逻辑）
    type_map = {
        'int4': 'INTEGER', 'int8': 'BIGINT', 'int2': 'SMALLINT',
        'float4': 'REAL', 'float8': 'DOUBLE PRECISION',
        'bool': 'BOOLEAN', 'varchar': 'VARCHAR', 'bpchar': 'CHAR',
        'text': 'TEXT', 'timestamptz': 'TIMESTAMP WITH TIME ZONE',
        'timestamp': 'TIMESTAMP', 'date': 'DATE', 'time': 'TIME',
        'uuid': 'UUID', 'jsonb': 'JSONB', 'json': 'JSON',
        'numeric': 'NUMERIC', 'bytea': 'BYTEA',
    }

    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        # 列出表
        cur.execute(
            "SELECT tablename FROM pg_tables WHERE schemaname = %s ORDER BY tablename",
            (schema,),
        )
        table_names = [r["tablename"] for r in cur.fetchall()]

        for tname in table_names:
            params = {"schema": schema, "table": tname}

            # 列
            cur.execute("""
                SELECT c.column_name, c.data_type, c.udt_name,
                       c.character_maximum_length, c.numeric_precision, c.numeric_scale,
                       c.is_nullable, c.column_default, c.ordinal_position,
                       pgd.description AS column_comment
                FROM information_schema.columns c
                LEFT JOIN pg_catalog.pg_statio_all_tables st
                    ON st.schemaname = c.table_schema AND st.relname = c.table_name
                LEFT JOIN pg_catalog.pg_description pgd
                    ON pgd.objoid = st.relid AND pgd.objsubid = c.ordinal_position
                WHERE c.table_schema = %(schema)s AND c.table_name = %(table)s
                ORDER BY c.ordinal_position
            """, params)
            columns = []
            for r in cur.fetchall():
                udt = r["udt_name"]
                normalized = type_map.get(udt, r["data_type"].upper())
                raw = udt
                if udt in ("varchar", "bpchar") and r["character_maximum_length"]:
                    raw = f"{udt}({r['character_maximum_length']})"
                elif udt == "numeric" and r["numeric_precision"]:
                    if r["numeric_scale"]:
                        raw = f"numeric({r['numeric_precision']},{r['numeric_scale']})"
                    else:
                        raw = f"numeric({r['numeric_precision']})"

                columns.append(Column(
                    name=r["column_name"],
                    data_type=normalized,
                    raw_type=raw,
                    is_nullable=(r["is_nullable"] == "YES"),
                    default=r["column_default"],
                    max_length=r["character_maximum_length"],
                    numeric_precision=r["numeric_precision"],
                    numeric_scale=r["numeric_scale"],
                    comment=r["column_comment"],
                    ordinal_position=r["ordinal_position"],
                ))

            # 约束
            cur.execute("""
                SELECT tc.constraint_name, tc.constraint_type,
                       kcu.column_name,
                       ccu.table_schema AS foreign_table_schema,
                       ccu.table_name AS foreign_table_name,
                       ccu.column_name AS foreign_column_name
                FROM information_schema.table_constraints tc
                JOIN information_schema.key_column_usage kcu
                    ON tc.constraint_name = kcu.constraint_name
                    AND tc.table_schema = kcu.table_schema
                LEFT JOIN information_schema.constraint_column_usage ccu
                    ON ccu.constraint_name = tc.constraint_name
                    AND ccu.table_schema = tc.table_schema
                WHERE tc.table_schema = %(schema)s AND tc.table_name = %(table)s
                ORDER BY tc.constraint_type, tc.constraint_name
            """, params)

            constraints_map = {}
            for r in cur.fetchall():
                key = r["constraint_name"]
                if key not in constraints_map:
                    constraints_map[key] = Constraint(
                        name=key,
                        type=r["constraint_type"],
                        columns=[],
                        foreign_schema=r.get("foreign_table_schema"),
                        foreign_table=r.get("foreign_table_name"),
                        foreign_columns=[],
                    )
                c = constraints_map[key]
                if r["column_name"] and r["column_name"] not in c.columns:
                    c.columns.append(r["column_name"])
                if r["constraint_type"] == "FOREIGN KEY" and r.get("foreign_column_name"):
                    if r["foreign_column_name"] not in c.foreign_columns:
                        c.foreign_columns.append(r["foreign_column_name"])

            # 索引
            cur.execute("""
                SELECT indexname, indexdef
                FROM pg_indexes
                WHERE schemaname = %(schema)s AND tablename = %(table)s
                ORDER BY indexname
            """, params)
            indexes = []
            for r in cur.fetchall():
                is_unique = "UNIQUE" in (r["indexdef"] or "").upper()
                is_primary = "_pkey" in r["indexname"]
                indexes.append(Index(
                    name=r["indexname"],
                    is_unique=is_unique,
                    is_primary=is_primary,
                    definition=r["indexdef"],
                ))

            # 大小
            cur.execute("""
                SELECT
                    pg_size_pretty(pg_total_relation_size(%(fqn)s)) AS total_size,
                    pg_size_pretty(pg_relation_size(%(fqn)s)) AS table_size,
                    pg_size_pretty(pg_total_relation_size(%(fqn)s)
                        - pg_relation_size(%(fqn)s)) AS index_size
            """, {"fqn": f"{schema}.{tname}"})
            size_row = cur.fetchone()

            # 注释
            cur.execute("""
                SELECT obj_description(c.oid) AS table_comment
                FROM pg_class c
                JOIN pg_namespace n ON n.oid = c.relnamespace
                WHERE n.nspname = %(schema)s AND c.relname = %(table)s
            """, params)
            comment_row = cur.fetchone()

            table = Table(
                schema=schema,
                name=tname,
                columns=columns,
                constraints=list(constraints_map.values()),
                indexes=indexes,
                comment=comment_row["table_comment"] if comment_row else None,
                table_size=size_row["table_size"] if size_row else None,
                index_size=size_row["index_size"] if size_row else None,
                total_size=size_row["total_size"] if size_row else None,
            )
            snapshot.tables.append(table)

    return snapshot


def export_mysql(conn, schema):
    """从 MySQL 连接导出 SchemaSnapshot"""
    snapshot = SchemaSnapshot(
        db_engine=DbEngine.MYSQL.value,
        schema_name=schema,
        snapshot_time=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    )

    with conn.cursor() as cur:
        # 连接信息
        cur.execute("SELECT @@hostname AS host, @@port AS port")
        info = cur.fetchone()
        snapshot.host = info.get("host", "")
        snapshot.port = int(info.get("port", 0) or 0)
        snapshot.database = schema

        # 列出表
        cur.execute(
            "SELECT TABLE_NAME FROM information_schema.TABLES "
            "WHERE TABLE_SCHEMA = %s AND TABLE_TYPE = 'BASE TABLE' ORDER BY TABLE_NAME",
            (schema,),
        )
        table_names = [r["TABLE_NAME"] for r in cur.fetchall()]

        # MySQL 常用类型规范化
        mysql_type_map = {
            "tinyint": "TINYINT", "smallint": "SMALLINT", "mediumint": "MEDIUMINT",
            "int": "INTEGER", "bigint": "BIGINT",
            "float": "FLOAT", "double": "DOUBLE", "decimal": "DECIMAL",
            "char": "CHAR", "varchar": "VARCHAR", "text": "TEXT",
            "tinytext": "TEXT", "mediumtext": "TEXT", "longtext": "TEXT",
            "blob": "BLOB", "tinyblob": "BLOB", "mediumblob": "BLOB", "longblob": "BLOB",
            "date": "DATE", "datetime": "DATETIME", "timestamp": "TIMESTAMP",
            "time": "TIME", "year": "YEAR",
            "json": "JSON", "enum": "ENUM", "set": "SET",
        }

        for tname in table_names:
            # 列
            cur.execute("""
                SELECT COLUMN_NAME, DATA_TYPE, COLUMN_TYPE,
                       CHARACTER_MAXIMUM_LENGTH, NUMERIC_PRECISION, NUMERIC_SCALE,
                       IS_NULLABLE, COLUMN_DEFAULT, ORDINAL_POSITION,
                       EXTRA, COLUMN_COMMENT
                FROM information_schema.COLUMNS
                WHERE TABLE_SCHEMA = %s AND TABLE_NAME = %s
                ORDER BY ORDINAL_POSITION
            """, (schema, tname))

            columns = []
            for r in cur.fetchall():
                dt = r["DATA_TYPE"].lower()
                normalized = mysql_type_map.get(dt, dt.upper())
                auto_inc = "auto_increment" in (r["EXTRA"] or "").lower()
                columns.append(Column(
                    name=r["COLUMN_NAME"],
                    data_type=normalized,
                    raw_type=r["COLUMN_TYPE"],
                    is_nullable=(r["IS_NULLABLE"] == "YES"),
                    default=r["COLUMN_DEFAULT"],
                    max_length=r["CHARACTER_MAXIMUM_LENGTH"],
                    numeric_precision=r["NUMERIC_PRECISION"],
                    numeric_scale=r["NUMERIC_SCALE"],
                    comment=r["COLUMN_COMMENT"] if r["COLUMN_COMMENT"] else None,
                    auto_increment=auto_inc,
                    ordinal_position=r["ORDINAL_POSITION"],
                ))

            # 约束
            cur.execute("""
                SELECT tc.CONSTRAINT_NAME, tc.CONSTRAINT_TYPE,
                       kcu.COLUMN_NAME,
                       kcu.REFERENCED_TABLE_SCHEMA,
                       kcu.REFERENCED_TABLE_NAME,
                       kcu.REFERENCED_COLUMN_NAME
                FROM information_schema.TABLE_CONSTRAINTS tc
                JOIN information_schema.KEY_COLUMN_USAGE kcu
                    ON tc.CONSTRAINT_NAME = kcu.CONSTRAINT_NAME
                    AND tc.TABLE_SCHEMA = kcu.TABLE_SCHEMA
                    AND tc.TABLE_NAME = kcu.TABLE_NAME
                WHERE tc.TABLE_SCHEMA = %s AND tc.TABLE_NAME = %s
                ORDER BY tc.CONSTRAINT_TYPE, tc.CONSTRAINT_NAME, kcu.ORDINAL_POSITION
            """, (schema, tname))

            constraints_map = {}
            for r in cur.fetchall():
                key = r["CONSTRAINT_NAME"]
                if key not in constraints_map:
                    constraints_map[key] = Constraint(
                        name=key,
                        type=r["CONSTRAINT_TYPE"],
                        columns=[],
                        foreign_schema=r.get("REFERENCED_TABLE_SCHEMA"),
                        foreign_table=r.get("REFERENCED_TABLE_NAME"),
                        foreign_columns=[],
                    )
                c = constraints_map[key]
                if r["COLUMN_NAME"] and r["COLUMN_NAME"] not in c.columns:
                    c.columns.append(r["COLUMN_NAME"])
                if r["CONSTRAINT_TYPE"] == "FOREIGN KEY" and r.get("REFERENCED_COLUMN_NAME"):
                    if r["REFERENCED_COLUMN_NAME"] not in c.foreign_columns:
                        c.foreign_columns.append(r["REFERENCED_COLUMN_NAME"])

            # 索引
            cur.execute("""
                SELECT INDEX_NAME,
                       GROUP_CONCAT(COLUMN_NAME ORDER BY SEQ_IN_INDEX) AS columns,
                       NON_UNIQUE, INDEX_TYPE
                FROM information_schema.STATISTICS
                WHERE TABLE_SCHEMA = %s AND TABLE_NAME = %s
                GROUP BY INDEX_NAME, NON_UNIQUE, INDEX_TYPE
                ORDER BY INDEX_NAME
            """, (schema, tname))

            indexes = []
            for r in cur.fetchall():
                indexes.append(Index(
                    name=r["INDEX_NAME"],
                    columns=[c.strip() for c in r["columns"].split(",")],
                    is_unique=not r["NON_UNIQUE"],
                    is_primary=(r["INDEX_NAME"] == "PRIMARY"),
                    index_type=r["INDEX_TYPE"],
                ))

            # 大小、引擎
            cur.execute("""
                SELECT TABLE_ROWS, DATA_LENGTH, INDEX_LENGTH,
                       ENGINE, TABLE_COLLATION, TABLE_COMMENT
                FROM information_schema.TABLES
                WHERE TABLE_SCHEMA = %s AND TABLE_NAME = %s
            """, (schema, tname))
            meta = cur.fetchone()

            table = Table(
                schema=schema,
                name=tname,
                columns=columns,
                constraints=list(constraints_map.values()),
                indexes=indexes,
                comment=meta["TABLE_COMMENT"] if meta and meta["TABLE_COMMENT"] else None,
                table_size=f"{round((meta['DATA_LENGTH'] or 0) / 1024 / 1024, 2)} MB" if meta else None,
                index_size=f"{round((meta['INDEX_LENGTH'] or 0) / 1024 / 1024, 2)} MB" if meta else None,
                total_size=f"{round(((meta['DATA_LENGTH'] or 0) + (meta['INDEX_LENGTH'] or 0)) / 1024 / 1024, 2)} MB" if meta else None,
                engine=meta["ENGINE"] if meta else None,
                collation=meta["TABLE_COLLATION"] if meta else None,
                row_count=meta["TABLE_ROWS"] if meta else None,
            )
            snapshot.tables.append(table)

    return snapshot


def save_snapshot(snapshot, filepath):
    """保存快照到 JSON 文件"""
    with open(filepath, "w", encoding="utf-8") as f:
        f.write(snapshot.to_json())


def load_snapshot(filepath):
    """从 JSON 文件加载快照"""
    with open(filepath, "r", encoding="utf-8") as f:
        return SchemaSnapshot.from_json(f.read())
