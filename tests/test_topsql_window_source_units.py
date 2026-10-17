"""「最近 N 分钟」怎么回答:用基线回看,还是现场采一小段(纯函数,不连库)。

**这组测试守的是一个已经在生产里炸过的 bug。** 第一版 `--window 10m` 的实现是
采一次样 → `time.sleep(600)` → 再采一次。两个硬伤:

  ① **语义反了。** 用户问的是**过去**十分钟,这个实现测的是**未来**十分钟。
     库在过去十分钟里的冲高,它一个字也答不上。
  ② **把会话挂死。** 一条在对话里阻塞十分钟的命令,实测让 opencode 直接卡住不出结果。
     用户看到的是「发了问题,没反应」。

所以下面每条测试都钉住同一条纪律:**等待量不随用户问的时长增长**,
而「实际覆盖多久」必须和「用户问的多久」分开呈现 —— 这两个数不一样时,
拿前者当后者答,是在给一个没有依据的结论。
"""
from __future__ import annotations

import pathlib
import sys

import pytest

_ROOT = pathlib.Path(__file__).resolve().parents[1]
_SCRIPTS = _ROOT / "skills" / "gaussdb-topsql" / "scripts"

for _m in ("delta", "render", "topsql"):
    sys.modules.pop(_m, None)
sys.path.insert(0, str(_SCRIPTS))
sys.path.insert(0, str(_ROOT))

import delta as D  # noqa: E402


# ---------------------------------------------------------------- 绝不等满

@pytest.mark.parametrize("asked", [600, 1800, 3600, 6 * 3600])
def test_waiting_never_grows_with_the_question(asked):
    """**本组最重要的一条。** 问「最近一小时」不代表愿意等一小时 ——
    他要的是答案,不是一个挂住的会话。没有基线时也只采 MAX_LIVE_SAMPLE_S。"""
    src = D.choose_source(asked, None)
    assert src.wait_s <= D.MAX_LIVE_SAMPLE_S


def test_a_usable_baseline_means_no_waiting_at_all():
    src = D.choose_source(600, 510)
    assert src.kind == "baseline" and src.wait_s == 0.0


def test_the_span_reported_is_the_baseline_age_not_what_was_asked():
    """基线 8.5 分钟前留的,用户问的是 10 分钟 —— 呈现必须是 8.5。
    报成 10 分钟就是把一个估计值当成测量值。"""
    src = D.choose_source(600, 510)
    assert src.span_s == pytest.approx(510)
    assert "8.5 分钟" in src.note and "10 分钟" in src.note


# ---------------------------------------------------------------- 什么时候不用基线

def test_no_baseline_falls_back_to_a_short_live_sample_and_says_why():
    src = D.choose_source(600, None)
    assert src.kind == "live" and src.wait_s == D.MAX_LIVE_SAMPLE_S
    assert "没有可用基线" in src.note and "留下基线" in src.note


def test_a_baseline_much_older_than_asked_is_not_used():
    """**这正是要解决的问题本身。** 基线是 3 小时前留的、用户问最近 10 分钟:
    拿它相减得到的是这 3 小时的平均,最近的冲高照样被拉平 ——
    用「会把冲高拉平」的办法去回答「冲高在哪」,等于没回答。"""
    src = D.choose_source(600, 3 * 3600)
    assert src.kind == "live"
    assert "拉平" in src.note


def test_a_baseline_too_fresh_is_not_used():
    """上次采样才过 3 秒,这么短的间隔里采样抖动比信号还大。"""
    src = D.choose_source(600, 3)
    assert src.kind == "live"


def test_the_boundary_between_using_and_not_using_a_baseline():
    assert D.choose_source(600, 600 * D.BASELINE_MAX_RATIO - 1).kind == "baseline"
    assert D.choose_source(600, 600 * D.BASELINE_MAX_RATIO + 1).kind == "live"


def test_a_baseline_older_than_asked_but_within_tolerance_is_used():
    """问 10 分钟、基线 20 分钟前的 —— 还在容忍范围内,用它并如实说覆盖 20 分钟,
    比现场采 60 秒更接近用户想知道的事。"""
    src = D.choose_source(600, 1200)
    assert src.kind == "baseline" and src.span_s == pytest.approx(1200)


# ---------------------------------------------------------------- 边界与坏输入

def test_a_negative_baseline_age_is_clamped_not_trusted():
    """容器与库的时钟不一致会算出负数年龄。负数会一路当成「非常新鲜」,
    必须先夹到 0 —— 夹到 0 就落进「太新,改现场采」那一支,是安全的那一边。"""
    src = D.choose_source(600, -50)
    assert src.kind == "live"


def test_a_short_question_does_not_get_a_longer_sample_than_asked():
    """问「最近 20 秒」时不该采 60 秒 —— 采样不能比问题还宽。"""
    src = D.choose_source(20, None)
    assert src.wait_s == 20


def test_the_explicit_live_cap_is_bigger_than_the_automatic_one():
    """自动兜底要小(不能挂住对话);用户点名要盯着看时才放宽,且仍然有限。"""
    assert D.MAX_LIVE_SAMPLE_S < D.MAX_LIVE_EXPLICIT_S < D.MAX_WINDOW_S


def test_a_custom_live_cap_is_respected():
    assert D.choose_source(600, None, live_cap_s=30).wait_s == 30


# ---------------------------------------------------------------- 时长的说法

@pytest.mark.parametrize("secs,text", [(0, "0 秒"), (45, "45 秒"), (89, "89 秒"),
                                       (600, "10 分钟"), (510, "8.5 分钟"),
                                       (3600, "60 分钟"), (3 * 3600, "3 小时")])
def test_spans_read_like_a_person_wrote_them(secs, text):
    assert D.human_span(secs) == text


def test_a_whole_number_of_minutes_has_no_decimal_point():
    """「问的 10.0 分钟」读着像个测量值,而那是用户自己说的数。"""
    assert D.human_span(600) == "10 分钟"


# ---------------------------------------------------------------- 真正跑一遍 _run_delta

class _Runner:
    """假取数:每次调用都把累计值往上加一点,这样两次采样之间一定算得出增量。"""

    def __init__(self):
        self.n = 0

    def run(self, script, params=None):
        self.n += 1
        return [{"unique_sql_id": "s1", "query": "SELECT 1", "calls": 10 * self.n,
                 "total_sec": 1.5 * self.n, "avg_ms": 150, "rows": self.n}]


class _Args:
    def __init__(self, **kw):
        self.by, self.limit, self.format = "time", 10, "markdown"
        self.window, self.since_last, self.live_sample = "", False, ""
        for k, v in kw.items():
            setattr(self, k, v)


def _load_topsql(monkeypatch, sleeps, tmp_path):
    """装载 topsql 并把 sleep 换成记账、把报告目录指到临时目录。"""
    monkeypatch.setenv("GSDB_REPORTS_DIR", str(tmp_path))
    for _m in ("topsql",):
        sys.modules.pop(_m, None)
    import topsql as T
    monkeypatch.setattr(T.time, "sleep", lambda s: sleeps.append(s))
    return T


def test_asking_about_ten_minutes_does_not_sleep_ten_minutes(monkeypatch, tmp_path, capsys):
    """**这正是把 opencode 挂住的那条命令。**

    `--window 10m` 原先会 `time.sleep(600)`,用户发出问题后什么都等不到。
    现在最多睡 MAX_LIVE_SAMPLE_S,而且会如实说明实际覆盖了多久。
    """
    sleeps = []
    T = _load_topsql(monkeypatch, sleeps, tmp_path)
    rc = T._run_delta(_Runner(), _Args(window="10m"), "og5")
    assert rc == 0
    assert max(sleeps or [0]) <= D.MAX_LIVE_SAMPLE_S
    out = capsys.readouterr().out
    assert "口径：" in out and "现场采" in out


def test_the_second_ask_uses_the_baseline_and_does_not_sleep_at_all(monkeypatch, tmp_path, capsys):
    """第一次问留下基线,第二次问就能真的回看 —— 一秒都不用等。"""
    sleeps = []
    T = _load_topsql(monkeypatch, sleeps, tmp_path)
    r = _Runner()
    T._run_delta(r, _Args(window="10m"), "og5")          # 留基线
    sleeps.clear()
    # 把基线的时间戳改成 5 分钟前,模拟「上次问过、过了一会儿再问」
    import delta as DD
    bpath = DD.baseline_path(pathlib.Path(str(tmp_path)), T.reports.instance_key("og5"))
    import json as _json
    import datetime as _dt
    d = _json.loads(bpath.read_text(encoding="utf-8"))
    d["at"] = (_dt.datetime.now(_dt.timezone.utc) - _dt.timedelta(minutes=5)).strftime("%Y%m%dT%H%M%SZ")
    bpath.write_text(_json.dumps(d), encoding="utf-8")

    rc = T._run_delta(r, _Args(window="10m"), "og5")
    assert rc == 0 and sleeps == []
    out = capsys.readouterr().out
    assert "最近 5 分钟" in out


def test_an_explicit_live_sample_longer_than_the_cap_is_refused(monkeypatch, tmp_path, capsys):
    """能在对话里阻塞几小时的命令是个陷阱 —— 明确拒绝,并说清怎么看更长的一段。"""
    sleeps = []
    T = _load_topsql(monkeypatch, sleeps, tmp_path)
    rc = T._run_delta(_Runner(), _Args(live_sample="1h"), "og5")
    assert rc == 2 and sleeps == []
    assert "--since-last" in capsys.readouterr().err


def test_an_explicit_live_sample_within_the_cap_still_waits(monkeypatch, tmp_path):
    """用户点名要盯着采 30 秒,那就老老实实采 30 秒。"""
    sleeps = []
    T = _load_topsql(monkeypatch, sleeps, tmp_path)
    assert T._run_delta(_Runner(), _Args(live_sample="30s"), "og5") == 0
    assert sleeps == [30]
