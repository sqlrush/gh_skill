import sys, pathlib
_ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT))

import pytest  # noqa: E402
from common.config import Connection, validate, ConfigError, load, state_dir  # noqa: E402

def _conn(**kw):
    base = dict(name="a", type="opengauss", host="h", port=5432, database="d", user="u")
    base.update(kw)
    return Connection(**base)

def test_driver_defaults_to_gsql():
    assert _conn().driver == "gsql"

def test_validate_accepts_pg8000():
    validate(_conn(driver="pg8000"))  # 不抛即通过

def test_validate_rejects_unknown_driver():
    with pytest.raises(ConfigError):
        validate(_conn(driver="mysqlcli"))

def test_load_fills_default_driver(tmp_path, monkeypatch):
    cfg = tmp_path / "config.yaml"
    cfg.write_text(
        "connections:\n"
        "  - name: a\n    type: opengauss\n    host: h\n"
        "    port: 5432\n    database: d\n    user: u\n"
    )
    monkeypatch.setenv("GDAA_HOME", str(tmp_path))
    conns = load()
    assert conns[0].driver == "gsql"

def test_load_reads_explicit_driver(tmp_path, monkeypatch):
    cfg = tmp_path / "config.yaml"
    cfg.write_text(
        "connections:\n"
        "  - name: a\n    type: opengauss\n    host: h\n"
        "    port: 5432\n    database: d\n    user: u\n    driver: pg8000\n"
    )
    monkeypatch.setenv("GDAA_HOME", str(tmp_path))
    conns = load()
    assert conns[0].driver == "pg8000"


def test_state_dir_honors_gsdb_home(tmp_path, monkeypatch):
    monkeypatch.delenv("GDAA_HOME", raising=False)
    monkeypatch.setenv("GSDB_HOME", str(tmp_path / "x"))
    assert state_dir() == tmp_path / "x"

def test_state_dir_gsdb_home_takes_precedence(tmp_path, monkeypatch):
    monkeypatch.setenv("GDAA_HOME", str(tmp_path / "legacy"))
    monkeypatch.setenv("GSDB_HOME", str(tmp_path / "new"))
    assert state_dir() == tmp_path / "new"

def test_state_dir_falls_back_to_legacy_gdaa_home(tmp_path, monkeypatch):
    monkeypatch.delenv("GSDB_HOME", raising=False)
    monkeypatch.setenv("GDAA_HOME", str(tmp_path / "legacy"))
    assert state_dir() == tmp_path / "legacy"

def test_state_dir_defaults_to_dot_gdaa(monkeypatch):
    monkeypatch.delenv("GSDB_HOME", raising=False)
    monkeypatch.delenv("GDAA_HOME", raising=False)
    assert state_dir() == pathlib.Path.home() / ".gdaa"
