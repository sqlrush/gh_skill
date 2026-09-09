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
    # 切不成(客户 09-09 早截图:执行器跑不了两条语句):先把 SQL 里不带 schema 的表名补全再走单语句模板——
    # 不依赖执行器、不依赖 DBA;补全后解析到同一张表,计划与原 SQL 等价。没有可补的才原样退回。
    from common import sqlqualify           # 延迟导入:sqlqualify 反过来引用本模块的 valid_schema
    qualified, changed = sqlqualify.qualify_tables(sql, schema)
    note = qualified_note(schema, changed, reason) if changed else fallback_note(schema, reason)
    try:
        rows = runner.run(base_script, {"sql": qualified if changed else sql})
    except Exception as exc:               # noqa: BLE001
        # 退回单语句模板后 EXPLAIN 又失败:这时没有报告可以放说明,把原因和 DBA 命令直接跟在报错后面——
        # 用户看到的就是这一段。
        try:
            wrapped = type(exc)("%s\n补充:%s" % (exc, note))
        except Exception:                  # noqa: BLE001 —— 异常类构造签名特殊时保留原报错
            raise exc
        raise wrapped from exc
    return _join(rows), "", note


_MISSING_SCRIPT_MARKS = ("不存在", "未注册", "not found", "no such", "unknown", "不在白名单", "does not exist")


def _cause(reason: str) -> str:
    """没切成的原因分两种说:探测脚本没灌白名单(发布问题,补灌即可)/ 执行器跑不了两条语句(限制)。"""
    low = (reason or "").lower()
    if any(m.lower() in low for m in _MISSING_SCRIPT_MARKS):
        return ("探测脚本 %s 未注册到白名单或调用失败(%s)——请先按交付文档 08 把本次新增的白名单脚本"
                "(含 *_schema 模板与探测脚本)灌入 script_config" % (PROBE_SCRIPT, reason))
    return "中间件不支持一条脚本跑两条语句(%s)" % (reason or "探测脚本 %s 未通过" % PROBE_SCRIPT)


def qualified_note(schema: str, changed, reason: str) -> str:
    """切不成 search_path、改为补全表名后取计划时的说明:说清没切的原因、补了哪些名字、计划等价,根治仍是 DBA 那条命令。"""
    names = "、".join("%s.%s" % (schema, n) for n in changed)
    return (
        "search_path 未切换到 %s:%s。已把 SQL 里不带 schema 的表名按该 schema 补全后取计划(%s),"
        "解析到同一张表,计划与原 SQL 等价。根治办法:由 DBA 给中间件执行账号在该库上设置 search_path,执行 "
        "ALTER ROLE <中间件执行账号> IN DATABASE <业务库名> SET search_path = %s, public; (新连接生效,不用重启)。"
        % (schema, _cause(reason), names, schema)
    )


def fallback_note(schema: str, reason: str) -> str:
    """没切成 search_path、SQL 里又没有可补全的表名时给用户看的话:先说清是哪种原因,再给可照做的处理办法。

    两种情况下 DBA 那条命令都能用,所以都给;备选是把 SQL 里的表名写全。
    """
    cause = _cause(reason)
    return (
        "未能把 search_path 切到 %s:%s。表名不带 schema 时 EXPLAIN 会报对象不存在。处理办法二选一:"
        "① 由 DBA 给中间件执行账号在该库上设置 search_path,执行 "
        "ALTER ROLE <中间件执行账号> IN DATABASE <业务库名> SET search_path = %s, public; (新连接生效,不用重启);"
        "② 把 SQL 里的表名写全为 %s.<表> 后重跑。" % (schema, cause, schema, schema)
    )


def set_search_path(db, schema: str) -> str:
    """直连原始会话:SET search_path TO "<schema>", public。返回实际切到的 schema(空串 = 没切)。"""
    if not schema:
        return ""
    if not valid_schema(schema):
        raise ValueError("schema 名 %r 不是合法标识符,拒绝拼进 SET search_path" % (schema,))
    db.execute('SET search_path TO "%s", public' % schema)
    return schema


__all__ = ["IDENT_RE", "PROBE_SCRIPT", "SCHEMA_SUFFIX", "valid_schema", "reset_probe_cache",
           "multi_statement_ok", "explain_plan", "set_search_path", "fallback_note", "qualified_note"]
