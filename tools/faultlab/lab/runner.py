"""按排期表造故障的编排器:起场景、打 WDR 快照、记时间线、到点收工。

**三道保险,缺一不可(故障不能比实验活得久):**
  ① 每条会话自带会话级超时(db.Conn 的 guards)—— 编排器被 kill 也会自己到期;
  ② 看门线程:窗口结束后再等 GUARD_MARGIN_S,无论主流程在干什么都强制收;
  ③ 收工后 cleanup 逐项核对残留,核不干净就非 0 退出 —— 不让人以为「跑完了就干净了」。

**时间线为什么要落盘:** 跑完之后要拿这些时刻去对 WDR 窗口、去对 skill 的输出。
全靠终端回滚的话,一个 20 分钟的运行、几十条事件,很快就说不清「那条慢 SQL 是几点开始的」。
"""
from __future__ import annotations

import datetime as _dt
import json
import threading
import time
from typing import Dict, List, Optional, Tuple

from . import config as C
from . import state
from .db import REAL_SESSION, Conn, LabError, assert_expected_instance, omm_sql
from .scenarios import ALL as _IMPORT_ALL      # noqa: F401  —— 导入即注册全部场景
from .scenarios.base import Base, build
from .session import Ctx

TIMELINE = C.STATE_DIR / "timeline.jsonl"
PROGRESS_EVERY_S = 60


class Timeline:
    """时间线:同时写终端与 ~/.faultlab/timeline.jsonl。"""

    def __init__(self, t0: float):
        self.t0 = t0
        self._lock = threading.Lock()
        C.STATE_DIR.mkdir(parents=True, exist_ok=True)
        self._fh = TIMELINE.open("w", encoding="utf-8")

    def add(self, event: str, detail: str = "", db_time: str = "") -> None:
        off = int(time.time() - self.t0)
        row = {"offset_s": off, "wall": _dt.datetime.now().strftime("%H:%M:%S"),
               "db_time": db_time, "event": event, "detail": detail}
        line = "  %+5ds %-10s %s%s" % (off, row["wall"], event, ("  " + detail) if detail else "")
        with self._lock:
            print(line, flush=True)
            self._fh.write(json.dumps(row, ensure_ascii=False) + "\n")
            self._fh.flush()

    def close(self) -> None:
        with self._lock:
            self._fh.close()


def _db_now(c: Conn) -> str:
    return str(c.one("SELECT to_char(now(), 'YYYY-MM-DD HH24:MI:SS')") or "")


def _snapshot(c: Conn, tl: Timeline, label: str) -> Optional[int]:
    """打一个 WDR 快照并记下快照号。

    **打不出来不算致命:** 快照只是让 WDR 能对窗口,故障本身照样在造。所以失败只记一行,
    不中断运行 —— 但要如实写明没打上,否则事后会以为窗口里有快照。
    """
    # **必须以 omm 身份打快照。** 实验用户即使给了 SYSADMIN,访问 snapshot 模式仍然
    # 报 permission denied(2026-10-17 实测:`permission denied for schema snapshot`)——
    # 那个模式归初始用户所有。与其给实验用户再开一道授权,不如这一步走容器里的 omm:
    # 打快照是运维动作,本来就不该靠业务用户的权限。
    try:
        out = omm_sql("SELECT create_wdr_snapshot();\n"
                      "SELECT max(snapshot_id) FROM snapshot.snapshot;")
        lines = [x.strip() for x in out.strip().splitlines() if x.strip()]
        sid = next((int(x) for x in reversed(lines) if x.isdigit()), None)
        if sid is None:
            raise LabError("打完快照没读到快照号:%s" % out.strip()[:160])
        tl.add("WDR 快照", "%s snap_id=%s" % (label, sid), _db_now(c))
        return sid
    except Exception as exc:                 # noqa: BLE001
        tl.add("WDR 快照失败", "%s:%s" % (label, str(exc).splitlines()[0][:120]))
        return None


def _start_scenario(sc: Base, tl: Timeline) -> None:
    """在单独线程里起一个场景 —— 有些场景要等持锁确认(K1 最多 20 秒),
    不能让它挡住后面场景的起跑时刻。"""
    def go() -> None:
        try:
            sc.start()
        except Exception as exc:             # noqa: BLE001
            sc.ctx.error(sc.code, exc)
            tl.add("场景起不来", "%s:%s" % (sc.code, str(exc).splitlines()[0][:140]))
    threading.Thread(target=go, name="start-" + sc.code, daemon=True).start()


def run(scenarios: Tuple[C.Scenario, ...] = (), window_s: int = C.WINDOW_S,
        quiet: int = C.BASELINE_QUIET_S) -> int:
    """造故障。返回退出码:0 = 跑完且清理干净。"""
    scenarios = scenarios or C.ALL_SCENARIOS
    if window_s < 60 or window_s > 7200:
        raise LabError("窗口要在 60~7200 秒之间,给的是 %r" % (window_s,))
    budget = C.cpu_budget(scenarios)
    if budget["peak"] > budget["limit"]:
        raise LabError("排期表里同时吃 CPU 的会话峰值 %d 超过上限 %d —— 先改排期,别把库压死"
                       % (budget["peak"], budget["limit"]))

    admin = Conn("runner")
    assert_expected_instance(admin)
    _pre_clean(admin)

    ctx = Ctx(log=lambda m: None)            # log 在下面接到时间线上
    t0 = time.time()
    tl = Timeline(t0)
    ctx.log = lambda m: tl.add("场景", m)
    live: Dict[str, Base] = {}
    snaps: List[int] = []
    rc = 0
    try:
        tl.add("开始", "窗口 %d 秒,场景 %d 个,CPU 峰值 %d/%d"
               % (window_s, len(scenarios), budget["peak"], budget["limit"]), _db_now(admin))
        s = _snapshot(admin, tl, "基线前")
        if s:
            snaps.append(s)
        if quiet > 0:
            tl.add("静默基线", "%d 秒(什么都不跑,给 WDR 一个干净的对照窗口)" % quiet)
            time.sleep(quiet)
            s = _snapshot(admin, tl, "基线后")
            if s:
                snaps.append(s)

        t_window = time.time()
        # 明确记一条「窗口开始」。**排期表里的秒数是从这一刻算的**,而时间线的 offset 是从
        # 整个运行开始算的(前面还有基线静默)—— 两个原点差一个基线时长,report 要据此换算,
        # 否则表里的「偏移」看着像排期全体晚了一分钟。
        tl.add("故障窗口开始", "排期表里的秒数从这一刻起算", _db_now(admin))
        _arm_watchdog(ctx, window_s)
        rc = _drive(ctx, tl, admin, scenarios, live, snaps, t_window, window_s)
    finally:
        tl.add("故障窗口结束", "", _db_now(admin))
        ctx.stop.set()          # 全局停止位:所有会话的循环下一轮就会退出
        for sc in live.values():
            for w in sc.workers:
                w.signal()
        for sc in live.values():
            sc.stop()
        tl.add("全部场景已停")
        s = _snapshot(admin, tl, "故障结束")
        if s:
            snaps.append(s)
        from . import cleanup
        info = cleanup.run()
        tl.add("清理", json.dumps(info, ensure_ascii=False)[:300])
        if str(info.get("残留")) not in ("无", "None"):
            rc = rc or 1
        if C.SETTLE_S > 0:
            tl.add("恢复观察", "%d 秒" % C.SETTLE_S)
            time.sleep(C.SETTLE_S)
            s = _snapshot(admin, tl, "恢复后")
            if s:
                snaps.append(s)
        _summary(ctx, tl, snaps)
        tl.close()
        admin.close()
    return rc


def _pre_clean(admin: Conn) -> None:
    """上一次跑完留下的东西,先收干净再开始 —— 不然这一次的现象里混着上一次的残留。"""
    left = state.residues()
    if left:
        print("  上次运行留下 %d 件残留,先清理" % len(left))
        from . import cleanup
        cleanup.run()


def _arm_watchdog(ctx: Ctx, window_s: int) -> None:
    """看门线程:到点无论主流程在干什么都置停止位。"""
    def watch() -> None:
        deadline = time.time() + window_s + C.GUARD_MARGIN_S
        while time.time() < deadline:
            if ctx.stop.is_set():
                return
            time.sleep(1)
        print("  [看门线程] 超过窗口 + %d 秒,强制停止" % C.GUARD_MARGIN_S, flush=True)
        ctx.stop.set()
    threading.Thread(target=watch, name="watchdog", daemon=True).start()


def _drive(ctx: Ctx, tl: Timeline, admin: Conn, scenarios: Tuple[C.Scenario, ...],
           live: Dict[str, Base], snaps: List[int], t_window: float, window_s: int) -> int:
    """主循环:到点起场景、到点停场景、按间隔打快照与报进度。"""
    pending = sorted(scenarios, key=lambda s: s.start_s)
    next_snap = C.SNAPSHOT_EVERY_S
    next_prog = PROGRESS_EVERY_S
    while True:
        off = time.time() - t_window
        if off >= window_s or ctx.stop.is_set():
            return 0
        while pending and pending[0].start_s <= off:
            meta = pending.pop(0)
            sc = build(ctx, meta)
            live[meta.code] = sc
            tl.add("起 " + meta.code, meta.title, _db_now(admin))
            _start_scenario(sc, tl)
        for code, sc in list(live.items()):
            meta = sc.meta
            if meta and meta.stop_s and off >= meta.stop_s:
                sc.stop()
                del live[code]
                tl.add("停 " + code, "按排期结束", _db_now(admin))
        if off >= next_snap and next_snap < window_s:
            s = _snapshot(admin, tl, "窗口内 %d 分钟" % (next_snap // 60))
            if s:
                snaps.append(s)
            next_snap += C.SNAPSHOT_EVERY_S
        if off >= next_prog:
            _progress(ctx, tl, admin, live)
            next_prog += PROGRESS_EVERY_S
        time.sleep(1)


def _progress(ctx: Ctx, tl: Timeline, admin: Conn, live: Dict[str, Base]) -> None:
    """每分钟一行总览 + 逐场景自证。"""
    act = admin.one("SELECT count(*) FROM pg_stat_activity "
                    " WHERE application_name LIKE %%s AND %s" % REAL_SESSION, (C.APP_PREFIX + "%",))
    waiting = admin.one("SELECT count(*) FROM pg_stat_activity "
                        " WHERE application_name LIKE %%s AND waiting AND %s" % REAL_SESSION,
                        (C.APP_PREFIX + "%",))
    tl.add("进度", "实验会话 %s 个(其中等锁 %s),在跑的场景 %d 个"
           % (act, waiting, len(live)))
    for code, sc in sorted(live.items()):
        try:
            tl.add("  " + code, sc.probe(admin))
        except Exception as exc:             # noqa: BLE001 —— 自证失败不该中断运行
            tl.add("  " + code, "自证失败:%s" % str(exc).splitlines()[0][:100])


def _summary(ctx: Ctx, tl: Timeline, snaps: List[int]) -> None:
    counters = ctx.snapshot_counters()
    tl.add("快照号", ", ".join(str(s) for s in snaps) or "(一个都没打上)")
    done = {k: v for k, v in counters.items() if k.endswith("_done")}
    if done:
        tl.add("慢 SQL 完成次数", ", ".join("%s=%s" % (k[:-5], v) for k, v in sorted(done.items())))
    if counters.get("deadlocks_seen"):
        tl.add("死锁", "本次新增 %s 次" % counters["deadlocks_seen"])
    if ctx.errors:
        tl.add("出错的会话", "%d 条" % len(ctx.errors))
        for e in ctx.errors[:10]:
            tl.add("  ", "%s:%s" % (e["who"], e["error"]))
    else:
        tl.add("出错的会话", "无(预期内的报错不计:死锁牺牲者、唯一键冲突等)")
