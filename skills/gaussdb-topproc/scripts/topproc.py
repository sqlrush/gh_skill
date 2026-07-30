#!/usr/bin/env python3
"""topproc — top resource-consuming stored procedures/functions.

Port of internal/probe/topproc.go + internal/cli/topproc.go. Ranks
pg_stat_user_functions (function-level cumulative stats) by a whitelisted
sort key (--by). Degrades with a note when the view is empty (typically
track_functions=none).

Usage:
    topproc.py -c <conn> [--by time|self|calls] [--limit 20] [--format json]
"""
from __future__ import annotations

import argparse
import json
import pathlib
import sys
from dataclasses import dataclass
from typing import Optional

_HERE = pathlib.Path(__file__).resolve()
sys.path.insert(0, str(_HERE.parent))          # sibling modules
for _anc in _HERE.parents:                      # locate common/ (repo root or install dir)
    if (_anc / "common" / "__init__.py").exists():
        sys.path.insert(0, str(_anc))
        break
        
for parent in _HERE.parents:
    if (parent / "common" / "sql.py").exists():
        sys.path.insert(0, str(parent))
        break
        
from common.sql import skill_topproc_001

import common  # noqa: E402
import render  # noqa: E402

# Whitelisted --by values; ORDER BY clause is injected, so it MUST come from
# this map only (never from user input directly).
_SORT_COLS = {
    "time": "s.total_time DESC",
    "self": "s.self_time DESC",
    "calls": "s.calls DESC",
}
SORT_KEYS = ["time", "self", "calls"]

_EMPTY_NOTE = (
    "无函数级统计：pg_stat_user_functions 为空（多因 track_functions=none，函数级统计关闭）。"
    "用 `SET track_functions='pl'`（或 'all'）后再调用过程重采；或用 gaussdb-topsql 看顶层调用，"
    "以及（track_stmt_stat_level 捕获到的）过程内部语句。"
)


@dataclass(frozen=True)
class ProcStat:
    schema: str
    name: str
    calls: int
    total_ms: float
    self_ms: float


def top_procs(db, by: str, limit: int) -> tuple[list[ProcStat], str]:
    """Rank user functions/procedures; returns (rows, degrade_note)."""
    order = _SORT_COLS.get(by)
    if order is None:
        raise ValueError(f"--by {by!r}: must be one of {SORT_KEYS}")
    q = skill_topproc_001.format(order=order, limit=int(limit))
    _, rows = db.query(q)
    out = [ProcStat(r[0], r[1], int(r[2]), float(r[3]), float(r[4])) for r in rows]
    note = "" if out else _EMPTY_NOTE
    return out, note


def proc_table(by: str, rows: list[ProcStat], note: str) -> str:
    out = f"# Top Procedures by {by}\n\n"
    if not rows:
        return out + "> " + note + "\n"
    body = [[f"{r.schema}.{r.name}", str(r.calls), f"{r.total_ms:.2f}", f"{r.self_ms:.2f}"]
            for r in rows]
    out += render.table(["PROCEDURE", "CALLS", "TOTAL_MS", "SELF_MS"], body)
    out += ("\nNext: 只读诊断 → `gaussdb-procinfo <schema.proc>`；"
            "经验证优化 → `gaussdb-proctune <schema.proc>`。\n")
    return out


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(
        prog="topproc.py",
        description="Top resource-consuming stored procedures/functions")
    ap.add_argument("-c", "--conn", required=True, help="connection name")
    ap.add_argument("--by", choices=SORT_KEYS, default="time", help="sort key")
    ap.add_argument("--limit", type=int, default=20, help="max rows")
    ap.add_argument("--format", choices=["markdown", "json"], default="markdown")
    ap.add_argument("--timeout", type=int, default=30)
    args = ap.parse_args(argv)

    try:
        db = common.Database.connect(args.conn)
    except (common.ConfigError, common.CredentialError, common.DBError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    try:
        db.set_statement_timeout(args.timeout)
        rows, note = top_procs(db, args.by, args.limit)
        if args.format == "json":
            print(json.dumps({"rows": [r.__dict__ for r in rows], "note": note},
                             ensure_ascii=False, indent=2))
        else:
            print(proc_table(args.by, rows, note), end="")
        return 0
    except (ValueError, common.DBError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
