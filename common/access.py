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
from .grmp.registry import Registry
from .grmp.runner import DirectRunner
from .grmp.settings import Settings

TOKEN_ENV = "GRMP_AUTH_TOKEN"

DIRECT_DRIVERS = frozenset({"gsql", "pg8000"})


class AccessError(Exception):
    """选路或凭据缺失。一律在构造时抛出，不拖到第一次请求。"""


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
