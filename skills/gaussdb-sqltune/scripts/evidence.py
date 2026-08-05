"""Tuning evidence bundle (port of probe/collect.go, explain.go, schema.go,
tables.go, guc.go and analyze/risks.go).

Collect gathers everything deterministic the agent needs for root-cause
analysis in one pass: version → plan → findings → tables/indexes/stats → GUCs.

取数分两条口子，**刻意不统一**：

  固定查询（版本/表/索引/列统计/GUC）  走 runner —— 统一入口执行已注册脚本，
                                       走中间件还是直连由连接的 driver 决定
  EXPLAIN 用户 SQL                     走 db —— 原始会话。任意 SQL 无法预注册，
                                       白名单模型下没有对应的口子

后者是**安全策略的边界**，不是技术难点：把任意 SQL 塞进一条「直通脚本」
等于把白名单拆了。所以它只走 access.session_for()，拿不到就在入口失败。
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

# 字符串值的还原一律用共用层：bool("f") 是 True 这类坑错一次就是静默出
# 错误结论，不该有各 skill 自己的一份实现
from common.grmp.values import as_bool, as_float, as_int  # noqa: E402

# SQL 已迁到 scripts/registry/sqltune/ —— 两条路径共用同一份定义
VERSION_SCRIPT = "sqltune.version"
TABLES_SCRIPT = "sqltune.tables"
INDEXES_SCRIPT = "sqltune.indexes"
COLUMN_STATS_SCRIPT = "sqltune.column_stats"
KEY_GUCS_SCRIPT = "sqltune.key_gucs"


# --- 协议取值：所有列值都是字符串，NULL 是空串 ------------------------------
#
# 按列名取值而不是下标：列序变了会当场 KeyError，而不是安静地把 size_mb
# 当成 reltuples。取值本身一律走 common.grmp.values 的 as_bool / as_int /
# as_float，本文件只为一个 openGauss 特有的写法留一个包装。

def _pages(raw) -> int:
    """页数列。**openGauss 的 pg_class.relpages 是 double precision**，
    取回来是 "3704.0"，as_int 会当场报错（它刻意不接受非整数写法）。

    迁移前这里拿到的是 float 对象，int(3704.0) 直接截断；这里补上同一次截断，
    行为与迁移前逐字一致。**只在本列放宽，不下沉到 common.grmp.values** ——
    「声明为整数的列里出现小数」在别处多半意味着取错了列，共用层报错是对的。
    """
    text = "" if raw is None else str(raw).strip()
    if "." in text:
        return int(float(text))
    return as_int(text)


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


def explain_via_script(runner, sql_text: str, analyze: bool) -> str:
    """走已注册的 EXPLAIN 模板 —— 没有原始会话时用这条。

    调用前必须先过 ensure_explainable()：模板是文本替换，参数位就是注入面。
    """
    script = "sqltune.plan_text_analyze" if analyze else "sqltune.plan_text"
    rows = runner.run(script, {"sql": sql_text})
    return "\n".join(str(next(iter(r.values()), "")) for r in rows)


def explain(db, sql_text: str, analyze: bool) -> str:
    """EXPLAIN in TEXT format; analyze executes (DML wrapped in rollback).

    **db 是原始会话，不是 runner。** 这里的 SQL 是用户临时给的，每次都不同，
    注册不进白名单 —— 见模块头。调用方负责先用 access.session_for() 拿到会话。
    """
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


def _quoted_list(names: list[str]) -> str:
    """['a', "b'c"] -> "'a','b''c'"。**不带外层括号** —— 括号写在脚本的
    `IN ({{names}})` 里。值和 SQL 各出一半括号是最容易踩的坑（会得到
    IN (('a','b'))），所以这里只出字面量。
    """
    if not names:
        # IN () 是语法错误。调用方都有空列表短路，走到这里说明短路漏了。
        raise ValueError("_quoted_list: 表名列表为空，无法构造 IN 列表")
    return ",".join("'" + n.replace("'", "''") + "'" for n in names)


def collect_tables(runner, names: list[str]) -> list[TableInfo]:
    if not names:
        return []
    rows = runner.run(TABLES_SCRIPT, {"names": _quoted_list(names)})
    return [TableInfo(r["nspname"], r["relname"], _pages(r["relpages"]),
                      as_int(r["reltuples"]), r["relkind"],
                      as_float(r["size_mb"]))
            for r in rows]


def collect_indexes(runner, names: list[str]) -> list[IndexInfo]:
    if not names:
        return []
    rows = runner.run(INDEXES_SCRIPT, {"names": _quoted_list(names)})
    # bool("f") 是 True —— 直接 bool() 会把每个索引都报成 UNIQUE/PRIMARY
    return [IndexInfo(r["table_name"], r["index_name"], as_bool(r["indisunique"]),
                      as_bool(r["indisprimary"]), r["index_def"])
            for r in rows]


def collect_column_stats(runner, names: list[str]) -> list[ColumnStat]:
    if not names:
        return []
    rows = runner.run(COLUMN_STATS_SCRIPT, {"names": _quoted_list(names)})
    # correlation 经常是 NULL，默认值给 None 而不是 0.0：0.0 会被读成
    # 「物理顺序与索引顺序完全无关」，那是一个结论，不是「不知道」。
    # 协议把 NULL 与真空串渲染成同一个值，这里只能把空串一律当 NULL。
    return [ColumnStat(r["tablename"], r["attname"], as_float(r["n_distinct"]),
                       as_float(r["null_frac"]), as_int(r["avg_width"]),
                       as_float(r["correlation"], None))
            for r in rows]


def collect_gucs(runner) -> list[GUC]:
    rows = runner.run(KEY_GUCS_SCRIPT)
    return [GUC(r["name"], r["setting"], r["unit"]) for r in rows]


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


def collect(runner, db, sql_text: str, do_analyze: bool) -> Evidence:
    """runner 取固定查询；EXPLAIN 用户 SQL 视有无原始会话走两条路。

    db 为 None 表示这条连接给不了持久会话（中间件、或每语句起子进程的 gsql）。
    这时 EXPLAIN 走已注册模板 —— 单条 EXPLAIN 本来就零状态，不需要会话。
    真正需要会话的只有 hypopg 索引验证，由调用方跳过并标注。
    """
    rows = runner.run(VERSION_SCRIPT)
    version = rows[0]["version"] if rows else ""
    if db is None:
        plan = explain_via_script(runner, sql_text, do_analyze)
    else:
        plan = explain(db, sql_text, do_analyze)
    names = extract_tables(sql_text)
    return Evidence(
        sql=sql_text,
        version=version,
        plan=plan,
        analyzed=do_analyze,
        findings=scan_plan(plan),
        tables=collect_tables(runner, names),
        indexes=collect_indexes(runner, names),
        columns=collect_column_stats(runner, names),
        gucs=collect_gucs(runner),
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
