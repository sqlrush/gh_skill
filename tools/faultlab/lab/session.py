"""会话模型:一条会话 = 一个线程 + 一条自己的 psycopg2 连接。

**为什么必须一人一条连接:** 锁、事务、等待状态在数据库里都是按后端算的。两个场景共用
一条连接的话,它们的事务会被搅在一起,造出来的现象就不是设计的那个,而人看输出根本看不出来。

**为什么要有「预期会报错」这档:** 死锁场景里牺牲者**一定**会收到报错,唯一键冲突、
锁超时也一样。把它们当失败的话,整个运行会被自己的设计意图判成红。所以每个会话都要
声明自己预期什么错:`expect_errors=True` 的会话把报错计数、照常继续;没声明的会话
一出错就记进 errors,运行结束时如实报出来。
"""
from __future__ import annotations

import threading
import time
from typing import Callable, Dict, List, Optional

from . import config as C
from .db import Conn

POLL_S = 0.2
# 等一条会话自己退出最多等这么久。等不到也不强等:它多半正挂在锁等待里,
# 而 cleanup 紧接着会把数据库会话杀掉 —— 会话一断,那条 SQL 就返回,线程随即结束。
# (线程都是 daemon,不会挡住进程退出。)
JOIN_TIMEOUT_S = 8.0


class Ctx:
    """跑一次的共享上下文:全局停止信号、时间线日志、错误与计数。"""

    def __init__(self, log: Callable[[str], None]):
        self.stop = threading.Event()
        self.log = log
        self._lock = threading.Lock()
        self.errors: List[Dict[str, str]] = []
        self.counters: Dict[str, int] = {}

    def error(self, who: str, exc: BaseException) -> None:
        with self._lock:
            self.errors.append({"who": who, "error": "%s: %s" % (type(exc).__name__,
                                                                 str(exc).splitlines()[0][:200])})

    def bump(self, key: str, n: int = 1) -> None:
        with self._lock:
            self.counters[key] = self.counters.get(key, 0) + n

    def snapshot_counters(self) -> Dict[str, int]:
        with self._lock:
            return dict(self.counters)


def _closed_on_purpose(exc: BaseException, ctx: Ctx) -> bool:
    """收工阶段「连接被断开」是我们自己干的,不算故障。

    cleanup 会 pg_terminate_session 掉所有造故障的会话 —— 正挂在长查询里的那几条
    必然收到 `server closed the connection unexpectedly`。第一版把它们记成了出错会话,
    于是每次运行都带着两三条假错误收场(2026-10-17 全量跑时 S3/S5 就是这样)。
    只在**停止位已经置上**之后才这么宽容,别把运行中途真的断连也掩盖掉。
    """
    if not ctx.stop.is_set():
        return False
    text = str(exc).lower()
    return ("server closed the connection" in text or "terminating connection" in text
            or "connection already closed" in text or "no connection to the server" in text)


class Worker:
    """一条会话。fn(conn, stop) 跑在自己的线程里;stop 被 set 后应尽快返回。

    连接在线程里建、在线程里关 —— psycopg2 的连接不是线程安全的,不能在主线程建好
    再交给别人用。
    """

    def __init__(self, ctx: Ctx, app: str, fn: Callable[[Conn, threading.Event], None], *,
                 autocommit: bool = True, guards: bool = True, expect_errors: bool = False):
        self.ctx = ctx
        self.app = app
        self._fn = fn
        self._autocommit = autocommit
        self._guards = guards
        self._expect_errors = expect_errors
        self._own_stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self.started = threading.Event()        # 连接已建好(不代表已拿到锁)

    # -- 生命周期 -----------------------------------------------------------

    def start(self) -> "Worker":
        self._thread = threading.Thread(target=self._run, name=self.app, daemon=True)
        self._thread.start()
        return self

    def _run(self) -> None:
        conn = None
        try:
            conn = Conn(self.app, autocommit=self._autocommit, guards=self._guards)
            self.started.set()
            self._fn(conn, self._stop_view())
        except BaseException as exc:            # noqa: BLE001 —— 线程里不接就只会打到 stderr
            if self._expect_errors or _closed_on_purpose(exc, self.ctx):
                self.ctx.bump("expected_errors")
            else:
                self.ctx.error(self.app, exc)
            self.started.set()
        finally:
            if conn is not None:
                conn.rollback()
                conn.close()

    def _stop_view(self) -> threading.Event:
        """合并「本会话停」与「全局停」。

        线程里只认一个 Event 最简单,所以起一个小看门线程把两者或起来 ——
        比在每个场景里到处写 `if a.is_set() or b.is_set()` 不容易漏。
        """
        merged = threading.Event()

        def watch() -> None:
            while not merged.is_set():
                if self._own_stop.is_set() or self.ctx.stop.is_set():
                    merged.set()
                    return
                time.sleep(POLL_S)

        threading.Thread(target=watch, name=self.app + "-watch", daemon=True).start()
        return merged

    def signal(self) -> None:
        """只置停止位,不等。

        **为什么要和 join 分开:** 一条正挂在锁等待里的会话,要等那条 SQL 返回才能回到
        Python 代码里看停止位 —— 不会主动醒。如果边置位边 join,6 条这样的会话就要
        串着各等一遍超时(实测收工花了 60 秒)。先把所有人的位都置上,再一起等,
        慢的也只慢一次。
        """
        self._own_stop.set()

    def stop(self, join_s: float = JOIN_TIMEOUT_S) -> None:
        self.signal()
        if self._thread is not None:
            self._thread.join(timeout=join_s)

    @property
    def alive(self) -> bool:
        return bool(self._thread and self._thread.is_alive())


# ---------------------------------------------------------------- 常用循环

def until(stop: threading.Event, step: Callable[[], None], *, period_s: float = 0.0,
          on_error: Optional[Callable[[BaseException], None]] = None) -> None:
    """反复跑 step 直到 stop。

    step 抛错时:给了 on_error 就交给它(死锁场景用来计数并继续),没给就抛出去
    (由 Worker 记进 errors)。**不会静默吞掉** —— 悄悄吞错会让「场景没生效」
    看起来和「场景正常跑着」一模一样。
    """
    while not stop.is_set():
        t0 = time.time()
        try:
            step()
        except BaseException as exc:            # noqa: BLE001
            if on_error is None:
                raise
            on_error(exc)
        left = period_s - (time.time() - t0)
        if left > 0:
            stop.wait(left)


def sleep_until(stop: threading.Event, seconds: float) -> None:
    """可被打断的 sleep。"""
    stop.wait(seconds)


def wait_for(cond: Callable[[], bool], timeout_s: float, *, interval_s: float = POLL_S) -> bool:
    """等一个条件成立(比如「等锁会话已经排上队」)。

    **不用固定 sleep 猜时间** —— tools/probe_lock_matrix.py 在审查里被打回过的
    就是这个:holder 还没确认拿到锁,waiter 已经抢先,测量作废却看不出来。
    """
    end = time.time() + timeout_s
    while time.time() < end:
        if cond():
            return True
        time.sleep(interval_s)
    return False


def app_name(code: str, role: str = "") -> str:
    """application_name 里的名字:`fl:K1/root`。status 与 cleanup 按 `fl:` 前缀认人。"""
    return "%s/%s" % (code, role) if role else code


def tagged(code: str, sql: str) -> str:
    """给 SQL 带上场景编号的注释,让 Top SQL / 慢 SQL 的输出里能认出是哪一项。"""
    return "/* %s%s */ %s" % (C.APP_PREFIX, code, sql)
