"""
统一连接管理器
支持 PostgreSQL (psycopg2) 和 MySQL (PyMySQL)
"""

import os
from urllib.parse import urlparse, unquote
from . import config


def _safe_int(value, default):
    """安全的 int 转换，失败时返回默认值"""
    if value is None:
        return default
    try:
        return int(value)
    except (ValueError, TypeError):
        return default


def _load_env_file(env_file=None):
    """从 .env 文件加载环境变量（跳过空值，避免覆盖有效默认值）"""
    path = env_file or ".env"
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip().strip("\r")
                if line and not line.startswith("#") and "=" in line:
                    key, val = line.split("=", 1)
                    val = val.strip().strip("\r").strip('"').strip("'")
                    if val:  # 跳过空值，防止 int("") 等下游错误
                        os.environ.setdefault(key.strip(), val)


def _parse_mysql_dsn(dsn, charset="utf8mb4"):
    """安全解析 MySQL DSN，正确处理密码中的特殊字符"""
    import pymysql
    parsed = urlparse(dsn)
    return pymysql.connect(
        host=parsed.hostname or "localhost",
        port=parsed.port or 3306,
        user=unquote(parsed.username) if parsed.username else "root",
        password=unquote(parsed.password) if parsed.password else "",
        database=parsed.path.lstrip("/") if parsed.path else None,
        charset=charset,
        cursorclass=pymysql.cursors.DictCursor,
    )


def connect_pg(host=None, port=None, user=None, password=None,
               dbname=None, dsn=None, env_file=None):
    """
    创建 PostgreSQL 连接
    优先级: 显式参数 > 环境变量 > .env 文件 > 默认值
    """
    try:
        import psycopg2
    except ImportError:
        raise ImportError("需要安装 psycopg2-binary: pip install psycopg2-binary")

    _load_env_file(env_file)

    dsn = dsn or os.environ.get("DATABASE_URL") or os.environ.get("PG_DSN")
    if dsn:
        return psycopg2.connect(dsn)

    params = {
        "host": host or os.environ.get("PGHOST", "localhost"),
        "port": _safe_int(port or os.environ.get("PGPORT"), 5432),
        "user": user or os.environ.get("PGUSER", "postgres"),
        "password": password or os.environ.get("PGPASSWORD", ""),
        "dbname": dbname or os.environ.get("PGDATABASE", "postgres"),
    }
    return psycopg2.connect(**params)


def connect_mysql(host=None, port=None, user=None, password=None,
                  database=None, dsn=None, charset="utf8mb4", env_file=None):
    """
    创建 MySQL 连接
    优先级: 显式参数 > 环境变量 > .env 文件 > 默认值
    """
    try:
        import pymysql
    except ImportError:
        raise ImportError("需要安装 pymysql: pip install pymysql")

    _load_env_file(env_file)

    if dsn:
        return _parse_mysql_dsn(dsn, charset)

    params = {
        "host": host or os.environ.get("MYSQL_HOST", "localhost"),
        "port": _safe_int(port or os.environ.get("MYSQL_PORT"), 3306),
        "user": user or os.environ.get("MYSQL_USER", "root"),
        "password": password or os.environ.get("MYSQL_PWD", ""),
        "database": database or os.environ.get("MYSQL_DATABASE"),
        "charset": charset,
        "cursorclass": pymysql.cursors.DictCursor,
    }
    # 移除 None 值
    params = {k: v for k, v in params.items() if v is not None}
    return pymysql.connect(**params)


def from_profile(profile_name: str, password=None):
    """
    从配置文件 profile 创建连接
    密码需运行时传入或通过环境变量提供
    """
    prof = config.get_profile(profile_name)
    if not prof:
        raise ValueError(f"未找到 profile: {profile_name}")

    engine = prof["engine"]
    conn_params = prof.get("connection", {})

    if engine in ("pg", "postgresql"):
        return connect_pg(
            host=conn_params.get("host"),
            port=conn_params.get("port"),
            user=conn_params.get("user"),
            password=password,
            dbname=conn_params.get("dbname"),
            dsn=conn_params.get("dsn"),
        )
    elif engine == "mysql":
        return connect_mysql(
            host=conn_params.get("host"),
            port=conn_params.get("port"),
            user=conn_params.get("user"),
            password=password,
            database=conn_params.get("database") or conn_params.get("dbname"),
            dsn=conn_params.get("dsn"),
        )
    else:
        raise ValueError(f"不支持的引擎类型: {engine}")


def from_args(args, engine=None):
    """
    从 argparse Namespace 创建连接
    engine: 'pg' 或 'mysql'，如果不指定则从 args 中推断
    """
    # 优先使用 profile
    profile_name = getattr(args, "profile", None)
    if profile_name:
        password = getattr(args, "password", None)
        return from_profile(profile_name, password=password)

    eng = engine or getattr(args, "engine", "pg")
    dsn = getattr(args, "dsn", None)
    host = getattr(args, "host", None)
    port = getattr(args, "port", None)
    user = getattr(args, "user", None)
    password = getattr(args, "password", None)
    dbname = getattr(args, "dbname", None) or getattr(args, "database", None)
    env_file = getattr(args, "env_file", None)

    if eng in ("pg", "postgresql"):
        return connect_pg(
            host=host, port=port, user=user,
            password=password, dbname=dbname,
            dsn=dsn, env_file=env_file,
        )
    elif eng == "mysql":
        return connect_mysql(
            host=host, port=port, user=user,
            password=password, database=dbname,
            dsn=dsn, env_file=env_file,
        )
    else:
        raise ValueError(f"不支持的引擎类型: {eng}")
