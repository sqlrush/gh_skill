#!/usr/bin/env python3
"""health — read-only OpenGauss/GaussDB health check across 12 dimensions.

Port of internal/probe/health/* + internal/cli/health.go. Runs every selected
read-only collector, assembles an evidence pack, and derives deterministic,
threshold-based findings (4 severity bands: 🟢健康/🟡关注/🟠告警/🔴严重).
Per-collector failures degrade (dimension marked unavailable) instead of
aborting. NEVER executes any fix (no kill/VACUUM/DDL/DML).

Usage:
    health.py -c <conn> [--include dims] [--exclude dims] [--top 10] [--format json]
    dims: overview,waits,slowsql,xact,bloat,lwlock,locks,conn,logs,repl,schema,concurrency
"""
from __future__ import annotations

import argparse
import pathlib
import sys
from typing import Optional

_HERE = pathlib.Path(__file__).resolve()
sys.path.insert(0, str(_HERE.parent))          # sibling modules
for _anc in _HERE.parents:                      # locate common/ (repo root or install dir)
    if (_anc / "common" / "__init__.py").exists():
        sys.path.insert(0, str(_anc))
        break

import common  # noqa: E402
import collectors  # noqa: E402
from model import HealthEvidence, Severity, worst  # noqa: E402
from report import render_health, render_health_json  # noqa: E402
from thresholds import Thresholds, default_thresholds  # noqa: E402


def run_health(db, include: list[str], exclude: list[str], top: int,
               th: Thresholds) -> HealthEvidence:
    """Run every selected collector (read-only) and assemble the evidence pack.
    Per-collector failures degrade (available=False); collectors never raise."""
    if top <= 0:
        top = 10
    ev = HealthEvidence()
    inc = set(include)
    exc = set(exclude)
    for key, fn in collectors.registry():
        if inc and key not in inc:
            continue
        if key in exc:
            continue
        d = fn(db, th, top)
        ev.dims.append(d)
        ev.findings.extend(d.findings)
    # Stable sort by severity desc (matches Go sort.SliceStable).
    ev.findings.sort(key=lambda f: int(f.severity), reverse=True)
    ev.overall = worst([f.severity for f in ev.findings])
    return ev


def _split_dim_list(s: str) -> list[str]:
    return [p.strip() for p in (s or "").split(",") if p.strip()]


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(
        prog="health.py",
        description="Read-only health check across 12 dimensions (deterministic findings)")
    ap.add_argument("-c", "--conn", required=True, help="connection name")
    ap.add_argument("--include", default="",
                    help="只采集这些维度(逗号分隔: overview,waits,slowsql,xact,bloat,"
                         "lwlock,locks,conn,logs,repl,schema,concurrency)")
    ap.add_argument("--exclude", default="", help="排除这些维度")
    ap.add_argument("--top", type=int, default=10, help="各 Top 列表条数")
    ap.add_argument("--format", choices=["markdown", "json"], default="markdown")
    ap.add_argument("--timeout", type=int, default=30)
    args = ap.parse_args(argv)

    try:
        db = common.Database.connect(args.conn)
    except (common.ConfigError, common.CredentialError, common.DBError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    try:
        db.set_statement_timeout(args.timeout)
        ev = run_health(db, _split_dim_list(args.include), _split_dim_list(args.exclude),
                        args.top, default_thresholds())
        ev.conn = args.conn
        if args.format == "json":
            print(render_health_json(ev))
        else:
            print(render_health(ev), end="")
        return 0
    except common.DBError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
