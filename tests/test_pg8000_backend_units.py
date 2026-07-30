# tests/test_pg8000_backend_units.py
import sys, pathlib
_ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT))

import pytest  # noqa: E402
from common.config import Connection  # noqa: E402
from common.backends.base import DBError  # noqa: E402
from common.backends import pg8000_backend as pgb  # noqa: E402

class FakeCursor:
    def __init__(self, desc, rows): self._desc, self._rows, self.description = desc, rows, desc
    def execute(self, sql, params=()): self.last = (sql, params)
    def fetchall(self): return self._rows
    def close(self): pass

class FakeSock:
    """Stands in for pg8000's raw socket (`Connection._usock`)."""
    def __init__(self): self.timeout = pgb.CONNECT_TIMEOUT
    def settimeout(self, t): self.timeout = t
    def gettimeout(self): return self.timeout

class FakeConn:
    def __init__(self, desc=None, rows=None):
        self.autocommit = False
        self._cur = FakeCursor(desc, rows or [])
        self.executed = []
        self._usock = FakeSock()
    def cursor(self):
        self.executed.append("cursor")
        return self._cur
    def rollback(self): self.executed.append("rollback")
    def close(self): self.executed.append("close")

def _conn(): return Connection(name="a", type="opengauss", host="h", port=5432, database="d", user="u")

def test_open_pins_read_only(monkeypatch):
    fake = FakeConn()
    monkeypatch.setattr(pgb.pg8000.dbapi, "connect", lambda **kw: fake)
    b = pgb.Pg8000Backend.open(_conn(), "pw", read_only=True)
    # 只读钉：execute 过 SET ... READ ONLY
    assert fake.autocommit is True
    # 强断言：最后真执行的语句就是只读钉 SQL（否则 autocommit 在钉之前
    # 已被无条件置 True，断言会即便钉从未执行也误判通过）
    assert fake._cur.last[0] == "SET SESSION CHARACTERISTICS AS TRANSACTION READ ONLY"

def test_open_skips_pin_when_not_read_only(monkeypatch):
    fake = FakeConn()
    monkeypatch.setattr(pgb.pg8000.dbapi, "connect", lambda **kw: fake)
    b = pgb.Pg8000Backend.open(_conn(), "pw", read_only=False)
    # 非只读：不钉只读，cursor 从未被取用，故无任何 SQL 被执行
    assert "cursor" not in fake.executed
    assert not hasattr(fake._cur, "last")

def test_query_returns_cols_and_rows(monkeypatch):
    fake = FakeConn(desc=[("a",), ("b",)], rows=[(1, "x")])
    monkeypatch.setattr(pgb.pg8000.dbapi, "connect", lambda **kw: fake)
    b = pgb.Pg8000Backend.open(_conn(), "pw", read_only=False)
    cols, rows = b.query("select 1 a, 'x' b")
    assert cols == ["a", "b"]
    assert rows == [(1, "x")]

def test_open_connect_failure_raises_dberror(monkeypatch):
    def boom(**kw): raise RuntimeError("refused")
    monkeypatch.setattr(pgb.pg8000.dbapi, "connect", boom)
    with pytest.raises(DBError):
        pgb.Pg8000Backend.open(_conn(), "pw")


# --- 客户端 socket 超时不得掐死长查询 ---------------------------------------
# 回归：pg8000 的 connect(timeout=15) 会把 15s 设成整条连接的 socket 超时，
# 于是任何跑超过 15s 的查询都在客户端被 socket.timeout 打断——无论
# set_statement_timeout() 把服务端超时设成多少。实测在 openGauss 上，一条
# 16s 的排序查询直接报 "timed out"，而我们明明设的是 60s。
# 约定：握手仍受 CONNECT_TIMEOUT 约束；握手成功后交给服务端 statement_timeout，
# 客户端 socket 只做兜底（服务端超时 + 握手余量）。

def test_socket_timeout_is_released_after_connect(monkeypatch):
    fake = FakeConn()
    monkeypatch.setattr(pgb.pg8000.dbapi, "connect", lambda **kw: fake)
    pgb.Pg8000Backend.open(_conn(), "pw", read_only=True)
    assert fake._usock.gettimeout() is None, \
        "握手后 socket 仍是 15s 超时：长查询会被客户端掐断"


def test_connect_still_bounded_by_connect_timeout(monkeypatch):
    """握手本身必须有界，否则连不上的主机会挂死。"""
    seen = {}
    def fake_connect(**kw):
        seen.update(kw)
        return FakeConn()
    monkeypatch.setattr(pgb.pg8000.dbapi, "connect", fake_connect)
    pgb.Pg8000Backend.open(_conn(), "pw")
    assert seen["timeout"] == pgb.CONNECT_TIMEOUT


def test_statement_timeout_also_bounds_the_socket(monkeypatch):
    """服务端先超时，客户端 socket 兜底——不能反过来。"""
    fake = FakeConn()
    monkeypatch.setattr(pgb.pg8000.dbapi, "connect", lambda **kw: fake)
    b = pgb.Pg8000Backend.open(_conn(), "pw", read_only=False)
    b.set_statement_timeout(60)
    assert fake._cur.last[0] == "SET statement_timeout = 60000"
    assert fake._usock.gettimeout() > 60, \
        "socket 超时必须严格大于服务端 statement_timeout，否则客户端先掐"


def test_zero_statement_timeout_leaves_socket_blocking(monkeypatch):
    fake = FakeConn()
    monkeypatch.setattr(pgb.pg8000.dbapi, "connect", lambda **kw: fake)
    b = pgb.Pg8000Backend.open(_conn(), "pw", read_only=False)
    b.set_statement_timeout(0)
    assert fake._usock.gettimeout() is None


def test_missing_usock_attribute_does_not_crash(monkeypatch):
    """pg8000 内部属性是私有的——换版本没了也不能炸。"""
    fake = FakeConn()
    del fake._usock
    monkeypatch.setattr(pgb.pg8000.dbapi, "connect", lambda **kw: fake)
    b = pgb.Pg8000Backend.open(_conn(), "pw", read_only=False)
    b.set_statement_timeout(30)          # 不抛异常即可
