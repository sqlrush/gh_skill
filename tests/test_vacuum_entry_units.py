"""vacuum 入口。用假 runner，不连库。

见 tests/test_vacuum_rules_units.py 关于 rules/thresholds 模块名冲突的处理——
这里同样先把 'rules'/'thresholds' 从 sys.modules 里踢出去，再插入
gaussdb-vacuum/scripts 到 sys.path 最前面，避免整套件一起跑时拿到别的 skill
的同名模块。
"""
import json
import pathlib
import sys

_ROOT = pathlib.Path(__file__).resolve().parents[1]
_SCRIPTS = _ROOT / "skills" / "gaussdb-vacuum" / "scripts"

for _m in ("rules", "thresholds", "render", "vacuum"):
    sys.modules.pop(_m, None)
sys.path.insert(0, str(_SCRIPTS))
sys.path.insert(0, str(_ROOT))

import vacuum  # noqa: E402
from common.finding import Severity  # noqa: E402

MB = 1024 * 1024

_SETTINGS_ROWS = [
    {"name": "autovacuum", "setting": "on"},
    {"name": "autovacuum_naptime", "setting": "30"},
    {"name": "autovacuum_max_workers", "setting": "3"},
    {"name": "autovacuum_vacuum_threshold", "setting": "50"},
    {"name": "autovacuum_vacuum_scale_factor", "setting": "0.2"},
]


def _dead_row(**kw):
    base = dict(schema="public", table="t", n_live_tup="1000", n_dead_tup="100",
                reltuples="1000", table_bytes=str(200 * MB),
                last_autovacuum_age_s="", last_vacuum_age_s="",
                vacuum_count="0", autovacuum_count="0",
                autovac_enabled="t", reloptions="")
    base.update(kw)
    return base


def _xmin_row(**kw):
    base = dict(source="long_xact", identifier="2259", xmin_age_s="3600",
                detail="usename=app state=idle in transaction")
    base.update(kw)
    return base


def _worker_row(**kw):
    base = dict(pid="4001", sessionid="9001", xact_age_s="12",
                query="autovacuum: VACUUM public.t")
    base.update(kw)
    return base


class _Runner:
    def __init__(self, dead=None, settings=None, workers=None, xmin=None):
        self._dead = dead if dead is not None else []
        self._settings = settings if settings is not None else _SETTINGS_ROWS
        self._workers = workers if workers is not None else []
        self._xmin = xmin if xmin is not None else []

    def run(self, script, values=None):
        if script == vacuum.DEAD_SCRIPT:
            return self._dead
        if script == vacuum.SETTINGS_SCRIPT:
            return self._settings
        if script == vacuum.WORKERS_SCRIPT:
            return self._workers
        if script == vacuum.XMIN_SCRIPT:
            return self._xmin
        raise AssertionError("没料到的脚本 %s" % script)


def _th():
    return vacuum.rules.default_thresholds()


# ---------------------------------------------------------------------------
# 1. 空结果必须明说 —— 风险表 / worker 两处都要
# ---------------------------------------------------------------------------

def test_no_risk_tables_says_so_explicitly_not_blank():
    """无风险表时必须明确写「未发现死元组风险表」，不是空白。"""
    # 唯一一行死元组不大，任何规则都不命中。
    rep = vacuum.collect(_Runner(dead=[_dead_row(n_dead_tup="10",
                                                  last_autovacuum_age_s="30")]),
                         limit=20, th=_th())
    md = vacuum.render_markdown(rep)
    assert "未发现死元组风险表" in md


def test_empty_dead_tuples_result_also_says_no_risk_tables():
    rep = vacuum.collect(_Runner(dead=[]), limit=20, th=_th())
    md = vacuum.render_markdown(rep)
    assert "未发现死元组风险表" in md


def test_no_running_worker_says_so_explicitly_not_blank():
    rep = vacuum.collect(_Runner(workers=[]), limit=20, th=_th())
    md = vacuum.render_markdown(rep)
    assert "当前没有正在运行的 autovacuum 线程" in md


def test_running_worker_is_listed():
    rep = vacuum.collect(_Runner(workers=[_worker_row(pid="4321")]),
                         limit=20, th=_th())
    md = vacuum.render_markdown(rep)
    assert "4321" in md
    assert "当前没有正在运行的 autovacuum 线程" not in md


# ---------------------------------------------------------------------------
# 2. 风险表每行显示命中了哪几条规则
# ---------------------------------------------------------------------------

def test_risk_row_shows_which_rules_it_hit():
    """一张同时命中 R1 与 R3 的表（照抄 plan_data 的量级）。"""
    row = _dead_row(schema="gsbench_e2e_20260801_100g", table="plan_data",
                    n_live_tup="20178297", n_dead_tup="20087028",
                    reltuples="20178297", table_bytes=str(8 * 1024 * MB),
                    last_autovacuum_age_s="", autovacuum_count="0")
    rep = vacuum.collect(_Runner(dead=[row]), limit=20, th=_th())
    md = vacuum.render_markdown(rep)
    assert "plan_data" in md
    assert "R1" in md
    assert "R3" in md


def test_risk_row_includes_the_computed_trigger_line():
    """触发线是现算的，不是写死常量：50 + 0.2 × 1000 = 250。"""
    row = _dead_row(n_live_tup="1000", n_dead_tup="1000", reltuples="1000",
                    table_bytes=str(200 * MB))
    rep = vacuum.collect(_Runner(dead=[row]), limit=20, th=_th())
    assert rep.tables[0]["trigger_line"] == 250.0
    md = vacuum.render_markdown(rep)
    assert "250" in md


# ---------------------------------------------------------------------------
# 3. R4 命中时报告出现「先处理该事务」的措辞
# ---------------------------------------------------------------------------

def test_r4_hit_produces_the_handle_the_transaction_first_wording():
    row = _dead_row(n_dead_tup="1000", n_live_tup="1000", table_bytes=str(200 * MB))
    rep = vacuum.collect(_Runner(dead=[row], xmin=[_xmin_row()]),
                         limit=20, th=_th())
    md = vacuum.render_markdown(rep)
    assert "R4" in md
    assert "先处理该事务" in md


# ---------------------------------------------------------------------------
# 4. 本 skill 不给空间回收量估算 —— 报告里绝不能出现这类字样
# ---------------------------------------------------------------------------

_FORBIDDEN_RECLAIM_PHRASES = ("可回收", "预计释放", "预计可回收", "可释放")


def test_report_never_estimates_reclaimable_space():
    """跑一份「什么都有」的报告（风险表 + xmin 阻塞 + worker），扫描全文
    绝不能出现任何空间回收量的预估措辞——那要真跑一次才知道，猜测值摆在
    一堆真实测量值旁边会被当成承诺来读。"""
    row = _dead_row(schema="gsbench_e2e_20260801_100g", table="plan_data",
                    n_live_tup="20178297", n_dead_tup="20087028",
                    reltuples="20178297", table_bytes=str(8 * 1024 * MB),
                    last_autovacuum_age_s="", autovacuum_count="0")
    rep = vacuum.collect(
        _Runner(dead=[row], xmin=[_xmin_row()],
               workers=[_worker_row()]),
        limit=20, th=_th())
    md = vacuum.render_markdown(rep)
    for phrase in _FORBIDDEN_RECLAIM_PHRASES:
        assert phrase not in md, "报告里出现了空间回收量预估措辞：%r" % phrase


def test_report_never_estimates_reclaimable_space_even_with_no_risk_tables():
    rep = vacuum.collect(_Runner(dead=[]), limit=20, th=_th())
    md = vacuum.render_markdown(rep)
    for phrase in _FORBIDDEN_RECLAIM_PHRASES:
        assert phrase not in md


# ---------------------------------------------------------------------------
# 5. --format json 的 skill == "gaussdb-vacuum"
# ---------------------------------------------------------------------------

def test_json_output_skill_field(monkeypatch, capsys):
    monkeypatch.setattr(vacuum.access, "for_conn", lambda *a, **k: _Runner())
    rc = vacuum.main(["-c", "x", "--format", "json"])
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["skill"] == "gaussdb-vacuum"


def test_json_output_findings_carry_int_severity(monkeypatch, capsys):
    row = _dead_row(n_dead_tup="1000", n_live_tup="1000", table_bytes=str(200 * MB))
    monkeypatch.setattr(vacuum.access, "for_conn",
                        lambda *a, **k: _Runner(dead=[row]))
    rc = vacuum.main(["-c", "x", "--format", "json"])
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["findings"], "R1/R3 应该都命中，findings 不该是空的"
    for f in payload["findings"]:
        assert isinstance(f["severity"], int)


# ---------------------------------------------------------------------------
# 6. R4 的报告必须独立于任何表是否命中——这是本任务在 brief 之外新加的
#    要求：rules.evaluate() 里 R4 的判定门槛是「这张表本身有没有死元组」
#    （n_dead > 0），这条门槛对逐表规则是对的，但会漏掉「所有表当前死元组
#    都是 0，但确实有一个更老的事务/复制槽正堵着回收」这一类现场——
#    这时候没有任何一张表会命中 R4，如果报告只在某张表命中 R4 时才提
#    xmin 阻塞源，这类现场会从报告里彻底消失。
#
#    这条测试直接构造「every table 的 n_dead_tup 都是 0（R4 gate 必然不
#    命中）、但 vacuum.oldest_xmin 有行」的场景，钉住：xmin 阻塞源的
#    报告只看 rep.oldest_xmin 是否非空，不看任何表的 hits 里有没有 R4。
# ---------------------------------------------------------------------------

def test_xmin_blocker_is_reported_even_when_no_table_hits_r4():
    """所有表死元组都是 0（R4 的 n_dead>0 门槛必然不命中），但确实存在一个
    卡住回收的长事务——报告必须仍然提到这个阻塞源，不能因为没有表命中
    R4 就整段消失。"""
    zero_dead_row = _dead_row(n_dead_tup="0", n_live_tup="1000")
    rep = vacuum.collect(
        _Runner(dead=[zero_dead_row], xmin=[_xmin_row(identifier="7777")]),
        limit=20, th=_th())
    # 前提：R4 在这张表上确实没有命中，测试才有意义。
    assert "R4" not in rep.tables[0]["hits"]
    assert not any("R4" in t["hits"] for t in rep.tables)
    md = vacuum.render_markdown(rep)
    assert "7777" in md, "xmin 阻塞源的标识没有出现在报告里"
    assert "VACUUM" in md


def test_xmin_blocker_is_reported_even_with_zero_raw_dead_tuple_rows():
    """更极端的一种：vacuum.dead_tuples 干脆一行都没返回（当前没有任何用户
    表——或者都被过滤掉了），但 oldest_xmin 有行。报告依然要提到阻塞源。"""
    rep = vacuum.collect(_Runner(dead=[], xmin=[_xmin_row(identifier="8888")]),
                         limit=20, th=_th())
    md = vacuum.render_markdown(rep)
    assert "8888" in md
    assert "VACUUM" in md


def test_no_xmin_blocker_also_says_so_explicitly():
    rep = vacuum.collect(_Runner(xmin=[]), limit=20, th=_th())
    md = vacuum.render_markdown(rep)
    assert "阻塞" in md  # 该小节本身要出现，且要明说「没有」
    assert "7777" not in md and "8888" not in md


# ---------------------------------------------------------------------------
# 7. 复制槽的 xmin_age_s 恒为空——报告必须说「未知」，绝不能读成「刚连上，
#    问题不大」，更不能编一个数字冒充年龄。
# ---------------------------------------------------------------------------

def test_replication_slot_blocker_reports_age_as_unavailable_not_recent():
    rep = vacuum.collect(
        _Runner(xmin=[_xmin_row(source="replication_slot",
                                identifier="rep_slot_1", xmin_age_s="")]),
        limit=20, th=_th())
    md = vacuum.render_markdown(rep)
    assert "rep_slot_1" in md
    assert "复制槽" in md
    assert "未知" in md
    # 绝不能让空值被 as_float 的默认值悄悄折成 0，显示成「已持续 0 秒」
    # 那种看起来「刚发生、无害」的措辞。
    assert "已持续 0 秒" not in md
    assert "0 秒" not in md


def test_long_xact_blocker_with_a_real_age_is_not_reported_as_unknown():
    """反方向：真有一个数字时不能被误判成「未知」。"""
    rep = vacuum.collect(
        _Runner(xmin=[_xmin_row(source="long_xact", identifier="2259",
                                xmin_age_s="3600")]),
        limit=20, th=_th())
    md = vacuum.render_markdown(rep)
    assert "2259" in md
    assert "3600" in md
    # 这一行本身不应该被打上「未知」标记
    for line in md.splitlines():
        if "2259" in line:
            assert "未知" not in line


# ---------------------------------------------------------------------------
# 8. NULL 与 0 是两个相反的事实：last_autovacuum_age_s 为 NULL 代表「从未
#    autovacuum 过」，为 0 代表「刚跑完」。协议把 NULL 渲染成空串。
# ---------------------------------------------------------------------------

def test_never_autovacuumed_table_says_so_not_zero():
    row = _dead_row(n_live_tup="1000", n_dead_tup="1000", table_bytes=str(200 * MB),
                    last_autovacuum_age_s="")
    rep = vacuum.collect(_Runner(dead=[row]), limit=20, th=_th())
    md = vacuum.render_markdown(rep)
    assert "从未运行" in md


def test_just_ran_table_shows_zero_not_never():
    row = _dead_row(n_live_tup="1000", n_dead_tup="1000", table_bytes=str(200 * MB),
                    last_autovacuum_age_s="0")
    rep = vacuum.collect(_Runner(dead=[row]), limit=20, th=_th())
    md = vacuum.render_markdown(rep)
    assert "从未运行" not in md
    assert "0 秒前" in md


# ---------------------------------------------------------------------------
# main() 的错误处理：取数失败要给错误信息，不能吐 Traceback。
# ---------------------------------------------------------------------------

def test_query_failure_is_reported_not_thrown(monkeypatch, capsys):
    from common.grmp.errors import QueryError

    class _Boom:
        def run(self, *a, **k):
            raise QueryError("ERROR: permission denied (SQLSTATE 42501)")

    monkeypatch.setattr(vacuum.access, "for_conn", lambda *a, **k: _Boom())
    rc = vacuum.main(["-c", "x"])
    err = capsys.readouterr().err
    assert rc == 2
    assert "Traceback" not in err
    assert "SQLSTATE" in err


def test_markdown_main_does_not_crash(monkeypatch, capsys):
    row = _dead_row(schema="gsbench_e2e_20260801_100g", table="plan_data",
                    n_live_tup="20178297", n_dead_tup="20087028",
                    reltuples="20178297", table_bytes=str(8 * 1024 * MB),
                    last_autovacuum_age_s="", autovacuum_count="0")
    monkeypatch.setattr(
        vacuum.access, "for_conn",
        lambda *a, **k: _Runner(dead=[row], xmin=[_xmin_row()],
                                workers=[_worker_row()]))
    rc = vacuum.main(["-c", "x"])
    out, err = capsys.readouterr()
    assert rc == 0
    assert "Traceback" not in out and "Traceback" not in err
    assert "# " in out  # 有标题，不是空输出
