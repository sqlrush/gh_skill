"""当前会话选中的数据库连接 —— **按句柄分文件**。

gaussdb-login 选定一个连接后写在这里，其余 skill 从这里取。存的是**整条连接定义**而不只是名字：
api 模式下的连接是登录时按用户给的库名现场构造的，配置文件里根本没有它，只存名字会导致下一个
skill 找不到。不存密码：api 模式的令牌、gsql 模式的口令仍各走各的通道，会话文件即使被看到也拿不到凭据。

**为什么按句柄分文件（2026-09-07 现场缺陷）**：客户多个用户共用一个沙箱、一个 GSDB_HOME。原先只有
一个 session.yaml，谁最后登录所有人就连谁的库——退出码 0、不报错、报告抬头写着别人的库，静默串库。
现在每次登录得到一个随机句柄，写 `sessions/<句柄>.yaml`；其余 skill 用 `--session <句柄>`（或环境变量
GSDB_SESSION，health 派生的子 skill 靠它继承）指名要哪一份。沙箱里只有一个会话时不用句柄，单用户
用法不变；**多个会话又没给句柄时拒绝执行并列出候选**——宁可多问一句，也不猜：猜错的后果是在别人的
库上跑诊断，而输出看起来完全正常。

闲置超过 TTL（默认 12 小时，GSDB_SESSION_TTL_HOURS 可调）的会话自动清理，否则用户走了以后沙箱里
残留一堆会话，后来的人每条命令都被拦。旧的单文件 session.yaml 在没有任何句柄会话时仍可用（升级过渡）。
文件权限 0600。
"""
from __future__ import annotations

import os
import pathlib
import re
import secrets
import time
from dataclasses import dataclass
from typing import List, Optional

import yaml

from .config import Connection, ConfigError, ensure_dir, state_dir, validate

_DIRNAME = "sessions"
_LEGACY_FILENAME = "session.yaml"
ENV_HANDLE = "GSDB_SESSION"
ENV_TTL = "GSDB_SESSION_TTL_HOURS"
DEFAULT_TTL_HOURS = 12
HANDLE_RE = re.compile(r"^[a-z0-9]{4,16}$")
_ALPHABET = "abcdefghjkmnpqrstuvwxyz23456789"   # 去掉 0/o、1/l/i 这些肉眼易混的字符
_HANDLE_LEN = 5

# 会话文件里允许出现的键。多余的键一律拒绝 —— 手工改这个文件时写错一个键名
# （比如把 data_ip 写成 dataip），静默忽略的话会连到错误的实例上去。
_ALLOWED = frozenset({"name", "type", "host", "port", "database", "user",
                      "sslmode", "driver", "data_ip", "app"})

_selected: Optional[str] = None      # 本进程用 use() 选中的句柄（来自 --session）


@dataclass(frozen=True)
class SessionInfo:
    handle: str
    conn: Connection
    last_used: float
    path: pathlib.Path


# --- 句柄 ---------------------------------------------------------------------

def ttl_seconds() -> int:
    raw = os.environ.get(ENV_TTL, "").strip()
    try:
        hours = float(raw) if raw else float(DEFAULT_TTL_HOURS)
    except ValueError:
        hours = float(DEFAULT_TTL_HOURS)
    return int(hours * 3600)


def _check_handle(handle) -> str:
    """句柄同时是文件名的一部分：形状不合法一律拒，这里就是路径穿越的闸。"""
    if not isinstance(handle, str) or not HANDLE_RE.match(handle):
        raise ConfigError(
            "会话句柄 %r 不合法：只能是 4~16 位小写字母或数字，即 gaussdb-login 登录成功时输出的那一串。"
            % (handle,))
    return handle


def new_handle() -> str:
    return "".join(secrets.choice(_ALPHABET) for _ in range(_HANDLE_LEN))


def use(handle: Optional[str]) -> None:
    """本进程选中一个句柄（来自 --session）。传 None / 空串清除选择。"""
    global _selected
    _selected = _check_handle(handle) if handle else None


def selected() -> Optional[str]:
    """当前指名的句柄：--session > 环境变量 GSDB_SESSION；都没有则 None。"""
    if _selected:
        return _selected
    env = os.environ.get(ENV_HANDLE, "").strip()
    return _check_handle(env) if env else None


# --- 文件 ---------------------------------------------------------------------

def _dir() -> pathlib.Path:
    return state_dir() / _DIRNAME


def _legacy_path() -> pathlib.Path:
    return state_dir() / _LEGACY_FILENAME


def path_for(handle: str) -> pathlib.Path:
    return _dir() / ("%s.yaml" % _check_handle(handle))


def _write(path: pathlib.Path, payload: dict) -> None:
    # 先写临时文件再改名：中途失败不会留下半个会话文件，
    # 而半个文件解析出来可能正好是另一个合法连接
    tmp = path.with_suffix(".tmp")
    tmp.write_text(yaml.safe_dump(payload, allow_unicode=True, sort_keys=False), encoding="utf-8")
    os.chmod(tmp, 0o600)
    tmp.replace(path)


def _read(path: pathlib.Path) -> Connection:
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as exc:
        raise ConfigError("解析会话文件 %s 失败：%s" % (path, exc)) from exc
    if not isinstance(raw, dict) or not raw:
        raise ConfigError("会话文件 %s 是空的。删掉它重新运行 gaussdb-login 即可。" % path)
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


def _touch(path: pathlib.Path) -> None:
    try:
        os.utime(path, None)
    except OSError:
        pass


def target_of(conn: Connection) -> str:
    """报告 / 清单里标识目标的写法：api 模式是 dataIp，gsql 模式是 host:port。"""
    return conn.data_ip or "%s:%s" % (conn.host, conn.port)


def save(conn: Connection, handle: Optional[str] = None) -> str:
    """把选中的连接写成一份新的会话文件，返回句柄。不覆盖任何已有会话。"""
    validate(conn)
    ensure_dir()
    d = _dir()
    d.mkdir(mode=0o700, exist_ok=True)
    os.chmod(d, 0o700)
    if handle:
        handle = _check_handle(handle)
    else:
        handle = new_handle()
        while path_for(handle).exists():
            handle = new_handle()
    payload = {
        "name": conn.name, "type": conn.type, "host": conn.host,
        "port": conn.port, "database": conn.database, "user": conn.user,
        "sslmode": conn.sslmode, "driver": conn.driver,
        "data_ip": conn.data_ip, "app": conn.app,
    }
    _write(path_for(handle), payload)
    return handle


def list_sessions(prune: bool = True) -> List[SessionInfo]:
    """现存的会话，按最后使用时间倒序。过期的顺手删掉（prune=False 时只跳过）。"""
    d = _dir()
    if not d.is_dir():
        return []
    now, ttl, out = time.time(), ttl_seconds(), []
    for path in sorted(d.glob("*.yaml")):
        if not HANDLE_RE.match(path.stem):
            continue
        try:
            last_used = path.stat().st_mtime
        except OSError:
            continue
        if now - last_used > ttl:
            if prune:
                try:
                    path.unlink()
                except OSError:
                    pass
            continue
        out.append(SessionInfo(handle=path.stem, conn=_read(path), last_used=last_used, path=path))
    return sorted(out, key=lambda s: s.last_used, reverse=True)


def _ambiguous(sessions: List[SessionInfo]) -> ConfigError:
    lines = ["沙箱里有 %d 个会话，不知道该用哪个。请在命令里带上 `--session <句柄>`"
             "（或设置环境变量 GSDB_SESSION）：" % len(sessions)]
    for s in sessions:
        lines.append("  %s  %s / %s  最后使用 %s" % (
            s.handle, target_of(s.conn), s.conn.database,
            time.strftime("%m-%d %H:%M", time.localtime(s.last_used))))
    lines.append("不猜：猜错会在别人的库上跑诊断，而输出看起来完全正常。"
                 "把这份清单转给用户确认要用哪个库，或让用户重新 gaussdb-login 拿一个新句柄。")
    return ConfigError("\n".join(lines))


def current() -> Optional[Connection]:
    """当前会话选中的连接；没有则 None；分不清则抛 ConfigError（不猜）。"""
    handle = selected()
    if handle:
        path = path_for(handle)
        if not path.exists():
            raise ConfigError(
                "会话句柄 %s 不存在或已过期（闲置超过 %d 小时会自动清理）。"
                "重新运行 gaussdb-login 登录，并带上它输出的新句柄。"
                % (handle, ttl_seconds() // 3600))
        conn = _read(path)
        _touch(path)
        return conn
    sessions = list_sessions()
    if len(sessions) == 1:
        _touch(sessions[0].path)
        return sessions[0].conn
    if not sessions:
        legacy = _legacy_path()
        return _read(legacy) if legacy.exists() else None
    raise _ambiguous(sessions)


def current_handle() -> Optional[str]:
    """指名的句柄；没指名但沙箱里只有一个会话时就是它；其余 None。"""
    handle = selected()
    if handle:
        return handle
    sessions = list_sessions(prune=False)
    return sessions[0].handle if len(sessions) == 1 else None


def clear(handle: Optional[str] = None) -> bool:
    """退出一个会话。返回是否真的删了。没指名且沙箱里有多个会话时拒绝（不猜要退谁）。"""
    handle = handle or selected()
    if handle:
        path = path_for(handle)
        if path.exists():
            path.unlink()
            return True
        return False
    sessions = list_sessions()
    if len(sessions) > 1:
        raise ConfigError(
            "沙箱里有 %d 个会话，退出哪一个要带 `--session <句柄>`；要全部清掉用 `--all`。"
            % len(sessions))
    removed = False
    for s in sessions:
        s.path.unlink()
        removed = True
    legacy = _legacy_path()
    if legacy.exists():
        legacy.unlink()
        removed = True
    return removed


def clear_all() -> int:
    """清掉沙箱里全部会话（含旧的单文件），返回删掉的个数。"""
    n = 0
    for s in list_sessions(prune=False):
        s.path.unlink()
        n += 1
    legacy = _legacy_path()
    if legacy.exists():
        legacy.unlink()
        n += 1
    return n
