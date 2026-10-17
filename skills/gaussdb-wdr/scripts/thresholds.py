"""Tunable WDR deterministic-finding thresholds — port of wdr/thresholds.go.

OLTP-oriented; v1 has no override flag (parity with the Go version).
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Thresholds:
    top_sql_dbtime_notice: float = 30   # 单条 SQL 占窗口 DB time %
    top_sql_dbtime_warn: float = 50
    top_sql_dbtime_crit: float = 80
    top_sql_low_cpu_ratio: float = 0.10  # CPU/elapsed 低于此 → 阻塞/睡眠主导
    # 「等了很久而 CPU≈0」要能**独立**报出来,不挂在占比门槛后面。
    # 2026-10-17 实测:榜首那条等了 5963 秒、CPU 0.03 秒,占 DB time 25.4% —— 没够 30% 的线,
    # 于是整个 Top SQL 维度一条发现都没出、判成健康。而「等几千秒、CPU 近零」本身就是
    # 被锁住的典型信号,和它占 DB time 多少无关。
    top_sql_low_cpu_min_elapsed_s: float = 60
    # CPU 占 DB time 低于此 → 这个窗口的时间主要花在等待上,不是在干活。
    # 取 30% 与 gaussdb-waitevent/scripts/dbtime.py 的 _IO_HEAVY_RATIO 对齐,
    # 免得两个 skill 对同一个窗口给出不一样的口径。
    cpu_share_notice: float = 30
    rollback_pct_notice: float = 5      # 回滚率 %
    rollback_pct_warn: float = 15
    rollback_floor: int = 1000          # 事务总数下限才判回滚率
    deadlock_notice: int = 1            # 窗口内死锁数
    deadlock_warn: int = 5
    cache_hit_notice: float = 99        # 缓存命中率 %（低于即告）
    cache_hit_warn: float = 95
    temp_spill_notice: int = 512 << 20  # 临时文件溢出字节
    temp_spill_warn: int = 2 << 30
    wait_skew_notice: float = 40        # 单一非空闲等待类占比 %
    wait_skew_warn: float = 60
    ckpt_req_notice: float = 30         # checkpoint_req 占比 %
    ckpt_req_warn: float = 50


def default_thresholds() -> Thresholds:
    return Thresholds()
