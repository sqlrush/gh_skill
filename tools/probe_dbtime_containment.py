#!/usr/bin/env python3
"""实测 `snapshot.snap_global_instance_time` 十项时间模型是否真的两层嵌套。

设计文档（`docs/superpowers/specs/2026-08-11-...-design.md` §gaussdb-waitevent）
打算把 DB time 画成两层树：

    DB_TIME
    ├ 解析阶段  PARSE_TIME / PLAN_TIME / REWRITE_TIME
    └ 执行阶段  EXECUTION_TIME
       ├ CPU_TIME
       ├ DATA_IO_TIME
       └ NET_SEND_TIME

这棵树成立的前提是两条包含关系都成立：

    (1) CPU_TIME + DATA_IO_TIME + NET_SEND_TIME <= EXECUTION_TIME
    (2) EXECUTION_TIME + PARSE_TIME + PLAN_TIME + REWRITE_TIME <= DB_TIME

本工具用最近 5 个快照窗口的实测增量（后一快照减前一快照，两者都是累计计数器）
逐一验证。**验不出来的关系不能画进树里**——分母一栏各占比例会加起来超过
100%，读者会拿一项减另一项算出一个没有意义的数字，比平铺列出更误导。

## 实测结论（2026-08-11，连接 og → openGauss-lite 5.0.3 / og5，快照 1508–1513）

```
window 1508->1509 (dur≈?): DB_TIME=132353117 EXECUTION_TIME=129658743 CPU_TIME=85426735
  DATA_IO_TIME=69548831 NET_SEND_TIME=76417 PARSE_TIME=16048 PLAN_TIME=94185
  REWRITE_TIME=4335 PL_EXECUTION_TIME=100474 PL_COMPILATION_TIME=5693
  check1 CPU+IO+NET=155051983 <= EXECUTION=129658743 ? NO  margin=-25393240
  check2 EXEC+PARSE+PLAN+REWRITE=129773311 <= DB_TIME=132353117 ? YES margin=2579806

window 1509->1510: check1 143962 <= 121680 ? NO margin=-22282   check2 154075 <= 156128 ? YES margin=2053
window 1510->1511: check1 135722 <= 118068 ? NO margin=-17654   check2 148450 <= 149092 ? YES margin=642
window 1511->1512: check1 141731 <= 114460 ? NO margin=-27271   check2 141422 <= 154162 ? YES margin=12740
window 1512->1513: check1 141296 <= 116523 ? NO margin=-24773   check2 144766 <= 152697 ? YES margin=7931
```

完整逐项数字见本文件跑出来的 stdout（记在 task-10-report.md 里），这里只摘两条校验。

**结论：**
- 关系 (2)（解析三项 + EXECUTION <= DB_TIME）**在全部 5 个窗口都成立**，margin
  始终是正数（642 ~ 2579806，绝对值因窗口时长不同差得很远，但没有一个窗口
  越界），不是贴着零的巧合。
- 关系 (1)（CPU + IO + NET <= EXECUTION）**在全部 5 个窗口都不成立**，而且不是
  误差量级的失败——CPU_TIME + DATA_IO_TIME 经常单独就逼近甚至超过 EXECUTION_TIME
  （例如窗口 1508→1509：CPU=85426735 + IO=69548831 已经是 EXECUTION=129658743 的
  约 1.2 倍）。五个窗口的 margin 绝对值差异很大（-17654 ~ -25393240，因为
  1508→1509 横跨的时长比后面四个窗口长得多），但**相对 EXECUTION_TIME 的超出
  比例稳定在 15%~24%**（-19.6% / -18.3% / -15.0% / -23.8% / -21.3%）。
  这不是"两者近似相等、四舍五入" 的边界情况，是系统性超出。合理猜测：CPU_TIME /
  DATA_IO_TIME 是各执行线程（含并行 worker）的累加耗时，而 EXECUTION_TIME 是
  会话可感知的墙钟时间；并行执行下前者对后者不是子集关系。这只是猜测，本工具
  不对原因下结论，只对"是否可以画成包含关系"下结论。

**因此：Task 12 不能画设计文档里那棵完整两层树。** 关系 (2) 那一层（解析阶段
+ 执行阶段 汇总到 DB_TIME）可以画；关系 (1) 那一层（CPU/IO/NET 汇总到
EXECUTION_TIME）不能画——应改为平铺列出 CPU_TIME / DATA_IO_TIME / NET_SEND_TIME
三项各自占 DB_TIME 的比例，并注明"这三项对 EXECUTION_TIME 的包含关系在本实例上
未能验证（5/5 窗口 CPU+IO+NET 超过 EXECUTION_TIME，例如窗口 1508→1509：
155051983 > 129658743），因此不作层级归并"。PL_EXECUTION_TIME /
PL_COMPILATION_TIME 本就与上述项重叠（设计文档已注明"与上面部分重叠"），一律
平铺展示,不纳入任何加总校验。

## 用法（mac 上）

    GSDB_HOME=~/.gdaa python3 tools/probe_dbtime_containment.py -c og

只读 SELECT，不建表、不改任何状态。若某个窗口的增量算出负数，说明那个窗口跨了
一次实例重启，计数器被清零重来——**这种窗口的算术结果没有意义，工具会把它标记
为「不可用」并说明原因，不会当成 0 处理，也不会悄悄跳过不提。**
"""
from __future__ import annotations

import argparse
import pathlib
import sys

_ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT))

from common.db import Database, DBError                # noqa: E402
from common.grmp.values import as_int, is_null          # noqa: E402

# 与 gs_instance_time / snap_global_instance_time 里实际出现的十项一致
# （brief 给定的顺序，也是设计文档两层树用到的全部输入）。
TIME_MODEL_ITEMS = [
    "DB_TIME", "EXECUTION_TIME", "CPU_TIME", "DATA_IO_TIME", "NET_SEND_TIME",
    "PARSE_TIME", "PLAN_TIME", "REWRITE_TIME",
    "PL_EXECUTION_TIME", "PL_COMPILATION_TIME",
]

DEFAULT_WINDOWS = 5


class ProbeError(RuntimeError):
    """取数或校验前提不满足，探测作废——不能拿不完整的数据去算包含关系。"""


def fetch_recent_snapshot_ids(db: Database, windows: int) -> list:
    """最近 `windows` 个窗口需要 windows+1 个快照，升序（旧→新）返回。"""
    need = windows + 1
    _, rows = db.query(
        "SELECT snapshot_id FROM snapshot.snapshot ORDER BY snapshot_id DESC LIMIT %s",
        (need,))
    ids = [as_int(r[0]) for r in rows]
    if len(ids) < need:
        raise ProbeError(
            "snapshot.snapshot 只有 %d 个快照，凑不出 %d 个窗口（需要 %d 个快照）"
            % (len(ids), windows, need))
    return sorted(ids)


def fetch_instance_time(db: Database, snapshot_ids: list) -> dict:
    """按快照 id 取十项时间模型的原始累计值：{snapshot_id: {item_name: value}}。

    显式用 is_null 挡 NULL——NULL 是异常（累计计数器不该缺值），不能让
    as_int 的默认值把它悄悄当成 0，那会把"取数出了问题"伪装成"这项耗时为零"。
    """
    placeholders = ", ".join(["%s"] * len(snapshot_ids))
    sql = (
        "SELECT snapshot_id, snap_node_name, snap_stat_name, snap_value "
        "FROM snapshot.snap_global_instance_time "
        "WHERE snapshot_id IN (%s) "
        "ORDER BY snapshot_id, snap_stat_name" % placeholders
    )
    _, rows = db.query(sql, tuple(snapshot_ids))

    nodes = set()
    values: dict = {}
    for snapshot_id, node_name, stat_name, raw_value in rows:
        nodes.add(node_name)
        if stat_name not in TIME_MODEL_ITEMS:
            continue  # 视图里出现过本工具不认识的新项，忽略而不是报错，但不参与校验
        if is_null(raw_value):
            raise ProbeError(
                "快照 %s 的 %s 取到 NULL——累计计数器不该有空值，"
                "这一格的数据不可信，不能当 0 处理" % (snapshot_id, stat_name))
        sid = as_int(snapshot_id)
        values.setdefault(sid, {})[stat_name] = as_int(raw_value)

    if len(nodes) > 1:
        raise ProbeError(
            "snap_global_instance_time 里出现了 %d 个 snap_node_name（%s）——"
            "本工具假设单节点实例，直接把它们当同一份计数器相减是错的，"
            "遇到分布式实例需要重新设计取数口径，不能默默求和" % (len(nodes), sorted(nodes)))

    for sid in snapshot_ids:
        got = values.get(sid, {})
        missing = [item for item in TIME_MODEL_ITEMS if item not in got]
        if missing:
            raise ProbeError(
                "快照 %s 缺少时间模型项 %s——十项应该每个快照都齐全" % (sid, missing))
    return values


def compute_window_delta(values: dict, snap_a: int, snap_b: int) -> dict:
    """后一快照减前一快照。计数器是累计量，不裁剪负数——负数是重启信号，得报出来。"""
    a, b = values[snap_a], values[snap_b]
    return {item: b[item] - a[item] for item in TIME_MODEL_ITEMS}


def print_window(snap_a: int, snap_b: int, delta: dict) -> dict:
    """打印一个窗口的十项增量与两条校验，返回本窗口的校验结果供汇总用。"""
    print("\n=== 窗口 快照%d -> 快照%d ===" % (snap_a, snap_b))

    negative = [item for item in TIME_MODEL_ITEMS if delta[item] < 0]
    if negative:
        print("  !!! 出现负增量 %s ——这个窗口跨了一次实例重启，计数器被清零重来。"
              % negative)
        print("  !!! 本窗口的算术结果不可用，不参与下面的汇总结论（不是 0，是作废）。")

    for item in TIME_MODEL_ITEMS:
        print("  %-20s = %d" % (item, delta[item]))

    sum1 = delta["CPU_TIME"] + delta["DATA_IO_TIME"] + delta["NET_SEND_TIME"]
    ok1 = sum1 <= delta["EXECUTION_TIME"]
    margin1 = delta["EXECUTION_TIME"] - sum1
    print("  校验1  CPU_TIME+DATA_IO_TIME+NET_SEND_TIME=%d <= EXECUTION_TIME=%d ? %s (margin=%d)"
          % (sum1, delta["EXECUTION_TIME"], "YES" if ok1 else "NO", margin1))

    sum2 = delta["EXECUTION_TIME"] + delta["PARSE_TIME"] + delta["PLAN_TIME"] + delta["REWRITE_TIME"]
    ok2 = sum2 <= delta["DB_TIME"]
    margin2 = delta["DB_TIME"] - sum2
    print("  校验2  EXECUTION_TIME+PARSE_TIME+PLAN_TIME+REWRITE_TIME=%d <= DB_TIME=%d ? %s (margin=%d)"
          % (sum2, delta["DB_TIME"], "YES" if ok2 else "NO", margin2))

    return {"usable": not negative, "ok1": ok1, "ok2": ok2}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("-c", "--conn", default="og", help="连接名")
    ap.add_argument("--windows", type=int, default=DEFAULT_WINDOWS,
                     help="窗口数（默认最近 %d 个）" % DEFAULT_WINDOWS)
    args = ap.parse_args()

    db = None
    try:
        db = Database.connect(args.conn, read_only=True)
        snapshot_ids = fetch_recent_snapshot_ids(db, args.windows)
        values = fetch_instance_time(db, snapshot_ids)
    except (DBError, ProbeError) as exc:
        print("!!! 取数失败：%s" % exc, file=sys.stderr)
        return 2
    finally:
        if db is not None:
            db.close()

    results = []
    for snap_a, snap_b in zip(snapshot_ids, snapshot_ids[1:]):
        delta = compute_window_delta(values, snap_a, snap_b)
        results.append(print_window(snap_a, snap_b, delta))

    usable = [r for r in results if r["usable"]]
    unusable_n = len(results) - len(usable)
    check1_all = bool(usable) and all(r["ok1"] for r in usable)
    check2_all = bool(usable) and all(r["ok2"] for r in usable)

    print("\n=== 汇总（%d 个窗口，%d 个可用，%d 个因跨重启作废）==="
          % (len(results), len(usable), unusable_n))
    print("校验1 CPU_TIME+DATA_IO_TIME+NET_SEND_TIME <= EXECUTION_TIME：%s"
          % ("全部可用窗口成立" if check1_all else "至少一个可用窗口不成立"))
    print("校验2 EXECUTION_TIME+PARSE_TIME+PLAN_TIME+REWRITE_TIME <= DB_TIME：%s"
          % ("全部可用窗口成立" if check2_all else "至少一个可用窗口不成立"))

    if unusable_n:
        print("\n!!! 有 %d 个窗口跨了实例重启，结论只覆盖剩下的可用窗口，"
              "不是全部 %d 个窗口。" % (unusable_n, len(results)))

    if check1_all and check2_all and not unusable_n:
        print("\n结论：两条包含关系在全部 %d 个窗口都成立 —— 可以画两层树。" % len(results))
    else:
        print("\n结论：包含关系未能在全部窗口验证 —— 不画两层树，"
              "改为平铺列出各项占 DB_TIME 的比例，并注明未验证的部分。")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
