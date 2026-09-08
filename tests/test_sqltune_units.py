"""DB-free unit tests for sqltune's ported pure-logic modules."""
import pathlib
import sys

_ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT / "skills" / "gaussdb-sqltune" / "scripts"))

import placeholder  # noqa: E402
import evidence  # noqa: E402
import render  # noqa: E402
import sqlfetch  # noqa: E402


# --- placeholder substitution ------------------------------------------------

def test_substitute_no_placeholders():
    r = placeholder.substitute("SELECT 1", [])
    assert r.placeholders == 0
    assert r.sql == "SELECT 1"


def test_substitute_limit_offset():
    r = placeholder.substitute("SELECT * FROM t LIMIT ? OFFSET ?", [])
    assert [s.value for s in r.substitutions] == ["100", "0"]
    assert "LIMIT 100 OFFSET 0" in r.sql


def test_substitute_int_vs_text_vs_date():
    r = placeholder.substitute(
        "SELECT * FROM t WHERE user_id = ? AND name = ? AND created_at >= ?", [])
    assert [s.value for s in r.substitutions] == ["1", "'test'", "'2024-01-01'"]


def test_substitute_typed_date_literal():
    # DATE ?/TIMESTAMP ?/TIME ? must become quoted literals (not a bare number,
    # which yields "syntax error near N").
    assert placeholder.substitute(
        "SELECT * FROM t WHERE d >= DATE ?", []).substitutions[0].value == "'2024-01-01'"
    assert placeholder.substitute(
        "SELECT * FROM t WHERE ts > TIMESTAMP ?", []).substitutions[0].value == "'2024-01-01 00:00:00'"
    # A column literally named order_date must NOT trip the keyword rule; the
    # '=' op rule still gives it a valid quoted date value.
    assert placeholder.substitute(
        "SELECT * FROM t WHERE order_date = ?", []).substitutions[0].value == "'2024-01-01'"


def test_substitute_to_char_followup():
    r = placeholder.substitute("SELECT * FROM t WHERE TO_CHAR(d, ?) = ?", [])
    vals = [s.value for s in r.substitutions]
    assert vals == ["'YYYY-MM-DD'", "'2024-01-15'"]
    assert r.substitutions[1].source == "rule-format-followup"


def test_substitute_bind_override():
    r = placeholder.substitute("SELECT * FROM t WHERE id = ?", ["42"])
    assert r.substitutions[0].value == "42"
    assert r.substitutions[0].source == "bind"


def test_substitute_bind_serializes_timestamp_and_text_values():
    r = placeholder.substitute(
        "SELECT * FROM t WHERE created_at >= TIMESTAMP ? AND name = ?",
        ["2024-01-01 00:00:00", "O'Reilly"],
        [None, "varchar"],
    )
    assert [s.value for s in r.substitutions] == [
        "'2024-01-01 00:00:00'", "'O''Reilly'",
    ]
    assert "TIMESTAMP '2024-01-01 00:00:00'" in r.sql


def test_substitute_bind_preserves_explicit_literals_and_numbers():
    r = placeholder.substitute(
        "SELECT * FROM t WHERE created_at >= TIMESTAMP ? AND level = ?",
        ["'2024-01-01 00:00:00'", "2"],
        ["timestamp without time zone", "smallint"],
    )
    assert [s.value for s in r.substitutions] == ["'2024-01-01 00:00:00'", "2"]


def test_bind_into_a_text_column_is_quoted_even_when_it_looks_numeric():
    """列类型说了算,不能因为值长得像数字就放弃加引号。

    账号/机构号/客户号这类"存在 varchar 列里的纯数字"是银行现场的主流形态;
    原样拼进去 openGauss 报 operator does not exist: character varying = bigint。
    """
    r = placeholder.substitute("SELECT 1 FROM t WHERE acct_no = ?",
                               ["6222021234567"], ["varchar"])
    assert r.substitutions[0].value == "'6222021234567'"
    r2 = placeholder.substitute("SELECT 1 FROM t WHERE org_no = ?",
                                ["001"], ["character"])
    assert r2.substitutions[0].value == "'001'"


def test_bind_of_bare_text_is_quoted_when_the_column_type_is_unknown():
    """类型探测失败时也不能把文本裸拼——那会变成标识符。

    `x = ABC` 报的是 column "abc" does not exist,这个报错完全指不到 bind 上,
    比不加引号本身更难排查。
    """
    r = placeholder.substitute("SELECT 1 FROM t WHERE x = ?", ["ABC"], [None])
    assert r.substitutions[0].value == "'ABC'"


def test_numeric_bind_stays_bare_so_limit_offset_keep_working():
    """LIMIT/OFFSET 的参数必须是裸数字,不能被顺手引起来。"""
    r = placeholder.substitute("SELECT 1 FROM t LIMIT ? OFFSET ?",
                               ["1000", "10"], [None, None])
    assert [s.value for s in r.substitutions] == ["1000", "10"]


# --- 报告里的合成值声明 --------------------------------------------------------

def _report_with(binds, types, sql="SELECT 1 FROM t WHERE a = ? AND b = ?"):
    import sqltune
    sub = placeholder.substitute(sql, binds, types)
    ev = evidence.Evidence(sql=sub.sql, version="og", plan="Seq Scan", analyzed=False)
    return sqltune.sqltune_report(sqltune.TuneResult(
        original_sql=sql, substitution=sub, evidence=ev))


def test_report_does_not_call_real_bind_values_synthetic():
    """全部值都来自 --bind 时,报告不能再自称合成值。

    SKILL.md 要求「基于合成值的倍数」必须附 caveat,报告若无条件声明合成,
    模型就会给一份真实值跑出来的结论硬加免责,把结论说弱。
    """
    out = _report_with(["42", "7"], ["integer", "integer"])
    assert "## Placeholder Substitution" in out          # 小节仍可被 SKILL.md 认出
    assert "synthetic" not in out.lower()
    assert "re-run with `--bind`" not in out


def test_report_still_warns_when_any_value_is_synthetic():
    """只要有一个值是猜的,合成值提醒必须还在——降级要说出口。"""
    out = _report_with(["42"], ["integer", "integer"])
    assert "synthetic" in out.lower()
    assert "--bind" in out


def test_substitute_skips_string_literals():
    # The ? inside the literal must NOT be treated as a placeholder.
    r = placeholder.substitute("SELECT '?' , id FROM t WHERE id = ?", [])
    assert r.placeholders == 1


def test_substitute_dollar_and_colon():
    r = placeholder.substitute("SELECT * FROM t WHERE a = $1 AND b = :2", [])
    assert r.placeholders == 2
    assert ":2" not in r.sql and "$1" not in r.sql


def test_comparison_column_handles_repeated_in_placeholders():
    sql = "SELECT * FROM customer c WHERE c.customer_level IN (?, ?, ?, ?)"
    columns = [placeholder.comparison_column(ctx)
               for ctx in placeholder.placeholder_contexts(sql)]
    assert columns == ["customer_level"] * 4


# --- table extraction --------------------------------------------------------

def test_extract_simple():
    assert evidence.extract_tables("SELECT * FROM orders") == ["orders"]


def test_extract_schema_qualified():
    assert evidence.extract_tables("SELECT * FROM public.orders") == ["orders"]


def test_extract_alias_and_comma():
    assert evidence.extract_tables("SELECT * FROM orders o, items i") == ["orders", "items"]


def test_extract_join_chain():
    got = evidence.extract_tables(
        "SELECT * FROM a JOIN b ON a.id=b.id LEFT JOIN c ON b.x=c.x")
    assert got == ["a", "b", "c"]


def test_extract_dedup():
    assert evidence.extract_tables("SELECT * FROM t JOIN t2 ON 1=1 JOIN t ON 1=1") == ["t", "t2"]


# --- is_dml ------------------------------------------------------------------

def test_is_dml():
    assert evidence.is_dml("UPDATE t SET x=1")
    assert evidence.is_dml("  delete from t")
    assert not evidence.is_dml("SELECT * FROM t")
    assert evidence.is_dml("WITH c AS (SELECT 1) INSERT INTO t SELECT * FROM c")
    assert not evidence.is_dml("WITH c AS (SELECT 1) SELECT * FROM c")


# --- render ------------------------------------------------------------------

def test_render_table_escapes_pipes_and_pads():
    out = render.table(["A", "B"], [["x|y"], ["1", "2"]])
    assert "x\\|y" in out
    lines = out.strip().split("\n")
    assert lines[0] == "| A | B |"
    assert lines[2] == "| x\\|y |  |"  # padded to 2 cols


def test_render_code_block_extends_fence():
    out = render.code_block("", "has ``` inside")
    assert out.startswith("````")  # fence longer than the inner run


def test_truncate():
    assert render.truncate("hello", 10) == "hello"
    assert render.truncate("hello", 3) == "he…"
    assert render.truncate("x", 0) == ""


# --- count_placeholders ------------------------------------------------------

def test_count_placeholders():
    assert sqlfetch.count_placeholders("a = ? and b = $1 and c = :name") == 3
    assert sqlfetch.count_placeholders("a::int") == 0  # cast, not placeholder


# --- 运行态计划(gs_get_explain,GaussDB 私有):按 sql_id 找正在执行的会话,有就附一节 ----------

from common import kernel_funcs as kf  # noqa: E402

_GAUSS = "(GaussDB Kernel V500R002C10 build 1) compiled at 2025"
_OG = "(openGauss-lite 5.0.3 build x) compiled at 2024"


class _RtRunner:
    def __init__(self, version, has_explain=True, session=True, plan="Index Scan rt"):
        self.version, self.has_explain, self.session, self.plan = version, has_explain, session, plan

    def run(self, script, values=None):
        if script == kf.PROBE_SCRIPT:
            rows = [{"item": "version", "detail": self.version}]
            if self.has_explain:
                rows.append({"item": "func:gs_get_explain", "detail": "integer"})
            return rows
        if script == kf.ACTIVE_PID_SCRIPT:
            return [{"pid": "42", "query": "select 1", "query_start": ""}] if self.session else []
        if script in (kf.RUNTIME_PLAN_SCRIPT, kf.RUNTIME_PLAN_INT4_SCRIPT):
            return [{"plan": self.plan}]
        raise AssertionError(script)


def _tr(**kw):
    import sqltune
    sub = placeholder.substitute("SELECT 1", [], [])
    ev = evidence.Evidence(sql="SELECT 1", version="og", plan="Seq Scan", analyzed=False)
    return sqltune.TuneResult(original_sql="SELECT 1", substitution=sub, evidence=ev, **kw)


def test_report_shows_runtime_plan_section_when_present():
    import sqltune
    out = sqltune.sqltune_report(_tr(sql_id="1", runtime_plan="Index Scan rt", runtime_plan_pid=42))
    assert "## Runtime Plan" in out and "gs_get_explain" in out and "42" in out
    assert "Index Scan rt" in out and "运行态" in out
    assert out.index("## Runtime Plan") > out.index("## Execution Plan")     # 紧跟估算计划之后


def test_report_shows_runtime_note_when_plan_unavailable():
    import sqltune
    out = sqltune.sqltune_report(_tr(sql_id="1", runtime_note="该 SQL 当前没有正在执行的会话"))
    assert "## Runtime Plan" in out and "没有正在执行" in out


def test_report_is_silent_without_runtime_info():
    import sqltune
    assert "Runtime Plan" not in sqltune.sqltune_report(_tr(sql_id="1"))


def test_runtime_plan_for_finds_active_session_and_fetches_plan():
    import sqltune
    assert sqltune._runtime_plan_for(_RtRunner(_GAUSS), "825712617") == ("Index Scan rt", 42, "")


def test_runtime_plan_for_notes_when_no_active_session():
    import sqltune
    plan, pid, note = sqltune._runtime_plan_for(_RtRunner(_GAUSS, session=False), "1")
    assert plan == "" and pid == 0 and "正在执行" in note


def test_runtime_plan_for_on_opengauss_is_silent():
    import sqltune
    assert sqltune._runtime_plan_for(_RtRunner(_OG, has_explain=False), "1") == ("", 0, "")


def test_runtime_plan_for_on_gaussdb_missing_function_explains():
    import sqltune
    plan, pid, note = sqltune._runtime_plan_for(_RtRunner(_GAUSS, has_explain=False), "1")
    assert plan == "" and "GaussDB" in note and "pg_proc" in note


def test_runtime_plan_for_empty_plan_reports_preconditions():
    import sqltune
    plan, pid, note = sqltune._runtime_plan_for(_RtRunner(_GAUSS, plan=""), "1")
    assert plan == "" and "plan_collect_thresh" in note


if __name__ == "__main__":
    import traceback
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    failed = 0
    for fn in fns:
        try:
            fn()
            print(f"  ok  {fn.__name__}")
        except Exception:
            failed += 1
            print(f"FAIL  {fn.__name__}")
            traceback.print_exc()
    print(f"\n{len(fns) - failed}/{len(fns)} passed")
    sys.exit(1 if failed else 0)


def test_evidence_says_when_analyze_was_requested_but_the_plan_is_an_estimate():
    """现场 sqltune.plan_text_analyze 按客户只读要求把 ANALYZE 固定关闭:要了 --analyze,
    模板回来的仍是估算计划。analyzed 必须按计划文本判(false),报告里要说明,不能写 Analyzed: true。"""
    class _R:
        def run(self, script, values=None):
            if script == evidence.VERSION_SCRIPT:
                return [{"version": "openGauss 5.0.3"}]
            if script.endswith("plan_text_analyze"):
                return [{"QUERY PLAN": "Seq Scan on t  (cost=0.00..1.00 rows=1 width=4)"}]
            return []

    ev = evidence.collect(_R(), None, "SELECT 1", True)
    assert ev.analyzed is False and ev.analyze_requested is True
    rep = evidence.evidence_report(ev)
    assert "Analyzed: false" in rep and "估算" in rep and "ANALYZE" in rep and "只读" in rep


def test_evidence_that_really_analyzed_is_reported_as_such():
    class _R:
        def run(self, script, values=None):
            if script == evidence.VERSION_SCRIPT:
                return [{"version": "openGauss 5.0.3"}]
            if script.endswith("plan_text_analyze"):
                return [{"QUERY PLAN": "Seq Scan on t  (cost=0.00..1.00 rows=1 width=4) "
                                       "(actual time=0.01..0.02 rows=1 loops=1)"}]
            return []

    ev = evidence.collect(_R(), None, "SELECT 1", True)
    assert ev.analyzed is True
    assert "估算" not in evidence.evidence_report(ev)
