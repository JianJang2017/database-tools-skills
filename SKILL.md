---
name: database-tools
description: "PostgreSQL / MySQL 数据库工具集。连接 PostgreSQL 或 MySQL 数据库，读取和分析表结构，生成 DDL 初始化脚本，进行索引性能优化分析并输出优化建议脚本，支持跨数据库结构对比与迁移 DDL 生成。当用户请求"查看数据库结构"、"导出 DDL"、"生成建表脚本"、"数据库初始化"、"索引优化"、"数据库性能分析"、"PostgreSQL 分析"、"MySQL 分析"、"数据库对比"、"数据库迁移"、"数据库 diff"时触发此技能。"
---

# Database Tools

PostgreSQL / MySQL 数据库元数据检查与性能优化工具集。提供统一入口、数据库连接、表结构读取、DDL 生成、索引性能分析、跨数据库结构对比与迁移 DDL 生成能力。

## 前置条件

```bash
# PostgreSQL 支持
pip install psycopg2-binary

# MySQL 支持
pip install pymysql
```

## 脚本路径

本技能的脚本位于 `~/.claude/skills/database-tools/` 目录下。

**推荐使用统一入口 `db.py`**（覆盖所有功能）：

```bash
SKILL_DIR="$HOME/.claude/skills/database-tools"
python "$SKILL_DIR/db.py" <command>
```

原有独立脚本仍可直接运行（向后兼容）：

```bash
python "$SKILL_DIR/scripts/pg_inspector.py" <command>
python "$SKILL_DIR/scripts/pg_index_advisor.py" <command>
python "$SKILL_DIR/scripts/mysql_inspector.py" <command>
python "$SKILL_DIR/scripts/mysql_index_advisor.py" <command>
```

以下文档中的命令示例均省略前缀，实际执行时需补全绝对路径。

## 数据库连接

### 连接方式一览

| 方式 | 说明 |
|------|------|
| `--profile <name>` | 从 `~/.dbtools.json` 读取已保存的连接配置 |
| `--dsn <url>` | 完整连接字符串 |
| `--host --port --user --password --dbname` | 独立参数 |
| 环境变量 | PG: `PGHOST` 等 / MySQL: `MYSQL_HOST` 等 |
| `.env` 文件 | 自动加载项目目录下的 `.env` |

连接参数优先级：`--profile` > 命令行参数 > 环境变量 > `.env` 文件。

### PostgreSQL 环境变量

| 变量 | 说明 |
|------|------|
| `DATABASE_URL` 或 `PG_DSN` | 完整连接字符串 |
| `PGHOST` | 主机（默认 localhost） |
| `PGPORT` | 端口（默认 5432） |
| `PGUSER` | 用户（默认 postgres） |
| `PGPASSWORD` | 密码 |
| `PGDATABASE` | 数据库名（默认 postgres） |

### MySQL 环境变量

| 变量 | 说明 |
|------|------|
| `MYSQL_DSN` | 完整连接字符串 (mysql://user:pass@host:port/db) |
| `MYSQL_HOST` | 主机（默认 localhost） |
| `MYSQL_PORT` | 端口（默认 3306） |
| `MYSQL_USER` | 用户（默认 root） |
| `MYSQL_PWD` | 密码 |
| `MYSQL_DATABASE` | 数据库名 |

### Profile 配置

使用 profile 避免重复输入连接参数：

```bash
# 保存 profile（密码不会存入配置文件）
python db.py config set dev --engine pg --host localhost --port 5432 --user postgres --dbname mydb
python db.py config set prod-mysql --engine mysql --host db.example.com --port 3306 --user app --dbname production

# 查看已保存的 profile
python db.py config list

# 删除 profile
python db.py config remove dev

# 使用 profile 连接
python db.py pg inspect --profile dev --schema public
python db.py mysql tables --profile prod-mysql --schema production
```

配置文件位于 `~/.dbtools.json`，自动设置 600 权限。密码建议通过 `--password` 参数或环境变量传入。

## 命令速查表

```
db.py pg schemas                         # 列出 PG schema
db.py pg tables    -s <schema>           # 列出表
db.py pg inspect   -s <schema> [-t tbl]  # 查看表结构
db.py pg ddl       -s <schema> [-o file] # 生成 DDL
db.py pg report    -s <schema> [-o file] # 性能分析报告
db.py pg optimize  -s <schema> [-o file] # 优化 DDL 脚本

db.py mysql schemas                      # 列出 MySQL 数据库
db.py mysql tables    -s <db>            # 列出表
db.py mysql inspect   -s <db> [-t tbl]   # 查看表结构
db.py mysql ddl       -s <db> [-o file]  # 生成 DDL
db.py mysql report    -s <db> [-o file]  # 性能分析报告
db.py mysql optimize  -s <db> [-o file]  # 优化 DDL 脚本

db.py diff --source <src> --target <tgt> # 结构对比
db.py snapshot --profile <name> -o file  # 导出快照
db.py config set/list/remove             # 配置管理
```

## PostgreSQL 功能

### 查看数据库结构

```bash
python db.py pg schemas
python db.py pg tables --schema public
python db.py pg inspect --schema public
python db.py pg inspect --schema public --table users orders
python db.py pg inspect --schema public --table users --format json
```

### 生成 DDL 初始化脚本

```bash
python db.py pg ddl --schema public
python db.py pg ddl --schema public --table users orders
python db.py pg ddl --schema public --output init.sql
```

生成内容包含：CREATE SCHEMA / EXTENSION / TYPE / SEQUENCE / TABLE / INDEX / FUNCTION / TRIGGER + COMMENT ON。

### 索引与性能分析

```bash
# 完整分析报告
python db.py pg report --schema public
python db.py pg report --schema public --output report.md

# 生成优化脚本
python db.py pg optimize --schema public
python db.py pg optimize --schema public --output optimize.sql
python db.py pg optimize --schema public --no-concurrently
```

分析维度：数据库概览、未使用索引、重复索引、冗余索引、外键缺失索引、高频顺序扫描表、表 I/O 缓存命中率、表膨胀、慢查询（pg_stat_statements）、锁等待。

## MySQL 功能

### 查看数据库结构

```bash
python db.py mysql schemas
python db.py mysql tables --schema mydb
python db.py mysql inspect --schema mydb
python db.py mysql inspect --schema mydb --table users orders
python db.py mysql inspect --schema mydb --format json
```

### 生成 DDL 脚本

```bash
# 快速模式（SHOW CREATE TABLE）
python db.py mysql ddl --schema mydb

# 自行拼装模式（用于 diff）
python db.py mysql ddl --schema mydb --mode build

# 输出到文件
python db.py mysql ddl --schema mydb --output init.sql
```

### 索引与性能分析

```bash
# 完整分析报告
python db.py mysql report --schema mydb
python db.py mysql report --schema mydb --output report.md

# 生成优化脚本
python db.py mysql optimize --schema mydb
python db.py mysql optimize --schema mydb --output optimize.sql
```

分析维度：服务器概览、InnoDB 缓冲池命中率、冗余索引（MySQL 8.0+ 使用 sys 视图 / 5.7 降级兼容）、未使用索引、外键缺失索引、高频读取表、表碎片、慢查询（performance_schema）。

## 结构对比与迁移

### 导出快照

```bash
python db.py snapshot --profile dev --schema public -o dev_snapshot.json
python db.py snapshot --profile prod-mysql --schema mydb -o prod_snapshot.json
```

快照文件为 JSON 格式，可纳入 git 版本控制。

### 结构对比

支持三种数据源：profile 名、DSN、快照 JSON 文件。

```bash
# 两个 profile 对比
python db.py diff --source dev --target prod --schema public

# DSN 对比
python db.py diff --source "postgresql://localhost/dev" --target "postgresql://localhost/prod" --schema public

# 快照对比
python db.py diff --source dev_snapshot.json --target prod_snapshot.json

# 混合对比
python db.py diff --source dev_snapshot.json --target prod
```

**同引擎对比**：生成完整对照报告 + 迁移 DDL（ALTER TABLE ADD/DROP/MODIFY COLUMN、CREATE/DROP INDEX 等）。

**跨引擎对比（PG ↔ MySQL）**：仅生成对照报告，不生成迁移 DDL（类型差异太大）。

## 工作流程

### 场景 A：了解 PostgreSQL 数据库结构

1. 确认连接信息（profile / .env / 手动提供）
2. 运行 `db.py pg schemas` 查看可用 schema
3. 运行 `db.py pg inspect -s public` 查看表结构
4. 将输出呈现给用户

### 场景 B：生成初始化脚本

1. 连接数据库
2. 运行 `db.py pg ddl -s public -o init.sql` 导出 DDL
3. 检查生成的 SQL 是否完整
4. 交付给用户

### 场景 C：PostgreSQL 索引性能优化

1. 运行 `db.py pg report -s public` 生成分析报告
2. 向用户展示报告中的关键发现
3. 运行 `db.py pg optimize -s public -o optimize.sql` 生成优化脚本
4. 与用户讨论每条优化建议的风险和收益
5. **重要**: 删除索引前必须确认该索引确实无用

### 场景 D：MySQL 数据库分析

1. 运行 `db.py mysql schemas` 查看可用数据库
2. 运行 `db.py mysql inspect -s mydb` 查看结构
3. 运行 `db.py mysql report -s mydb` 生成性能报告
4. 运行 `db.py mysql optimize -s mydb` 生成优化脚本

### 场景 E：跨环境 / 跨数据库结构对比

1. 配置 profile: `db.py config set dev --engine pg --host localhost --dbname mydb`
2. 导出快照: `db.py snapshot --profile dev -s public -o dev.json`
3. 对比: `db.py diff --source dev.json --target prod -s public`
4. 审查对比报告和迁移 DDL
5. 应用迁移前务必在测试环境验证

## 参考文档

- PostgreSQL 系统视图查询参考: `references/pg_queries.md`
- MySQL 系统表查询参考: `references/mysql_queries.md`
