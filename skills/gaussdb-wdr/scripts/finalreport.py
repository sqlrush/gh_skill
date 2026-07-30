"""Render the final WDR report from evidence + interp — port of wdr/render.go.

Runs the mechanical anchoring recheck, builds a paint-agnostic model, then emits
markdown or ANSI. Both emitters consume the same model; differences are color +
table style only.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import render
from ansi import (
    ANSI_BOLD, ANSI_DIM, ANSI_ORANGE, box_table, colorize, severity_color,
)
from interp import Interp, Suggestion
from model import (
    DIM_CACHE, DIM_CHECKPOINT, DIM_DBSTAT, DIM_FILEIO, DIM_LOADPROFILE,
    DIM_TOPSQL, DIM_WAITS, Evidence, Severity, dim_severity,
)
from recheck import Rechecked, recheck
from util import trunc

# Top-down presentation order in 全景分析 (global load → waits → SQL → ...).
_REPORT_ORDER = [DIM_LOADPROFILE, DIM_DBSTAT, DIM_WAITS, DIM_TOPSQL,
                 DIM_CHECKPOINT, DIM_CACHE, DIM_FILEIO]


def _dim_rank(name: str) -> int:
    return _REPORT_ORDER.index(name) if name in _REPORT_ORDER else len(_REPORT_ORDER)


@dataclass
class _MatrixRow:
    dim: str
    sev: Severity
    headline: str


@dataclass
class _FindingView:
    sev: Severity
    code: str
    dim: str
    evidence_line: str
    root_cause: str
    suggestions: list = field(default_factory=list)


@dataclass
class _RawTable:
    title: str
    headline: str
    headers: list
    rows: list


@dataclass
class _ReportModel:
    conn: str = ""
    target: str = ""
    window: object = None
    overall: Severity = Severity.OK
    driver: str = ""
    badge: str = ""
    matrix: list = field(default_factory=list)
    findings: list = field(default_factory=list)
    missing: list = field(default_factory=list)
    unanchored: list = field(default_factory=list)
    unverified: list = field(default_factory=list)
    raw_tables: list = field(default_factory=list)


def format_suggestion(s: Suggestion) -> str:
    out = s.text + "  风险:" + s.risk
    if s.manual:
        out += " [需人工执行]"
    if s.validation is not None and s.validation.status == "verified" and s.validation.evidence:
        out += "  ✅实证 " + s.validation.evidence
    return out


def build_model(ev: Evidence, r: Rechecked) -> _ReportModel:
    m = _ReportModel(conn=ev.conn, target=ev.target, window=ev.window,
                     overall=r.overall, driver=r.driver, badge=r.badge)
    for d in ev.dims:
        m.matrix.append(_MatrixRow(d.dimension, dim_severity(d), d.headline))
    for a in r.anchored:
        fv = _FindingView(
            sev=a.ev.severity, code=a.ev.code, dim=a.ev.dimension,
            evidence_line=f"{a.ev.metric} {a.ev.value} vs 阈值 {a.ev.threshold}（{a.ev.evidence}）",
            root_cause=a.root_cause)
        for s in a.verified:
            fv.suggestions.append(format_suggestion(s))
        m.findings.append(fv)
    for f in r.missing:
        m.missing.append(f"{f.code}（{f.severity.label()}）")
    for inf in r.unanchored:
        m.unanchored.append(inf.code)
    for a in r.anchored:
        for s in a.unverified:
            method = s.validation.method if s.validation is not None else ""
            m.unverified.append(f"{s.text}（{method} 未通过/未验证）")
    # 全景分析：所有可用维度按自顶向下顺序铺开，每维带其 Headline。
    for d in ev.dims:
        if d.available and d.rows:
            m.raw_tables.append(_RawTable(d.dimension, d.headline, d.headers, d.rows))
    m.raw_tables.sort(key=lambda t: _dim_rank(t.title))
    return m


def render_report(ev: Evidence, in_: Interp, fmt: str = "md", no_color: bool = False) -> str:
    r = recheck(ev, in_)
    m = build_model(ev, r)
    if fmt in ("md", ""):
        return _emit_markdown(m)
    if fmt == "ansi":
        return _emit_ansi(m, not no_color)
    raise ValueError(f"--format {fmt!r}: must be md or ansi")


# ---- markdown emitter ----

def _emit_markdown(m: _ReportModel) -> str:
    w = m.window
    if m.target:
        out = f"# WDR 报告 — {m.conn} ({m.target})\n\n"
    else:
        out = f"# WDR 报告 — {m.conn}\n\n"
    out += f"总体状态 {m.overall.label()}  驱动：{m.driver}\n\n"
    out += f"判断校验 {m.badge}\n\n"
    out += (f"## 报告窗口\n\nsnap {w.begin_id} ({w.begin_ts}) → snap {w.end_id} "
            f"({w.end_ts})，时长 {w.duration_min} 分钟，scope={w.scope} node={w.node}\n\n")

    out += "## 维度概览\n\n"
    mrows = [[r.dim, r.sev.label(), r.headline] for r in m.matrix]
    out += render.table(["维度", "严重度", "关键指标"], mrows)
    out += "\n"

    if m.raw_tables:
        out += "## 全景分析（自顶向下）\n\n"
        for t in m.raw_tables:
            out += f"### {t.title}\n\n"
            if t.headline:
                out += f"{t.headline}\n\n"
            if t.headers and t.rows:
                out += render.table(t.headers, t.rows)
                out += "\n"

    out += "## 高风险发现（根因 → 引发请求 → 优化）\n\n"
    if not m.findings:
        out += "无确定性发现被解读。\n\n"
    for f in m.findings:
        out += f"### {f.sev.label()} {f.code} · {f.dim}\n\n"
        out += f"- 证据：{f.evidence_line}\n"
        out += f"- 根因：{f.root_cause}\n"
        if f.suggestions:
            out += "- 建议：\n"
            for s in f.suggestions:
                out += f"  - {s}\n"
        out += "\n"

    out += _emit_quarantine_md(m)
    out += "—— 本报告经脚本只读采集；结论已对确定性发现锚定校验，建议经实证。\n"
    return out


def _emit_quarantine_md(m: _ReportModel) -> str:
    out = ""
    if m.missing:
        out += "## ⚠ 模型遗漏\n\n"
        for s in m.missing:
            out += f"- {s}：确定性发现未被解读\n"
        out += "\n"
    if m.unanchored:
        out += "## ⚠ 未锚定（已剔除，不作正式发现）\n\n"
        for c in m.unanchored:
            out += f"- {c}：interp 引用的 Code 不在确定性发现中\n"
        out += "\n"
    if m.unverified:
        out += "## ⚠ 未验证建议（已剔除）\n\n"
        for s in m.unverified:
            out += f"- {s}\n"
        out += "\n"
    return out


# ---- ansi emitter ----

def _emit_ansi(m: _ReportModel, color: bool) -> str:
    w = m.window

    def sev_col(s, text):
        return colorize(text, severity_color(s), color)

    title = f"📊 WDR 报告 · {m.conn}"
    if m.target:
        title += f" ({m.target})"
    out = colorize(title, ANSI_BOLD, color) + "\n"
    out += (f"窗口 snap {w.begin_id} ({w.begin_ts}) → {w.end_id} ({w.end_ts})  "
            f"时长 {w.duration_min} 分钟  scope={w.scope} node={w.node}\n")
    out += f"总体状态 {sev_col(m.overall, m.overall.label())}  驱动：{m.driver}\n"
    out += f"判断校验 {m.badge}\n\n"

    out += colorize("维度概览", ANSI_BOLD, color) + "\n"
    mrows = [[r.dim, r.sev.label(), trunc(r.headline, 40)] for r in m.matrix]
    out += box_table(["维度", "严重度", "关键指标"], mrows)
    out += "\n"

    if m.raw_tables:
        out += colorize("全景分析（自顶向下）", ANSI_BOLD, color) + "\n"
        for t in m.raw_tables:
            out += colorize("【" + t.title + "】", ANSI_BOLD, color) + "\n"
            if t.headline:
                out += "  " + t.headline + "\n"
            out += box_table(t.headers, t.rows)
            out += "\n"

    out += colorize("高风险发现（根因 → 引发请求 → 优化）", ANSI_BOLD, color) + "\n"
    for f in m.findings:
        out += f"{sev_col(f.sev, f.sev.label())} {f.code} · {f.dim}\n"
        out += f"  证据  {f.evidence_line}\n"
        out += f"  根因  {f.root_cause}\n"
        for s in f.suggestions:
            out += f"  建议  {s}\n"
    out += "\n"

    out += _emit_quarantine_ansi(m, color)
    out += colorize("—— 脚本只读采集；结论已锚定校验，建议经实证。", ANSI_DIM, color) + "\n"
    return out


def _emit_quarantine_ansi(m: _ReportModel, color: bool) -> str:
    out = ""
    if m.missing:
        out += colorize("⚠ 模型遗漏", ANSI_ORANGE, color) + "\n"
        for s in m.missing:
            out += f"  {s}\n"
    if m.unanchored:
        out += colorize("⚠ 未锚定（已剔除）", ANSI_ORANGE, color) + "\n"
        for c in m.unanchored:
            out += f"  {c}\n"
    if m.unverified:
        out += colorize("⚠ 未验证建议（已剔除）", ANSI_ORANGE, color) + "\n"
        for s in m.unverified:
            out += f"  {s}\n"
    if m.missing or m.unanchored or m.unverified:
        out += "\n"
    return out
