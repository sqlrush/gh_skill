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
    # 注意：LIMIT 在 SQL 里先于这里的冲突过滤生效——lockwait.pairs 先按
    # limit 截断原始行，_filter_conflicting() 再从截断后的结果里剔除旁观
    # 者。所以 --limit 20 之后拿到的真冲突对可能少于 20 条（截断掉的那些
    # 原始行里如果混了旁观者，真冲突的名额被占掉了）。不是本次要改的行为。
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

    out.append("\n" + _render_recovery_section(rep))
    return "\n".join(out)


def _root_holders(rep: LockReport):
    """把 pairs 里的 holder 分成两类：confirmed（chain 确认是根）与
    unconfirmed（chain 完全没提到对应的 waiter，无法判断）。

    `lockwait.pairs`（`pg_locks` 自连接）与 `lockwait.chain`
    （`pg_thread_wait_status`）是两条**独立、不同视图**上的查询，覆盖面
    不保证一致：一个在 pairs 里真实冲突的 holder，chain 完全可能没有
    这个 waiter 对应的边。这与「chain 明确知道这个 waiter 的根是别的
    会话」是两件不同的事——后者是正常情况（这个 holder 只是链条中间
    节点，不该杀），前者是**数据缺口**，不能被当成「不是根」而悄悄放过：
    对应的 pair 既不会被杀、也不会被特别提示，报告读起来和「已确认无需
    处理」一模一样，而事实是「没能确认」。

    confirmed 按 **holder** 去重——同一个根 holder 挡了好几个 waiter 时，
    只需要一条 kill 语句，杀一次就够了。unconfirmed 按 **(waiter, holder)
    这一对** 去重，不按 holder 单独去重——它统计和列出的单位是「对阻塞
    关系」（报告原话是「N 对……」），如果按 holder 折叠，两个不同的
    waiter 被同一个未确认 holder 挡住时会被压成一条，数字和枚举都会比
    实际少，而这一段的全部职责就是把这些缺口如实列出来，自己先漏一半
    说不过去。
    """
    root_ids = set(rep.roots.values())
    seen, confirmed = set(), []
    unconfirmed_seen, unconfirmed = set(), []
    for p in rep.pairs:
        waiter_sid = as_int(p.get("waiter_sessionid"))
        holder_sid = as_int(p.get("holder_sessionid"))
        if holder_sid in root_ids:
            if holder_sid not in seen:
                seen.add(holder_sid)
                confirmed.append(p)
            continue
        if waiter_sid not in rep.roots:
            key = (waiter_sid, holder_sid)
            if key not in unconfirmed_seen:
                unconfirmed_seen.add(key)
                unconfirmed.append(p)
    return confirmed, unconfirmed


def _kill_statements(root_holders: list):
    """给已确认的根 holder 生成 kill 语句。

    recovery.kill_for() 内部是裸 `int(holder.get("holder_pid") or 0)`——
    在 brief 起草时输入还是假想的，可以不管；这里接的是真实查询结果，
    协议把所有列值都渲染成字符串。两类输入都不能悄悄变成一个编造的 0：

      - 非数字字符串（取数异常留下的脏值）：as_int() 会抛，这里接住。
      - NULL（协议里是空串 ""，见 common.grmp.values.is_null()）：
        as_int() 对空串/None 的默认行为是返回 0，而不是抛——那正是
        本函数要避免的"悄悄编一个 0"，所以在调用 as_int() 之前先用
        is_null() 单独挡一道，NULL 一律算失败，不让它混进 as_int()
        的默认值路径。

    两种情况都不生成语句，把原因摆出来让人工核对，而不是生成一条看着
    正常、其实指向假会话（pid=0）的 kill 语句。
    """
    kills, failures = [], []
    for p in root_holders:
        pid_raw = p.get("holder_pid")
        sid_raw = p.get("holder_sessionid")
        if is_null(pid_raw) or is_null(sid_raw):
            failures.append((p, "holder_pid/holder_sessionid 缺失（NULL），"
                                "拒绝用默认值 0 顶替去生成 kill 语句"))
            continue
        try:
            safe = dict(p)
            safe["holder_pid"] = as_int(pid_raw)
            safe["holder_sessionid"] = as_int(sid_raw)
        except (ValueError, TypeError) as exc:
            failures.append((p, str(exc)))
            continue
        kills.append(recovery.kill_for(safe))
    return kills, failures


def _render_recovery_section(rep: LockReport) -> str:
    """渲染「## 快速恢复语句」整段。

    **不变量（这是本函数存在的唯一理由，按不变量把住，不按分支把住）：**
    只要 `rep.pairs` 非空，这一段就绝不能出现
    `recovery.render_kills([])` 那句裸的「无 —— 当前没有需要处理的根阻塞
    会话」。那句话只在「压根没有阻塞对」这一种情况下才成立——不管是
    chain 缺数据（unconfirmed）、pid/sessionid 拿到但生成语句时失败
    （kill_failures），还是 chain 确认了根、但那个根没有出现在 pairs
    的持有者列里（下面的兜底分支），全都不是「无」，是「没能确认/没能
    生成」，必须在这个小节内部说清楚，不能让读者读到「无需处理」。

    上一轮只在「有 unconfirmed」这一个分支里堵了这句话，结果
    「有 confirmed 但全部生成失败、且没有 unconfirmed」这个分支原样
    漏了出去（`kills == [] and caveat == ""` 时仍然落到
    `recovery.render_kills([])`）。这一轮改成：**pairs 非空时压根不再
    调用 `render_kills([])`**——kills 非空才用它渲染真正的语句列表，
    kills 为空则由本函数自己给标题和「未生成」说明，永远不经过那句
    「无」的文案；再逐项追加 unconfirmed / kill_failures / 兜底三类原因，
    确保 kills 为空时至少有一类原因被打印出来。
    """
    confirmed_roots, unconfirmed_roots = _root_holders(rep)
    kills, kill_failures = _kill_statements(confirmed_roots)

    if not rep.pairs:
        return recovery.render_kills(kills)   # 真正的"无"：没有阻塞对

    if kills:
        section = recovery.render_kills(kills)
    else:
        section = ("## 快速恢复语句\n\n"
                   "> **未生成任何 kill 语句**——原因见下，不代表当前"
                   "无需处理：\n")

    reasons = []
    if unconfirmed_roots:
        reasons.append(
            "> **%d 对阻塞关系因阻塞链数据缺失，未能确认根 holder，"
            "未生成对应 kill 语句**（`lockwait.pairs` 与 `lockwait.chain` "
            "是两条独立查询，覆盖面可能不一致；不能因为 chain 没提到就"
            "当作「不是根」处理，也不能猜它是根去生成语句）：\n"
            % len(unconfirmed_roots)
            + "\n".join(
                "> - 会话 %s ← %s（%s %s）"
                % (p.get("waiter_sessionid"), p.get("holder_sessionid"),
                   p.get("locktype"), p.get("lock_object") or "")
                for p in unconfirmed_roots))
    if kill_failures:
        reasons.append(
            "> **%d 个根 holder 的 kill 语句未能生成**（pid/sessionid "
            "缺失或不是合法数字，为避免编造数据已跳过，请人工核对该会话）：\n"
            % len(kill_failures)
            + "\n".join("> - 会话 %s：%s" % (p.get("holder_sessionid"), reason)
                       for p, reason in kill_failures))
    if not kills and not reasons:
        # 兜底：pairs 非空、没有已确认的根、也没有 unconfirmed、也没有
        # kill_failures——只剩一种情况能落到这里：chain 已经确认了某个
        # waiter 的根，但那个根会话没有作为 holder 出现在**任何**一条
        # pairs 里（它可能持有的是链条上游、这次 pairs 明细没覆盖到的
        # 另一把锁）。既不是「数据缺口」（chain 其实给出了根），也不是
        # 「生成失败」（根本没有材料可以尝试生成），但同样不能被漏掉。
        reasons.append(
            "> 阻塞链数据已确认了根会话，但该会话未出现在「阻塞明细」的"
            "持有者列里（它可能持有的是链条上游、不在本次 pairs 明细"
            "范围内的另一把锁），因此没有材料生成 kill 语句，需人工"
            "进一步排查。\n")

    for r in reasons:
        section += "\n" + r
    return section


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
