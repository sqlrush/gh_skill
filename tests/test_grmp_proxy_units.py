"""经凭据代理访问 GRMP(common/grmp/proxy.py,2026-10-23)。

容器部署下令牌与 SM2 私钥不再放进模型所在的容器:技能脚本经 Unix 套接字把报文交给代理,代理加凭据、签名、工号。
这里核对客户端这一侧:走代理时请求里一样凭据都不带、工号也不由它加(由代理按绑定的人强制写入);
代理报错 / 不在时说清楚;access 在没有令牌的情况下也能走通。
"""
import http.server
import json
import os
import pathlib
import socketserver
import sys
import tempfile
import threading

_ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT))

import pytest  # noqa: E402

from common import access  # noqa: E402
from common.config import Connection  # noqa: E402
from common.grmp import client as gc  # noqa: E402
from common.grmp import proxy  # noqa: E402


class _Server(socketserver.ThreadingMixIn, socketserver.UnixStreamServer):
    daemon_threads = True


@pytest.fixture()
def fake_proxy():
    """Unix 套接字上的假代理:记下收到的请求,按路径回固定的应答。路径放 /tmp:macOS 套接字路径上限 104 字节。"""
    d = tempfile.mkdtemp(dir="/tmp", prefix="gp")
    path = os.path.join(d, "grmp.sock")
    seen = []
    replies = {}

    class H(http.server.BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def address_string(self):
            return "unix"

        def do_POST(self):
            n = int(self.headers.get("Content-Length") or 0)
            seen.append((self.path, {k.lower(): v for k, v in self.headers.items()}, json.loads(self.rfile.read(n))))
            code, body = replies.get(self.path, (200, {"code": "0", "result": {"list": [], "hasNextPage": False}}))
            data = json.dumps(body).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

    srv = _Server(path, H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield path, seen, replies
    srv.shutdown()
    srv.server_close()


def test_requests_through_the_proxy_carry_no_credentials_and_no_user_id(fake_proxy, monkeypatch):
    path, seen, _r = fake_proxy
    monkeypatch.setenv("GRMP_USER_ID_HEADER", "X-User-Id")         # 工号由代理强制写入,客户端这边不加
    monkeypatch.setenv("GRMP_USER_ID_PARAM", "userId")
    monkeypatch.setenv("GSDB_USER_ID", "u1234")
    c = gc.GrmpClient(base_url="http://credproxy", token="", data_ip="10.0.0.9", proxy_socket=path)
    assert c.list_operations() == []
    url, headers, payload = seen[0]
    assert url == gc.LIST_PATH and payload == {"dataIp": "10.0.0.9", "offset": 1, "limit": gc.LIST_PAGE_SIZE}
    for h in ("auth", "appkey", "timestamp", "signature", "x-user-id"):
        assert h not in headers, h


def test_proxy_errors_come_back_with_the_reason(fake_proxy):
    path, _seen, replies = fake_proxy
    replies[gc.INVOKE_PATH] = (403, {"msg": "槽位不在用"})
    replies[gc.LIST_PATH] = (200, {"code": "0", "result": {"list": [{"cmdName": "a.b", "id": "7"}],
                                                          "hasNextPage": False}})
    c = gc.GrmpClient(base_url="http://credproxy", token="", data_ip="10.0.0.9", proxy_socket=path)
    with pytest.raises(gc.GrmpError) as exc:
        c._post(gc.INVOKE_PATH, {"id": "7"})
    assert "403" in str(exc.value) and "槽位不在用" in str(exc.value) and "凭据代理" in str(exc.value)


def test_a_missing_proxy_says_so():
    c = gc.GrmpClient(base_url="http://credproxy", token="", data_ip="10.0.0.9", proxy_socket="/tmp/nope-%d.sock" % os.getpid())
    with pytest.raises(gc.GrmpError) as exc:
        c.list_operations()
    assert "凭据代理不可用" in str(exc.value)


def test_without_a_proxy_a_token_is_still_required():
    with pytest.raises(gc.GrmpError):
        gc.GrmpClient(base_url="http://x", token="", data_ip="10.0.0.9")


def test_access_goes_through_the_proxy_without_any_token(fake_proxy, monkeypatch):
    path, _seen, _r = fake_proxy
    monkeypatch.delenv("GRMP_AUTH_TOKEN", raising=False)
    monkeypatch.delenv("GRMP_SM2_PRIVATE_KEY", raising=False)
    monkeypatch.setenv(proxy.ENV_SOCKET, path)
    conn = Connection(name="og", type="opengauss", host="", port=8080, database="d", user="u", driver="grmp",
                      data_ip="10.0.0.9")
    runner = access.runner_for(conn)
    assert runner.client._proxy == path and runner.client._signer is None


def test_socket_path_reads_the_env():
    assert proxy.socket_path({proxy.ENV_SOCKET: " /run/credproxy/grmp.sock "}) == "/run/credproxy/grmp.sock"
    assert proxy.socket_path({}) == ""
