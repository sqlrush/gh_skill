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

# SQL 已迁到 scripts/registry/wdr/ —— 两条路径共用同一份定义
# 取数失败只认这一个类型。换一种数据库访问方式时，改的是访问模块，
# 不是这里 —— 详见 common/grmp/errors.py。
from common import access  # noqa: E402
# 协议回来的值全是字符串,NULL 是空串而不是 None —— 累计计数器的增量**不能**
# 把取数失败悄悄当成 0,那会把「没取到」伪装成「恰好为零」。
from common.grmp.values import as_int, is_null  # noqa: E402



def _i(x) -> int:
    return 0 if x is None else int(x)


# --- 窗口内的真实 DB time ----------------------------------------------------
#
# **为什么不再用 wdr.load_profile(2026-10-17 修):** 那条脚本把 DB time 算成
# snap_summary_statement 里「两端都在的 sql_id」的耗时增量之和:
#
#     FROM e JOIN b USING (sid)     -- 内连接
#
# snap_summary_statement 是**滚动的语句历史**,语句集合每时每刻都在变。窗口中途才出现
# 的语句(在 begin 快照里没有)和窗口结束前就老化出历史的语句,全被这个内连接静默丢掉。
# 压测实测:22 分钟窗口里出现过 147 条语句,内连接只剩 55 条,算出来的 DB time 是
# **2.67 秒**,而内核自己的 DB_TIME 增量是 **23518 秒** —— 差 8800 倍,还不报错,
# 于是负载维度被判成「健康」。
#
# 现在改取 snap_global_instance_time 的 DB_TIME / CPU_TIME 增量(注册脚本
# waitevent.instance_time,**早就在白名单里**,客户不需要重新导入)。它是实例级累计
# 计数器,没有「集合会变」的问题。判定规则与 gaussdb-waitevent/scripts/dbtime.py
# 一致(那边是这套取数的参考实现,连重启判定一起):
#   · 取不到 / 缺项 / 值是 NULL  → 整个维度降级,绝不当 0;
#   · 任一项增量为负            → 窗口跨了实例重启,这个窗口的算术结果不可用。

_DB_TIME = "DB_TIME"
_CPU_TIME = "CPU_TIME"


def window_db_time(runner, opt: Options) -> tuple:
    """窗口内 (DB time 微秒, CPU time 微秒, 不可用原因)。原因非空时前两个值无意义。"""
    try:
        rows = runner.run("waitevent.instance_time", {"b": int(opt.begin), "e": int(opt.end)})
    except access.QueryError as exc:
        return 0, 0, summarize_err(exc)
    parsed = {}
    for r in rows:
        name = str(r.get("stat_name") or "")
        if name not in (_DB_TIME, _CPU_TIME):
            continue
        raw = r.get("delta_us")
        if is_null(raw):
            return 0, 0, "%s 的增量是空值 —— 累计计数器的增量不该有空值,不能当 0 处理" % name
        parsed[name] = as_int(raw)
    missing = [k for k in (_DB_TIME, _CPU_TIME) if k not in parsed]
    if missing:
        return 0, 0, "waitevent.instance_time 没返回 %s" % "、".join(missing)
    if any(v < 0 for v in parsed.values()):
        return 0, 0, ("窗口内有负增量 —— 跨了一次实例重启,计数器已清零,"
                      "这个窗口的 DB time 算出来是假的,不report")
    return parsed[_DB_TIME], parsed[_CPU_TIME], ""


# --- Load Profile ------------------------------------------------------------

def collect_loadprofile(runner, opt: Options) -> DimResult:
    b, e = int(opt.begin), int(opt.end)
    db_time_us, cpu_time_us, why = window_db_time(runner, opt)
    if why:
        return degraded(DIM_LOADPROFILE, why)
    # Top SQL 的「占 DB time%」要拿这个真实值做分母,存一份给它用
    opt.db_time_us, opt.cpu_time_us = db_time_us, cpu_time_us
    try:
        rows = runner.run("wdr.db_summary", {"b": b, "e": e})
    except access.QueryError as exc:
        return degraded(DIM_LOADPROFILE, summarize_err(exc))
    commits, blks_read, blks_hit = (_i(rows[0]["xact_commit"]),
                                _i(rows[0]["blks_read"]),
                                _i(rows[0]["blks_hit"]))

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

def collect_dbstat(runner, opt: Options) -> DimResult:
    b, e = int(opt.begin), int(opt.end)
    try:
        rows = runner.run("wdr.db_stat", {"b": b, "e": e})
    except access.QueryError as exc:
        return degraded(DIM_DBSTAT, summarize_err(exc))
    commits, rollbacks, deadlocks, temp_bytes, blks_hit, blks_read = (
        _i(rows[0][k]) for k in ("xact_commit", "xact_rollback", "deadlocks",
                                 "temp_bytes", "blks_hit", "blks_read"))

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

def collect_topsql(runner, opt: Options) -> DimResult:
    b, e, top = int(opt.begin), int(opt.end), int(opt.top)
    try:
        rows = runner.run("wdr.top_sql", {"b": b, "e": e, "top": top})
    except access.QueryError as exc:
        return degraded(DIM_TOPSQL, summarize_err(exc))

    # each list item: dict-like tuple (id, query, calls, elapsed, cpu, spill, phys)
    items = [(str(r["sid"]), str(r["query"]), _i(r["calls"]), _i(r["elapsed_us"]),
              _i(r["cpu_us"]), _i(r["spill_kb"]), _i(r["phys_blocks"]))
             for r in rows]

    # **分母必须是窗口的真实 DB time,不能是本榜单的合计(2026-10-17 修)。**
    # 原来拿「Top N 行的 elapsed 之和」当分母,于是 Top N 截断得越狠、每条的占比越高 ——
    # 压测里一条跑了 1 秒的 TRUNCATE 被算成「占 DB time 37.7%」,还触发了
    # 「单条 SQL 占比过高」的告警。真实占比是 1.01s / 23518s ≈ 0.004%。
    # 拿不到真实 DB time 时**改列名**说明分母变了,不假装还是占 DB time。
    db_time_us = int(getattr(opt, "db_time_us", 0) or 0)
    list_total = sum(it[3] for it in items)
    total_elapsed = db_time_us if db_time_us > 0 else list_total
    pct_header = "占DB time%" if db_time_us > 0 else "占本榜合计%"

    d = DimResult(dimension=DIM_TOPSQL, available=True,
                  headers=["sql_id", "calls", "elapsed_s", "cpu_s", "spill_MB", "物理读(块)", pct_header, "query"])
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
                                  f"snap_summary_statement：sqlid {t_sid} elapsed {t_elapsed / 1e6:.1f}s ×{t_calls}"
                                  + (f"，窗口 DB time {total_elapsed / 1e6:.0f}s" if db_time_us > 0
                                     else "，分母是本榜合计(窗口 DB time 取不到)"),
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

def collect_waits(runner, opt: Options) -> DimResult:
    b, e, top = int(opt.begin), int(opt.end), int(opt.top)
    try:
        rows = runner.run("wdr.waits", {"b": b, "e": e, "top": top})
    except access.QueryError as exc:
        return degraded(DIM_WAITS, summarize_err(exc))
    items = [(str(r["wait_class"]), _i(r["waits"]), _i(r["wait_us"])) for r in rows]
    total = sum(it[2] for it in items)
    opt.wait_total_us = total          # 收尾时与 DB time 交叉校验(见 _check_consistency)

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

def collect_checkpoint(runner, opt: Options) -> DimResult:
    b, e = int(opt.begin), int(opt.end)
    try:
        rows = runner.run("wdr.checkpoint", {"b": b, "e": e})
    except access.QueryError as exc:
        return degraded(DIM_CHECKPOINT, summarize_err(exc))
    timed, req = _i(rows[0]["checkpoints_timed"]), _i(rows[0]["checkpoints_req"])
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

def collect_cache(runner, opt: Options) -> DimResult:
    b, e, top = int(opt.begin), int(opt.end), int(opt.top)
    try:
        rows = runner.run("wdr.cache", {"b": b, "e": e, "top": top})
    except access.QueryError as exc:
        return degraded(DIM_CACHE, summarize_err(exc))
    d = DimResult(dimension=DIM_CACHE, available=True, headers=["对象", "物理读(块)", "逻辑读(块)"])
    for r in rows:
        d.rows.append([str(r["snap_relname"]), i64(_i(r["phys_read"])),
                       i64(_i(r["logical_read"]))])
    if not d.rows:
        d.headline = "窗口内无显著物理读对象"
    else:
        d.headline = f"物理读 Top 对象：{d.rows[0][0]}（命中率详见 Database Stat）"
    return d


# --- File IO -----------------------------------------------------------------

def collect_fileio(runner, opt: Options) -> DimResult:
    b, e, top = int(opt.begin), int(opt.end), int(opt.top)
    try:
        rows = runner.run("wdr.file_io", {"b": b, "e": e, "top": top})
    except access.QueryError as exc:
        return degraded(DIM_FILEIO, summarize_err(exc))
    d = DimResult(dimension=DIM_FILEIO, available=True, headers=["文件", "物理读", "物理写"])
    for r in rows:
        d.rows.append([trunc(str(r["filename"]), 40), i64(_i(r["reads"])),
                       i64(_i(r["writes"]))])
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


# 等待时长超过 DB time 这么多倍,就认定 DB time 不可信(留足余量:两个计数器的
# 口径本来就不完全可比 —— DB time 按线程累加,等待事件也按线程累加,但采样边界不同)。
_DB_TIME_SANITY_FACTOR = 1.5


def _check_consistency(ev: Evidence, opt: Options) -> None:
    """跨维度自检:**DB time 比总等待时长还小,就不是「负载很轻」,是这个数不可信。**

    2026-10-17 压测抓到的真实后果:同一份报告里 DB time 写 2.67 秒、锁等待写 15371 秒,
    两者并存本身就说明前者是错的,而当时没有任何提示,负载维度还被判成「健康」,
    看报告的人完全无从察觉。取数的 bug 已经修了(见 window_db_time),这条校验是**兜底**:
    以后不管哪个数据源再出类似问题,至少会明说「这个数不可信」,而不是悄悄给个绿灯。
    """
    db_time_us = int(getattr(opt, "db_time_us", 0) or 0)
    wait_us = int(getattr(opt, "wait_total_us", 0) or 0)
    if db_time_us <= 0 or wait_us <= 0:
        return                      # 有一边没取到,不做这个判断(取不到已各自降级说明)
    if wait_us <= db_time_us * _DB_TIME_SANITY_FACTOR:
        return
    for d in ev.dims:
        if d.dimension != DIM_LOADPROFILE:
            continue
        note = ("DB time(%.1fs)比总等待时长(%.1fs)还小 —— 这个 DB time 不可信,"
                "不要据此判断负载轻重,改看等待事件与原生 WDR。"
                % (db_time_us / 1e6, wait_us / 1e6))
        d.note = (d.note + "；" + note) if d.note else note
        d.findings.append(Finding(
            DIM_LOADPROFILE, "WDR_DBTIME_INCONSISTENT", Severity.WARN,
            "DB time 与等待时长矛盾",
            "DB time %.1fs < 等待 %.1fs" % (db_time_us / 1e6, wait_us / 1e6),
            f">{_DB_TIME_SANITY_FACTOR:g}×",
            "snap_global_instance_time DB_TIME 增量 vs snap_global_wait_events 总等待 —— "
            "两者不可能是这个关系,DB time 取数有问题"))
        ev.findings.extend(d.findings[-1:])
        return


def collect_evidence(runner, opt: Options) -> Evidence:
    """Run every collector over the window and assemble evidence. Per-collector
    failures degrade; only the window/native prep can fail (propagated)."""
    if opt.top <= 0:
        opt.top = 10
    ev = Evidence()
    ev.window = load_window(runner, opt)            # validate snaps + scope/node + wdr-enabled
    ev.native = generate_native(runner, opt, ev.window)  # best-effort留底
    for fn in registry():
        d = fn(runner, opt)
        ev.dims.append(d)
        ev.findings.extend(d.findings)
    _check_consistency(ev, opt)                     # 跨维度兜底,必须在全部维度采完之后
    ev.findings.sort(key=lambda f: int(f.severity), reverse=True)
    ev.overall = worst([f.severity for f in ev.findings])
    return ev
