#!/usr/bin/env python3
"""在真库上把 8x8 锁互斥矩阵撞出来，与 common/lockmodes.py 的表逐格比对。

用法（mac 上）：
    GSDB_HOME=~/.gdaa python3 tools/probe_lock_matrix.py -c og

一条会话持 A 模式，另一条请求 B 模式；能在超时内拿到就是不互斥，
被挡住就是互斥。需要持久会话，所以只能用 driver: pg8000 的连接。

**这是矩阵的事实来源。** common/lockmodes.py 里的表若与本工具的结果不一致，
以本工具为准 —— 表是人写的，撞出来的是数据库说的。
"""
from __future__ import annotations

import argparse
import os
import pathlib
import sys
import threading
import time

_ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT))

from common.db import Database                       # noqa: E402
from common.lockmodes import LOCK_MODES, conflicts   # noqa: E402

TBL = "zz_lock_matrix_probe"
ACQUIRE_TIMEOUT_S = 2.0     # 超过这个时间没拿到，判定为被挡住


def _lock_sql(mode: str) -> str:
    """把 pg_locks 的拼写换成 LOCK TABLE 的语法（AccessShareLock → ACCESS SHARE）。"""
    body = mode[:-4] if mode.endswith("Lock") else mode      # 去掉结尾的 Lock
    out = []
    for ch in body:
        if ch.isupper() and out:
            out.append(" ")
        out.append(ch.upper())
    return "".join(out)


def measure(conn: str, holder_mode: str, waiter_mode: str) -> bool:
    """返回 True 表示实测互斥（waiter 在超时内没拿到锁）。"""
    got = threading.Event()
    stop = threading.Event()

    def holder():
        db = Database.connect(conn, read_only=False)
        try:
            db.execute("BEGIN")
            db.execute("LOCK TABLE %s IN %s MODE" % (TBL, _lock_sql(holder_mode)))
            while not stop.is_set():
                time.sleep(0.05)
            db.execute("ROLLBACK")
        finally:
            db.close()

    def waiter():
        db = Database.connect(conn, read_only=False)
        try:
            db.execute("BEGIN")
            db.execute("LOCK TABLE %s IN %s MODE" % (TBL, _lock_sql(waiter_mode)))
            got.set()
            db.execute("ROLLBACK")
        except Exception:
            pass
        finally:
            db.close()

    th = threading.Thread(target=holder, daemon=True)
    th.start()
    time.sleep(0.4)                       # 让 holder 先拿到
    tw = threading.Thread(target=waiter, daemon=True)
    tw.start()
    blocked = not got.wait(ACQUIRE_TIMEOUT_S)
    stop.set()
    th.join(timeout=10)
    tw.join(timeout=10)
    return blocked


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("-c", "--conn", default="og",
                    help="连接名（必须是 driver: pg8000 —— 需要持久会话）")
    args = ap.parse_args()

    admin = Database.connect(args.conn, read_only=False)
    admin.execute("DROP TABLE IF EXISTS %s" % TBL)
    admin.execute("CREATE TABLE %s (i int)" % TBL)
    mismatches = []
    try:
        print("%-26s %s" % ("holder \\ waiter", " ".join(
            "%2d" % (i + 1) for i in range(len(LOCK_MODES)))))
        for h in LOCK_MODES:
            row = []
            for w in LOCK_MODES:
                actual = measure(args.conn, h, w)
                expected = conflicts(h, w)
                row.append("X" if actual else ".")
                if actual != expected:
                    mismatches.append((h, w, expected, actual))
            print("%-26s %s" % (h, "  ".join(row)))
    finally:
        admin.execute("DROP TABLE IF EXISTS %s" % TBL)
        admin.close()

    if mismatches:
        print("\n!!! 与 common/lockmodes.py 不一致的格子（以实测为准）：")
        for h, w, e, a in mismatches:
            print("  holder=%s waiter=%s 表里=%s 实测=%s" % (h, w, e, a))
        return 1
    print("\n8x8 全部与 common/lockmodes.py 一致")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
