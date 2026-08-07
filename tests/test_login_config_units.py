"""新配置格式、会话、内联密文的单测。

这一层的坏法几乎全是「连到了另一个库，而输出看起来完全正常」——
跨应用重名、会话文件键名写错、密文被挪到别的连接名下。所以测试重点在
「该拒的拒了」，而不是「能读出来」。
"""
import base64
import importlib
import os
import pathlib
import sys

import pytest
import yaml

_ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT))


@pytest.fixture()
def home(tmp_path, monkeypatch):
    """把 GSDB_HOME 指到临时目录，并重载配置模块让它重新读环境变量。"""
    monkeypatch.setenv("GSDB_HOME", str(tmp_path))
    monkeypatch.delenv("GSDB_PASSWORD", raising=False)
    monkeypatch.delenv("GDAA_PASSWORD", raising=False)
    from common import config, credential, session
    importlib.reload(config)
    importlib.reload(credential)
    importlib.reload(session)
    return tmp_path


def write_config(home, payload):
    (home / "config.yaml").write_text(
        yaml.safe_dump(payload, allow_unicode=True, sort_keys=False),
        encoding="utf-8")


_CONN = {"type": "opengauss", "host": "127.0.0.1", "port": 5432,
         "database": "db", "user": "gaussdb"}


# --- 模式 --------------------------------------------------------------------

def test_mode_defaults_to_gsql(home):
    """老配置没有 connection_mode，而它们全是直连。

    默认成 api 会让老配置在第一次取数时才失败，且错误表现成「中间件连不上」。
    """
    from common import config
    write_config(home, {"connections": [dict(_CONN, name="legacy")]})
    assert config.mode() == "gsql"


def test_unknown_mode_refuses(home):
    from common import config
    write_config(home, {"connection_mode": "jdbc"})
    with pytest.raises(config.ConfigError) as ei:
        config.mode()
    assert "jdbc" in str(ei.value)


# --- 应用分组 ----------------------------------------------------------------

def test_db_connections_are_grouped_by_app(home):
    from common import config
    write_config(home, {"connection_mode": "gsql", "db_connections": {
        "app1": [dict(_CONN, name="a1c1")],
        "app2": [dict(_CONN, name="a2c1")]}})
    by_app = {c.name: c.app for c in config.load()}
    assert by_app == {"a1c1": "app1", "a2c1": "app2"}


def test_qualified_name_includes_app(home):
    from common import config
    write_config(home, {"db_connections": {"app1": [dict(_CONN, name="c1")]}})
    assert config.find("c1").qualified == "app1/c1"


def test_find_accepts_app_qualified_name(home):
    from common import config
    write_config(home, {"db_connections": {"app1": [dict(_CONN, name="c1")]}})
    assert config.find("app1/c1").name == "c1"


def test_duplicate_name_across_apps_refuses(home):
    """**最重要的一条。**

    `-c conn1` 无法判断该用哪个应用的库。取第一个会连到另一个应用上，
    执行成功、结果无关、不报错 —— 正是本项目一路在防的那类失败。
    """
    from common import config
    write_config(home, {"db_connections": {
        "app1": [dict(_CONN, name="conn1")],
        "app2": [dict(_CONN, name="conn1", port=5433)]}})
    with pytest.raises(config.ConfigError) as ei:
        config.load()
    assert "重复" in str(ei.value)
    assert "app1" in str(ei.value) and "app2" in str(ei.value)


def test_old_flat_format_still_loads(home):
    """老配置不动也要能跑 —— 否则升级会让所有现存部署当场失效。"""
    from common import config
    write_config(home, {"connections": [dict(_CONN, name="legacy")]})
    conns = config.load()
    assert [c.name for c in conns] == ["legacy"]
    assert conns[0].app == ""


def test_both_formats_merge(home):
    from common import config
    write_config(home, {
        "connections": [dict(_CONN, name="legacy")],
        "db_connections": {"app1": [dict(_CONN, name="new")]}})
    assert {c.name for c in config.load()} == {"legacy", "new"}


# --- api 端点 ----------------------------------------------------------------

def test_api_endpoint_parsed(home):
    from common import config
    write_config(home, {"connection_mode": "api", "api_connection": [
        {"host": "ucmp-grmp-app-d.sdc.cs.icbc", "port": 8080, "token": "t0"}]})
    ep = config.api_endpoint()
    assert ep.host == "ucmp-grmp-app-d.sdc.cs.icbc" and ep.port == 8080


def test_env_token_wins_over_inline(home, monkeypatch):
    """内联令牌会进版本库、进备份、被随手 cat；环境变量少一份落盘副本。"""
    from common import config
    write_config(home, {"connection_mode": "api", "api_connection": [
        {"host": "h", "port": 8080, "token": "inline"}]})
    monkeypatch.setenv("GRMP_AUTH_TOKEN", "from-env")
    assert config.api_endpoint().resolve_token() == "from-env"


def test_api_mode_without_endpoint_refuses(home):
    from common import config
    write_config(home, {"connection_mode": "api"})
    with pytest.raises(config.ConfigError) as ei:
        config.api_endpoint()
    assert "api_connection" in str(ei.value)


def test_multiple_endpoints_refuse(home):
    """两个端点时无从判断用哪个 —— 猜一个就是往错误的环境发请求。"""
    from common import config
    write_config(home, {"connection_mode": "api", "api_connection": [
        {"host": "a", "port": 8080}, {"host": "b", "port": 8080}]})
    with pytest.raises(config.ConfigError):
        config.api_endpoint()


# --- 会话 --------------------------------------------------------------------

def test_session_roundtrip(home):
    from common import config, session
    write_config(home, {"db_connections": {"app1": [dict(_CONN, name="c1")]}})
    conn = config.find("c1")
    session.save(conn)
    live = session.current()
    assert live.name == "c1" and live.app == "app1"


def test_resolve_prefers_explicit_name(home):
    from common import config, session
    write_config(home, {"db_connections": {
        "app1": [dict(_CONN, name="c1"), dict(_CONN, name="c2", port=5433)]}})
    session.save(config.find("c1"))
    assert config.resolve("c2").name == "c2"


def test_resolve_falls_back_to_session(home):
    from common import config, session
    write_config(home, {"db_connections": {"app1": [dict(_CONN, name="c1")]}})
    session.save(config.find("c1"))
    assert config.resolve().name == "c1"


def test_resolve_without_session_says_what_to_do(home):
    from common import config
    write_config(home, {"db_connections": {"app1": [dict(_CONN, name="c1")]}})
    with pytest.raises(config.ConfigError) as ei:
        config.resolve()
    assert "gaussdb-login" in str(ei.value)


def test_session_connection_not_in_config_still_resolves(home):
    """api 模式的连接是登录时现场造的，配置文件里根本没有它。

    不回落到会话的话，下一个 skill 会报「没有这个连接」。
    """
    from common import config, session
    write_config(home, {"connection_mode": "api", "api_connection": [
        {"host": "h", "port": 8769}]})
    live = config.Connection(name="10-0-0-9", type="gaussdb", host="h",
                             port=8769, database="10.0.0.9", user="grmp",
                             driver="grmp", data_ip="10.0.0.9", app="api")
    session.save(live)
    assert config.resolve("10-0-0-9").data_ip == "10.0.0.9"
    assert config.resolve().data_ip == "10.0.0.9"


def test_session_with_unknown_key_refuses(home):
    """手工改会话文件时写错键名（data_ip 写成 dataip），静默忽略会连错实例。"""
    from common import config, session
    (home / "session.yaml").write_text(
        yaml.safe_dump({"name": "c1", "type": "opengauss", "host": "h",
                        "port": 5432, "database": "db", "user": "u",
                        "dataip": "10.0.0.9"}), encoding="utf-8")
    with pytest.raises(config.ConfigError) as ei:
        session.current()
    assert "dataip" in str(ei.value)


def test_clear_session(home):
    from common import config, session
    write_config(home, {"db_connections": {"app1": [dict(_CONN, name="c1")]}})
    session.save(config.find("c1"))
    assert session.clear() is True
    assert session.current() is None
    assert session.clear() is False


# --- 内联密文 ----------------------------------------------------------------

def test_inline_encrypted_password_roundtrip(home):
    from common import config, credential
    blob = credential.seal_secret("c1", "s3cret")
    write_config(home, {"db_connections": {"app1": [
        dict(_CONN, name="c1", password=blob, encrypted=True)]}})
    assert credential.secret_for(config.find("c1")) == "s3cret"


def test_ciphertext_cannot_be_moved_to_another_connection(home):
    """AAD 绑定连接名 —— 密文泄露了也不能挪到别的连接上复用。"""
    from common import config, credential
    blob = credential.seal_secret("c1", "s3cret")
    write_config(home, {"db_connections": {"app1": [
        dict(_CONN, name="c2", password=blob, encrypted=True)]}})
    with pytest.raises(credential.CredentialError) as ei:
        credential.secret_for(config.find("c2"))
    assert "AAD" in str(ei.value) or "解开" in str(ei.value)


def test_plaintext_password_when_not_encrypted(home):
    from common import config, credential
    write_config(home, {"db_connections": {"app1": [
        dict(_CONN, name="c1", password="plain", encrypted=False)]}})
    assert credential.secret_for(config.find("c1")) == "plain"


def test_bad_base64_says_what_to_fix(home):
    from common import config, credential
    write_config(home, {"db_connections": {"app1": [
        dict(_CONN, name="c1", password="不是base64", encrypted=True)]}})
    with pytest.raises(credential.CredentialError) as ei:
        credential.secret_for(config.find("c1"))
    assert "encrypted" in str(ei.value)


def test_env_password_overrides_everything(home, monkeypatch):
    from common import config, credential
    write_config(home, {"db_connections": {"app1": [
        dict(_CONN, name="c1", password="plain", encrypted=False)]}})
    monkeypatch.setenv("GSDB_PASSWORD", "from-env")
    assert credential.secret_for(config.find("c1")) == "from-env"


# --- 报告里必须写明「针对哪个库」 --------------------------------------------

def test_resolved_name_falls_back_to_the_session(home):
    """省略 -c 时报告不能记成空串 —— 而「省略 -c」正是推荐用法。

    一份不写明针对哪个库的健康报告，事后没法分辨它是生产还是测试的；
    两份放在一起更是完全一样。
    """
    from common import config, session
    write_config(home, {"db_connections": {"app1": [dict(_CONN, name="c1")]}})
    session.save(config.find("c1"))
    assert config.resolved_name() == "app1/c1"
    assert config.resolved_name("") == "app1/c1"


def test_resolved_name_is_app_qualified(home):
    """只记 `og` 在多应用环境里仍然是二义的。"""
    from common import config
    write_config(home, {"db_connections": {
        "app1": [dict(_CONN, name="c1")],
        "app2": [dict(_CONN, name="c2")]}})
    assert config.resolved_name("c2") == "app2/c2"


def test_resolved_name_does_not_raise_when_unresolvable(home):
    """它只负责「报告怎么写」—— 连接取不到该由真正取数时报错，不该它抢先抛。"""
    from common import config
    write_config(home, {})
    assert config.resolved_name("nope") == "nope"
    assert config.resolved_name() == ""
