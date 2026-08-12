#!/usr/bin/env python3
"""vacuum — 死元组 / autovacuum 健康度评估：哪些表堆积了太多死元组、
autovacuum 有没有追上、手工 VACUUM 值不值得跑。

只评估，不执行任何 VACUUM/ANALYZE。所有触发线与规则判定都在 `rules.py`/
`thresholds.py`（纯函数，不连库）；本文件只管取数、拼装报告。

Usage:
    vacuum.py -c <conn> [--limit 20] [--format json] [--timeout N]

**R4（xmin 阻塞）的报告口径，是本文件存在的一个专门理由：**
`rules.evaluate()` 里 R4 是逐表规则，命中门槛之一是「这张表本身有没有
死元组」（`n_dead_tup > 0`）——这个门槛对逐表规则是对的：一个 instance
级别的事实（有更老事务/复制槽挡着回收）如果在每一行死元组表上都重复一遍，
就是噪音。

但这留下一个真实的缺口：**如果没有任何一张表恰好命中 R4——包括「当前
每张表死元组都是 0」这种情况——报告就会对这个阻塞源只字不提。** 而这个
阻塞源恰恰是最早的先行信号：只要它还在，死元组只会继续堆积，跑多少次
VACUUM 都回收不掉。

所以本文件的 xmin 小节（`_xmin_blockers_section`）**只看
`rep.oldest_xmin` 是否非空**，完全不看任何一张表的 `hits` 里有没有 R4——
`collect()` 直接把 `vacuum.oldest_xmin` 的原始查询结果整份挂在
`VacuumReport.oldest_xmin` 上，渲染时独立于逐表判定读取。见
tests/test_vacuum_entry_units.py 里
`test_xmin_blocker_is_reported_even_when_no_table_hits_r4` 与
`test_xmin_blocker_is_reported_even_with_zero_raw_dead_tuple_rows`。

复制槽（replication_slot）来源的 `xmin_age_s` 在这个内核上恒为空——
`pg_replication_slots` 没有任何时间戳列，不是取数出错。年龄显示成
「未知」，绝不能被 `as_float()` 的默认值悄悄折成 0，读成「刚连上，
问题不大」。

**报告绝不给空间回收量的预估**（「可回收 X GB」「预计释放」之类）——
清理后能回收多少空间，只有真的跑一次才知道；猜测值摆在一堆真实测量值
旁边会被当成承诺来读。见 test_report_never_estimates_reclaimable_space。
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
from common.finding import findings_to_json  # noqa: E402
# 结果值全是字符串：bool("f") 是 True、NULL 渲染成空串而不是 None。
# 类型/空值还原一律走这里，不用裸 int()/float()/`is None`/`or 默认值`。
from common.grmp.values import as_float, is_null  # noqa: E402
import render  # noqa: E402
import rules  # noqa: E402
from rules import default_thresholds  # noqa: E402

SKILL = "gaussdb-vacuum"
DIM = "Dead Tuples"

DEAD_SCRIPT = "vacuum.dead_tuples"
SETTINGS_SCRIPT = "vacuum.autovac_settings"
WORKERS_SCRIPT = "vacuum.autovac_workers"
XMIN_SCRIPT = "vacuum.oldest_xmin"

MB = 1024 * 1024


@dataclass(frozen=True)
class VacuumReport:
    tables: list = field(default_factory=list)      # 每项含原行 + trigger_line + hits
    settings: dict = field(default_factory=dict)
    workers: list = field(default_factory=list)
    oldest_xmin: list = field(default_factory=list)
    findings: list = field(default_factory=list)


def collect(runner, limit: int, th) -> VacuumReport:
    settings = {r["name"]: r["setting"]
                for r in runner.run(SETTINGS_SCRIPT, {})}
    raw = runner.run(DEAD_SCRIPT, {"limit": int(limit)})
    workers = runner.run(WORKERS_SCRIPT, {})
    xmin = runner.run(XMIN_SCRIPT, {})
    tables = []
    for row in raw:
        line = rules.trigger_line(as_float(row["reltuples"]), settings,
                                  row.get("reloptions") or "")
        tables.append(dict(row, trigger_line=line,
                           hits=rules.evaluate(row, settings, xmin, th)))
    return VacuumReport(tables=tables, settings=settings, workers=workers,
                        oldest_xmin=xmin,
                        findings=rules.judge_tables(raw, settings, xmin, th))


# ---------------------------------------------------------------------------
# 渲染：三段固定结构——风险表 → autovacuum 近期运行情况（含 GUC / worker /
# xmin 阻塞源）→ 手工清理评估。
# ---------------------------------------------------------------------------

_RULE_MEANINGS = {
    "R1": "autovacuum 没追上：死元组已过触发线，且这张表从未被 autovacuum "
          "服务过，或距上次服务已经很久",
    "R2": "autovacuum 在这张表上被关掉了（reloptions 里 "
          "autovacuum_enabled=false），不管死元组堆多少都不会被处理",
    "R3": "死元组比例高，且这张表大到值得管",
    "R4": "有更老的事务/复制槽挡着回收，现在跑 VACUUM 也回收不掉",
}

# 逐表判定用得到的 GUC 之外，报告里还要单独摆出来的「关键 GUC」——
# 名字来自 pg_settings 实测（见 SKILL.md）。有的名字在个别内核上可能不
# 存在，缺失时明说「未取到」，不能悄悄跳过那一行（跳过会被读成没查）。
_KEY_GUCS = (
    ("autovacuum", "autovacuum"),
    ("autovacuum_naptime", "naptime"),
    ("autovacuum_max_workers", "max_workers"),
    ("autovacuum_mode", "mode"),
    ("autovacuum_vacuum_threshold", "threshold"),
    ("autovacuum_vacuum_scale_factor", "scale_factor"),
)

_XMIN_SOURCE_LABELS = {
    "long_xact": "长事务",
    "prepared_xact": "两阶段（prepared）事务",
    "replication_slot": "复制槽",
}


def _dead_ratio_pct(row: dict) -> float:
    live = as_float(row.get("n_live_tup"))
    dead = as_float(row.get("n_dead_tup"))
    denom = live + dead
    if denom <= 0:
        return 0.0
    return dead / denom * 100.0


def _last_autovac_display(row: dict) -> str:
    """last_autovacuum_age_s 为 NULL（协议里是空串）代表「从未被 autovacuum
    服务过」，为 0 代表「刚跑完」——两个相反的事实，绝不能用 `or` 类写法
    把 0 吞成「未知/从未」。"""
    age = row.get("last_autovacuum_age_s")
    if is_null(age):
        return "从未运行"
    return "%.0f 秒前" % as_float(age)


def _risk_table_section(rep: VacuumReport) -> str:
    risk = [t for t in rep.tables if t["hits"]]
    if not risk:
        return ("## 风险表\n\n未发现死元组风险表 —— 查询正常返回，"
                "当前没有超过阈值的表。\n")
    rows = []
    for t in risk:
        name = "%s.%s" % (t.get("schema"), t.get("table"))
        live = as_float(t.get("n_live_tup"))
        dead = as_float(t.get("n_dead_tup"))
        size_mb = as_float(t.get("table_bytes")) / MB
        rows.append([
            name, "%d" % int(live), "%d" % int(dead),
            "%.1f%%" % _dead_ratio_pct(t), "%.0f MB" % size_mb,
            "%.0f" % t["trigger_line"], _last_autovac_display(t),
            "、".join(t["hits"]),
        ])
    return "## 风险表\n\n" + render.table(
        ["表", "活元组", "死元组", "死元组比例", "表大小", "触发线",
         "last_autovacuum", "命中规则"], rows)


def _guc_section(rep: VacuumReport) -> str:
    rows = []
    for key, label in _KEY_GUCS:
        if key not in rep.settings:
            rows.append([label, "未取到（本实例可能没有这个 GUC）"])
            continue
        val = rep.settings[key]
        rows.append([label, "（空值）" if is_null(val) else str(val)])
    return "### 关键 GUC\n\n" + render.table(["GUC", "当前值"], rows)


def _workers_section(rep: VacuumReport) -> str:
    if not rep.workers:
        return ("### 当前正在运行的 autovacuum worker\n\n"
                "当前没有正在运行的 autovacuum 线程。\n")
    rows = []
    for w in rep.workers:
        age = w.get("xact_age_s")
        age_display = "未知" if is_null(age) else "%.0f 秒" % as_float(age)
        rows.append([str(w.get("pid")), str(w.get("sessionid")), age_display,
                    render.truncate(w.get("query") or "", 80)])
    return "### 当前正在运行的 autovacuum worker\n\n" + render.table(
        ["pid", "sessionid", "已运行", "query"], rows)


def _xmin_age_display(row: dict) -> str:
    """复制槽这一路的 `xmin_age_s` 恒为空——不是取数失败，是
    `pg_replication_slots` 在这个内核上没有任何时间戳列。空值必须显示成
    「未知」，绝不能被 `as_float()` 的默认值悄悄折成 0、读成「刚连上，
    问题不大」。"""
    age = row.get("xmin_age_s")
    if is_null(age):
        return ("年龄未知（这一路没有时间戳列，算不出已经挡了多久，"
                "但它照样在挡回收，不能当成刚发生、无害）")
    return "已持续 %.0f 秒" % as_float(age)


def _xmin_blockers_section(rep: VacuumReport) -> str:
    """独立于任何一张表是否命中 R4——只看 `vacuum.oldest_xmin` 有没有返回
    行。见本文件头部注释与
    test_xmin_blocker_is_reported_even_when_no_table_hits_r4。"""
    if not rep.oldest_xmin:
        return ("### 回收阻塞源（长事务 / 两阶段事务 / 复制槽）\n\n"
                "当前没有发现阻塞死元组回收的长事务、两阶段事务或复制槽"
                "（`vacuum.oldest_xmin` 未返回任何行）。\n")
    lines = [
        "### 回收阻塞源（长事务 / 两阶段事务 / 复制槽）\n",
        "发现 %d 个可能阻塞死元组回收的来源——只要它们存在，`VACUUM` 就"
        "无法回收这些快照仍可能用到的死元组，与本报告风险表里有没有表"
        "命中 R4 无关：\n" % len(rep.oldest_xmin),
    ]
    for x in rep.oldest_xmin:
        source = x.get("source", "?")
        label = _XMIN_SOURCE_LABELS.get(source, source)
        identifier = x.get("identifier", "?")
        line = ("- %s（标识 %s）：%s —— `VACUUM` 无法回收它快照之前产生的死"
               "元组" % (label, identifier, _xmin_age_display(x)))
        # detail 带着 usename/state/query 之类的原始上下文——不只是给人看
        # 的补充信息：它也是唯一能证明"这一行确实是一个独立于取数会话
        # 本身的真实事务"的证据。见 tools/matrix_vacuum.py 的
        # 「gsql 不自证阻塞源」用例：不能只查 pid 是否还活着就判定，某些
        # 环境下（本实例 pid/线程号池很小、回收很快）一个已经关闭的自证
        # 连接留下的 pid 可能在检查这一刻已经被别的、完全无关的新连接
        # 复用，单看 pid 活不活会给出假阳性的"通过"。
        detail = x.get("detail")
        if not is_null(detail):
            line += "（%s）" % render.truncate(str(detail), 160)
        lines.append(line)
    lines.append("")
    return "\n".join(lines)


def _autovac_status_section(rep: VacuumReport) -> str:
    return ("\n## autovacuum 近期运行情况\n\n" + _guc_section(rep) + "\n" +
            _workers_section(rep) + "\n" + _xmin_blockers_section(rep))


def _manual_cleanup_section(rep: VacuumReport) -> str:
    """逐表列出命中了哪几条规则及各自含义。

    findings（rules.judge_tables() 的产出）不带表标识——Finding 的字段里
    没有 schema/table，多张表命中同一条规则时无法从 findings 反查是哪一张，
    所以这里改用 `rep.tables`（自带 hits）直接渲染，不经过 findings。
    """
    risk = [t for t in rep.tables if t["hits"]]
    lines = ["\n## 手工清理评估\n"]
    if not risk:
        lines.append("未发现命中任何清理规则的表 —— 当前没有需要手工介入"
                     "的表。\n")
        return "".join(lines)
    if any("R4" in t["hits"] for t in risk):
        lines.append(
            "> **命中 R4 的表：其死元组回收被更老的事务/复制槽挡住 —— "
            "先处理该事务，现在跑 VACUUM 不会有效果，再看其余规则。**\n\n")
    for t in risk:
        name = "%s.%s" % (t.get("schema"), t.get("table"))
        codes = t["hits"]
        detail = "；".join("%s：%s" % (c, _RULE_MEANINGS.get(c, c))
                           for c in codes)
        lines.append("- **%s** 命中 %s —— %s\n" % (name, "、".join(codes),
                                                    detail))
    return "".join(lines)


def render_markdown(rep: VacuumReport) -> str:
    out = ["# 死元组 / autovacuum 健康度评估\n"]
    out.append(_risk_table_section(rep))
    out.append(_autovac_status_section(rep))
    out.append(_manual_cleanup_section(rep))
    return "\n".join(out)


def main(argv: Optional[list] = None) -> int:
    ap = argparse.ArgumentParser(
        prog="vacuum.py",
        description="死元组 / autovacuum 健康度评估（只评估，不执行 VACUUM）")
    ap.add_argument("-c", "--conn", default="", help="连接名（省略则用 gaussdb-login 建立的会话）")
    ap.add_argument("--limit", type=int, default=20, help="风险表返回条数上限")
    ap.add_argument("--format", choices=["markdown", "json"], default="markdown")
    ap.add_argument("--timeout", type=int, default=None)
    args = ap.parse_args(argv)

    try:
        runner = access.for_conn(args.conn, timeout=args.timeout)
    except (common.ConfigError, common.CredentialError, access.AccessError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    try:
        rep = collect(runner, args.limit, default_thresholds())
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
