"""SQL placeholder auto-substituter (port of internal/probe/placeholder.go).

Replaces ?, $N, :N placeholders in normalized SQL with realistic sample
literals so EXPLAIN can run without real bind values. Pure text heuristics,
no DB lookups. Callers may supply --bind values to override the first N.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

_TO_CHAR_FORMAT_RE = re.compile(r"(?i)to_char\s*\(\s*[a-z_][a-z0-9_.]*\s*,\s*$")


@dataclass(frozen=True)
class Substitution:
    position: int
    token: str
    context: str
    value: str
    source: str  # rule | rule-format-followup | default | bind


@dataclass(frozen=True)
class SubstituteResult:
    sql: str
    substitutions: list = field(default_factory=list)
    placeholders: int = 0


def substitute(sql_text: str, binds: list[str] | None = None) -> SubstituteResult:
    """Replace placeholders with deterministic literals (binds override first N)."""
    binds = binds or []
    positions = _find_all_placeholder_positions(sql_text)
    if not positions:
        return SubstituteResult(sql=sql_text, substitutions=[], placeholders=0)

    subs: list[Substitution] = []
    for i, (start, end) in enumerate(positions):
        token = sql_text[start:end]
        left_ctx = _extract_left_context(sql_text, start, 80)
        context = left_ctx.strip()
        if i < len(binds) and binds[i] != "":
            value, source = binds[i], "bind"
        else:
            value, source = _choose_with_history(left_ctx, subs)
        subs.append(Substitution(start, token, context, value, source))

    # Replace back-to-front so earlier offsets stay valid.
    out = sql_text
    for i in range(len(subs) - 1, -1, -1):
        start, end = positions[i]
        out = out[:start] + subs[i].value + out[end:]

    return SubstituteResult(sql=out, substitutions=subs, placeholders=len(subs))


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
