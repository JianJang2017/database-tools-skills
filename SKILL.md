---
name: database-tools-skills
description: "PostgreSQL / MySQL 数据库工具集。连接数据库、读取和分析表结构、生成 DDL 初始化脚本、索引性能优化分析、输出优化建议脚本、跨数据库结构对比与迁移 DDL 生成。触发场景：\"查看数据库结构\"、\"导出 DDL\"、\"生成建表脚本\"、\"数据库初始化\"、\"索引优化\"、\"数据库性能分析\"、\"数据库对比\"、\"数据库迁移\"、\"数据库 diff\"，或用户询问\"数据库有哪些表\"、\"表结构是什么样的\"、\"帮我分析一下索引\"、\"生成初始化 SQL\"、\"对比两个环境的库\"等。"
---

# Database Tools

PostgreSQL / MySQL 数据库元数据检查与性能优化工具集。提供统一入口、数据库连接、表结构读取、DDL 生成、索引性能分析、跨数据库结构对比与迁移 DDL 生成能力。

## 前置条件

**Python 版本**: >= 3.9

```bash
# PostgreSQL 支持
pip install psycopg2-binary

# MySQL 支持
pip install pymysql
```

## 脚本路径

本技能的脚本位于 skill 安装目录下（通常为 `~/.claude/skills/database-tools-skills/`）。

**推荐使用统一入口 `db.py`**（覆盖所有功能）：

```bash
SKILL_DIR="$HOME/.claude/skills/database-tools-skills"
python "$SKILL_DIR/db.py" <command>
```

如果你的 skill 安装在其他位置，请将 `SKILL_DIR` 替换为实际路径。

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
# PostgreSQL
db.py pg schemas                         # 列出 PG schema
db.py pg tables    -s <schema>           # 列出表
db.py pg inspect   -s <schema> [-t tbl]  # 查看表结构
db.py pg ddl       -s <schema> [-o file] # 生成 DDL（含 EXTENSION/TYPE/SEQUENCE/FUNCTION/TRIGGER）
db.py pg report    -s <schema> [-o file] # 性能分析报告（未使用索引、冗余索引、慢查询等）
db.py pg optimize  -s <schema> [-o file] # 优化 DDL 脚本

# MySQL
db.py mysql schemas                      # 列出 MySQL 数据库
db.py mysql tables    -s <db>            # 列出表
db.py mysql inspect   -s <db> [-t tbl]   # 查看表结构
db.py mysql ddl       -s <db> [-o file]  # 生成 DDL（默认 SHOW CREATE TABLE 模式）
db.py mysql report    -s <db> [-o file]  # 性能分析报告（InnoDB 缓冲池、冗余索引、表碎片等）
db.py mysql optimize  -s <db> [-o file]  # 优化 DDL 脚本

# 结构对比与迁移
db.py diff --source <src> --target <tgt> # 结构对比（同引擎生成迁移 DDL）
db.py snapshot --profile <name> -o file  # 导出快照（JSON 格式，可纳入 git）

# 配置管理
db.py config set/list/remove             # Profile 配置管理
```

## 与用户交互

### 收集连接信息

用户通常不会主动提供完整连接参数。开始前先确认：

1. 是否已有 profile（运行 `db.py config list` 查看）
2. 是否有 `.env` 文件或环境变量
3. 如果都没有，询问：数据库类型（PostgreSQL/MySQL）、host、port、user、password、dbname

不要假设默认值就能连上——先问清楚，避免反复失败。

### 呈现结果

- **表结构**：重点说明表数量、关键表的字段和索引情况，不要把原始输出全部粘贴。例如："共 15 张表，核心表 users 有 8 个字段（id/username/email 等），主键索引 + 2 个普通索引"
- **DDL 脚本**：告知文件路径，说明包含哪些对象（表/索引/函数等）。PG DDL 包含 EXTENSION/TYPE/SEQUENCE/FUNCTION/TRIGGER；MySQL 默认用 SHOW CREATE TABLE 快速模式
- **性能报告**：优先展示高风险项（未使用索引数量、慢查询 Top 3、表膨胀严重的表），再给出优化脚本路径。例如："发现 3 个未使用索引（占用 120MB）、5 个冗余索引、2 张表膨胀率超过 30%"
- **结构对比**：先说差异摘要（新增/删除/修改了哪些表和字段），再给出迁移 DDL。例如："dev 比 prod 多 2 张表（logs/audit_trail）、users 表新增 last_login_at 字段、orders 表删除了 legacy_status 索引"

## 工作流程

### 场景 A：了解数据库结构

**目的**：快速掌握数据库的表结构、字段定义、索引情况。

1. 确认连接信息（见上方"收集连接信息"）
2. `db.py pg schemas` / `db.py mysql schemas` 查看可用 schema
3. `db.py pg inspect -s <schema>` / `db.py mysql inspect -s <db>` 查看表结构
4. 向用户总结：有哪些核心表、大致的表关系、值得关注的字段设计（如缺失索引的外键、过长的 VARCHAR 等）

### 场景 B：导出 DDL 初始化脚本

**目的**：生成可重复执行的数据库初始化脚本，用于新环境搭建或版本控制。

1. 连接数据库
2. `db.py pg ddl -s <schema> -o init.sql` 或 `db.py mysql ddl -s <db> -o init.sql`
3. 告知用户文件路径，说明脚本包含的对象类型
4. 提醒：PG DDL 包含 EXTENSION/TYPE/SEQUENCE/FUNCTION/TRIGGER；MySQL 默认用 SHOW CREATE TABLE 快速模式（如需 diff 兼容可用 `--mode build`）

### 场景 C：索引性能优化

**目的**：识别性能瓶颈，生成可执行的优化脚本。

1. `db.py pg report -s <schema>` / `db.py mysql report -s <db>` 生成分析报告
2. 向用户展示关键发现：
   - 未使用索引（从未扫描，占用空间）
   - 冗余索引（被其他索引完全覆盖）
   - 缺失外键索引（JOIN 性能差）
   - 慢查询 Top 3（需 pg_stat_statements / performance_schema）
3. `db.py pg optimize -s <schema> -o optimize.sql` / `db.py mysql optimize -s <db> -o optimize.sql` 生成优化脚本
4. 与用户逐条讨论风险：
   - 删除索引前确认确实无用（检查应用代码中是否有隐式依赖）
   - CREATE INDEX CONCURRENTLY 不锁表但耗时更长（PG 专用）
   - 表维护操作（VACUUM / OPTIMIZE TABLE）可能锁表
5. 建议先在测试环境执行，观察查询计划变化后再上生产

### 场景 D：跨环境结构对比与迁移

**目的**：对比开发/测试/生产环境的数据库结构差异，生成迁移脚本。

1. 为两个环境配置 profile 或准备 DSN
2. 导出快照（可选，便于离线对比或纳入 git）：`db.py snapshot --profile dev -s <schema> -o dev.json`
3. `db.py diff --source dev --target prod -s <schema>` 生成对比报告
4. 向用户说明差异摘要，展示迁移 DDL（同引擎才有）：
   - 新增表：列出表名和字段数
   - 删除表：提醒是否误删
   - 修改表：列出字段变更（类型/可空/默认值）、索引变更、约束变更
5. 跨引擎（PG ↔ MySQL）只生成对照报告，迁移 DDL 需人工处理（类型系统差异太大）
6. 应用迁移前务必在测试环境验证，检查：
   - ALTER TABLE 是否会锁表过久
   - 数据类型变更是否需要数据迁移
   - 删除列/索引是否影响现有查询

## 参考文档

- PostgreSQL 系统视图查询参考: `references/pg_queries.md`
- MySQL 系统表查询参考: `references/mysql_queries.md`
