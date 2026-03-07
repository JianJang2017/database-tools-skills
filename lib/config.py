"""
Profile 配置管理
配置文件: ~/.dbtools.json
密码不存入配置文件，推荐通过环境变量或运行时传入
"""

import json
import os
import stat
from typing import Optional

CONFIG_PATH = os.path.expanduser("~/.dbtools.json")

DEFAULT_CONFIG = {"profiles": {}}


def _ensure_permissions(path):
    """确保配置文件权限为 600（仅 Unix 有效）"""
    if os.path.exists(path):
        if os.name != "nt":
            os.chmod(path, stat.S_IRUSR | stat.S_IWUSR)
        else:
            import sys
            print(
                f"警告: Windows 系统无法设置文件权限，配置文件 {path} 可能被其他用户读取。",
                file=sys.stderr
            )


def load() -> dict:
    """加载配置文件"""
    if not os.path.exists(CONFIG_PATH):
        return DEFAULT_CONFIG.copy()
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def save(config: dict):
    """保存配置文件（自动设置 600 权限）"""
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(config, f, indent=2, ensure_ascii=False)
    _ensure_permissions(CONFIG_PATH)


def get_profile(name: str) -> Optional[dict]:
    """获取指定 profile"""
    config = load()
    return config.get("profiles", {}).get(name)


def set_profile(name: str, engine: str, **connection_params):
    """
    设置 profile
    engine: 'pg' 或 'mysql'
    connection_params: host, port, user, dbname 等（不含密码）
    """
    config = load()
    if "profiles" not in config:
        config["profiles"] = {}

    # 过滤掉密码和空值
    params = {k: v for k, v in connection_params.items()
              if v is not None and k not in ("password", "pwd")}

    config["profiles"][name] = {
        "engine": engine,
        "connection": params,
    }
    save(config)


def remove_profile(name: str) -> bool:
    """删除 profile，返回是否成功"""
    config = load()
    if name in config.get("profiles", {}):
        del config["profiles"][name]
        save(config)
        return True
    return False


def list_profiles() -> dict:
    """列出所有 profile"""
    config = load()
    return config.get("profiles", {})
