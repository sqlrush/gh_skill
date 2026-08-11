"""WDR evidence types — port of internal/probe/wdr/types.go + findings.go.

Includes JSON to_dict / from_dict so `wdr collect --format json` output can be
read back by `wdr render` (the no-DB render step). Field names match the Go json
tags exactly so a Go-produced evidence pack and a Python one are interchangeable.
"""
from __future__ import annotations

from dataclasses import dataclass, field

# Finding 与 Severity 统一在 common/finding.py —— 本文件曾存一份完全相同的
# 定义，health / wdr / memanalyze 三家各一份。汇总层要跨进程解析这个形状，
# 三份定义迟早分叉，而分叉的表现是**少一条风险**，不是报错。
from common.finding import Finding, Severity, worst  # noqa: F401

# Dimension names — also the ## section titles in the evidence pack.
DIM_LOADPROFILE = "Load Profile"
DIM_DBSTAT = "Database Stat"
DIM_TOPSQL = "Top SQL"
DIM_WAITS = "Wait Events / Classes"
DIM_CHECKPOINT = "Checkpoint / BgWriter / Redo"
DIM_CACHE = "Cache / Memory"
DIM_FILEIO = "File IO"


def _finding_from_dict(d: dict) -> Finding:
    """本文件曾在 Finding 上挂一个同名 staticmethod；Finding 挪到 common 后
    那个 staticmethod 跟着没了，但 DimResult.from_dict / Evidence.from_dict
    仍要从 json 里重建 Finding —— 挪成模块级函数，逻辑照旧一字不改。"""
    return Finding(d.get("dimension", ""), d.get("code", ""),
                   Severity(int(d.get("severity", 0))), d.get("metric", ""),
                   d.get("value", ""), d.get("threshold", ""),
                   d.get("evidence", ""), d.get("sql_id", ""))


@dataclass
class DimResult:
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

    @staticmethod
    def from_dict(d: dict) -> "DimResult":
        return DimResult(
            dimension=d.get("dimension", ""), available=d.get("available", True),
            note=d.get("note", ""), headline=d.get("headline", ""),
            headers=d.get("headers", []) or [], rows=d.get("rows", []) or [],
            findings=[_finding_from_dict(f) for f in d.get("findings", []) or []])


@dataclass
class Window:
    begin_id: int = 0
    end_id: int = 0
    begin_ts: str = ""
    end_ts: str = ""
    duration_min: int = 0
    scope: str = ""
    node: str = ""
    wdr_enabled: bool = False

    def to_dict(self) -> dict:
        return {"begin_id": self.begin_id, "end_id": self.end_id,
                "begin_ts": self.begin_ts, "end_ts": self.end_ts,
                "duration_min": self.duration_min, "scope": self.scope,
                "node": self.node, "wdr_enabled": self.wdr_enabled}

    @staticmethod
    def from_dict(d: dict) -> "Window":
        return Window(
            begin_id=int(d.get("begin_id", 0)), end_id=int(d.get("end_id", 0)),
            begin_ts=d.get("begin_ts", ""), end_ts=d.get("end_ts", ""),
            duration_min=int(d.get("duration_min", 0)), scope=d.get("scope", ""),
            node=d.get("node", ""), wdr_enabled=bool(d.get("wdr_enabled", False)))


@dataclass
class NativeInfo:
    generated: bool = False
    saved_path: str = ""
    bytes: int = 0
    note: str = ""

    def to_dict(self) -> dict:
        d = {"generated": self.generated, "bytes": self.bytes}
        if self.saved_path:
            d["saved_path"] = self.saved_path
        if self.note:
            d["note"] = self.note
        return d

    @staticmethod
    def from_dict(d: dict) -> "NativeInfo":
        return NativeInfo(generated=bool(d.get("generated", False)),
                          saved_path=d.get("saved_path", ""),
                          bytes=int(d.get("bytes", 0)), note=d.get("note", ""))


@dataclass
class Options:
    begin: int = 0
    end: int = 0
    scope: str = "node"
    node: str = ""
    top: int = 10
    save_html: str = ""
    thresholds: object = None


@dataclass
class Evidence:
    conn: str = ""
    target: str = ""
    window: Window = field(default_factory=Window)
    dims: list = field(default_factory=list)
    findings: list = field(default_factory=list)
    overall: Severity = Severity.OK
    native: NativeInfo = field(default_factory=NativeInfo)

    def to_dict(self) -> dict:
        return {"conn": self.conn, "target": self.target,
                "window": self.window.to_dict(),
                "dims": [d.to_dict() for d in self.dims],
                "findings": [f.to_dict() for f in self.findings],
                "overall": int(self.overall),
                "native": self.native.to_dict()}

    @staticmethod
    def from_dict(d: dict) -> "Evidence":
        return Evidence(
            conn=d.get("conn", ""), target=d.get("target", ""),
            window=Window.from_dict(d.get("window", {}) or {}),
            dims=[DimResult.from_dict(x) for x in d.get("dims", []) or []],
            findings=[_finding_from_dict(x) for x in d.get("findings", []) or []],
            overall=Severity(int(d.get("overall", 0))),
            native=NativeInfo.from_dict(d.get("native", {}) or {}))


def degraded(dim: str, reason: str) -> DimResult:
    """DimResult for a collector whose query failed (missing view / counter
    reset). Not fatal: the report shows the dimension as unavailable."""
    return DimResult(dimension=dim, available=False, note=reason,
                     headline="不可用：" + reason)


def dim_severity(d: DimResult) -> Severity:
    return worst([f.severity for f in d.findings])
