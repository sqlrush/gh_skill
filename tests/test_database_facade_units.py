# tests/test_database_facade_units.py
import sys, pathlib
_ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT))

import pytest  # noqa: E402
from common.config import Connection  # noqa: E402
from common.backends.base import DBError  # noqa: E402
import common.db as dbmod  # noqa: E402

def _conn(driver="gsql"):
    return Connection(name="a", type="opengauss", host="h", port=5432,
                      database="d", user="u", driver=driver)

class FakeBackend:
    def __init__(self, tag): self.tag = tag
    @classmethod
    def make(cls, tag, fail):
        def _open(conn, password, read_only=True):
            if fail:
                raise DBError(f"{tag} cannot connect")
            return cls(tag)
        return _open

def test_uses_preferred_driver(monkeypatch):
    loaded = {}
    def fake_load(driver):
        loaded["driver"] = driver
        ok = type("B", (), {"open": staticmethod(FakeBackend.make(driver, fail=False))})
        return ok
    monkeypatch.setattr(dbmod, "_load_backend", fake_load)
    db = dbmod.Database.open(_conn(driver="gsql"), "pw")
    assert loaded["driver"] == "gsql"

def test_falls_back_when_preferred_fails(monkeypatch):
    seen = []
    def fake_load(driver):
        seen.append(driver)
        fail = (driver == "gsql")
        return type("B", (), {"open": staticmethod(FakeBackend.make(driver, fail=fail))})
    monkeypatch.setattr(dbmod, "_load_backend", fake_load)
    db = dbmod.Database.open(_conn(driver="gsql"), "pw")
    assert seen == ["gsql", "pg8000"]      # 先首选后兜底
    assert db._backend.tag == "pg8000"

def test_raises_when_all_fail(monkeypatch):
    def fake_load(driver):
        return type("B", (), {"open": staticmethod(FakeBackend.make(driver, fail=True))})
    monkeypatch.setattr(dbmod, "_load_backend", fake_load)
    with pytest.raises(DBError) as ei:
        dbmod.Database.open(_conn(driver="pg8000"), "pw")
    assert "gsql" in str(ei.value) and "pg8000" in str(ei.value)
