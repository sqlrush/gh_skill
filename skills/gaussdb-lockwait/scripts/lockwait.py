#!/usr/bin/env python3
"""lockwait — 锁堵塞分析：谁挡了谁、挡在什么锁上、挡了多久、根源是谁。

只读。生成的 kill 语句**只输出文本，不执行**。

Usage:
    lockwait.py -c <conn> [--limit 20] [--format json] [--timeout 30]

**能力边界：只能在堵塞发生时抓。** openGauss 的 statement_history 只记
lock_wait_time 总量，不记当时是谁在阻塞 —— 事后追查「某条 SQL 被谁挡了」
在这个内核上做不到。实测记录：sqlid 870461000 等锁 35.4 秒，事后无法定位阻塞者。

**本文件与 task-7-brief.md 的三处对照（详见 task-7-report.md）：**

1. lockwait.pairs 按 locktag 自连接会把与 waiter 请求模式并不冲突的
   holder 也配进来（无辜旁观者）。这里用 common.lockmodes.conflicts()
   过滤；矩阵不认识的模式不当成「不冲突」丢掉——那等于用一个猜测冒充判定，
   而现场可能正被这一对堵着。保留该行，渲染时明确标「互斥关系未知」。
2. waiter_wait_s / holder_xact_age_s 现在可能是 NULL。**协议把 NULL
   渲染成空串，不是 Python None**（common/grmp/serialize.py 的
   render_cell：value is None 时走 settings.null_text，默认就是空串；
   common/grmp/values.py 的 is_null() 正是为这件事存在的）。这里一律用
   is_null() 判断"未知"，不用 `is None`，也不用 `or 默认值`（后者会把
   真正的 0 也吞成"未知"）。未知时长不折成 0：那会让本该最先报出来的
   一行（被遗弃的预备/2PC 事务，持锁时间不可知）在严重度上显得无害，
   与 pairs.yaml 把它排到结果最前面的用意正好相反。
3. recovery.py 生成的函数已是两参数、会话感知的
   pg_cancel_session(pid, sessionid) / pg_terminate_session(pid, sessionid)。
   本文件调用 recovery.kill_for() 时继承这一点，不重新引入单参数形式。
"""
from __future__ import annotations

import argparse
import pathlib
import sys
from dataclasses import dataclass, field
from typing import Optional

_HERE = pathlib.Path(__file__).resolve()
sys.path.insert(0, str(_HERE.parent))          # sibling modules
for _anc in _HERE.parents:                      # locate common/
    if (_anc / "common" / "__init__.py").exists():
        sys.path.insert(0, str(_anc))
        break

import common  # noqa: E402
from common import access  # noqa: E402
from common.finding import Finding, Severity, findings_to_json  # noqa: E402
from common.grmp.values import as_float, as_int, is_null  # noqa: E402
from common.lockmodes import conflict_reason, conflicts, typical_statements  # noqa: E402
import chain  # noqa: E402
import recovery  # noqa: E402
import render  # noqa: E402

SKILL = "gaussdb-lockwait"
DIM = "Locks"

WAIT_WARN_S = 5.0
WAIT_CRIT_S = 60.0

PAIRS_SCRIPT = "lockwait.pairs"
CHAIN_SCRIPT = "lockwait.chain"


@dataclass(frozen=True)
class LockReport:
    pairs: list = field(default_factory=list)
    edges: list = field(default_factory=list)
    roots: dict = field(default_factory=dict)
    deadlocks: list = field(default_factory=list)
    findings: list = field(default_factory=list)


def _mode_conflict(holder_mode, waiter_mode):
    """三态判定：True/False/None（None＝矩阵不认识这个模式，判不出）。"""
    try:
        return conflicts(holder_mode, waiter_mode)
    except KeyError:
        return None


def _filter_conflicting(pairs: list) -> list:
    """按 8 级锁矩阵过滤掉「无辜旁观者」holder。

    lockwait.pairs 在同一 locktag 上把 waiter 与**每一个**已授予的 holder
    配对，其中只有模式互斥的那些才是真正挡住 waiter 的会话；其余只是碰巧
    在同一把锁上、模式并不冲突的旁观者，不该出现在报告里（会被误当成
    「有 N 个会话阻塞」）。

    矩阵不认识的模式（conflicts() 抛 KeyError）不当「不冲突」处理——那是
    冒充一个我们根本没做出的判定，而现场可能正被这一对堵着。这种行的
    唯一正确处理是**保留**，交给渲染层明确标注「互斥关系未知」，让人自己
    判断，而不是让它悄悄从报告里消失。
    """
    kept = []
    for p in pairs:
        verdict = _mode_conflict(p.get("holder_mode"), p.get("waiter_mode"))
        if verdict is not False:   # True 或 None（未知）都保留
            kept.append(p)
    return kept


def collect(runner, limit: int) -> LockReport:
    raw_pairs = runner.run(PAIRS_SCRIPT, {"limit": int(limit)})
    pairs = _filter_conflicting(raw_pairs)
    raw_edges = runner.run(CHAIN_SCRIPT, {})
    edges = [(as_int(e["sessionid"]), as_int(e["block_sessionid"]))
             for e in raw_edges]
    roots = chain.roots(edges)
    deadlocks = chain.cycles(edges)
    findings = _judge(pairs, deadlocks)
    return LockReport(pairs=pairs, edges=edges, roots=roots,
                      deadlocks=deadlocks, findings=findings)


def _wait_seconds(p: dict):
    """取一对的等待秒数。未知（NULL，协议里是空串）原样返回 None，
    不折成 0——0 与「取不到」是两个不同的事实。"""
    raw = p.get("waiter_wait_s")
    return None if is_null(raw) else as_float(raw)


def _judge(pairs: list, deadlocks: list) -> list:
    """阈值判定 —— 确定性的，LLM 不得更改。"""
    out = []
    if deadlocks:
        out.append(Finding(
            DIM, "LOCK_DEADLOCK", Severity.CRITICAL, "死锁环数",
            str(len(deadlocks)), ">0",
            "环上的会话：" + "；".join(
                ", ".join(str(s) for s in ring) for ring in deadlocks)))
    if not pairs:
        return out

    waits = [_wait_seconds(p) for p in pairs]
    known = [w for w in waits if w is not None]
    unknown_count = len(waits) - len(known)
    longest_known = max(known) if known else None

    # 未知时长不当 0：0 意味着"确认过、几乎没等"，未知意味着"等了多久
    # 不知道，可能是被遗弃的预备/2PC 事务，锁可能一直挂着"。与
    # pairs.yaml 的 ORDER BY waiter_wait_s DESC NULLS FIRST 同一立场——
    # 未知在严重度上至少与已知最长值一样紧急，归到最高档，绝不能因为
    # "凑不出一个数字"就显得比已知的长等待更无害。
    if unknown_count or (longest_known is not None and longest_known >= WAIT_CRIT_S):
        code, sev = "LOCK_WAIT_LONG", Severity.CRITICAL
        thr = ">=%.0fs 或时长未知" % WAIT_CRIT_S
        if unknown_count:
            value = ("未知（%d/%d 条等待时长取不到，很可能是被遗弃的预备/2PC "
                     "事务持锁，持续时间不可知；已知最长 %s）"
                     % (unknown_count, len(pairs),
                        "%.1fs" % longest_known if longest_known is not None else "无"))
        else:
            value = "%.1fs" % longest_known
    elif longest_known is not None and longest_known >= WAIT_WARN_S:
        code, sev = "LOCK_WAIT", Severity.WARN
        thr = ">=%.0fs" % WAIT_WARN_S
        value = "%.1fs" % longest_known
    else:
        code, sev = "LOCK_BLOCKED", Severity.NOTICE
        thr = ">0"
        value = "%.1fs" % longest_known

    out.append(Finding(
        DIM, code, sev, "最长锁等待", value, thr,
        "%d 个会话被阻塞；最久的一条等在 %s 上"
        % (len(pairs), pairs[0].get("lock_object") or pairs[0].get("locktype"))))

    for p in pairs:
        if str(p.get("holder_state") or "").startswith("idle in transaction"):
            age_raw = p.get("holder_xact_age_s")
            age_display = "未知" if is_null(age_raw) else age_raw
            out.append(Finding(
                DIM, "LOCK_ROOT_IDLE_XACT", Severity.WARN, "空闲事务持锁",
                "会话 %s" % p.get("holder_sessionid"), "不应长期持有",
                "状态 %s，事务已持续 %s 秒，仍持有 %s"
                % (p.get("holder_state"), age_display, p.get("holder_mode"))))
            break
    return out


def _wait_display(p: dict) -> str:
    raw = p.get("waiter_wait_s")
    return "未知" if is_null(raw) else str(raw)


def render_markdown(rep: LockReport) -> str:
    if not rep.pairs:
        # **空结果要明说。** 空白会被读成「这项没查」。
        if rep.deadlocks:
            # pairs 与 chain 是两条独立查询，中间有时间差：理论上可能查到
            # 死锁环、却拿不到对应的持有者/等待者明细（状态在两次查询之间
            # 变了）。这时不能说"当前无锁等待"——那与死锁环矛盾。
            return ("# 锁堵塞分析\n\n检测到死锁环，但未能取得对应的持有者/"
                    "等待者明细（两次查询之间状态发生了变化）。环上的会话："
                    + "；".join(" → ".join(str(s) for s in ring) + " → …回到起点"
                                for ring in rep.deadlocks) + "\n")
        return ("# 锁堵塞分析\n\n当前无锁等待 —— 查询正常返回，"
                "没有任何会话在等锁。\n")

    waiter_ids = sorted({as_int(p.get("waiter_sessionid")) for p in rep.pairs})
    deepest = max((chain.depth(rep.edges, sid) for sid in waiter_ids), default=0)
    unknown_mode_count = sum(
        1 for p in rep.pairs
        if _mode_conflict(p.get("holder_mode"), p.get("waiter_mode")) is None)
    root_groups = chain.blocked_by_root(rep.edges)

    out = ["# 锁堵塞分析\n",
           "共 %d 条阻塞链、%d 对阻塞关系，涉及 %d 个等待会话，阻塞链最深 %d 层。\n"
           % (len(root_groups), len(rep.pairs), len(waiter_ids), deepest)]
    if unknown_mode_count:
        out.append("> %d 对的锁模式不在已知的 8 级矩阵里，互斥关系无法判定，"
                   "已保留在明细中并单独标注，未按「不冲突」丢弃。\n"
                   % unknown_mode_count)
    if rep.deadlocks:
        out.append("> **检测到死锁**：" + "；".join(
            " → ".join(str(s) for s in ring) + " → …回到起点"
            for ring in rep.deadlocks) + "\n")

    body = []
    for p in rep.pairs:
        body.append([
            str(p.get("waiter_sessionid")), str(p.get("holder_sessionid")),
            "%s ← %s" % (p.get("waiter_mode"), p.get("holder_mode")),
            "%s %s" % (p.get("locktype"), p.get("lock_object") or ""),
            _wait_display(p),
            str(rep.roots.get(as_int(p.get("waiter_sessionid")), "")),
            render.truncate(p.get("holder_query") or "", 60),
        ])
    out.append("## 阻塞明细\n")
    out.append(render.table(
        ["等待会话", "持有会话", "模式对(waiter←holder)", "锁对象",
         "等待秒", "根阻塞会话", "持有者正在执行"], body))

    out.append("\n## 互斥关系\n")
    for p in rep.pairs[:5]:
        holder_mode, waiter_mode = p.get("holder_mode"), p.get("waiter_mode")
        try:
            reason = conflict_reason(holder_mode, waiter_mode)
            typical = typical_statements(holder_mode)
        except KeyError as exc:
            reason = "互斥关系未知：%s" % exc
            typical = "（未知锁模式，无法给出典型语句）"
        out.append("- 会话 %s ← %s：%s\n  持有者取这把锁的典型语句：%s"
                   % (p.get("waiter_sessionid"), p.get("holder_sessionid"),
                      reason, typical))

    out.append("\n## 阻塞链与根\n")
    if not root_groups:
        out.append("没有可归纳的阻塞链（`lockwait.chain` 未给出可用的边，"
                   "「根阻塞会话」栏留空）。\n")
    else:
        for root, blocked in root_groups.items():
            out.append("- 根会话 %s 最终挡住 %d 个会话：%s"
                       % (root, len(blocked),
                          ", ".join(str(s) for s in blocked)))
    out.append("")

    kills, kill_failures = _kill_statements(_root_holders(rep))
    out.append("\n" + recovery.render_kills(kills))
    if kill_failures:
        out.append("\n> **%d 个根 holder 的 kill 语句未能生成**（pid/sessionid "
                   "不是合法数字，为避免编造数据已跳过，请人工核对该会话）："
                   % len(kill_failures))
        for p, reason in kill_failures:
            out.append("> - 会话 %s：%s" % (p.get("holder_sessionid"), reason))
    return "\n".join(out)


def _root_holders(rep: LockReport) -> list:
    """只取**根** holder 去生成 kill 语句 —— 杀中间节点不解堵。"""
    root_ids = set(rep.roots.values())
    seen, out = set(), []
    for p in rep.pairs:
        sid = as_int(p.get("holder_sessionid"))
        if sid in root_ids and sid not in seen:
            seen.add(sid)
            out.append(p)
    return out


def _kill_statements(root_holders: list):
    """给根 holder 生成 kill 语句。

    recovery.kill_for() 内部是裸 `int(holder.get("holder_pid") or 0)`——
    在 brief 起草时输入还是假想的，可以不管；这里接的是真实查询结果，
    协议把所有列值都渲染成字符串，非数字字符串会让那个裸 int() 直接抛。
    这里先用项目统一的 as_int() 做转换：能转就转成真正的 int 再交给
    kill_for()（它内部的 int() 这时只是对已转好的 int 再包一层，不会
    出错）；转不了（真的不是数字，比如取数异常留下的脏值）不能悄悄编一个
    0 出来生成一条看着正常、其实指向假会话的 kill 语句——宁可跳过，把
    原因摆出来让人工核对。
    """
    kills, failures = [], []
    for p in root_holders:
        try:
            safe = dict(p)
            safe["holder_pid"] = as_int(p.get("holder_pid"))
            safe["holder_sessionid"] = as_int(p.get("holder_sessionid"))
        except (ValueError, TypeError) as exc:
            failures.append((p, str(exc)))
            continue
        kills.append(recovery.kill_for(safe))
    return kills, failures


def main(argv: Optional[list] = None) -> int:
    ap = argparse.ArgumentParser(prog="lockwait.py",
                                 description="锁堵塞分析（只读；kill 语句只生成不执行）")
    ap.add_argument("-c", "--conn", default="", help="连接名（省略则用 gaussdb-login 建立的会话）")
    ap.add_argument("--limit", type=int, default=20, help="阻塞明细条数上限")
    ap.add_argument("--format", choices=["markdown", "json"], default="markdown")
    ap.add_argument("--timeout", type=int, default=None)
    args = ap.parse_args(argv)

    try:
        runner = access.for_conn(args.conn, timeout=args.timeout)
    except (common.ConfigError, common.CredentialError, access.AccessError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    try:
        rep = collect(runner, args.limit)
    except (common.DBError, access.QueryError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    try:
        if args.format == "json":
            print(findings_to_json(rep.findings, skill=SKILL))
        else:
            print(render_markdown(rep), end="")
        return 0
    except (ValueError, KeyError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
