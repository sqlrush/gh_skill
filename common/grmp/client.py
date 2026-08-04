"""中间件路径：走 GRMP 的两个 HTTP 接口访问数据库。

调用链与 agent 在客户环境必须做的事完全一致：
    查命令清单 → 按 cmd_name 匹配逻辑名 → 取 id → invoke

**不硬编码 id**：脚本 ID 是环境相关数据。硬编码的失败方式极其隐蔽 ——
换环境后 ID 依然存在，指向另一条脚本，执行成功、结果无关、不报错。

响应解析上有两条不能省的校验（规范说明 §9）：
  接口一判成功看 code，接口二判成功看 status 且 result 必须存在。
  只判 HTTP 200 会把业务错误当成成功；只取 result.data 会把执行失败
  读成「查询结果为空」。
"""
from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Any, Dict, List, Mapping, Optional

from .params import to_param_value

PATH_PREFIX = "/icbc/paas/aiops/grmp/diagnostic/agent"
LIST_PATH = PATH_PREFIX + "/common-operations"
INVOKE_PATH = LIST_PATH + "/invoke"

DEFAULT_TIMEOUT = 120
LIST_PAGE_SIZE = 1000  # 协议上限；脚本数不多，一次拉完


class GrmpError(Exception):
    """中间件返回的业务错误，或响应结构不符合协议。"""


class GrmpClient:
    """一个 GRMP 端点 + 一个 dataIp。命令清单在进程内缓存。"""

    def __init__(
        self,
        base_url: str,
        token: str,
        data_ip: str,
        timeout: int = DEFAULT_TIMEOUT,
    ):
        if not token:
            raise GrmpError("缺少 auth 令牌")
        self.base_url = base_url.rstrip("/")
        self.data_ip = data_ip
        self._token = token
        self._timeout = timeout
        self._ids: Optional[Dict[str, str]] = None

    # -- 传输 -------------------------------------------------------------

    def _post(self, path: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        request = urllib.request.Request(
            self.base_url + path,
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={
                # 头名是 auth，不是 Authorization，且无 Bearer 前缀
                "auth": self._token,
                "Content-Type": "application/json",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self._timeout) as resp:
                body = resp.read().decode("utf-8")
        except urllib.error.URLError as exc:
            raise GrmpError("请求 %s 失败：%s" % (path, exc)) from exc
        try:
            parsed = json.loads(body)
        except ValueError as exc:
            raise GrmpError("响应不是合法 JSON：%s" % body[:200]) from exc
        if not isinstance(parsed, dict):
            raise GrmpError("响应不是 JSON 对象：%s" % body[:200])
        return parsed

    # -- 接口一 -----------------------------------------------------------

    def list_operations(self) -> List[Dict[str, Any]]:
        """拉全量命令清单。翻页按原始分页走，不做客户端过滤后的计数。"""
        details: List[Dict[str, Any]] = []
        page = 1
        while True:
            body = self._post(
                LIST_PATH,
                {"dataIp": self.data_ip, "offset": page, "limit": LIST_PAGE_SIZE},
            )
            # 接口一判成功只能看 code —— 业务错误也是 HTTP 200
            if body.get("code") != "0":
                raise GrmpError(
                    "查询命令清单失败：%s" % body.get("msg", body)
                )
            result = body.get("result") or {}
            details.extend(result.get("list") or [])
            if not result.get("hasNextPage"):
                break
            page += 1
        return details

    def _id_map(self) -> Dict[str, str]:
        if self._ids is None:
            self._ids = {
                d.get("cmd_name"): d.get("id")
                for d in self.list_operations()
                if d.get("cmd_name")
            }
        return self._ids

    def resolve_id(self, script_name: str) -> str:
        ids = self._id_map()
        if script_name not in ids:
            raise GrmpError(
                "中间件未注册脚本 %s。已注册的有：%s"
                % (script_name, ", ".join(sorted(ids)) or "（无）")
            )
        return ids[script_name]

    def invalidate_cache(self) -> None:
        """脚本集随版本变化，需要时手动失效。"""
        self._ids = None

    # -- 接口二 -----------------------------------------------------------

    def invoke(
        self, script_name: str, values: Mapping[str, Any] = None
    ) -> List[Dict[str, str]]:
        payload: Dict[str, Any] = {
            "dataIp": self.data_ip,
            "id": self.resolve_id(script_name),
        }
        if values:
            payload["param"] = [
                {"param_name": key, "param_value": to_param_value(val)}
                for key, val in values.items()
            ]

        body = self._post(INVOKE_PATH, payload)

        # 两者缺一即按失败处理。只取 result.data 会把执行失败读成
        # 「查询结果为空」——「慢 SQL 返回 0 条」被读成「当前没有慢 SQL」。
        status = body.get("status")
        if status != "finished" or "result" not in body:
            raise GrmpError(
                "执行 %s 失败（status=%r，task_id=%s）：%s"
                % (script_name, status, body.get("task_id"), body.get("msg", ""))
            )

        result = body["result"] or {}
        result_type = str(result.get("type", "")).lower()
        if result_type != "array":
            raise GrmpError(
                "脚本 %s 未返回结果集（type=%r）。诊断脚本应当是查询语句。"
                % (script_name, result.get("type"))
            )
        return list(result.get("data") or [])


class GrmpRunner:
    """driver 为 grmp 时使用。对外形状与 DirectRunner 一致。

    **不提供持久会话**：接口二每次调用都是独立连接。实测 —— 第一次调用
    set work_mem='63MB'，第二次调用 show 读回的是默认值 16MB。
    """

    provides_session = False

    def __init__(self, client: GrmpClient):
        self._client = client

    @property
    def data_ip(self) -> str:
        return self._client.data_ip

    def run(
        self, script_name: str, values: Mapping[str, Any] = None
    ) -> List[Dict[str, str]]:
        return self._client.invoke(script_name, values)
