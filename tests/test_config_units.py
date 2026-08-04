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
    # GSDB_HOME 优先级高于 GDAA_HOME，不清掉就会读到真实的 ~/.gdaa/config.yaml，
    # 断言变成对本机配置的偶然依赖（本机按文档就是带 GSDB_HOME 运行的）
    monkeypatch.delenv("GSDB_HOME", raising=False)
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
    monkeypatch.delenv("GSDB_HOME", raising=False)
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

def test_state_dir_defaults_to_the_container_path(monkeypatch):
    """两个环境变量都没有时，落到客户容器里的绝对路径。

    这不是笔误：客户改造版把默认值从 ~/.gdaa 改成了容器内的固定路径，
    OpenCode 容器里 /workspace 一定存在。而测试原本还断言 ~/.gdaa，
    所以它自客户版导入起就一直是红的。

    **在容器外这是个坑**：开发机上 /workspace 不存在、也建不出来（根目录
    无写权限），不设 GSDB_HOME 直接跑任何 skill 都会在写状态时失败。
    本机开发/测试请显式 export GSDB_HOME=$HOME/.gdaa。
    """
    monkeypatch.delenv("GSDB_HOME", raising=False)
    monkeypatch.delenv("GDAA_HOME", raising=False)
    assert state_dir() == pathlib.Path("/workspace/.opencode/skills/common")
