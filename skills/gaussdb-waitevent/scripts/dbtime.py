"""DB time 分解与判定 —— 纯函数，不连库。

## 为什么这里没有两层树

设计文档（`docs/superpowers/specs/2026-08-11-...-design.md` §gaussdb-waitevent）
原打算把 DB_TIME 画成两层树：

    DB_TIME
    ├ 解析阶段  PARSE_TIME / PLAN_TIME / REWRITE_TIME
    └ 执行阶段  EXECUTION_TIME
       ├ CPU_TIME
       ├ DATA_IO_TIME
       └ NET_SEND_TIME

`tools/probe_dbtime_containment.py` 用 5 个真实快照窗口（og5 / openGauss-lite
5.0.3，快照 1508–1513）实测过这棵树成立所需的两条包含关系，结论记在那份
docstring 里，不重测：

  1. `EXECUTION_TIME+PARSE_TIME+PLAN_TIME+REWRITE_TIME<=DB_TIME` —— **5/5 窗口成立**
  2. `CPU_TIME+DATA_IO_TIME+NET_SEND_TIME<=EXECUTION_TIME` —— **5/5 窗口不成立**，
     超出 EXECUTION_TIME 15%~24%，不是舍入误差量级的失败

（这 5 个窗口不是 5 次独立采样：其中 4 个是同一段近空闲期的连续短快照
（`DATA_IO_TIME` 全部为 0），结论真正依赖的是唯一一个长/忙窗口
（margin −19.6%），另外四个只是没有出现反例、起印证作用，不构成独立证据；
完整数字见 `tools/probe_dbtime_containment.py` 的 docstring。）

两条关系一真一假时画树是最危险的做法：树暗示"总量减去已知子项等于剩余
子项"，读者会拿子项减父项去算"其余"；在不成立的那一半上，这个减法会得出
负数或没有意义的数字，而且**没有任何提示**——不会报错，不会标红，看上去
和成立的那一半一样正常。只画验证过的那一半（关系 1）也不行：视觉上的相邻
会诱导读者对另一半同等信任。所以 `breakdown()` 把十项时间模型里除 DB_TIME
外的全部 9 项**平铺**返回，各自独立算一个占 DB_TIME 的比例，`Breakdown.note`
里把这个决定和理由带上，不留给报告层去猜测或重新措辞。

（未证实的猜测，仅供参考，不是本模块的结论：探测工具认为 CPU_TIME/
DATA_IO_TIME 可能是各执行线程（含并行 worker）的累加耗时，而 EXECUTION_TIME
是会话可感知的墙钟时间，并行执行下前者对后者不是子集关系。）

## 时间模型里没有"锁"

`snap_global_instance_time` 的十项里没有锁或轻量锁耗时——那部分只能从等待
事件（`waitevent.events`，对应 `snap_global_wait_events`）补，这也是本 skill
要合并两个数据源的原因。`judge_dbtime()` 的 `waits` 参数就是这条查询的行。

## 三个陷阱

1. **NULL 与 0 是两回事，两个方向都要防。** 查询结果全是字符串，NULL 渲染成
   **空串**、不是 `None`（`common/grmp/values.py` 的 `is_null` 文档已注明这一点）。
   `delta_us` 缺行或取到空串都不能被 `as_int` 的默认值悄悄吃成 0——那会把
   "取数出了问题"伪装成"这一项耗时恰好为零"。`DB_TIME` 为 0（或缺失）也不能
   拿来当分母，但这属于"真的是 0"，处理方式是不除，返回空 `items`，不是报错。

2. **负的增量意味着窗口跨了一次实例重启**，计数器被清零重来。这个窗口的
   算术结果**不可用**，不是零成本——`restarted=True` 时 `breakdown()` 返回空
   `items`，`judge_dbtime()` 只产出 `DBTIME_RESTART` 一条，其余判定一律跳过：
   跨重启算出来的比例是假的，报出去比不报更糟，因为它看起来和正常结果一样。

3. **两个数据源对重启的表现不一样。** `waitevent.instance_time` 的增量能算出
   负数，重启一眼可见；但 `waitevent.events` 的注册脚本继承自 `wdr.waits` 的
   `HAVING SUM(e.wt-b.wt) > 0`，重启窗口里这个谓词会把负增量的行悄悄过滤掉——
   于是等待事件那一侧只是"行变少了"，看不出重启。`judge_dbtime()` 因此只信
   `bd.restarted`（来自 instance_time 一侧），一旦为真就完全不读 `waits`
   参数；不能反过来拿"waits 是不是空"去猜有没有重启——空 waits 在**没有重启**
   的窗口里是合法结果（真的没有锁/轻量锁等待），含义完全不同。
"""
from __future__ import annotations

from dataclasses import dataclass

from common.finding import Finding, Severity
from common.grmp.values import as_int, is_null

DIM_DBTIME = "DB Time"

# 十项时间模型。DB_TIME 是分母，不出现在 items 里；其余 9 项顺序取自
# waitevent.instance_time 注册脚本（也是 tools/probe_dbtime_containment.py
# 的 TIME_MODEL_ITEMS 顺序，两处对齐，免得"查了几项"和"校验了几项"对不上）。
_DB_TIME = "DB_TIME"
_ITEM_ORDER = (
    "EXECUTION_TIME", "CPU_TIME", "DATA_IO_TIME", "NET_SEND_TIME",
    "PARSE_TIME", "PLAN_TIME", "REWRITE_TIME",
    "PL_EXECUTION_TIME", "PL_COMPILATION_TIME",
)
_ALL_ITEMS = (_DB_TIME,) + _ITEM_ORDER

_FLATTEN_NOTE = (
    "以下各项都是各自占 DB_TIME 的独立占比，不是一棵包含树。实测（见 "
    "tools/probe_dbtime_containment.py，5 个真实快照窗口）：EXECUTION_TIME+"
    "PARSE_TIME+PLAN_TIME+REWRITE_TIME<=DB_TIME 在全部窗口成立，但 CPU_TIME+"
    "DATA_IO_TIME+NET_SEND_TIME<=EXECUTION_TIME 在全部窗口都不成立（超出 "
    "EXECUTION_TIME 15%~24%，不是舍入误差）。两条关系一真一假时画树最危险："
    "读者会拿子项减父项去算'其余'，在不成立的那一半上得到负数或无意义的结果，"
    "且不会有任何报错提示。因此哪怕只画验证过的那一半也不画，全部平铺；各项"
    "之间可能重叠，不能相加、不能相减估算'剩余部分'。（未证实的猜测：并行 "
    "worker 的 CPU_TIME/DATA_IO_TIME 累计可能不是会话墙钟 EXECUTION_TIME 的"
    "子集，两者统计口径不同。）"
)

_RESTART_NOTE = (
    "本窗口跨了一次实例重启：十项时间模型里至少一项后减前的增量算出负数，"
    "说明计数器在窗口内被清零重来。这个窗口的算术结果不可用，不是零成本，"
    "下面不返回任何占比——报出一份看似正常的比例比什么都不报更危险。"
)

_ZERO_NOTE = "本窗口 DB_TIME=0，没有可分解的 DB time，不返回任何占比。"


@dataclass(frozen=True)
class Breakdown:
    """一个窗口内 DB time 的平铺分解。

    items 里的 float 是「占 DB_TIME 的比例」，取值 0.0~1.0（不是百分数）——
    judge_dbtime() 和渲染层都按小数做阈值比较/换算，别在这里先乘 100。

    items 里各项彼此可能重叠（EXECUTION_TIME 与 CPU_TIME/DATA_IO_TIME/
    NET_SEND_TIME 之间的包含关系没有实测支撑，见 note），所以本类型不提供
    "求和"或"求剩余"的方法——那种方法只会诱使调用方做一次没有意义的算术。

    restarted=True 或 db_time_us<=0 时 items 为空列表；note 说明是哪种情况
    （跨重启不可用 / DB_TIME 为 0 无可分解量 / 正常窗口的平铺理由），报告层
    直接照抄，不必重新判断该说哪句话。
    """
    db_time_us: int
    items: list  # list[tuple[str, int, float]] —— (stat_name, delta_us, share)
    restarted: bool
    note: str


def breakdown(rows: list) -> Breakdown:
    """把 waitevent.instance_time 的行（列 stat_name / delta_us，值全是字符串）
    转成一个窗口的平铺分解。

    - delta_us 为 NULL（协议里是空串，不是 None）或整项缺行 → 抛 ValueError，
      不当 0 处理（陷阱 1）。
    - 任一项 delta_us < 0 → restarted=True，items=[]（陷阱 2）。
    - DB_TIME 为 0 → items=[]，不除零，不抛异常（真的是 0，不是"缺值"）。
    """
    parsed = {}
    for row in rows:
        name = row.get("stat_name")
        if name not in _ALL_ITEMS:
            continue  # 视图里可能出现本模块不认识的新项，忽略而不是报错
        raw = row.get("delta_us")
        if is_null(raw):
            raise ValueError(
                "%s 的 delta_us 是空值——累计计数器的增量不该有空值，"
                "这一项不能当 0 处理" % name)
        parsed[name] = as_int(raw)

    missing = [name for name in _ALL_ITEMS if name not in parsed]
    if missing:
        raise ValueError(
            "DB time 分解缺少 %s——waitevent.instance_time 应该十项齐全"
            % missing)

    restarted = any(parsed[name] < 0 for name in _ALL_ITEMS)
    db_time_us = parsed[_DB_TIME]

    if restarted:
        return Breakdown(db_time_us=db_time_us, items=[], restarted=True,
                          note=_RESTART_NOTE)
    if db_time_us == 0:
        return Breakdown(db_time_us=0, items=[], restarted=False,
                          note=_ZERO_NOTE)

    items = [(name, parsed[name], parsed[name] / db_time_us)
             for name in _ITEM_ORDER]
    return Breakdown(db_time_us=db_time_us, items=items, restarted=False,
                      note=_FLATTEN_NOTE)


# 判定阈值——见 task-12-brief.md 的判定规则表，均为比例（0.0~1.0），不是百分数。
_IO_HEAVY_RATIO = 0.30
_CPU_HEAVY_RATIO = 0.70
_NET_HEAVY_RATIO = 0.30
_LOCK_HEAVY_RATIO = 0.10
_LWLOCK_HEAVY_RATIO = 0.10

_LOCK_CLASS = "LOCK_EVENT"
_LWLOCK_CLASS = "LWLOCK_EVENT"


def _pct(ratio: float) -> str:
    return "%.1f%%" % (ratio * 100)


def _sum_wait_us(waits: list, wait_class: str) -> int:
    """按 wait_class 精确匹配求 wait_us 之和。忽略不匹配的行——它们属于
    IO_EVENT/STATUS/DMS_EVENT 等本判定表没有定义阈值的类别，不是数据缺失。

    命中的行如果 wait_us 是空值，同样按陷阱 1 处理：报错而不是当 0。
    """
    total = 0
    want = wait_class.strip().upper()
    for row in waits:
        cls = (row.get("wait_class") or "").strip().upper()
        if cls != want:
            continue
        raw = row.get("wait_us")
        if is_null(raw):
            raise ValueError(
                "wait_class=%s 的 wait_us 是空值——waitevent.events 这一行"
                "取数有问题，不能当 0 处理" % cls)
        total += as_int(raw)
    return total


def judge_dbtime(bd: Breakdown, waits: list) -> list:
    """DB time 分解的阈值判定，返回 list[Finding]（`dimension="DB Time"`）。

    restarted=True 时**只产出 DBTIME_RESTART**，完全不读 waits 参数——跨重启
    窗口的占比是拿被清零重来的计数器算出来的，看着正常但是假的，比不报还
    危险。这也是应对陷阱 3 的地方：waitevent.events 的 HAVING SUM(e.wt-b.wt)>0
    在重启窗口里会把负增量行悄悄滤掉，所以 waits 在重启窗口里只是"行变少
    了"，不会像 instance_time 那样出现负数——单看 waits 分不出"这个窗口没有
    等待"和"等待事件被重启悄悄滤掉了"。所以重启判定只信 bd.restarted，不
    反过来看 waits 是否为空。
    """
    if bd.restarted:
        return [Finding(
            DIM_DBTIME, "DBTIME_RESTART", Severity.NOTICE,
            "窗口可用性", "跨实例重启", "delta_us 应 >= 0",
            "waitevent.instance_time 十项时间模型里至少一项后减前的增量为负，"
            "说明窗口内发生过一次实例重启，计数器清零重来；本窗口占比数据"
            "不可用，只报这一条，其余判定不做（waitevent.events 的 HAVING "
            "谓词会在此类窗口里把负增量行悄悄滤掉，wait 明细一并跳过，不当"
            "成'没有等待'）")]

    if bd.db_time_us <= 0:
        return []  # 没有可分解的 DB time，无从判定，不是"一切正常"

    findings = []
    shares = {name: share for name, _, share in bd.items}

    io_ratio = shares.get("DATA_IO_TIME", 0.0)
    if io_ratio >= _IO_HEAVY_RATIO:
        findings.append(Finding(
            DIM_DBTIME, "DBTIME_IO_HEAVY", Severity.WARN,
            "DATA_IO_TIME 占 DB_TIME", _pct(io_ratio),
            ">=" + _pct(_IO_HEAVY_RATIO),
            "snapshot.snap_global_instance_time DATA_IO_TIME/DB_TIME"))

    cpu_ratio = shares.get("CPU_TIME", 0.0)
    if cpu_ratio >= _CPU_HEAVY_RATIO:
        findings.append(Finding(
            DIM_DBTIME, "DBTIME_CPU_HEAVY", Severity.NOTICE,
            "CPU_TIME 占 DB_TIME", _pct(cpu_ratio),
            ">=" + _pct(_CPU_HEAVY_RATIO),
            "snapshot.snap_global_instance_time CPU_TIME/DB_TIME"))

    net_ratio = shares.get("NET_SEND_TIME", 0.0)
    if net_ratio >= _NET_HEAVY_RATIO:
        findings.append(Finding(
            DIM_DBTIME, "DBTIME_NET_HEAVY", Severity.WARN,
            "NET_SEND_TIME 占 DB_TIME", _pct(net_ratio),
            ">=" + _pct(_NET_HEAVY_RATIO),
            "snapshot.snap_global_instance_time NET_SEND_TIME/DB_TIME"))

    lock_ratio = _sum_wait_us(waits, _LOCK_CLASS) / bd.db_time_us
    if lock_ratio >= _LOCK_HEAVY_RATIO:
        findings.append(Finding(
            DIM_DBTIME, "WAIT_LOCK_HEAVY", Severity.WARN,
            "LOCK_EVENT 耗时占 DB_TIME", _pct(lock_ratio),
            ">=" + _pct(_LOCK_HEAVY_RATIO),
            "snapshot.snap_global_wait_events wait_class=LOCK_EVENT 耗时之和/DB_TIME"))

    lwlock_ratio = _sum_wait_us(waits, _LWLOCK_CLASS) / bd.db_time_us
    if lwlock_ratio >= _LWLOCK_HEAVY_RATIO:
        findings.append(Finding(
            DIM_DBTIME, "WAIT_LWLOCK_HEAVY", Severity.WARN,
            "LWLOCK_EVENT 耗时占 DB_TIME", _pct(lwlock_ratio),
            ">=" + _pct(_LWLOCK_HEAVY_RATIO),
            "snapshot.snap_global_wait_events wait_class=LWLOCK_EVENT 耗时之和/DB_TIME"))

    return findings
