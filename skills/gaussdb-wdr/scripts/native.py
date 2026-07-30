"""Snapshot-window loading + best-effort native WDR留底 — port of native.go.

Snapshot ids are validated ints from --begin/--end, inlined into the SQL exactly
as the Go version did (fmt.Sprintf with %d). node/scope are escaped defensively.
"""
from __future__ import annotations

from model import NativeInfo, Options, Window
from util import summarize_err

import common  # resolved on sys.path by the entry script

import pathlib
import sys

_HERE = pathlib.Path(__file__).resolve()

for parent in _HERE.parents:
    if (parent / "common" / "sql.py").exists():
        sys.path.insert(0, str(parent))
        break

from common.sql import skill_wdr_009


def sql_literal(s: str) -> str:
    """Escape a single-quoted SQL string literal."""
    return (s or "").replace("'", "''")


def load_window(db, opt: Options) -> Window:
    """Validate the snapshot pair, resolve node name, read wdr-enabled GUC, and
    fetch the window's begin/end times + duration."""
    if opt.begin <= 0 or opt.end <= opt.begin:
        raise common.DBError(
            f"无效窗口：begin={opt.begin} end={opt.end}（end 必须 > begin > 0）")
    w = Window(begin_id=opt.begin, end_id=opt.end, scope=opt.scope or "node")

    try:
        enabled = db.scalar("SHOW enable_wdr_snapshot")
        w.wdr_enabled = str(enabled or "").strip().lower() == "on"
    except common.DBError:
        pass

    w.node = opt.node
    if not w.node:
        try:
            node = db.scalar("SHOW pgxc_node_name")
            w.node = str(node or "").strip()
        except common.DBError:
            pass

    q = skill_wdr_009.format(begin=int(opt.begin), end=opt.end)
    try:
        _, rows = db.query(q)
    except common.DBError as exc:
        raise common.DBError(
            f"加载快照窗口失败（snap {opt.begin}/{opt.end} 是否存在？run: wdr snaps）：{exc}")
    if not rows:
        raise common.DBError(
            f"加载快照窗口失败：snap {opt.begin}/{opt.end} 不存在（run: wdr snaps 查看可用快照）")
    w.begin_ts, w.end_ts = rows[0][0], rows[0][1]
    w.duration_min = int(rows[0][2] or 0)
    return w


def generate_native(db, opt: Options, w: Window) -> NativeInfo:
    """Call the native generate_wdr_report (read-only) for留底/审计. Failure is
    non-fatal — deterministic findings come from the self-computed delta."""
    q = (f"SELECT generate_wdr_report({int(opt.begin)}, {int(opt.end)}, 'all', "
         f"'{sql_literal(w.scope)}', '{sql_literal(w.node)}')")
    try:
        _, rows = db.query(q)
    except common.DBError as exc:
        return NativeInfo(generated=False,
                          note="generate_wdr_report 不可用或失败：" + summarize_err(exc))
    body = "".join((str(r[0]) + "\n") for r in rows if r[0] is not None)
    ni = NativeInfo(generated=True, bytes=len(body.encode("utf-8")))
    if opt.save_html:
        try:
            with open(opt.save_html, "w", encoding="utf-8") as fh:
                fh.write(body)
            ni.saved_path = opt.save_html
        except OSError as exc:
            ni.note = "原生报告已生成但落盘失败：" + summarize_err(exc)
    return ni
