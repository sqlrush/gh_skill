#!/usr/bin/env python3
"""proctune entry — stored-procedure analysis & cursor SELECT tuning.

Port of internal/probe/proc.go + internal/cli/proc.go. Two subcommands:

  collect <schema.proc>       advisory evidence: source + structural findings
                              + embedded statements + runtime note + GUC
  tune-cursor <schema.proc>   per read-only cursor: substituted SELECT evidence
                              + hypopg index verification; ineligible cursors
                              are listed under Skipped Cursors

The procedure is never executed; the session is read-only.

取数分两条口子：

  collect       全部是固定查询（过程定义 + 关键 GUC），走 access.for_conn()
                的统一入口，中间件与直连两条路径都跑得通
  tune-cursor   还需要一条**原始会话**：EXPLAIN 变量替换后的游标 SELECT
                （每个游标都不同，注册不进白名单）与 hypopg 虚拟索引验证
                （建索引与 EXPLAIN 必须同会话）。会话由 access.session_for()
                索取，拿不到就 exit 2 —— 不降级成没有计划的证据包

注：--timeout 只作用在 tune-cursor 的那条会话上；collect 走 runner，
超时由统一入口自己管（DirectRunner 默认 60s）。

Usage:
    proctune.py collect      -c <conn> <schema.proc>
    proctune.py tune-cursor  -c <conn> <schema.proc> [--cursor NAME ...] [--bind var=value ...]
"""
from __future__ import annotations

import argparse
import json
import pathlib
import sys
from dataclasses import dataclass, field
from typing import Optional

_HERE = pathlib.Path(__file__).resolve()
sys.path.insert(0, str(_HERE.parent))          # sibling modules
for _anc in _HERE.parents:                      # locate common/ (repo root or install dir)
    if (_anc / "common" / "__init__.py").exists():
        sys.path.insert(0, str(_anc))
        break

import common  # noqa: E402
from common import access  # noqa: E402
import procanalyze as pa  # noqa: E402
import render  # noqa: E402
from evidence import Evidence, collect, collect_gucs, evidence_report  # noqa: E402
from hypoindex import MIN_SPEEDUP, verify_indexes  # noqa: E402

for parent in _HERE.parents:
    if (parent / "common" / "sql.py").exists():
        sys.path.insert(0, str(parent))
        break

# SQL 已迁到 scripts/registry/proctune/ —— 两条路径共用同一份定义
PROC_DEF_SCRIPT = "proctune.proc_def"

# 单个游标取证失败时降级成 Skipped Cursor 的错误集合。
# access.QueryError 归一了两条路径的取数失败（中间件 GrmpError / 直连
# DBError）；会话那条口子不经过 runner，报的还是原始的 common.DBError。
# ColumnError / ParamError / SessionUnavailable **刻意不在此列** ——
# 那三类分别是脚本定义缺陷、调用方传参错误、能力缺口，被降级逻辑接住
# 就等于永远发现不了。
_EVIDENCE_ERRORS = (access.QueryError, common.DBError, ValueError)

_SESSION_REQUIRED = (
    "proctune tune-cursor 的两项核心能力都要求一条原始数据库会话：\n"
    "  · EXPLAIN 变量替换后的游标 SELECT —— 白名单按逻辑脚本名放行预注册的 SQL，"
    "而每个游标的 SELECT 都不同，无法预注册；\n"
    "  · hypopg 虚拟索引验证 —— 建虚拟索引与 EXPLAIN 必须落在同一会话，"
    "跨调用会**不报错地**得出「加索引没用」的错误结论。\n"
    "**该能力在白名单模型下不可用。**\n"
    "已迁到白名单的部分（过程定义、表/索引/列统计/GUC）本身能走中间件，"
    "但缺了执行计划与索引验证的证据包不足以支撑调优结论，所以整条子命令在此停止，"
    "不做半份输出。\n"
    "可选做法：为这类诊断保留一条直连通道（driver: pg8000），"
    "或在客户环境不提供本能力；只做结构诊断可改用 "
    "`proctune.py collect`（走中间件）或 gaussdb-procinfo。"
)


_RUNTIME_NOTE = ("运行时归因（embedded SQL 的真实 calls/avg/total）需实例开启 "
                 "track_stmt_stat_level 捕获嵌套语句；当前按纯静态分析。见 references/proc-setup.md。")


@dataclass(frozen=True)
class EmbeddedStmt:
    line: int
    kind: str
    sql: str


@dataclass(frozen=True)
class ProcEvidence:
    proc: pa.ProcDef
    structure: list
    embedded: list
    runtime_note: str
    gucs: list


@dataclass(frozen=True)
class CursorEvidence:
    name: str
    kind: str
    orig_sql: str
    var_subs: list
    evidence: Evidence
    verified_indexes: list = field(default_factory=list)
    index_verify_note: str = ""


@dataclass(frozen=True)
class CursorTuneResult:
    proc: pa.ProcDef
    cursors: list = field(default_factory=list)
    skipped: list = field(default_factory=list)


def _split_qualified(q: str) -> tuple[str, str]:
    if "." in q:
        i = q.rindex(".")
        return q[:i], q[i + 1:]
    return "", q


def fetch_proc_def(runner, qualified: str) -> pa.ProcDef:
    """经统一入口取数。走中间件还是直连由连接的 driver 决定，这里不感知。"""
    schema, name = _split_qualified(qualified)
    rows = runner.run(PROC_DEF_SCRIPT, {"name": name, "schema": schema})
    if not rows:
        raise ValueError(f"proc {qualified!r} not found")
    r = rows[0]
    # 协议把 NULL 渲染成空串，原来那句 `x if x is not None else ""` 的效果
    # 由入口保证，这里不必再兜。
    return pa.analyze(r["nspname"], r["proname"], r["lanname"],
                      r["prosrc"], r["args"])


def proc_collect(runner, qualified: str) -> ProcEvidence:
    proc = fetch_proc_def(runner, qualified)
    embedded = [EmbeddedStmt(line=c.line, kind=f"cursor:{c.name}", sql=c.select_sql)
                for c in pa.extract_cursors(proc.body)]
    return ProcEvidence(
        proc=proc,
        structure=pa.scan_structure(proc.body),
        embedded=embedded,
        runtime_note=_RUNTIME_NOTE,
        gucs=collect_gucs(runner),
    )


_NO_HYPOPG_NOTE = (
    "**索引建议未经验证。** 本次连接不提供跨语句的持久会话（中间件的白名单模型，"
    "或每条语句起独立子进程的 gsql），而 hypopg 虚拟索引必须与随后的 EXPLAIN "
    "落在同一条连接里 —— 跨调用会不报错地得出「加这个索引没用」的错误结论。\n"
    "下面的索引建议来自表/列统计与执行计划的推断，**加索引前请人工验证**；"
    "需要验证背书请改用 driver: pg8000 的连接重跑。"
)


def tune_cursors(runner, db, qualified: str,
                 only: list[str], binds: dict) -> CursorTuneResult:
    """runner 取固定查询；db 是原始会话，EXPLAIN 与 hypopg 两段都要用它。

    hypopg 是「建虚拟索引 → 在**同一会话**里 EXPLAIN」，两步必须落在一条
    连接上。统一入口的两条路径都不提供跨调用的持久会话（中间件每次调用
    独立连接，DirectRunner 每次 run() 也开关连接），所以会话由 main()
    显式向 access.session_for() 索取，拿不到就整条命令停下 —— 不降级、
    不产出没有计划的证据包。
    """
    proc = fetch_proc_def(runner, qualified)
    only_set = {c.lower() for c in only}
    cursors: list[CursorEvidence] = []
    skipped: list[pa.CursorDecl] = []

    # Record/composite variables (e.g. an outer cursor's `rec_N record`). A
    # nested cursor SELECT that references `recvar.field` depends on the enclosing
    # loop and cannot be EXPLAINed standalone — skip it cleanly with a clear
    # reason instead of letting substitution mangle it into a syntax error.
    record_vars = pa.record_var_names(proc.vars)

    for cur in pa.extract_cursors(proc.body):
        if only_set and cur.name.lower() not in only_set:
            continue
        if cur.eligible:
            rv = pa.references_record_var(cur.select_sql, record_vars)
            if rv:
                cur.eligible = False
                cur.skip_reason = (
                    f"引用外层游标记录变量 {rv}.*（依赖循环上下文，非独立可执行）")
        if not cur.eligible:
            skipped.append(cur)
            continue
        sub = pa.substitute_vars(cur.select_sql, proc.vars, binds)
        try:
            ev = collect(runner, db, sub.sql, False)
        except _EVIDENCE_ERRORS as exc:
            cur.eligible = False
            cur.skip_reason = "证据采集失败：" + str(exc)
            skipped.append(cur)
            continue

        verified, note = [], ""
        if db is None:
            # 没有原始会话 —— hypopg 虚拟索引必须与随后的 EXPLAIN 同处一条连接，
            # 跨调用会**不报错地**得出「加这个索引没用」的错误结论。
            note = _NO_HYPOPG_NOTE
        else:
            try:
                verified = verify_indexes(db, sub.sql, MIN_SPEEDUP)
            except Exception as exc:
                note = ("索引验证不可用（hypopg/gs_index_advise 未启用或不支持）："
                        + str(exc))

        cursors.append(CursorEvidence(
            name=cur.name, kind=cur.kind, orig_sql=cur.select_sql,
            var_subs=sub.subs, evidence=ev,
            verified_indexes=verified, index_verify_note=note))

    return CursorTuneResult(proc=proc, cursors=cursors, skipped=skipped)


# --- reports (port of cli/proc.go) -------------------------------------------

def _arg_string(args: list) -> str:
    return ", ".join(f"{a.name} {a.type}" for a in args)


def proc_collect_report(pe: ProcEvidence) -> str:
    d = pe.proc
    b = ["# Proc Collect\n\n## Procedure Source\n",
         f"- Name: `{d.schema}.{d.name}`",
         f"- Language: `{d.lang}`",
         "- Args: `" + _arg_string(d.args) + "`",
         f"- Rollback-safe: {str(d.rollback_safe).lower()}\n"]
    out = "\n".join(b) + "\n" + render.code_block("", d.body)

    out += "\n## Structural Findings\n\n"
    if not pe.structure:
        out += "None.\n"
    else:
        rows = [[f"[H{i + 1}]", str(f.line), f.kind, render.truncate(f.snippet, 80)]
                for i, f in enumerate(pe.structure)]
        out += render.table(["Marker", "Line", "Kind", "Snippet"], rows)

    out += "\n## Embedded Statements\n\n"
    if not pe.embedded:
        out += "None statically extracted.\n"
    else:
        rows = [[str(e.line), e.kind, render.truncate(e.sql, 100)] for e in pe.embedded]
        out += render.table(["Line", "Kind", "SQL"], rows)

    out += "\n## Runtime Attribution\n\n> " + pe.runtime_note + "\n"

    out += "\n## Key Parameters (GUC)\n\n"
    out += render.table(["NAME", "SETTING", "UNIT"],
                        [[g.name, g.setting, g.unit] for g in pe.gucs])
    return out


def _verified_index_block(indexes: list, note: str) -> str:
    out = "## Verified Index Candidates\n\n"
    if note:
        return out + note + "\n"
    if not indexes:
        return out + ("No index candidate passed verification (gs_index_advise found none, "
                      "or none reduced cost ≥1.3×).\n")
    rows = []
    for i, c in enumerate(indexes):
        rows.append([str(i + 1), c.ddl, f"{c.orig_cost:.2f}", f"{c.hypo_cost:.2f}",
                     f"{c.speedup:.2f}×", "✓" if c.used else "—"])
    out += render.table(["#", "Index DDL", "Orig Cost", "Hypo Cost", "Speedup", "Used"], rows)
    out += ("\n> These indexes were verified with hypothetical (virtual) indexes — "
            "costs are real EXPLAIN comparisons, no index was actually built.\n")
    return out


def cursor_tune_report(tr: CursorTuneResult) -> str:
    p = tr.proc
    out = f"# Cursor Tune  (proc: `{p.schema}.{p.name}`, lang: {p.lang})\n"
    if not tr.cursors:
        out += "\n没有可处理的只读游标 SELECT。见 `## Skipped Cursors`。\n"
    for ce in tr.cursors:
        out += f"\n## Cursor {ce.name}\n\n- Kind: `{ce.kind}`\n\n原始游标 SELECT（含变量）：\n\n"
        out += render.code_block("sql", ce.orig_sql)
        if ce.var_subs:
            out += "\n## Variable Substitution\n\n"
            rows = [[s.var, s.type, s.value, s.source] for s in ce.var_subs]
            out += render.table(["Var", "Type", "Value", "Source"], rows)
        out += "\n" + evidence_report(ce.evidence)
        out += "\n" + _verified_index_block(ce.verified_indexes, ce.index_verify_note)

    out += "\n## Skipped Cursors\n\n"
    if not tr.skipped:
        out += "None.\n"
    else:
        rows = [[s.name, s.kind, s.skip_reason] for s in tr.skipped]
        out += render.table(["Name", "Kind", "Reason"], rows)
    return out


# --- JSON serialization ------------------------------------------------------

def _evidence_dict(ev: Evidence) -> dict:
    return {
        "version": ev.version, "analyzed": ev.analyzed, "plan": ev.plan,
        "findings": [f.__dict__ for f in ev.findings],
        "tables": [t.__dict__ for t in ev.tables],
        "indexes": [i.__dict__ for i in ev.indexes],
        "columns": [c.__dict__ for c in ev.columns],
        "gucs": [g.__dict__ for g in ev.gucs],
    }


def _proc_dict(p: pa.ProcDef) -> dict:
    return {"schema": p.schema, "name": p.name, "lang": p.lang,
            "args": [a.__dict__ for a in p.args], "vars": p.vars,
            "rollback_safe": p.rollback_safe}


def _collect_json(pe: ProcEvidence) -> str:
    return json.dumps({
        "proc": _proc_dict(pe.proc),
        "structure": [f.__dict__ for f in pe.structure],
        "embedded": [e.__dict__ for e in pe.embedded],
        "runtime_note": pe.runtime_note,
        "gucs": [g.__dict__ for g in pe.gucs],
    }, ensure_ascii=False, indent=2)


def _tune_json(tr: CursorTuneResult) -> str:
    return json.dumps({
        "proc": _proc_dict(tr.proc),
        "cursors": [{
            "name": ce.name, "kind": ce.kind, "orig_sql": ce.orig_sql,
            "var_subs": [v.__dict__ for v in ce.var_subs],
            "evidence": _evidence_dict(ce.evidence),
            "verified_indexes": [c.__dict__ for c in ce.verified_indexes],
            "index_verify_note": ce.index_verify_note,
        } for ce in tr.cursors],
        "skipped": [s.__dict__ for s in tr.skipped],
    }, ensure_ascii=False, indent=2)


def _parse_bind_pairs(pairs: list[str]) -> dict:
    m: dict = {}
    for p in pairs:
        k = p.find("=")
        if k < 1:
            raise ValueError(f"--bind {p!r} must be var=value")
        m[p[:k].strip().lower()] = p[k + 1:]
    return m


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(prog="proctune.py",
                                 description="Stored-procedure analysis & cursor SELECT tuning")
    sub = ap.add_subparsers(dest="cmd", required=True)

    pc = sub.add_parser("collect", help="advisory evidence for a procedure")
    pc.add_argument("proc", help="schema.proc")
    pc.add_argument("-c", "--conn", required=True)
    pc.add_argument("--format", choices=["markdown", "json"], default="markdown")
    pc.add_argument("--timeout", type=int, default=None)

    pt = sub.add_parser("tune-cursor", help="tune read-only cursor SELECTs")
    pt.add_argument("proc", help="schema.proc")
    pt.add_argument("-c", "--conn", required=True)
    pt.add_argument("--cursor", action="append", default=[],
                    help="only process the named cursor(s) (repeatable)")
    pt.add_argument("--bind", action="append", default=[],
                    help="override a cursor variable: var=value (repeatable)")
    pt.add_argument("--format", choices=["markdown", "json"], default="markdown")
    pt.add_argument("--timeout", type=int, default=None)

    args = ap.parse_args(argv)

    try:
        binds = _parse_bind_pairs(args.bind) if args.cmd == "tune-cursor" else {}
    except ValueError as exc:
        ap.error(str(exc))

    # collect 全部走已注册脚本，中间件上跑得通；tune-cursor 还要一条原始会话
    # （EXPLAIN 任意游标 SELECT + hypopg），在此显式索取，拿不到就当场停。
    needs_session = args.cmd == "tune-cursor"
    db = None
    try:
        runner = access.for_conn(args.conn, timeout=args.timeout)
        if needs_session:
            try:
                db = access.session_for(args.conn)
            except access.SessionUnavailable:
                # 没有会话不等于什么都做不了：证据与执行计划照采，
                # 只是索引建议拿不到 hypopg 背书。降级写进报告，不隐瞒。
                db = None
    except (common.ConfigError, common.CredentialError,
            common.DBError, access.AccessError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    try:
        if db is not None:
            db.set_statement_timeout(
                args.timeout if args.timeout is not None
                else access.DEFAULT_SKILL_TIMEOUT_SECONDS)
        if args.cmd == "collect":
            pe = proc_collect(runner, args.proc)
            out = _collect_json(pe) if args.format == "json" else proc_collect_report(pe)
        else:
            tr = tune_cursors(runner, db, args.proc, args.cursor, binds)
            out = _tune_json(tr) if args.format == "json" else cursor_tune_report(tr)
        print(out, end="" if args.format == "markdown" else "\n")
        return 0
    # access.QueryError 归一了两条路径的取数失败；common.DBError 仍要留着 ——
    # 会话那条口子不经过 runner，报的还是原始的 DBError。
    # ColumnError / ParamError 刻意不接：那是脚本定义缺陷，必须响亮失败。
    except (ValueError, KeyError, common.DBError, access.QueryError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    finally:
        if db is not None:
            db.close()


if __name__ == "__main__":
    raise SystemExit(main())
