#!/usr/bin/env python3
"""topsql — top resource-consuming statements (no threshold).

Port of internal/probe/topsql.go + internal/cli/topsql.go. Ranks
dbe_perf.statement by a whitelisted sort key (--by).

Usage:
    topsql.py -c <conn> [--by time|avg|calls|reads|rows] [--limit 10] [--format json]
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
        
from common.sql import skill_topsql_001        

import common  # noqa: E402
import render  # noqa: E402

# Whitelisted --by values; ORDER BY clause is injected, so it MUST come from
# this map only (never from user input directly).
_SORT_COLS = {
    "time": "total_elapse_time DESC",
    "avg": "total_elapse_time/NULLIF(n_calls,0) DESC",
    "calls": "n_calls DESC",
    "reads": "n_blocks_hit + n_blocks_fetched DESC",
    "rows": "n_returned_rows DESC",
}
SORT_KEYS = ["time", "avg", "calls", "reads", "rows"]


@dataclass(frozen=True)
class StmtRow:
    sql_id: str
    query: str
    calls: int
    total_sec: float
    avg_ms: float
    rows: int


def top_sql(db, by: str, limit: int) -> list[StmtRow]:
    order = _SORT_COLS.get(by)
    if order is None:
        raise ValueError(f"--by {by!r}: must be one of {SORT_KEYS}")
    q = skill_topsql_001.format(order=order,limit=int(limit))
    _, rows = db.query(q)
    return [StmtRow(r[0], r[1], int(r[2]), float(r[3]), float(r[4]), int(r[5]))
            for r in rows]


def stmt_table(title: str, rows: list[StmtRow]) -> str:
    if not rows:
        return (f"## {title}\n\nNo matching statements. "
                f"Check `enable_stmt_track` / lower --threshold.\n")
    body = [[str(i + 1), r.sql_id, str(r.calls), f"{r.avg_ms:.2f}",
             f"{r.total_sec:.2f}", str(r.rows), render.truncate(r.query, 100)]
            for i, r in enumerate(rows)]
    return ("## " + title + "\n\n" +
            render.table(["#", "SQL_ID", "CALLS", "AVG_MS", "TOTAL_S", "ROWS", "QUERY"], body) +
            "\nNext: `python3 ../../gaussdb-sqlfetch/scripts/sqlfetch.py -c <conn> <SQL_ID>` "
            "to get the full SQL text.\n")


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(prog="topsql.py",
                                 description="Top resource-consuming statements")
    ap.add_argument("-c", "--conn", required=True, help="connection name")
    ap.add_argument("--by", choices=SORT_KEYS, default="time", help="sort key")
    ap.add_argument("--limit", type=int, default=10, help="max rows")
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
        rows = top_sql(db, args.by, args.limit)
        if args.format == "json":
            print(json.dumps([r.__dict__ for r in rows], ensure_ascii=False, indent=2))
        else:
            print(stmt_table("Top SQL by " + args.by, rows), end="")
        return 0
    except (ValueError, common.DBError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
