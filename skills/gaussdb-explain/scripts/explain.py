#!/usr/bin/env python3
"""explain — EXPLAIN a statement with deterministic risk findings.

Port of internal/probe/explain.go + internal/analyze/risks.go + cli/explain.go.

Usage:
    explain.py -c <conn> --sql-stdin [--analyze] [--format json] <<'SQL'
    SELECT ...
    SQL
"""
from __future__ import annotations

import argparse
import json
import pathlib
import sys
from dataclasses import dataclass
from typing import Optional

_HERE = pathlib.Path(__file__).resolve()
sys.path.insert(0, str(_HERE.parent))  # sibling modules
for _anc in _HERE.parents:  # locate common/ (repo root or install dir)
    if (_anc / "common" / "__init__.py").exists():
        sys.path.insert(0, str(_anc))
        break

import common  # noqa: E402
from common import access  # noqa: E402
from common import kernel_funcs as kf  # noqa: E402
from common import explain_actual as ea  # noqa: E402
from common.grmp import statement as stmt  # noqa: E402
from common.grmp.statement import (  # noqa: E402
    ExplainNotAllowed,
    ensure_explainable,
)
import render  # noqa: E402

# --pid 路径依赖的白名单脚本(走中间件必须先注册;交付闸按脚本名全串在 skills/ 里检索,故写全):
#   explain.kernel_funcs   探测 version() 与 gs_get_explain / gs_get_kernel_info 是否存在
#   explain.runtime_plan   gs_get_explain({{pid}}::bigint) —— GaussDB 私有的运行态计划(505.2.1 实测签名)
#   explain.runtime_plan_int4  gs_get_explain({{pid}}::integer) —— 文档签名 (integer) 的老内核用
#   explain.session_by_pid 按 pid 取该会话当前语句,内核没有 gs_get_explain 时退回 EXPLAIN 用
#
# 2026-09 现场四类报错之一就是脚本直接调 gs_get_explain 报 does not exist:openGauss 从来没有它,
# GaussDB 缺失时也可能是 catalog 未升级 / 逐库不一致 / 实参类型不符。所以先探测再用,
# 两条路径同一份报告形状,只在「来源」一行注明拿到的是运行态计划还是估算计划。


class PidNotFound(Exception):
    """--pid 指定的会话不存在,或它此刻没有在执行任何语句。"""


class ShapeRejected(Exception):
    """从会话里取到的 SQL 过不了形态白名单(DML / 多语句 / 维护语句)。"""

# 本 skill 唯一的查询点就是「对用户给的任意 SQL 做 EXPLAIN」。
# 它**没有可迁到 scripts/registry/ 的部分**：白名单模型按逻辑脚本名放行
# 预注册的 SQL，而这里的 SQL 每次都不同，注册不进去。
#
# 唯一能让它走中间件的写法是注册一条 `EXPLAIN {{user_sql}}` 的直通脚本
# （实测可行，见 docs/test/2026-08-03-gh_skill-经中间件访问测试报告.md 发现 4）。
# 本实现**不这么做**：那等于在白名单上开一个通用入口，任何 SQL 都能从这条
# 脚本进去，白名单的意义被架空。要不要开这个口子是客户的安全策略决策，
# 不是技术决策，不该由交付方替客户定。
#
# 走的是注册好的 `EXPLAIN (...) {{sql}}` 模板 —— **中间件与直连同一条路**。
# 原先还留了一条「模板受理不了就回落到原始连接」的旁路，理由是直连能出
# DML 的计划。那条旁路实际上到不了：main() 的形态校验先把 DML 拒了。而它
# 一旦被别的形态触到，拿到的就是一条**可写**的原始会话（--analyze 时
# read_only=False），用户 SQL 不经 EXPLAIN 包裹直接下发 —— 实测就是这条路
# 让 `/* c */ UPDATE ...` 真写了库。已删除：两条模式共用一条路径，
# 差异面才是零。


@dataclass(frozen=True)
class Finding:
    kind: str
    severity: str
    detail: str
    advice: str


def shape_reject(sql_text: str) -> Optional[str]:
    """这条 SQL 的**形态**能不能受理；不能就回一句给用户看的话。

    纯文本判断，不连库。判定一律建立在 common.grmp.statement 的归一化结果上
    （先按引号与注释切语句，再取每条的首关键字），**不在原始 SQL 文本上跑
    正则**。原先那套正则有两个方向相反的毛病，实测都能复现：

      - DML 那条 `^\\s*(insert|update|delete|merge)\\b` 锚死在串首却不跳注释，
        `/* c */ UPDATE ...` 判成非 DML —— 漏放行。
      - DDL 那条不带锚点、扫整串原文，`SELECT comment FROM t`、
        `WHERE relname = 'drop'` 全被当成 DDL —— 过度拦截。

    现在改成白名单：首关键字必须是只读起始关键字，其余一律拒。黑名单永远
    会漏（原来那份就漏了 COPY / GRANT / VACUUM / CALL），白名单漏不了。
    """
    statements = stmt.split_statements(sql_text)
    if not statements:
        return ("No executable SQL statement detected "
                "(comments or whitespace only).")
    if len(statements) > 1:
        # 原先数的是分号个数 `> 1`，于是**恰好一个分号**的两条语句漏了过去：
        # `SELECT 1; SELECT pg_backend_pid()`。实测后果三种，没有一种是对的 ——
        # gsql 把第二条真跑了并把结果拼进「执行计划」（退出 0），psycopg2 抛
        # Traceback，中间件才是正确拒绝。数语句，不数分号。
        return ("Multiple SQL statements detected (%d). "
                "Submit one statement at a time." % len(statements))
    if stmt.is_dml(sql_text):
        return "DML keywords (INSERT/UPDATE/DELETE) detected in SQL statement."
    keyword = stmt.leading_keyword(statements[0])
    if keyword not in stmt.READ_ONLY_STARTERS:
        # 措辞不说"非只读" —— 打错的首关键字远比真正的写语句常见，
        # 把 `SELEKT 1` 报成"非只读语句"会把人往完全错误的方向带。
        # 也不能为了给出数据库那句 syntax error 就放它过去：认不出的关键字
        # 未必真的无害（CALL / DO / COPY 都是数据库认得而这里不认的），
        # 白名单的意义就在于不去赌这一把。
        return ("Unsupported leading keyword detected (%s). explain only "
                "plans read-only queries (%s). Check for a typo; DDL/DCL/"
                "maintenance statements are refused by design." % (
                    keyword.upper() or "none",
                    "/".join(sorted(stmt.READ_ONLY_STARTERS)).upper()))
    return None


def explain_via_script(runner, sql_text: str, analyze: bool) -> str:
    """走已注册的 EXPLAIN 模板。中间件与直连共用这条路。

    调用前必须先过 ensure_explainable() —— 模板是文本替换，参数位就是注入面。
    """
    script = "explain.plan_text_analyze" if analyze else "explain.plan_text"
    rows = runner.run(script, {"sql": sql_text})
    # 结果行是列名到值的字典；EXPLAIN 只有一列，取那一列的值
    return "\n".join(str(next(iter(r.values()), "")) for r in rows)


def scan_plan(plan_text: str) -> list[Finding]:
    lower = plan_text.lower()
    orig_lines = plan_text.split("\n")
    out: list[Finding] = []
    for i, line in enumerate(lower.split("\n")):
        trimmed = line.strip()
        if trimmed.startswith("->"):
            trimmed = trimmed[2:].strip()
        detail = orig_lines[i].strip()
        if trimmed.startswith("seq scan"):
            out.append(Finding("seq_scan", "warn", detail,
                               "Full table scan; consider an index on the Filter columns if "
                               "the table is large and selectivity is high."))
        elif trimmed.startswith("sort"):
            out.append(Finding("sort", "warn", detail,
                               "Explicit sort; an index matching ORDER BY may remove it. "
                               "Check work_mem if the sort spills to disk."))
    if "nested loop" in lower and "seq scan" in lower:
        out.append(Finding("nestloop_seqscan", "warn",
                           "Nested Loop combined with Seq Scan",
                           "Inner-side full scans inside a nested loop multiply cost; "
                           "consider an index on the join key."))
    if "hash join" in lower:
        out.append(Finding("hash_join", "info", "Hash Join present",
                           "Usually fine for large joins; verify hash memory fits work_mem."))
    return out


def explain_report(sql_text: str, plan: str, findings: list[Finding],
                   source: str = "", notes: tuple = ()) -> str:
    sql_shown = sql_text if sql_text.strip() else "(未取到 SQL 文本)"
    out = ("## SQL\n\n" + render.code_block("sql", render.truncate(sql_shown, 2000)) +
           "\n## Execution Plan\n\n")
    if source:
        out += f"> 来源:{source}\n\n"
    out += render.code_block("", plan)
    for n in notes:
        out += f"\n> {n}\n"
    if not findings:
        return out + "\n## Findings\n\nNo deterministic risk patterns detected.\n"
    out += "\n## Findings\n\n"
    for f in findings:
        out += f"- **[{f.severity}] {f.kind}**: {f.detail} — {f.advice}\n"
    return out


def explain_by_pid(runner, pid: int, analyze: bool):
    """--pid 路径。返回 (sql_text, plan, source, notes)。

    内核有 gs_get_explain → 运行态计划(不执行任何 SQL);没有、或函数在但返回为空、或调用失败
    → 取该会话当前语句走 EXPLAIN 模板,并把原因写进 notes。GaussDB 该有而没有时 notes 里带中文说明。
    会话不存在抛 PidNotFound;取到的 SQL 过不了形态白名单抛 ShapeRejected。
    """
    notes: list[str] = []
    probe = kf.probe(runner)
    note = kf.missing_note(probe)
    if note:
        notes.append(note)

    sess = kf.session_by_pid(runner, pid)
    sql_text = sess.query if sess is not None else ""

    if probe.has_explain:
        try:
            plan = kf.runtime_plan(runner, pid, probe.explain_args)
            return (sql_text, plan,
                    f"gs_get_explain 运行态计划(pid={pid}):内核里该会话此刻实际执行的计划,未执行该 SQL",
                    tuple(notes))
        except kf.NoRuntimePlan as exc:
            notes.append(f"{exc} 已退回 EXPLAIN 估算计划。")
        except access.QueryError as exc:
            notes.append(f"gs_get_explain 调用失败:{exc}\n已退回 EXPLAIN 估算计划。")

    if sess is None or not sql_text.strip():
        raise PidNotFound(
            f"pid {pid} 没有对应的会话,或该会话当前没有正在执行的语句(pg_stat_activity 里查不到);"
            f"退回 EXPLAIN 需要 SQL 文本,无法继续。")
    reject = shape_reject(sql_text)
    if reject:
        raise ShapeRejected(reject)
    ensure_explainable(sql_text, analyze=analyze)
    plan = explain_via_script(runner, sql_text, analyze)
    return (sql_text, plan,
            f"EXPLAIN 估算计划(会话 pid={pid} 的当前语句;内核无 gs_get_explain 或其未返回计划)",
            tuple(notes))


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(prog="explain.py",
                                 description="EXPLAIN a statement with risk findings")
    ap.add_argument("-c", "--conn", default="", help="连接名（省略则用 gaussdb-login 建立的会话）")
    ap.add_argument("--sql-stdin", action="store_true",
                    help="read SQL text from stdin")
    ap.add_argument("--pid", type=int, default=None,
                    help="按后台线程 pid 取计划:内核有 gs_get_explain(GaussDB)时取运行态计划,"
                         "否则取该会话当前语句走 EXPLAIN")
    ap.add_argument("--analyze", action="store_true",
                    help="EXPLAIN ANALYZE（真执行该 SQL；只受理只读语句）")
    ap.add_argument("--format", choices=["markdown", "json"], default="markdown")
    ap.add_argument("--timeout", type=int, default=None)
    args = ap.parse_args(argv)
    if args.pid is None and not args.sql_stdin:
        ap.error("需要 --sql-stdin 或 --pid 二选一")
    if args.pid is not None and args.sql_stdin:
        ap.error("--sql-stdin 与 --pid 不能同时给")

    if args.pid is not None:
        try:
            runner = access.for_conn(args.conn, timeout=args.timeout)
            sql_text, plan, source, notes = explain_by_pid(runner, args.pid, args.analyze)
        except ShapeRejected as exc:
            print(str(exc))
            return 1
        except (PidNotFound, ExplainNotAllowed) as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 2
        except (common.ConfigError, common.CredentialError, common.DBError,
                access.AccessError, access.QueryError) as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 2
        return _emit(args, sql_text, plan, source, notes)

    sql_text = sys.stdin.read()
    if not sql_text.strip():
        ap.error("empty SQL on stdin")

    # 语句形态校验 —— **纯文本检查，放在连库之前**。
    #
    # 这几条原先写在取到计划之后，那时才 return 1：白跑一次 EXPLAIN、白建一次
    # 连接，而且拒绝理由与「已经拿到计划」同时出现，读起来自相矛盾。
    reject = shape_reject(sql_text)
    if reject:
        print(reject)
        return 1

    # 第二道闸，与上面那道**各判各的**。上面按形态白名单拒，这道是模板自己的
    # 守卫（单语句 + analyze 时只读）。今天两者的结论必然一致 —— 正因如此，
    # 它一旦真的抛出来，说明两道闸对同一条 SQL 判出了不同结果，那本身就是
    # 要当场喊停的事，不是悄悄走下去。
    try:
        ensure_explainable(sql_text, analyze=args.analyze)
    except ExplainNotAllowed as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    try:
        runner = access.for_conn(args.conn, timeout=args.timeout)
        plan = explain_via_script(runner, sql_text, args.analyze)
    # access.QueryError 必须在列 —— 它是本项目归一化的「取数失败」类型，
    # runner.run() 在 SQL 本身执行失败时抛的就是它（打错字、表不存在、
    # 类型不匹配）。漏掉它的后果不是少一条错误信息，而是**直接吐 Traceback**：
    # 用户粘了一条有 typo 的 SQL，看到的是 Python 栈而不是
    # 「syntax error at or near "SELEKT"」。而这是最常见的用户路径之一。
    except (common.ConfigError, common.CredentialError, common.DBError,
            access.AccessError, access.QueryError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    script = "explain.plan_text_analyze" if args.analyze else "explain.plan_text"
    return _emit(args, sql_text, plan, f"EXPLAIN 模板({script})", ())


def _emit(args, sql_text: str, plan: str, source: str, notes: tuple) -> int:
    try:
        # 要了 --analyze 却拿到估算计划(现场脚本按客户只读要求固定关闭 ANALYZE):来源行与说明都要写明。
        # 只对 EXPLAIN 那几条来源判——gs_get_explain 的运行态计划本来就不是 ANALYZE 的产物。
        analyzed = ea.analyzed_for_real(plan, args.analyze)
        if args.analyze and not analyzed and source.startswith("EXPLAIN"):
            source += ";ANALYZE 未生效,这是估算计划"
            notes = tuple(notes) + (ea.FIELD_ANALYZE_OFF_NOTE,)
        findings = scan_plan(plan)
        if args.format == "json":
            print(json.dumps({"sql": sql_text, "plan": plan, "source": source, "analyzed": analyzed,
                              "notes": list(notes),
                              "findings": [f.__dict__ for f in findings]},
                             ensure_ascii=False, indent=2))
        else:
            print(explain_report(sql_text, plan, findings, source=source, notes=notes), end="")
        return 0
    except (ValueError, common.DBError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
