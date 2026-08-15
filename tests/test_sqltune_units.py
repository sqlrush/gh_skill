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
