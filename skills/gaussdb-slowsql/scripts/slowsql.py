#!/usr/bin/env python3
"""slowsql — list statements slower than a threshold (avg ms).

Port of internal/probe/slowsql.go + internal/cli/slowsql.go. Reads
dbe_perf.statement aggregates; slowest first. cpu_sec is captured (JSON) to
expose the DB-time trap (slow-but-low-CPU = contention, not CPU-bound work).

Usage:
    slowsql.py -c <conn> [--threshold 1000] [--limit 20] [--format json]
"""
from __future__ import annotations

import argparse
import json
import pathlib
import sys
from datetime import datetime, timedelta
from dataclasses import dataclass
from typing import Optional
import os
import csv

_HERE = pathlib.Path(__file__).resolve()
ROOT_DIR = _HERE.parents[3]
sys.path.insert(0, str(_HERE.parent))  # sibling modules
for _anc in _HERE.parents:  # locate common/ (repo root or install dir)
    if (_anc / "common" / "__init__.py").exists():
        sys.path.insert(0, str(_anc))
        break

for parent in _HERE.parents:
    if (parent / "common" / "sql.py").exists():
        sys.path.insert(0, str(parent))
        break

import common  # noqa: E402
from common import access  # noqa: E402
# 结果值全是字符串：bool("f") 是 True、int("3704.0") 会抛异常。
# 类型还原一律走这里，不用裸 int()/float()/bool()。
from common.grmp.values import as_bool, as_float, as_int  # noqa: E402
import render  # noqa: E402

SLOWSQL_MAX_ROWS = 10
@dataclass(frozen=True)
class StmtRow:
    sql_id: str
    query: str
    calls: int
    avg_ms: float
    total_sec: float
    cpu_sec: float
    rows: int


# 脚本 scripts/registry/slowsql/slow_sql.yaml 的 SELECT 列序。
# 下游按 r[0]..r[6] 取值，两者必须对齐。
SLOW_SQL_COLUMNS = (
    "unique_sql_id", "query", "calls", "avg_ms", "total_sec", "cpu_sec", "rows",
)

SLOW_SQL_SCRIPT = "slowsql.slow_sql"


def fetch_rows(runner, threshold_ms: int, limit: int, begin_time: str) -> list[tuple]:
    """经统一入口取数，摊成位置元组。

    按**逻辑名**调用而不是脚本 ID：ID 是环境相关数据，硬编码后换环境
    仍然存在但指向另一条脚本，表现为执行成功、结果无关、不报错。

    两条路径（中间件 / 直连）返回的都是全字符串化的行字典，所以这里
    直接用 r[col] 而不是 r.get(col)：脚本 SELECT 列变了而这里没跟上时
    要当场 KeyError，用 get 兜底会让缺失列静默变成 None，报错点被推迟到
    格式化环节，排查方向被带偏。
    """
    return [
        tuple(row[col] for col in SLOW_SQL_COLUMNS)
        for row in runner.run(
            SLOW_SQL_SCRIPT,
            {
                "threshold_ms": int(threshold_ms),
                "limit": int(limit),
                "begin_time": str(begin_time),
            },
        )
    ]


def slow_sql(runner, threshold_ms: int, limit: int, begin_time: str, export: bool) -> list[StmtRow]:

    rows = fetch_rows(runner, threshold_ms, limit, begin_time)

    # === 新增逻辑：rows > 20 时保存为 CSV 文件 ===
    if len(rows) > SLOWSQL_MAX_ROWS or export:
        # 1. 定义 CSV 文件路径（可根据需要调整）
        csv_filename = _HERE.parents[3] / "csv" / f"slow_sql_export_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"

        # 2. 定义 CSV 表头（根据你查询的字段调整）
        headers = ['unique_sql_id', 'query', 'calls', 'avg_ms', 'total_sec',
                   'cpu_sec', 'rows']

        # 3. 写入 CSV 文件
        with open(csv_filename, 'w', newline='', encoding='utf-8') as f:
            writer = csv.writer(f)
            writer.writerow(headers)
            for r in rows:
                writer.writerow([r[0], r[1], r[2], r[3], r[4], r[5], r[6]])
        print(f"数据已导出到: {csv_filename} (共 {len(rows)} 行)")
        last_index = min(SLOWSQL_MAX_ROWS, len(rows), 3)
        return [StmtRow(r[0], r[1], as_int(r[2]), as_float(r[3]), as_float(r[4]),
                        as_float(r[5]), as_int(r[6])) for r in rows[:last_index]]
    else:
        return [StmtRow(r[0], r[1], as_int(r[2]), as_float(r[3]), as_float(r[4]),
                      as_float(r[5]), as_int(r[6])) for r in rows]


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
    ap = argparse.ArgumentParser(prog="slowsql.py",
                                 description="List statements slower than --threshold (avg ms)")
    ap.add_argument("-c", "--conn", required=True, help="connection name")
    seven_days_ago = datetime.now() - timedelta(days=7)
    begin_time_str = seven_days_ago.strftime('%Y-%m-%d %H:%M:%S')
    ap.add_argument("--threshold", type=int, default=1000, help="avg elapsed threshold (ms)")
    ap.add_argument("--limit", type=int, default=20, help="max rows")
    ap.add_argument("--begin_time", type=str, default=begin_time_str, help="execution begin time")
    ap.add_argument("--export", type=bool, default=False, help="export results to csv")
    ap.add_argument("--format", choices=["markdown", "json"], default="markdown")
    # 仅直连路径生效：GRMP 协议没有超时字段，走中间件时超时由服务端决定
    ap.add_argument("--timeout", type=int, default=30)
    args = ap.parse_args(argv)
    try:
        runner = access.for_conn(args.conn)
    except (common.ConfigError, common.CredentialError, access.AccessError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    try:
        rows = slow_sql(runner, args.threshold, args.limit, args.begin_time, args.export)
        if args.format == "json":
            print(json.dumps([r.__dict__ for r in rows], ensure_ascii=False, indent=2))
        else:
            print(stmt_table("Slow SQL", rows), end="")
        return 0
    except (ValueError, KeyError, common.DBError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    except Exception as exc:      # 渲染/协议层的失败也要清楚地报出来
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
