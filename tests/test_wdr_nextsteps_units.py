"""WDR 的三件「让结论能接着往下走」的事(纯函数,不连库)。

都来自 2026-10-17 压测后对模型输出的复核:
  · 表格里显示的是 SQL 开头的**注释**,看着像注释、认不出是哪条语句,用户以为报告里没有慢 SQL;
  · 锁受害者(CPU≈0)和真在干活的语句(CPU 占主导)混在一张榜上,而两者的下一步方向相反;
  · SKILL.md 早就要求「带 sql_id 的先 sqltune 实证」,可模型那一轮 8 条建议一条都没实证 ——
    规则写在提示词里只是「希望它做」,落成报告里的一个段落才看得见有没有做。
"""
from __future__ import annotations

import pathlib
import sys

import pytest

_ROOT = pathlib.Path(__file__).resolve().parents[1]
_SCRIPTS = _ROOT / "skills" / "gaussdb-wdr" / "scripts"

for _m in ("model", "thresholds", "util", "collectors", "report", "native", "snaps",
           "interp", "recheck", "finalreport", "ansi"):
    sys.modules.pop(_m, None)
sys.path.insert(0, str(_SCRIPTS))
sys.path.insert(0, str(_ROOT))

import collectors as C                                   # noqa: E402
import finalreport as FR                                 # noqa: E402
import model                                             # noqa: E402
import thresholds                                        # noqa: E402
from model import DimResult, Finding, Options, Severity   # noqa: E402


# ---------------------------------------------------------------- 剥开头注释

@pytest.mark.parametrize("raw,starts", [
    ("-- S7 高频点查。单次零点几秒。\n--\n-- 成因:没索引\nSELECT count(*) FROM fl.orders", "SELECT count(*)"),
    ("/* app=order-svc */ SELECT 1", "SELECT 1"),
    ("/* a */\n-- b\nUPDATE t SET x = 1", "UPDATE t"),
    ("\n\n   -- 一行注释\n  DELETE FROM t", "DELETE FROM t"),
    ("SELECT 1", "SELECT 1"),
])
def test_leading_comments_are_stripped_for_display(raw, starts):
    assert C._strip_lead_comments(raw).startswith(starts)


def test_comments_in_the_middle_are_kept():
    """中间的注释可能是 hint 或业务标记,不能动。"""
    out = C._strip_lead_comments("SELECT /* +index(t idx) */ 1 FROM t")
    assert "/* +index(t idx) */" in out


def test_a_statement_that_is_all_comment_still_shows_something():
    """整段都是注释时返回原文,免得表格里显示成空白 —— 空白让人以为取数失败。"""
    assert C._strip_lead_comments("-- 只有注释,没有语句").strip() != ""


def test_stripping_happens_line_wise_not_after_collapsing_whitespace():
    """**第一版的 bug:** 先把换行压成空格再剥,行注释的边界就没了 ——
    只剥掉第一个 `--` 和紧跟的一个词,后面几行注释照样留在显示里。"""
    raw = "-- 第一行注释\n-- 第二行注释\n-- 第三行注释\nSELECT 42"
    assert C._strip_lead_comments(raw) == "SELECT 42"


def test_full_text_is_archived_unstripped():
    """存档里的全文一个字都不剥 —— 要靠它去对现场的 SQL。"""
    raw = "/* app=svc */ SELECT 1"
    assert C._clip_sql(raw).startswith("/* app=svc */")


# ---------------------------------------------------------------- 两类语句分开

class Runner:
    def __init__(self, rows):
        self.rows = rows

    def run(self, name, params=None):
        return self.rows


def _row(sid, elapsed_s, cpu_s, spill_kb=0, calls=1, query="SELECT 1"):
    return {"sid": sid, "query": query, "calls": str(calls),
            "elapsed_us": str(int(elapsed_s * 1e6)), "cpu_us": str(int(cpu_s * 1e6)),
            "spill_kb": str(spill_kb), "phys_blocks": "0"}


def _opt(**kw):
    o = Options(begin=1, end=2, top=10, thresholds=thresholds.default_thresholds())
    for k, v in kw.items():
        setattr(o, k, v)
    return o


def test_a_cpu_bound_statement_is_flagged_as_doing_real_work():
    d = C.collect_topsql(Runner([_row("plan1", 1142.0, 698.0)]), _opt(db_time_us=23_000_000_000))
    codes = [f.code for f in d.findings]
    assert "WDR_TOPSQL_CPU_HEAVY" in codes
    f = next(f for f in d.findings if f.code == "WDR_TOPSQL_CPU_HEAVY")
    assert f.sql_id == "plan1"
    assert "不是被锁住" in f.evidence


def test_a_lock_victim_is_not_flagged_as_doing_real_work():
    """CPU≈0 是被锁住,**不能**往执行计划的方向指 —— 方向错了用户会白查半天。"""
    d = C.collect_topsql(Runner([_row("lock1", 5963.0, 0.03)]), _opt(db_time_us=23_000_000_000))
    codes = [f.code for f in d.findings]
    assert "WDR_TOPSQL_CPU_HEAVY" not in codes
    assert "WDR_TOPSQL_LOW_CPU" in codes


def test_cpu_bound_statements_are_found_even_when_they_are_not_at_the_top():
    """**实测的真实形态:** 按耗时排序时锁受害者霸占前几名(它们等得最久),
    真在干活的排在 6~8 位。只看前 3 名就一条都报不出来。"""
    rows = [_row("lock%d" % i, 5000.0 - i, 0.0) for i in range(5)]
    rows.append(_row("plan1", 1142.0, 698.0))
    d = C.collect_topsql(Runner(rows), _opt(db_time_us=23_000_000_000))
    heavy = [f.sql_id for f in d.findings if f.code == "WDR_TOPSQL_CPU_HEAVY"]
    assert heavy == ["plan1"]


def test_at_most_two_cpu_bound_findings():
    """一张榜刷出十条发现会把重点冲淡。"""
    rows = [_row("p%d" % i, 1000.0, 700.0) for i in range(6)]
    d = C.collect_topsql(Runner(rows), _opt(db_time_us=23_000_000_000))
    heavy = [f for f in d.findings if f.code == "WDR_TOPSQL_CPU_HEAVY"]
    assert len(heavy) == C.CPU_HEAVY_MAX == 2


def test_a_short_cpu_bound_statement_is_not_flagged():
    """几百毫秒的小语句 CPU 占比高是正常的。"""
    d = C.collect_topsql(Runner([_row("tiny", 0.3, 0.25)]), _opt(db_time_us=23_000_000_000))
    assert "WDR_TOPSQL_CPU_HEAVY" not in [f.code for f in d.findings]


# ---------------------------------------------------------------- 下一步

def _model_with(findings, top_rows=(), headers=()):
    m = FR._ReportModel()
    m.ev_findings = list(findings)
    m.top_sql_rows = list(top_rows)
    m.top_sql_headers = list(headers)
    return m


def _f(code, sql_id=""):
    return Finding("Top SQL", code, Severity.NOTICE, "m", "v", "t", "e", sql_id=sql_id)


def test_next_steps_fills_the_sql_id_into_the_command():
    out = FR._emit_next_steps(_model_with([_f("WDR_TOPSQL_CPU_HEAVY", "4198238080")]))
    assert "--sql-id 4198238080" in out
    assert "{sql_id}" not in out and "<sql_id>" not in out


def test_next_steps_points_lock_victims_away_from_plan_tuning():
    out = FR._emit_next_steps(_model_with([_f("WDR_TOPSQL_LOW_CPU", "x")]))
    assert "lockwait" in out
    assert "不要" in out and "执行计划" in out


def test_next_steps_resolves_the_spill_culprit_from_the_top_sql_table():
    """溢出那条发现来自库级计数器,本身不知道是哪条语句落的盘;
    从 Top SQL 的 spill_MB 列里取最大的那条,才能把 sql_id 填进命令。"""
    headers = ["sql_id", "calls", "elapsed_s", "cpu_s", "spill_MB", "物理读(块)", "占DB time%", "query"]
    rows = [["a", "1", "10", "1", "12.00", "0", "1", "q"],
            ["b", "1", "10", "1", "4483.68", "0", "1", "q"],
            ["c", "1", "10", "1", "0.00", "0", "1", "q"]]
    out = FR._emit_next_steps(_model_with([_f("WDR_TEMP_SPILL")], rows, headers))
    assert "sqltune.py b" in out


def test_next_steps_drops_a_command_it_cannot_fill():
    """填不出 sql_id 就不给这条命令 —— 甩一个占位符等于让用户自己翻表。"""
    out = FR._emit_next_steps(_model_with([_f("WDR_TEMP_SPILL")]))
    assert "<sql_id>" not in out and "{sql_id}" not in out


def test_next_steps_always_mentions_what_wdr_does_not_cover():
    """膨胀/回收 WDR 根本不覆盖;一直没跑完的 SQL 也不在 dbe_perf 的统计里。
    **每次都要提** —— 不提等于让用户以为已经查全了。"""
    out = FR._emit_next_steps(_model_with([]))
    assert "vacuum" in out and "膨胀" in out
    assert "已完成" in out and "slowsql" in out


def test_next_steps_reads_deterministic_findings_not_the_model_interpretation():
    """「下一步」是确定性输出:模型漏解读某一条,不该让那条的命令也消失。

    另外 recheck 把模型的解读按 **code** 对齐证据,同一个 code 有两条发现时
    (两条语句都 CPU 占主导)只会锚定一条 —— 读锚定结果的话第二条语句就没有命令了。
    """
    m = _model_with([_f("WDR_TOPSQL_CPU_HEAVY", "s1"), _f("WDR_TOPSQL_CPU_HEAVY", "s2")])
    m.findings = []          # 模型一条都没解读
    out = FR._emit_next_steps(m)
    assert "--sql-id s1" in out and "--sql-id s2" in out


def test_next_steps_does_not_repeat_the_same_command():
    out = FR._emit_next_steps(_model_with([_f("WDR_DEADLOCK"), _f("WDR_TOPSQL_LOW_CPU", "x")]))
    assert out.count("lockwait/scripts/lockwait.py") == 1
