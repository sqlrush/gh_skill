"""pg8000 后端：openGauss/GaussDB 的 PostgreSQL wire 协议直连。

逻辑搬迁自原 common/db.py（保持完全一致的行为）。会话默认钉 READ ONLY。
"""
from __future__ import annotations

import ssl
from typing import Any, Optional, Sequence

import pg8000.dbapi

from .base import Backend, DBError

CONNECT_TIMEOUT = 15  # 秒，对齐 gdaa pingTimeout

_SSL_MODES = frozenset({"allow", "prefer", "require", "verify-ca", "verify-full"})


def _ssl_context(sslmode: str) -> Optional[ssl.SSLContext]:
    if sslmode not in _SSL_MODES:
        return None
    ctx = ssl.create_default_context()
    if sslmode in ("allow", "prefer", "require"):
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
    return ctx


def _format_pg_error(exc: Exception) -> str:
    args = getattr(exc, "args", None)
    if args and isinstance(args[0], dict):
        fields = args[0]
        msg = fields.get("M", str(exc))
        code = fields.get("C", "")
        if code:
            return f"ERROR: {msg} (SQLSTATE {code})"
        return f"ERROR: {msg}"
    return str(exc)


class Pg8000Backend(Backend):
    name = "pg8000"
    provides_session = True  # 单条持久连接:会话级 GUC / hypopg 虚拟索引跨语句留存

    def __init__(self, raw: "pg8000.dbapi.Connection", conn: Any):
        self._raw = raw
        self.conn = conn

    @classmethod
    def open(cls, conn: Any, password: str, read_only: bool = True) -> "Pg8000Backend":
        try:
            raw = pg8000.dbapi.connect(
                host=conn.host,
                port=conn.port,
                database=conn.database,
                user=conn.user,
                password=password,
                timeout=CONNECT_TIMEOUT,
                ssl_context=_ssl_context(conn.sslmode or "disable"),
            )
        except Exception as exc:
            raise DBError(
                f"connect to {conn.name} "
                f"({conn.user}@{conn.host}:{conn.port}/{conn.database}): {exc}"
            ) from exc

        raw.autocommit = True
        b = cls(raw, conn)
        # pg8000 applies `timeout` to the socket for the connection's whole life,
        # not just the handshake — so a 15s cap would kill any query running
        # longer than that, whatever server-side statement_timeout we set. Hand
        # the handshake its bound, then let the server own the query deadline.
        b._set_socket_timeout(None)
        if read_only:
            try:
                b.execute("SET SESSION CHARACTERISTICS AS TRANSACTION READ ONLY")
            except DBError:
                b.execute("SET default_transaction_read_only = on")
        return b

    def _set_socket_timeout(self, seconds) -> None:
        """Retune the raw socket. `_usock` is pg8000-private: tolerate its absence."""
        sock = getattr(self._raw, "_usock", None)
        if sock is not None and hasattr(sock, "settimeout"):
            sock.settimeout(seconds)

    def query(self, sql, params=None):
        cur = self._raw.cursor()
        try:
            cur.execute(sql, params or ())
            cols = [d[0] for d in (cur.description or [])]
            rows = [tuple(r) for r in cur.fetchall()] if cur.description else []
            return cols, rows
        except Exception as exc:
            raise DBError(_format_pg_error(exc)) from exc
        finally:
            cur.close()

    def query_in_rollback(self, sql, params=None):
        prev = self._raw.autocommit
        self._raw.autocommit = False
        cur = self._raw.cursor()
        try:
            cur.execute(sql, params or ())
            cols = [d[0] for d in (cur.description or [])]
            rows = [tuple(r) for r in cur.fetchall()] if cur.description else []
            return cols, rows
        except Exception as exc:
            raise DBError(_format_pg_error(exc)) from exc
        finally:
            try:
                self._raw.rollback()
            finally:
                cur.close()
                self._raw.autocommit = prev

    def set_statement_timeout(self, seconds: int) -> None:
        secs = int(seconds)
        self.execute(f"SET statement_timeout = {secs * 1000}")
        # The server owns the deadline; the socket is only a backstop, so give it
        # strictly more room (mirrors GsqlBackend, which budgets
        # CONNECT_TIMEOUT + statement_timeout for its subprocess). 0 = no limit.
        self._set_socket_timeout(None if secs <= 0 else secs + CONNECT_TIMEOUT)

    def execute(self, sql, params=None) -> None:
        cur = self._raw.cursor()
        try:
            cur.execute(sql, params or ())
        except Exception as exc:
            raise DBError(_format_pg_error(exc)) from exc
        finally:
            cur.close()

    def close(self) -> None:
        try:
            self._raw.close()
        except Exception:
            pass
