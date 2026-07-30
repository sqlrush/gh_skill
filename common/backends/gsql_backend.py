"""gsql 后端：本机 TCP 直连 openGauss/GaussDB 的命令行客户端。

每次查询起一个 gsql -c 子进程（无状态）；会话级设置（只读钉、
statement_timeout）作为前缀拼进每次调用。类型保真靠 json_agg 包裹
（见 gsql_protocol）。密码经 PGPASSWORD 传入，绝不进 argv。
"""
from __future__ import annotations

import os
import shutil
import subprocess
from typing import Any, Optional

from .base import Backend, DBError
from . import gsql_protocol as gp

CONNECT_TIMEOUT = 15  # 秒，对齐 pg8000


class GsqlBackend(Backend):
    name = "gsql"
    provides_session = False  # 每查询一个子进程:会话级状态/hypopg 虚拟索引不跨语句留存

    def __init__(self, conn: Any, password: str, binary: str,
                 read_only: bool = True):
        self.conn = conn
        self._password = password
        self._binary = binary
        self._read_only = read_only
        self._timeout_ms: Optional[int] = None

    @classmethod
    def open(cls, conn: Any, password: str, read_only: bool = True) -> "GsqlBackend":
        binary = shutil.which(os.environ.get("GDAA_GSQL", "gsql"))
        if not binary:
            raise DBError(
                "gsql binary not found (set GDAA_GSQL or add gsql to PATH)"
            )
        b = cls(conn, password, binary, read_only=read_only)
        b._run("SELECT 1", {})  # 验活；失败抛 DBError 供门面兜底
        return b

    # ---- 内部 ----

    def _env(self) -> dict:
        env = dict(os.environ)
        env["PGPASSWORD"] = self._password
        sslmode = self.conn.sslmode or "disable"
        env["PGSSLMODE"] = sslmode
        return env

    def _prefix(self, *, read_only: bool) -> str:
        parts = []
        if read_only:
            parts.append("SET default_transaction_read_only = on;")
        if self._timeout_ms is not None:
            parts.append(f"SET statement_timeout = {self._timeout_ms};")
        return " ".join(parts)

    def _run(self, full_sql: str, vars_: dict) -> str:
        argv = [
            self._binary,
            "-h", str(self.conn.host),
            "-p", str(self.conn.port),
            "-U", self.conn.user,
            "-d", self.conn.database,
            "-A", "-t", "-q",
            "-v", "ON_ERROR_STOP=1",
            "-v", "VERBOSITY=verbose",
        ]
        for name, value in vars_.items():
            argv += ["-v", f"{name}={value}"]
        argv += ["-c", full_sql]
        try:
            cp = subprocess.run(
                argv, capture_output=True, text=True,
                env=self._env(), timeout=CONNECT_TIMEOUT + (
                    (self._timeout_ms or 0) // 1000),
            )
        except FileNotFoundError as exc:
            raise DBError(f"gsql not executable: {exc}") from exc
        except subprocess.TimeoutExpired as exc:
            raise DBError(f"gsql timed out: {exc}") from exc
        if cp.returncode != 0:
            raise DBError(gp.parse_gsql_error(cp.stderr))
        return cp.stdout

    # ---- Backend 接口 ----

    def query(self, sql, params=None):
        body, vars_ = gp.rewrite_params(sql, params or ())
        if gp.is_wrappable_select(body):
            stmt = gp.wrap_select_json(body)
            full = f"{self._prefix(read_only=self._read_only)} {stmt}".strip()
            return gp.parse_json_result(self._run(full, vars_))
        full = f"{self._prefix(read_only=self._read_only)} {body}".strip()
        return gp.parse_text_result(self._run(full, vars_))

    def execute(self, sql, params=None) -> None:
        body, vars_ = gp.rewrite_params(sql, params or ())
        full = f"{self._prefix(read_only=self._read_only)} {body}".strip()
        self._run(full, vars_)

    def query_in_rollback(self, sql, params=None):
        # 始终走文本旁路，返回形式为 ([], [(line,), ...])。
        # 当前第一方消费者仅为 EXPLAIN ANALYZE 纯文本输出，不涉及行返回语句。
        # 若将来有行返回语句走此路径，须注意其行形与 pg8000 的 (cols, rows) 不同。
        body, vars_ = gp.rewrite_params(sql, params or ())
        parts = ["BEGIN;"]
        prefix = self._prefix(read_only=False)  # 回滚路径不注入只读钉
        if prefix:
            parts.append(prefix)
        parts.append(f"{body};")
        parts.append("ROLLBACK;")
        full = " ".join(parts)
        return gp.parse_text_result(self._run(full, vars_))

    def set_statement_timeout(self, seconds: int) -> None:
        self._timeout_ms = int(seconds) * 1000

    def close(self) -> None:
        pass  # 无常驻连接
