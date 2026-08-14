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
