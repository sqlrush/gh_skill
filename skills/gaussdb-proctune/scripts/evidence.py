"""Tuning evidence bundle (port of probe/collect.go, explain.go, schema.go,
tables.go, guc.go and analyze/risks.go).

Collect gathers everything deterministic the agent needs for root-cause
analysis in one pass: version → plan → findings → tables/indexes/stats → GUCs.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Optional

import render

import pathlib
import sys

_HERE = pathlib.Path(__file__).resolve()

for parent in _HERE.parents:
    if (parent / "common" / "sql.py").exists():
        sys.path.insert(0, str(parent))
        break

from common.sql import skill_proctune_001,skill_proctune_002,skill_proctune_003,skill_proctune_004

# --- table-name extraction (port of tables.go ExtractTables) -----------------

_TABLE_REF_RE = re.compile(r"(?is)\b(?:from|join|update|into)\s+([a-z_][\w.]*)")
_IDENT_RE = re.compile(r"(?i)^[a-z_][\w.]*")
_SQL_KEYWORDS = frozenset({
    "select", "lateral", "unnest", "generate_series", "where", "on", "set",
    "values", "join", "left", "right", "inner", "outer", "cross", "full",
    "natural", "from", "into", "update",
})
_WS = " \t\r\n"


def extract_tables(sql_text: str) -> list[str]:
    """Lowercase, deduped, schema-stripped table names in first-appearance order."""
    seen: set[str] = set()
    out: list[str] = []

    def add(raw: str) -> None:
        name = raw.strip().lower()
        if "." in name:
            name = name[name.rindex(".") + 1:]
        if name in ("", "(") or name in _SQL_KEYWORDS or name in seen:
            return
        seen.add(name)
        out.append(name)

    for m in _TABLE_REF_RE.finditer(sql_text):
        add(m.group(1))
        pos = m.end(1)
        while True:
            while pos < len(sql_text) and sql_text[pos] in _WS:
                pos += 1
            if pos >= len(sql_text):
                break
            if sql_text[pos] == ",":
                pos += 1
                while pos < len(sql_text) and sql_text[pos] in _WS:
                    pos += 1
                im = _IDENT_RE.match(sql_text[pos:])
                if not im:
                    break
                add(im.group(0))
                pos += len(im.group(0))
            else:
                im = _IDENT_RE.match(sql_text[pos:])
                if not im:
                    break
                if im.group(0).lower() in _SQL_KEYWORDS:
                    break
                pos += len(im.group(0))  # alias — skip
                while pos < len(sql_text) and sql_text[pos] in _WS:
                    pos += 1
                if pos >= len(sql_text) or sql_text[pos] != ",":
                    break
                pos += 1
                while pos < len(sql_text) and sql_text[pos] in _WS:
                    pos += 1
                im2 = _IDENT_RE.match(sql_text[pos:])
                if not im2:
                    break
                add(im2.group(0))
                pos += len(im2.group(0))
    return out


# --- DML detection + EXPLAIN (port of explain.go) ----------------------------

_DML_RE = re.compile(r"(?i)^\s*(insert|update|delete|merge)\b")
_CTE_RE = re.compile(r"(?i)^\s*with\b")
_CTE_DML_RE = re.compile(r"(?i)\b(insert|update|delete|merge)\b")


def is_dml(sql_text: str) -> bool:
    if _DML_RE.search(sql_text):
        return True
    return bool(_CTE_RE.search(sql_text) and _CTE_DML_RE.search(sql_text))


def explain(db, sql_text: str, analyze: bool) -> str:
    """EXPLAIN in TEXT format; analyze executes (DML wrapped in rollback)."""
    stmt = (f"EXPLAIN (ANALYZE {str(analyze).lower()}, "
            f"BUFFERS {str(analyze).lower()}, FORMAT TEXT) {sql_text}")
    if analyze and is_dml(sql_text):
        _, rows = db.query_in_rollback(stmt)
    else:
        _, rows = db.query(stmt)
    return "\n".join(r[0] for r in rows)


# --- deterministic plan findings (port of analyze/risks.go) ------------------

@dataclass(frozen=True)
class Finding:
    kind: str
    severity: str  # warn | info
    detail: str
    advice: str


def scan_plan(plan_text: str) -> list[Finding]:
    lower = plan_text.lower()
    orig_lines = plan_text.split("\n")
    out: list[Finding] = []
    for i, line in enumerate(lower.split("\n")):
        trimmed = line.strip()
        if trimmed.startswith("->"):
            trimmed = trimmed[2:].strip()
        detail = orig_lines[i].strip()
        if trimmed.startswith("seq scan"):
            out.append(Finding("seq_scan", "warn", detail,
                "Full table scan; consider an index on the Filter columns if "
                "the table is large and selectivity is high."))
        elif trimmed.startswith("sort"):
            out.append(Finding("sort", "warn", detail,
                "Explicit sort; an index matching ORDER BY may remove it. "
                "Check work_mem if the sort spills to disk."))
    if "nested loop" in lower and "seq scan" in lower:
        out.append(Finding("nestloop_seqscan", "warn",
            "Nested Loop combined with Seq Scan",
            "Inner-side full scans inside a nested loop multiply cost; "
            "consider an index on the join key."))
    if "hash join" in lower:
        out.append(Finding("hash_join", "info", "Hash Join present",
            "Usually fine for large joins; verify hash memory fits work_mem."))
    return out


# --- schema probes (port of schema.go) ---------------------------------------

@dataclass(frozen=True)
class TableInfo:
    schema: str
    name: str
    pages: int
    tuples: int
    kind: str
    size_mb: float


@dataclass(frozen=True)
class IndexInfo:
    table: str
    name: str
    is_unique: bool
    is_primary: bool
    definition: str


@dataclass(frozen=True)
class ColumnStat:
    table: str
    column: str
    n_distinct: float
    null_frac: float
    avg_width: int
    correlation: Optional[float]


@dataclass(frozen=True)
class GUC:
    name: str
    setting: str
    unit: str


def _quote_literals(names: list[str]) -> str:
    quoted = ["'" + n.replace("'", "''") + "'" for n in names]
    return "(" + ",".join(quoted) + ")"


def collect_tables(db, names: list[str]) -> list[TableInfo]:
    if not names:
        return []
    q = skill_proctune_001.format(names=names)
    _, rows = db.query(q)
    return [TableInfo(r[0], r[1], int(r[2]), int(r[3]), r[4], float(r[5])) for r in rows]


def collect_indexes(db, names: list[str]) -> list[IndexInfo]:
    if not names:
        return []
    q = skill_proctune_002.format(names=names)
    _, rows = db.query(q)
    return [IndexInfo(r[0], r[1], bool(r[2]), bool(r[3]), r[4]) for r in rows]


def collect_column_stats(db, names: list[str]) -> list[ColumnStat]:
    if not names:
        return []
    q = skill_proctune_003.format(names=names)
    _, rows = db.query(q)
    out = []
    for r in rows:
        corr = float(r[5]) if r[5] is not None else None
        out.append(ColumnStat(r[0], r[1], float(r[2]), float(r[3]), int(r[4]), corr))
    return out


_KEY_GUCS_QUERY = skill_proctune_004


def collect_gucs(db) -> list[GUC]:
    _, rows = db.query(_KEY_GUCS_QUERY)
    return [GUC(r[0], r[1], r[2]) for r in rows]


# --- evidence orchestration (port of collect.go) -----------------------------

@dataclass(frozen=True)
class Evidence:
    sql: str
    version: str
    plan: str
    analyzed: bool
    tables: list = field(default_factory=list)
    indexes: list = field(default_factory=list)
    columns: list = field(default_factory=list)
    gucs: list = field(default_factory=list)
    findings: list = field(default_factory=list)


def collect(db, sql_text: str, do_analyze: bool) -> Evidence:
    version = db.scalar("SELECT version()")
    plan = explain(db, sql_text, do_analyze)
    names = extract_tables(sql_text)
    return Evidence(
        sql=sql_text,
        version=version,
        plan=plan,
        analyzed=do_analyze,
        findings=scan_plan(plan),
        tables=collect_tables(db, names),
        indexes=collect_indexes(db, names),
        columns=collect_column_stats(db, names),
        gucs=collect_gucs(db),
    )


# --- evidence renderer (port of cli/collect.go evidenceReport) ---------------

def evidence_report(ev: Evidence) -> str:
    out = (
        "# Tuning Evidence Bundle\n\n## Environment\n\n- Version: " + ev.version +
        "\n- Analyzed: " + str(ev.analyzed).lower() + "\n" +
        "\n## SQL\n\n" + render.code_block("sql", ev.sql) +
        "\n## Execution Plan\n\n" + render.code_block("", ev.plan)
    )
    out += "\n## Deterministic Findings\n\n"
    if not ev.findings:
        out += "None.\n"
    for f in ev.findings:
        out += f"- **[{f.severity}] {f.kind}**: {f.detail} — {f.advice}\n"

    t_rows = [[t.schema, t.name, str(t.pages), str(t.tuples), t.kind, f"{t.size_mb:.1f}"]
              for t in ev.tables]
    out += "\n## Tables\n\n" + render.table(
        ["SCHEMA", "TABLE", "PAGES", "TUPLES", "KIND", "SIZE_MB"], t_rows)

    i_rows = [[ix.table, ix.name, str(ix.is_unique).lower(), str(ix.is_primary).lower(),
               render.truncate(ix.definition, 120)] for ix in ev.indexes]
    out += "\n## Indexes\n\n" + render.table(
        ["TABLE", "INDEX", "UNIQUE", "PRIMARY", "DEF"], i_rows)

    c_rows = []
    for c in ev.columns:
        corr = "n/a" if c.correlation is None else f"{c.correlation:.2f}"
        c_rows.append([c.table, c.column, f"{c.n_distinct:.2f}",
                       f"{c.null_frac:.3f}", str(c.avg_width), corr])
    out += "\n## Column Statistics\n\n" + render.table(
        ["TABLE", "COLUMN", "N_DISTINCT", "NULL_FRAC", "AVG_WIDTH", "CORRELATION"], c_rows)

    g_rows = [[g.name, g.setting, g.unit] for g in ev.gucs]
    out += "\n## Key Parameters (GUC)\n\n" + render.table(
        ["NAME", "SETTING", "UNIT"], g_rows)
    return out
