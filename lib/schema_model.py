"""
统一数据库结构抽象模型
支持 PostgreSQL 和 MySQL 的结构信息标准化表示
"""

import json
from dataclasses import dataclass, field, asdict
from enum import Enum
from typing import List, Optional


class DbEngine(str, Enum):
    POSTGRESQL = "postgresql"
    MYSQL = "mysql"


@dataclass
class Column:
    """列定义"""
    name: str
    data_type: str          # 规范化类型 (INTEGER, VARCHAR, TEXT, ...)
    raw_type: str           # 原始类型 (int4, varchar(255), bigint unsigned, ...)
    is_nullable: bool = True
    default: Optional[str] = None
    max_length: Optional[int] = None
    numeric_precision: Optional[int] = None
    numeric_scale: Optional[int] = None
    comment: Optional[str] = None
    auto_increment: bool = False
    ordinal_position: int = 0


@dataclass
class Constraint:
    """约束定义"""
    name: str
    type: str               # PRIMARY KEY / FOREIGN KEY / UNIQUE / CHECK
    columns: List[str] = field(default_factory=list)
    foreign_schema: Optional[str] = None
    foreign_table: Optional[str] = None
    foreign_columns: List[str] = field(default_factory=list)
    definition: Optional[str] = None  # CHECK 约束表达式


@dataclass
class Index:
    """索引定义"""
    name: str
    columns: List[str] = field(default_factory=list)
    is_unique: bool = False
    is_primary: bool = False
    index_type: str = "BTREE"       # BTREE / HASH / GIN / GIST / FULLTEXT / SPATIAL
    definition: Optional[str] = None  # 完整 CREATE INDEX 语句


@dataclass
class Table:
    """表结构"""
    schema: str
    name: str
    columns: List[Column] = field(default_factory=list)
    constraints: List[Constraint] = field(default_factory=list)
    indexes: List[Index] = field(default_factory=list)
    comment: Optional[str] = None
    table_size: Optional[str] = None
    index_size: Optional[str] = None
    total_size: Optional[str] = None
    engine: Optional[str] = None      # MySQL: InnoDB / MyISAM
    collation: Optional[str] = None   # MySQL: utf8mb4_general_ci
    row_count: Optional[int] = None


@dataclass
class SchemaSnapshot:
    """可序列化的完整 schema 快照"""
    db_engine: str              # DbEngine 值
    host: str = ""
    port: int = 0
    database: str = ""
    schema_name: str = ""
    snapshot_time: str = ""
    tables: List[Table] = field(default_factory=list)
    version: str = "1.0"

    def to_dict(self) -> dict:
        return asdict(self)

    def to_json(self, indent=2) -> str:
        return json.dumps(self.to_dict(), indent=indent, ensure_ascii=False)

    @classmethod
    def from_dict(cls, data: dict) -> "SchemaSnapshot":
        tables = []
        for t in data.get("tables", []):
            columns = [Column(**c) for c in t.get("columns", [])]
            constraints = [Constraint(**c) for c in t.get("constraints", [])]
            indexes = [Index(**i) for i in t.get("indexes", [])]
            tbl = Table(
                schema=t["schema"],
                name=t["name"],
                columns=columns,
                constraints=constraints,
                indexes=indexes,
                comment=t.get("comment"),
                table_size=t.get("table_size"),
                index_size=t.get("index_size"),
                total_size=t.get("total_size"),
                engine=t.get("engine"),
                collation=t.get("collation"),
                row_count=t.get("row_count"),
            )
            tables.append(tbl)
        return cls(
            db_engine=data["db_engine"],
            host=data.get("host", ""),
            port=data.get("port", 0),
            database=data.get("database", ""),
            schema_name=data.get("schema_name", ""),
            snapshot_time=data.get("snapshot_time", ""),
            tables=tables,
            version=data.get("version", "1.0"),
        )

    @classmethod
    def from_json(cls, text: str) -> "SchemaSnapshot":
        return cls.from_dict(json.loads(text))
