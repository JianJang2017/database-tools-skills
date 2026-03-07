# Database Tools

[中文](./README.md)

PostgreSQL + MySQL database toolkit. One command to handle it all: inspect schemas, export DDL, analyze performance, optimize indexes, and diff across databases.

## Installation

**Python Version**: >= 3.9

```bash
# PostgreSQL support
pip install psycopg2-binary

# MySQL support
pip install pymysql
```

## Quick Start

All features are accessed through the unified entry point `db.py`:

```bash
python db.py <command>
```

### Core Commands at a Glance

```bash
# ---- PostgreSQL ----
db.py pg schemas                          # List schemas
db.py pg tables    -s public              # List tables
db.py pg inspect   -s public -t users     # Inspect table structure
db.py pg ddl       -s public -o init.sql  # Export DDL
db.py pg report    -s public              # Performance analysis report
db.py pg optimize  -s public              # Generate optimization script

# ---- MySQL ----
db.py mysql schemas                       # List databases
db.py mysql tables    -s mydb             # List tables
db.py mysql inspect   -s mydb -t users    # Inspect table structure
db.py mysql ddl       -s mydb -o init.sql # Export DDL
db.py mysql report    -s mydb             # Performance analysis report
db.py mysql optimize  -s mydb             # Generate optimization script

# ---- Cross-Database Diff ----
db.py diff --source dev --target prod     # Structure diff + migration DDL

# ---- Snapshot ----
db.py snapshot --profile dev -o snap.json # Export structure snapshot

# ---- Configuration ----
db.py config set dev --engine pg --host localhost --dbname mydb
db.py config list
db.py config remove dev
```

---

## Connecting to Databases

Three options — pick whichever suits you.

### Option 1: Command-Line Arguments (Ad-hoc)

```bash
db.py pg inspect -s public \
  --host localhost --port 5432 --user postgres --password secret --dbname mydb
```

Or use a DSN in one line:

```bash
db.py pg inspect -s public --dsn "postgresql://postgres:secret@localhost:5432/mydb"
```

### Option 2: Environment Variables (Project-Level)

**PostgreSQL** (compatible with standard PG variables):

```bash
export PGHOST=localhost
export PGPORT=5432
export PGUSER=postgres
export PGPASSWORD=secret
export PGDATABASE=mydb
```

**MySQL**:

```bash
export MYSQL_HOST=localhost
export MYSQL_PORT=3306
export MYSQL_USER=root
export MYSQL_PWD=secret
export MYSQL_DATABASE=mydb
```

`.env` files in the project directory are also auto-loaded.

### Option 3: Profile Configuration (Recommended for Long-Term Use)

Save once, then just use `--profile dev`:

```bash
# Save connection config (passwords are never written to the file)
db.py config set dev     --engine pg    --host localhost --dbname mydb --user postgres
db.py config set staging --engine mysql --host 10.0.0.5  --dbname app  --user deploy

# List all profiles
db.py config list

# Use by specifying the profile name
db.py pg inspect -s public --profile dev --password secret
db.py mysql tables -s app --profile staging --password secret
```

> Config file is stored at `~/.dbtools.json` with permissions automatically set to 600 on Unix/Linux/macOS (a warning is displayed on Windows).

---

## Feature Details

### 1. Inspect Database Structure

```bash
# List all schemas / databases
db.py pg schemas
db.py mysql schemas

# List all tables in a schema
db.py pg tables -s public
db.py mysql tables -s mydb

# Inspect table details (Markdown format by default)
db.py pg inspect -s public                    # All tables
db.py pg inspect -s public -t users orders    # Specific tables
db.py pg inspect -s public -t users -f json   # JSON format
```

Output includes: column definitions, types, nullability, defaults, comments, constraints (PK/FK/UNIQUE), indexes, and table size.

### 2. Generate DDL Scripts

```bash
# PostgreSQL
db.py pg ddl -s public                    # Entire schema
db.py pg ddl -s public -t users orders    # Specific tables
db.py pg ddl -s public -o init.sql        # Output to file

# MySQL (two modes)
db.py mysql ddl -s mydb                   # Default: uses SHOW CREATE TABLE
db.py mysql ddl -s mydb --mode build      # Build mode (useful for diff scenarios)
db.py mysql ddl -s mydb -o init.sql       # Output to file
```

PG DDL covers: SCHEMA / EXTENSION / TYPE (enum) / SEQUENCE / TABLE / INDEX / FUNCTION / TRIGGER / COMMENT.

### 3. Performance Analysis Report

```bash
db.py pg report -s public
db.py pg report -s public -o report.md

db.py mysql report -s mydb
db.py mysql report -s mydb -o report.md
```

**PostgreSQL Analysis Dimensions**:

| Dimension | Description |
|-----------|-------------|
| Database Overview | Cache hit ratio, connections, deadlocks, temp files |
| Unused Indexes | Indexes that have never been scanned |
| Duplicate Indexes | Index pairs with identical column combinations |
| Redundant Indexes | Indexes fully covered by composite indexes |
| Missing FK Indexes | Foreign key columns without indexes |
| Sequential Scan Hot Tables | Large tables with frequent full table scans |
| Table I/O Cache | Per-table cache efficiency |
| Table Bloat | Excessive dead tuples |
| Slow Queries | Requires pg_stat_statements extension |
| Lock Waits | Current blocking situations |

**MySQL Analysis Dimensions**:

| Dimension | Description |
|-----------|-------------|
| Server Overview | Version, connections, buffer pool size, uptime |
| InnoDB Buffer Pool | Hit ratio, dirty pages, free pages |
| Redundant Indexes | Uses sys views on 8.0+ / fallback for 5.7 |
| Unused Indexes | Requires performance_schema |
| Missing FK Indexes | Foreign key columns without indexes |
| High-Read Tables | Table-level I/O statistics |
| Table Fragmentation | DATA_FREE ratio |
| Slow Queries | Based on performance_schema |

### 4. Generate Optimization Scripts

```bash
db.py pg optimize -s public
db.py pg optimize -s public -o optimize.sql
db.py pg optimize -s public --no-concurrently  # Without CONCURRENTLY

db.py mysql optimize -s mydb
db.py mysql optimize -s mydb -o optimize.sql
```

Script contents: create missing indexes → drop unused indexes → drop redundant indexes → table maintenance (VACUUM / OPTIMIZE TABLE).

> **All DROP operations include comments explaining the reason. Review carefully before executing.**

### 5. Structure Diff

Compare table structures between two databases. Automatically generates migration DDL for same-engine comparisons.

```bash
# Profile-based diff
db.py diff --source dev --target prod -s public

# DSN-based diff
db.py diff \
  --source "postgresql://localhost/dev_db" \
  --target "postgresql://localhost/prod_db" \
  -s public

# Snapshot file diff
db.py diff --source dev.json --target prod.json

# Mixed diff (snapshot vs live database)
db.py diff --source dev.json --target prod -s public
```

**Output includes**:
- Added / removed / modified tables
- Column changes (type, nullability, defaults)
- Index changes, constraint changes
- Same engine: generates `ALTER TABLE` migration DDL
- Cross-engine (PG ↔ MySQL): generates comparison report only

### 6. Structure Snapshot

Save database structure as a JSON file for version control and offline comparison:

```bash
db.py snapshot --profile dev -s public -o dev_20260305.json
```

Typical workflow:

```bash
# Export snapshot before changes
db.py snapshot --profile prod -s public -o before.json

# Apply database changes...

# Export snapshot after changes
db.py snapshot --profile prod -s public -o after.json

# Compare differences
db.py diff --source before.json --target after.json
```

---

## Project Structure

```
database-tools-skills/
├── db.py                       # Unified entry point (recommended)
├── SKILL.md                    # Claude skill documentation
├── scripts/
│   ├── pg_inspector.py         # PG structure inspection + DDL (standalone)
│   ├── pg_index_advisor.py     # PG index performance analysis (standalone)
│   ├── mysql_inspector.py      # MySQL structure inspection + DDL
│   └── mysql_index_advisor.py  # MySQL index performance analysis
├── lib/
│   ├── schema_model.py         # Unified data model
│   ├── connection.py           # Connection management
│   ├── config.py               # Profile configuration (~/.dbtools.json)
│   ├── formatters.py           # Markdown / JSON output formatting
│   ├── snapshot.py             # Snapshot export / import
│   └── differ.py               # Structure diff engine + migration DDL generation
└── references/
    ├── pg_queries.md            # PG system view query reference
    └── mysql_queries.md         # MySQL system table query reference
```

---

## FAQ

**Q: What database permissions are required?**

Only `SELECT` privileges are needed. Performance analysis requires access to `pg_stat_*` / `performance_schema` views. Slow query analysis on PG requires the `pg_stat_statements` extension to be enabled.

**Q: Are passwords stored securely?**

The profile config file `~/.dbtools.json` does not store passwords. File permissions are automatically set to 600 (owner read/write only) on Unix/Linux/macOS; a warning is displayed on Windows. Passwords are passed via the `--password` argument or environment variables.

**Q: Is MySQL 5.7 supported?**

Yes. Redundant index detection uses `information_schema` fallback queries on 5.7, and the more accurate `sys.schema_redundant_indexes` on 8.0+.

**Q: Can cross-engine diff (PG ↔ MySQL) generate migration scripts?**

No. The type systems differ too much between the two engines. Only a comparison report is generated for manual review. Same-engine diffs will automatically generate migration DDL.
