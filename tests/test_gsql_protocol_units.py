import sys, pathlib
from decimal import Decimal
_ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT))

import pytest  # noqa: E402
from common.backends.base import DBError  # noqa: E402
from common.backends import gsql_protocol as gp  # noqa: E402

def test_string_param_uses_quoted_var():
    sql, vars_ = gp.rewrite_params("WHERE n = %s", ["public"])
    assert sql == "WHERE n = :'p0'"
    assert vars_ == {"p0": "public"}

def test_numeric_param_uses_raw_var():
    sql, vars_ = gp.rewrite_params("LIMIT %s", [100])
    assert sql == "LIMIT :p0"
    assert vars_ == {"p0": "100"}

def test_decimal_param_preserved_as_text():
    sql, vars_ = gp.rewrite_params("x > %s", [Decimal("1.5")])
    assert sql == "x > :p0"
    assert vars_ == {"p0": "1.5"}

def test_bool_and_none_inlined():
    sql, vars_ = gp.rewrite_params("a=%s AND b=%s", [True, None])
    assert sql == "a=TRUE AND b=NULL"
    assert vars_ == {}

def test_mixed_string_and_numeric():
    sql, vars_ = gp.rewrite_params(
        "p=%s AND (%s='' OR n=%s) LIMIT %s", ["proc", "", "public", 1]
    )
    assert sql == "p=:'p0' AND (:'p1'='' OR n=:'p2') LIMIT :p3"
    assert vars_ == {"p0": "proc", "p1": "", "p2": "public", "p3": "1"}

def test_percent_literal_escaped():
    sql, vars_ = gp.rewrite_params("x LIKE 'a%%b'", [])
    assert sql == "x LIKE 'a%b'"
    assert vars_ == {}

def test_count_mismatch_raises():
    with pytest.raises(DBError):
        gp.rewrite_params("a=%s AND b=%s", ["only-one"])

def test_unsupported_type_raises():
    with pytest.raises(DBError):
        gp.rewrite_params("x=%s", [object()])

def test_is_wrappable_true_for_select():
    assert gp.is_wrappable_select("SELECT 1")
    assert gp.is_wrappable_select("  select * from t")
    assert gp.is_wrappable_select("WITH x AS (SELECT 1) SELECT * FROM x")

def test_is_wrappable_strips_leading_comment():
    assert gp.is_wrappable_select("-- c\nSELECT 1")
    assert gp.is_wrappable_select("/* c */ SELECT 1")

def test_is_wrappable_false_for_non_select():
    assert not gp.is_wrappable_select("SHOW enable_wdr_snapshot")
    assert not gp.is_wrappable_select("EXPLAIN ANALYZE SELECT 1")
    assert not gp.is_wrappable_select("SET statement_timeout = 1000")

def test_wrap_select_json_strips_trailing_semicolon():
    assert (
        gp.wrap_select_json("SELECT a FROM t;")
        == "SELECT json_agg(row_to_json(_t)) FROM (SELECT a FROM t) _t"
    )

def test_parse_json_recovers_types_and_null():
    out = '[{"a": 1, "b": "x", "c": null}]\n'
    cols, rows = gp.parse_json_result(out)
    assert cols == ["a", "b", "c"]
    assert rows == [(1, "x", None)]

def test_parse_json_float_is_decimal():
    cols, rows = gp.parse_json_result('[{"v": 1.5}]')
    assert rows[0][0] == Decimal("1.5")
    assert isinstance(rows[0][0], Decimal)

def test_parse_json_empty_set():
    assert gp.parse_json_result("\n") == ([], [])
    assert gp.parse_json_result("") == ([], [])

def test_parse_text_lines():
    cols, rows = gp.parse_text_result("on\n")
    assert cols == []
    assert rows == [("on",)]
    _, rows2 = gp.parse_text_result("line1\nline2\n")
    assert rows2 == [("line1",), ("line2",)]

def test_parse_text_empty():
    assert gp.parse_text_result("") == ([], [])

def test_parse_text_no_trailing_newline():
    assert gp.parse_text_result("on") == ([], [("on",)])

def test_parse_error_with_sqlstate():
    err = "gsql: ERROR:  42P01: relation \"foo\" does not exist\n"
    assert gp.parse_gsql_error(err) == 'ERROR: relation "foo" does not exist (SQLSTATE 42P01)'

def test_parse_error_without_sqlstate():
    assert gp.parse_gsql_error("gsql: ERROR:  boom\n") == "ERROR: boom"

def test_parse_error_fallback():
    assert gp.parse_gsql_error("could not connect to server") == "could not connect to server"

def test_parse_error_lowercase_token_not_sqlstate():
    assert gp.parse_gsql_error("gsql: ERROR:  error: boom") == "ERROR: error: boom"
