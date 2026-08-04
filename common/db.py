"""Database 门面：对各 skill 暴露稳定接口，内部委托给某个 Backend。

本层只做：后端选择/兜底、scalar 派生、上下文管理。具体的连接/查询/类型
处理在 backends/ 各后端里。DBError 从 backends.base 再导出，保持
`from common.db import DBError` 向后兼容。
"""
from __future__ import annotations

from typing import Any, Optional, Sequence

from .backends.base import Backend, DBError  # 再导出
from .config import find
from .credential import load_secret

# 不做跨驱动兜底：配置里写的 driver 就是实际使用的 driver。
#
# 原先失败时会自动改用另一个驱动。那让同一份配置在不同机器上跑出不同后端，
# 而两者能力不同 —— gsql 每条语句起独立子进程（provides_session=False），
# pg8000 是单条持久连接（True）。于是 hypopg 虚拟索引验证这类依赖会话的功能，
# 在「配了 gsql、实际兜底到 pg8000」的机器上能跑，在真用 gsql 的客户环境
# 跑不了，而且不报错。
#
# 本机没有 gsql 时，在 config.yaml 里另配一条 driver: pg8000 的连接。


def _load_backend(driver: str):
    """惰性导入指定后端类（gsql-only 环境无需装 pg8000，反之亦然）。"""
    if driver == "pg8000":
        from .backends.pg8000_backend import Pg8000Backend
        return Pg8000Backend
    if driver == "gsql":
        from .backends.gsql_backend import GsqlBackend
        return GsqlBackend
    raise DBError(f"unknown driver {driver!r}")


class Database:
    """委托给一个 Backend 的薄门面（连接句柄，状态性资源）。"""

    def __init__(self, backend: Backend, conn: Any):
        self._backend = backend
        self.conn = conn

    @classmethod
    def open(cls, conn: Any, password: str, read_only: bool = True) -> "Database":
        driver = conn.driver or "gsql"
        try:
            backend = _load_backend(driver).open(
                conn, password, read_only=read_only
            )
        except DBError as exc:
            raise DBError(
                f"connect to {conn.name} with driver {driver!r}: {exc}\n"
                f"（driver 严格生效，不会自动改用其他驱动。"
                f"要换驱动请改 config.yaml 里该连接的 driver 字段。）"
            ) from exc
        return cls(backend, conn)

    @classmethod
    def connect(cls, name: str, read_only: bool = True) -> "Database":
        conn = find(name)
        return cls.open(conn, load_secret(conn.name), read_only=read_only)

    def query(self, sql, params=None):
        return self._backend.query(sql, params)

    def scalar(self, sql, params=None):
        _, rows = self.query(sql, params)
        return rows[0][0] if rows else None

    def query_in_rollback(self, sql, params=None):
        return self._backend.query_in_rollback(sql, params)

    def set_statement_timeout(self, seconds: int) -> None:
        self._backend.set_statement_timeout(seconds)

    def execute(self, sql, params=None) -> None:
        self._backend.execute(sql, params)

    def close(self) -> None:
        self._backend.close()

    @property
    def provides_session(self) -> bool:
        """底层后端是否提供跨语句持久会话(hypopg 索引验证依赖它)。"""
        return self._backend.provides_session

    def __enter__(self) -> "Database":
        return self

    def __exit__(self, *exc) -> None:
        self.close()
