# MySQL 系统表查询参考

本文档包含 database-tools-skills 技能中使用的关键 MySQL 系统视图和查询模式。

## 核心系统视图

### 元数据查询 (information_schema)

| 系统视图 | 用途 |
|----------|------|
| `information_schema.SCHEMATA` | 数据库列表 |
| `information_schema.TABLES` | 表信息（行数、大小、引擎、排序规则） |
| `information_schema.COLUMNS` | 列信息（名称、类型、默认值、可空性、注释） |
| `information_schema.TABLE_CONSTRAINTS` | 约束信息（PK、FK、UNIQUE） |
| `information_schema.KEY_COLUMN_USAGE` | 约束对应的列及外键引用 |
| `information_schema.STATISTICS` | 索引详情（列、基数、类型） |
| `information_schema.INNODB_BUFFER_POOL_STATS` | InnoDB 缓冲池状态 |

### 性能分析视图 (performance_schema)

| 系统视图 | 用途 | 关键指标 |
|----------|------|----------|
| `performance_schema.table_io_waits_summary_by_index_usage` | 索引使用统计 | count_star（索引访问次数） |
| `performance_schema.table_io_waits_summary_by_table` | 表 I/O 统计 | count_read, count_fetch |
| `performance_schema.events_statements_summary_by_digest` | 查询级统计 | AVG_TIMER_WAIT, SUM_ROWS_EXAMINED |
| `performance_schema.global_status` | 全局状态变量 | Uptime, Threads_connected |

### sys 视图 (MySQL 8.0+)

| 系统视图 | 用途 |
|----------|------|
| `sys.schema_redundant_indexes` | 冗余索引自动检测 |
| `sys.schema_unused_indexes` | 未使用索引 |
| `sys.statement_analysis` | 语句性能分析 |
| `sys.schema_table_statistics` | 表级统计汇总 |

## 常用大小计算

```sql
-- 表数据大小（MB）
SELECT
    TABLE_NAME,
    ROUND(DATA_LENGTH / 1024 / 1024, 2) AS data_mb,
    ROUND(INDEX_LENGTH / 1024 / 1024, 2) AS index_mb,
    ROUND((DATA_LENGTH + INDEX_LENGTH) / 1024 / 1024, 2) AS total_mb
FROM information_schema.TABLES
WHERE TABLE_SCHEMA = 'your_db';

-- 碎片空间
SELECT
    TABLE_NAME,
    ROUND(DATA_FREE / 1024 / 1024, 2) AS free_mb,
    ROUND(100.0 * DATA_FREE / DATA_LENGTH, 1) AS fragmentation_pct
FROM information_schema.TABLES
WHERE TABLE_SCHEMA = 'your_db' AND DATA_FREE > 0;
```

## 索引分析关键模式

### 冗余索引检测（MySQL 8.0+）
```sql
-- 直接使用 sys 视图
SELECT * FROM sys.schema_redundant_indexes
WHERE table_schema = 'your_db';
```

### 冗余索引检测（MySQL 5.7）
- 基于 `information_schema.STATISTICS` 手动比较索引列组合
- 如果索引 A 的所有列是索引 B 的前缀，则 A 冗余

### 未使用索引
```sql
SELECT object_name, index_name
FROM performance_schema.table_io_waits_summary_by_index_usage
WHERE object_schema = 'your_db'
    AND index_name IS NOT NULL
    AND index_name != 'PRIMARY'
    AND count_star = 0;
```

### 外键缺失索引
- 检查 `FOREIGN KEY` 约束的列
- 验证该列是否出现在 `information_schema.STATISTICS` 中且 `SEQ_IN_INDEX = 1`

## 慢查询分析

```sql
-- 基于 performance_schema（无需开启 slow_query_log）
SELECT
    DIGEST_TEXT,
    COUNT_STAR AS calls,
    ROUND(AVG_TIMER_WAIT / 1000000000000, 4) AS avg_sec,
    SUM_NO_INDEX_USED
FROM performance_schema.events_statements_summary_by_digest
WHERE SCHEMA_NAME = 'your_db'
ORDER BY AVG_TIMER_WAIT DESC
LIMIT 20;
```

## InnoDB 缓冲池

```sql
-- 缓冲池命中率
SELECT
    HIT_RATE,
    POOL_SIZE,
    FREE_BUFFERS,
    DATABASE_PAGES,
    MODIFIED_DB_PAGES AS dirty_pages
FROM information_schema.INNODB_BUFFER_POOL_STATS;
```

命中率 `HIT_RATE` 以千分比表示（1000 = 100%），理想值应 > 990。

## 表碎片整理

```sql
-- 查看碎片
SELECT TABLE_NAME, DATA_FREE, DATA_LENGTH
FROM information_schema.TABLES
WHERE TABLE_SCHEMA = 'your_db' AND DATA_FREE > 0;

-- 整理碎片（InnoDB）
-- 注意: OPTIMIZE TABLE 会重建表，期间会锁表
OPTIMIZE TABLE your_db.your_table;

-- 大表替代方案
ALTER TABLE your_db.your_table ENGINE=InnoDB;
```

## performance_schema 启用

MySQL 5.7+ 默认启用 `performance_schema`。如果被关闭，需要在 `my.cnf` 中配置：

```ini
[mysqld]
performance_schema = ON
```

需要重启 MySQL 服务。

## MySQL 版本差异注意

| 功能 | MySQL 5.7 | MySQL 8.0+ |
|------|-----------|------------|
| `sys` 视图 | 部分可用 | 完整支持 |
| `schema_redundant_indexes` | 不可用 | 可用 |
| `INFORMATION_SCHEMA` 查询速度 | 较慢（访问磁盘） | 使用 data dictionary 缓存 |
| `CHECK` 约束 | 语法支持但不生效 | 完整支持 |
| `EXPLAIN ANALYZE` | 不可用 | 可用 |
