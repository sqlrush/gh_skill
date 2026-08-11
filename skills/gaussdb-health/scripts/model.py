"""Health evidence types — port of internal/probe/health/types.go + findings.go.

Severity is a deterministic, gdaa-assigned band (the LLM may not change it).
Ordering matters: higher int = worse.
"""
from __future__ import annotations

from dataclasses import dataclass, field

# Finding 与 Severity 统一在 common/finding.py —— 本文件曾存一份完全相同的
# 定义，health / wdr / memanalyze 三家各一份。汇总层要跨进程解析这个形状，
# 三份定义迟早分叉，而分叉的表现是**少一条风险**，不是报错。
from common.finding import Finding, Severity, worst  # noqa: F401

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
