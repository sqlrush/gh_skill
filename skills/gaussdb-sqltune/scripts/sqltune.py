#!/usr/bin/env python3
"""sqltune entry — one-shot SQL tuning pipeline (port of probe/sqltune.go +
cli/sqltune.go).

  1. Fetch normalized SQL by unique_sql_id (or read from --sql-stdin)
  2. Auto-substitute placeholders with synthetic values (override with --bind)
  3. Collect the full evidence bundle (plan + schema + GUCs + findings)
  4. Hard-verify index candidates via hypopg (best-effort; non-fatal)

Usage:
    sqltune.py -c <conn> <unique_sql_id> [--bind V ...] [--analyze]
    sqltune.py -c <conn> --sql-stdin <<'SQL'
    SELECT ...
    SQL
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

import coltypes  # noqa: E402
import common  # noqa: E402
from common import access  # noqa: E402
from common.grmp.statement import (  # noqa: E402
    ExplainNotAllowed,
    ensure_explainable,
)
import render  # noqa: E402
import systables  # noqa: E402
from evidence import (  # noqa: E402
    Evidence,
    collect,
    evidence_report,
    explain_json,
    explain_json_via_script,
)
from hypoindex import MIN_SPEEDUP, IndexCandidate, verify_indexes  # noqa: E402
from placeholder import SubstituteResult, substitute  # noqa: E402
from sqlfetch import sql_fetch  # noqa: E402

# 代价推演。hypopg 走不通时它是唯一的定量证据来源 —— 见 _derivation_report。
import calibrate  # noqa: E402
import catalog  # noqa: E402
import costconst  # noqa: E402
import derivation  # noqa: E402
import plantree  # noqa: E402
import resolve  # noqa: E402
import whatif  # noqa: E402

# 本 skill 的取数分两条口子（见 evidence.py 模块头）：
#
#   runner   固定查询 —— 版本 / 表 / 索引 / 列统计 / GUC / 按 id 取 SQL 原文。
#            已迁到 scripts/registry/sqltune/，走中间件还是直连由 driver 决定。
#   session  原始会话 —— 两件事白名单模型撑不住：
#              1. EXPLAIN 用户临时给的任意 SQL：每次都不同，注册不进去
#              2. hypopg 虚拟索引验证：建虚拟索引与 EXPLAIN 必须在同一会话里
#
# 第 2 条尤其危险：中间件每次调用独立连接、DirectRunner 每次 run() 也开关连接，
# 硬走 runner 不会报错，只会让第二次调用看到没有虚拟索引的原计划，
# 从而得出「加这个索引没用」的**错误结论**。所以宁可在入口失败。
#
# 不为任意 SQL 注册「EXPLAIN {{user_sql}}」这类直通脚本：那等于给白名单开一个
# 通用入口，任何 SQL 都能从这一条进去。要不要开属于客户的安全策略决策，
# 不是交付方能替客户定的技术选择。同 gaussdb-explain 的处理。
_SESSION_REQUIRED = (
    "sqltune 的两项核心能力都要求一条原始数据库会话：\n"
    "  · EXPLAIN 用户给的任意 SQL —— 白名单按逻辑脚本名放行预注册的 SQL，"
    "而这里的 SQL 每次都不同，无法预注册；\n"
    "  · hypopg 虚拟索引验证 —— 建虚拟索引与 EXPLAIN 必须落在同一会话，"
    "跨调用会**不报错地**得出「加索引没用」的错误结论。\n"
    "**该能力在白名单模型下不可用。**\n"
    "已迁到白名单的部分（按 id 取 SQL 原文、表/索引/列统计/GUC）本身能走中间件，"
    "但缺了执行计划的证据包不足以支撑调优结论，所以整条命令在此停止，不做半份输出。\n"
    "可选做法：为这类诊断保留一条直连通道（driver: pg8000），"
    "或在客户环境不提供本 skill；只看 SQL 原文/慢 SQL 清单可改用 "
    "gaussdb-sqlfetch / gaussdb-topsql / gaussdb-slowsql / gaussdb-health。"
)


@dataclass(frozen=True)
class TuneResult:
    original_sql: str
    substitution: SubstituteResult
    evidence: Evidence
    sql_id: str = ""
    source: str = ""
    schema: str = ""
    verified_indexes: list = field(default_factory=list)
    index_verify_note: str = ""
    derivation_report: str = ""


_NO_HYPOPG_BODY = (
    "**hypopg 虚拟索引验证在本次连接下不可用。** 虚拟索引必须与随后的 EXPLAIN "
    "落在同一条连接里，而本次访问路径不提供跨语句的持久会话 —— 跨调用会不报错"
    "地得出「加这个索引没用」的错误结论。\n"
    "**替代证据见下面的「代价推演」一节**：它用规划器自己的公式复算当前计划的"
    "每一个节点并与 EXPLAIN 实测逐节点比对，通过了才说明模型在这个实例上可信。\n"
    "注意两者的区别，别混为一谈：hypopg 是**实测**加了索引之后的计划；"
    "代价推演校准的是**基线**，即「当前这个代价是怎么算出来的」。\n"
    "所以下面的索引建议依然**未经验证** —— 推演没有回答「加了这条索引会变成"
    "多少」，加索引前请**人工验证**。"
)

# 收尾那句按访问路径分叉。同一件事，两边的下一步不一样：
#   本机直连  下一步是换一条 driver: pg8000 的连接重跑，确实能拿到背书
#   白名单    压根没有直连通道 —— 那句话在客户环境不是建议，是噪音，
#             还会把人往「绕过白名单」的方向引。这边只能是人工验证。
_HYPOPG_HINT_DIRECT = "需要 hypopg 背书请改用 driver: pg8000 的连接重跑。"
_HYPOPG_HINT_WHITELIST = (
    "本次走的是白名单访问路径（只执行预注册脚本、每次调用独立连接），"
    "该能力在这套部署里不提供 —— 没有可切换的选项，索引建议以人工验证为准。"
)

# 兼容旧名：降级标注不能丢，tests/test_degrade_contract_units.py 钉着它。
_NO_HYPOPG_NOTE = _NO_HYPOPG_BODY + _HYPOPG_HINT_DIRECT


def no_hypopg_note(runner) -> str:
    """按访问路径给出降级标注。runner 就是「这条路是什么」的载体。"""
    if getattr(runner, "whitelist_only", False):
        return _NO_HYPOPG_BODY + _HYPOPG_HINT_WHITELIST
    return _NO_HYPOPG_BODY + _HYPOPG_HINT_DIRECT


def _derivation_report(runner, db, sql_text: str, ev) -> str:
    """跑一遍代价推演，返回报告正文。

    **任何一步失败都返回一段说明，不抛异常。** 推演是附加证据，拿不到不该让
    整条调优命令失败。但失败原因必须落到报告里 —— 静默省略这一节，读的人会
    以为「没有推演」而不是「推演没做成」，而这两件事对结论可信度的影响不同。
    """
    header = "\n## 代价推演\n\n"
    try:
        cost = costconst.from_gucs(ev.gucs)
    except costconst.MissingConstant as exc:
        return header + "未进行：代价常数不全 —— %s\n" % exc
    try:
        cat = catalog.from_evidence(ev)
    except catalog.CatalogError as exc:
        return header + "未进行：%s\n" % exc
    try:
        raw = (explain_json(db, sql_text) if db is not None
               else explain_json_via_script(runner, sql_text))
        root = plantree.parse(raw)
    except Exception as exc:            # 取计划失败的形态太多，统一兜住
        return header + "未进行：拿不到 JSON 格式的执行计划 —— %s\n" % exc

    cal = calibrate.calibrate_best_variant(
        root, lambda v: resolve.make_resolver(cat, cost, v))
    verdicts = cat.freshness_report([t.name for t in ev.tables])

    proposals = _index_proposals(root, cat, cost, cal, verdicts)
    return "\n" + derivation.render_report(cal, cost, verdicts,
                                           proposals=proposals)


def _index_proposals(root, cat, cost, cal, verdicts) -> list:
    """算候选索引的假设代价。

    **门没过就一条都不算。** 不是算了再标成不可信 —— 数字一旦印出来就会被读，
    旁边那行免责声明拦不住。这个判断在这里做一次，报告里再做一次，两处都做
    是有意的：将来有人直接调 render_report 传进 proposals，也拦得住。
    """
    ok, _ = derivation.may_emit_advice(cal, verdicts)
    if not ok:
        return []

    resolver = resolve.make_resolver(cat, cost, cal.variant)
    out = []
    for cand in whatif.propose_from_plan(root, cat, cost, plantree.walk):
        stat = cand["stat"]
        if stat.avg_width <= 0 or stat.correlation is None:
            continue
        try:
            scan = whatif.hypothetical_index_scan(
                cand["table"], stat, cand["selectivity"], cost,
                cat.total_table_pages(), stat.avg_width,
                # propose_from_plan 的选择率来自该节点的 Plan Rows，是实测反推
                selectivity_from_plan=True)
            rec = whatif.recompute_with_override(root, resolver, cand["node"],
                                                 scan)
        except Exception:
            # 假设路径算不出来不该影响已经校准好的基线报告
            continue
        out.append(whatif.Proposal(
            ddl=cand["ddl"], table=cand["table"].name, column=cand["column"],
            baseline_total=root.total_cost, hypothetical_total=rec.root_total,
            scan_estimate=scan, recomputed=rec))
    return out


def _guard_sql(sql_text: str, analyze: bool) -> None:
    """没有会话时，用户 SQL 要走 EXPLAIN 模板 —— 先过注入守卫。

    DML + --analyze 在这条路上**不可用**，必须报错而不是悄悄不 analyze：
    静默降级会让用户以为拿到的是实际执行的计划，实测两者能差 2.3 倍。
    """
    ensure_explainable(sql_text, analyze=analyze)


def _tune(runner, db, *, original_sql: str, binds: list[str], do_analyze: bool,
          sql_id: str = "", source: str = "", schema: str = "") -> TuneResult:
    verdict = systables.system_verdict(original_sql)
    if verdict.is_system:
        raise systables.SystemSQLSkipped(verdict.system_objects)
    types = coltypes.infer_types(runner, original_sql)
    sub = substitute(original_sql, binds, types=types)
    coltypes.validate_binds(sub.substitutions, types)
    if db is None:
        # 没有会话时 SQL 要递进 EXPLAIN 模板 —— 无论它从哪来都得过守卫。
        # 按 sql_id 取的 SQL 走的是另一条入口，早先漏了这一道。
        _guard_sql(sub.sql, do_analyze)
    try:
        ev = collect(runner, db, sub.sql, do_analyze)
    except Exception as exc:
        # 类型转换错时点名坏值出自哪个占位符;非类型错原样抛。
        enriched = coltypes.enrich_type_error(str(exc), sub.substitutions)
        if not enriched:
            raise
        try:
            wrapped = type(exc)(enriched)
        except Exception:  # 异常类构造签名特殊时,宁可保留原报错
            raise exc
        raise wrapped from exc

    verified: list[IndexCandidate] = []
    note = ""
    if db is None:
        # 没有原始会话 —— hypopg 的虚拟索引必须与随后的 EXPLAIN 同处一条连接，
        # 跨调用会**不报错地**得出「加这个索引没用」的错误结论。所以不做，
        # 并且把这件事写进报告：本次的索引建议没有验证背书。
        note = no_hypopg_note(runner)
    else:
        try:
            verified = verify_indexes(db, sub.sql, MIN_SPEEDUP)
        except Exception as exc:  # best-effort: degrade gracefully (non-fatal)
            note = ("索引验证不可用（OpenGauss hypopg/gs_index_advise 未启用或不支持）："
                    + str(exc))

    # 推演两条路径都跑：直连路径也要它。hypopg 只回答「加了索引之后代价多少」，
    # 回答不了「当前这个代价是怎么来的」—— 后者才是让人能复核结论的那部分。
    deriv = _derivation_report(runner, db, sub.sql, ev)

    return TuneResult(original_sql=original_sql, substitution=sub, evidence=ev,
                      sql_id=sql_id, source=source, schema=schema,
                      verified_indexes=verified, index_verify_note=note,
                      derivation_report=deriv)


def tune_by_id(runner, db, raw_id: str, binds: list[str], do_analyze: bool) -> TuneResult:
    fr = sql_fetch(runner, raw_id)
    if fr.truncated:
        raise ValueError(
            f"sql id {raw_id} 的文本被 openGauss 截断（{fr.truncated_reason}）——"
            f"track_activity_query_size 限制了留存长度，数据库里就没有完整 SQL。"
            f"无法对半截 SQL 做调优。请改用 `--sql-stdin` 传入完整 SQL 文本"
            f"（或调大 track_activity_query_size 并让该 SQL 重新执行后再按 id 取）。")
    return _tune(runner, db, original_sql=fr.sql, binds=binds, do_analyze=do_analyze,
                 sql_id=fr.sql_id, source=fr.source, schema=fr.schema)


def tune_by_sql(runner, db, sql_text: str, binds: list[str], do_analyze: bool) -> TuneResult:
    return _tune(runner, db, original_sql=sql_text, binds=binds, do_analyze=do_analyze)


def sqltune_report(tr: TuneResult) -> str:
    sb = ["# SQL Tune\n"]
    if tr.sql_id:
        sb.append(f"- SQL_ID: `{tr.sql_id}`")
        if tr.source:
            sb.append(f"- Source: `dbe_perf.{tr.source}`")
        if tr.schema:
            sb.append(f"- Schema: `{tr.schema}`")
        sb.append("")
    out = "\n".join(sb) + "\n"

    sub = tr.substitution
    if sub.placeholders > 0:
        # 小节名固定以 "## Placeholder Substitution" 开头(SKILL.md 按它取节),
        # 但**不能无条件自称合成值**:调用方全程用 --bind 传了真实值时,再劝
        # 一句"re-run with --bind"会让模型给真实结论硬加一条合成值免责,
        # 把已经可靠的倍数说弱。降级要说出口,没降级也别装。
        if any(s.source != "bind" for s in sub.substitutions):
            out += "## Placeholder Substitution (synthetic values)\n\n"
            out += ("> Placeholders have been replaced with synthetic values to generate "
                    "an execution plan. **Plan shape is reliable; row counts and "
                    "selectivity estimates are approximate.**\n")
            out += "> For precise analysis, re-run with `--bind` to supply real values.\n\n"
        else:
            out += "## Placeholder Substitution (real values from `--bind`)\n\n"
            out += ("> Every placeholder was replaced with a real value supplied via "
                    "`--bind`. **Row counts and selectivity reflect these actual "
                    "parameters** — the approximate-value caveat does not apply "
                    "to this report.\n\n")
        rows = [[str(i + 1), s.token, s.value, s.source, render.truncate(s.context, 60)]
                for i, s in enumerate(sub.substitutions)]
        out += render.table(["#", "Token", "Value", "Source", "Context"], rows) + "\n"

    out += evidence_report(tr.evidence)

    out += "\n## Verified Index Candidates\n\n"
    if tr.index_verify_note:
        out += tr.index_verify_note + "\n"
    elif not tr.verified_indexes:
        out += ("No index candidate passed verification (gs_index_advise found none, "
                "or none reduced cost ≥1.3×).\n")
    else:
        rows = []
        for i, c in enumerate(tr.verified_indexes):
            rows.append([str(i + 1), c.ddl, f"{c.orig_cost:.2f}", f"{c.hypo_cost:.2f}",
                         f"{c.speedup:.2f}×", "✓" if c.used else "—"])
        out += render.table(["#", "Index DDL", "Orig Cost", "Hypo Cost", "Speedup", "Used"], rows)
        out += ("\n> These indexes were verified with hypothetical (virtual) indexes — "
                "costs are real EXPLAIN comparisons, no index was actually built.\n")

    if tr.derivation_report:
        out += tr.derivation_report
    return out


def _to_jsonable(tr: TuneResult) -> dict:
    return {
        "sql_id": tr.sql_id,
        "source": tr.source,
        "schema": tr.schema,
        "original_sql": tr.original_sql,
        "substitution": {
            "sql": tr.substitution.sql,
            "placeholders": tr.substitution.placeholders,
            "substitutions": [s.__dict__ for s in tr.substitution.substitutions],
        },
        "evidence": {
            "version": tr.evidence.version,
            "analyzed": tr.evidence.analyzed,
            "plan": tr.evidence.plan,
            "findings": [f.__dict__ for f in tr.evidence.findings],
            "tables": [t.__dict__ for t in tr.evidence.tables],
            "indexes": [i.__dict__ for i in tr.evidence.indexes],
            "columns": [c.__dict__ for c in tr.evidence.columns],
            "gucs": [g.__dict__ for g in tr.evidence.gucs],
        },
        "verified_indexes": [c.__dict__ for c in tr.verified_indexes],
        "index_verify_note": tr.index_verify_note,
        "derivation_report": tr.derivation_report,
    }


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(prog="sqltune.py",
                                 description="One-shot SQL tuning evidence + hypopg index verification")
    ap.add_argument("sql_id", nargs="?", help="unique_sql_id (integer, may be negative)")
    ap.add_argument("-c", "--conn", default="", help="连接名（省略则用 gaussdb-login 建立的会话）")
    ap.add_argument("--sql-stdin", action="store_true", help="read SQL text from stdin")
    ap.add_argument("--bind", action="append", default=[],
                    help="bind value for placeholder (repeatable, positional order)")
    ap.add_argument("--analyze", action="store_true",
                    help="EXPLAIN ANALYZE (executes the SQL; DML wrapped in rollback)")
    ap.add_argument("--format", choices=["markdown", "json"], default="markdown")
    ap.add_argument("--timeout", type=int, default=None, help="statement timeout (s)")
    args = ap.parse_args(argv)

    has_id = args.sql_id is not None
    if not has_id and not args.sql_stdin:
        ap.error("provide a <sql_id> positional arg or --sql-stdin")
    if has_id and args.sql_stdin:
        ap.error("provide either <sql_id> or --sql-stdin, not both")

    sql_text = None
    if args.sql_stdin:
        sql_text = sys.stdin.read()
        if not sql_text.strip():
            ap.error("empty SQL on stdin")

    db = None
    try:
        runner = access.for_conn(args.conn, timeout=args.timeout)
        # 先问再取。中间件这条路注定给不了会话，原先仍要先 session_for() 一次
        # 再从 SessionUnavailable 里恢复 —— 白跑一趟，且降级路径是靠 except
        # 分支拼出来的。问一句就知道走不走得通，不必拿异常当控制流。
        if access.may_provide_session(args.conn):
            try:
                # 有会话就用 —— 索引验证只有这条路
                db = access.session_for(args.conn, read_only=not args.analyze)
            except access.SessionUnavailable:
                # gsql 要建连之后才看得出没有会话（每条语句起独立子进程），
                # 所以这条 except 还得留着，只是不再兼管中间件那种情形。
                db = None
        if db is None:
            # 没有会话不等于什么都做不了：证据与执行计划照采，
            # 只是索引建议拿不到 hypopg 背书。降级的事实写进报告，不隐瞒。
            #
            # 按 sql_id 取的 SQL 此刻还没到手，守卫挪到取回之后（_tune 里）。
            # 直接给的 SQL 现在就能校验，早报错早收工。
            if not has_id:
                _guard_sql(sql_text, args.analyze)
    except (common.ConfigError, common.CredentialError, common.DBError,
            ExplainNotAllowed, access.AccessError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    try:
        if db is not None:
            db.set_statement_timeout(
                args.timeout if args.timeout is not None
                else access.DEFAULT_SKILL_TIMEOUT_SECONDS)
        if has_id:
            tr = tune_by_id(runner, db, args.sql_id, args.bind, args.analyze)
        else:
            tr = tune_by_sql(runner, db, sql_text, args.bind, args.analyze)

        if len(args.bind) > tr.substitution.placeholders:
            print(f"warning: {len(args.bind)} --bind value(s) given but only "
                  f"{tr.substitution.placeholders} placeholder(s) found; extras ignored",
                  file=sys.stderr)

        if args.format == "json":
            print(json.dumps(_to_jsonable(tr), ensure_ascii=False, indent=2))
        else:
            print(sqltune_report(tr), end="")
        return 0
    except systables.SystemSQLSkipped as exc:
        # 策略性跳过是确定性结论,不是失败——exit 0,免得现场 agent 当错误反复重试。
        if args.format == "json":
            print(json.dumps(systables.skip_json(exc.objects), ensure_ascii=False, indent=2))
        else:
            print(systables.skip_report(exc.objects), end="")
        return 0
    # access.QueryError 归一了两条路径的取数失败（中间件 GrmpError / 直连
    # DBError），skill 只认这一个类型；common.DBError 仍要留着 —— 会话那条口子
    # 不经过 runner，报的还是原始的 DBError。
    # ColumnError / ParamError 刻意不接：那是脚本定义缺陷，必须响亮失败。
    except (ValueError, KeyError, common.DBError, access.QueryError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    finally:
        if db is not None:
            db.close()


if __name__ == "__main__":
    raise SystemExit(main())
