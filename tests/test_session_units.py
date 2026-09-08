"""common.session —— 会话按句柄分文件;多会话又没给句柄时拒绝,不猜。

现场缺陷(2026-09-07 客户反馈):多个用户共用一个沙箱、一个 GSDB_HOME、一个 session.yaml,
谁最后登录所有人就连谁的库,退出码 0、不报错、报告抬头写着别人的库——静默串库。
修法是把「一个沙箱一张便签」改成「一次登录一个句柄一张便签」。这里钉住的行为:

  · 每次 save 得到一个新句柄,写 sessions/<句柄>.yaml(0600),互不覆盖;
  · 只有一个会话时不用句柄(单用户沙箱行为不变);
  · 多个会话又没句柄 → ConfigError,把候选列全(句柄 / ip / 库),让模型去问用户;
  · 句柄来源:use() > 环境变量 GSDB_SESSION;形状不合法(路径穿越)一律拒;
  · 过期会话不算数且顺手删掉;读一次就更新最后使用时间;
  · 旧的单文件 session.yaml 在没有任何句柄会话时仍可用(升级过渡)。
"""
import os
import pathlib
import sys
import time

import pytest
import yaml

_ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT))

from common import session  # noqa: E402
from common.config import ConfigError, Connection  # noqa: E402


@pytest.fixture()
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("GSDB_HOME", str(tmp_path))
    monkeypatch.delenv(session.ENV_HANDLE, raising=False)
    session.use(None)
    yield tmp_path
    os.environ.pop(session.ENV_HANDLE, None)
    session.use(None)


def _conn(ip: str, db: str) -> Connection:
    return Connection(name="%s-%s" % (ip.replace(".", "-"), db), type="gaussdb", host="127.0.0.1",
                      port=8769, database=db, user="grmp", driver="grmp", data_ip=ip, app="api")


def test_each_login_gets_its_own_file(home):
    h1 = session.save(_conn("10.0.0.9", "core"))
    h2 = session.save(_conn("10.0.0.20", "report"))
    assert h1 != h2 and session.HANDLE_RE.match(h1) and session.HANDLE_RE.match(h2)
    files = sorted(p.name for p in (home / "sessions").glob("*.yaml"))
    assert files == sorted([h1 + ".yaml", h2 + ".yaml"])
    assert oct(session.path_for(h1).stat().st_mode & 0o777) == "0o600"


def test_single_session_is_used_without_a_handle(home):
    session.save(_conn("10.0.0.9", "core"))
    assert session.current().database == "core"


def test_two_sessions_without_a_handle_refuse_and_list_both(home):
    """安全底线:今天是猜最后一个,猜错没人知道;改后是拒绝执行、把候选摆出来。"""
    h1 = session.save(_conn("10.0.0.9", "core"))
    h2 = session.save(_conn("10.0.0.20", "report"))
    with pytest.raises(ConfigError) as ei:
        session.current()
    msg = str(ei.value)
    assert h1 in msg and h2 in msg
    assert "10.0.0.9" in msg and "core" in msg and "10.0.0.20" in msg and "report" in msg
    assert "--session" in msg


def test_handle_selects_the_right_session(home):
    h1 = session.save(_conn("10.0.0.9", "core"))
    h2 = session.save(_conn("10.0.0.20", "report"))
    session.use(h1)
    assert session.current().database == "core" and session.current_handle() == h1
    session.use(h2)
    assert session.current().database == "report"


def test_env_var_carries_the_handle(home, monkeypatch):
    """宿主将来能按用户注入环境变量时零改动接上;health 派生的子 skill 也靠它继承。"""
    session.save(_conn("10.0.0.9", "core"))
    h2 = session.save(_conn("10.0.0.20", "report"))
    monkeypatch.setenv(session.ENV_HANDLE, h2)
    assert session.current().database == "report"


def test_malformed_or_unknown_handle_is_refused(home):
    session.save(_conn("10.0.0.9", "core"))
    for bad in ("../etc", "a/b", "A B", "x" * 40, "ab"):     # 空串是「不选」,不在此列
        with pytest.raises(ConfigError):
            session.use(bad)
    session.use("zzzz9")                       # 形状合法但不存在
    with pytest.raises(ConfigError) as ei:
        session.current()
    assert "zzzz9" in str(ei.value) and "gaussdb-login" in str(ei.value)


def test_expired_sessions_are_ignored_and_removed(home):
    """不清的话,用户走了以后沙箱里残留一堆会话,后来的人每条命令都被拦。"""
    h_old = session.save(_conn("10.0.0.9", "core"))
    stale = time.time() - (session.ttl_seconds() + 3600)
    os.utime(session.path_for(h_old), (stale, stale))
    session.save(_conn("10.0.0.20", "report"))
    assert session.current().database == "report"
    assert not session.path_for(h_old).exists()


def test_reading_touches_last_used(home):
    h = session.save(_conn("10.0.0.9", "core"))
    old = time.time() - 3600
    os.utime(session.path_for(h), (old, old))
    session.current()
    assert session.path_for(h).stat().st_mtime > old + 1800


def test_legacy_single_file_still_works_until_a_handle_session_exists(home):
    legacy = _conn("10.0.0.9", "legacy")
    (home / "session.yaml").write_text(yaml.safe_dump({
        "name": legacy.name, "type": legacy.type, "host": legacy.host, "port": legacy.port,
        "database": legacy.database, "user": legacy.user, "sslmode": "", "driver": "grmp",
        "data_ip": legacy.data_ip, "app": "api"}), encoding="utf-8")
    assert session.current().database == "legacy"
    session.save(_conn("10.0.0.20", "core"))
    assert session.current().database == "core"       # 有了句柄会话,旧文件不再参与


def test_list_and_clear_by_handle(home):
    h1 = session.save(_conn("10.0.0.9", "core"))
    h2 = session.save(_conn("10.0.0.20", "report"))
    listed = session.list_sessions()
    assert {s.handle for s in listed} == {h1, h2}
    assert {s.conn.database for s in listed} == {"core", "report"}
    assert session.clear(h1) is True
    assert [s.handle for s in session.list_sessions()] == [h2]
    assert session.clear("nope1") is False
    assert session.clear(h2) is True and session.list_sessions() == []


def test_clear_without_handle_refuses_when_ambiguous(home):
    h1 = session.save(_conn("10.0.0.9", "core"))
    session.save(_conn("10.0.0.20", "report"))
    with pytest.raises(ConfigError):
        session.clear()
    session.clear(h1)
    assert session.clear() is True                     # 只剩一个时不用句柄


def test_unknown_keys_in_a_session_file_are_still_refused(home):
    h = session.save(_conn("10.0.0.9", "core"))
    path = session.path_for(h)
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    raw["dataip"] = raw.pop("data_ip")
    path.write_text(yaml.safe_dump(raw), encoding="utf-8")
    with pytest.raises(ConfigError) as ei:
        session.current()
    assert "dataip" in str(ei.value)
