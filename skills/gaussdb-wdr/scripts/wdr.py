#!/usr/bin/env python3
"""wdr — read-only OpenGauss/GaussDB WDR snapshot interpretation.

Port of internal/probe/wdr/* + internal/cli/wdr.go. Three subcommands:

  snaps      list WDR snapshots + preflight enable_wdr_snapshot (read-only)
  collect    self-compute snapshot-delta evidence across 7 dimensions +
             deterministic findings; best-effort留底 native generate_wdr_report
  render     no-DB: render the final report from evidence JSON + LLM interp JSON,
             after a mechanical anchoring recheck of the interp vs the evidence

NEVER creates snapshots, NEVER executes changes.

Usage:
    wdr.py snaps    -c <conn> [--limit 20]
    wdr.py collect  -c <conn> --begin <id> --end <id> [--top 10] [--scope node|cluster] [--format json]
    wdr.py render   --evidence ev.json --interp interp.json [--format md|ansi]
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
from collectors import collect_evidence  # noqa: E402
from interp import load_evidence, load_interp  # noqa: E402
from finalreport import render_report  # noqa: E402
from model import Options  # noqa: E402
from report import render_evidence, render_evidence_json  # noqa: E402
from snaps import snaps as run_snaps  # noqa: E402
from thresholds import default_thresholds  # noqa: E402


def _cmd_snaps(args) -> int:
    try:
        db = common.Database.connect(args.conn)
    except (common.ConfigError, common.CredentialError, common.DBError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    try:
        db.set_statement_timeout(args.timeout)
        print(run_snaps(db, args.limit), end="")
        return 0
    except common.DBError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    finally:
        db.close()


def _cmd_collect(args) -> int:
    if args.begin <= 0 or args.end <= 0 or args.end <= args.begin:
        print(f"error: --begin/--end required with end>begin (run: wdr.py snaps -c {args.conn})",
              file=sys.stderr)
        return 1
    try:
        db = common.Database.connect(args.conn)
    except (common.ConfigError, common.CredentialError, common.DBError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    try:
        db.set_statement_timeout(args.timeout)
        opt = Options(begin=args.begin, end=args.end, scope=args.scope, node=args.node,
                      top=args.top, save_html=args.save_html or "",
                      thresholds=default_thresholds())
        ev = collect_evidence(db, opt)
        ev.conn = args.conn
        if args.format == "json":
            print(render_evidence_json(ev))
        else:
            print(render_evidence(ev), end="")
        return 0
    except common.DBError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    finally:
        db.close()


def _cmd_render(args) -> int:
    if not args.evidence or not args.interp:
        print("error: --evidence 与 --interp 均必填", file=sys.stderr)
        return 1
    try:
        ev = load_evidence(args.evidence)
        in_ = load_interp(args.interp)
        report = render_report(ev, in_, fmt=args.format, no_color=args.no_color)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    if args.out:
        try:
            pathlib.Path(args.out).write_text(report, encoding="utf-8")
        except OSError as exc:
            print(f"error: 写报告文件失败：{exc}", file=sys.stderr)
            return 1
        print(f"report written to {args.out}", file=sys.stderr)
        return 0
    print(report, end="")
    return 0


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(prog="wdr.py",
                                 description="Read-only WDR snapshot interpretation")
    sub = ap.add_subparsers(dest="cmd", required=True)

    ps = sub.add_parser("snaps", help="list WDR snapshots + preflight")
    ps.add_argument("-c", "--conn", required=True)
    ps.add_argument("--limit", type=int, default=20, help="列出最近 N 个快照")
    ps.add_argument("--timeout", type=int, default=30)

    pc = sub.add_parser("collect", help="collect snapshot-delta evidence + findings")
    pc.add_argument("-c", "--conn", required=True)
    pc.add_argument("--begin", type=int, default=0, help="begin snapshot id (required)")
    pc.add_argument("--end", type=int, default=0, help="end snapshot id (required, > begin)")
    pc.add_argument("--scope", default="node", help="cluster | node")
    pc.add_argument("--node", default="", help="node name (empty → auto-detect)")
    pc.add_argument("--top", type=int, default=10, help="各 Top 列表条数")
    pc.add_argument("--save-html", dest="save_html", default="",
                    help="落盘原生 WDR 原文到此路径（审计，默认不落）")
    pc.add_argument("--format", choices=["markdown", "json"], default="markdown")
    pc.add_argument("--timeout", type=int, default=30)

    pr = sub.add_parser("render", help="render final report from evidence + interp (no DB)")
    pr.add_argument("--evidence", default="", help="collect --format json 产出的证据 JSON 路径")
    pr.add_argument("--interp", default="", help="LLM 产出的 interp.json 路径")
    pr.add_argument("--format", choices=["md", "ansi"], default="md")
    pr.add_argument("--no-color", dest="no_color", action="store_true", help="ansi 模式去掉颜色")
    pr.add_argument("--out", default="", help="落盘到文件（默认打印到 stdout）")

    args = ap.parse_args(argv)
    if args.cmd == "snaps":
        return _cmd_snaps(args)
    if args.cmd == "collect":
        return _cmd_collect(args)
    return _cmd_render(args)


if __name__ == "__main__":
    raise SystemExit(main())
