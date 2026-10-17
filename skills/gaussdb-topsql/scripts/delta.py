"""窗口增量:两次采样相减,回答「最近这段时间谁在冲高」。

**为什么需要它。** `dbe_perf.statement` 是**自上次清零以来的累计值**。库跑了一个月、
某条 SQL 在最近十分钟突然冲高,它那十分钟的耗时被一个月的总量摊平,在累计榜上根本看不见。
用户问的恰恰是「最近十分钟怎么了」,而累计榜回答的是「这一个月总体谁最重」—— 两个问题
不一样,拿后者当前者答,会让一次真实的冲高被完全漏掉。

**两种取法,对应两个真实问题:**
  · `--window 10m`  现场采两次,中间等十分钟 → 精确的「最近十分钟」。代价是命令要跑十分钟。
  · `--since-last`  与上一次运行留下的基线相减 → 「距上次巡检」。几乎零代价,适合日常巡检。
    基线由每次运行自动留下(与报告存档同一个目录),所以越跑越有用。

**三件必须说清、不能静默处理的事:**
  ① 窗口里**新出现**的语句,基线里没有它 —— 全部耗时都算进窗口(不是丢掉)。
     这正是 gaussdb-wdr 那个内连接 bug 的教训:拿两端取交集,新上来的语句会被整批丢掉,
     而故障期间冲高的恰恰是新上来的那批。
  ② 累计值**变小**说明统计被清零过(`reset_unique_sql`)或实例重启过。这时相减得到的是
     负数,毫无意义 —— 必须明说「这个窗口的增量不可用」,不能把负数或 0 当结论。
  ③ 采样是 `ORDER BY 累计值 LIMIT n` 取回来的**前 n 条**,不是全量。一条冲高的语句,它的
     累计值里已经含了这次冲高,所以通常在前 n 条里;但理论上存在「累计很小、刚冲高一点」
     而落在 n 之外的情况。采样宽度因此要明显大于最终呈现条数(见 SAMPLE_WIDTH),
     并在报告里写明这是「从前 n 条里算的增量」。
"""
from __future__ import annotations

import json
import pathlib
import re
from typing import Dict, List, NamedTuple, Optional, Tuple

# 采样宽度:算增量时先取这么多条累计榜,再在它们之间算增量、重新排序。
# 比最终呈现的条数宽得多 —— 累计榜靠后的语句完全可能是本窗口的冲高元凶。
SAMPLE_WIDTH = 500

# 窗口长度的上下限。下限 10 秒:再短的话采样本身的抖动比信号还大;
# 上限 6 小时:再长就该用 WDR 快照,不该让一条命令挂在那里等。
MIN_WINDOW_S = 10
MAX_WINDOW_S = 6 * 3600

_WINDOW_RE = re.compile(r"^\s*(\d+)\s*([smh])\s*$", re.I)
_UNIT_S = {"s": 1, "m": 60, "h": 3600}

BASELINE_NAME = "_baseline.json"

# 增量模式下能排序的维度。`reads` 不在列:取数脚本的 SELECT 里没有块数列,
# 没有原始值就算不出增量 —— 宁可明确拒绝,也不能悄悄按别的维度排。
DELTA_SORT_KEYS = ("time", "avg", "calls", "rows")


class Counters(NamedTuple):
    """一条语句在某一刻的累计值。"""
    calls: int
    total_sec: float
    rows: int
    query: str = ""


class Sample(NamedTuple):
    """某一刻的一批累计值。at 是数据库时间的字符串,只用于呈现,不参与计算。"""
    at: str
    rows: Dict[str, Counters]


class DeltaRow(NamedTuple):
    sql_id: str
    query: str
    calls: int          # 窗口内调用次数
    total_sec: float    # 窗口内总耗时
    avg_ms: float       # 窗口内平均耗时
    rows: int           # 窗口内返回行数
    is_new: bool        # 窗口里才出现的语句(基线里没有)


class DeltaResult(NamedTuple):
    rows: List[DeltaRow]
    span_s: float       # 两次采样相隔多久
    reset: bool         # 统计被清零 / 实例重启 → rows 无意义
    note: str           # 给报告照抄的一句话


def parse_window(text: str) -> int:
    """`10m` / `600s` / `1h` → 秒。不合法就抛 ValueError,不猜。"""
    m = _WINDOW_RE.match(text or "")
    if not m:
        raise ValueError("窗口要写成 <数字><s|m|h>,例如 10m、600s、1h,给的是 %r" % (text,))
    n, unit = int(m.group(1)), m.group(2).lower()
    secs = n * _UNIT_S[unit]
    if secs < MIN_WINDOW_S or secs > MAX_WINDOW_S:
        raise ValueError("窗口要在 %d 秒 ~ %d 小时之间,给的是 %s"
                         % (MIN_WINDOW_S, MAX_WINDOW_S // 3600, text))
    return secs


def to_sample(at: str, rows) -> Sample:
    """把取数结果转成一刻的采样。rows 里每项要有 sql_id / calls / total_sec / rows / query。"""
    out: Dict[str, Counters] = {}
    for r in rows:
        sid = str(getattr(r, "sql_id", "") or "")
        if not sid:
            continue
        out[sid] = Counters(calls=int(getattr(r, "calls", 0) or 0),
                            total_sec=float(getattr(r, "total_sec", 0.0) or 0.0),
                            rows=int(getattr(r, "rows", 0) or 0),
                            query=str(getattr(r, "query", "") or ""))
    return Sample(at=at, rows=out)


def _reset_detected(before: Sample, after: Sample) -> bool:
    """两端都在的语句里,只要有一条累计值变小,就说明统计被清零过。

    判「有没有清零」只看两端都在的语句 —— 只在 after 里出现的语句本来就没有可比的前值,
    拿它判断会把「新语句」误当成「清零」。
    """
    for sid, a in after.rows.items():
        b = before.rows.get(sid)
        if b is None:
            continue
        if a.calls < b.calls or a.total_sec < b.total_sec - 1e-6:
            return True
    return False


def diff(before: Sample, after: Sample, span_s: float, by: str = "time",
         limit: int = 10) -> DeltaResult:
    """两次采样相减,按 by 排序取前 limit 条。"""
    if by not in DELTA_SORT_KEYS:
        raise ValueError("增量模式不支持按 %s 排序(取数脚本里没有这一列的原始值);"
                         "可用:%s" % (by, "、".join(DELTA_SORT_KEYS)))
    if _reset_detected(before, after):
        return DeltaResult(rows=[], span_s=span_s, reset=True,
                           note=("两次采样之间累计值变小了 —— 统计被清零(reset_unique_sql)"
                                 "或实例重启过。这个窗口算不出增量,**不是「没有变化」**;"
                                 "请重新采一次,或改用 WDR 快照窗口。"))
    out: List[DeltaRow] = []
    for sid, a in after.rows.items():
        b = before.rows.get(sid)
        # 基线里没有 = 窗口内新出现的语句,全部计入(不是丢掉 —— 见模块 docstring ①)
        d_calls = a.calls - (b.calls if b else 0)
        d_total = a.total_sec - (b.total_sec if b else 0.0)
        d_rows = a.rows - (b.rows if b else 0)
        if d_calls <= 0 and d_total <= 1e-6:
            continue                      # 这个窗口里没动过
        avg_ms = (d_total * 1000.0 / d_calls) if d_calls > 0 else 0.0
        out.append(DeltaRow(sql_id=sid, query=a.query or (b.query if b else ""),
                            calls=max(0, d_calls), total_sec=max(0.0, d_total),
                            avg_ms=avg_ms, rows=max(0, d_rows), is_new=b is None))
    key = {"time": lambda r: -r.total_sec,
           "avg": lambda r: -r.avg_ms,
           "calls": lambda r: -r.calls,
           "rows": lambda r: -r.rows}[by]
    out.sort(key=key)
    note = ("窗口增量:%s 秒内的变化量(不是累计值)。窗口内新出现的语句整条计入;"
            "增量从累计榜前 %d 条里算,极少数「累计很小、刚冲高」的语句可能不在其中。"
            % (int(span_s), SAMPLE_WIDTH))
    return DeltaResult(rows=out[:max(1, int(limit))], span_s=span_s, reset=False, note=note)


# ---------------------------------------------------------------- 基线存取

def baseline_path(base_dir: Optional[pathlib.Path], instance_key: str) -> Optional[pathlib.Path]:
    """基线放在报告存档目录里:`<dir>/topsql/<实例键>/_baseline.json`。

    没设存档目录(非容器环境)就没有基线可言 —— 返回 None,`--since-last` 会如实报错,
    而不是悄悄退回累计模式。
    """
    if base_dir is None or not instance_key:
        return None
    return base_dir / "topsql" / instance_key / BASELINE_NAME


def save_baseline(path: Optional[pathlib.Path], sample: Sample) -> bool:
    """留下基线供下次相减。失败只返回 False —— 存不下基线不该让这次的报告失败。"""
    if path is None:
        return False
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"at": sample.at,
                   "rows": {sid: {"calls": c.calls, "total_sec": c.total_sec,
                                  "rows": c.rows, "query": c.query}
                            for sid, c in sample.rows.items()}}
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        tmp.replace(path)
        return True
    except (OSError, TypeError, ValueError):
        return False


def load_baseline(path: Optional[pathlib.Path]) -> Tuple[Optional[Sample], str]:
    """读基线。返回 (采样, 读不到的原因)。原因非空时采样为 None。"""
    if path is None:
        return None, "没有报告存档目录,无法保存/读取基线(--since-last 只在容器环境可用)"
    if not path.exists():
        return None, "还没有基线 —— 这是第一次对这个实例采样,本次会留下基线,下次就能算增量"
    try:
        d = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return None, "基线文件读不出来(%s),本次重新留一份" % exc
    rows = d.get("rows")
    if not isinstance(rows, dict) or not rows:
        return None, "基线文件里没有数据,本次重新留一份"
    out: Dict[str, Counters] = {}
    for sid, v in rows.items():
        if not isinstance(v, dict):
            continue
        try:
            out[str(sid)] = Counters(calls=int(v.get("calls") or 0),
                                     total_sec=float(v.get("total_sec") or 0.0),
                                     rows=int(v.get("rows") or 0),
                                     query=str(v.get("query") or ""))
        except (TypeError, ValueError):
            continue
    if not out:
        return None, "基线文件里的数据都解析不了,本次重新留一份"
    return Sample(at=str(d.get("at") or ""), rows=out), ""
