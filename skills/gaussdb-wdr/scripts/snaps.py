"""`wdr snaps` — list WDR snapshots + preflight enable_wdr_snapshot.

Port of internal/probe/wdr/snaps.go. Returns an ERROR (not a degraded report)
when WDR is off or fewer than 2 snapshots exist — the toolkit never creates
snapshots; it tells the user to enable WDR / create one themselves.
"""
from __future__ import annotations

import render
from util import i64

import common  # resolved on sys.path by the entry script

import pathlib
import sys

_HERE = pathlib.Path(__file__).resolve()

for parent in _HERE.parents:
    if (parent / "common" / "sql.py").exists():
        sys.path.insert(0, str(parent))
        break

# SQL 已迁到 scripts/registry/wdr/ —— 两条路径共用同一份定义
# 取数失败只认这一个类型。换一种数据库访问方式时，改的是访问模块，
# 不是这里 —— 详见 common/grmp/errors.py。
from common import access  # noqa: E402
# 结果值全是字符串：bool("f") 是 True、int("3704.0") 会抛异常。
# 类型还原一律走这里，不用裸 int()/float()/bool()。
from common.grmp.values import as_bool, as_float, as_int  # noqa: E402


def snaps(runner, limit: int) -> str:
    if limit <= 0:
        limit = 20
    try:
        rows = runner.run("wdr.wdr_enabled")
        enabled = rows[0]["enable_wdr_snapshot"] if rows else ""
    except access.QueryError as exc:
        raise common.DBError(f"读取 enable_wdr_snapshot 失败：{exc}")
    if str(enabled or "").strip().lower() != "on":
        raise common.DBError(
            "WDR 未开启（enable_wdr_snapshot=off）。请由 DBA 执行 "
            "`ALTER SYSTEM SET enable_wdr_snapshot=on`（需 reload/重启）并等待自动快照，"
            "或自行 `SELECT create_wdr_snapshot();`。本工具只读、不代为开启或创建。")

    try:
        rows = runner.run("wdr.snapshots", {"limit": int(limit)})
    except access.QueryError as exc:
        raise common.DBError(f"查询 snapshot.snapshot 失败：{exc}")

    snaps_list = [(as_int(r["snapshot_id"]), r["start_ts"], r["end_ts"],
                   as_int(r["dur_min"])) for r in rows]
    if len(snaps_list) < 2:
        raise common.DBError(
            f"可用快照不足（{len(snaps_list)} 个）：WDR 报告至少需要两个快照围出窗口。"
            "请等待下一个自动快照间隔，或由 DBA 自行 `SELECT create_wdr_snapshot();`。")

    tbl = [[i64(s[0]), s[1], s[2], i64(s[3])] for s in snaps_list]
    out = f"# WDR Snapshots（enable_wdr_snapshot={enabled}）\n\n"
    out += render.table(["snapshot_id", "start_ts", "end_ts", "dur_min"], tbl)
    # snaps_list[0] is newest (DESC); suggest the two most-recent consecutive.
    out += f"\n建议窗口：`--begin {snaps_list[1][0]} --end {snaps_list[0][0]}`（最近两个快照）。\n"
    return out
