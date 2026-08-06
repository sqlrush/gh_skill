"""当前会话选中的数据库连接。

gaussdb-login 选定一个连接后写在这里，其余 13 个 skill 不带 `-c` 时从这里取。
存的是**整条连接定义**而不只是名字：api 模式下的连接是登录时按用户给的库名
现场构造的，配置文件里根本没有它，只存名字会导致下一个 skill 找不到。

不存密码。api 模式的令牌、gsql 模式的口令仍各走各的通道（环境变量 /
credentials 目录 / 配置里的密文），会话文件即使被看到也拿不到凭据。

文件权限 0600。它暴露的是「这台机器正在连哪个库」——不是秘密，但也不必让
同机其他用户随手看到。
"""
from __future__ import annotations

import os
import pathlib
from typing import Optional

import yaml

from .config import Connection, ConfigError, ensure_dir, state_dir, validate

_FILENAME = "session.yaml"

# 会话文件里允许出现的键。多余的键一律拒绝 —— 手工改这个文件时写错一个键名
# （比如把 data_ip 写成 dataip），静默忽略的话会连到错误的实例上去。
_ALLOWED = frozenset({"name", "type", "host", "port", "database", "user",
                      "sslmode", "driver", "data_ip", "app"})


def _path() -> pathlib.Path:
    return state_dir() / _FILENAME


def save(conn: Connection) -> pathlib.Path:
    """把选中的连接写进会话文件，返回路径。"""
    validate(conn)
    payload = {
        "name": conn.name, "type": conn.type, "host": conn.host,
        "port": conn.port, "database": conn.database, "user": conn.user,
        "sslmode": conn.sslmode, "driver": conn.driver,
        "data_ip": conn.data_ip, "app": conn.app,
    }
    path = _path()
    ensure_dir()
    # 先写临时文件再改名：中途失败不会留下半个会话文件，
    # 而半个文件解析出来可能正好是另一个合法连接
    tmp = path.with_suffix(".tmp")
    tmp.write_text(yaml.safe_dump(payload, allow_unicode=True,
                                  sort_keys=False), encoding="utf-8")
    os.chmod(tmp, 0o600)
    tmp.replace(path)
    return path


def current() -> Optional[Connection]:
    """返回当前会话选中的连接；没有则 None。"""
    path = _path()
    if not path.exists():
        return None
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as exc:
        raise ConfigError("解析会话文件 %s 失败：%s" % (path, exc)) from exc
    if not isinstance(raw, dict) or not raw:
        return None

    unknown = set(raw) - _ALLOWED
    if unknown:
        raise ConfigError(
            "会话文件 %s 里有无法识别的键：%s。\n"
            "静默忽略它们的话，写错一个键名（比如 data_ip 写成 dataip）"
            "会让下一次取数连到错误的实例上去，而且不报错。\n"
            "删掉这个文件重新运行 gaussdb-login 即可。"
            % (path, "、".join(sorted(unknown))))

    conn = Connection(
        name=raw.get("name", ""), type=raw.get("type", ""),
        host=raw.get("host", ""), port=raw.get("port", 0),
        database=raw.get("database", ""), user=raw.get("user", ""),
        sslmode=raw.get("sslmode", "") or "",
        driver=raw.get("driver", "gsql") or "gsql",
        data_ip=raw.get("data_ip", "") or "",
        app=raw.get("app", "") or "",
    )
    validate(conn)
    return conn


def clear() -> bool:
    """删除会话文件。返回是否真的删了。"""
    path = _path()
    if path.exists():
        path.unlink()
        return True
    return False
