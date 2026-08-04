#!/usr/bin/env python3
"""procinfo — read-only stored-procedure diagnostic.

Port of internal/probe/proc.go (ProcCollect) + internal/cli/proc.go (collect).
This is the diagnose-only path: it fetches the source, runs the static
structural scanner (loop-internal SQL, per-row DML, dynamic SQL, exception in
loop), lists embedded cursor SELECTs, and snapshots key GUCs. It NEVER rewrites,
verifies, or executes the procedure — for verified index/rewrite optimization,
hand off to the proctune skill.

Usage:
    procinfo.py -c <conn> <schema.proc> [--format json]
"""
from __future__ import annotations

import argparse
import json
import pathlib
import sys
from dataclasses import dataclass
from typing import Optional

_HERE = pathlib.Path(__file__).resolve()
sys.path.insert(0, str(_HERE.parent))          # sibling modules
for _anc in _HERE.parents:                      # locate common/ (repo root or install dir)
    if (_anc / "common" / "__init__.py").exists():
        sys.path.insert(0, str(_anc))
        break

import common  # noqa: E402
from common import access  # noqa: E402
import procanalyze as pa  # noqa: E402
import render  # noqa: E402


for parent in _HERE.parents:
    if (parent / "common" / "sql.py").exists():
        sys.path.insert(0, str(parent))
        break

# SQL 已迁到 scripts/registry/procinfo/ —— 两条路径共用同一份定义




# Key planner/memory GUCs (same whitelist as the proctune evidence collector).


_RUNTIME_NOTE = ("运行时归因（embedded SQL 的真实 calls/avg/total）需实例开启 "
                 "track_stmt_stat_level 捕获嵌套语句；当前按纯静态分析。"
                 "要拿到经验证（cost + 等价 + hypopg 索引）的优化，对同一过程运行 gaussdb-proctune。")


@dataclass(frozen=True)
class GUC:
    name: str
    setting: str
    unit: str


@dataclass(frozen=True)
class EmbeddedStmt:
    line: int
    kind: str
    sql: str


@dataclass(frozen=True)
class ProcEvidence:
    proc: pa.ProcDef
    structure: list
    embedded: list
    runtime_note: str
    gucs: list


def _split_qualified(q: str) -> tuple[str, str]:
    if "." in q:
        i = q.rindex(".")
        return q[:i], q[i + 1:]
    return "", q


KEY_GUCS_SCRIPT = "procinfo.key_gucs"
PROC_DEF_SCRIPT = "procinfo.proc_def"


def collect_gucs(runner) -> list[GUC]:
    rows = runner.run(KEY_GUCS_SCRIPT)
    return [GUC(r["name"], r["setting"], r["unit"]) for r in rows]


def fetch_proc_def(runner, qualified: str) -> pa.ProcDef:
    """经统一入口取数。走中间件还是直连由连接的 driver 决定，这里不感知。"""
    schema, name = _split_qualified(qualified)
    rows = runner.run(PROC_DEF_SCRIPT, {"name": name, "schema": schema})
    if not rows:
        raise ValueError(f"proc {qualified!r} not found")
    r = rows[0]
    return pa.analyze(r["nspname"], r["proname"], r["lanname"],
                      r["prosrc"], r["args"])


def proc_collect(runner, qualified: str) -> ProcEvidence:
    proc = fetch_proc_def(runner, qualified)
    embedded = [EmbeddedStmt(line=c.line, kind=f"cursor:{c.name}", sql=c.select_sql)
                for c in pa.extract_cursors(proc.body)]
    return ProcEvidence(
        proc=proc,
        structure=pa.scan_structure(proc.body),
        embedded=embedded,
        runtime_note=_RUNTIME_NOTE,
        gucs=collect_gucs(runner),
    )


# --- report (port of cli/proc.go procCollectReport) --------------------------

def _arg_string(args: list) -> str:
    return ", ".join(f"{a.name} {a.type}" for a in args)


def proc_info_report(pe: ProcEvidence) -> str:
    d = pe.proc
    b = ["# Proc Info（只读诊断）\n\n## Procedure Source\n",
         f"- Name: `{d.schema}.{d.name}`",
         f"- Language: `{d.lang}`",
         "- Args: `" + _arg_string(d.args) + "`",
         f"- Rollback-safe: {str(d.rollback_safe).lower()}\n"]
    out = "\n".join(b) + "\n" + render.code_block("", d.body)

    out += "\n## Structural Findings\n\n"
    if not pe.structure:
        out += "None.\n"
    else:
        rows = [[f"[H{i + 1}]", str(f.line), f.kind, render.truncate(f.snippet, 80)]
                for i, f in enumerate(pe.structure)]
        out += render.table(["Marker", "Line", "Kind", "Snippet"], rows)

    out += "\n## Embedded Statements\n\n"
    if not pe.embedded:
        out += "None statically extracted.\n"
    else:
        rows = [[str(e.line), e.kind, render.truncate(e.sql, 100)] for e in pe.embedded]
        out += render.table(["Line", "Kind", "SQL"], rows)

    out += "\n## Runtime Attribution\n\n> " + pe.runtime_note + "\n"

    out += "\n## Key Parameters (GUC)\n\n"
    out += render.table(["NAME", "SETTING", "UNIT"],
                        [[g.name, g.setting, g.unit] for g in pe.gucs])
    return out


def _to_jsonable(pe: ProcEvidence) -> dict:
    p = pe.proc
    return {
        "proc": {"schema": p.schema, "name": p.name, "lang": p.lang,
                 "args": [a.__dict__ for a in p.args], "vars": p.vars,
                 "rollback_safe": p.rollback_safe},
        "structure": [f.__dict__ for f in pe.structure],
        "embedded": [e.__dict__ for e in pe.embedded],
        "runtime_note": pe.runtime_note,
        "gucs": [g.__dict__ for g in pe.gucs],
    }


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(
        prog="procinfo.py",
        description="Read-only stored-procedure structural diagnostic")
    ap.add_argument("proc", help="schema.proc")
    ap.add_argument("-c", "--conn", required=True, help="connection name")
    ap.add_argument("--format", choices=["markdown", "json"], default="markdown")
    ap.add_argument("--timeout", type=int, default=None)
    args = ap.parse_args(argv)

    try:
        runner = access.for_conn(args.conn, timeout=args.timeout)
    except (common.ConfigError, common.CredentialError, access.AccessError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    try:
        pe = proc_collect(runner, args.proc)
        if args.format == "json":
            print(json.dumps(_to_jsonable(pe), ensure_ascii=False, indent=2))
        else:
            print(proc_info_report(pe), end="")
        return 0
    except (ValueError, KeyError, common.DBError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    except Exception as exc:          # 渲染/协议层的失败也要清楚报出来
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
