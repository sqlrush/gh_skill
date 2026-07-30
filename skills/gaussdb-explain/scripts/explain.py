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
import re
import sys
from dataclasses import dataclass
from typing import Optional

_HERE = pathlib.Path(__file__).resolve()
sys.path.insert(0, str(_HERE.parent))          # sibling modules
for _anc in _HERE.parents:                      # locate common/ (repo root or install dir)
    if (_anc / "common" / "__init__.py").exists():
        sys.path.insert(0, str(_anc))
        break

import common  # noqa: E402
import render  # noqa: E402

_DML_RE = re.compile(r"(?i)^\s*(insert|update|delete|merge)\b")
_CTE_RE = re.compile(r"(?i)^\s*with\b")
_CTE_DML_RE = re.compile(r"(?i)\b(insert|update|delete|merge)\b")


@dataclass(frozen=True)
class Finding:
    kind: str
    severity: str
    detail: str
    advice: str


def is_dml(sql_text: str) -> bool:
    if _DML_RE.search(sql_text):
        return True
    return bool(_CTE_RE.search(sql_text) and _CTE_DML_RE.search(sql_text))


def explain(db, sql_text: str, analyze: bool) -> str:
    stmt = (f"EXPLAIN (ANALYZE {str(analyze).lower()}, "
            f"BUFFERS {str(analyze).lower()}, FORMAT TEXT) {sql_text}")
    if analyze and is_dml(sql_text):
        _, rows = db.query_in_rollback(stmt)
    else:
        _, rows = db.query(stmt)
    return "\n".join(r[0] for r in rows)


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


def explain_report(sql_text: str, plan: str, findings: list[Finding]) -> str:
    out = ("## SQL\n\n" + render.code_block("sql", render.truncate(sql_text, 2000)) +
           "\n## Execution Plan\n\n" + render.code_block("", plan))
    if not findings:
        return out + "\n## Findings\n\nNo deterministic risk patterns detected.\n"
    out += "\n## Findings\n\n"
    for f in findings:
        out += f"- **[{f.severity}] {f.kind}**: {f.detail} — {f.advice}\n"
    return out


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(prog="explain.py",
                                 description="EXPLAIN a statement with risk findings")
    ap.add_argument("-c", "--conn", required=True, help="connection name")
    ap.add_argument("--sql-stdin", action="store_true", required=True,
                    help="read SQL text from stdin")
    ap.add_argument("--analyze", action="store_true",
                    help="EXPLAIN ANALYZE (executes; DML wrapped in rollback)")
    ap.add_argument("--format", choices=["markdown", "json"], default="markdown")
    ap.add_argument("--timeout", type=int, default=30)
    args = ap.parse_args(argv)

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
        plan = explain(db, sql_text, args.analyze)
        findings = scan_plan(plan)
        if args.format == "json":
            print(json.dumps({"sql": sql_text, "plan": plan,
                              "findings": [f.__dict__ for f in findings]},
                             ensure_ascii=False, indent=2))
        else:
            print(explain_report(sql_text, plan, findings), end="")
        return 0
    except (ValueError, common.DBError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
