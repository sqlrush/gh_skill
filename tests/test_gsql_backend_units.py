# tests/test_gsql_backend_units.py
import sys, pathlib, types
_ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT))

import pytest  # noqa: E402
from common.config import Connection  # noqa: E402
from common.backends.base import DBError  # noqa: E402
from common.backends import gsql_backend as gb  # noqa: E402

def _conn(**kw):
    base = dict(name="a", type="opengauss", host="h", port=5432,
                database="d", user="u", driver="gsql")
    base.update(kw)
    return Connection(**base)

class FakeCompleted:
    def __init__(self, rc=0, out="", err=""):
        self.returncode, self.stdout, self.stderr = rc, out, err

def _patch(monkeypatch, *, rc=0, out="", err="", sink=None):
    monkeypatch.setattr(gb.shutil, "which", lambda x: "/usr/bin/gsql")
    def fake_run(argv, **kw):
        if sink is not None:
            sink.append((argv, kw))
        return FakeCompleted(rc=rc, out=out, err=err)
    monkeypatch.setattr(gb.subprocess, "run", fake_run)

def test_open_verifies_with_select_1(monkeypatch):
    calls = []
    _patch(monkeypatch, out="[{\"?column?\":1}]\n", sink=calls)
    b = gb.GsqlBackend.open(_conn(), "secret")
    assert isinstance(b, gb.GsqlBackend)
    # 验活确有一次调用，且发出的就是 SELECT 1
    assert calls
    assert any("SELECT 1" in a for a in calls[0][0])

def test_password_goes_via_env_not_argv(monkeypatch):
    calls = []
    _patch(monkeypatch, out="[{\"?column?\":1}]\n", sink=calls)
    gb.GsqlBackend.open(_conn(), "secretpw")
    argv, kw = calls[0]
    assert "secretpw" not in " ".join(argv)
    assert kw["env"]["PGPASSWORD"] == "secretpw"

def test_missing_binary_raises_dberror(monkeypatch):
    monkeypatch.setattr(gb.shutil, "which", lambda x: None)
    with pytest.raises(DBError):
        gb.GsqlBackend.open(_conn(), "pw")

def test_query_wraps_select_and_parses_json(monkeypatch):
    calls = []
    _patch(monkeypatch, out='[{"a":1,"b":"x"}]\n', sink=calls)
    b = gb.GsqlBackend.open(_conn(), "pw", read_only=False)
    calls.clear()
    cols, rows = b.query("SELECT a, b FROM t")
    assert cols == ["a", "b"] and rows == [(1, "x")]
    sent = calls[-1][0]
    assert any("json_agg(row_to_json(_t))" in a for a in sent)

def test_read_only_prefix_present(monkeypatch):
    calls = []
    _patch(monkeypatch, out="[]\n", sink=calls)
    b = gb.GsqlBackend.open(_conn(), "pw", read_only=True)
    calls.clear()
    b.query("SELECT 1")
    sent = " ".join(calls[-1][0])
    assert "default_transaction_read_only = on" in sent

def test_show_uses_text_bypass(monkeypatch):
    calls = []
    _patch(monkeypatch, out="on\n", sink=calls)
    b = gb.GsqlBackend.open(_conn(), "pw", read_only=False)
    calls.clear()
    cols, rows = b.query("SHOW enable_wdr_snapshot")
    assert rows == [("on",)]
    sent = " ".join(calls[-1][0])
    assert "json_agg" not in sent

def test_query_in_rollback_wraps_begin_rollback(monkeypatch):
    calls = []
    _patch(monkeypatch, out="Seq Scan\n", sink=calls)
    b = gb.GsqlBackend.open(_conn(), "pw", read_only=False)
    calls.clear()
    b.query_in_rollback("EXPLAIN ANALYZE INSERT INTO t VALUES (1)")
    sent = " ".join(calls[-1][0])
    assert "BEGIN;" in sent and "ROLLBACK;" in sent

def test_query_in_rollback_with_timeout(monkeypatch):
    calls = []
    _patch(monkeypatch, out="Seq Scan\n", sink=calls)
    b = gb.GsqlBackend.open(_conn(), "pw", read_only=False)
    b.set_statement_timeout(5)
    calls.clear()
    b.query_in_rollback("EXPLAIN ANALYZE INSERT INTO t VALUES (1)")
    sent = " ".join(calls[-1][0])
    assert "BEGIN;" in sent and "ROLLBACK;" in sent
    assert "SET statement_timeout = 5000;" in sent
    assert "INSERT INTO t VALUES (1)" in sent

def test_sql_error_raises_parsed_dberror(monkeypatch):
    _patch(monkeypatch, rc=1, err='gsql: ERROR:  42P01: relation "x" does not exist\n')
    # 直接构造实例（绕过 open 的验活）以测查询错误路径：
    inst = gb.GsqlBackend(_conn(), "pw", "/usr/bin/gsql", read_only=False)
    with pytest.raises(DBError) as ei:
        inst.query("SELECT * FROM x")
    assert "42P01" in str(ei.value)
