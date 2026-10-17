"""轻量锁(L1–L5):五段,每段 3 条会话,边界与 WDR 快照严格对齐。

**轻量锁和普通锁完全是两回事:** 普通锁(表锁、行锁)保护的是**数据**,能在 pg_locks 里
看到谁锁了哪张表、等了多久;轻量锁保护的是内核自己的**共享内存结构**(WAL 缓冲区、
缓冲池映射表、事务号分配器、锁管理器的分区),持有时间以微秒计,在 pg_locks 里一行都没有。
唯一能看到它的地方是等待事件统计(`pg_thread_wait_status` / WDR 的等待事件段)。

**所以这五段是专门用来检验 waitevent / wdr 的:** 如果一个工具只会查 pg_locks,
这五段在它眼里全是「没有锁等待,库很正常」,而实际上库已经在打转了。

**为什么边界要和快照对齐:** 每个快照窗口里只有一种轻量锁争用,WDR 的多窗口分解才能
给出「这 4 分钟是谁在争、下 4 分钟换成了谁」。五段混在一起的话,等待事件榜上永远是
同一锅粥,分不出是哪种操作引起的。

**不能保证每种都一定出现:** 轻量锁争用取决于内核版本、线程池配置、机器核数。
og5 只有 4 个核、3 条会话,有些段可能只是轻微争用。probe 会如实报出当下排名靠前的
等待事件,**没争起来就说没争起来**,不去替它圆场 —— 手册里也写明了这一点。
"""
from __future__ import annotations

import threading
import time
from typing import Dict, List

from .. import config as C
from ..db import Conn
from ..session import until
from .base import Base, register

SESSIONS = 3


class _Churn(Base):
    """五段的共同骨架:开 SESSIONS 条会话,每条反复跑自己那一小段 SQL。"""

    group = "lwlock"
    role = "w"

    def start(self) -> None:
        for n in range(SESSIONS):
            self.worker("%s%d" % (self.role, n),
                        lambda c, stop, i=n: self._loop(c, stop, i), expect_errors=True)

    def _loop(self, c: Conn, stop: threading.Event, idx: int) -> None:
        self.setup(c)
        until(stop, lambda: self.step(c, idx), period_s=0.0)

    def setup(self, c: Conn) -> None:
        """每条会话开始前做一次的设置(默认什么都不做)。"""

    def step(self, c: Conn, idx: int) -> None:
        raise NotImplementedError

    SAMPLES = 20
    SAMPLE_GAP_S = 0.1

    def probe(self, admin: Conn) -> str:
        """这个场景的会话在等什么 —— 连采 20 次(约 2 秒)再汇总。

        **为什么不能只看一眼:** 轻量锁的持有时间以**微秒**计。单次采样几乎总是落在
        「没在等」的那一刻,于是 probe 一直报 none —— 看起来像场景没生效,实际是采样方式不对
        (2026-10-17 全量跑时 L1/L2 一直显示 none/none,而同一时刻全局等待事件里
        明明有 WALFlushWait)。多采几次就能把这种瞬时争用抓出来。

        这同时也是给 skill 的一条提醒:**轻量锁要看累计统计(WDR 的等待事件段),
        不能靠查一次 pg_thread_wait_status。**
        """
        tally: Dict[str, int] = {}
        for _ in range(self.SAMPLES):
            rows = admin.query(
                "SELECT w.wait_status, coalesce(w.wait_event, '-') "
                "  FROM pg_thread_wait_status w JOIN pg_stat_activity a ON a.sessionid = w.sessionid "
                " WHERE a.application_name LIKE %s AND a.sessionid <> 0",
                (C.APP_PREFIX + self.code + "%",))
            for status, event in rows:
                key = "%s/%s" % (status, event)
                tally[key] = tally.get(key, 0) + 1
            time.sleep(self.SAMPLE_GAP_S)
        if not tally:
            return "连采 %d 次都没有本场景的等待记录(会话可能刚起或已停)" % self.SAMPLES
        top = sorted(tally.items(), key=lambda kv: -kv[1])[:4]
        busy = sum(v for k, v in tally.items() if not k.startswith("none/"))
        return "%d 次采样:%s(其中在等的 %d 次)" % (
            self.SAMPLES, "、".join("%s×%d" % (k, v) for k, v in top), busy)


@register("L1")
class CommitStorm(_Churn):
    """提交风暴:每条会话单行插入、每条都立即提交。

    每次提交都要把 WAL 写进缓冲区并刷盘 → 抢 WAL 插入锁、WAL 写锁;
    `synchronous_commit=on`(og5 的默认)时还要等刷盘完成。
    现实里的样子:应用把批量插入拆成了一行一个事务,吞吐上不去、CPU 也不高,全在等提交。
    """

    role = "ins"

    def step(self, c: Conn, idx: int) -> None:
        c.exec("INSERT INTO %s.wal_log(who, payload) VALUES (%%s, %%s)" % C.SCHEMA,
               ("L1-%d" % idx, "x" * 160))


@register("L2")
class HotPage(_Churn):
    """热点页:三条会话反复更新同一张 100 行的小表 —— 所有行都挤在同一个数据页上。

    每次更新都要拿这个页的内容锁,并发一上来就全在等同一个缓冲区。
    现实里的样子:全局计数器表、序列号表、状态标志表 —— 行数极少、更新极频繁。
    """

    role = "hot"

    def step(self, c: Conn, idx: int) -> None:
        c.exec("UPDATE %s.hotpage SET cnt = cnt + 1 WHERE id = %%s" % C.SCHEMA, ((idx % 5) + 1,))


@register("L3")
class ShortTransactions(_Churn):
    """极短事务高频提交:每笔事务只读一行,但不停地开、不停地提。

    每开一笔事务都要拿事务号、建快照 —— 争的是事务号分配器与活跃事务数组。
    现实里的样子:连接池里几百个连接,每个都在跑「SELECT 一行」级别的小事务。
    """

    role = "tx"

    def step(self, c: Conn, idx: int) -> None:
        c.exec("BEGIN")
        c.query("SELECT bal FROM %s.acct WHERE id = %%s" % C.SCHEMA, ((idx % 100) + 1,))
        c.exec("COMMIT")


@register("L4")
class PartitionLockMgr(_Churn):
    """1000 个分区的表,在一笔事务里扫全部分区 —— 一次要拿上千把分区锁。

    锁管理器的共享结构是按分区分段的,一次性申请上千把锁就会在那些分段上排队。
    现实里的样子:按天分区保留两三年的大表,查询没带分区键,一扫就是上千个分区。
    """

    role = "part"

    def setup(self, c: Conn) -> None:
        # 一笔事务里要同时持上千把分区锁,默认的 max_locks_per_transaction=256 不够;
        # 这是**会话级**改不了的实例参数,所以改成分批扫:每次扫 200 个分区。
        # (不去动实例参数 —— og5 上还跑着别人的测试。)
        c.set("enable_partition_opfusion", "off") if self._has_guc(c) else None

    @staticmethod
    def _has_guc(c: Conn) -> bool:
        return bool(c.one("SELECT count(*) FROM pg_settings WHERE name = 'enable_partition_opfusion'"))

    def step(self, c: Conn, idx: int) -> None:
        lo = (idx * 200) % 800
        c.exec("BEGIN")
        c.query("SELECT count(*) FROM %s.part_log WHERE bucket >= %%s AND bucket < %%s"
                % C.SCHEMA, (lo, lo + 200))
        c.exec("COMMIT")


@register("L5")
class SubTransactions(_Churn):
    """每笔事务里上千个子事务:PL/pgSQL 的 EXCEPTION 块,每进一次就是一个子事务。

    子事务数超过内核在内存里能缓存的上限(每笔事务 64 个)之后就要落到磁盘结构上,
    读的一方判断可见性时还要反复去查 —— 争的是子事务与提交日志的共享结构。
    现实里的样子:存储过程里循环套 EXCEPTION 做「单行容错」,一次调用上千次循环。
    """

    role = "sub"
    LOOPS = 1200

    def step(self, c: Conn, idx: int) -> None:
        c.exec("""DO $$
                  DECLARE v int;
                  BEGIN
                    FOR i IN 1..%d LOOP
                      BEGIN
                        SELECT cnt INTO v FROM %s.hotpage WHERE id = (i %% 100) + 1;
                      EXCEPTION WHEN others THEN NULL;
                      END;
                    END LOOP;
                  END $$;""" % (self.LOOPS, C.SCHEMA))
