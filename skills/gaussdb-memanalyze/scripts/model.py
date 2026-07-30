"""memanalyze evidence types (no I/O).

Severity is script-assigned and deterministic — the LLM may read it, not change
it. Ordering matters: higher int = worse. Mirrors skills/health/scripts/model.py
so the two skills' reports read the same way.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import IntEnum
from types import MappingProxyType
from typing import Mapping


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


def worst(severities) -> Severity:
    """Highest (worst) severity, OK when empty."""
    w = Severity.OK
    for s in severities:
        if s > w:
            w = s
    return w


# Dimension names (also the ## section titles in the report).
DIM_INSTANCE = "L1 实例内存"
DIM_CONTEXT = "L2 内存上下文"
DIM_SESSION = "L3 会话"
DIM_SQL = "L4 SQL"
DIM_OPERATOR = "L5 算子"
DIM_CONFIG = "L6 配置面"

LAYER_OF_DIM = MappingProxyType({
    DIM_INSTANCE: "L1", DIM_CONTEXT: "L2", DIM_SESSION: "L3",
    DIM_SQL: "L4", DIM_OPERATOR: "L5", DIM_CONFIG: "L6",
})


@dataclass(frozen=True)
class Finding:
    """One deterministic, threshold-crossing observation. `code` is stable so
    the report and the SKILL.md verification gate can cross-reference it."""
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
    """One layer's output. Collectors never raise: on failure they return
    degraded(dim, reason) with available=False."""
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


def degraded(dim: str, reason: str) -> DimResult:
    """A layer we could not collect. Never fatal — the report shows it as
    unavailable *with the reason*, which is the whole point: a blind layer must
    explain itself rather than render an empty table that reads like 'all good'."""
    return DimResult(dimension=dim, available=False, note=reason,
                     headline="不可用：" + reason)


def dim_severity(d: DimResult) -> Severity:
    return worst([f.severity for f in d.findings])


@dataclass(frozen=True)
class ViewInfo:
    """One catalog view we probed for: does it exist, and what columns does it
    actually have? Both vary across openGauss / GaussDB versions."""
    name: str = ""
    columns: tuple = ()
    available: bool = False
    reason: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "columns", tuple(self.columns))

    def to_dict(self) -> dict:
        return {"name": self.name, "columns": list(self.columns),
                "available": self.available, "reason": self.reason}


@dataclass(frozen=True)
class Catalog:
    """The views this instance actually offers, one entry per probe slot."""
    views: Mapping[str, ViewInfo] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "views", MappingProxyType(dict(self.views)))

    def get(self, slot: str) -> ViewInfo:
        return self.views.get(slot, ViewInfo(reason="该层视图未探测到（无可用候选视图）"))

    def has(self, slot: str) -> bool:
        return self.get(slot).available

    def reason(self, slot: str) -> str:
        return self.get(slot).reason

    def to_dict(self) -> dict:
        return {slot: vi.to_dict() for slot, vi in self.views.items()}


@dataclass(frozen=True)
class Capability:
    """Which layers can actually produce data, and — when they cannot — why."""
    gucs: Mapping[str, str] = field(default_factory=dict)
    sql_available: bool = False
    operator_available: bool = False
    history_available: bool = False
    context_available: bool = False
    reasons: Mapping[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "gucs", MappingProxyType(dict(self.gucs)))
        object.__setattr__(self, "reasons", MappingProxyType(dict(self.reasons)))

    def to_dict(self) -> dict:
        return {"gucs": dict(self.gucs),
                "sql_available": self.sql_available,
                "operator_available": self.operator_available,
                "history_available": self.history_available,
                "context_available": self.context_available,
                "reasons": dict(self.reasons)}


@dataclass
class MemEvidence:
    conn: str = ""
    target: str = ""
    mode: str = "snapshot"          # snapshot | history | watch
    capability: Capability = field(default_factory=Capability)
    catalog: Catalog = field(default_factory=Catalog)
    dims: list = field(default_factory=list)
    findings: list = field(default_factory=list)
    notes: list = field(default_factory=list)
    overall: Severity = Severity.OK

    def __post_init__(self) -> None:
        self.overall = worst([f.severity for f in self.findings])

    def to_dict(self) -> dict:
        return {"conn": self.conn, "target": self.target, "mode": self.mode,
                "capability": self.capability.to_dict(),
                "catalog": self.catalog.to_dict(),
                "dims": [d.to_dict() for d in self.dims],
                "findings": [f.to_dict() for f in self.findings],
                "notes": list(self.notes),
                "overall": int(self.overall)}
