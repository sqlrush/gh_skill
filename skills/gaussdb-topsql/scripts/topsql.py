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
import time
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
from common import cli  # noqa: E402
from common import reports  # noqa: E402
# 结果值全是字符串：bool("f") 是 True、int("3704.0") 会抛异常。
# 类型还原一律走这里，不用裸 int()/float()/bool()。
from common.grmp.values import as_bool, as_float, as_int  # noqa: E402
import delta as delta_mod  # noqa: E402
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


def _sample(runner, at: str) -> "delta_mod.Sample":
    """采一刻的累计值。**按累计耗时取宽一些**(SAMPLE_WIDTH),增量再在这批里算 ——
    累计榜靠后的语句完全可能是本窗口的冲高元凶。"""
    rows = top_sql(runner, "time", delta_mod.SAMPLE_WIDTH)
    return delta_mod.to_sample(at, rows)


def _now() -> str:
    """采样时刻,用**本容器的时钟**(UTC)。

    两次采样都由同一个进程打时间戳,所以相隔多久是准的 —— 这正是增量需要的。
    没有去查数据库的 now():白名单里没有「只返回当前时间」的脚本,而为了一个时间戳
    去加一条下发脚本、让客户重导,不值得。报告里会写明这是采样时刻。
    """
    return reports.utc_stamp()


def _stats_window(runner) -> str:
    """累计值到底覆盖了多长时间 —— 解释一切累计值的前提。

    这是「累计拉平」问题的另一半:就算不算增量,至少要告诉用户「这个榜是过去 23 天的总量」,
    他才知道十分钟的冲高会被摊成什么样。脚本 health.stats_window 早在白名单里。
    """
    try:
        rows = runner.run("health.stats_window", {})
        if not rows:
            return ""
        r = rows[0]
        reset, secs = str(r.get("stats_reset") or ""), as_float(r.get("window_seconds") or -1)
        if reset == "never" or secs < 0:
            return "累计值的起算时间未知(统计从未重置过)"
        if secs >= 86400:
            span = "%.1f 天" % (secs / 86400.0)
        elif secs >= 3600:
            span = "%.1f 小时" % (secs / 3600.0)
        else:
            span = "%d 秒" % int(secs)
        return "下表是**累计值**,自 %s 起算,已累计 %s" % (reset, span)
    except Exception:          # noqa: BLE001 —— 取不到不影响主结果
        return ""


def delta_table(title: str, res: "delta_mod.DeltaResult", by: str) -> str:
    """增量结果的表格。**表头写明是增量**,不让人误读成累计值。"""
    if res.reset:
        return "## %s\n\n%s\n" % (title, res.note)
    if not res.rows:
        return ("## %s\n\n这个窗口里没有语句产生新的耗时(增量为零)。%s\n"
                % (title, res.note))
    body = [[str(i + 1), r.sql_id, str(r.calls), "%.2f" % r.avg_ms, "%.2f" % r.total_sec,
             str(r.rows), ("新" if r.is_new else ""), render.truncate(r.query, 100)]
            for i, r in enumerate(res.rows)]
    return ("## " + title + "\n\n" +
            render.table(["#", "SQL_ID", "窗口内调用", "窗口内平均(ms)", "窗口内耗时(s)",
                          "窗口内返回行", "窗口内新出现", "QUERY"], body) +
            "\n" + res.note + "\n" +
            "\nNext: `python3 ../../gaussdb-sqlfetch/scripts/sqlfetch.py -c <conn> <SQL_ID>` "
            "to get the full SQL text.\n")


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


def _run_delta(runner, args, conn_name: str) -> int:
    """增量模式。两条路:现场采两次(--window)或与上次基线相减(--since-last)。

    **存档与累计模式分开**(name 带 `.delta`):大盘按维度页签取最近一份,如果增量报告覆盖了
    累计报告的文件名,历史曲线会把两种口径的数混在一条线上 —— 那种图比没有图更糟。
    """
    base_dir = reports.reports_dir()
    inst_key = reports.instance_key(conn_name)
    bpath = delta_mod.baseline_path(base_dir, inst_key)

    if args.window:
        try:
            span = delta_mod.parse_window(args.window)
        except ValueError as exc:
            print("error: %s" % exc, file=sys.stderr)
            return 2
        before = _sample(runner, _now())
        print("已采第一次样,等 %d 秒后再采一次 …" % span, file=sys.stderr)
        time.sleep(span)
        after = _sample(runner, _now())
        label = "最近 %s" % args.window
    else:
        before, why = delta_mod.load_baseline(bpath)
        after = _sample(runner, _now())
        if before is None:
            # 第一次跑:留下基线,**明确告诉用户这次给不出增量**,不要悄悄退回累计榜
            delta_mod.save_baseline(bpath, after)
            print("无法给出增量:%s。\n已留下基线,下次用 `--since-last` 就能算出"
                  "「距上次」的增量;要立刻看最近一段时间,用 `--window 10m`。" % why)
            return 0
        span = max(0.0, _span_seconds(before.at, after.at))
        label = "距上次采样(%s)" % (before.at or "未知时刻")

    try:
        res = delta_mod.diff(before, after, span, by=args.by, limit=args.limit)
    except ValueError as exc:
        print("error: %s" % exc, file=sys.stderr)
        return 2
    # 不管这次算不算得出增量,都把最新采样留成基线 —— 下次才有东西可减
    delta_mod.save_baseline(bpath, after)

    payload = {"conn": conn_name, "by": args.by, "limit": args.limit,
               "mode": "delta", "window_s": int(res.span_s), "reset": res.reset,
               "sampled_at": after.at, "baseline_at": before.at,
               "rows": [r._asdict() for r in res.rows]}
    if args.format == "json":
        print(json.dumps(payload["rows"], ensure_ascii=False, indent=2))
    else:
        print(delta_table("Top SQL(%s,按%s)" % (label, args.by), res, args.by), end="")
    saved = reports.archive("topsql", payload,
                            name="%s.%s.delta" % (reports.utc_stamp(), args.by),
                            instance=conn_name)
    reports.announce("topsql", saved, conn_name, by=args.by)
    return 0


def _span_seconds(a: str, b: str) -> float:
    """两个 utc_stamp(`YYYYMMDDTHHMMSSZ`)之间的秒数。解析不了就返回 0,由调用方照实呈现。"""
    import datetime as _dt
    fmt = "%Y%m%dT%H%M%SZ"
    try:
        return (_dt.datetime.strptime(b, fmt) - _dt.datetime.strptime(a, fmt)).total_seconds()
    except (ValueError, TypeError):
        return 0.0


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(prog="topsql.py",
                                 description="Top resource-consuming statements")
    ap.add_argument("-c", "--conn", default="", help="连接名（省略则用 gaussdb-login 建立的会话）")
    cli.add_session_arg(ap)
    ap.add_argument("--by", choices=SORT_KEYS, default="time", help="sort key")
    ap.add_argument("--limit", type=int, default=10, help="max rows")
    ap.add_argument("--format", choices=["markdown", "json"], default="markdown")
    ap.add_argument("--timeout", type=int, default=None)
    # 不带这两个开关 = 原来的行为(累计榜)。带了就是**窗口增量**,回答「最近这段时间谁在冲高」。
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--window", default="", metavar="10m",
                   help="现场采两次、相隔这么久,给出这段时间的增量(10s~6h)。命令会等满这段时间")
    g.add_argument("--since-last", action="store_true",
                   help="与上次运行留下的基线相减,给出「距上次」的增量(几乎不耗时)")
    args = ap.parse_args(argv)
    cli.apply_session_arg(args)

    try:
        runner = access.for_conn(args.conn, timeout=args.timeout)
    except (common.ConfigError, common.CredentialError, access.AccessError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    try:
        conn_name = common.config.resolved_name(args.conn)
        if args.window or args.since_last:
            return _run_delta(runner, args, conn_name)
        rows = top_sql(runner, args.by, args.limit)
        # conn 一定要带:没有它报告没法归到实例目录,大盘也没法显示这是哪个库
        payload = {"conn": conn_name, "by": args.by, "limit": args.limit,
                   "mode": "cumulative",
                   "rows": [r.__dict__ for r in rows]}
        if args.format == "json":
            print(json.dumps(payload["rows"], ensure_ascii=False, indent=2))   # stdout 形状不变
        else:
            # 累计榜必须写明「累计了多久」—— 否则十分钟的冲高被几十天的总量摊平,
            # 而看榜的人完全不知道自己看的是多长时间的总量。
            win = _stats_window(runner)
            print(stmt_table("Top SQL by " + args.by, rows), end="")
            if win:
                print("\n> %s。要看最近一段时间的变化,用 `--window 10m` 或 `--since-last`。\n" % win)
        # 存档带 by:大盘按维度页签找最近一份;失败只 warn,不影响输出与退出码
        saved = reports.archive("topsql", payload, name="%s.%s" % (reports.utc_stamp(), args.by),
                                instance=payload["conn"])
        reports.announce("topsql", saved, payload["conn"], by=args.by)
        return 0
    except (ValueError, KeyError, common.DBError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    except Exception as exc:          # 渲染/协议层的失败也要清楚报出来
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
