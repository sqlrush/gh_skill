"""SQL placeholder auto-substituter (port of internal/probe/placeholder.go).

Replaces ?, $N, :N placeholders in normalized SQL with realistic sample
literals so EXPLAIN can run without real bind values. Pure text heuristics,
no DB lookups. Callers may supply --bind values to override the first N.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

_TO_CHAR_FORMAT_RE = re.compile(r"(?i)to_char\s*\(\s*[a-z_][a-z0-9_.]*\s*,\s*$")
_NUMERIC_LITERAL_RE = re.compile(r"^[+-]?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?$")
_SINGLE_QUOTED_LITERAL_RE = re.compile(r"^'(?:''|[^'])*'$", re.S)
_STRING_TYPES = frozenset({
    "text", "varchar", "character varying", "character", "char", "bpchar",
    "name", "nvarchar", "nvarchar2", "varchar2", "clob",
})
_RAW_SQL_LITERALS = frozenset({
    "null", "true", "false", "current_date", "current_time", "current_timestamp",
    "localtime", "localtimestamp",
})


@dataclass(frozen=True)
class Substitution:
    position: int
    token: str
    context: str
    value: str
    source: str  # rule | rule-format-followup | default | bind | type


@dataclass(frozen=True)
class SubstituteResult:
    sql: str
    substitutions: list = field(default_factory=list)
    placeholders: int = 0


def substitute(sql_text: str, binds: list[str] | None = None,
               types: list[str | None] | None = None) -> SubstituteResult:
    """Replace placeholders with deterministic literals.

    Priority per position: bind (caller-supplied real value) > type (catalog
    column type, see coltypes.infer_types) > text heuristics. Bind values are
    CLI data values, not SQL fragments: known text/date/time contexts are
    quoted and escaped before substitution.
    """
    binds = binds or []
    types = types or []
    positions = _find_all_placeholder_positions(sql_text)
    if not positions:
        return SubstituteResult(sql=sql_text, substitutions=[], placeholders=0)

    subs: list[Substitution] = []
    for i, (start, end) in enumerate(positions):
        token = sql_text[start:end]
        left_ctx = _extract_left_context(sql_text, start, 80)
        context = left_ctx.strip()
        typed = value_for_type(types[i]) if i < len(types) else None
        if i < len(binds) and binds[i] != "":
            type_name = types[i] if i < len(types) else None
            value, source = _format_bind_value(binds[i], type_name, left_ctx), "bind"
        elif typed is not None:
            value, source = typed, "type"
        else:
            value, source = _choose_with_history(left_ctx, subs)
        subs.append(Substitution(start, token, context, value, source))

    # Replace back-to-front so earlier offsets stay valid.
    out = sql_text
    for i in range(len(subs) - 1, -1, -1):
        start, end = positions[i]
        out = out[:start] + subs[i].value + out[end:]

    return SubstituteResult(sql=out, substitutions=subs, placeholders=len(subs))


def _format_bind_value(value: str, type_name: str | None, left_ctx: str) -> str:
    """Turn a CLI bind data value into a SQL literal when its context is known.

    Shell quotes are consumed before Python receives argv. For example, the
    command-line value '2024-01-01 00:00:00' reaches this function without the
    quotes, but a ``TIMESTAMP ?`` expression still requires a SQL-quoted value.
    Explicit SQL literals remain supported for callers that already pass them.

    判定顺序要紧,**列类型优先于值的长相**:账号/机构号是"存在 varchar 列里的
    纯数字",按长相放行会拼出 `acct_no = 6222021234567`(operator does not
    exist: character varying = bigint)。类型未知时才退回按值判断,兜底一律引
    起来——bind 收的是数据值不是 SQL 片段,裸文本拼进去会被当成标识符,报
    `column "abc" does not exist`,而这个报错根本指不到 bind 上。
    """
    raw = value.strip()
    if not raw or _is_explicit_sql_literal(raw):
        return raw
    if _needs_quoted_bind(type_name, left_ctx):
        return "'" + raw.replace("'", "''") + "'"
    # 数值列的错值不引:留着裸值让 coltypes.validate_binds 报出错位,
    # 引起来反而把 'L1' 伪装成一个合法字符串字面量。
    if is_numeric_type(type_name) or _NUMERIC_LITERAL_RE.match(raw):
        return raw          # 裸数字也是 LIMIT ? / OFFSET ? 唯一能用的形态
    return "'" + raw.replace("'", "''") + "'"


def _is_explicit_sql_literal(value: str) -> bool:
    """调用方自己就给了 SQL 字面量(或 NULL/CURRENT_DATE 这类关键字)。

    刻意不含"看着像数字"——那要等类型判完再说,见 _format_bind_value。
    """
    if _SINGLE_QUOTED_LITERAL_RE.match(value):
        return True
    return value.lower() in _RAW_SQL_LITERALS


def _needs_quoted_bind(type_name: str | None, left_ctx: str) -> bool:
    if type_name:
        normalized = type_name.strip().lower()
        if normalized in _STRING_TYPES or normalized.startswith("varchar("):
            return True
        if normalized == "date" or normalized.startswith("timestamp") \
                or normalized.startswith("time") or normalized == "smalldatetime":
            return True
    trimmed = left_ctx.lower().rstrip(" \t\r\n")
    return (_ends_with_keyword(trimmed, "timestamp")
            or _ends_with_keyword(trimmed, "date")
            or _ends_with_keyword(trimmed, "time")
            or _ends_with_keyword(trimmed, "like")
            or _ends_with_keyword(trimmed, "ilike"))


def _find_all_placeholder_positions(sql: str) -> list[tuple[int, int]]:
    """Scan for ?, $N, :N while skipping literals, quoted idents, and comments."""
    out: list[tuple[int, int]] = []
    i, n = 0, len(sql)
    while i < n:
        c = sql[i]
        if c == "'":  # single-quoted string ('' escapes ')
            i += 1
            while i < n:
                if sql[i] == "'":
                    if i + 1 < n and sql[i + 1] == "'":
                        i += 2
                        continue
                    i += 1
                    break
                i += 1
            continue
        if c == '"':  # double-quoted identifier
            i += 1
            while i < n and sql[i] != '"':
                i += 1
            if i < n:
                i += 1
            continue
        if c == "-" and i + 1 < n and sql[i + 1] == "-":  # line comment
            while i < n and sql[i] != "\n":
                i += 1
            continue
        if c == "/" and i + 1 < n and sql[i + 1] == "*":  # block comment
            i += 2
            while i + 1 < n and not (sql[i] == "*" and sql[i + 1] == "/"):
                i += 1
            i = i + 2 if i + 1 < n else n
            continue
        if c == "?":
            out.append((i, i + 1))
            i += 1
            continue
        if c == "$" and i + 1 < n and sql[i + 1].isdigit():
            j = i + 1
            while j < n and sql[j].isdigit():
                j += 1
            out.append((i, j))
            i = j
            continue
        if c == ":" and i + 1 < n and sql[i + 1].isdigit():  # :N only, not :name / ::cast
            j = i + 1
            while j < n and sql[j].isdigit():
                j += 1
            out.append((i, j))
            i = j
            continue
        i += 1
    return out


def _extract_left_context(sql: str, start: int, max_len: int) -> str:
    begin = max(0, start - max_len)
    return sql[begin:start]


def _choose_with_history(left_ctx: str, prev: list[Substitution]) -> tuple[str, str]:
    if prev:
        last = prev[-1]
        if last.source in ("rule", "rule-format-followup") and last.value.startswith("'YYYY"):
            return "'2024-01-15'", "rule-format-followup"
    return _choose(left_ctx)


def _choose(left_ctx: str) -> tuple[str, str]:
    lower = left_ctx.lower()
    trimmed = lower.rstrip(" \t\n")

    if trimmed.endswith("limit"):
        return "100", "rule"
    if trimmed.endswith("offset"):
        return "0", "rule"
    if trimmed.endswith("interval"):
        return "'1 day'", "rule"
    # Typed-literal placeholders (DATE ?, TIMESTAMP ?, TIME ?) need a quoted
    # literal — a bare number gives "syntax error near N". Whole-word match so
    # column names like order_date / start_time are NOT mistaken for the keyword.
    if _ends_with_keyword(trimmed, "timestamp"):
        return "'2024-01-01 00:00:00'", "rule"
    if _ends_with_keyword(trimmed, "date"):
        return "'2024-01-01'", "rule"
    if _ends_with_keyword(trimmed, "time"):
        return "'12:00:00'", "rule"
    if _ends_with_keyword(trimmed, "like") or _ends_with_keyword(trimmed, "ilike"):
        return "'%test%'", "rule"
    if _TO_CHAR_FORMAT_RE.search(left_ctx):
        return "'YYYY-MM-DD'", "rule"

    if _ends_with_op(trimmed, "=") or _ends_with_op(trimmed, "<>") or _ends_with_op(trimmed, "!="):
        if _looks_like_int_column(trimmed):
            return "1", "rule"
        if _looks_like_date_column(trimmed):
            return "'2024-01-01'", "rule"
        return "'test'", "rule"

    if (_ends_with_op(trimmed, "<=") or _ends_with_op(trimmed, ">=")
            or _ends_with_op(trimmed, "<") or _ends_with_op(trimmed, ">")):
        if _looks_like_date_column(trimmed):
            return "'2024-01-01'", "rule"
        return "50", "rule"

    if "in (" in trimmed or "in(" in trimmed:
        if _looks_like_int_column(trimmed):
            return "1", "rule"
        return "'test'", "rule"

    if _ends_with_keyword(trimmed, "between") or _ends_with_keyword(trimmed, "and"):
        return "1", "rule"

    return "1", "default"


def _ends_with_op(s: str, op: str) -> bool:
    if not s.endswith(op):
        return False
    if op == "=" and len(s) >= 2 and s[-2] in "<>!":
        return False
    return True


def _ends_with_keyword(s: str, kw: str) -> bool:
    if not s.endswith(kw):
        return False
    if len(s) == len(kw):
        return True
    prev = s[len(s) - len(kw) - 1]
    return prev in " \t\n(,"


def _looks_like_int_column(ctx: str) -> bool:
    tokens = ctx.split()
    if len(tokens) < 2:
        return False
    for t in reversed(tokens):
        t = t.rstrip("=<>!,()")
        if t == "":
            continue
        if "." in t:
            t = t[t.rindex(".") + 1:]
        return (t.endswith("_id") or t == "id" or t.endswith("_no") or t.endswith("_num")
                or t.endswith("count") or t.endswith("qty") or t.endswith("amount")
                or t.endswith("price"))
    return False


def placeholder_contexts(sql_text: str) -> list[str]:
    """Left context (raw, 80 chars) of each placeholder, in positional order."""
    return [_extract_left_context(sql_text, start, 80)
            for start, _ in _find_all_placeholder_positions(sql_text)]


_IDENT_ONLY_RE = re.compile(r"^[a-z_][a-z0-9_$]*$")
_PLACEHOLDER_TOKEN_RE = re.compile(r"^(?:\?|\$\d+|:\d+)$")
# Tokens between the column and its placeholder that carry no column name.
_NON_COLUMN_TOKENS = frozenset({"in", "any", "some", "all", "not", "between", "and", "("})
# Identifier-shaped SQL keywords that mean "no comparison column here".
_NONCOLUMN_KEYWORDS = frozenset({
    "select", "where", "from", "join", "on", "values", "set", "by", "order",
    "group", "having", "limit", "offset", "when", "then", "else", "case",
    "union", "distinct", "as", "insert", "update", "delete", "returning",
})


def comparison_column(left_ctx: str) -> str | None:
    """Column name being compared against the placeholder, or None.

    Walks tokens right-to-left, skipping operators and IN/ANY/BETWEEN noise,
    so `o.stock_quantity = `, `total_items=` and `category_id IN (` all yield
    the bare column name. Non-identifier contexts (function calls, literals)
    yield None — callers fall back to text heuristics.
    """
    for raw in reversed(left_ctx.lower().split()):
        t = raw.rstrip("=<>!,()").lstrip("(")
        # For the second and later item in IN (?, ?, ...) or BETWEEN ? AND ?,
        # the left context includes earlier placeholders. They are not column
        # names and must be skipped to reach the column at the start of the
        # comparison.
        if t == "" or t in _NON_COLUMN_TOKENS or _PLACEHOLDER_TOKEN_RE.match(t):
            continue
        if t in _NONCOLUMN_KEYWORDS:
            return None
        if "." in t:
            t = t[t.rindex(".") + 1:]
        return t if _IDENT_ONLY_RE.match(t) else None
    return None


_INT_TYPES = frozenset({"tinyint", "smallint", "integer", "bigint", "oid",
                        "int1", "int2", "int4", "int8"})
_NUMERIC_TYPES = frozenset({"numeric", "number", "real", "double precision", "money"})


def is_numeric_type(type_name: str | None) -> bool:
    if not type_name:
        return False
    t = type_name.strip().lower()
    return t in _INT_TYPES or t in _NUMERIC_TYPES


def value_for_type(type_name: str | None) -> str | None:
    """Synthetic literal for a catalog column type; None = defer to heuristics.

    String types deliberately return None so context rules keep working
    (e.g. LIKE still gets '%test%').
    """
    if not type_name:
        return None
    t = type_name.strip().lower()
    if t in _INT_TYPES or t in _NUMERIC_TYPES:
        return "1"
    if t == "date":
        return "'2024-01-01'"
    # "timestamp…" must be tested before "time…" — it shares the prefix.
    if t.startswith("timestamp") or t == "smalldatetime":
        return "'2024-01-01 00:00:00'"
    if t.startswith("time"):
        return "'12:00:00'"
    if t in ("boolean", "bool"):
        return "true"
    if t == "interval":
        return "'1 day'"
    if t == "uuid":
        return "'00000000-0000-0000-0000-000000000000'"
    return None


def _looks_like_date_column(ctx: str) -> bool:
    tokens = ctx.split()
    if len(tokens) < 2:
        return False
    for t in reversed(tokens):
        t = t.rstrip("=<>!,()")
        if t == "":
            continue
        if "." in t:
            t = t[t.rindex(".") + 1:]
        return (t.endswith("_date") or t.endswith("_time") or t.endswith("_at")
                or t == "date" or t == "time" or "timestamp" in t)
    return False
