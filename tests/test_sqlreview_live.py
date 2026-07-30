"""Live tests for sqlreview's catalog collection (auto-skip without a DB).

Unit tests mock the DB, so they can never catch dialect SQL errors. This file
exists because they didn't: the index query used `WITH ORDINALITY` (PostgreSQL
9.4+), which openGauss — based on 9.2 — rejects outright. The collector degraded
exactly as designed and reported the reason, but the index layer went blind and
every index rule silently stopped firing. Only a real server catches that.

Run with:  pytest -m live
"""
import importlib.util
import os
import pathlib
import sys

import pytest

_ROOT = pathlib.Path(__file__).resolve().parents[1]
_SCRIPTS = _ROOT / "skills" / "gaussdb-sqlreview" / "scripts"

CONN = os.environ.get("SQLREVIEW_LIVE_CONN", "og")


# Several skills ship a module named `model` / `render`. Whichever test file ran
# first leaves its own copy in sys.modules, and sqlreview's `from model import ...`
# would then resolve against another skill's model. Drop them before loading ours
# (same guard as tests/test_health_units.py).
_SIBLINGS = ("model", "lexer", "rules", "checks", "objects", "report", "render")


def _load(mod: str):
    for name in _SIBLINGS:
        sys.modules.pop(name, None)
    path = _SCRIPTS / f"{mod}.py"
    sys.path.insert(0, str(path.parent))
    sys.path.insert(0, str(_ROOT))
    spec = importlib.util.spec_from_file_location(mod, path)
    m = importlib.util.module_from_spec(spec)
    sys.modules[mod] = m
    spec.loader.exec_module(m)
    return m


@pytest.fixture(scope="module")
def db():
    sys.path.insert(0, str(_ROOT))
    import common
    try:
        conn = common.Database.connect(CONN)
    except Exception as exc:                      # no config / no server / no creds
        pytest.skip(f"no live connection {CONN!r}: {exc}")
    yield conn
    conn.close()


@pytest.mark.live
def test_catalog_queries_parse_on_a_real_server(db):
    """Every object query must actually run — a degraded layer is a blind layer."""
    objects = _load("objects")
    facts = objects.collect_facts(db, "pg_catalog")   # always exists, always populated
    assert facts.notes == (), f"catalog collection degraded: {facts.notes}"
    assert facts.tables, "no tables collected from pg_catalog"
    assert facts.indexes, "no indexes collected — the index query silently went blind"


@pytest.mark.live
def test_index_columns_are_resolved_in_order(db):
    """indkey -> column names must survive the openGauss dialect, in order."""
    objects = _load("objects")
    facts = objects.collect_facts(db, "pg_catalog")
    multi = [i for i in facts.indexes if len(i.columns) > 1]
    assert multi, "no multi-column index resolved — column extraction is broken"
    for idx in facts.indexes:
        assert all(c and not c.isdigit() for c in idx.columns), \
            f"{idx.name}: columns look like raw attnums, not names: {idx.columns}"
