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

import common  # noqa: E402
from common import access  # noqa: E402
import render  # noqa: E402
from evidence import Evidence, collect, evidence_report  # noqa: E402
from hypoindex import MIN_SPEEDUP, IndexCandidate, verify_indexes  # noqa: E402
from placeholder import SubstituteResult, substitute  # noqa: E402
from sqlfetch import sql_fetch  # noqa: E402

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


def _tune(runner, db, *, original_sql: str, binds: list[str], do_analyze: bool,
          sql_id: str = "", source: str = "", schema: str = "") -> TuneResult:
    sub = substitute(original_sql, binds)
    ev = collect(runner, db, sub.sql, do_analyze)

    verified: list[IndexCandidate] = []
    note = ""
    try:
        # db 是原始会话：hypopg 的虚拟索引必须与随后的 EXPLAIN 同处一条连接
        verified = verify_indexes(db, sub.sql, MIN_SPEEDUP)
    except Exception as exc:  # best-effort: degrade gracefully (non-fatal)
        note = ("索引验证不可用（OpenGauss hypopg/gs_index_advise 未启用或不支持）："
                + str(exc))

    return TuneResult(original_sql=original_sql, substitution=sub, evidence=ev,
                      sql_id=sql_id, source=source, schema=schema,
                      verified_indexes=verified, index_verify_note=note)


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
        out += "## Placeholder Substitution (synthetic values)\n\n"
        out += ("> Placeholders have been replaced with synthetic values to generate "
                "an execution plan. **Plan shape is reliable; row counts and "
                "selectivity estimates are approximate.**\n")
        out += "> For precise analysis, re-run with `--bind` to supply real values.\n\n"
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
    }


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(prog="sqltune.py",
                                 description="One-shot SQL tuning evidence + hypopg index verification")
    ap.add_argument("sql_id", nargs="?", help="unique_sql_id (integer, may be negative)")
    ap.add_argument("-c", "--conn", required=True, help="connection name")
    ap.add_argument("--sql-stdin", action="store_true", help="read SQL text from stdin")
    ap.add_argument("--bind", action="append", default=[],
                    help="bind value for placeholder (repeatable, positional order)")
    ap.add_argument("--analyze", action="store_true",
                    help="EXPLAIN ANALYZE (executes the SQL; DML wrapped in rollback)")
    ap.add_argument("--format", choices=["markdown", "json"], default="markdown")
    ap.add_argument("--timeout", type=int, default=30, help="statement timeout (s)")
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

    try:
        runner = access.for_conn(args.conn)
        # 会话在此显式索取：拿不到就当场停，绝不降级成「没有计划的证据包」。
        # read_only 与迁移前一致 —— --analyze 要真执行 SQL（DML 包在回滚事务里）。
        db = access.session_for(args.conn, read_only=not args.analyze)
    except access.SessionUnavailable as exc:
        print(f"error: {exc}\n{_SESSION_REQUIRED}", file=sys.stderr)
        return 2
    except (common.ConfigError, common.CredentialError, common.DBError,
            access.AccessError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    try:
        db.set_statement_timeout(args.timeout)
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
    # access.QueryError 归一了两条路径的取数失败（中间件 GrmpError / 直连
    # DBError），skill 只认这一个类型；common.DBError 仍要留着 —— 会话那条口子
    # 不经过 runner，报的还是原始的 DBError。
    # ColumnError / ParamError 刻意不接：那是脚本定义缺陷，必须响亮失败。
    except (ValueError, KeyError, common.DBError, access.QueryError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
