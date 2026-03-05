"""
跨数据库结构对比引擎
支持同引擎 diff + 迁移 DDL 生成，跨引擎仅生成对照报告
"""

from .schema_model import SchemaSnapshot, DbEngine


class SchemaDiffer:
    """对比两个 SchemaSnapshot"""

    def diff(self, source: SchemaSnapshot, target: SchemaSnapshot) -> dict:
        """
        对比 source 和 target，返回 diff 结果
        语义: source → target 的变更（target 是期望状态）
        """
        result = {
            "source_info": f"{source.db_engine} {source.host}:{source.port}/{source.database} ({source.schema_name})",
            "target_info": f"{target.db_engine} {target.host}:{target.port}/{target.database} ({target.schema_name})",
            "same_engine": source.db_engine == target.db_engine,
            "source_engine": source.db_engine,
            "target_engine": target.db_engine,
            "added_tables": [],
            "removed_tables": [],
            "modified_tables": {},
        }

        source_tables = {t.name: t for t in source.tables}
        target_tables = {t.name: t for t in target.tables}

        source_names = set(source_tables.keys())
        target_names = set(target_tables.keys())

        # 新增表（在 target 中有，source 中没有）
        result["added_tables"] = sorted(target_names - source_names)

        # 删除表（在 source 中有，target 中没有）
        result["removed_tables"] = sorted(source_names - target_names)

        # 修改表
        common = source_names & target_names
        for tname in sorted(common):
            changes = self._diff_table(source_tables[tname], target_tables[tname])
            if changes:
                result["modified_tables"][tname] = changes

        return result

    def _diff_table(self, src_table, tgt_table) -> dict | None:
        """对比两个表，返回变更详情"""
        changes = {
            "added_columns": [],
            "removed_columns": [],
            "modified_columns": [],
            "added_indexes": [],
            "removed_indexes": [],
            "added_constraints": [],
            "removed_constraints": [],
        }

        # 列对比
        src_cols = {c.name: c for c in src_table.columns}
        tgt_cols = {c.name: c for c in tgt_table.columns}

        for name in sorted(set(tgt_cols.keys()) - set(src_cols.keys())):
            c = tgt_cols[name]
            changes["added_columns"].append({
                "name": name,
                "data_type": c.data_type,
                "raw_type": c.raw_type,
                "is_nullable": c.is_nullable,
                "default": c.default,
            })

        for name in sorted(set(src_cols.keys()) - set(tgt_cols.keys())):
            changes["removed_columns"].append({"name": name})

        for name in sorted(set(src_cols.keys()) & set(tgt_cols.keys())):
            col_changes = self._diff_column(src_cols[name], tgt_cols[name])
            if col_changes:
                changes["modified_columns"].append({
                    "name": name,
                    "changes": col_changes,
                })

        # 索引对比
        src_idx = {i.name: i for i in src_table.indexes}
        tgt_idx = {i.name: i for i in tgt_table.indexes}

        for name in sorted(set(tgt_idx.keys()) - set(src_idx.keys())):
            idx = tgt_idx[name]
            changes["added_indexes"].append({
                "name": name,
                "columns": idx.columns,
                "is_unique": idx.is_unique,
                "definition": idx.definition,
            })

        for name in sorted(set(src_idx.keys()) - set(tgt_idx.keys())):
            changes["removed_indexes"].append({"name": name})

        # 约束对比
        src_con = {c.name: c for c in src_table.constraints}
        tgt_con = {c.name: c for c in tgt_table.constraints}

        for name in sorted(set(tgt_con.keys()) - set(src_con.keys())):
            con = tgt_con[name]
            changes["added_constraints"].append({
                "name": name,
                "type": con.type,
                "columns": con.columns,
            })

        for name in sorted(set(src_con.keys()) - set(tgt_con.keys())):
            changes["removed_constraints"].append({"name": name})

        # 检查是否有实质变更
        has_changes = any(v for v in changes.values() if v)
        return changes if has_changes else None

    def _diff_column(self, src, tgt) -> dict | None:
        """对比两个列，返回变更详情 dict"""
        changes = {}

        if src.data_type != tgt.data_type:
            changes["type"] = {"from": src.data_type, "to": tgt.data_type}
        elif src.raw_type != tgt.raw_type:
            changes["raw_type"] = {"from": src.raw_type, "to": tgt.raw_type}

        if src.is_nullable != tgt.is_nullable:
            changes["nullable"] = {"from": src.is_nullable, "to": tgt.is_nullable}

        if src.default != tgt.default:
            changes["default"] = {"from": src.default, "to": tgt.default}

        return changes if changes else None


class MigrationGenerator:
    """基于 diff 结果生成迁移 DDL"""

    def __init__(self, engine: str):
        self.engine = engine

    def generate(self, diff_result: dict) -> str:
        """生成迁移 DDL"""
        if not diff_result.get("same_engine"):
            return "-- 跨引擎对比，不生成迁移 DDL\n"

        lines = []
        lines.append("-- ============================================================")
        lines.append("-- 迁移 DDL (source → target)")
        lines.append("-- ============================================================\n")

        is_pg = self.engine in (DbEngine.POSTGRESQL.value, "pg", "postgresql")
        q = '"' if is_pg else '`'

        # 删除表
        for tname in diff_result.get("removed_tables", []):
            lines.append(f"DROP TABLE IF EXISTS {q}{tname}{q};")

        # 新增表（仅注释提示，完整 DDL 需从目标导出）
        for tname in diff_result.get("added_tables", []):
            lines.append(f"-- TODO: CREATE TABLE {q}{tname}{q} (从目标数据库导出完整 DDL)")

        # 修改表
        for tname, changes in diff_result.get("modified_tables", {}).items():
            lines.append(f"\n-- Table: {tname}")

            for col in changes.get("removed_columns", []):
                lines.append(f"ALTER TABLE {q}{tname}{q} DROP COLUMN {q}{col['name']}{q};")

            for col in changes.get("added_columns", []):
                nullable = "" if col.get("is_nullable") else " NOT NULL"
                default = f" DEFAULT {col['default']}" if col.get("default") else ""
                if is_pg:
                    lines.append(
                        f"ALTER TABLE {q}{tname}{q} ADD COLUMN "
                        f"{q}{col['name']}{q} {col['data_type']}{nullable}{default};"
                    )
                else:
                    lines.append(
                        f"ALTER TABLE {q}{tname}{q} ADD COLUMN "
                        f"{q}{col['name']}{q} {col.get('raw_type', col['data_type'])}{nullable}{default};"
                    )

            for col in changes.get("modified_columns", []):
                ch = col["changes"]
                desc_parts = []
                if "type" in ch:
                    desc_parts.append(f"类型: {ch['type']['from']} → {ch['type']['to']}")
                if "raw_type" in ch:
                    desc_parts.append(f"原始类型: {ch['raw_type']['from']} → {ch['raw_type']['to']}")
                if "nullable" in ch:
                    s = "NULL" if ch["nullable"]["from"] else "NOT NULL"
                    t = "NULL" if ch["nullable"]["to"] else "NOT NULL"
                    desc_parts.append(f"可空: {s} → {t}")
                if "default" in ch:
                    desc_parts.append(f"默认值: {ch['default']['from']!r} → {ch['default']['to']!r}")
                desc = "; ".join(desc_parts)
                lines.append(f"-- ALTER COLUMN {q}{col['name']}{q}: {desc}")

                if is_pg:
                    if "type" in ch:
                        lines.append(
                            f"ALTER TABLE {q}{tname}{q} ALTER COLUMN "
                            f"{q}{col['name']}{q} TYPE {ch['type']['to']};"
                        )
                    elif "raw_type" in ch:
                        lines.append(
                            f"ALTER TABLE {q}{tname}{q} ALTER COLUMN "
                            f"{q}{col['name']}{q} TYPE {ch['raw_type']['to']};"
                        )
                    if "nullable" in ch:
                        if not ch["nullable"]["to"]:
                            lines.append(
                                f"ALTER TABLE {q}{tname}{q} ALTER COLUMN "
                                f"{q}{col['name']}{q} SET NOT NULL;"
                            )
                        else:
                            lines.append(
                                f"ALTER TABLE {q}{tname}{q} ALTER COLUMN "
                                f"{q}{col['name']}{q} DROP NOT NULL;"
                            )
                    if "default" in ch:
                        new_def = ch["default"]["to"]
                        if new_def is None:
                            lines.append(
                                f"ALTER TABLE {q}{tname}{q} ALTER COLUMN "
                                f"{q}{col['name']}{q} DROP DEFAULT;"
                            )
                        else:
                            lines.append(
                                f"ALTER TABLE {q}{tname}{q} ALTER COLUMN "
                                f"{q}{col['name']}{q} SET DEFAULT {new_def};"
                            )
                else:
                    lines.append(
                        f"-- 请手动确认完整列定义后执行 MODIFY COLUMN"
                    )

            for idx in changes.get("removed_indexes", []):
                if is_pg:
                    lines.append(f"DROP INDEX IF EXISTS {q}{idx['name']}{q};")
                else:
                    lines.append(f"ALTER TABLE {q}{tname}{q} DROP INDEX {q}{idx['name']}{q};")

            for idx in changes.get("added_indexes", []):
                if idx.get("definition"):
                    lines.append(f"{idx['definition']};")
                else:
                    unique = "UNIQUE " if idx.get("is_unique") else ""
                    cols_str = ", ".join(f"{q}{c}{q}" for c in idx.get("columns", []))
                    if is_pg:
                        lines.append(
                            f"CREATE {unique}INDEX {q}{idx['name']}{q} "
                            f"ON {q}{tname}{q} ({cols_str});"
                        )
                    else:
                        lines.append(
                            f"ALTER TABLE {q}{tname}{q} ADD {unique}INDEX "
                            f"{q}{idx['name']}{q} ({cols_str});"
                        )

            for con in changes.get("removed_constraints", []):
                if is_pg:
                    lines.append(
                        f"ALTER TABLE {q}{tname}{q} DROP CONSTRAINT IF EXISTS {q}{con['name']}{q};"
                    )
                else:
                    lines.append(
                        f"ALTER TABLE {q}{tname}{q} DROP FOREIGN KEY {q}{con['name']}{q};"
                    )

            for con in changes.get("added_constraints", []):
                cols_str = ", ".join(f"{q}{c}{q}" for c in con.get("columns", []))
                if con["type"] == "PRIMARY KEY":
                    lines.append(
                        f"ALTER TABLE {q}{tname}{q} ADD PRIMARY KEY ({cols_str});"
                    )
                elif con["type"] == "UNIQUE":
                    lines.append(
                        f"ALTER TABLE {q}{tname}{q} ADD CONSTRAINT "
                        f"{q}{con['name']}{q} UNIQUE ({cols_str});"
                    )

        if len(lines) <= 3:
            lines.append("-- 无需迁移，结构完全一致。")

        return "\n".join(lines)
