"""Health evidence types — port of internal/probe/health/types.go + findings.go.

Severity is a deterministic, gdaa-assigned band (the LLM may not change it).
Ordering matters: higher int = worse.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import IntEnum


class Severity(IntEnum):
    OK = 0
    NOTICE = 1
    WARN = 2
    CRITICAL = 3

    def label(self) -> str:
        return {
            Severity.CRITICAL: "🔴严重",
            Severity.WARN: "🟠告警",
            Severity.NOTICE: "🟡关注",
        }.get(self, "🟢健康")


def worst(severities: list[Severity]) -> Severity:
    """Return the highest (worst) severity, OK if empty."""
    w = Severity.OK
    for s in severities:
        if s > w:
            w = s
    return w


# Dimension names (also the ## section titles in the evidence pack).
DIM_OVERVIEW = "Overview"
DIM_WAITS = "Wait Events"
DIM_SLOWSQL = "Slow SQL"
DIM_XACT = "Long & Idle Transactions"
DIM_BLOAT = "Dead Tuples & Bloat"
DIM_LWLOCK = "Lightweight Locks (LWLock)"
DIM_LOCKS = "Transaction Locks & Blocking Chains"
DIM_CONN = "Connections"
DIM_LOGS = "Checkpoint / WAL / Archiving"
DIM_REPL = "Replication / Standby"
DIM_SCHEMA = "Schema / Objects"
DIM_CONCURRENCY = "Transactions / Concurrency"


@dataclass(frozen=True)
class Finding:
    """One deterministic, threshold-crossing observation. ``code`` is a stable
    identifier the skill's verification gate and report cross-reference."""
    dimension: str
    code: str
    severity: Severity
    metric: str
    value: str
    threshold: str
    evidence: str

    def to_dict(self) -> dict:
        return {"dimension": self.dimension, "code": self.code,
                "severity": int(self.severity), "metric": self.metric,
                "value": self.value, "threshold": self.threshold,
                "evidence": self.evidence}


@dataclass
class DimResult:
    """One dimension's collected output. Collectors never raise; on query
    failure they set available=False with a note (degrade, not fatal)."""
    dimension: str
    available: bool = True
    note: str = ""
    headline: str = ""
    headers: list = field(default_factory=list)
    rows: list = field(default_factory=list)
    findings: list = field(default_factory=list)

    def to_dict(self) -> dict:
        d = {"dimension": self.dimension, "available": self.available,
             "headline": self.headline}
        if self.note:
            d["note"] = self.note
        if self.headers:
            d["headers"] = self.headers
        if self.rows:
            d["rows"] = self.rows
        if self.findings:
            d["findings"] = [f.to_dict() for f in self.findings]
        return d


@dataclass
class HealthEvidence:
    conn: str = ""
    target: str = ""
    dims: list = field(default_factory=list)
    findings: list = field(default_factory=list)
    overall: Severity = Severity.OK

    def to_dict(self) -> dict:
        return {"conn": self.conn, "target": self.target,
                "dims": [d.to_dict() for d in self.dims],
                "findings": [f.to_dict() for f in self.findings],
                "overall": int(self.overall)}


def degraded(dim: str, reason: str) -> DimResult:
    """Build a DimResult for a collector whose query failed (missing view /
    no permission). Not fatal: the report shows the dimension as unavailable."""
    return DimResult(dimension=dim, available=False, note=reason,
                     headline="不可用：" + reason)


def dim_severity(d: DimResult) -> Severity:
    """Worst finding severity within one dimension."""
    return worst([f.severity for f in d.findings])
