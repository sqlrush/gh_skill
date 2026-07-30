"""Small formatting helpers — port of internal/probe/wdr/util.go."""
from __future__ import annotations


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


def mib(byts: int) -> str:
    """Format a byte count as a human MiB string."""
    return f2(float(byts) / (1 << 20)) + "MiB"
