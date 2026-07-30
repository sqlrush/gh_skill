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
import render  # noqa: E402
from evidence import Evidence, collect, evidence_report  # noqa: E402
from hypoindex import MIN_SPEEDUP, IndexCandidate, verify_indexes  # noqa: E402
from placeholder import SubstituteResult, substitute  # noqa: E402
from sqlfetch import sql_fetch  # noqa: E402


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


def _tune(db, *, original_sql: str, binds: list[str], do_analyze: bool,
          sql_id: str = "", source: str = "", schema: str = "") -> TuneResult:
    sub = substitute(original_sql, binds)
    ev = collect(db, sub.sql, do_analyze)

    verified: list[IndexCandidate] = []
    note = ""
    try:
        verified = verify_indexes(db, sub.sql, MIN_SPEEDUP)
    except Exception as exc:  # best-effort: degrade gracefully (non-fatal)
        note = ("索引验证不可用（OpenGauss hypopg/gs_index_advise 未启用或不支持）："
                + str(exc))

    return TuneResult(original_sql=original_sql, substitution=sub, evidence=ev,
                      sql_id=sql_id, source=source, schema=schema,
                      verified_indexes=verified, index_verify_note=note)


def tune_by_id(db, raw_id: str, binds: list[str], do_analyze: bool) -> TuneResult:
    fr = sql_fetch(db, raw_id)
    if fr.truncated:
        raise ValueError(
            f"sql id {raw_id} 的文本被 openGauss 截断（{fr.truncated_reason}）——"
            f"track_activity_query_size 限制了留存长度，数据库里就没有完整 SQL。"
            f"无法对半截 SQL 做调优。请改用 `--sql-stdin` 传入完整 SQL 文本"
            f"（或调大 track_activity_query_size 并让该 SQL 重新执行后再按 id 取）。")
    return _tune(db, original_sql=fr.sql, binds=binds, do_analyze=do_analyze,
                 sql_id=fr.sql_id, source=fr.source, schema=fr.schema)


def tune_by_sql(db, sql_text: str, binds: list[str], do_analyze: bool) -> TuneResult:
    return _tune(db, original_sql=sql_text, binds=binds, do_analyze=do_analyze)


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
        db = common.Database.connect(args.conn, read_only=not args.analyze)
    except (common.ConfigError, common.CredentialError, common.DBError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    try:
        db.set_statement_timeout(args.timeout)
        if has_id:
            tr = tune_by_id(db, args.sql_id, args.bind, args.analyze)
        else:
            tr = tune_by_sql(db, sql_text, args.bind, args.analyze)

        if len(args.bind) > tr.substitution.placeholders:
            print(f"warning: {len(args.bind)} --bind value(s) given but only "
                  f"{tr.substitution.placeholders} placeholder(s) found; extras ignored",
                  file=sys.stderr)

        if args.format == "json":
            print(json.dumps(_to_jsonable(tr), ensure_ascii=False, indent=2))
        else:
            print(sqltune_report(tr), end="")
        return 0
    except (ValueError, common.DBError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
