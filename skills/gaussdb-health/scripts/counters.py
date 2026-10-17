"""累计计数器的「距上次」增量(健康检查用)。

**要解决的问题:** 死锁数、回滚数、缓存命中这些都是**自上次统计重置以来**的累计值。
一旦发生过一次死锁,`deadlocks` 就永远 > 0,健康检查从此每次都报「死锁累计数 24」——
报了三个月,而其中可能两个月零新增。看报告的人分不出「正在发生」和「历史上发生过」,
久了就把这条当背景噪音忽略掉,真的新增死锁时也看不见。

**做法:** 每次巡检把这些计数器存成基线,下次巡检与基线相减,给出「距上次新增」。
累计值照样显示(它回答「历史上有没有」),但判定以增量为准(它回答「现在有没有」)。

**与 topsql 的增量是同一套思路,但数据形状不同** —— 这里是几个标量计数器,
那边是每条 SQL 一组计数器,所以没有合并成一个模块:合并之后两边的取值、重置判定、
呈现都要加分支,反而更难读。两处的共同教训都写在各自的 docstring 里。

**计数器变小 = 统计被重置过**(`pg_stat_reset()` / 实例重启),这时增量无意义,
必须明说,不能把负数当 0 —— 那会变成一句「距上次没有新增」,而那是错的。
"""
from __future__ import annotations

import json
import pathlib
from typing import Dict, NamedTuple, Optional, Tuple

BASELINE_NAME = "_counters.json"

# 要跟踪的累计计数器。键名就是报告里的说法,顺序即呈现顺序。
TRACKED = ("deadlocks", "xact_commit", "xact_rollback")


class Delta(NamedTuple):
    """一个计数器的「现在」与「距上次新增」。

    delta 为 None = 没有可比的基线(第一次巡检),或统计被重置过 —— 两种都不能当 0。
    """
    name: str
    now: int
    delta: Optional[int]


class Deltas(NamedTuple):
    items: Dict[str, Delta]
    since: str          # 基线的时刻(空 = 没有基线)
    reset: bool         # 计数器变小 → 统计被重置过,增量不可用
    note: str           # 给报告照抄的一句话


def _path(base_dir: Optional[pathlib.Path], instance_key: str) -> Optional[pathlib.Path]:
    if base_dir is None or not instance_key:
        return None
    return base_dir / "health" / instance_key / BASELINE_NAME


def baseline_path(base_dir, instance_key: str):
    """基线放在报告存档目录里:`<dir>/health/<实例键>/_counters.json`。

    没有存档目录(非容器环境)就没有基线 —— 返回 None,调用方如实说明「给不出增量」,
    不能悄悄退回「累计值当增量」。
    """
    return _path(base_dir, instance_key)


def load(path: Optional[pathlib.Path]) -> Tuple[Dict[str, int], str, str]:
    """读基线。返回 (计数器, 基线时刻, 读不到的原因)。"""
    if path is None:
        return {}, "", "没有报告存档目录,无法保存/读取基线"
    if not path.exists():
        return {}, "", "这是第一次对这个实例巡检,本次留下基线,下次就能给出「距上次新增」"
    try:
        d = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return {}, "", "基线读不出来(%s),本次重新留一份" % exc
    vals = d.get("counters")
    if not isinstance(vals, dict):
        return {}, "", "基线里没有计数器,本次重新留一份"
    out: Dict[str, int] = {}
    for k, v in vals.items():
        try:
            out[str(k)] = int(v)
        except (TypeError, ValueError):
            continue
    if not out:
        return {}, "", "基线里的计数器都解析不了,本次重新留一份"
    return out, str(d.get("at") or ""), ""


def save(path: Optional[pathlib.Path], at: str, counters: Dict[str, int]) -> bool:
    """留下基线。失败返回 False —— 存不下基线不该让这次巡检失败。"""
    if path is None:
        return False
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_text(json.dumps({"at": at, "counters": counters}, ensure_ascii=False),
                       encoding="utf-8")
        tmp.replace(path)
        return True
    except (OSError, TypeError, ValueError):
        return False


def compute(now: Dict[str, int], before: Dict[str, int], since: str) -> Deltas:
    """现在的计数器 vs 基线 → 每项的增量。"""
    if not before:
        return Deltas(items={k: Delta(k, int(now.get(k, 0)), None) for k in TRACKED},
                      since="", reset=False,
                      note="没有基线,这次只能给累计值;本次已留基线,下次可给出「距上次新增」")
    # 任一项变小 → 统计被重置过。只看两边都有的项。
    shrunk = [k for k in TRACKED
              if k in before and int(now.get(k, 0)) < int(before[k])]
    if shrunk:
        return Deltas(items={k: Delta(k, int(now.get(k, 0)), None) for k in TRACKED},
                      since=since, reset=True,
                      note=("计数器比上次小了(%s)—— 统计被重置过(pg_stat_reset 或实例重启),"
                            "这次给不出「距上次新增」,**不是「没有新增」**" % "、".join(shrunk)))
    items = {}
    for k in TRACKED:
        cur = int(now.get(k, 0))
        items[k] = Delta(k, cur, cur - int(before[k]) if k in before else None)
    return Deltas(items=items, since=since, reset=False,
                  note="「距上次新增」是与上一次巡检(%s)相比的增量" % (since or "未知时刻"))


def rollback_pct(d: Deltas, window: str = "delta") -> Optional[float]:
    """回滚率。window='delta' 用增量算(这段时间的回滚率),'total' 用累计值算。

    **增量回滚率才反映现在。** 累计回滚率被历史摊平:库跑了一个月、刚才十分钟疯狂回滚,
    累计回滚率可能还是 0.1%,而这十分钟其实是 90%。
    """
    c, r = d.items.get("xact_commit"), d.items.get("xact_rollback")
    if c is None or r is None:
        return None
    if window == "delta":
        if c.delta is None or r.delta is None:
            return None
        total = c.delta + r.delta
        return 100.0 * r.delta / total if total > 0 else None
    total = c.now + r.now
    return 100.0 * r.now / total if total > 0 else None
