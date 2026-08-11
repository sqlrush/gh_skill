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


def _classify_pairs(rep: LockReport) -> dict:
    """把 `rep.pairs` 里**每一对**都归到唯一一类——这是一个全函数
    （total function）：并集覆盖全部 pairs，四类互不重叠，任何一对都不
    会因为落在两类判据的缝隙里而消失。

    上两轮的教训：先是只分 confirmed/unconfirmed 两类，遗漏了"根已确认
    但没出现在 pairs 里"的第三种情况；后来在渲染层用 `kills`/`reasons`
    两个聚合状态去判断该不该提示，又在"两条链混在一份报告里、一条成功
    生成语句、另一条恰好落进第三类"时失效——聚合判断只关心"整体有没有
    话可说"，看不见"某一条具体的 pair 有没有被照顾到"。这次把判断挪到
    最细的粒度：**每一对**在四类里必属其一，渲染层只管照着这四类如实
    转述，不再做任何整体性的"还有没有话说"式推断。

    四类（对每一对 (waiter, holder)）：

      root          holder 本身就是（某条链的）确认根——是 kill 语句的
                    直接对象。
      data_gap      chain 里完全没有这个 waiter 的数据（waiter_sid 不在
                    rep.roots 里）——不知道这个 holder 是不是根。
      intermediate  chain 给出了这个 waiter 的根，根不是这个 holder，
                    但那个根**确实**作为某一对的 holder 出现在了
                    rep.pairs 里——这个 holder 只是链条中间节点，真正
                    该杀的根有另一对负责生成语句/说明。
      orphan_root   chain 给出了这个 waiter 的根，根不是这个 holder，
                    而那个根**没有**作为任何一对的 holder 出现在
                    rep.pairs 里——根已确认，但没有材料（pid/状态/
                    query……）生成语句，需要人工继续查。

    `lockwait.pairs`（`pg_locks` 自连接）与 `lockwait.chain`
    （`pg_thread_wait_status`）是两条**独立、不同视图**上的查询，覆盖面
    不保证一致，data_gap 与 orphan_root 都是这种不一致的直接后果。
    """
    root_ids = set(rep.roots.values())
    holder_sids_in_pairs = {as_int(p.get("holder_sessionid")) for p in rep.pairs}

    buckets: dict = {"root": [], "data_gap": [], "intermediate": [], "orphan_root": []}
    for p in rep.pairs:
        waiter_sid = as_int(p.get("waiter_sessionid"))
        holder_sid = as_int(p.get("holder_sessionid"))
        if holder_sid in root_ids:
            buckets["root"].append(p)
        elif waiter_sid not in rep.roots:
            buckets["data_gap"].append(p)
        elif rep.roots[waiter_sid] in holder_sids_in_pairs:
            buckets["intermediate"].append(p)
        else:
            buckets["orphan_root"].append(p)
    return buckets


def _dedup_by_holder(pairs: list) -> list:
    """按 holder 去重——生成 kill 语句只需要一条，同一个根挡了几个
    waiter 不该重复杀。"""
    seen, out = set(), []
    for p in pairs:
        h = as_int(p.get("holder_sessionid"))
        if h not in seen:
            seen.add(h)
            out.append(p)
    return out


def _dedup_by_waiter_and_holder(pairs: list) -> list:
    """按 (waiter, holder) 这一对去重——枚举"缺口"类文本的单位是
    「对阻塞关系」，按 holder 单独折叠会让两个不同 waiter 撞上同一个
    holder 时被压成一条，数字和枚举都比实际少。"""
    seen, out = set(), []
    for p in pairs:
        key = (as_int(p.get("waiter_sessionid")), as_int(p.get("holder_sessionid")))
        if key not in seen:
            seen.add(key)
            out.append(p)
    return out


def _waiters_by_holder(pairs: list) -> dict:
    """holder_sessionid -> 被它挡住的 waiter_sessionid 列表（保留原始
    字符串、去重、按出现顺序）。用来在 kill 语句/失败说明旁边标注
    "这条语句预计解除了哪些会话"——`recovery.kill_for()` 只知道 holder，
    不知道被它挡住的是谁，这段追加要在 lockwait.py 这一层做。"""
    out: dict = {}
    for p in pairs:
        h = as_int(p.get("holder_sessionid"))
        w = p.get("waiter_sessionid")
        bucket = out.setdefault(h, [])
        if w not in bucket:
            bucket.append(w)
    return out


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

    **不变量（这是本函数存在的唯一理由）：`rep.pairs` 里的每一对，要么
    被一条已生成的 kill 语句覆盖，要么有一句写明的理由说明为什么没有。
    不允许任何一对因为"恰好还有别的对生成了语句/给出了理由"就被聚合
    状态判断悄悄放过。**

    这是本函数第三次因为同一类问题被改：
      round 1：只判断"有没有 unconfirmed"，漏了"confirmed 但全部生成
        失败"这个分支。
      round 2：把裸"无"堵死了，但用 `kills`/`reasons` 两个**聚合**布尔
        状态去决定要不要打印某一类说明——两条独立的阻塞链混在一份报告
        里、一条成功生成了语句时，`kills` 非空，另一条恰好落进"根已
        确认但不在 pairs 里"这个兜底分支就被跳过了，因为兜底分支的
        触发条件写的是 `if not kills and not reasons`，只要**别的**pair
        让 kills 非空，这一对就静默消失。
      round 3（本次）：不再用任何聚合状态做判断。`_classify_pairs()`
        把每一对都放进四个互斥的桶（root / data_gap / intermediate /
        orphan_root），下面对四个桶**各自独立**地渲染说明——桶 A 是否
        非空只取决于桶 A 里有没有 pair，与桶 B 是否非空无关。这样不管
        一份报告里同时出现多少条独立的阻塞链、各自处于什么状态，每一对
        都有自己对应的一句话，不会因为报告里别的部分"看起来正常"就被
        捎带着忽略。
    """
    if not rep.pairs:
        return recovery.render_kills([])   # 真正的"无"：没有阻塞对

    buckets = _classify_pairs(rep)
    root_pairs = _dedup_by_holder(buckets["root"])
    waiters_by_holder = _waiters_by_holder(buckets["root"])
    kills, kill_failures = _kill_statements(root_pairs)

    if kills:
        section = recovery.render_kills(kills)
        notes = []
        for k in kills:
            waiters = waiters_by_holder.get(k.target_sessionid, [])
            if waiters:
                notes.append(
                    "> 会话 %s 的这条语句预计解除：%s 的阻塞"
                    % (k.target_sessionid, "、".join(str(w) for w in waiters)))
        if notes:
            section += "\n" + "\n".join(notes)
    else:
        section = ("## 快速恢复语句\n\n"
                   "> **未生成任何 kill 语句**——原因见下，不代表当前"
                   "无需处理：\n")

    if kill_failures:
        lines = []
        for p, reason in kill_failures:
            h = as_int(p.get("holder_sessionid"))
            waiters = waiters_by_holder.get(h) or [p.get("waiter_sessionid")]
            lines.append("> - 会话 %s（挡住 %s）：%s"
                         % (h, "、".join(str(w) for w in waiters), reason))
        section += "\n" + (
            "> **%d 个根 holder 的 kill 语句未能生成**（pid/sessionid "
            "缺失或不是合法数字，为避免编造数据已跳过，请人工核对该会话）：\n"
            % len(kill_failures) + "\n".join(lines))

    data_gap = _dedup_by_waiter_and_holder(buckets["data_gap"])
    if data_gap:
        section += "\n" + (
            "> **%d 对阻塞关系因阻塞链数据缺失，未能确认根 holder，"
            "未生成对应 kill 语句**（`lockwait.pairs` 与 `lockwait.chain` "
            "是两条独立查询，覆盖面可能不一致；不能因为 chain 没提到就"
            "当作「不是根」处理，也不能猜它是根去生成语句）：\n"
            % len(data_gap)
            + "\n".join(
                "> - 会话 %s ← %s（%s %s）"
                % (p.get("waiter_sessionid"), p.get("holder_sessionid"),
                   p.get("locktype"), p.get("lock_object") or "")
                for p in data_gap))

    intermediate = _dedup_by_waiter_and_holder(buckets["intermediate"])
    if intermediate:
        section += "\n" + (
            "> %d 对是阻塞链的中间节点（真正的根另有确认，见上文/下方"
            "对应根的语句或说明；杀中间节点不解堵，因此不单独生成"
            "语句）：\n" % len(intermediate)
            + "\n".join(
                "> - 会话 %s ← %s（根是会话 %s）"
                % (p.get("waiter_sessionid"), p.get("holder_sessionid"),
                   rep.roots[as_int(p.get("waiter_sessionid"))])
                for p in intermediate))

    orphan_root = _dedup_by_waiter_and_holder(buckets["orphan_root"])
    if orphan_root:
        section += "\n" + (
            "> **%d 对阻塞关系的根会话已被阻塞链数据确认，但该根未出现在"
            "「阻塞明细」的持有者列里**（它可能持有的是链条上游、不在"
            "本次 pairs 明细范围内的另一把锁），没有材料生成 kill 语句，"
            "需人工进一步排查：\n" % len(orphan_root)
            + "\n".join(
                "> - 会话 %s ← %s（根是会话 %s，未出现在明细持有者列里）"
                % (p.get("waiter_sessionid"), p.get("holder_sessionid"),
                   rep.roots[as_int(p.get("waiter_sessionid"))])
                for p in orphan_root))

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
