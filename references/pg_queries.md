# PostgreSQL 系统视图查询参考

本文档包含 database-tools 技能中使用的关键 PostgreSQL 系统视图和查询模式。

## 核心系统视图

### 元数据查询

| 系统视图 | 用途 |
|----------|------|
| `information_schema.columns` | 列信息（名称、类型、默认值、可空性） |
| `information_schema.table_constraints` | 约束信息（PK、FK、UNIQUE、CHECK） |
| `information_schema.key_column_usage` | 约束对应的列 |
| `information_schema.sequences` | 序列信息 |
| `pg_indexes` | 索引定义（含完整 CREATE INDEX 语句） |
| `pg_class` + `pg_namespace` | 表/索引 OID、大小计算 |
| `pg_description` | 列和表的注释 |
| `pg_type` + `pg_enum` | 枚举类型 |
| `pg_proc` | 函数和存储过程 |
| `information_schema.triggers` | 触发器 |

### 性能分析视图

| 系统视图 | 用途 | 关键指标 |
|----------|------|----------|
| `pg_stat_user_tables` | 表级统计 | seq_scan, idx_scan, n_live_tup, n_dead_tup |
| `pg_stat_user_indexes` | 索引使用统计 | idx_scan, idx_tup_read, idx_tup_fetch |
| `pg_statio_user_tables` | 表 I/O 统计 | heap_blks_read, heap_blks_hit |
| `pg_stat_statements` | 查询级统计（需扩展） | calls, mean_exec_time, shared_blks_hit |
| `pg_stat_database` | 数据库全局统计 | cache_hit_ratio, deadlocks |
| `pg_locks` + `pg_stat_activity` | 锁等待分析 | 阻塞 PID、等待时长 |
| `pg_index` | 索引详情 | indkey（列组合）、indisunique |

## 常用大小计算函数

```sql
-- 表数据大小（不含索引）
pg_relation_size('schema.table')

-- 表总大小（含索引、TOAST）
pg_total_relation_size('schema.table')

-- 索引大小
pg_relation_size('schema.index_name')

-- 人类可读格式
pg_size_pretty(pg_total_relation_size('schema.table'))
```

## 索引分析关键模式

### 判断冗余索引的逻辑
- 索引 A 的列集合是索引 B 列集合的子集 → A 可能冗余
- 前提：A 不是主键，不是唯一约束

### 判断缺失外键索引的逻辑
- 检查 FOREIGN KEY 约束的列
- 验证该列是否作为某个索引的前导列（array_position = 1）

### 判断未使用索引
- `pg_stat_user_indexes.idx_scan = 0`
- 排除 PRIMARY KEY 和 UNIQUE 约束索引

## pg_stat_statements 启用方法

```sql
-- 1. postgresql.conf 中添加（需重启）
shared_preload_libraries = 'pg_stat_statements'
pg_stat_statements.max = 10000
pg_stat_statements.track = all

-- 2. 创建扩展
CREATE EXTENSION pg_stat_statements;

-- 3. 重置统计
SELECT pg_stat_statements_reset();
```

## CONCURRENTLY 模式说明

- `CREATE INDEX CONCURRENTLY` 不锁表，允许并发读写
- `DROP INDEX CONCURRENTLY` 同样不锁表
- 缺点：速度较慢，不能在事务块中执行
- 建议：生产环境始终使用 CONCURRENTLY
