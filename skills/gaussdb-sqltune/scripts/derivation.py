"""推演报告：把「凭什么信这些数」摊开给人看。

报告的顺序就是可信度的依赖顺序，任何一节不成立，后面的都不该出现：

    0  统计信息新鲜度   不新鲜 → 输入本身不可信，整段作废
    1  代价常数         本实例实际值，让人看出哪些被调过
    2  复算基线（校准） 用公式重算 EXPLAIN 已给出的计划，逐节点比对
    3  结论             只有 0 和 2 都通过才允许出现

**这份报告存在的意义不是把话说长，是让每个数字可被独立复核。** 所以每个
算子都摊开成逐项算式（代入了实际数值），每一项都能拿计算器验。

三句话必须出现在报告里，不能因为「显得不够自信」而删：
  · cost 是规划器的内部单位，不是时间
  · 未建模的算子没有参与校准，覆盖率就是「验过多少」
  · 用了估算值的节点单独标出（目前只有 btree 树高）
"""
from __future__ import annotations

from typing import List, Optional, Sequence

import calibrate as _cal
import render

# 报告里反复出现的免责话术。集中在这里，避免各处措辞不一致 ——
# 措辞一旦软化（「大约」「预计提升」），读的人对确定性的判断就被误导了。
COST_IS_NOT_TIME = (
    "**cost 是规划器的内部单位，不是时间。** 代价降低 N 倍不等于快 N 倍："
    "它衡量的是规划器认为要做多少工作，不含实际 IO 等待、锁、并发干扰。")


def render_report(calibration, constants, freshness: Sequence,
                  sql: str = "", plan_text: str = "") -> str:
    out = ["# 代价推演\n"]
    if sql:
        out.append("## 被分析的 SQL\n\n" + render.code_block("sql", sql))

    out.append(_section_freshness(freshness))
    out.append(_section_constants(constants))
    out.append(_section_calibration(calibration))
    out.append(_section_verdict(calibration, freshness))
    return "\n".join(out)


def may_emit_advice(calibration, freshness: Sequence):
    """能不能出代价结论。返回 (可以吗, 理由)。

    两道门都要过。**顺序不能反**：统计不新鲜时校准可能照样通过（公式没错，
    错的是喂给公式的数），那种「通过」是最危险的一种，因为它看起来最像
    验证过了。
    """
    stale = [f for f in freshness if not f.fresh]
    if stale:
        return False, ("统计信息不新鲜：%s。推演的每一个输入都来自上次 ANALYZE "
                       "的快照，快照过期时公式再对也算不出对的数。"
                       % "；".join("%s（%s）" % (f.table, f.reason) for f in stale))
    if not calibration.passed:
        return False, "复算未通过校准：%s" % calibration.reason
    return True, ("统计新鲜且复算与实测吻合（%s），可以出代价结论。"
                  % calibration.summary())


# --- 各节 --------------------------------------------------------------------

def _section_freshness(freshness: Sequence) -> str:
    out = ["## 0. 统计信息新鲜度\n",
           "推演的全部输入（页数、行数、n_distinct、correlation）都是上次 "
           "ANALYZE 时冻结的快照。表在那之后变化太大，这些数不会报错，"
           "只会让推演算出一个**精确的错数**。所以先判新鲜度。\n"]
    if not freshness:
        out.append("> 没有拿到任何表的新鲜度数据 —— 无法判断，按不通过处理。\n")
        return "\n".join(out)

    rows = []
    for f in freshness:
        drift = "n/a" if f.drift is None else "%.1f%%" % (f.drift * 100.0)
        rows.append([f.table, "%.0f" % f.reltuples, "%.0f" % f.live_tuples,
                     drift, f.last_analyze,
                     "通过" if f.fresh else "**不通过**"])
    out.append(render.table(
        ["表", "冻结行数", "近实时行数", "偏离", "上次 ANALYZE", "判定"], rows))

    for f in freshness:
        if not f.fresh:
            out.append("- **%s**：%s" % (f.table, f.reason))
    return "\n".join(out) + "\n"


def _section_constants(constants) -> str:
    out = ["## 1. 代价常数（本实例实际值）\n",
           "全部读自 pg_settings，不使用任何默认值。调过参的实例上，"
           "拿出厂默认值去算会让复算与实测对不上，而对不上正是判定"
           "「模型不可信」的依据 —— 用默认值顶替等于让闸门失灵。\n"]
    if constants is None:
        out.append("> 未取到代价常数。\n")
        return "\n".join(out)
    out.append(render.table(["参数", "取值"],
                            [[k, v] for k, v in constants.describe()]))
    return "\n".join(out) + "\n"


def _section_calibration(calibration) -> str:
    out = ["## 2. 复算基线（校准闸）\n",
           "用公式重算 EXPLAIN **已经给出**的那个计划，与数据库自己报的数字"
           "逐节点比对。对上了，才有资格拿同一套公式去算别的路径。\n",
           "这一步是整份报告唯一的可信度来源：下面每个数字都不是引用来的，"
           "是重新算出来又与已知答案对上的。\n"]

    rows = []
    for c in calibration.checks:
        computed = "—" if c.computed_total is None else "%.2f" % c.computed_total
        dev = "—" if c.deviation is None else "%.4f%%" % (c.deviation * 100.0)
        status = {"matched": "吻合", "mismatched": "**超差**",
                  "unmodeled": "未建模"}.get(c.status, c.status)
        if c.approximate:
            status += "（含估算）"
        rows.append([render.truncate(c.node_type, 40), c.relation or "—",
                     computed, "%.2f" % c.measured_total, dev, status])
    out.append(render.table(
        ["算子", "对象", "复算", "实测", "相对偏差", "判定"], rows))
    out.append("\n%s\n" % calibration.summary())
    out.append("采用的内核版本变体：min_io_split_seq=%s、btree_page_cpu_cost=%s"
               "（四种变体逐个试出来的，不是假设的）\n"
               % (calibration.variant.min_io_split_seq,
                  calibration.variant.btree_page_cpu_cost))

    unmodeled = [c for c in calibration.checks if c.status == _cal.UNMODELED]
    if unmodeled:
        out.append("\n### 没有参与校准的算子\n")
        out.append("覆盖率 %.0f%% 的意思就是「验过这么多」，剩下的**没验**。"
                   "把没验的当验过了，是这类报告最容易出的错。\n"
                   % (calibration.coverage * 100.0))
        out.append(render.table(
            ["算子", "实测代价", "原因"],
            [[render.truncate(c.node_type, 40), "%.2f" % c.measured_total,
              c.reason] for c in unmodeled]))

    # 只在真有话说的时候才起这一节 —— 空标题会让人以为「没有估算成分」，
    # 而实际上是「有估算但没说明」，两者相差很远。
    approx_notes = [(c, note) for c in calibration.checks if c.approximate
                    for note in (getattr(c.estimate, "notes", []) or [])]
    if approx_notes:
        out.append("\n### 含估算成分的节点\n")
        out.append("这些节点的某一项不是精确算出来的。它们**照样参加校准**，"
                   "标出来只是为了对不上时先查这里。\n")
        for check, note in approx_notes:
            out.append("- **%s**：%s" % (check.node_type, note))
        out.append("")

    out.append("\n### 逐项算式\n")
    out.append("每一项都代入了实际数值，可以拿计算器逐条复核。\n")
    for c in calibration.checks:
        if c.estimate is None:
            continue
        terms = getattr(c.estimate, "terms", []) or []
        if not terms:
            continue
        body = []
        width = max(len(t.label) for t in terms)
        for t in terms:
            body.append("%-*s  %-34s = %16.4f" % (width, t.label, t.formula,
                                                  t.value))
        body.append("%s" % ("─" * (width + 56)))
        body.append("%-*s  %-34s = %16.4f" % (width, "合计", "", c.computed_total))
        body.append("%-*s  %-34s = %16.4f" % (width, "实测", "", c.measured_total))
        out.append("**%s%s**\n" % (c.node_type,
                                   "（%s）" % c.relation if c.relation else ""))
        out.append(render.code_block("", "\n".join(body)))
    return "\n".join(out)


def _section_verdict(calibration, freshness: Sequence) -> str:
    ok, reason = may_emit_advice(calibration, freshness)
    out = ["## 3. 结论\n"]
    if not ok:
        out.append("**不出代价结论。**\n")
        out.append("%s\n" % reason)
        out.append("> 这里给不出数字，不是功能没做，是证据不足以支撑数字。"
                   "给一个没有背书的数字，比不给更危险 —— 它会被当成验证过的。\n")
        return "\n".join(out)
    out.append("%s\n" % reason)
    out.append("%s\n" % COST_IS_NOT_TIME)
    return "\n".join(out)
