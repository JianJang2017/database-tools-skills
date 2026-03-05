# Database Tools

[English](./README_EN.md)

PostgreSQL + MySQL 数据库工具集。一行命令搞定：查结构、导 DDL、性能分析、索引优化、跨库对比。

## 安装

```bash
# PostgreSQL 支持
pip install psycopg2-binary

# MySQL 支持
pip install pymysql
```

## 快速上手

所有功能通过 `db.py` 统一入口调用：

```bash
python db.py <命令>
```

### 30 秒看完核心命令

```bash
# ---- PostgreSQL ----
db.py pg schemas                          # 列出 schema
db.py pg tables    -s public              # 列出表
db.py pg inspect   -s public -t users     # 查看表结构
db.py pg ddl       -s public -o init.sql  # 导出 DDL
db.py pg report    -s public              # 性能分析报告
db.py pg optimize  -s public              # 生成优化脚本

# ---- MySQL ----
db.py mysql schemas                       # 列出数据库
db.py mysql tables    -s mydb             # 列出表
db.py mysql inspect   -s mydb -t users    # 查看表结构
db.py mysql ddl       -s mydb -o init.sql # 导出 DDL
db.py mysql report    -s mydb             # 性能分析报告
db.py mysql optimize  -s mydb             # 生成优化脚本

# ---- 跨库对比 ----
db.py diff --source dev --target prod     # 结构对比 + 迁移 DDL

# ---- 快照 ----
db.py snapshot --profile dev -o snap.json # 导出结构快照

# ---- 配置管理 ----
db.py config set dev --engine pg --host localhost --dbname mydb
db.py config list
db.py config remove dev
```

---

## 连接数据库

三种方式，任选其一。

### 方式一：命令行参数（临时使用）

```bash
db.py pg inspect -s public \
  --host localhost --port 5432 --user postgres --password secret --dbname mydb
```

也可以用 DSN 一行搞定：

```bash
db.py pg inspect -s public --dsn "postgresql://postgres:secret@localhost:5432/mydb"
```

### 方式二：环境变量（项目级）

**PostgreSQL**（兼容标准 PG 变量）：

```bash
export PGHOST=localhost
export PGPORT=5432
export PGUSER=postgres
export PGPASSWORD=secret
export PGDATABASE=mydb
```

**MySQL**：

```bash
export MYSQL_HOST=localhost
export MYSQL_PORT=3306
export MYSQL_USER=root
export MYSQL_PWD=secret
export MYSQL_DATABASE=mydb
```

也支持项目目录下的 `.env` 文件自动加载。

### 方式三：Profile 配置（推荐，长期使用）

保存一次，以后只需 `--profile dev`：

```bash
# 保存连接配置（密码不会写入文件）
db.py config set dev     --engine pg    --host localhost --dbname mydb --user postgres
db.py config set staging --engine mysql --host 10.0.0.5  --dbname app  --user deploy

# 查看所有 profile
db.py config list

# 使用时只需指定 profile 名
db.py pg inspect -s public --profile dev --password secret
db.py mysql tables -s app --profile staging --password secret
```

> 配置文件位于 `~/.dbtools.json`，自动设置 600 权限。

---

## 功能详解

### 1. 查看数据库结构

```bash
# 列出所有 schema / 数据库
db.py pg schemas
db.py mysql schemas

# 列出某个 schema 下的所有表
db.py pg tables -s public
db.py mysql tables -s mydb

# 查看表结构详情（默认 Markdown 格式）
db.py pg inspect -s public                    # 所有表
db.py pg inspect -s public -t users orders    # 指定表
db.py pg inspect -s public -t users -f json   # JSON 格式
```

输出内容包含：列定义、类型、可空、默认值、注释、约束（PK/FK/UNIQUE）、索引、表大小。

### 2. 生成 DDL 脚本

```bash
# PostgreSQL
db.py pg ddl -s public                    # 整个 schema
db.py pg ddl -s public -t users orders    # 指定表
db.py pg ddl -s public -o init.sql        # 输出到文件

# MySQL（两种模式）
db.py mysql ddl -s mydb                   # 默认用 SHOW CREATE TABLE
db.py mysql ddl -s mydb --mode build      # 自行拼装（用于 diff 场景）
db.py mysql ddl -s mydb -o init.sql       # 输出到文件
```

PG 生成的 DDL 覆盖：SCHEMA / EXTENSION / TYPE(枚举) / SEQUENCE / TABLE / INDEX / FUNCTION / TRIGGER / COMMENT。

### 3. 性能分析报告

```bash
db.py pg report -s public
db.py pg report -s public -o report.md

db.py mysql report -s mydb
db.py mysql report -s mydb -o report.md
```

**PostgreSQL 分析维度**：

| 维度 | 说明 |
|------|------|
| 数据库概览 | 缓存命中率、连接数、死锁、临时文件 |
| 未使用索引 | 从未扫描的索引 |
| 重复索引 | 列组合完全相同的索引对 |
| 冗余索引 | 被复合索引完全覆盖的索引 |
| 外键缺失索引 | 外键列无索引 |
| 顺序扫描热表 | 大表频繁全表扫描 |
| 表 I/O 缓存 | 各表缓存效率 |
| 表膨胀 | 死元组过多 |
| 慢查询 | 需 pg_stat_statements 扩展 |
| 锁等待 | 当前阻塞情况 |

**MySQL 分析维度**：

| 维度 | 说明 |
|------|------|
| 服务器概览 | 版本、连接、缓冲池大小、运行时长 |
| InnoDB 缓冲池 | 命中率、脏页、空闲页 |
| 冗余索引 | 8.0+ 用 sys 视图 / 5.7 降级兼容 |
| 未使用索引 | 需 performance_schema |
| 外键缺失索引 | 外键列无索引 |
| 高频读取表 | 表级 I/O 统计 |
| 表碎片 | DATA_FREE 占比 |
| 慢查询 | 基于 performance_schema |

### 4. 生成优化脚本

```bash
db.py pg optimize -s public
db.py pg optimize -s public -o optimize.sql
db.py pg optimize -s public --no-concurrently  # 不用 CONCURRENTLY

db.py mysql optimize -s mydb
db.py mysql optimize -s mydb -o optimize.sql
```

脚本内容：创建缺失索引 → 删除未使用索引 → 删除冗余索引 → 表维护（VACUUM / OPTIMIZE TABLE）。

> **所有 DROP 操作都带注释说明原因，执行前请仔细确认。**

### 5. 结构对比 (Diff)

对比两个数据库的表结构差异，同引擎自动生成迁移 DDL。

```bash
# Profile 对比
db.py diff --source dev --target prod -s public

# DSN 对比
db.py diff \
  --source "postgresql://localhost/dev_db" \
  --target "postgresql://localhost/prod_db" \
  -s public

# 快照文件对比
db.py diff --source dev.json --target prod.json

# 混合对比（快照 vs 在线数据库）
db.py diff --source dev.json --target prod -s public
```

**输出内容**：
- 新增 / 删除 / 修改的表
- 列变更（类型、可空、默认值）
- 索引变更、约束变更
- 同引擎：生成 `ALTER TABLE` 迁移 DDL
- 跨引擎（PG ↔ MySQL）：仅生成对照报告

### 6. 结构快照

把数据库结构保存为 JSON 文件，方便版本控制和离线对比：

```bash
db.py snapshot --profile dev -s public -o dev_20260305.json
```

典型用法：

```bash
# 变更前导出快照
db.py snapshot --profile prod -s public -o before.json

# 执行数据库变更...

# 变更后再导出
db.py snapshot --profile prod -s public -o after.json

# 对比差异
db.py diff --source before.json --target after.json
```

---

## 项目结构

```
database-tools-skills/
├── db.py                       # 统一入口（推荐使用）
├── SKILL.md                    # Claude 技能文档
├── scripts/
│   ├── pg_inspector.py         # PG 结构检查 + DDL（可独立运行）
│   ├── pg_index_advisor.py     # PG 索引性能分析（可独立运行）
│   ├── mysql_inspector.py      # MySQL 结构检查 + DDL
│   └── mysql_index_advisor.py  # MySQL 索引性能分析
├── lib/
│   ├── schema_model.py         # 统一数据模型
│   ├── connection.py           # 连接管理
│   ├── config.py               # Profile 配置 (~/.dbtools.json)
│   ├── formatters.py           # Markdown / JSON 输出格式化
│   ├── snapshot.py             # 快照导出 / 导入
│   └── differ.py               # 结构对比引擎 + 迁移 DDL 生成
└── references/
    ├── pg_queries.md            # PG 系统视图查询参考
    └── mysql_queries.md         # MySQL 系统表查询参考
```

---

## 常见问题

**Q: 需要什么数据库权限？**

只需 `SELECT` 权限。性能分析需要访问 `pg_stat_*` / `performance_schema` 视图。慢查询分析 PG 端需启用 `pg_stat_statements` 扩展。

**Q: 密码安全吗？**

Profile 配置文件 `~/.dbtools.json` 不存储密码，文件权限自动设为 600。密码通过 `--password` 参数或环境变量传入。

**Q: MySQL 5.7 支持吗？**

支持。冗余索引检测在 5.7 下使用 `information_schema` 降级查询，8.0+ 使用更准确的 `sys.schema_redundant_indexes`。

**Q: 跨引擎对比（PG ↔ MySQL）能生成迁移脚本吗？**

不能。两者类型系统差异太大，仅生成对照报告供人工参考。同引擎对比才会自动生成迁移 DDL。
