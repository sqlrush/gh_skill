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
from common.grmp.values import as_bool, as_float, as_int, is_null  # noqa: E402




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
        age_str = "—" if is_null(age) else f"{as_float(age):.0f}"
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



def _chain_depth(session, blocked_by: dict, limit: int = 32) -> int:
    """从某个等待者往上追到根，返回它上面压着几层。

    带环保护：真实现场出现过互相等待（虽然内核会检测死锁并中断其中一方，
    但快照可能正好抓在检测之前）。没有 seen 集合的话这里会死循环，
    而健康检查挂住比报错更难排查。
    """
    depth, seen, cur = 0, {session}, blocked_by.get(session)
    while cur is not None and cur not in seen and depth < limit:
        depth += 1
        seen.add(cur)
        cur = blocked_by.get(cur)
    return depth


def collect_locks(runner, th: Thresholds, top: int) -> DimResult:
    """锁等待的逐条明细 + 阻塞链结构。

    原先只输出「根阻塞 pid / 链深 / 被阻数 / 状态 / 时长」五个数 —— 三层堆积、
    十几个会话在报告里只剩一行。要能据此行动，至少得知道：等的是什么锁、
    锁在哪个对象上、阻塞者是谁、它在跑什么、开着事务多久了。
    """
    try:
        rows = runner.run("health.lock_chain", {"limit": max(top, 50)})
    except access.QueryError as exc:
        return degraded(DIM_LOCKS, summarize_err(exc))

    d = DimResult(dimension=DIM_LOCKS, available=True,
                  headers=["等待会话", "锁模式", "锁对象", "阻塞会话",
                           "阻塞方状态", "事务时长(s)", "阻塞方语句"])
    if not rows:
        d.headline = "无阻塞"
        return d

    blocked_by, by_waiter = {}, {}
    for row in rows:
        waiter = as_int(row["waiter_session"])
        blocked_by[waiter] = as_int(row["blocker_session"])
        by_waiter[waiter] = row

    # 根 = 阻塞了别人、自己却没被阻塞的会话
    roots = {b for b in blocked_by.values() if b not in blocked_by}
    waiters_of = {r: 0 for r in roots}
    depth_of = {r: 0 for r in roots}
    for waiter in blocked_by:
        cur, hops = waiter, 0
        seen = {waiter}
        while cur in blocked_by and blocked_by[cur] not in seen and hops < 32:
            cur = blocked_by[cur]
            seen.add(cur)
            hops += 1
        if cur in waiters_of:
            waiters_of[cur] += 1
            depth_of[cur] = max(depth_of[cur], _chain_depth(waiter, blocked_by))

    worst_sev, worst_line = Severity.OK, ""
    for waiter, row in sorted(by_waiter.items(),
                              key=lambda kv: -_f(kv[1]["blocker_xact_age_s"])):
        blocker = as_int(row["blocker_session"])
        state = row["blocker_state"]
        secs = (_f(row["blocker_state_age_s"]) if state == "idle in transaction"
                else _f(row["blocker_xact_age_s"]))
        d.rows.append([i64(waiter), row["lockmode"] or "?",
                       (row["locktag"] or "?")[:28], i64(blocker),
                       state or "?", f"{secs:.0f}",
                       (row["blocker_query"] or "").replace("\n", " ")[:70]])

    for root in sorted(roots, key=lambda r: -waiters_of.get(r, 0)):
        row = next((r for r in by_waiter.values()
                    if as_int(r["blocker_session"]) == root), None)
        if row is None:
            continue
        state = row["blocker_state"]
        secs = (_f(row["blocker_state_age_s"]) if state == "idle in transaction"
                else _f(row["blocker_xact_age_s"]))
        depth, n_waiters = depth_of.get(root, 1), waiters_of.get(root, 1)
        sev = sev_by_duration(secs, th.block_notice, th.block_warn, th.block_crit)
        if depth > th.block_chain_warn_depth and sev < Severity.WARN:
            sev = Severity.WARN
        if state == "idle in transaction":
            # 事务开着却什么都不干 —— 阻塞纯属占着不放，比正在执行的更该处理
            sev = escalate(sev)
        if sev == Severity.OK:
            continue
        query = (row["blocker_query"] or "").replace("\n", " ")[:120]
        d.findings.append(Finding(
            DIM_LOCKS, "LOCK_BLOCKING_CHAIN", sev,
            f"阻塞源 session {root}（{state or '状态未知'}）",
            f"{n_waiters} 个会话被阻，链深 {depth}，事务已开 {secs:.0f}s",
            ">阻塞时长/链深阈值",
            f"锁模式 {row['lockmode'] or '?'}，对象 {row['locktag'] or '?'}；"
            f"阻塞方语句：{query or '(取不到)'}"))
        if sev > worst_sev:
            worst_sev = sev
            worst_line = (f"阻塞源 session {root}({state})，"
                          f"{n_waiters} 个会话被阻，链深 {depth}，{secs:.0f}s")
    d.headline = worst_line if worst_line else f"{len(rows)} 处等待，均未达阈值"
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
        if not is_null(val):
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
        if not is_null(lag) and as_int(lag) > th.repl_lag_notice:
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
    # 观测窗口：idx_scan 是**自上次统计重置以来**的累计值，不知道「以来」
    # 是多久，等于 0 什么都说明不了。统计刚重置时全库索引都是 0。
    window_text, window_seconds = "无法确定", -1.0
    try:
        wrows = runner.run("health.stats_window")
        if wrows:
            window_seconds = _f(wrows[0]["window_seconds"])
            reset = wrows[0]["stats_reset"]
            window_text = (f"自 {reset} 起 {window_seconds / 86400.0:.1f} 天"
                           if window_seconds >= 0 else "统计从未重置过")
    except access.QueryError:
        pass
    d.rows.append(["观测窗口", "pg_stat_database.stats_reset", window_text])

    for row in rows:
        name, sz = row["idx_name"], as_int(row["idx_bytes"])
        scans = as_int(row["idx_scan"])
        constraint = as_bool(row["indisprimary"]) or as_bool(row["indisunique"])
        table_idx = as_int(row["table_idx_scan"])
        table_seq = as_int(row["table_seq_scan"])
        kind = "约束索引" if constraint else "普通索引"
        d.rows.append([kind, name,
                       f"{human_bytes(sz)} 扫描 {scans} 次"
                       f"（该表索引扫描共 {table_idx}、顺扫 {table_seq}）"])
        if scans > 0:
            continue
        if constraint:
            # 主键/唯一约束背书的索引，不论用量都不能删 —— 列出来只为让人
            # 看到这张表上还有什么，不产生任何建议。
            continue
        if window_seconds >= 0 and window_seconds < th.index_unused_min_window_s:
            # 窗口太短，0 次不构成证据。**不出这条结论**，而不是出了再加个警告。
            continue
        n_unused += 1
        context = (f"该表其他索引共被扫描 {table_idx} 次"
                   if table_idx > 0 else "该表所有索引在本窗口内均未被扫描")
        d.findings.append(Finding(
            DIM_SCHEMA, "INDEX_UNUSED", Severity.NOTICE,
            # 措辞不能写死成「无用」：只是**这个观测窗口内**没被用到。
            f"{name} 在当前统计窗口内未被使用",
            f"{human_bytes(sz)}，idx_scan=0，窗口 {window_text}",
            f">{human_bytes(th.index_unused_bytes)} 且窗口内 0 次扫描",
            f"pg_stat_user_indexes.idx_scan=0；{context}。"
            f"反例需排除后再决定是否删除：统计重置或实例重启会让计数归零；"
            f"月度/季度/年终报表用的索引可以数周为 0；"
            f"主备分离时备机上的使用不计入本机计数器。"))
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
        frozen_pages = _f(row["frozen_pages"])
        cur_pages = _f(row["cur_pages"])
        stat_columns = as_int(row["stat_columns"])
        last_analyze = row["last_analyze"]
        drift = (abs(cur_pages - frozen_pages) / frozen_pages
                 if frozen_pages > 0 else None)
        drift_text = "无法计算" if drift is None else f"{drift * 100:.1f}%"
        d.rows.append(["统计新鲜度", name,
                       f"冻结 {frozen_pages:.0f} 页 / 实时 {cur_pages:.0f} 页"
                       f"（偏离 {drift_text}），pg_stats {stat_columns} 列，"
                       f"上次 ANALYZE {last_analyze}"])

        # 判据只用不会被 pg_stat_reset() 清掉的信号。og5 实测过：
        # gsbench.fact_sales 报 last_analyze=never、n_live_tup=0，但 pg_stats
        # 里有 8 列统计信息 —— 拿 last_analyze 当判据会把好表判成从未分析。
        if stat_columns == 0:
            reason = ("pg_stats 里一列统计信息都没有 —— 该表确实从未被 ANALYZE "
                      "覆盖（或统计被删过），规划器只能靠默认估算。")
        elif drift is None:
            reason = "pg_class.relpages 为 0，没有可比对的冻结基准。"
        elif drift > th.stale_page_drift:
            reason = (f"冻结页数 {frozen_pages:.0f} 与实时页数 {cur_pages:.0f} "
                      f"相差 {drift_text}，超过阈值 {th.stale_page_drift * 100:.0f}% "
                      f"—— 表在上次 ANALYZE 之后长大了，而 n_distinct、"
                      f"correlation 不会跟着更新。")
        else:
            continue
        n_stale += 1
        d.findings.append(Finding(
            DIM_SCHEMA, "STALE_STATS", Severity.NOTICE,
            f"{name} 的统计信息不足以支撑计划估算", reason,
            f"页偏离>{th.stale_page_drift * 100:.0f}% 或无统计列",
            f"pg_class.relpages={frozen_pages:.0f}，实时页数={cur_pages:.0f}，"
            f"pg_stats 统计列数={stat_columns}，reltuples={as_int(row['frozen_tuples'])}，"
            f"n_live_tup={as_int(row['live_tuples'])}。"
            f"（last_analyze={last_analyze} 仅供参考：该计数器可被 pg_stat_reset "
            f"清除，而 ANALYZE 的成果存在 pg_statistic 里不受影响，"
            f"所以它不参与本判定。）"))
    # 措辞不写「无用索引」：那是个关于未来的断言，而我们只观测到了一个窗口。
    d.headline = (f"窗口内未使用的索引 {n_unused}、失效索引 {invalid}、"
                  f"统计不足以支撑估算的表 {n_stale}（观测窗口 {window_text}）")
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
