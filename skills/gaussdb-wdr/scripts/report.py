"""Render the WDR evidence pack (the `wdr collect` output) — port of report.go.

Fixed ## sections so the skill can parse it deterministically.
"""
from __future__ import annotations

import json

import render
from model import Evidence


def render_evidence_json(ev: Evidence) -> str:
    return json.dumps(ev.to_dict(), ensure_ascii=False, indent=2)


def render_evidence(ev: Evidence) -> str:
    if ev.target:
        out = f"# WDR Evidence — {ev.conn} ({ev.target})\n\n"
    else:
        out = f"# WDR Evidence — {ev.conn}\n\n"
    out += f"总体状态：{ev.overall.label()}\n\n"

    w = ev.window
    out += "## Report Window\n\n"
    out += (f"snap {w.begin_id} ({w.begin_ts}) → snap {w.end_id} ({w.end_ts})，"
            f"时长 {w.duration_min} 分钟，scope={w.scope} node={w.node}，"
            f"enable_wdr_snapshot={'on' if w.wdr_enabled else 'off'}\n\n")

    for d in ev.dims:
        out += f"## {d.dimension}\n\n"
        if not d.available:
            out += f"> 不可用：{d.note}\n\n"
            continue
        if d.headline:
            out += f"{d.headline}\n\n"
        if d.headers and d.rows:
            out += render.table(d.headers, d.rows)
            out += "\n"

    out += "## Deterministic Findings\n\n"
    if not ev.findings:
        out += "无（所有维度未越阈值）。\n\n"
    else:
        rows = [[f.severity.label(), f.dimension, f.code, f.metric, f.value,
                 f.threshold, f.evidence, f.sql_id] for f in ev.findings]
        out += render.table(["严重度", "维度", "Code", "指标", "值", "阈值", "证据", "sql_id"], rows)
        out += "\n"

    out += "## Collection Notes\n\n"
    any_note = False
    for d in ev.dims:
        if not d.available:
            out += f"- {d.dimension}：降级（{d.note}）\n"
            any_note = True
    if ev.native.note:
        out += f"- 原生 WDR：{ev.native.note}\n"
        any_note = True
    elif ev.native.generated:
        out += f"- 原生 WDR：已留底 {ev.native.saved_path}（{ev.native.bytes} 字节）\n"
        any_note = True
    if not any_note:
        out += "- 全部维度采集成功。\n"
    return out
