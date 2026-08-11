"""lockwait 入口。用假 runner，不连库。"""
import io
import json
import pathlib
import sys

import pytest

_ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT))
sys.path.insert(0, str(_ROOT / "skills" / "gaussdb-lockwait" / "scripts"))

import lockwait  # noqa: E402
from common.finding import Severity  # noqa: E402


def _pair(**kw):
    base = dict(waiter_pid="1002", waiter_sessionid="2260",
                waiter_mode="AccessShareLock",
                holder_pid="1001", holder_sessionid="2259",
                holder_mode="AccessExclusiveLock",
                locktype="relation", lock_object="public.t",
                locktag="3985:b2123:0:0:0:0", waiter_wait_s="4.0",
                waiter_user="app", waiter_app="gsql",
                waiter_query="SELECT count(*) FROM t",
                holder_state="active", holder_user="gaussdb",
                holder_app="gsql", holder_xact_age_s="10.0",
                holder_query="LOCK TABLE t IN ACCESS EXCLUSIVE MODE")
    base.update(kw)
    return base


class _Runner:
    def __init__(self, pairs=None, edges=None):
        self._pairs = pairs if pairs is not None else []
        self._edges = edges if edges is not None else []

    def run(self, script, values=None):
        if script == "lockwait.pairs":
            return self._pairs
        if script == "lockwait.chain":
            return self._edges
        raise AssertionError("没料到的脚本 %s" % script)


def test_no_blocking_is_reported_explicitly(monkeypatch, capsys):
    """**没有锁堵塞是正常状态。** 必须明说，不能留空白 ——
    空白会被读成「这项没查」。"""
    monkeypatch.setattr(lockwait.access, "for_conn", lambda *a, **k: _Runner())
    rc = lockwait.main(["-c", "x"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "当前无锁等待" in out


def test_pair_reports_both_modes_and_the_conflict_reason():
    rep = lockwait.collect(_Runner(pairs=[_pair()]), limit=20)
    md = lockwait.render_markdown(rep)
    assert "AccessExclusiveLock" in md and "AccessShareLock" in md
    assert "互斥" in md


def test_root_is_the_top_of_the_chain_not_the_direct_blocker():
    """3 等 2、2 等 1 —— 根是 1。杀 2 不解堵。"""
    edges = [{"sessionid": "3", "block_sessionid": "2"},
             {"sessionid": "2", "block_sessionid": "1"}]
    rep = lockwait.collect(_Runner(pairs=[_pair(waiter_sessionid="3")], edges=edges), limit=20)
    assert rep.roots[3] == 1


def test_deadlock_is_critical():
    edges = [{"sessionid": "1", "block_sessionid": "2"},
             {"sessionid": "2", "block_sessionid": "1"}]
    rep = lockwait.collect(_Runner(pairs=[_pair()], edges=edges), limit=20)
    codes = {f.code: f.severity for f in rep.findings}
    assert codes.get("LOCK_DEADLOCK") is Severity.CRITICAL


def test_long_wait_is_critical():
    rep = lockwait.collect(_Runner(pairs=[_pair(waiter_wait_s="90.0")]), limit=20)
    assert any(f.code == "LOCK_WAIT_LONG" and f.severity is Severity.CRITICAL
               for f in rep.findings)


def test_short_wait_is_only_a_notice():
    rep = lockwait.collect(_Runner(pairs=[_pair(waiter_wait_s="1.0")]), limit=20)
    sevs = {f.code: f.severity for f in rep.findings}
    assert sevs.get("LOCK_BLOCKED") is Severity.NOTICE
    assert "LOCK_WAIT_LONG" not in sevs


def test_idle_in_transaction_root_is_flagged():
    rep = lockwait.collect(
        _Runner(pairs=[_pair(holder_state="idle in transaction")]), limit=20)
    assert any(f.code == "LOCK_ROOT_IDLE_XACT" for f in rep.findings)


def test_json_output_is_the_finding_contract(monkeypatch, capsys):
    """health 汇总认的就是这个形状。"""
    monkeypatch.setattr(lockwait.access, "for_conn",
                        lambda *a, **k: _Runner(pairs=[_pair(waiter_wait_s="90.0")]))
    rc = lockwait.main(["-c", "x", "--format", "json"])
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["skill"] == "gaussdb-lockwait"
    assert payload["findings"][0]["skill"] == "gaussdb-lockwait"
    assert isinstance(payload["findings"][0]["severity"], int)


def test_query_failure_is_reported_not_thrown(monkeypatch, capsys):
    """取数失败要给错误信息，不能吐 Traceback。"""
    from common.grmp.errors import QueryError

    class _Boom:
        def run(self, *a, **k):
            raise QueryError("ERROR: permission denied (SQLSTATE 42501)")

    monkeypatch.setattr(lockwait.access, "for_conn", lambda *a, **k: _Boom())
    rc = lockwait.main(["-c", "x"])
    err = capsys.readouterr().err
    assert rc == 2
    assert "Traceback" not in err
    assert "SQLSTATE" in err


# ---------------------------------------------------------------------------
# 四点核对（brief 写成时 Task 4-6 还没定型，这里补测新出现的行为）
# ---------------------------------------------------------------------------

def test_non_conflicting_holder_is_filtered_out_as_innocent_bystander():
    """lockwait.pairs 按 locktag 自连接，会把同一把锁上模式其实不冲突的
    holder 也配进来（无辜旁观者）——一个真冲突 + 一个旁观者，旁观者不能
    出现在报告里。"""
    real = _pair()  # AccessShare(waiter) vs AccessExclusive(holder) —— 真冲突
    bystander = _pair(holder_pid="9001", holder_sessionid="9001",
                       holder_mode="AccessShareLock")  # 与 AccessShare 不冲突
    rep = lockwait.collect(_Runner(pairs=[real, bystander]), limit=20)
    assert len(rep.pairs) == 1
    assert rep.pairs[0]["holder_sessionid"] == "2259"


def test_all_bystanders_leave_pairs_empty_and_report_says_so():
    bystander = _pair(holder_mode="AccessShareLock")  # 与默认 waiter 模式不冲突
    rep = lockwait.collect(_Runner(pairs=[bystander]), limit=20)
    assert rep.pairs == []
    md = lockwait.render_markdown(rep)
    assert "当前无锁等待" in md


def test_deadlock_with_no_pairs_does_not_claim_no_waiting():
    """pairs 和 chain 是两条独立查询，中间有时间差：理论上可能查到死锁环、
    却拿不到对应的持有者/等待者明细。这时不能说"当前无锁等待"——
    那与死锁环矛盾，比留空更误导。"""
    edges = [{"sessionid": "1", "block_sessionid": "2"},
             {"sessionid": "2", "block_sessionid": "1"}]
    rep = lockwait.collect(_Runner(pairs=[], edges=edges), limit=20)
    assert rep.pairs == []
    assert rep.deadlocks
    md = lockwait.render_markdown(rep)
    assert "当前无锁等待" not in md
    assert "死锁" in md


def test_unknown_lock_mode_is_kept_not_silently_dropped():
    """矩阵不认识的模式是数据库给出的真实信号，不能因为判不出冲突就当成
    旁观者悄悄丢掉；也不能让 KeyError 冒穿到用户面前。"""
    weird = _pair(holder_mode="SomeBrandNewModeNobodyHasSeen")
    rep = lockwait.collect(_Runner(pairs=[weird]), limit=20)
    assert len(rep.pairs) == 1
    md = lockwait.render_markdown(rep)  # 不能抛
    assert "SomeBrandNewModeNobodyHasSeen" in md


def test_null_wait_duration_is_not_folded_into_zero():
    """**真实协议把 NULL 渲染成空串，不是 Python None**
    （common/grmp/serialize.py: render_cell 对 None 走 settings.null_text，
    默认就是空串；common/grmp/values.py 的 is_null() 就是为这件事存在的）。
    空串形式的未知时长不能被显示成 0，也不能让严重度判定把它当"不严重"。"""
    rep = lockwait.collect(_Runner(pairs=[_pair(waiter_wait_s="")]), limit=20)
    md = lockwait.render_markdown(rep)
    assert "0.0" not in md
    codes = {f.code: f.severity for f in rep.findings}
    assert codes.get("LOCK_WAIT_LONG") is Severity.CRITICAL


def test_none_wait_duration_is_also_handled_defensively():
    """is_null() 同时认 None 和空串——万一某天来了个走原生类型的驱动，
    照样不能被当 0。"""
    rep = lockwait.collect(_Runner(pairs=[_pair(waiter_wait_s=None)]), limit=20)
    md = lockwait.render_markdown(rep)
    assert "0.0" not in md
    codes = {f.code: f.severity for f in rep.findings}
    assert codes.get("LOCK_WAIT_LONG") is Severity.CRITICAL


def test_zero_wait_duration_is_shown_as_zero_not_unknown():
    """反方向的坑：真正的 0 不能被当成"取不到"——0 秒等待信息量很大
    （cancel 代价最小的那种情形），`0 or 默认值` 那种写法会把它吞掉。"""
    rep = lockwait.collect(_Runner(pairs=[_pair(waiter_wait_s="0.0")]), limit=20)
    md = lockwait.render_markdown(rep)
    assert "0.0" in md
    assert "未知" not in md
    sevs = {f.code: f.severity for f in rep.findings}
    assert sevs.get("LOCK_BLOCKED") is Severity.NOTICE


def test_idle_xact_with_unknown_age_does_not_print_literal_none():
    rep = lockwait.collect(
        _Runner(pairs=[_pair(holder_state="idle in transaction",
                              holder_xact_age_s="")]), limit=20)
    f = next(f for f in rep.findings if f.code == "LOCK_ROOT_IDLE_XACT")
    assert "None" not in f.evidence


def _root_edge():
    """让默认 _pair() 里的 holder（2259）在 chain 里被识别成根 ——
    kill 语句只对根 holder 生成，不给这条边就永远没有 kill 语句可测。"""
    return [{"sessionid": "2260", "block_sessionid": "2259"}]


def test_kill_statements_use_the_two_argument_session_aware_functions():
    """recovery.py 生成的函数已经改名——两个参数、会话感知，
    不是单参数的 pg_cancel_backend/pg_terminate_backend。"""
    rep = lockwait.collect(_Runner(pairs=[_pair()], edges=_root_edge()), limit=20)
    md = lockwait.render_markdown(rep)
    assert "pg_cancel_session(" in md or "pg_terminate_session(" in md
    assert "pg_cancel_backend(" not in md
    assert "pg_terminate_backend(" not in md


def test_garbled_holder_pid_does_not_crash_kill_generation():
    """recovery.kill_for() 内部是裸 int(x or 0)——真实查询结果都是字符串，
    非数字字符串会让它抛。lockwait 是第一个把真实取数结果接进 kill_for()
    的调用方，责任在这一层：不能让这个 Traceback 冒到用户面前，也不能
    编一个假 pid 生成一条看着正常、其实指向别的会话的 kill 语句。"""
    rep = lockwait.collect(
        _Runner(pairs=[_pair(holder_pid="not-a-number")], edges=_root_edge()),
        limit=20)
    md = lockwait.render_markdown(rep)  # 不能抛
    assert "not-a-number" in md or "无法生成" in md


def test_main_does_not_crash_on_any_of_the_above_via_json_format(monkeypatch, capsys):
    """--format json 走的是同一条 collect() 路径，同样不能抛。"""
    pairs = [_pair(holder_mode="SomeBrandNewModeNobodyHasSeen",
                    waiter_wait_s="", holder_pid="not-a-number")]
    monkeypatch.setattr(lockwait.access, "for_conn",
                        lambda *a, **k: _Runner(pairs=pairs))
    rc = lockwait.main(["-c", "x", "--format", "json"])
    err = capsys.readouterr().err
    assert rc == 0
    assert "Traceback" not in err
