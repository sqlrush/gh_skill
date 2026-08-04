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
        
# SQL 已迁到 scripts/registry/topsql/top_sql.yaml —— 两条路径共用同一份定义

import common  # noqa: E402
from common import access  # noqa: E402
# 结果值全是字符串：bool("f") 是 True、int("3704.0") 会抛异常。
# 类型还原一律走这里，不用裸 int()/float()/bool()。
from common.grmp.values import as_bool, as_float, as_int  # noqa: E402
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


TOP_SQL_SCRIPT = "topsql.top_sql"

# 脚本 SELECT 列表的列名。用名字取值而不是下标：列序变了会当场 KeyError，
# 而不是安静地把 avg_ms 当成 total_sec。
TOP_SQL_COLUMNS = (
    "unique_sql_id", "query", "calls", "total_sec", "avg_ms", "rows",
)


def top_sql(runner, by: str, limit: int) -> list[StmtRow]:
    """经统一入口取数。走中间件还是直连由连接的 driver 决定，这里不感知。"""
    order = _SORT_COLS.get(by)
    if order is None:
        raise ValueError(f"--by {by!r}: must be one of {SORT_KEYS}")
    # order 落在 ORDER BY 位，类型校验对标识符位无效 —— 取值只能来自
    # 上面那张白名单，绝不能直接来自用户输入。
    rows = runner.run(TOP_SQL_SCRIPT, {"order": order, "limit": int(limit)})
    return [
        StmtRow(r["unique_sql_id"], r["query"], as_int(r["calls"]),
                as_float(r["total_sec"]), as_float(r["avg_ms"]), as_int(r["rows"]))
        for r in rows
    ]


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
    ap.add_argument("--timeout", type=int, default=None)
    args = ap.parse_args(argv)

    try:
        runner = access.for_conn(args.conn, timeout=args.timeout)
    except (common.ConfigError, common.CredentialError, access.AccessError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    try:
        rows = top_sql(runner, args.by, args.limit)
        if args.format == "json":
            print(json.dumps([r.__dict__ for r in rows], ensure_ascii=False, indent=2))
        else:
            print(stmt_table("Top SQL by " + args.by, rows), end="")
        return 0
    except (ValueError, KeyError, common.DBError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    except Exception as exc:          # 渲染/协议层的失败也要清楚报出来
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
