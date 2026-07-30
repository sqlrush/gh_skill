"""L4 SQL / L5 operator collectors (WLM resource tracking).

Live and historical share one code path — only the view slot differs — because
`gs_wlm_session_statistics` and `gs_wlm_session_history` carry the same columns
under different names of the same shape. Column adaptation (probe.columns_expr)
absorbs whatever this version actually has.

When a layer is blind, it degrades *with the capability reason*: an empty
operator table would read as "no operator problems", which is exactly the lie
this skill exists to prevent.
"""
from __future__ import annotations

import common
import probe
from model import (
    DIM_OPERATOR, DIM_SQL, Capability, Catalog, DimResult, Finding, Severity,
    degraded,
)
from thresholds import Thresholds
from util import f, human_mb, i64, trunc

_SQL_COLS = ("queryid", "query", "start_time", "duration", "estimate_memory",
             "max_peak_memory", "max_spill_size", "warning")

_OP_COLS = ("queryid", "plan_node_id", "plan_node_name", "duration",
            "estimated_rows", "tuple_processed", "max_peak_memory",
            "max_spill_size", "memory_skew_percent", "warning")

# A resolvable-but-empty WLM view is the trap this skill exists to avoid: the
# capability line says ✓, the table renders blank, and it reads as "no operator
# problems". Empirically, openGauss-lite 5.0.3 (single-node) never populates the
# operator views at all, even with resource_track_level=operator — the feature
# targets distributed GaussDB. Say that, rather than showing an innocent blank.
_EMPTY_OPERATOR_NOTE = (
    "视图存在且 resource_track_level=operator，但查不到任何算子记录。可能原因："
    "(a) 采样窗口内没有代价超过 resource_track_cost 的作业；"
    "(b) resource_track_duration 大于作业实际耗时（历史表只记录跑够该时长的作业）；"
    "(c) **openGauss 单机版不填充算子级视图**——算子级资源跟踪主要面向分布式 GaussDB。"
    "无论哪种，都不能据此认为「算子层没有问题」。"
)

_EMPTY_SQL_NOTE = (
    "视图存在且资源跟踪已开启，但查不到作业记录。可能原因："
    "(a) 采样窗口内没有代价超过 resource_track_cost 的作业；"
    "(b) 实时视图只显示**正在运行**的作业——冲高已结束时请改用 history 子命令；"
    "(c) 历史表受 resource_track_duration 约束，只记录跑够该时长的作业。"
    "不能据此认为「没有大内存 SQL」。"
)


def _order_by(vi, preferred: str) -> str:
    """ORDER BY only on a column this view really has — otherwise the query
    would fail on the dialects that lack it."""
    return f" ORDER BY {preferred} DESC NULLS LAST" if probe.has_col(vi, preferred) else ""


# --------------------------------------------------------------------------
# L4 — SQL level
# --------------------------------------------------------------------------
def collect_sql(db, cat: Catalog, cap: Capability, th: Thresholds, top: int,
                historical: bool = False) -> DimResult:
    if historical and not cap.history_available:
        return degraded(DIM_SQL, cap.reasons.get("history", "历史数据不可用"))
    if not historical and not cap.sql_available:
        return degraded(DIM_SQL, cap.reasons.get("L4", "SQL 级资源跟踪不可用"))

    vi = cat.get("wlm_session_hist" if historical else "wlm_session")
    if not vi.available:
        return degraded(DIM_SQL, vi.reason)

    q = (f"SELECT {probe.columns_expr(vi, _SQL_COLS)} FROM {vi.name}"
         f"{_order_by(vi, 'max_peak_memory')} LIMIT {int(top)}")
    try:
        _, rows = db.query(q)
    except common.DBError as exc:
        from util import summarize_err
        return degraded(DIM_SQL, summarize_err(exc))

    d = DimResult(dimension=DIM_SQL, available=True,
                  headers=["query_id", "耗时(ms)", "估算内存", "峰值内存",
                           "下盘", "warning", "SQL"])

    for r in rows:
        qid = str(r[0] or "")
        query = str(r[1] or "")
        duration = i64(r[3])
        est_mb, peak_mb, spill_mb = f(r[4]), f(r[5]), f(r[6])
        warning = str(r[7] or "")

        d.rows.append([qid, str(duration), human_mb(est_mb), human_mb(peak_mb),
                       human_mb(spill_mb), trunc(warning, 30), trunc(query, 60)])

        if peak_mb >= th.session_hog_mb:
            d.findings.append(Finding(
                DIM_SQL, "MEM_SQL_HOG", Severity.WARN, "SQL 峰值内存",
                human_mb(peak_mb), f">={human_mb(th.session_hog_mb)}",
                f"query_id {qid} 峰值 {human_mb(peak_mb)}，SQL：{trunc(query, 80)}"))

        if spill_mb >= th.spill_warn_mb:
            d.findings.append(Finding(
                DIM_SQL, "MEM_SQL_SPILL", Severity.WARN, "SQL 下盘量",
                human_mb(spill_mb), f">={human_mb(th.spill_warn_mb)}",
                f"query_id {qid} 下盘 {human_mb(spill_mb)}，说明 work_mem 不足以容纳"
                f"排序/哈希，算子被迫落盘（先定位到具体算子再调 work_mem）"))

        if est_mb > 0 and peak_mb / est_mb >= th.estimate_dev_ratio:
            d.findings.append(Finding(
                DIM_SQL, "MEM_SQL_ESTIMATE_OFF", Severity.WARN, "内存估算偏差",
                f"{peak_mb / est_mb:.1f}×", f">={th.estimate_dev_ratio:.0f}×",
                f"query_id {qid} 估算 {human_mb(est_mb)} 实际 {human_mb(peak_mb)}，"
                f"优化器估算严重偏低——通常是统计信息过期或行数估算错误"))

        if warning.strip():
            d.findings.append(Finding(
                DIM_SQL, "MEM_SQL_WARNING", Severity.NOTICE, "GaussDB 自带告警",
                trunc(warning, 60), "非空",
                f"query_id {qid} 的 warning 字段：{trunc(warning, 120)}"))

    if not d.rows:
        d.headline = "视图可用，但无 SQL 级数据"
        d.note = _EMPTY_SQL_NOTE
    else:
        d.headline = f"峰值内存最高 SQL：query_id {d.rows[0][0]}（{d.rows[0][3]}）"
    return d


# --------------------------------------------------------------------------
# L5 — operator level
# --------------------------------------------------------------------------
def collect_operator(db, cat: Catalog, cap: Capability, th: Thresholds, top: int,
                     historical: bool = False) -> DimResult:
    if historical and not cap.history_available:
        return degraded(DIM_OPERATOR, cap.reasons.get("history", "历史数据不可用"))
    if not cap.operator_available:
        return degraded(DIM_OPERATOR, cap.reasons.get("L5", "算子级资源跟踪不可用"))

    vi = cat.get("wlm_operator_hist" if historical else "wlm_operator")
    if not vi.available:
        return degraded(DIM_OPERATOR, vi.reason)

    q = (f"SELECT {probe.columns_expr(vi, _OP_COLS)} FROM {vi.name}"
         f"{_order_by(vi, 'max_peak_memory')} LIMIT {int(top)}")
    try:
        _, rows = db.query(q)
    except common.DBError as exc:
        from util import summarize_err
        return degraded(DIM_OPERATOR, summarize_err(exc))

    d = DimResult(dimension=DIM_OPERATOR, available=True,
                  headers=["query_id", "node_id", "算子", "耗时(ms)", "估算行",
                           "实际行", "峰值内存", "下盘", "倾斜%"])

    for r in rows:
        qid, node_id, node_name = str(r[0] or ""), str(r[1] or ""), str(r[2] or "")
        duration = i64(r[3])
        est_rows, act_rows = f(r[4]), f(r[5])
        peak_mb, spill_mb = f(r[6]), f(r[7])
        skew = f(r[8])
        where = f"query_id {qid} 的算子 #{node_id} {node_name}"

        d.rows.append([qid, node_id, trunc(node_name, 28), str(duration),
                       str(i64(est_rows)), str(i64(act_rows)), human_mb(peak_mb),
                       human_mb(spill_mb), f"{skew:.0f}"])

        if peak_mb >= th.session_hog_mb:
            d.findings.append(Finding(
                DIM_OPERATOR, "MEM_OP_HOG", Severity.WARN, "算子峰值内存",
                human_mb(peak_mb), f">={human_mb(th.session_hog_mb)}",
                f"{where} 峰值 {human_mb(peak_mb)} —— 内存就消耗在这个算子上"))

        if spill_mb >= th.spill_warn_mb:
            d.findings.append(Finding(
                DIM_OPERATOR, "MEM_OP_SPILL", Severity.WARN, "算子下盘量",
                human_mb(spill_mb), f">={human_mb(th.spill_warn_mb)}",
                f"{where} 下盘 {human_mb(spill_mb)}，work_mem 不足以容纳该算子"))

        if est_rows > 0 and act_rows / est_rows >= th.rows_dev_ratio:
            d.findings.append(Finding(
                DIM_OPERATOR, "MEM_OP_ROWS_OFF", Severity.WARN, "行数估算偏差",
                f"{act_rows / est_rows:.1f}×", f">={th.rows_dev_ratio:.0f}×",
                f"{where} 估算 {i64(est_rows)} 行、实际 {i64(act_rows)} 行，"
                f"低估导致内存分配不足（根因通常是统计信息过期）"))

        if skew >= th.skew_warn_pct:
            d.findings.append(Finding(
                DIM_OPERATOR, "MEM_OP_SKEW", Severity.WARN, "内存倾斜",
                f"{skew:.0f}%", f">={th.skew_warn_pct:.0f}%",
                f"{where} 各 DN 内存分配倾斜 {skew:.0f}%，指向数据分布不均"))

    if not d.rows:
        d.headline = "视图可用，但无算子级数据"
        d.note = _EMPTY_OPERATOR_NOTE
    else:
        d.headline = (f"峰值内存最高算子：{d.rows[0][2]}（{d.rows[0][6]}，query_id "
                      f"{d.rows[0][0]}）")
    return d
