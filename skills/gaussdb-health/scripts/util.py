"""Small formatting/severity helpers — port of health/util.go + shared bits of
xact.go (sev_by_duration), locks.go (escalate), repl.go (human_bytes)."""
from __future__ import annotations

from model import Severity


def f2(v: float) -> str:
    return f"{v:.2f}"


def i64(v: int) -> str:
    return str(int(v))


def summarize_err(exc: Exception) -> str:
    """Make a degrade note: truncate, no secret leakage."""
    s = str(exc)
    if len(s) > 160:
        s = s[:160] + "…"
    return s


def trunc(s: str, n: int) -> str:
    """Shorten s for a table cell (newlines flattened, rune-safe)."""
    s = (s or "").replace("\n", " ")
    if len(s) > n:
        return s[:n] + "…"
    return s


def sev_by_duration(secs: float, notice: int, warn: int, crit: int) -> Severity:
    """Map an elapsed duration (seconds) to a severity band (xact & locks)."""
    if crit > 0 and secs >= crit:
        return Severity.CRITICAL
    if secs >= warn:
        return Severity.WARN
    if secs >= notice:
        return Severity.NOTICE
    return Severity.OK


def escalate(s: Severity) -> Severity:
    """Bump one severity band, capped at CRITICAL."""
    if s < Severity.CRITICAL:
        return Severity(int(s) + 1)
    return s


def human_bytes(b: int) -> str:
    """Format a byte count compactly (repl/schema)."""
    b = int(b)
    if b >= 1 << 30:
        return f"{b / (1 << 30):.1f}G"
    if b >= 1 << 20:
        return f"{b / (1 << 20):.1f}M"
    if b >= 1 << 10:
        return f"{b / (1 << 10):.1f}K"
    return f"{b}B"
