"""The 7 read-only WDR snapshot-delta collectors + the Collect orchestrator.

Port of internal/probe/wdr/{loadprofile,dbstat,topsql,waits,checkpoint,cache,
fileio,wdr}.go. Snapshot ids / top-N are validated ints inlined into the SQL
exactly as the Go version did (fmt.Sprintf %d). Collectors never raise: on query
failure they return degraded(dim, reason).
"""
from __future__ import annotations

from model import (
    DIM_CACHE, DIM_CHECKPOINT, DIM_DBSTAT, DIM_FILEIO, DIM_LOADPROFILE,
    DIM_TOPSQL, DIM_WAITS, DimResult, Evidence, Finding, Options, Severity,
    degraded, worst,
)
from native import generate_native, load_window
from util import f2, i64, mib, summarize_err, trunc

import common  # resolved on sys.path by the entry script

import pathlib
import sys

_HERE = pathlib.Path(__file__).resolve()

for parent in _HERE.parents:
    if (parent / "common" / "sql.py").exists():
        sys.path.insert(0, str(parent))
        break

from common.sql import skill_wdr_001,skill_wdr_002,skill_wdr_003,skill_wdr_004,skill_wdr_005,skill_wdr_006,skill_wdr_007,skill_wdr_008


def _i(x) -> int:
    return 0 if x is None else int(x)


# --- Load Profile ------------------------------------------------------------

def collect_loadprofile(db, opt: Options) -> DimResult:
    b, e = int(opt.begin), int(opt.end)
    qt = skill_wdr_001.format(b=b, e=e)
    try:
        _, rows = db.query(qt)
    except common.DBError as exc:
        return degraded(DIM_LOADPROFILE, summarize_err(exc))
    db_time_us, cpu_time_us = _i(rows[0][0]), _i(rows[0][1])
    qd = skill_wdr_002.format(b=b, e=e)
    
    try:
        _, rows = db.query(qd)
    except common.DBError as exc:
        return degraded(DIM_LOADPROFILE, summarize_err(exc))
    commits, blks_read, blks_hit = _i(rows[0][0]), _i(rows[0][1]), _i(rows[0][2])

    cpu_pct = 100.0 * cpu_time_us / db_time_us if db_time_us > 0 else 0.0
    d = DimResult(dimension=DIM_LOADPROFILE, available=True,
                  headers=["DB time(s)", "CPU time(s)", "CPU占DBtime%", "commits", "物理读(块)", "逻辑读(块)"],
                  rows=[[f2(db_time_us / 1e6), f2(cpu_time_us / 1e6), f2(cpu_pct),
                         i64(commits), i64(blks_read), i64(blks_hit)]])
    d.headline = (f"DB time {db_time_us / 1e6:.1f}s（其中 CPU {cpu_time_us / 1e6:.1f}s="
                  f"{cpu_pct:.0f}%，余为等待/锁/IO/睡眠）、commits {commits}、"
                  f"物理读 {blks_read} 块、逻辑读 {blks_hit} 块")
    return d


# --- Database Stat -----------------------------------------------------------

def collect_dbstat(db, opt: Options) -> DimResult:
    b, e = int(opt.begin), int(opt.end)
    q = skill_wdr_003.format(b=b, e=e)
    try:
        _, rows = db.query(q)
    except common.DBError as exc:
        return degraded(DIM_DBSTAT, summarize_err(exc))
    commits, rollbacks, deadlocks, temp_bytes, blks_hit, blks_read = (_i(x) for x in rows[0])

    total = commits + rollbacks
    rb_pct = 100.0 * rollbacks / total if total > 0 else 0.0
    cache_hit = 100.0 * blks_hit / (blks_hit + blks_read) if blks_hit + blks_read > 0 else 100.0

    th = opt.thresholds
    d = DimResult(dimension=DIM_DBSTAT, available=True,
                  headers=["commits", "rollbacks", "回滚率%", "死锁", "临时溢出", "cache_hit%"],
                  rows=[[i64(commits), i64(rollbacks), f2(rb_pct), i64(deadlocks),
                         mib(temp_bytes), f2(cache_hit)]])
    if total >= th.rollback_floor:
        if rb_pct > th.rollback_pct_warn:
            d.findings.append(Finding(DIM_DBSTAT, "WDR_ROLLBACK_RATIO", Severity.WARN,
                                      "回滚率", f2(rb_pct) + "%", f">{th.rollback_pct_warn:.0f}%",
                                      "snap_summary_stat_database xact_commit/rollback"))
        elif rb_pct > th.rollback_pct_notice:
            d.findings.append(Finding(DIM_DBSTAT, "WDR_ROLLBACK_RATIO", Severity.NOTICE,
                                      "回滚率", f2(rb_pct) + "%", f">{th.rollback_pct_notice:.0f}%",
                                      "snap_summary_stat_database xact_commit/rollback"))
    if deadlocks >= th.deadlock_warn:
        d.findings.append(Finding(DIM_DBSTAT, "WDR_DEADLOCK", Severity.WARN, "死锁数",
                                  i64(deadlocks), f"≥{th.deadlock_warn}",
                                  "snap_summary_stat_database.deadlocks"))
    elif deadlocks >= th.deadlock_notice:
        d.findings.append(Finding(DIM_DBSTAT, "WDR_DEADLOCK", Severity.NOTICE, "死锁数",
                                  i64(deadlocks), f"≥{th.deadlock_notice}",
                                  "snap_summary_stat_database.deadlocks"))
    if temp_bytes > th.temp_spill_warn:
        d.findings.append(Finding(DIM_DBSTAT, "WDR_TEMP_SPILL", Severity.WARN, "临时文件溢出",
                                  mib(temp_bytes), ">" + mib(th.temp_spill_warn),
                                  "snap_summary_stat_database.temp_bytes"))
    elif temp_bytes > th.temp_spill_notice:
        d.findings.append(Finding(DIM_DBSTAT, "WDR_TEMP_SPILL", Severity.NOTICE, "临时文件溢出",
                                  mib(temp_bytes), ">" + mib(th.temp_spill_notice),
                                  "snap_summary_stat_database.temp_bytes"))
    if cache_hit < th.cache_hit_warn:
        d.findings.append(Finding(DIM_DBSTAT, "WDR_BUFFER_HIT_LOW", Severity.WARN, "缓存命中率",
                                  f2(cache_hit) + "%", f"<{th.cache_hit_warn:.0f}%",
                                  "snap_summary_stat_database blks_hit/read"))
    elif cache_hit < th.cache_hit_notice:
        d.findings.append(Finding(DIM_DBSTAT, "WDR_BUFFER_HIT_LOW", Severity.NOTICE, "缓存命中率",
                                  f2(cache_hit) + "%", f"<{th.cache_hit_notice:.0f}%",
                                  "snap_summary_stat_database blks_hit/read"))
    d.headline = (f"commit {commits} / rollback {rollbacks}（回滚率 {rb_pct:.1f}%）、"
                  f"死锁 {deadlocks}、临时溢出 {mib(temp_bytes)}、命中率 {cache_hit:.1f}%")
    return d


# --- Top SQL -----------------------------------------------------------------

def collect_topsql(db, opt: Options) -> DimResult:
    b, e, top = int(opt.begin), int(opt.end), int(opt.top)
    q = skill_wdr_004.format(b=b, e=e,top=top)
    try:
        _, rows = db.query(q)
    except common.DBError as exc:
        return degraded(DIM_TOPSQL, summarize_err(exc))

    # each list item: dict-like tuple (id, query, calls, elapsed, cpu, spill, phys)
    items = [(str(r[0]), str(r[1]), _i(r[2]), _i(r[3]), _i(r[4]), _i(r[5]), _i(r[6])) for r in rows]
    total_elapsed = sum(it[3] for it in items)

    d = DimResult(dimension=DIM_TOPSQL, available=True,
                  headers=["sql_id", "calls", "elapsed_s", "cpu_s", "spill_MB", "物理读(块)", "占DB time%", "query"])
    if not items:
        d.headline = "窗口内无正增量 SQL"
        return d
    for sid, query, calls, elapsed, cpu, spill, phys in items:
        pct = 100.0 * elapsed / total_elapsed if total_elapsed > 0 else 0.0
        d.rows.append([sid, i64(calls), f2(elapsed / 1e6), f2(cpu / 1e6),
                       f2(spill / 1024), i64(phys), f2(pct), trunc(query, 50)])

    th = opt.thresholds
    t_sid, t_query, t_calls, t_elapsed, t_cpu, t_spill, t_phys = items[0]
    top_pct = 100.0 * t_elapsed / total_elapsed if total_elapsed > 0 else 0.0

    def mk(sev: Severity, thr: str):
        d.findings.append(Finding(DIM_TOPSQL, "WDR_TOPSQL_DBTIME", sev, "单条 SQL 占 DB time",
                                  f2(top_pct) + "%", thr,
                                  f"snap_summary_statement：sqlid {t_sid} elapsed {t_elapsed / 1e6:.1f}s ×{t_calls}",
                                  sql_id=t_sid))
    if top_pct > th.top_sql_dbtime_crit:
        mk(Severity.CRITICAL, f">{th.top_sql_dbtime_crit:.0f}%")
    elif top_pct > th.top_sql_dbtime_warn:
        mk(Severity.WARN, f">{th.top_sql_dbtime_warn:.0f}%")
    elif top_pct > th.top_sql_dbtime_notice:
        mk(Severity.NOTICE, f">{th.top_sql_dbtime_notice:.0f}%")

    cpu_ratio = t_cpu / t_elapsed if t_elapsed > 0 else 0.0
    if top_pct > th.top_sql_dbtime_notice and cpu_ratio < th.top_sql_low_cpu_ratio:
        d.findings.append(Finding(
            DIM_TOPSQL, "WDR_TOPSQL_LOW_CPU", Severity.NOTICE, "高 DB time 但 CPU 极低",
            f"CPU 仅占其耗时 {cpu_ratio * 100:.1f}%", f"<{th.top_sql_low_cpu_ratio * 100:.0f}%",
            f"sqlid {t_sid} elapsed {t_elapsed / 1e6:.1f}s / cpu {t_cpu / 1e6:.1f}s → "
            "阻塞/睡眠主导(锁/等待/pg_sleep)，非资源消耗；真凶看 CPU time 与等待事件",
            sql_id=t_sid))

    # 各维度元凶：每个资源维度的冠军 SQL 往往不是同一条。
    def champ(idx: int):
        best = items[0]
        for it in items:
            if it[idx] > best[idx]:
                best = it
        return best
    by_cpu, by_spill, by_phys, by_calls = champ(4), champ(5), champ(6), champ(2)
    d.headline = (
        f"各维度元凶 — DB time:{t_sid}({top_pct:.0f}%,CPU占{cpu_ratio * 100:.0f}%) ｜ "
        f"CPU:{by_cpu[0]}({by_cpu[4] / 1e6:.0f}s) ｜ 溢出:{by_spill[0]}({by_spill[5] / 1024:.0f}MB) ｜ "
        f"物理读:{by_phys[0]}({by_phys[6]}块) ｜ 调用:{by_calls[0]}({by_calls[2]}次)；共 {len(items)} 条")
    return d


# --- Wait Events / Classes ---------------------------------------------------

def collect_waits(db, opt: Options) -> DimResult:
    b, e, top = int(opt.begin), int(opt.end), int(opt.top)
    q = skill_wdr_005.format(b=b, e=e,top=top)
    try:
        _, rows = db.query(q)
    except common.DBError as exc:
        return degraded(DIM_WAITS, summarize_err(exc))
    items = [(str(r[0]), _i(r[1]), _i(r[2])) for r in rows]
    total = sum(it[2] for it in items)

    d = DimResult(dimension=DIM_WAITS, available=True, headers=["等待类", "waits", "wait_s", "占比%"])
    if not items:
        d.headline = "窗口内无显著非空闲等待"
        return d
    for cls, waits, usec in items:
        pct = 100.0 * usec / total if total > 0 else 0.0
        d.rows.append([cls, i64(waits), f2(usec / 1e6), f2(pct)])

    th = opt.thresholds
    top_pct = 100.0 * items[0][2] / total if total > 0 else 0.0
    if top_pct > th.wait_skew_warn:
        d.findings.append(Finding(DIM_WAITS, "WDR_WAIT_CLASS_SKEW", Severity.WARN, "等待类倾斜",
                                  f"{items[0][0]} {top_pct:.0f}%", f">{th.wait_skew_warn:.0f}%",
                                  "snap_global_wait_events 等待类聚合"))
    elif top_pct > th.wait_skew_notice:
        d.findings.append(Finding(DIM_WAITS, "WDR_WAIT_CLASS_SKEW", Severity.NOTICE, "等待类倾斜",
                                  f"{items[0][0]} {top_pct:.0f}%", f">{th.wait_skew_notice:.0f}%",
                                  "snap_global_wait_events 等待类聚合"))
    d.headline = f"Top 等待类 {items[0][0]} 占 {top_pct:.0f}%"
    return d


# --- Checkpoint / BgWriter / Redo --------------------------------------------

def collect_checkpoint(db, opt: Options) -> DimResult:
    b, e = int(opt.begin), int(opt.end)
    q = skill_wdr_006.format(b=b, e=e)
    try:
        _, rows = db.query(q)
    except common.DBError as exc:
        return degraded(DIM_CHECKPOINT, summarize_err(exc))
    timed, req = _i(rows[0][0]), _i(rows[0][1])
    total = timed + req
    req_pct = 100.0 * req / total if total > 0 else 0.0

    th = opt.thresholds
    d = DimResult(dimension=DIM_CHECKPOINT, available=True,
                  headers=["timed_ckpt", "req_ckpt", "req占比%"],
                  rows=[[i64(timed), i64(req), f2(req_pct)]])
    if total > 0:
        if req_pct > th.ckpt_req_warn:
            d.findings.append(Finding(DIM_CHECKPOINT, "WDR_CKPT_REQ_HIGH", Severity.WARN,
                                      "强制 checkpoint 占比", f2(req_pct) + "%", f">{th.ckpt_req_warn:.0f}%",
                                      "snap_global_bgwriter_stat checkpoints_req/timed"))
        elif req_pct > th.ckpt_req_notice:
            d.findings.append(Finding(DIM_CHECKPOINT, "WDR_CKPT_REQ_HIGH", Severity.NOTICE,
                                      "强制 checkpoint 占比", f2(req_pct) + "%", f">{th.ckpt_req_notice:.0f}%",
                                      "snap_global_bgwriter_stat checkpoints_req/timed"))
    d.headline = f"checkpoint timed {timed} / req {req}（req 占 {req_pct:.0f}%）"
    return d


# --- Cache / Memory ----------------------------------------------------------

def collect_cache(db, opt: Options) -> DimResult:
    b, e, top = int(opt.begin), int(opt.end), int(opt.top)
    q = skill_wdr_007.format(b=b, e=e, top=top)
    try:
        _, rows = db.query(q)
    except common.DBError as exc:
        return degraded(DIM_CACHE, summarize_err(exc))
    d = DimResult(dimension=DIM_CACHE, available=True, headers=["对象", "物理读(块)", "逻辑读(块)"])
    for r in rows:
        d.rows.append([str(r[0]), i64(_i(r[1])), i64(_i(r[2]))])
    if not d.rows:
        d.headline = "窗口内无显著物理读对象"
    else:
        d.headline = f"物理读 Top 对象：{d.rows[0][0]}（命中率详见 Database Stat）"
    return d


# --- File IO -----------------------------------------------------------------

def collect_fileio(db, opt: Options) -> DimResult:
    b, e, top = int(opt.begin), int(opt.end), int(opt.top)
    q = skill_wdr_008.format(b=b, e=e, top=top)
    try:
        _, rows = db.query(q)
    except common.DBError as exc:
        return degraded(DIM_FILEIO, summarize_err(exc))
    d = DimResult(dimension=DIM_FILEIO, available=True, headers=["文件", "物理读", "物理写"])
    for r in rows:
        d.rows.append([trunc(str(r[0]), 40), i64(_i(r[1])), i64(_i(r[2]))])
    if not d.rows:
        d.headline = "窗口内无显著文件 IO"
    else:
        d.headline = f"物理读 Top 文件：{d.rows[0][0]}"
    return d


# --- orchestrator ------------------------------------------------------------

def registry():
    """Collectors in evidence-pack display order (= report section order)."""
    return [collect_loadprofile, collect_dbstat, collect_topsql, collect_waits,
            collect_checkpoint, collect_cache, collect_fileio]


def collect_evidence(db, opt: Options) -> Evidence:
    """Run every collector over the window and assemble evidence. Per-collector
    failures degrade; only the window/native prep can fail (propagated)."""
    if opt.top <= 0:
        opt.top = 10
    ev = Evidence()
    ev.window = load_window(db, opt)            # validate snaps + scope/node + wdr-enabled
    ev.native = generate_native(db, opt, ev.window)  # best-effort留底
    for fn in registry():
        d = fn(db, opt)
        ev.dims.append(d)
        ev.findings.extend(d.findings)
    ev.findings.sort(key=lambda f: int(f.severity), reverse=True)
    ev.overall = worst([f.severity for f in ev.findings])
    return ev
