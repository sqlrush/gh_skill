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
    sql_id: str = ""        # 这条发现指向哪条语句;SQL 附录按它列全文


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
    sql_texts: dict = field(default_factory=dict)   # sql_id → 全文,给 SQL 附录用
    # **全部确定性发现**(脚本判出来的,不管模型有没有解读)。「下一步」只认这一份:
    # 它是确定性输出,不该因为模型漏解读某一条就少给一条命令。
    # 另外 recheck 把 interp 按 **code** 对齐证据,同一个 code 有两条发现时(两条语句都
    # CPU 占主导)只会锚定一条 —— 下一步若读锚定结果,第二条语句就没有命令了。
    ev_findings: list = field(default_factory=list)
    top_sql_rows: list = field(default_factory=list)     # 解析溢出元凶的 sql_id 用
    top_sql_headers: list = field(default_factory=list)


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
            root_cause=a.root_cause, sql_id=getattr(a.ev, "sql_id", "") or "")
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
    m.sql_texts = dict(getattr(ev, "sql_texts", {}) or {})   # SQL 附录用
    m.ev_findings = list(ev.findings or [])                  # 「下一步」用,见字段注释
    for d in ev.dims:
        if d.dimension == "Top SQL" and d.rows:
            m.top_sql_rows, m.top_sql_headers = d.rows, (d.headers or [])
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
    out += _emit_next_steps(m)
    out += _emit_sql_appendix(m)
    out += "—— 本报告经脚本只读采集；结论已对确定性发现锚定校验，建议经实证。\n"
    return out


# 发现码 → 下一步该跑什么。**由脚本生成,不靠模型自己想。**
#
# 为什么要钉成确定性输出:SKILL.md 里早就写了「带 sql_id 的先 sqltune 实证」,
# 可 2026-10-17 实测那一轮,模型给的 8 条建议一条都没去实证,报告自己也写着
# 「0 条建议经 hypopg/cost 实证」。规则在提示词里,就只是「希望它做」;
# 落成报告里的一个段落,用户才看得见该跑什么、也看得出有没有跑。
_NEXT_BY_CODE = {
    "WDR_TOPSQL_CPU_HEAVY": ("这条在真干活(CPU 占主导)——看执行计划、做调优实证",
                             ["python3 ../gaussdb-explain/scripts/explain.py --sql-id {sql_id}",
                              "python3 ../gaussdb-sqltune/scripts/sqltune.py {sql_id}"]),
    "WDR_TOPSQL_LOW_CPU": ("这条在等(CPU 近零)——查谁挡着它,**不要**去调执行计划",
                           ["python3 ../gaussdb-lockwait/scripts/lockwait.py"]),
    "WDR_WAIT_CLASS_SKEW": ("等待集中在某一类——按窗口拆开看是谁在争",
                            ["python3 ../gaussdb-waitevent/scripts/waitevent.py"]),
    "WDR_DEADLOCK": ("死锁——看阻塞链与具体死锁对",
                     ["python3 ../gaussdb-lockwait/scripts/lockwait.py"]),
    "WDR_TEMP_SPILL": ("临时文件溢出——定位落盘的语句并评估会话级 work_mem",
                       ["python3 ../gaussdb-sqltune/scripts/sqltune.py {sql_id}"]),
    "WDR_DBTIME_WAIT_HEAVY": ("时间主要花在等待上——真凶在等待事件里,不是 CPU 不够",
                              ["python3 ../gaussdb-waitevent/scripts/waitevent.py"]),
}

# WDR 根本不覆盖的东西。**每次都要提**:不提等于让用户以为已经查全了。
_ALWAYS_NEXT = [
    ("WDR 不覆盖膨胀与回收水位(死元组、autovacuum 追不追得上、谁卡住了回收)",
     ["python3 ../gaussdb-vacuum/scripts/vacuum.py"]),
    ("WDR 只统计**已完成**的执行;一直没跑完的 SQL 在这里一行都没有,要看正在执行的会话",
     ["python3 ../gaussdb-health/scripts/health.py --include slowsql"]),
]


def _spill_culprit(m: _ReportModel) -> str:
    """Top SQL 表里落盘最多的那条的 sql_id。

    临时文件溢出那条发现来自 Database Stat(库级计数器),它本身不知道是哪条语句落的盘。
    但 Top SQL 表里有 spill_MB 这一列 —— 取最大的那条,「下一步」才能把 sql_id 填进命令里,
    而不是甩一个 `<sql_id>` 让用户自己翻表。
    """
    try:
        i_sid = m.top_sql_headers.index("sql_id")
        i_spill = next(i for i, h in enumerate(m.top_sql_headers) if h.startswith("spill"))
    except (ValueError, StopIteration):
        return ""
    best, best_v = "", 0.0
    for r in m.top_sql_rows:
        try:
            v = float(r[i_spill])
        except (TypeError, ValueError, IndexError):
            continue
        if v > best_v:
            best, best_v = str(r[i_sid]), v
    return best


def _emit_next_steps(m: _ReportModel) -> str:
    """下一步:按本次的**确定性发现**列出具体命令(sql_id 已填好)。"""
    lines, seen = [], set()
    spill_sid = _spill_culprit(m)
    for f in m.ev_findings:
        hit = _NEXT_BY_CODE.get(f.code)
        if not hit:
            continue
        why, cmds = hit
        sid = getattr(f, "sql_id", "") or (spill_sid if f.code == "WDR_TEMP_SPILL" else "")
        for c in cmds:
            if "{sql_id}" in c and not sid:
                continue        # 填不出 sql_id 的命令不给 —— 甩个占位符等于让用户自己翻表
            cmd = c.replace("{sql_id}", sid)
            if cmd in seen:
                continue
            seen.add(cmd)
            lines.append((why, cmd))
    out = "## 下一步\n\n"
    if lines:
        for why, cmd in lines:
            out += "- %s\n  `%s`\n" % (why, cmd)
    for why, cmds in _ALWAYS_NEXT:
        for c in cmds:
            out += "- %s\n  `%s`\n" % (why, c)
    return out + "\n"


# 附录里每条 SQL 最多展示这么长。再长的留标记,让人去大盘或 sqlfetch 取 ——
# 报告是给人读的,一条几 KB 的 SQL 糊在正文里谁也读不下去。
APPENDIX_SQL_MAX = 1200


def _emit_sql_appendix(m: _ReportModel) -> str:
    """SQL 附录:根因里提到的 sql_id → 完整 SQL 文本。

    **为什么要有附录(照 AWR 的做法):** 根因那几行里只写 sql_id 和一小段片段,
    读的人认不出是哪条语句;可要是把全文塞进根因,一条几百字的 SQL 会把结论冲掉。
    所以正文给 sql_id + 片段,全文集中在附录,再给出大盘与执行计划的去处。

    只列**根因里真正提到过**的 sql_id —— 把 Top SQL 全表的文本都搬下来,
    附录会比报告本身还长。
    """
    texts = m.sql_texts or {}
    if not texts:
        return ""
    wanted, seen = [], set()
    for f in m.findings:
        sid = getattr(f, "sql_id", "") or ""
        if sid and sid in texts and sid not in seen:
            seen.add(sid)
            wanted.append(sid)
    if not wanted:
        return ""
    out = "## SQL 附录（根因里提到的语句）\n\n"
    for sid in wanted:
        body = texts[sid]
        if len(body) > APPENDIX_SQL_MAX:
            body = body[:APPENDIX_SQL_MAX] + "\n…（全文更长，到大盘或用 gaussdb-sqlfetch 取）"
        out += "### sql_id `%s`\n\n" % sid
        out += render.code_block("sql", body)
        out += ("\n- 执行计划：`python3 ../gaussdb-explain/scripts/explain.py --sql-id %s`"
                "（计划要实时连库，报告里不预先生成）\n" % sid)
        out += ("- 调优实证：`python3 ../gaussdb-sqltune/scripts/sqltune.py %s`\n\n" % sid)
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
