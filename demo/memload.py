#!/usr/bin/env python3
"""memload — 给 memanalyze 造一个真实的动态内存冲高现场（仅用于测试环境）。

开 N 个 worker 会话，每个把 work_mem 调大后反复跑大排序，把动态内存顶上去；
再留一个 idle-in-transaction 会话占着内存不释放。跑满 --minutes 后自动退出。

    python3 demo/memload.py -c og --minutes 15 --workers 3

⚠️ 只在测试库上跑。它会显著抬高实例的动态内存占用。
"""
from __future__ import annotations

import argparse
import multiprocessing as mp
import pathlib
import sys
import time

_HERE = pathlib.Path(__file__).resolve()
sys.path.insert(0, str(_HERE.parents[1]))

import common  # noqa: E402

# 8x 放大 200 万行 → 1600 万行排序；work_mem 给足，让它把内存真吃进来
_SORT = """
SELECT a.payload
FROM demo_mem.big_orders a, generate_series(1, 8) g
ORDER BY a.payload, a.amount
OFFSET 15999990 LIMIT 2"""

_IDLE_XACT = """
SELECT a.payload
FROM demo_mem.big_orders a, generate_series(1, 4) g
ORDER BY a.payload
OFFSET 7999990 LIMIT 2"""


def _worker(conn: str, work_mem: str, deadline: float, wid: int) -> None:
    db = common.Database.connect(conn, read_only=False)
    db.execute("SET statement_timeout = 0")
    db.execute(f"SET work_mem = '{work_mem}'")
    db.execute(f"SET application_name = 'memload_worker_{wid}'")
    n = 0
    while time.time() < deadline:
        try:
            db.query(_SORT)
            n += 1
            print(f"  [worker {wid}] 第 {n} 轮大排序完成", flush=True)
        except common.DBError as exc:
            print(f"  [worker {wid}] 查询失败：{str(exc)[:80]}", flush=True)
            time.sleep(2)
    db.close()


def _idle_xact(conn: str, work_mem: str, deadline: float) -> None:
    """跑一个大查询后把事务挂着不提交 —— 内存不会释放，直到事务结束。"""
    db = common.Database.connect(conn, read_only=False)
    db.execute("SET statement_timeout = 0")
    db.execute(f"SET work_mem = '{work_mem}'")
    db.execute("SET application_name = 'memload_idle_xact'")
    db.execute("BEGIN")
    try:
        db.query(_IDLE_XACT)
        print("  [idle-xact] 大查询完成，事务保持打开（内存不释放）", flush=True)
        while time.time() < deadline:
            time.sleep(2)
        db.execute("ROLLBACK")
    except common.DBError as exc:
        print(f"  [idle-xact] {str(exc)[:80]}", flush=True)
    db.close()


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="memload.py", description=__doc__)
    ap.add_argument("-c", "--conn", required=True, help="连接名")
    ap.add_argument("--minutes", type=float, default=15.0, help="持续分钟数（默认 15）")
    ap.add_argument("--workers", type=int, default=3, help="并发大排序会话数（默认 3）")
    ap.add_argument("--work-mem", default="1GB", help="每会话 work_mem（默认 1GB）")
    args = ap.parse_args(argv)

    deadline = time.time() + args.minutes * 60
    print(f"▶ 造负载：{args.workers} 个 worker（work_mem={args.work_mem}）"
          f" + 1 个 idle-in-transaction，持续 {args.minutes} 分钟")
    print(f"  结束时间：{time.strftime('%H:%M:%S', time.localtime(deadline))}")
    print("  现在可以去 opencode 里跑 memanalyze 了。\n")

    procs = [mp.Process(target=_worker, args=(args.conn, args.work_mem, deadline, i + 1))
             for i in range(args.workers)]
    procs.append(mp.Process(target=_idle_xact, args=(args.conn, args.work_mem, deadline)))
    for p in procs:
        p.start()
    for p in procs:
        p.join()

    print("\n✓ 负载结束，内存应已回落（这正是 watch 模式判定「尖峰回落」的场景）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
