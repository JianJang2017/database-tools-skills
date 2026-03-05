"""
统一输出格式化
支持 Markdown 和 JSON 格式输出
"""

import json
from datetime import datetime
from decimal import Decimal


class _Encoder(json.JSONEncoder):
    """处理 Decimal、datetime 等类型的 JSON 编码器"""
    def default(self, obj):
        if isinstance(obj, Decimal):
            return str(obj)
        if isinstance(obj, datetime):
            return obj.strftime("%Y-%m-%d %H:%M:%S")
        return super().default(obj)


class MarkdownFormatter:
    """Markdown 格式化输出"""

    @staticmethod
    def heading(text, level=2):
        return f"\n{'#' * level} {text}\n"

    @staticmethod
    def table(headers, rows):
        """生成 Markdown 表格"""
        if not rows:
            return ""
        lines = []
        lines.append("| " + " | ".join(headers) + " |")
        lines.append("|" + "|".join("------" for _ in headers) + "|")
        for row in rows:
            cells = [str(c) if c is not None else "" for c in row]
            lines.append("| " + " | ".join(cells) + " |")
        return "\n".join(lines)

    @staticmethod
    def table_info(table_data):
        """
        格式化单个表的结构信息
        table_data: dict with keys schema, name, columns, constraints, indexes, comment, size
        """
        lines = []
        schema = table_data.get("schema", "")
        name = table_data.get("name", "")
        lines.append(f"## {schema}.{name}")

        comment = table_data.get("comment")
        if comment:
            lines.append(f"> {comment}\n")

        size = table_data.get("size")
        if size:
            ts = size.get("table_size", "")
            isz = size.get("index_size", "")
            total = size.get("total_size", "")
            lines.append(f"**大小**: 表 {ts} / 索引 {isz} / 总计 {total}\n")

        # Columns
        columns = table_data.get("columns", [])
        if columns:
            lines.append("### 列定义\n")
            headers = ["列名", "类型", "可空", "默认值", "备注"]
            rows = []
            for col in columns:
                nullable = "YES" if col.get("is_nullable") in (True, "YES") else "NO"
                default = col.get("default") or col.get("column_default") or ""
                cm = col.get("comment") or col.get("column_comment") or ""
                type_str = col.get("type_display") or col.get("data_type", "")
                rows.append([
                    col.get("name") or col.get("column_name", ""),
                    type_str,
                    nullable,
                    default,
                    cm,
                ])
            lines.append(MarkdownFormatter.table(headers, rows))

        # Constraints
        constraints = table_data.get("constraints", [])
        if constraints:
            lines.append("\n### 约束\n")
            headers = ["约束名", "类型", "列", "引用"]
            rows = []
            for con in constraints:
                ref = ""
                if con.get("type") == "FOREIGN KEY" or con.get("constraint_type") == "FOREIGN KEY":
                    fs = con.get("foreign_schema") or con.get("foreign_table_schema", "")
                    ft = con.get("foreign_table") or con.get("foreign_table_name", "")
                    fc = con.get("foreign_columns") or con.get("foreign_column_name", "")
                    if isinstance(fc, list):
                        fc = ", ".join(fc)
                    ref = f"{fs}.{ft}({fc})" if fs else f"{ft}({fc})"
                cols = con.get("columns") or con.get("column_name", "")
                if isinstance(cols, list):
                    cols = ", ".join(cols)
                rows.append([
                    con.get("name") or con.get("constraint_name", ""),
                    con.get("type") or con.get("constraint_type", ""),
                    cols,
                    ref,
                ])
            lines.append(MarkdownFormatter.table(headers, rows))

        # Indexes
        indexes = table_data.get("indexes", [])
        if indexes:
            lines.append("\n### 索引\n")
            for idx in indexes:
                idx_name = idx.get("name") or idx.get("indexname", "")
                idx_def = idx.get("definition") or idx.get("indexdef", "")
                lines.append(f"- `{idx_name}`: `{idx_def}`")

        lines.append("\n---\n")
        return "\n".join(lines)

    @staticmethod
    def diff_report(diff_result):
        """格式化 diff 结果"""
        lines = []
        lines.append("# 数据库结构对比报告")
        lines.append(f"**时间**: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")

        if diff_result.get("source_info"):
            lines.append(f"**源**: {diff_result['source_info']}")
        if diff_result.get("target_info"):
            lines.append(f"**目标**: {diff_result['target_info']}")
        lines.append("")

        # 新增表
        added = diff_result.get("added_tables", [])
        if added:
            lines.append("## 新增表\n")
            for t in added:
                lines.append(f"- `{t}`")
            lines.append("")

        # 删除表
        removed = diff_result.get("removed_tables", [])
        if removed:
            lines.append("## 删除表\n")
            for t in removed:
                lines.append(f"- `{t}`")
            lines.append("")

        # 修改表
        modified = diff_result.get("modified_tables", {})
        if modified:
            lines.append("## 修改表\n")
            for tname, changes in modified.items():
                lines.append(f"### {tname}\n")

                for change in changes.get("added_columns", []):
                    lines.append(f"- **新增列**: `{change['name']}` {change['data_type']}")
                for change in changes.get("removed_columns", []):
                    lines.append(f"- **删除列**: `{change['name']}`")
                for change in changes.get("modified_columns", []):
                    ch = change["changes"]
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
                    lines.append(f"- **修改列**: `{change['name']}` — {'; '.join(desc_parts)}")
                for change in changes.get("added_indexes", []):
                    lines.append(f"- **新增索引**: `{change['name']}`")
                for change in changes.get("removed_indexes", []):
                    lines.append(f"- **删除索引**: `{change['name']}`")
                for change in changes.get("added_constraints", []):
                    lines.append(f"- **新增约束**: `{change['name']}` ({change['type']})")
                for change in changes.get("removed_constraints", []):
                    lines.append(f"- **删除约束**: `{change['name']}`")

                lines.append("")

        if not added and not removed and not modified:
            lines.append("**结构完全一致，无差异。**\n")

        return "\n".join(lines)


class JsonFormatter:
    """JSON 格式化输出"""

    @staticmethod
    def format(data, indent=2):
        return json.dumps(data, indent=indent, cls=_Encoder, ensure_ascii=False)
