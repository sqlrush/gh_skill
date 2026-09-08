"""EXPLAIN 前把 search_path 切到 SQL 原本的 schema。

现场 sqltune 报 HTTP 400 的成因(客户 2026-09-07 反馈「入参 sql 没带 schema」):业务 SQL 的表名多半不带 schema,
靠应用账号的 search_path 解析;EXPLAIN 是用中间件执行账号跑的,它的 search_path 是 "$user", public,
解析不到 → relation does not exist → 中间件包成 400。而 statement_history 里本来就记着 schema_name。

两条路:
  · 中间件(白名单模板):注册一条「SET search_path TO "<schema>", public; EXPLAIN …」的两语句模板
    (<skill>.plan_text_schema 等)。中间件能不能跑一条脚本里的两条语句不确定——所以先用一条探测脚本
    explain.multi_stmt_probe 验一次(每个 runner 只验一次),不行就退回单语句模板并把原因写进说明,
    不让它变成新的 400。
  · 直连原始会话:直接在会话上 SET search_path,后面的 EXPLAIN 与 hypopg 都受益。

schema 名同时进 SET 语句的引号里:不是合法标识符一律不用——中间件是文本替换,这里就是注入面。
"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Tuple

IDENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_$]{0,62}$")
PROBE_SCRIPT = "explain.multi_stmt_probe"
SCHEMA_SUFFIX = "_schema"

_probe_cache: Dict[int, Tuple[bool, str]] = {}


def valid_schema(name: Any) -> bool:
    return isinstance(name, str) and bool(IDENT_RE.match(name))


def reset_probe_cache() -> None:
    _probe_cache.clear()


def multi_statement_ok(runner) -> Tuple[bool, str]:
    """中间件(或直连 runner)能不能跑一条脚本里的两条语句。每个 runner 只探一次。"""
    key = id(runner)
    if key in _probe_cache:
        return _probe_cache[key]
    try:
        rows = runner.run(PROBE_SCRIPT, {})
    except Exception as exc:                     # noqa: BLE001 —— 探测失败只是「不支持」,原因带回
        result = (False, str(exc).splitlines()[0][:160])
    else:
        first = ""
        for row in rows or []:
            if isinstance(row, dict) and row:
                first = str(next(iter(row.values()), "")).strip()
                break
        result = (True, "") if first == "1" else (False, "探测脚本没有返回 1(返回 %r)" % first)
    _probe_cache[key] = result
    return result


def _join(rows: List[Dict[str, Any]]) -> str:
    return "\n".join(str(next(iter(r.values()), "")) for r in (rows or []) if isinstance(r, dict))


def explain_plan(runner, base_script: str, sql: str, schema: str) -> Tuple[str, str, str]:
    """走注册模板取计划。返回 (计划文本, 实际切到的 schema, 说明)。

    schema 为空 → 单语句模板;有 schema 且中间件能跑两条语句 → base_script + "_schema";
    不能 → 退回单语句模板,说明里写清没切换的原因。
    """
    if not schema:
        return _join(runner.run(base_script, {"sql": sql})), "", ""
    if not valid_schema(schema):
        return (_join(runner.run(base_script, {"sql": sql})), "",
                "schema 名 %r 不是合法标识符,未切换 search_path;表名不带 schema 时 EXPLAIN 可能报对象不存在" % (schema,))
    ok, reason = multi_statement_ok(runner)
    if ok:
        rows = runner.run(base_script + SCHEMA_SUFFIX, {"sql": sql, "schema": schema})
        return _join(rows), schema, ""
    note = ("中间件不支持一条脚本跑两条语句(%s),未能把 search_path 切到 %s;"
            "表名不带 schema 时 EXPLAIN 可能报对象不存在——可让 DBA 给执行账号设置 search_path,"
            "或把 SQL 里的表名写全 schema.表" % (reason or "探测脚本 %s 未通过" % PROBE_SCRIPT, schema))
    return _join(runner.run(base_script, {"sql": sql})), "", note


def set_search_path(db, schema: str) -> str:
    """直连原始会话:SET search_path TO "<schema>", public。返回实际切到的 schema(空串 = 没切)。"""
    if not schema:
        return ""
    if not valid_schema(schema):
        raise ValueError("schema 名 %r 不是合法标识符,拒绝拼进 SET search_path" % (schema,))
    db.execute('SET search_path TO "%s", public' % schema)
    return schema


__all__ = ["IDENT_RE", "PROBE_SCRIPT", "SCHEMA_SUFFIX", "valid_schema", "reset_probe_cache",
           "multi_statement_ok", "explain_plan", "set_search_path"]
