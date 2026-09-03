"""报错翻译(hints)与中间件客户端对 4xx 的处理。

钉住的纪律:HTTPError 先于 URLError 接住,响应体里的原因必须带出来(现场为「HTTP Error 400: 」后面
一片空白追了两天参数名);已知报错模式追加中文提示但不改原文;认不出的原样返回。
"""
import io
import json
import pathlib
import sys
import urllib.error

import pytest

_ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT))

from common.grmp import client as client_mod  # noqa: E402
from common.grmp import hints  # noqa: E402
from common.grmp.client import GrmpClient, GrmpError  # noqa: E402

STANDBY = ("SQL execution failed via JDBC on instance 1d95b59d: "
           "ERROR: Temporary or unlogged table cannot be accessed on the standby.")


def test_explain_matches_known_patterns_and_keeps_silent_otherwise():
    assert "备机" in hints.explain(STANDBY) and "statement_history" in hints.explain(STANDBY)
    assert "语句跟踪" in hints.explain("ERROR: enable_stmt_track is off")
    assert "权限" in hints.explain("ERROR: permission denied for relation statement_history")
    assert hints.explain("something completely unknown") == ""
    assert hints.explain("") == ""


def test_with_hint_appends_without_altering_original():
    out = hints.with_hint("请求 /x 失败：" + STANDBY)
    assert out.startswith("请求 /x 失败：" + STANDBY) and "\n提示:" in out and "主库 IP" in out
    assert hints.with_hint("plain") == "plain"


def _client():
    return GrmpClient(base_url="http://127.0.0.1:1", token="t0ken", data_ip="10.0.0.9")


def _http_error(code: int, payload):
    body = payload if isinstance(payload, bytes) else json.dumps(payload, ensure_ascii=False).encode("utf-8")
    return urllib.error.HTTPError("http://127.0.0.1:1/x", code, "Bad Request", None, io.BytesIO(body))


def test_post_reads_4xx_body_and_appends_hint(monkeypatch):
    def boom(request, timeout=None):
        raise _http_error(400, {"code": "1", "msg": STANDBY, "status": "failed"})
    monkeypatch.setattr(client_mod.urllib.request, "urlopen", boom)
    with pytest.raises(GrmpError) as ei:
        _client()._post("/x", {"dataIp": "10.0.0.9"})
    text = str(ei.value)
    assert "HTTP 400" in text and "cannot be accessed on the standby" in text      # 原文带出来
    assert "提示:" in text and "备机" in text                                       # 翻译追加在后


def test_post_4xx_with_non_json_body_still_surfaces_it(monkeypatch):
    def boom(request, timeout=None):
        raise _http_error(500, b"<html>Internal Server Error</html>")
    monkeypatch.setattr(client_mod.urllib.request, "urlopen", boom)
    with pytest.raises(GrmpError) as ei:
        _client()._post("/x", {})
    assert "HTTP 500" in str(ei.value) and "Internal Server Error" in str(ei.value)


def test_post_plain_urlerror_unchanged(monkeypatch):
    def boom(request, timeout=None):
        raise urllib.error.URLError("connection refused")
    monkeypatch.setattr(client_mod.urllib.request, "urlopen", boom)
    with pytest.raises(GrmpError) as ei:
        _client()._post("/x", {})
    assert "请求 /x 失败" in str(ei.value) and "connection refused" in str(ei.value)


def test_invoke_failure_message_gets_hint(monkeypatch):
    c = _client()
    monkeypatch.setattr(c, "resolve_id", lambda name: "id-1")
    monkeypatch.setattr(c, "_post", lambda path, payload: {"status": "failed", "task_id": "t", "msg": STANDBY})
    with pytest.raises(GrmpError) as ei:
        c.invoke("sqlfetch.from_history", {"sid": 300316117})
    assert "status='failed'" in str(ei.value) and "提示:" in str(ei.value) and "备机" in str(ei.value)
