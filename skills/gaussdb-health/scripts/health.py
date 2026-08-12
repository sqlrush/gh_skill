#!/usr/bin/env python3
"""health — read-only OpenGauss/GaussDB health check, 8 local dimensions +
3 sub-skill dimensions aggregated via subprocess.

Port of internal/probe/health/* + internal/cli/health.go. Runs every selected
read-only collector, assembles an evidence pack, and derives deterministic,
threshold-based findings (4 severity bands: 🟢健康/🟡关注/🟠告警/🔴严重).
Per-collector failures degrade (dimension marked unavailable) instead of
aborting. NEVER executes any fix (no kill/VACUUM/DDL/DML).

**waits/lwlock/bloat/locks 不再是本地 collector。** 它们现在由
gaussdb-waitevent / gaussdb-vacuum / gaussdb-lockwait 三个子 skill 各自采集，
health 通过 aggregate.py 以子进程调用、汇总它们的 findings —— 见 collectors.py
顶部的说明。这四个名字在 --include/--exclude 里继续有效，只是改成路由到
对应子 skill（见 `_sub_skill_in_scope` / `SUB_SKILL_DIMS`），保证已有命令行
不因为这次内部重组而失效。

Usage:
    health.py -c <conn> [--include dims] [--exclude dims] [--top 10] [--format json]
    本地维度: overview,slowsql,xact,conn,logs,repl,schema,concurrency
    路由维度（本次采集，来自子 skill）: waits,lwlock -> gaussdb-waitevent；
                                       bloat -> gaussdb-vacuum；
                                       locks -> gaussdb-lockwait

退出码：
    0 报告完整（本地维度 + 所有纳入范围的子 skill 都采集到）
    1 参数/形状被拒绝，或渲染层出错
    2 连接建立失败，没有产出报告
    3 报告已经打印，但至少一个纳入范围的子 skill 采集失败——3 是附加信息，
      不是"没有报告"；报告本身照常完整打印，顶部会点名哪个子 skill 失败、
      为什么。
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

import aggregate  # noqa: E402
import common  # noqa: E402
from common import access  # noqa: E402
import collectors  # noqa: E402
from model import HealthEvidence, Severity, worst  # noqa: E402
from report import render_health, render_health_json  # noqa: E402
from thresholds import Thresholds, default_thresholds  # noqa: E402

# 四个退役的本地维度名，现在路由到对应子 skill 而不是本地 collector。
# waits 和 lwlock 出自同一份 gaussdb-waitevent 报告（同一次 DB time 分解的
# 两个切面：锁等待占比 / 轻量锁等待占比），没法只取其中一半 —— exclude 任
# 一个就整份跳过那次子进程调用，include 任一个就整份触发。
SUB_SKILL_DIMS: dict = {
    "gaussdb-lockwait": ("locks",),
    "gaussdb-waitevent": ("waits", "lwlock"),
    "gaussdb-vacuum": ("bloat",),
}


def _sub_skill_in_scope(skill: str, inc: set, exc: set) -> bool:
    """--include/--exclude 对子 skill 的过滤，语义与本地 8 个维度一致：
    exc 优先于 inc；inc 为空视为"没有限定，谁都算在内"。"""
    dims = SUB_SKILL_DIMS.get(skill, ())
    if any(d in exc for d in dims):
        return False
    if inc and not any(d in inc for d in dims):
        return False
    return True


def _exit_code(sub_results) -> int:
    """0 = 全部纳入范围的子 skill 都采集到；3 = 报告已打印，但至少一个失败。

    **只看 ok，不看 findings 是否为空。** ok=False 时 findings 必然是空列表
    （aggregate.py 的契约），跟"确实没查出风险"的 ok=True 在列表长度上长得
    一样——唯一能分辨两者的字段是 ok。这里特意不写 `if not r.findings`：
    那样会把一次真实的采集失败和一次干净的体检结果混为一谈，恰恰是
    Task 18/19 要防的静默失效。
    """
    if any(not r.ok for r in sub_results):
        return 3
    return 0


def run_health(runner, include: list[str], exclude: list[str], top: int,
               th: Thresholds, sub_results: list = ()) -> HealthEvidence:
    """Run every selected collector (read-only) and assemble the evidence pack.
    Per-collector failures degrade (available=False); collectors never raise.

    sub_results：已经按 scope 过滤过的 aggregate.SubSkillResult 列表（locks/
    waits/lwlock/bloat 四个维度现在从这里来，不再是本地 collector）。只并入
    `r.ok is True` 的 findings —— 判断依据是 ok，不是 findings 是否为空。
    """
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
        d = fn(runner, th, top)
        ev.dims.append(d)
        ev.findings.extend(d.findings)
    for r in sub_results:
        if r.ok:
            ev.findings.extend(r.findings)
    # Stable sort by severity desc (matches Go sort.SliceStable).
    ev.findings.sort(key=lambda f: int(f.severity), reverse=True)
    ev.overall = worst([f.severity for f in ev.findings])
    return ev


def _split_dim_list(s: str) -> list[str]:
    return [p.strip() for p in (s or "").split(",") if p.strip()]


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(
        prog="health.py",
        description="Read-only health check: 8 local dimensions + 3 sub-skill "
                    "dimensions aggregated via subprocess (deterministic findings)")
    ap.add_argument("-c", "--conn", default="", help="连接名（省略则用 gaussdb-login 建立的会话）")
    ap.add_argument("--include", default="",
                    help="只采集这些维度(逗号分隔)。本地: overview,slowsql,xact,conn,logs,"
                         "repl,schema,concurrency；路由到子 skill: waits,lwlock,locks,bloat")
    ap.add_argument("--exclude", default="", help="排除这些维度（同上，含四个路由名）")
    ap.add_argument("--top", type=int, default=10, help="各 Top 列表条数")
    ap.add_argument("--format", choices=["markdown", "json"], default="markdown")
    ap.add_argument("--timeout", type=int, default=None)
    args = ap.parse_args(argv)

    try:
        runner = access.for_conn(args.conn, timeout=args.timeout)
    except (common.ConfigError, common.CredentialError, access.AccessError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    inc_list = _split_dim_list(args.include)
    exc_list = _split_dim_list(args.exclude)
    inc_set, exc_set = set(inc_list), set(exc_list)
    timeout = (args.timeout if args.timeout is not None
              else access.DEFAULT_SKILL_TIMEOUT_SECONDS)

    try:
        # 只有真的有子 skill 维度在范围内时才起子进程——用户只要本地维度
        # （比如 --include overview）时不必白跑三个子进程。
        if any(_sub_skill_in_scope(s, inc_set, exc_set) for s in aggregate.SUB_SKILLS):
            all_sub_results = aggregate.collect_all(args.conn, timeout)
        else:
            all_sub_results = []
        sub_results = [r for r in all_sub_results
                       if _sub_skill_in_scope(r.skill, inc_set, exc_set)]

        ev = run_health(runner, inc_list, exc_list, args.top, default_thresholds(),
                        sub_results=sub_results)
        ev.conn = common.config.resolved_name(args.conn)
        if args.format == "json":
            print(render_health_json(ev, sub_results=sub_results))
        else:
            print(render_health(ev, sub_results=sub_results), end="")
        # 报告已经打印完——3 是附加信息，不是替代输出。exit code 只看 ok，
        # 见 _exit_code 的说明。
        return _exit_code(sub_results)
    except common.DBError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    except Exception as exc:          # 渲染/协议层的失败也要清楚报出来
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
