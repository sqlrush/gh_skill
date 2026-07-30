"""Catalog collection (I/O only — this module never judges anything).

Each dimension degrades independently: if the index query is denied, the table
findings still come out and the reason lands in `notes`, mirroring
skills/health/scripts/collectors.py.
"""
from __future__ import annotations

import common
from model import IndexFact, ObjectFacts, TableFact

import pathlib
import sys

_HERE = pathlib.Path(__file__).resolve()

for parent in _HERE.parents:
    if (parent / "common" / "sql.py").exists():
        sys.path.insert(0, str(parent))
        break

from common.sql import skill_sqlreview_001,skill_sqlreview_002

_TABLES_Q = skill_sqlreview_001

# indkey is an int2vector. openGauss is based on PostgreSQL 9.2, which has no
# WITH ORDINALITY (9.4+) — expanding the vector with generate_series keeps the
# column order without it. Expression index columns (attnum 0) drop out.
_INDEXES_Q = skill_sqlreview_002



def _as_tuple(val) -> tuple[str, ...]:
    """Array columns come back as list (pg8000) or JSON array (gsql)."""
    if not val:
        return ()
    if isinstance(val, str):                 # defensive: '{a,b}' text form
        return tuple(v for v in val.strip("{}").split(",") if v)
    return tuple(str(v) for v in val)


def _collect_tables(db, schema: str) -> tuple[tuple[TableFact, ...], list[str]]:
    try:
        _, rows = db.query(_TABLES_Q, (schema,))
    except common.DBError as exc:
        return (), [f"表信息采集失败（已降级）：{exc}"]
    return tuple(
        TableFact(schema=str(r[0]), table=str(r[1]), has_pk=bool(r[2]),
                  fks=_as_tuple(r[3]), columns=_as_tuple(r[4]))
        for r in rows
    ), []


def _collect_indexes(db, schema: str) -> tuple[tuple[IndexFact, ...], list[str]]:
    try:
        _, rows = db.query(_INDEXES_Q, (schema,))
    except common.DBError as exc:
        return (), [f"索引信息采集失败（已降级）：{exc}"]
    return tuple(
        IndexFact(schema=str(r[0]), table=str(r[1]), name=str(r[2]),
                  columns=_as_tuple(r[3]), is_unique=bool(r[4]),
                  is_primary=bool(r[5]), scans=int(r[6] or 0))
        for r in rows
    ), []


def collect_facts(db, schema: str) -> ObjectFacts:
    """Snapshot one schema's tables and indexes. Never raises on query failure."""
    tables, t_notes = _collect_tables(db, schema)
    indexes, i_notes = _collect_indexes(db, schema)
    return ObjectFacts(tables=tables, indexes=indexes, notes=tuple(t_notes + i_notes))
