"""DB-free unit tests for proctune's procanalyze port."""
import pathlib
import sys

_ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT / "skills" / "gaussdb-proctune" / "scripts"))

import procanalyze as pa  # noqa: E402


# --- cursor extraction -------------------------------------------------------

def test_declare_is_eligible():
    body = "DECLARE\n  CURSOR c IS SELECT a FROM t WHERE x = v;\nBEGIN\n  NULL;\nEND;"
    curs = pa.extract_cursors(body)
    c = [x for x in curs if x.name == "c"][0]
    assert c.kind == pa.CURSOR_DECLARE_IS
    assert c.eligible
    assert c.select_sql == "SELECT a FROM t WHERE x = v"


def test_for_update_skipped():
    body = "DECLARE CURSOR c IS SELECT a FROM t FOR UPDATE; BEGIN NULL; END;"
    c = pa.extract_cursors(body)[0]
    assert not c.eligible and "FOR UPDATE" in c.skip_reason


def test_parameterized_skipped():
    body = "DECLARE CURSOR c(p int) IS SELECT a FROM t WHERE x = p; BEGIN NULL; END;"
    c = pa.extract_cursors(body)[0]
    assert not c.eligible and "参数化" in c.skip_reason


def test_open_for_execute_skipped():
    body = "BEGIN OPEN c FOR EXECUTE 'select 1'; END;"
    c = [x for x in pa.extract_cursors(body) if x.name == "c"][0]
    assert not c.eligible and "动态游标" in c.skip_reason


def test_for_loop_select():
    body = "BEGIN FOR rec IN SELECT id FROM t WHERE v = 1 LOOP NULL; END LOOP; END;"
    c = [x for x in pa.extract_cursors(body) if x.kind == pa.CURSOR_FOR_LOOP][0]
    assert c.eligible and c.select_sql == "SELECT id FROM t WHERE v = 1"


def test_for_loop_numeric_range_ignored():
    body = "BEGIN FOR i IN 1..10 LOOP NULL; END LOOP; END;"
    assert not [x for x in pa.extract_cursors(body) if x.kind == pa.CURSOR_FOR_LOOP]


# --- structural findings -----------------------------------------------------

def test_scan_dynamic_sql():
    body = "BEGIN EXECUTE 'select 1'; END;"
    kinds = {f.kind for f in pa.scan_structure(body)}
    assert "dynamic_sql" in kinds


def test_scan_per_row_dml_in_loop():
    body = "BEGIN LOOP INSERT INTO t VALUES (1); END LOOP; END;"
    kinds = {f.kind for f in pa.scan_structure(body)}
    assert "per_row_dml" in kinds


def test_scan_dml_outside_loop_not_flagged():
    body = "BEGIN INSERT INTO t VALUES (1); END;"
    kinds = {f.kind for f in pa.scan_structure(body)}
    assert "per_row_dml" not in kinds


def test_for_update_not_treated_as_dml():
    body = "BEGIN LOOP SELECT 1 FROM t FOR UPDATE; END LOOP; END;"
    kinds = {f.kind for f in pa.scan_structure(body)}
    assert "per_row_dml" not in kinds  # "FOR UPDATE" must not match the update DML rule


# --- args + vars + substitution ----------------------------------------------

def test_parse_args():
    args = pa.parse_args("p_id integer, p_status character varying DEFAULT 'x'::text")
    assert args == [pa.Arg("p_id", "integer"), pa.Arg("p_status", "character varying")]


def test_extract_vars_from_declare():
    body = "DECLARE\n  v_total numeric := 0;\n  CURSOR c IS SELECT 1;\nBEGIN\n NULL; END;"
    vars_ = pa.extract_vars(body, [pa.Arg("p_id", "integer")])
    assert vars_["p_id"] == "integer"
    assert vars_["v_total"] == "numeric"
    assert "c" not in vars_  # cursor decl skipped


def test_substitute_vars_by_type():
    vars_ = {"p_id": "integer", "p_name": "varchar", "p_day": "date"}
    r = pa.substitute_vars(
        "SELECT * FROM t WHERE id = p_id AND name = p_name AND d = p_day", vars_, {})
    assert "id = 1" in r.sql
    assert "name = 'test'" in r.sql
    assert "d = '2024-01-15'" in r.sql
    assert {s.var for s in r.subs} == {"p_id", "p_name", "p_day"}


def test_substitute_vars_bind_override():
    r = pa.substitute_vars("SELECT * FROM t WHERE id = p_id", {"p_id": "integer"}, {"p_id": "42"})
    assert "id = 42" in r.sql
    assert r.subs[0].source == "bind"


def test_substitute_vars_qualified_untouched():
    # o.p_id is a column reference, not the variable p_id.
    r = pa.substitute_vars("SELECT o.p_id FROM t o", {"p_id": "integer"}, {})
    assert "o.p_id" in r.sql
    assert not r.subs


def test_substitute_vars_string_literal_untouched():
    r = pa.substitute_vars("SELECT 'p_id' FROM t", {"p_id": "integer"}, {})
    assert "'p_id'" in r.sql
    assert not r.subs


def test_rollback_safe():
    assert pa.detect_rollback_safe("BEGIN SELECT 1; END;")
    assert not pa.detect_rollback_safe("BEGIN COMMIT; END;")


def test_record_var_names_and_reference():
    vars_ = {"rec_0": "record", "p_cust": "integer", "r_row": "orders%rowtype"}
    assert set(pa.record_var_names(vars_)) == {"rec_0", "r_row"}
    rv = ["rec_0", "r_row"]
    # Nested cursor referencing an outer record var -> detected.
    assert pa.references_record_var(
        "SELECT a FROM t WHERE order_id = rec_0.k", rv) == "rec_0"
    # A column whose name merely contains a record-var name -> NOT a match.
    assert pa.references_record_var(
        "SELECT a FROM t WHERE my_rec_0.x = 1", rv) == ""
    # No record reference -> "".
    assert pa.references_record_var(
        "SELECT a FROM t WHERE customer_id = p_cust", rv) == ""


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
