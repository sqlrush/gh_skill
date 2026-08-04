"""skill 侧的统一入口：按连接的 driver 选路。

    runner = access.for_conn("og")
    rows = runner.run("slowsql.slow_sql", {"threshold_ms": 200, "limit": 20})

skill 不感知自己走的是中间件还是直连，两条路径返回相同形状
（全字符串化的行字典）。这一点就是本层的全部价值：skill 代码在本地
与客户环境完全相同，不需要为两边各留一套。

    driver: pg8000 / gsql  →  直连路径
    driver: grmp           →  中间件路径
"""
from __future__ import annotations

import os
from typing import Any, Optional

from .config import Connection, find
from .grmp.client import GrmpClient, GrmpRunner
from .grmp.errors import QueryError
from .grmp.registry import Registry
from .grmp.runner import DirectRunner
from .grmp.settings import Settings

# 取数失败的**唯一**对外异常类型。skill 的降级逻辑只 catch 它 ——
# 新增一种访问方式时，新 Runner 抛 QueryError 即可，skill 一行不改。
# 详见 common/grmp/errors.py 里关于「哪些错误不归一」的说明。
__all__ = ["for_conn", "runner_for", "session_for", "session_for_conn",
           "QueryError", "AccessError", "SessionUnavailable"]

TOKEN_ENV = "GRMP_AUTH_TOKEN"

DIRECT_DRIVERS = frozenset({"gsql", "pg8000"})


class AccessError(Exception):
    """选路或凭据缺失。一律在构造时抛出，不拖到第一次请求。"""


class SessionUnavailable(AccessError):
    """当前访问路径不提供跨语句的持久会话。

    hypopg 虚拟索引验证这类流程（建虚拟索引 → 在同一会话里 EXPLAIN）
    离开会话就会**静默给出错误结论**：虚拟索引没了，EXPLAIN 看到原计划，
    于是得出「加这个索引没用」。所以宁可在入口处报错，也不能让它跑下去。
    """


def _open_database(conn: Connection, read_only: bool = True):
    """打开原始连接。抽成函数是为了测试能替换掉它。"""
    from .db import Database

    return Database.connect(conn.name, read_only=read_only)


def session_for_conn(conn: Connection, read_only: bool = True):
    """索取一条**带持久会话**的原始连接，拿不到就报错。

    与 runner_for() 是两条不同的口子：runner 面向「执行一条已注册脚本」，
    这里面向「一串必须落在同一会话里的语句」。后者是白名单模型撑不住的
    场景，所以要显式索取、显式失败。

    read_only 默认 True，与 runner 一侧一致。放开它只有一个已知理由：
    EXPLAIN ANALYZE 一条 DML —— 语句要真执行（外面包回滚事务），只读会话
    会在事务里就把它挡回。默认不放开，调用方必须显式要求。
    """
    driver = conn.driver or "gsql"
    if driver == "grmp":
        raise SessionUnavailable(
            "连接 %s 的 driver 是 grmp：中间件的执行接口每次调用都是独立连接，"
            "不提供跨语句的持久会话。\n"
            "依赖会话的流程（hypopg 虚拟索引验证等）在客户的白名单模型下"
            "本来就跑不了 —— 需要为这类诊断单独保留一条直连通道，"
            "或在客户环境不提供该能力。" % conn.name
        )
    db = _open_database(conn, read_only=read_only)
    if not getattr(db, "provides_session", False):
        db.close()
        raise SessionUnavailable(
            "连接 %s 的 driver 是 %s：该后端每条语句起独立子进程，"
            "不提供跨语句的持久会话。\n"
            "本机调试请改用 driver: pg8000 的连接。" % (conn.name, driver)
        )
    return db


def session_for(name: str, read_only: bool = True):
    """按连接名索取带持久会话的原始连接。"""
    return session_for_conn(find(name), read_only=read_only)


def _base_url(conn: Connection) -> str:
    scheme = "https" if conn.sslmode in ("require", "verify-ca", "verify-full") else "http"
    return "%s://%s:%d" % (scheme, conn.host, conn.port)


def runner_for(
    conn: Connection,
    registry: Optional[Registry] = None,
    settings: Optional[Settings] = None,
) -> Any:
    """按 Connection 造一个 runner。"""
    driver = conn.driver or "gsql"
    if driver in DIRECT_DRIVERS:
        return DirectRunner(
            conn_name=conn.name,
            registry=registry or Registry(),
            settings=settings or Settings(),
        )
    if driver == "grmp":
        token = os.environ.get(TOKEN_ENV)
        if not token:
            # fail fast：令牌只从环境变量读，不落盘、不进代码。
            # 拖到第一次请求才失败，错误会表现成「中间件返回鉴权失败」，
            # 排查方向会被带到中间件那边去。
            raise AccessError(
                "连接 %s 使用 grmp 驱动，但环境变量 %s 未设置。"
                % (conn.name, TOKEN_ENV)
            )
        if not conn.data_ip:
            raise AccessError(
                "连接 %s 使用 grmp 驱动，但未配置 data_ip。" % conn.name
            )
        return GrmpRunner(
            GrmpClient(
                base_url=_base_url(conn),
                token=token,
                data_ip=conn.data_ip,
            )
        )
    raise AccessError("连接 %s 的 driver %r 不受支持" % (conn.name, driver))


def for_conn(
    name: str,
    registry: Optional[Registry] = None,
    settings: Optional[Settings] = None,
) -> Any:
    """按连接名造 runner。"""
    return runner_for(find(name), registry=registry, settings=settings)
