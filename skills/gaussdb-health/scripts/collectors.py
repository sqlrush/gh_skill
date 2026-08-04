"""The 12 read-only health collectors — port of internal/probe/health/*.go.

Each collector takes (runner, thresholds, top) and returns a DimResult. Collectors
never raise: on query failure they return degraded(dim, reason) so one missing
view / permission gap cannot abort the whole check.
"""
from __future__ import annotations

from model import (
    DIM_BLOAT, DIM_CONCURRENCY, DIM_CONN, DIM_LOCKS, DIM_LOGS, DIM_LWLOCK,
    DIM_OVERVIEW, DIM_REPL, DIM_SCHEMA, DIM_SLOWSQL, DIM_WAITS, DIM_XACT,
    DimResult, Finding, Severity, degraded,
)
from thresholds import Thresholds, go_duration
from util import (
    escalate, f2, human_bytes, i64, sev_by_duration, summarize_err, trunc,
)

# common is resolved on sys.path by the entry script (health.py).
import common  # noqa: E402


import pathlib
import sys

_HERE = pathlib.Path(__file__).resolve()

for parent in _HERE.parents:
    if (parent / "common" / "sql.py").exists():
        sys.path.insert(0, str(parent))
        break

# SQL 已迁到 scripts/registry/health/ —— 两条路径共用同一份定义
# 取数失败只认这一个类型。换一种数据库访问方式时，改的是访问模块，
# 不是这里 —— 详见 common/grmp/errors.py。
from common import access  # noqa: E402
# 结果值全是字符串：bool("f") 是 True、int("3704.0") 会抛异常。
# 类型还原一律走这里，不用裸 int()/float()/bool()。
from common.grmp.values import as_bool, as_float, as_int  # noqa: E402




def _f(x, default: float = 0.0) -> float:
    """Coerce a possibly-None numeric (Decimal/float) to float."""
    return default if x is None else float(x)


# --- overview ----------------------------------------------------------------




def collect_overview(runner, th: Thresholds, _top: int) -> DimResult:
    try:
        rows = runner.run("health.overview")
    except access.QueryError as exc:
        return degraded(DIM_OVERVIEW, summarize_err(exc))
    r = rows[0]
    cache_hit = _f(r["cache_hit_pct"])
    backends = as_int(r["numbackends"])
    max_conn = as_int(r["max_conn"])
    # bool("f") 是 True —— 结果值全是字符串，必须用 as_bool 还原
    in_recovery = as_bool(r["in_recovery"])
    oldest = as_int(r["oldest_xact_s"])
    oldest_str = f"{oldest}s" if oldest > 0 else "无"
    d = DimResult(dimension=DIM_OVERVIEW, available=True,
                  headers=["cache_hit%", "connections", "max_conn", "in_recovery", "最老事务"],
                  rows=[[f2(cache_hit), i64(backends), i64(max_conn),
                         "true" if in_recovery else "false", oldest_str]])
    if max_conn <= 0:
        d.headline = "max_connections 不可读"
        return d
    conn_pct = 100.0 * backends / max_conn
    if conn_pct > th.conn_pct_warn:
        d.findings.append(Finding(DIM_OVERVIEW, "CONN_HIGH", Severity.WARN, "连接使用率",
                                  f"{conn_pct:.0f}% ({backends}/{max_conn})",
                                  f">{th.conn_pct_warn:.0f}%", "pg_stat_database.numbackends"))
    elif conn_pct > th.conn_pct_notice:
        d.findings.append(Finding(DIM_OVERVIEW, "CONN_HIGH", Severity.NOTICE, "连接使用率",
                                  f"{conn_pct:.0f}% ({backends}/{max_conn})",
                                  f">{th.conn_pct_notice:.0f}%", "pg_stat_database.numbackends"))
    if cache_hit < th.cache_hit_warn:
        d.findings.append(Finding(DIM_OVERVIEW, "CACHE_LOW", Severity.WARN, "缓存命中率",
                                  f2(cache_hit) + "%", f"<{th.cache_hit_warn:.0f}%",
                                  "pg_stat_database blks_hit/read"))
    elif cache_hit < th.cache_hit_notice:
        d.findings.append(Finding(DIM_OVERVIEW, "CACHE_LOW", Severity.NOTICE, "缓存命中率",
                                  f2(cache_hit) + "%", f"<{th.cache_hit_notice:.0f}%",
                                  "pg_stat_database blks_hit/read"))
    d.headline = (f"命中率 {cache_hit:.1f}%、连接 {backends}/{max_conn}、"
                  f"{'恢复中' if in_recovery else '未在恢复'}、最老事务 {oldest_str}")
    return d


# --- wait events -------------------------------------------------------------



def collect_waits(runner, th: Thresholds, top: int) -> DimResult:
    try:
        rows = runner.run("health.waits")
    except access.QueryError as exc:
        return degraded(DIM_WAITS, summarize_err(exc))
    d = DimResult(dimension=DIM_WAITS, available=True, headers=["wait_status", "会话数"])
    total = top_cnt = 0
    top_wait = ""
    for n, row in enumerate(rows):
        ws, cnt = row["wait_status"], as_int(row["cnt"])
        total += cnt
        if n < top:
            d.rows.append([ws, i64(cnt)])
        if cnt > top_cnt:
            top_cnt, top_wait = cnt, ws
    if total >= 5 and top_cnt > 0:
        conc = 100.0 * top_cnt / total
        sev = Severity.OK
        if conc > th.wait_conc_warn:
            sev = Severity.WARN
        elif conc > th.wait_conc_notice:
            sev = Severity.NOTICE
        if sev != Severity.OK:
            thr = th.wait_conc_warn if sev == Severity.WARN else th.wait_conc_notice
            d.findings.append(Finding(DIM_WAITS, "WAIT_CONCENTRATION", sev, "等待集中度",
                                      f"{conc:.0f}% 在 {top_wait}",
                                      f">{thr:.0f}%（共{total}等待）", "pg_thread_wait_status"))
        d.headline = f"{total} 会话等待，{conc:.0f}% 在 {top_wait}"
    else:
        d.headline = f"等待会话 {total}（无显著集中）"
    return d


# --- slow SQL ----------------------------------------------------------------




def collect_slowsql(runner, th: Thresholds, top: int) -> DimResult:
    try:
        rows = runner.run("health.slow_sql",
                          {"threshold_ms": int(th.slow_sql_avg_ms), "limit": top})
    except access.QueryError as exc:
        return degraded(DIM_SLOWSQL, summarize_err(exc))
    d = DimResult(dimension=DIM_SLOWSQL, available=True,
                  headers=["sql_id", "calls", "avg_ms", "total_s", "cpu_s", "query"])
    stmts = []
    for row in rows:
        sql_id = row["unique_sql_id"]
        query = row["query"]
        calls = as_int(row["calls"])
        avg_ms = _f(row["avg_ms"])
        total_s = _f(row["total_sec"])
        cpu_s = _f(row["cpu_sec"])
        stmts.append((sql_id, query, calls, avg_ms, total_s, cpu_s))
        d.rows.append([sql_id, i64(calls), f2(avg_ms), f2(total_s), f2(cpu_s), trunc(query, 50)])
    if stmts:
        sid, _q, calls, avg_ms, total_s, cpu_s = stmts[0]
        d.findings.append(Finding(DIM_SLOWSQL, "SLOWSQL_TOP", Severity.NOTICE, "慢 SQL",
                                  f"Top1 avg {avg_ms:.0f}ms ×{calls}",
                                  f">{th.slow_sql_avg_ms}ms", "dbe_perf.statement（/gaussdb-sqltune 深调）"))
        # CPU-light guard (DB-time trap): the slowest statement whose CPU is a
        # tiny fraction of its elapsed time is NOT compute/index-bound — time
        # went to waiting (locks/sleep) or I/O. Don't reach for sqltune first.
        if total_s > 0:
            cpu_ratio = cpu_s / total_s
            if cpu_ratio < th.slow_sql_low_cpu_ratio:
                d.findings.append(Finding(
                    DIM_SLOWSQL, "SLOWSQL_LOW_CPU", Severity.NOTICE, "慢但 CPU 极低",
                    f"Top1 CPU 仅占其耗时 {cpu_ratio * 100:.1f}%",
                    f"<{th.slow_sql_low_cpu_ratio * 100:.0f}%",
                    f"sqlid {sid} 总耗时 {total_s:.1f}s / CPU {cpu_s:.1f}s → 非 CPU 消耗主导"
                    "（锁等待/睡眠 或 I/O/排序溢出），非缺索引类资源问题；勿直接按缺索引上 "
                    "sqltune，先查锁与等待事件、必要时看临时文件/排序"))
        d.headline = f"Top1 avg {avg_ms:.0f}ms ×{calls}（共{len(stmts)}条超阈值）"
    else:
        d.headline = "无超阈值慢 SQL"
    return d


# --- long & idle transactions ------------------------------------------------



def _xact_threshold(code: str, sev: Severity, th: Thresholds) -> str:
    n, w, c = th.long_xact_notice, th.long_xact_warn, th.long_xact_crit
    if code == "XACT_IDLE":
        n, w, c = th.idle_xact_notice, th.idle_xact_warn, th.idle_xact_crit
    if sev == Severity.CRITICAL:
        return ">" + go_duration(c)
    if sev == Severity.WARN:
        return ">" + go_duration(w)
    return ">" + go_duration(n)


def collect_xact(runner, th: Thresholds, top: int) -> DimResult:
    try:
        rows = runner.run("health.long_xact", {"limit": top})
    except access.QueryError as exc:
        return degraded(DIM_XACT, summarize_err(exc))
    d = DimResult(dimension=DIM_XACT, available=True,
                  headers=["pid", "user", "state", "时长(s)", "query"])
    worst_sev = Severity.OK
    worst_line = ""
    n_rows = 0
    max_secs = 0.0
    for row in rows:
        pid = as_int(row["pid"])
        user, state = row["usename"], row["state"]
        xact_age, state_age = _f(row["xact_age_s"]), _f(row["state_age_s"])
        query = row["query"]
        if state == "idle in transaction":
            secs, code = state_age, "XACT_IDLE"
            sev = sev_by_duration(secs, th.idle_xact_notice, th.idle_xact_warn, th.idle_xact_crit)
        else:
            secs, code = xact_age, "XACT_LONG"
            sev = sev_by_duration(secs, th.long_xact_notice, th.long_xact_warn, th.long_xact_crit)
        dur_str = f"{secs:.0f}"
        d.rows.append([i64(pid), user, state, dur_str, trunc(query, 60)])
        n_rows += 1
        if secs > max_secs:
            max_secs = secs
        if sev != Severity.OK:
            d.findings.append(Finding(DIM_XACT, code, sev, f"pid {pid} {state}",
                                      dur_str + "s", _xact_threshold(code, sev, th),
                                      f"pg_stat_activity pid={pid}"))
            if sev > worst_sev:
                worst_sev = sev
                worst_line = f"{code} pid {pid} {dur_str}s"
    if worst_line:
        d.headline = worst_line
    elif n_rows > 0:
        d.headline = f"{n_rows} 个客户端事务，均在阈值内（最长 {max_secs:.0f}s）"
    else:
        d.headline = "无活动客户端事务"
    return d


# --- dead tuples & bloat -----------------------------------------------------



def collect_bloat(runner, th: Thresholds, top: int) -> DimResult:
    try:
        rows = runner.run("health.bloat", {"limit": top})
    except access.QueryError as exc:
        return degraded(DIM_BLOAT, summarize_err(exc))
    d = DimResult(dimension=DIM_BLOAT, available=True,
                  headers=["table", "live", "dead", "dead%", "autovacuum前(s)", "autovacuum"])
    worst_ratio = 0.0
    worst_tbl = ""
    for row in rows:
        sch, rel = row["schemaname"], row["relname"]
        live, dead = as_int(row["n_live_tup"]), as_int(row["n_dead_tup"])
        age = row["last_autovacuum_age_s"]
        autovac = as_bool(row["autovac_enabled"])
        ratio = 100.0 * dead / max(live + dead, 1)
        age_str = "—" if age is None else f"{float(age):.0f}"
        av_str = "on" if autovac else "off"
        d.rows.append([f"{sch}.{rel}", i64(live), i64(dead), f2(ratio), age_str, av_str])
        if dead > th.dead_tup_min:
            sev = Severity.OK
            if ratio > th.dead_ratio_warn:
                sev = Severity.WARN
            elif ratio > th.dead_ratio_notice:
                sev = Severity.NOTICE
            if sev != Severity.OK:
                thr = th.dead_ratio_warn if sev == Severity.WARN else th.dead_ratio_notice
                d.findings.append(Finding(
                    DIM_BLOAT, "BLOAT_DEAD_RATIO", sev, f"{sch}.{rel} dead_ratio",
                    f2(ratio) + "%", f">{thr:.0f}% 且 dead>{th.dead_tup_min}",
                    f"pg_stat_user_tables dead={dead} live={live} autovacuum={av_str}"))
                if ratio > worst_ratio:
                    worst_ratio, worst_tbl = ratio, f"{sch}.{rel}"
    d.headline = f"{worst_tbl} dead {worst_ratio:.0f}%" if worst_tbl else "无显著膨胀"
    return d


# --- lightweight locks -------------------------------------------------------




def collect_lwlock(runner, th: Thresholds, top: int) -> DimResult:
    try:
        rows = runner.run("health.lwlock", {"limit": top})
    except access.QueryError as exc:
        return degraded(DIM_LWLOCK, summarize_err(exc))
    d = DimResult(dimension=DIM_LWLOCK, available=True, headers=["lwlock", "等待会话数"])
    hot = ""
    hot_cnt = 0
    for row in rows:
        evt, cnt = row["evt"], as_int(row["cnt"])
        d.rows.append([evt, i64(cnt)])
        if cnt >= th.lwlock_sessions and cnt > hot_cnt:
            hot, hot_cnt = evt, cnt
    if hot:
        d.findings.append(Finding(DIM_LWLOCK, "LWLOCK_HOT", Severity.NOTICE, "热点轻量锁",
                                  f"{hot} ×{hot_cnt} 会话", f"≥{th.lwlock_sessions} 会话",
                                  "pg_thread_wait_status lwlock"))
        d.headline = f"热点 {hot}（{hot_cnt} 会话等待）"
    else:
        d.headline = "无持续 LWLock 热点"
    return d


# --- transaction locks & blocking chains -------------------------------------



def collect_locks(runner, th: Thresholds, top: int) -> DimResult:
    try:
        rows = runner.run("health.lock_chain", {"limit": top})
    except access.QueryError as exc:
        return degraded(DIM_LOCKS, summarize_err(exc))
    d = DimResult(dimension=DIM_LOCKS, available=True,
                  headers=["根阻塞pid", "链深", "被阻数", "根状态", "时长(s)"])
    worst_sev = Severity.OK
    worst_line = ""
    for row in rows:
        root, depth, waiters = (as_int(row["root"]), as_int(row["depth"]),
                                as_int(row["waiters"]))
        state = row["state"]
        secs = (_f(row["state_age_s"]) if state == "idle in transaction"
                else _f(row["xact_age_s"]))
        sev = sev_by_duration(secs, th.block_notice, th.block_warn, th.block_crit)
        if depth > th.block_chain_warn_depth and sev < Severity.WARN:
            sev = Severity.WARN
        if state == "idle in transaction":
            sev = escalate(sev)
        d.rows.append([i64(root), i64(depth), i64(waiters), state, f"{secs:.0f}"])
        if sev != Severity.OK:
            d.findings.append(Finding(DIM_LOCKS, "LOCK_BLOCKING_CHAIN", sev,
                                      f"根阻塞 pid {root}",
                                      f"链深{depth} 阻{waiters} {secs:.0f}s",
                                      ">阻塞时长/链深阈值",
                                      f"pg_locks/pg_stat_activity root={root} state={state}"))
            if sev > worst_sev:
                worst_sev = sev
                worst_line = f"根阻塞 pid {root}({state}), 链深 {depth}, 阻 {waiters}"
    d.headline = worst_line if worst_line else "无阻塞"
    return d


# --- connections -------------------------------------------------------------


#_CONN_Q = ("SELECT COALESCE(state,'<null>') AS state, count(*) "
#           "FROM pg_stat_activity GROUP BY state ORDER BY 2 DESC")



def collect_conn(runner, th: Thresholds, _top: int) -> DimResult:
    try:
        rows = runner.run("health.conn_states")
    except access.QueryError as exc:
        return degraded(DIM_CONN, summarize_err(exc))
    d = DimResult(dimension=DIM_CONN, available=True, headers=["state", "会话数"])
    total = active = idle = iit = 0
    for row in rows:
        st, cnt = row["state"], as_int(row["cnt"])
        d.rows.append([st, i64(cnt)])
        total += cnt
        if st == "active":
            active = cnt
        elif st == "idle":
            idle = cnt
        elif st == "idle in transaction":
            iit = cnt
    if active > th.active_warn:
        d.findings.append(Finding(DIM_CONN, "ACTIVE_HIGH", Severity.WARN, "活跃会话数",
                                  i64(active), f">{th.active_warn}", "pg_stat_activity state=active"))
    elif active > th.active_notice:
        d.findings.append(Finding(DIM_CONN, "ACTIVE_HIGH", Severity.NOTICE, "活跃会话数",
                                  i64(active), f">{th.active_notice}", "pg_stat_activity state=active"))
    if active >= th.active_conc_floor:
        try:
            r2 = runner.run("health.conn_concentration")
        except access.QueryError:
            r2 = []
        if r2:
            top_q = r2[0]["q"]
            top_c = as_int(r2[0]["c"])
            real_total = as_int(r2[0]["total"] or 0)
            if real_total >= th.active_conc_floor and top_c > 0:
                conc = 100.0 * top_c / real_total
                if conc >= th.active_conc_pct:
                    d.findings.append(Finding(
                        DIM_CONN, "ACTIVE_SQL_HOT", Severity.NOTICE, "活跃集中在单条 SQL",
                        f"{conc:.0f}% ({top_c}/{real_total}) 在: {trunc(top_q, 40)}",
                        f">{th.active_conc_pct:.0f}%", "pg_stat_activity active client SQL"))
    d.headline = f"共 {total}：active {active}、idle {idle}、IIT {iit}"
    return d


# --- checkpoint / WAL / archiving --------------------------------------------

def collect_logs(runner, th: Thresholds, _top: int) -> DimResult:
    d = DimResult(dimension=DIM_LOGS, available=True, headers=["指标", "值"])
    try:
        rows = runner.run("health.bgwriter")
    except access.QueryError as exc:
        return degraded(DIM_LOGS, summarize_err(exc))
    timed, req = (as_int(rows[0]["checkpoints_timed"]),
                  as_int(rows[0]["checkpoints_req"]))
    req_pct = 100.0 * req / (timed + req) if timed + req > 0 else 0.0
    d.rows.append(["checkpoint timed/req", f"{timed}/{req}"])
    d.rows.append(["checkpoint req 占比", f2(req_pct) + "%"])
    sev = Severity.OK
    thr = th.ckpt_req_notice
    if req_pct > th.ckpt_req_warn:
        sev, thr = Severity.WARN, th.ckpt_req_warn
    elif req_pct > th.ckpt_req_notice:
        sev = Severity.NOTICE
    if sev != Severity.OK:
        d.findings.append(Finding(DIM_LOGS, "CKPT_PRESSURE", sev, "checkpoint 请求占比",
                                  f2(req_pct) + "%", f">{thr:.0f}%", "pg_stat_bgwriter checkpoints_req"))
    am = "未知"
    try:
        _am = runner.run("health.archive_mode")
        val = _am[0]["setting"] if _am else None
        if val is not None:
            am = str(val)
            d.rows.append(["archive_mode", am])
    except access.QueryError:
        pass
    d.headline = f"checkpoint req 占比 {req_pct:.0f}%、归档 {am}"
    return d


# --- replication / standby ---------------------------------------------------



def collect_repl(runner, th: Thresholds, _top: int) -> DimResult:
    try:
        rows = runner.run("health.replication")
    except access.QueryError as exc:
        return degraded(DIM_REPL, summarize_err(exc))
    d = DimResult(dimension=DIM_REPL, available=True,
                  headers=["standby", "client", "state", "sync", "replay_lag"])
    n = 0
    for row in rows:
        app, caddr, state, sync = (row["application_name"], row["client_addr"],
                                   row["state"], row["sync_state"])
        lag = row["lag_bytes"]
        n += 1
        d.rows.append([trunc(app, 24), caddr, state, sync, human_bytes(int(lag or 0))])
        if state != "Streaming":
            d.findings.append(Finding(DIM_REPL, "REPL_NOT_STREAMING", Severity.WARN,
                                      f"备库 {app} 状态", state, "=Streaming",
                                      "pg_stat_replication.state"))
        if lag is not None and int(lag) > th.repl_lag_notice:
            sev = Severity.NOTICE
            thr = th.repl_lag_notice
            if int(lag) > th.repl_lag_warn:
                sev, thr = Severity.WARN, th.repl_lag_warn
            d.findings.append(Finding(DIM_REPL, "REPL_LAG", sev, f"备库 {app} replay 延迟",
                                      human_bytes(int(lag)), ">" + human_bytes(thr),
                                      "pg_stat_replication sent vs replay"))
    d.headline = "无下游备库（单机，或本节点为备库）" if n == 0 else f"{n} 个备库"
    return d


# --- schema / objects --------------------------------------------------------

_SCHEMA_SYS_FILTER = ("('pg_catalog','information_schema','snapshot','dbe_perf',"
                      "'dbe_pldeveloper','cstore','pg_toast')")


#_UNUSED_IDX_Q = """
#SELECT s.schemaname||'.'||s.indexrelname, pg_relation_size(s.indexrelid)
#FROM pg_stat_user_indexes s
#JOIN pg_index i ON i.indexrelid = s.indexrelid
#WHERE s.idx_scan=0 AND pg_relation_size(s.indexrelid) > %s
#  AND NOT i.indisprimary AND NOT i.indisunique
#  AND s.schemaname NOT IN """ + _SCHEMA_SYS_FILTER + """
#ORDER BY pg_relation_size(s.indexrelid) DESC LIMIT %s"""


#_STALE_STATS_Q = """
#SELECT schemaname||'.'||relname FROM pg_stat_user_tables
#WHERE n_live_tup > %s AND schemaname NOT IN """ + _SCHEMA_SYS_FILTER + """
#  AND (last_analyze IS NULL OR (last_data_changed IS NOT NULL AND last_data_changed > last_analyze))
#ORDER BY n_live_tup DESC LIMIT %s"""


def collect_schema(runner, th: Thresholds, top: int) -> DimResult:
    d = DimResult(dimension=DIM_SCHEMA, available=True, headers=["项", "对象", "值"])
    n_unused = n_stale = 0
    invalid = 0
    # 1) unused indexes (failure of this primary query degrades the dimension)
    try:
        rows = runner.run("health.unused_index",
                          {"min_bytes": int(th.index_unused_bytes),
                           "schema_filter": _SCHEMA_SYS_FILTER, "limit": top})
    except access.QueryError as exc:
        return degraded(DIM_SCHEMA, summarize_err(exc))
    for row in rows:
        name, sz = row["idx_name"], as_int(row["idx_bytes"])
        d.rows.append(["无用索引", name, human_bytes(sz)])
        d.findings.append(Finding(DIM_SCHEMA, "INDEX_UNUSED", Severity.NOTICE,
                                  "无用索引 " + name, human_bytes(sz) + " idx_scan=0",
                                  ">" + human_bytes(th.index_unused_bytes), "pg_stat_user_indexes"))
        n_unused += 1
    # 2) invalid indexes (best-effort)
    try:
        _iv = runner.run("health.invalid_index")
        invalid = int(_iv[0]["cnt"] if _iv else 0)
    except access.QueryError:
        invalid = 0
    if invalid > 0:
        d.rows.append(["失效索引", "(invalid)", i64(invalid)])
        d.findings.append(Finding(DIM_SCHEMA, "INDEX_INVALID", Severity.WARN,
                                  "失效索引数", i64(invalid), ">0", "pg_index.indisvalid=false"))
    # 3) stale stats (best-effort)
    try:
        srows = runner.run("health.stale_stats",
                           {"min_rows": int(th.stale_min_rows),
                            "schema_filter": _SCHEMA_SYS_FILTER, "limit": top})
    except access.QueryError:
        srows = []
    for row in srows:
        name = row["tbl_name"]
        d.rows.append(["统计陈旧", name, "数据已变更未 analyze"])
        d.findings.append(Finding(DIM_SCHEMA, "STALE_STATS", Severity.NOTICE,
                                  "统计陈旧 " + name, "last_analyze 落后于数据变更",
                                  f"行>{th.stale_min_rows}",
                                  "pg_stat_user_tables last_analyze/last_data_changed"))
        n_stale += 1
    d.headline = f"无用索引 {n_unused}、失效索引 {invalid}、统计陈旧 {n_stale}"
    return d


# --- transactions / concurrency ----------------------------------------------

def collect_concurrency(runner, th: Thresholds, _top: int) -> DimResult:
    d = DimResult(dimension=DIM_CONCURRENCY, available=True, headers=["指标", "值"])
    try:
        rows = runner.run("health.db_concurrency")
    except access.QueryError as exc:
        return degraded(DIM_CONCURRENCY, summarize_err(exc))
    deadlocks, commit, rollback = (as_int(rows[0]["deadlocks"]),
                                   as_int(rows[0]["xact_commit"]),
                                   as_int(rows[0]["xact_rollback"]))
    total = commit + rollback
    rb_pct = 100.0 * rollback / total if total > 0 else 0.0
    d.rows.append(["deadlocks", i64(deadlocks)])
    d.rows.append(["commit/rollback", f"{commit}/{rollback} ({rb_pct:.1f}%回滚)"])
    if deadlocks > 0:
        d.findings.append(Finding(DIM_CONCURRENCY, "DEADLOCKS", Severity.NOTICE,
                                  "死锁累计数", i64(deadlocks), ">0", "pg_stat_database.deadlocks"))
    if total > th.rollback_floor and rb_pct > th.rollback_pct:
        d.findings.append(Finding(DIM_CONCURRENCY, "ROLLBACK_HIGH", Severity.NOTICE,
                                  "事务回滚率", f"{rb_pct:.1f}%", f">{th.rollback_pct:.0f}%",
                                  "pg_stat_database commit/rollback"))
    prepared = 0
    try:
        _px = runner.run("health.prepared_xacts")
        prepared = int(_px[0]["cnt"] if _px else 0)
        d.rows.append(["prepared 2PC", i64(prepared)])
        if prepared > 0:
            d.findings.append(Finding(DIM_CONCURRENCY, "PREPARED_XACT", Severity.WARN,
                                      "悬挂的两阶段事务", i64(prepared), ">0", "pg_prepared_xacts"))
    except access.QueryError:
        pass
    d.headline = f"死锁 {deadlocks}、回滚率 {rb_pct:.1f}%、2PC {prepared}"
    return d


# --- registry ----------------------------------------------------------------

def registry():
    """Ordered (key, collector_fn) list — order is the report's section order."""
    return [
        ("overview", collect_overview),
        ("waits", collect_waits),
        ("slowsql", collect_slowsql),
        ("xact", collect_xact),
        ("bloat", collect_bloat),
        ("lwlock", collect_lwlock),
        ("locks", collect_locks),
        ("conn", collect_conn),
        ("logs", collect_logs),
        ("repl", collect_repl),
        ("schema", collect_schema),
        ("concurrency", collect_concurrency),
    ]
