"""Render the health evidence pack — port of internal/probe/health/report.go.

Fixed ## sections so the skill can parse the output deterministically.
"""
from __future__ import annotations

import json

import render
from model import HealthEvidence


def render_health_json(ev: HealthEvidence) -> str:
    return json.dumps(ev.to_dict(), ensure_ascii=False, indent=2)


def render_health(ev: HealthEvidence) -> str:
    if ev.target:
        out = f"# Health Evidence — {ev.conn} ({ev.target})\n\n"
    else:
        out = f"# Health Evidence — {ev.conn}\n\n"
    out += f"总体状态：{ev.overall.label()}\n\n"

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
        rows = [[f.severity.label(), f.dimension, f.code, f.metric, f.value, f.threshold, f.evidence]
                for f in ev.findings]
        out += render.table(["严重度", "维度", "Code", "指标", "值", "阈值", "证据"], rows)
        out += "\n"

    out += "## Collection Notes\n\n"
    any_degraded = False
    for d in ev.dims:
        if not d.available:
            out += f"- {d.dimension}：降级（{d.note}）\n"
            any_degraded = True
    if not any_degraded:
        out += "- 全部维度采集成功。\n"
    return out
