"""waitevent 入口。用假 runner，不连库。"""
import json
import pathlib
import sys

_ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT))
sys.path.insert(0, str(_ROOT / "skills" / "gaussdb-waitevent" / "scripts"))

import waitevent  # noqa: E402


def _snaps(ids):
    return [{"snapshot_id": str(i)} for i in ids]


# dbtime.breakdown() 要求 snap_global_instance_time 十项（DB_TIME + 九项）齐全，
# 缺一项就整体抛 ValueError（"DB time 分解缺少 [...]"）—— 不是当 0 处理。
# 所以这里十项必须全给，PARSE_TIME / PLAN_TIME / REWRITE_TIME /
# PL_EXECUTION_TIME / PL_COMPILATION_TIME 这五项本组测试不关心，给 0，
# 不影响 DATA_IO_TIME=35%、CPU_TIME=40%、NET_SEND_TIME=10% 这几个阈值判定。
_TIME_ROWS = [
    {"stat_name": "DB_TIME", "delta_us": "1000000"},
    {"stat_name": "EXECUTION_TIME", "delta_us": "700000"},
    {"stat_name": "CPU_TIME", "delta_us": "400000"},
    {"stat_name": "DATA_IO_TIME", "delta_us": "350000"},
    {"stat_name": "NET_SEND_TIME", "delta_us": "100000"},
    {"stat_name": "PARSE_TIME", "delta_us": "0"},
    {"stat_name": "PLAN_TIME", "delta_us": "0"},
    {"stat_name": "REWRITE_TIME", "delta_us": "0"},
    {"stat_name": "PL_EXECUTION_TIME", "delta_us": "0"},
    {"stat_name": "PL_COMPILATION_TIME", "delta_us": "0"},
]

_EVENT_ROWS = [
    {"wait_class": "IO_EVENT", "event": "DataFileRead",
     "waits": "100", "wait_us": "120000"},
    {"wait_class": "LOCK_EVENT", "event": "relation",
     "waits": "3", "wait_us": "150000"},
]


class _Runner:
    def __init__(self, snap_ids=(1, 2, 3, 4, 5, 6), time_rows=None, event_rows=None):
        self._snap_ids = list(snap_ids)
        self._time = _TIME_ROWS if time_rows is None else time_rows
        self._events = _EVENT_ROWS if event_rows is None else event_rows
        self.calls = []

    def run(self, script, values=None):
        self.calls.append((script, dict(values or {})))
        if script == "wdr.snapshots":
            return _snaps(self._snap_ids)
        if script == "wdr.window":
            return [{"b_start": "2026-08-11 10:00", "e_start": "2026-08-11 11:00",
                     "dur": "60"}]
        if script == "waitevent.instance_time":
            return self._time
        if script == "waitevent.events":
            return self._events
        raise AssertionError("没料到的脚本 %s" % script)


def test_normal_report(monkeypatch, capsys):
    monkeypatch.setattr(waitevent.access, "for_conn", lambda *a, **k: _Runner())
    rc = waitevent.main(["-c", "x"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "DB_TIME" in out


def test_six_snapshots_make_five_windows():
    """最近 6 个快照 → 5 个窗口。相邻两两成对，不是首尾一对。"""
    r = _Runner(snap_ids=(11, 12, 13, 14, 15, 16))
    rep = waitevent.collect(r, snapshots=6)
    assert len(rep.windows) == 5
    pairs = [(c[1]["b"], c[1]["e"]) for c in r.calls
             if c[0] == "waitevent.instance_time"]
    assert pairs == [(11, 12), (12, 13), (13, 14), (14, 15), (15, 16)]


def test_only_the_most_recent_snapshots_are_used():
    """给了 10 个快照但要 3 个 —— 用最近的 3 个，不是最早的。"""
    r = _Runner(snap_ids=tuple(range(1, 11)))
    waitevent.collect(r, snapshots=3)
    pairs = [(c[1]["b"], c[1]["e"]) for c in r.calls
             if c[0] == "waitevent.instance_time"]
    assert pairs == [(8, 9), (9, 10)]


def test_explicit_begin_end_skips_auto_selection():
    r = _Runner(snap_ids=(1, 2, 3, 4, 5, 6))
    rep = waitevent.collect(r, snapshots=6, begin=2, end=3)
    assert len(rep.windows) == 1
    assert not any(c[0] == "wdr.snapshots" for c in r.calls), \
        "显式给了窗口就不该再去列快照"


def test_too_few_snapshots_is_an_explicit_error(monkeypatch, capsys):
    """**不是空报告。** 一份空报告会被读成「这段时间没问题」。"""
    monkeypatch.setattr(waitevent.access, "for_conn",
                        lambda *a, **k: _Runner(snap_ids=(7,)))
    rc = waitevent.main(["-c", "x"])
    err = capsys.readouterr().err
    assert rc == 2
    assert "至少需要 2 个快照" in err
    assert "Traceback" not in err


def test_json_output_is_the_finding_contract(monkeypatch, capsys):
    monkeypatch.setattr(waitevent.access, "for_conn", lambda *a, **k: _Runner())
    rc = waitevent.main(["-c", "x", "--format", "json"])
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["skill"] == "gaussdb-waitevent"
    for f in payload["findings"]:
        assert f["skill"] == "gaussdb-waitevent"
        assert isinstance(f["severity"], int)


def test_io_heavy_window_produces_a_finding(monkeypatch, capsys):
    """DATA_IO_TIME 占 35%（阈值 30%）→ 该报 DBTIME_IO_HEAVY。"""
    monkeypatch.setattr(waitevent.access, "for_conn", lambda *a, **k: _Runner())
    waitevent.main(["-c", "x", "--format", "json"])
    payload = json.loads(capsys.readouterr().out)
    assert any(f["code"] == "DBTIME_IO_HEAVY" for f in payload["findings"])


def test_restarted_window_reports_unavailable_not_percentages(monkeypatch, capsys):
    """**跨实例重启的窗口只报不可用。** 负增量算出的比例是假的，
    报出去比不报更糟 —— 它看起来是个正常数字。"""
    # 同样要十项齐全（见 _TIME_ROWS 上面的说明）；全部给负数才是一次真实的
    # 计数器清零重来，不是只挑一两项。
    rows = [
        {"stat_name": "DB_TIME", "delta_us": "-500"},
        {"stat_name": "EXECUTION_TIME", "delta_us": "-450"},
        {"stat_name": "CPU_TIME", "delta_us": "-100"},
        {"stat_name": "DATA_IO_TIME", "delta_us": "-80"},
        {"stat_name": "NET_SEND_TIME", "delta_us": "-20"},
        {"stat_name": "PARSE_TIME", "delta_us": "-5"},
        {"stat_name": "PLAN_TIME", "delta_us": "-5"},
        {"stat_name": "REWRITE_TIME", "delta_us": "-5"},
        {"stat_name": "PL_EXECUTION_TIME", "delta_us": "-5"},
        {"stat_name": "PL_COMPILATION_TIME", "delta_us": "-5"},
    ]
    monkeypatch.setattr(waitevent.access, "for_conn",
                        lambda *a, **k: _Runner(time_rows=rows))
    waitevent.main(["-c", "x", "--format", "json"])
    payload = json.loads(capsys.readouterr().out)
    codes = {f["code"] for f in payload["findings"]}
    assert "DBTIME_RESTART" in codes
    assert not (codes & {"DBTIME_IO_HEAVY", "DBTIME_CPU_HEAVY", "DBTIME_NET_HEAVY"})


def test_lock_wait_share_produces_a_finding(monkeypatch, capsys):
    """时间模型里没有锁 —— 锁的占比来自等待事件（15% > 10% 阈值）。"""
    monkeypatch.setattr(waitevent.access, "for_conn", lambda *a, **k: _Runner())
    waitevent.main(["-c", "x", "--format", "json"])
    payload = json.loads(capsys.readouterr().out)
    assert any(f["code"] == "WAIT_LOCK_HEAVY" for f in payload["findings"])


def test_query_failure_is_reported_not_thrown(monkeypatch, capsys):
    from common.grmp.errors import QueryError

    class _Boom:
        def run(self, *a, **k):
            raise QueryError("ERROR: relation does not exist (SQLSTATE 42P01)")

    monkeypatch.setattr(waitevent.access, "for_conn", lambda *a, **k: _Boom())
    rc = waitevent.main(["-c", "x"])
    err = capsys.readouterr().err
    assert rc == 2
    assert "Traceback" not in err
    assert "SQLSTATE" in err
