"""VACUUM 相关(V1–V7):死元组、膨胀、回收水位被卡、限速追不上、维护堵业务、统计信息过期。

**一条贯穿的主线:死元组清不掉的原因不止一个,而解法完全不同:**
  · V1 autovacuum 被关掉 —— 开回去就好;
  · V2 长事务持着旧快照 —— 要去找那笔事务,改 autovacuum 没用;
  · V3 两阶段事务 —— 连会话都没有,只能 ROLLBACK PREPARED;
  · V4 复制槽没人消费 —— 要删槽或让备机跟上;
  · V5 限速太狠 —— 调 cost_delay / cost_limit。
  诊断工具的价值就在于**分清是哪一种**:报一句「有死元组」等于没说。

**V2/V3/V4 都是「状态型」的:** 建好之后一直成立、几乎不耗 CPU。它们留下的东西不会随
会话结束消失,所以全部登记进 state,由 cleanup 收。
"""
from __future__ import annotations

import threading
from typing import Optional

from .. import config as C
from .. import state
from ..db import Conn
from ..session import sleep_until, until
from .base import Base, register

HOLD_POLL_S = 0.5


def _dead_ratio(admin: Conn, table: str) -> str:
    row = admin.query(
        "SELECT n_live_tup, n_dead_tup, "
        "       CASE WHEN n_live_tup + n_dead_tup > 0 "
        "            THEN round(100.0 * n_dead_tup / (n_live_tup + n_dead_tup), 1) ELSE 0 END, "
        "       pg_size_pretty(pg_total_relation_size(%s)) "
        "  FROM pg_stat_user_tables WHERE schemaname = %s AND relname = %s",
        ("%s.%s" % (C.SCHEMA, table), C.SCHEMA, table))
    if not row:
        return "查不到 %s 的统计" % table
    live, dead, pct, size = row[0]
    return "存活 %s、死元组 %s(%s%%)、占用 %s" % (live, dead, pct, size)


class _Updater(Base):
    """反复更新同一张表的同一批行 —— 每次更新都留下一个死元组。"""

    table = ""
    rows = 0

    def _update(self, c: Conn, stop: threading.Event) -> None:
        pos = [0]

        def step() -> None:
            pos[0] = (pos[0] + 1) % max(1, self.rows)
            c.exec("UPDATE %s.%s SET cnt = cnt + 1, touched_at = now() "
                   "  WHERE id BETWEEN %%s AND %%s" % (C.SCHEMA, self.table),
                   (pos[0] + 1, pos[0] + 200))

        # **更新失败要接着干,不能停。** V6 对同一张表做 VACUUM FULL 时会把表文件整个重写,
        # 正在更新它的会话会收到 `could not seek to end of file …`(2026-10-17 全量跑时实测)。
        # 这在现场就是真实后果 —— 业务 SQL 当场报错 —— 但它不该让造膨胀这件事停下来,
        # 否则 VACUUM FULL 之后死元组就不再增长,V1 的现象也跟着消失了。
        until(stop, step, period_s=0.05,
              on_error=lambda exc: self.ctx.bump("%s_update_errors" % self.code))


@register("V1")
class BloatNoAutovacuum(_Updater):
    """关掉表级 autovacuum,然后一直更新 → 死元组过半、表膨胀数倍。

    现实里的样子:有人为了「别让 vacuum 影响业务」把某张热表的 autovacuum 关了,
    然后忘了。几周后这张表的扫描慢了好几倍,查询计划也开始走偏。
    """

    group = "vacuum"
    table = "bloat_hot"
    rows = 300_000

    def start(self) -> None:
        c = Conn(self.code + "/setup")
        try:
            c.exec("ALTER TABLE %s.%s SET (autovacuum_enabled = false)" % (C.SCHEMA, self.table))
            state.add_residue("table_option", "%s.%s:autovacuum_enabled" % (C.SCHEMA, self.table))
            before = c.one("SELECT pg_total_relation_size('%s.%s')" % (C.SCHEMA, self.table))
            self.log("已关掉 %s 的 autovacuum,起始大小 %s 字节" % (self.table, before))
        finally:
            c.close()
        self.worker("upd", self._update)

    def probe(self, admin: Conn) -> str:
        return _dead_ratio(admin, self.table)


@register("V5")
class BloatThrottled(_Updater):
    """autovacuum 开着,但被表级参数限到很慢 → 它一直在跑,却追不上更新速度。

    **和 V1 的区别就是这套工具要分清的事:** 这里 autovacuum 是**开着**的,
    `last_autovacuum` 一直在更新,但死元组照样涨。只看「autovacuum 开没开」会误判成正常。
    """

    group = "vacuum"
    table = "bloat_slow"
    rows = 200_000

    def start(self) -> None:
        c = Conn(self.code + "/setup")
        try:
            # 限到几乎不干活:每清一点就睡 100 毫秒,一轮的配额只有 10
            c.exec("ALTER TABLE %s.%s SET (autovacuum_enabled = true, "
                   "  autovacuum_vacuum_cost_delay = 100, autovacuum_vacuum_cost_limit = 10, "
                   "  autovacuum_vacuum_scale_factor = 0.001)" % (C.SCHEMA, self.table))
            state.add_residue("table_option", "%s.%s:autovacuum_cost" % (C.SCHEMA, self.table))
            self.log("已把 %s 的 autovacuum 限速到 cost_delay=100ms / cost_limit=10" % self.table)
        finally:
            c.close()
        self.worker("upd", self._update)

    def probe(self, admin: Conn) -> str:
        last = admin.one(
            "SELECT to_char(last_autovacuum, 'HH24:MI:SS') FROM pg_stat_user_tables "
            " WHERE schemaname = %s AND relname = %s", (C.SCHEMA, self.table))
        return "%s;最近一次 autovacuum %s(开着却追不上)" % (_dead_ratio(admin, self.table), last or "还没来过")


@register("V2")
class LongTransactionHold(Base):
    """长事务持着可重复读快照 → 它开始之后产生的死元组一个都清不掉。

    autovacuum 会照常跑完、`last_autovacuum` 照常更新,但死元组纹丝不动 ——
    因为那笔老事务还可能要看到旧版本。日志里的说法是「… cannot be removed yet」。
    """

    group = "vacuum"
    table = "xmin_hold"

    def start(self) -> None:
        self.worker("holder", self._hold)
        self.worker("upd", self._churn)

    def _hold(self, c: Conn, stop: threading.Event) -> None:
        c.exec("START TRANSACTION ISOLATION LEVEL REPEATABLE READ")
        # **核对隔离级别真的生效了。** 可重复读的快照是这个场景成立的唯一前提:
        # 退化成读已提交的话,每条语句都取新快照,回收水位压根不住,而死元组照样会涨
        # (autovacuum 还没来得及跑)—— 看起来和成功一模一样。
        level = str(c.one("SELECT current_setting('transaction_isolation')") or "")
        if "repeatable" not in level.lower():
            raise RuntimeError("隔离级别是 %s,不是可重复读 —— V2 不成立,不往下演" % level)
        c.query("SELECT count(*) FROM %s.%s" % (C.SCHEMA, self.table))   # 快照在这里定下来
        self.log("长事务已持住快照(%s),之后的死元组都清不掉" % level)
        while not stop.is_set():
            stop.wait(HOLD_POLL_S)
        c.exec("ROLLBACK")

    def _churn(self, c: Conn, stop: threading.Event) -> None:
        pos = [0]

        def step() -> None:
            pos[0] = (pos[0] + 1) % 100_000
            c.exec("UPDATE %s.%s SET cnt = cnt + 1 WHERE id BETWEEN %%s AND %%s"
                   % (C.SCHEMA, self.table), (pos[0] + 1, pos[0] + 100))
        until(stop, step, period_s=0.2)

    def probe(self, admin: Conn) -> str:
        age = admin.one(
            "SELECT extract(epoch FROM now() - xact_start)::int FROM pg_stat_activity "
            " WHERE application_name = %s AND sessionid <> 0", (C.APP_PREFIX + "V2/holder",))
        return "%s;长事务已持续 %s 秒" % (_dead_ratio(admin, self.table), age)


@register("V3")
class PreparedXactHold(Base):
    """两阶段事务卡住回收水位:连会话都没有,只有 pg_prepared_xacts 里一条记录。

    和 V2 的差别:V2 至少能在 pg_stat_activity 里找到那笔长事务;这里什么会话都找不到,
    只能靠 pg_prepared_xacts。**现场遇到过「找不到长事务却清不掉死元组」的工单就是这种。**
    """

    group = "vacuum"
    GID = "fl_xmin_v3"
    table = "xmin_hold"

    def start(self) -> None:
        self.worker("prep", self._prepare)

    def _prepare(self, c: Conn, stop: threading.Event) -> None:
        c.exec("BEGIN")
        c.exec("UPDATE %s.%s SET cnt = cnt + 1 WHERE id = 1" % (C.SCHEMA, self.table))
        c.exec("PREPARE TRANSACTION '%s'" % self.GID)
        state.add_residue("prepared_xact", self.GID)
        self.log("已 PREPARE %s —— 回收水位从此卡住" % self.GID)

    def probe(self, admin: Conn) -> str:
        n = admin.one("SELECT count(*) FROM pg_prepared_xacts WHERE gid = %s", (self.GID,))
        return "两阶段事务 %s 条(查 pg_stat_activity 是找不到它的)" % n


@register("V4")
class ReplicationSlotHold(Base):
    """建一个没人消费的复制槽 → WAL 只增不删,回收水位也被它压住。

    **带守卫:** 槽一旦留着,WAL 会一直涨。og5 的盘是和别的测试共用的,所以这里起一条
    看门线程,每分钟看一次 WAL 目录大小,超过 WAL_GUARD_BYTES 就立刻删槽并记一行日志。
    宁可让这个场景提前结束,也不能把别人的测试环境搞坏。
    """

    group = "vacuum"
    SLOT = "fl_slot_v4"

    def start(self) -> None:
        c = Conn(self.code + "/setup")
        try:
            c.exec("SELECT pg_drop_replication_slot(%s) WHERE EXISTS "
                   "  (SELECT 1 FROM pg_replication_slots WHERE slot_name = %s)",
                   (self.SLOT, self.SLOT))
            # openGauss 的物理槽要两个参数(第二个是「是否 dummy standby」)
            c.exec("SELECT pg_create_physical_replication_slot(%s, false)", (self.SLOT,))
            state.add_residue("slot", self.SLOT)
            self.log("已建复制槽 %s(无人消费)" % self.SLOT)
        finally:
            c.close()
        self.worker("guard", self._guard)

    def _guard(self, c: Conn, stop: threading.Event) -> None:
        # **看增量,不看总量。** 第一版比的是 pg_ls_waldir() 的总和,而 og5 上本来就
        # 存着 8.8 GB 的 WAL —— 守卫在建槽后几十秒就触发,把场景自己掐了
        # (2026-10-17 全量跑时 +95 秒就被删了)。要防的是「这个槽让 WAL 涨多少」,
        # 所以记下基线,只盯增量。
        base = c.one("SELECT sum(size) FROM pg_ls_waldir()") if self._has_waldir(c) else None
        if base is None:
            self.log("这个内核没有 pg_ls_waldir(),WAL 守卫无法工作 —— 槽会留到清理时才删")
            return
        base = int(base)

        def check() -> None:
            used = c.one("SELECT sum(size) FROM pg_ls_waldir()")
            if used is None:
                return
            grown = int(used) - base
            if grown > C.WAL_GUARD_BYTES:
                c.exec("SELECT pg_drop_replication_slot(%s)", (self.SLOT,))
                state.drop_residue("slot", self.SLOT)
                self.log("槽建立以来 WAL 涨了 %s 字节,超过守卫阈值 —— 已主动删掉复制槽" % grown)
                stop.set()
        until(stop, check, period_s=60)

    @staticmethod
    def _has_waldir(c: Conn) -> bool:
        return bool(c.one("SELECT count(*) FROM pg_proc WHERE proname = 'pg_ls_waldir'"))

    def probe(self, admin: Conn) -> str:
        row = admin.query("SELECT slot_name, active, restart_lsn FROM pg_replication_slots "
                          " WHERE slot_name = %s", (self.SLOT,))
        if not row:
            return "槽已不在(可能被守卫删了,见时间线)"
        return "槽 %s 在、active=%s、restart_lsn=%s" % row[0]


@register("V7")
class ImportNoAnalyze(Base):
    """批量导入 50 万行、**导完不收集统计信息** —— S5 的成因。

    这一项本身耗时很短(实测不到 1 秒),它造的是一个**状态**:统计信息与实际数据
    差几十倍。表级 autovacuum 已经在建表时关掉了,所以这个状态不会被自动修正。
    """

    group = "vacuum"

    def start(self) -> None:
        self.worker("import", self._import)

    def _import(self, c: Conn, stop: threading.Event) -> None:
        from ..prepare import import_batch
        import_batch(c)
        self.log("已导入 %d 行,**没有** ANALYZE" % C.IMPORT_ROWS)

    def probe(self, admin: Conn) -> str:
        est = admin.one("SELECT reltuples::bigint FROM pg_class "
                        " WHERE oid = '%s.import_stage'::regclass" % C.SCHEMA)
        real = admin.one("SELECT count(*) FROM %s.import_stage" % C.SCHEMA)
        return "统计信息里 %s 行、实际 %s 行" % (est, real)


@register("V6")
class VacuumFullBlocks(Base):
    """业务高峰做维护:对膨胀的表做 VACUUM FULL,这张表的读写全被挡住。

    VACUUM FULL 要整表排他锁、还要重写整张表 —— 空间是收回来了,代价是这段时间里
    谁也别想碰它。现实里的样子:发现表膨胀,白天就手工 VACUUM FULL,业务当场中断。
    """

    group = "vacuum"
    table = "bloat_hot"

    def start(self) -> None:
        self.worker("maint", self._maint, expect_errors=True)
        self.worker("biz", self._biz, expect_errors=True)

    def _maint(self, c: Conn, stop: threading.Event) -> None:
        self.log("开始 VACUUM FULL %s(期间这张表读写都进不去)" % self.table)
        c.exec("VACUUM FULL %s.%s" % (C.SCHEMA, self.table))
        self.log("VACUUM FULL 做完")
        while not stop.is_set():
            stop.wait(HOLD_POLL_S)

    def _biz(self, c: Conn, stop: threading.Event) -> None:
        until(stop, lambda: c.query("SELECT count(*) FROM %s.%s WHERE id < 100"
                                    % (C.SCHEMA, self.table)), period_s=3)

    def probe(self, admin: Conn) -> str:
        running = admin.one(
            "SELECT count(*) FROM pg_stat_activity "
            " WHERE application_name = %s AND state = 'active' AND sessionid <> 0",
            (C.APP_PREFIX + "V6/maint",))
        return "维护会话 %s 个在跑,业务侧等锁 %d 个;%s" % (
            running, self.waiting(admin), _dead_ratio(admin, self.table))
