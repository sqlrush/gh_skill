"""时段锁回溯(lockwait --from/--to,aspchain.py)。

样本取自 og5 10-21 18:32–18:52(faultlab 第一轮)的真实 gs_asp 聚合结果,只保留链上的几对。
"""
import json
import pathlib
import sys

_ROOT = pathlib.Path(__file__).resolve().parents[1]
_SCRIPTS = _ROOT / "skills" / "gaussdb-lockwait" / "scripts"
sys.path.insert(0, str(_ROOT))
sys.path.insert(0, str(_SCRIPTS))

import pytest  # noqa: E402

import aspchain  # noqa: E402
import lockwait  # noqa: E402
from common.finding import Severity  # noqa: E402

BEGIN, END = "2026-10-21 18:32:00", "2026-10-21 18:52:00"

STATUS = [{"item": "asp_flush_rate", "value": "10"}, {"item": "asp_retention_days", "value": "2"},
          {"item": "asp_sample_interval", "value": "1"}, {"item": "enable_asp", "value": "on"},
          {"item": "samples_in_window", "value": "4680"}, {"item": "now", "value": "2026-10-22 08:48:59"}]


def _pair(w, h, ev, mode, obj, samples, first, last="2026-10-21 18:51:53", sql="3167133288",
          q="UPDATE fl.acct SET bal = bal + ? WHERE id = ?"):
    return {"waiter_sessionid": str(w), "holder_sessionid": str(h), "wait_event": ev, "waiter_mode": mode,
            "locktag": "", "lock_decoded": "", "lock_object": obj, "waiter_sql_id": sql, "waiter_query": q,
            "samples": str(samples), "first_seen": first, "last_seen": last}


T0 = "2026-10-21 18:32:34"
PAIRS = [
    _pair(5142, 5141, "transactionid", "ShareLock", "", 115, T0),
    _pair(5143, 5142, "transactionid", "ShareLock", "", 115, T0),
    _pair(5144, 5145, "tuple", "ExclusiveLock", "fl.acct", 115, T0),
    _pair(5145, 5143, "transactionid", "ShareLock", "", 115, T0),
    _pair(5146, 5144, "tuple", "ExclusiveLock", "fl.acct", 115, T0),
    _pair(5167, 5142, "tuple", "AccessExclusiveLock", "fl.acct", 105, "2026-10-21 18:34:15",
          sql="4168886180", q="SELECT id FROM fl.acct WHERE id BETWEEN ? AND ? ORDER BY id FOR UPDATE"),
    # 与上面不重叠的一对:时间上不相交,不该连进 5142 的链
    _pair(5141, 5300, "advisory", "ExclusiveLock", "", 2, "2026-10-21 18:10:00", last="2026-10-21 18:10:10"),
]
SESSIONS = [
    {"sessionid": "5141", "state": "idle in transaction", "last_event": "wait cmd", "sql_id": "",
     "query": "", "application_name": "fl:K1/root", "client_addr": "192.0.2.10",
     "xact_start": "2026-10-21 18:32:26", "samples": "115"},
    {"sessionid": "5142", "state": "active", "last_event": "transactionid", "sql_id": "3167133288",
     "query": "UPDATE fl.acct SET bal = bal + ? WHERE id = ?", "application_name": "fl:K1/mid1",
     "client_addr": "192.0.2.10", "xact_start": "2026-10-21 18:32:27", "samples": "115"},
]


class FakeRunner:
    def __init__(self, status=STATUS, pairs=PAIRS, sessions=SESSIONS):
        self.data = {aspchain.STATUS_SCRIPT: status, aspchain.PAIRS_SCRIPT: pairs,
                     aspchain.SESSIONS_SCRIPT: sessions}
        self.calls = []

    def run(self, script, values):
        self.calls.append((script, dict(values)))
        return self.data[script]


def test_chain_is_rebuilt_from_waiter_to_root():
    rep = aspchain.collect(FakeRunner(), BEGIN, END)
    assert rep.chains[0].path == (5146, 5144, 5145, 5143, 5142, 5141)
    assert rep.chains[0].lock_object == "fl.acct"
    assert rep.roots[0] == 5141
    # 5167 → 5142 → 5141 是另一条(5142 那段被长链包含,不单列)
    assert (5167, 5142, 5141) in [c.path for c in rep.chains]


def test_non_overlapping_pair_is_not_chained():
    rep = aspchain.collect(FakeRunner(), BEGIN, END)
    assert all(5300 not in c.path for c in rep.chains)


def test_wait_is_samples_times_flush_step():
    rep = aspchain.collect(FakeRunner(), BEGIN, END)
    assert rep.status.step_s == 10.0
    assert rep.chains[0].wait_s == 1150.0
    # 链的时长按链头算:5167 采到 105 次,不是链上最长那一对的 115 次
    short = next(c for c in rep.chains if c.path[0] == 5167)
    assert short.wait_s == 1050.0 and short.first == "2026-10-21 18:34:15"


def test_unsampled_root_says_why():
    """ASP 不采纯空闲会话:拿着会话级 advisory 锁的根一次也采不到,要说明,不能只写「未采到」。"""
    pairs = [_pair(5190, 5185, "advisory", "ExclusiveLock", "", 93, "2026-10-21 18:36:16",
                   sql="648622204", q="SELECT pg_advisory_lock(?)")]
    rep = aspchain.collect(FakeRunner(pairs=pairs, sessions=[]), BEGIN, END)
    md = aspchain.render_markdown(rep, aspchain.judge(rep, "Locks", 5, 60), "og")
    assert "采样不记空闲会话" in md


def test_judge_long_wait_and_idle_root():
    rep = aspchain.collect(FakeRunner(), BEGIN, END)
    fs = aspchain.judge(rep, lockwait.DIM, lockwait.WAIT_WARN_S, lockwait.WAIT_CRIT_S)
    codes = {f.code: f for f in fs}
    assert codes["LOCK_WAIT_LONG"].severity == Severity.CRITICAL
    assert codes["LOCK_WAIT_LONG"].value == "约 1150s"
    assert codes["LOCK_ROOT_IDLE_XACT"].value == "会话 5141"
    assert "挡住 6 个会话" in codes["LOCK_ROOT_IDLE_XACT"].evidence
    assert "fl:K1/root" in codes["LOCK_ROOT_IDLE_XACT"].evidence
    # 2026-10-22 实测:模型对过去时段的根会话建议 pg_terminate_backend —— 证据里要写明不据此 kill
    assert "不要据此 kill" in codes["LOCK_ROOT_IDLE_XACT"].evidence


def test_asp_off_is_not_reported_as_no_waits():
    st = [dict(r) for r in STATUS]
    st[3] = {"item": "enable_asp", "value": "off"}
    r = FakeRunner(status=st)
    rep = aspchain.collect(r, BEGIN, END)
    assert "enable_asp=off" in rep.unavailable
    assert [c[0] for c in r.calls] == [aspchain.STATUS_SCRIPT]
    assert aspchain.judge(rep, "Locks", 5, 60) == []


def test_expired_window_says_retention():
    st = [dict(r) for r in STATUS]
    st[4] = {"item": "samples_in_window", "value": "0"}
    st[5] = {"item": "now", "value": "2026-10-25 08:00:00"}
    rep = aspchain.collect(FakeRunner(status=st), BEGIN, END)
    assert "保留期" in rep.unavailable


def test_no_waits_in_samples_is_explicit():
    rep = aspchain.collect(FakeRunner(pairs=[]), BEGIN, END)
    md = aspchain.render_markdown(rep, [], "og")
    assert "没有采到锁等待" in md and "4680" in md


def test_markdown_has_chain_roots_and_no_kill(monkeypatch):
    rep = aspchain.collect(FakeRunner(), BEGIN, END)
    fs = aspchain.judge(rep, "Locks", 5, 60)
    md = aspchain.render_markdown(rep, fs, "og")
    assert "5146 → 5144 → 5145 → 5143 → 5142 → **5141（根，idle in transaction）**" in md
    assert "pg_terminate_session(" not in md and "pg_cancel_session(" not in md
    assert "估算" in md


def test_cli_span_json(monkeypatch, capsys):
    monkeypatch.setattr(lockwait.access, "for_conn", lambda *a, **k: FakeRunner())
    monkeypatch.setattr(lockwait.cli, "apply_session_arg", lambda args: None)
    rc = lockwait.main(["--from", "2026-10-21 18:32", "--to", "2026-10-21 18:52", "--format", "json"])
    assert rc == 0
    d = json.loads(capsys.readouterr().out)
    assert d["skill"] == "gaussdb-lockwait"
    assert d["asp"]["chains"][0]["path"] == [5146, 5144, 5145, 5143, 5142, 5141]
    assert {f["code"] for f in d["findings"]} >= {"LOCK_WAIT_LONG", "LOCK_ROOT_IDLE_XACT"}


def test_cli_span_passes_seconds_to_whitelist(monkeypatch, capsys):
    r = FakeRunner()
    monkeypatch.setattr(lockwait.access, "for_conn", lambda *a, **k: r)
    monkeypatch.setattr(lockwait.cli, "apply_session_arg", lambda args: None)
    lockwait.main(["--from", "2026-10-21 18:32", "--to", "2026-10-21 18:52", "--format", "json"])
    assert r.calls[0][1] == {"begin_time": BEGIN, "end_time": END}


@pytest.mark.parametrize("bad", ["10-21 18:32", "2026/10/21 18:32", "昨晚"])
def test_cli_span_rejects_bad_time(monkeypatch, capsys, bad):
    monkeypatch.setattr(lockwait.access, "for_conn", lambda *a, **k: FakeRunner())
    monkeypatch.setattr(lockwait.cli, "apply_session_arg", lambda args: None)
    assert lockwait.main(["--from", bad]) == 2
    assert "格式不对" in capsys.readouterr().err


def test_pairs_are_fetched_in_full_not_by_display_limit(monkeypatch, capsys):
    """只取前 20 对会把链从中间截断,断点被当成根(2026-10-22 第二轮实测)。"""
    r = FakeRunner()
    monkeypatch.setattr(lockwait.access, "for_conn", lambda *a, **k: r)
    monkeypatch.setattr(lockwait.cli, "apply_session_arg", lambda args: None)
    lockwait.main(["--from", "2026-10-21 18:32", "--to", "2026-10-21 18:52", "--limit", "2"])
    pairs_call = next(v for s, v in r.calls if s == aspchain.PAIRS_SCRIPT)
    assert pairs_call["limit"] == aspchain.FETCH_LIMIT
    out = capsys.readouterr().out
    assert "共 7 对，按采到次数列了前 2 对" in out
    assert "5146 → 5144 → 5145 → 5143 → 5142 → **5141" in out, "展示截断不影响连链"
