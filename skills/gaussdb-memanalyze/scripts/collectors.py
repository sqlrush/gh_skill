"""L1 instance / L2 contexts / L3 sessions / L6 config collectors.

Collectors never raise: on a failed query they return degraded(dim, reason), so
one missing view or permission gap cannot abort the other five layers.
"""
from __future__ import annotations

from dataclasses import dataclass

import common
import probe
from model import (
    DIM_CONFIG, DIM_CONTEXT, DIM_INSTANCE, DIM_SESSION,
    Capability, Catalog, DimResult, Finding, Severity, degraded,
)
from thresholds import Thresholds
from util import f, human_mb, i64, pct, summarize_err, trunc

_MB = 1024.0 * 1024.0


# --------------------------------------------------------------------------
# L1 — instance memory
# --------------------------------------------------------------------------
_INSTANCE_COLS = ("memorytype", "memorymbytes")


def instance_memory(db, cat: Catalog) -> dict:
    """memorytype -> MB. Raises DBError; watch mode calls this directly, so it
    can sample cheaply without rebuilding the whole DimResult each round."""
    vi = cat.get("instance")
    q = f"SELECT {probe.columns_expr(vi, _INSTANCE_COLS)} FROM {vi.name}"
    _, rows = db.query(q)
    return {str(r[0]).strip().lower(): f(r[1]) for r in rows if r and r[0]}


def collect_instance(db, cat: Catalog, th: Thresholds, _top: int) -> DimResult:
    vi = cat.get("instance")
    if not vi.available:
        return degraded(DIM_INSTANCE, vi.reason)

    try:
        mem = instance_memory(db, cat)
    except common.DBError as exc:
        return degraded(DIM_INSTANCE, summarize_err(exc))

    d = DimResult(dimension=DIM_INSTANCE, available=True,
                  headers=["内存类型", "大小"])
    for k in sorted(mem):
        d.rows.append([k, human_mb(mem[k])])

    max_dyn = mem.get("max_dynamic_memory", 0.0)
    used = mem.get("dynamic_used_memory", 0.0)
    peak = mem.get("dynamic_peak_memory", 0.0)
    proc_used = mem.get("process_used_memory", 0.0)
    other = mem.get("other_used_memory", 0.0)

    dyn_pct = pct(used, max_dyn)
    peak_pct = pct(peak, max_dyn)

    if dyn_pct >= th.dyn_critical_pct:
        sev = Severity.CRITICAL
    elif dyn_pct >= th.dyn_warn_pct:
        sev = Severity.WARN
    elif dyn_pct >= th.dyn_notice_pct:
        sev = Severity.NOTICE
    else:
        sev = Severity.OK

    if sev > Severity.OK:
        d.findings.append(Finding(
            DIM_INSTANCE, "MEM_DYNAMIC_HIGH", sev, "动态内存使用率",
            f"{dyn_pct:.1f}%", f">={th.dyn_notice_pct:.0f}%",
            f"dynamic_used_memory {human_mb(used)} / max_dynamic_memory "
            f"{human_mb(max_dyn)}"))

    # The spike already happened and is over — silence here would hide the incident.
    if peak_pct >= th.peak_fallback_pct and dyn_pct < th.dyn_notice_pct:
        d.findings.append(Finding(
            DIM_INSTANCE, "MEM_PEAK_FALLBACK", Severity.NOTICE, "动态内存历史峰值",
            f"{peak_pct:.1f}%", f">={th.peak_fallback_pct:.0f}%",
            f"dynamic_peak_memory {human_mb(peak)} 曾接近上限，当前已回落至 "
            f"{human_mb(used)}（{dyn_pct:.1f}%）——冲高已发生但已结束"))

    other_pct = pct(other, proc_used)
    if other_pct >= th.other_pct_notice:
        d.findings.append(Finding(
            DIM_INSTANCE, "MEM_OTHER_HIGH", Severity.NOTICE, "other 内存占比",
            f"{other_pct:.1f}%", f">={th.other_pct_notice:.0f}%",
            f"other_used_memory {human_mb(other)}（非托管内存，多为第三方库/元数据）"))

    d.headline = (f"动态内存 {human_mb(used)} / {human_mb(max_dyn)}"
                  f"（{dyn_pct:.1f}%），历史峰值 {human_mb(peak)}（{peak_pct:.1f}%）")
    return d


# --------------------------------------------------------------------------
# L2 — memory contexts
# --------------------------------------------------------------------------
_CTX_COLS = ("contextname", "totalsize", "freesize", "usedsize")


def collect_context(db, cat: Catalog, th: Thresholds, top: int) -> DimResult:
    slot = "session_ctx" if cat.has("session_ctx") else "thread_ctx"
    vi = cat.get(slot)
    if not vi.available:
        return degraded(DIM_CONTEXT, vi.reason)

    cols = probe.columns_expr(vi, _CTX_COLS)
    q = (f"SELECT contextname, sum(totalsize) AS totalsize, "
         f"sum(freesize) AS freesize, sum(usedsize) AS usedsize "
         f"FROM (SELECT {cols} FROM {vi.name}) t "
         f"GROUP BY contextname ORDER BY 4 DESC NULLS LAST LIMIT {int(top)}")
    try:
        _, rows = db.query(q)
    except common.DBError as exc:
        return degraded(DIM_CONTEXT, summarize_err(exc))

    d = DimResult(dimension=DIM_CONTEXT, available=True,
                  headers=["memory context", "total", "free", "used", "占比"])
    total_used = sum(f(r[3]) for r in rows) or 1.0

    for r in rows:
        name = str(r[0])
        total_b, free_b, used_b = f(r[1]), f(r[2]), f(r[3])
        share = pct(used_b, total_used)
        d.rows.append([trunc(name, 48), human_mb(total_b / _MB),
                       human_mb(free_b / _MB), human_mb(used_b / _MB),
                       f"{share:.1f}%"])

        if share >= th.context_pct_warn:
            d.findings.append(Finding(
                DIM_CONTEXT, "MEM_CONTEXT_DOMINANT", Severity.WARN,
                f"context {trunc(name, 40)} 占比", f"{share:.1f}%",
                f">={th.context_pct_warn:.0f}%",
                f"该 context 已用 {human_mb(used_b / _MB)}，占本次采集全部 context "
                f"内存的 {share:.1f}%（缓存类 context 持续增长通常指向元数据膨胀或"
                f"会话不释放）"))

        frag = pct(free_b, total_b)
        if frag >= th.context_frag_pct and total_b / _MB >= th.context_frag_min_mb:
            d.findings.append(Finding(
                DIM_CONTEXT, "MEM_CONTEXT_FRAGMENT", Severity.NOTICE,
                f"context {trunc(name, 40)} 空闲率", f"{frag:.1f}%",
                f">={th.context_frag_pct:.0f}%",
                f"total {human_mb(total_b / _MB)} 中有 {human_mb(free_b / _MB)} "
                f"空闲未归还，疑似内存碎片"))

    d.headline = (f"Top context：{d.rows[0][0]}（{d.rows[0][3]}）"
                  if d.rows else "未采集到内存上下文数据")
    return d


# --------------------------------------------------------------------------
# L3 — sessions (memory row <-> the SQL that session is running)
# --------------------------------------------------------------------------
@dataclass(frozen=True)
class SessionRow:
    sessid: str
    init_mb: float
    used_mb: float
    peak_mb: float
    usename: str = ""
    application_name: str = ""
    state: str = ""
    query: str = ""


_UNMATCHED = "（未关联到活动会话，可能已结束）"

_SESS_COLS = ("sessid", "init_mem", "used_mem", "peak_mem")
_ACT_COLS = ("sessionid", "pid", "usename", "application_name", "state", "query")


def correlate_sessions(mem_rows, activity: dict) -> list:
    """Join memory rows to running sessions (pure).

    openGauss reports `sessid` either as a plain thread id or as
    `<timestamp>.<threadid>`; pg_stat_activity keys on sessionid and pid. Rather
    than bet on one dialect's join, we try the whole id, then the trailing
    thread id, and keep the row either way — a session that already exited still
    consumed the memory, so dropping it would hide the culprit.
    """
    out: list[SessionRow] = []
    for r in mem_rows:
        sessid = str(r[0])
        info = activity.get(sessid)
        if info is None and "." in sessid:
            info = activity.get(sessid.rsplit(".", 1)[-1])
        info = info or {}
        out.append(SessionRow(
            sessid=sessid,
            init_mb=f(r[1]), used_mb=f(r[2]), peak_mb=f(r[3]),
            usename=str(info.get("usename", "")),
            application_name=str(info.get("application_name", "")),
            state=str(info.get("state", "")),
            query=str(info.get("query") or _UNMATCHED),
        ))
    return out


def _activity_map(db, cat: Catalog) -> dict:
    """pid/sessionid -> session info. Best effort: no activity view is survivable."""
    vi = cat.get("activity")
    if not vi.available:
        return {}
    try:
        _, rows = db.query(
            f"SELECT {probe.columns_expr(vi, _ACT_COLS)} FROM {vi.name}")
    except common.DBError:
        return {}

    out: dict[str, dict] = {}
    for r in rows:
        info = {"usename": r[2] or "", "application_name": r[3] or "",
                "state": r[4] or "", "query": r[5] or ""}
        for key in (r[0], r[1]):              # sessionid and pid both index it
            if key is not None:
                out[str(i64(key))] = info
    return out


def collect_session(db, cat: Catalog, th: Thresholds, top: int) -> DimResult:
    vi = cat.get("session_mem")
    if not vi.available:
        return degraded(DIM_SESSION, vi.reason)

    order = "peak_mem" if probe.has_col(vi, "peak_mem") else "used_mem"
    q = (f"SELECT {probe.columns_expr(vi, _SESS_COLS)} FROM {vi.name} "
         f"ORDER BY {order} DESC NULLS LAST LIMIT {int(top)}")
    try:
        _, rows = db.query(q)
    except common.DBError as exc:
        return degraded(DIM_SESSION, summarize_err(exc))

    sessions = correlate_sessions(rows, _activity_map(db, cat))

    d = DimResult(dimension=DIM_SESSION, available=True,
                  headers=["会话", "用户", "应用", "状态", "已用", "峰值", "SQL"])
    for s in sessions:
        d.rows.append([s.sessid, s.usename, s.application_name, s.state,
                       human_mb(s.used_mb), human_mb(s.peak_mb),
                       trunc(s.query, 60)])

        if s.peak_mb >= th.session_hog_mb:
            d.findings.append(Finding(
                DIM_SESSION, "MEM_SESSION_HOG", Severity.WARN, "单会话峰值内存",
                human_mb(s.peak_mb), f">={human_mb(th.session_hog_mb)}",
                f"会话 {s.sessid}（{s.usename or '?'}@{s.application_name or '?'}，"
                f"{s.state or '?'}）峰值 {human_mb(s.peak_mb)}，SQL：{trunc(s.query, 80)}"))

        if s.state.strip().lower().startswith("idle in transaction") \
                and s.used_mb >= th.idle_xact_mem_mb:
            d.findings.append(Finding(
                DIM_SESSION, "MEM_SESSION_IDLE_XACT", Severity.WARN,
                "空闲事务占用内存", human_mb(s.used_mb),
                f">={human_mb(th.idle_xact_mem_mb)}",
                f"会话 {s.sessid} 处于 idle in transaction 却仍占用 "
                f"{human_mb(s.used_mb)}，内存不会释放直到事务结束"))

    d.headline = (f"内存最高会话：{sessions[0].sessid}（峰值 "
                  f"{human_mb(sessions[0].peak_mb)}）" if sessions else "无会话内存数据")
    return d


# --------------------------------------------------------------------------
# L6 — configuration sanity (no query: the GUCs are already in Capability)
# --------------------------------------------------------------------------
def _dynamic_ceiling_kb(gucs, mem) -> float:
    """The dynamic-memory ceiling, in kB.

    On openGauss `max_dynamic_memory` is NOT a GUC — it only shows up as a row
    in gs_total_memory_detail (MB). Reading pg_settings alone made the overcommit
    check silently never fire. Fall back through: GUC -> L1 view -> the process
    ceiling, which is at least a real bound.
    """
    guc_kb = f(gucs.get("max_dynamic_memory", 0))
    if guc_kb:
        return guc_kb
    view_mb = f((mem or {}).get("max_dynamic_memory", 0))
    if view_mb:
        return view_mb * 1024.0
    return f(gucs.get("max_process_memory", 0))


def collect_config(_db, cap: Capability, th: Thresholds, _top: int,
                   mem: dict | None = None) -> DimResult:
    g = cap.gucs
    if not g:
        return degraded(DIM_CONFIG, "未能读取 pg_settings")

    d = DimResult(dimension=DIM_CONFIG, available=True, headers=["GUC", "值"])
    for name in sorted(g):
        d.rows.append([name, g[name]])

    # work_mem is per-operator, per-connection: the theoretical worst case is
    # work_mem x max_connections. If that exceeds the dynamic memory ceiling,
    # the instance is one concurrency spike away from OOM by configuration.
    work_mem_kb = f(g.get("work_mem", 0))
    max_conn = f(g.get("max_connections", 0))
    max_dyn_kb = _dynamic_ceiling_kb(g, mem)
    if work_mem_kb and max_conn and max_dyn_kb:
        worst_kb = work_mem_kb * max_conn
        ratio = worst_kb / max_dyn_kb
        if ratio > 1.0:
            d.findings.append(Finding(
                DIM_CONFIG, "MEM_CONFIG_OVERCOMMIT", Severity.NOTICE,
                "work_mem × max_connections 理论上限",
                f"{worst_kb / 1024:.0f} MB（{ratio:.1f}× 动态内存上限）",
                "<= max_dynamic_memory",
                f"work_mem {work_mem_kb / 1024:.0f} MB × max_connections "
                f"{max_conn:.0f} = {worst_kb / 1024:.0f} MB，超过 max_dynamic_memory "
                f"{max_dyn_kb / 1024:.0f} MB。理论最坏情况，非必然发生；"
                f"高并发下大排序/哈希可能撑爆动态内存"))

    d.headline = (
        f"work_mem {human_mb(work_mem_kb / 1024)}｜"
        f"max_process_memory {human_mb(f(g.get('max_process_memory', 0)) / 1024)}｜"
        f"max_connections {g.get('max_connections', '?')}｜"
        f"resource_track_level {g.get('resource_track_level', '?')}")
    return d
