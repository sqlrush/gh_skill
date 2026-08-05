"""推演报告与出数闸的单测。

报告本身只是渲染，真正要钉死的是 may_emit_advice：**统计不新鲜时校准可能
照样通过**（公式没错，错的是喂给公式的数），那种「通过」是最危险的一种，
因为它看起来最像验证过了。两道门的顺序不能反。
"""
import pathlib
import sys
import types

import pytest

_ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT / "skills" / "gaussdb-sqltune" / "scripts"))

import calibrate  # noqa: E402
import costconst  # noqa: E402
import costmodel  # noqa: E402
import derivation  # noqa: E402
import plantree  # noqa: E402

COST = costconst.CostConstants(
    seq_page_cost=1.0, random_page_cost=1.1, cpu_tuple_cost=0.01,
    cpu_index_tuple_cost=0.005, cpu_operator_cost=0.0025, block_size=8192,
    effective_cache_size=1572864, work_mem=16 * 1024 * 1024, query_dop=2)

_PLAN = [{"Plan": {
    "Node Type": "Hash Join", "Startup Cost": 0.0, "Total Cost": 200.0,
    "Plan Rows": 10.0, "Plan Width": 8,
    "Plans": [
        {"Node Type": "Seq Scan", "Relation Name": "big", "Alias": "b",
         "Startup Cost": 0.0, "Total Cost": 100.0,
         "Plan Rows": 10.0, "Plan Width": 8},
        {"Node Type": "Sort", "Startup Cost": 0.0, "Total Cost": 50.0,
         "Plan Rows": 10.0, "Plan Width": 8},
    ]}}]
ROOT = plantree.parse(_PLAN)


def _fresh(table="big", ok=True):
    return types.SimpleNamespace(
        table=table, fresh=ok,
        reason="冻结值 100 行与近实时 100 行相差 0.0%" if ok
               else "冻结值 100 行与近实时 900 行相差 800.0%，超过阈值 10%",
        reltuples=100.0, live_tuples=100.0 if ok else 900.0,
        drift=0.0 if ok else 8.0, last_analyze="2026-08-01 03:12:44",
        last_autoanalyze="never")


def _calibration(passed=True, with_unmodeled=True):
    def resolve(node, children):
        if node.node_type == "Sort" and with_unmodeled:
            return None
        total = node.total_cost if passed else node.total_cost * 1.5
        return costmodel.Estimate(
            node_type=node.node_type, startup_cost=0.0, total_cost=total,
            terms=[costmodel.Term("测试项", "1 × %g" % total, total)])
    return calibrate.calibrate(ROOT, resolve)


# --- 出数闸 ------------------------------------------------------------------

def test_stale_stats_block_advice_even_when_calibration_passes():
    """**最危险的一种「通过」。** 公式没错，错的是喂给公式的数。"""
    cal = _calibration(passed=True)
    assert cal.passed is True
    ok, reason = derivation.may_emit_advice(cal, [_fresh(ok=False)])
    assert ok is False
    assert "统计信息不新鲜" in reason
    assert "800.0%" in reason      # 拒绝理由要带上可复核的数字


def test_failed_calibration_blocks_advice():
    ok, reason = derivation.may_emit_advice(_calibration(passed=False),
                                            [_fresh()])
    assert ok is False
    assert "复算未通过校准" in reason


def test_both_gates_pass_allows_advice():
    ok, reason = derivation.may_emit_advice(_calibration(), [_fresh()])
    assert ok is True
    assert "可以出代价结论" in reason


def test_no_freshness_data_is_not_a_pass():
    """拿不到新鲜度数据 = 不确定 = 按失败处理。"""
    cal = _calibration()
    report = derivation.render_report(cal, COST, [])
    assert "无法判断，按不通过处理" in report


# --- 报告内容 ----------------------------------------------------------------

def test_report_states_cost_is_not_time_when_advice_allowed():
    """这句话不能因为「显得不够自信」而删 —— 它防的是「代价降 600 倍」
    被读成「快 600 倍」。"""
    report = derivation.render_report(_calibration(), COST, [_fresh()])
    assert "cost 是规划器的内部单位，不是时间" in report


def test_blocked_report_says_why_and_refuses_numbers():
    report = derivation.render_report(_calibration(passed=False), COST,
                                      [_fresh()])
    assert "**不出代价结论。**" in report
    assert "给一个没有背书的数字，比不给更危险" in report


def test_report_lists_unmodeled_operators_and_coverage():
    """覆盖率就是「验过多少」，剩下的没验 —— 必须点名。"""
    report = derivation.render_report(_calibration(), COST, [_fresh()])
    assert "没有参与校准的算子" in report
    assert "Sort" in report
    assert "67%" in report or "66%" in report


def test_report_shows_per_term_arithmetic():
    """每一项要代入实际数值，读的人能拿计算器复核。"""
    report = derivation.render_report(_calibration(), COST, [_fresh()])
    assert "逐项算式" in report
    assert "测试项" in report
    assert "实测" in report


def test_report_shows_which_variant_was_used():
    """「用了哪一版公式」是测出来的，报告里要写清楚是哪一版。"""
    report = derivation.render_report(_calibration(), COST, [_fresh()])
    assert "min_io_split_seq" in report
    assert "不是假设的" in report


def test_report_lists_actual_cost_constants():
    """random_page_cost 在 og5 上是 1.1 不是 4.0 —— 报告要让人看得见。"""
    report = derivation.render_report(_calibration(), COST, [_fresh()])
    assert "random_page_cost" in report and "1.1" in report
    assert "不使用任何默认值" in report


def test_freshness_table_shows_both_numbers():
    report = derivation.render_report(_calibration(), COST, [_fresh(ok=False)])
    assert "冻结行数" in report and "近实时行数" in report
    assert "**不通过**" in report


def test_report_includes_sql_when_given():
    report = derivation.render_report(_calibration(), COST, [_fresh()],
                                      sql="SELECT 1")
    assert "SELECT 1" in report
