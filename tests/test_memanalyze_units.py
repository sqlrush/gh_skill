"""DB-free unit tests for the memanalyze skill.

The load-bearing logic is pure: which view to pick when the dialect differs,
how to build a SELECT when a column is missing, whether a GUC leaves a layer
blind, whether a sample series is a leak or a spike, and how to correlate a
memory row with the session that is running the SQL. All of that is tested here
without a database.
"""
import importlib.util
import pathlib
import sys

import pytest

_ROOT = pathlib.Path(__file__).resolve().parents[1]
_SCRIPTS = _ROOT / "skills" / "gaussdb-memanalyze" / "scripts"

for _m in ("model", "thresholds", "util", "probe", "capability", "collectors",
           "wlm", "trend", "report", "render"):
    sys.modules.pop(_m, None)
sys.path.insert(0, str(_SCRIPTS))
sys.path.insert(0, str(_ROOT))


def _load(mod: str):
    """Load under the plain module name so the skill's own `from model import ...`
    resolves to the same object this test holds."""
    path = _SCRIPTS / f"{mod}.py"
    spec = importlib.util.spec_from_file_location(mod, path)
    m = importlib.util.module_from_spec(spec)
    sys.modules[mod] = m
    spec.loader.exec_module(m)
    return m


model = _load("model")
thresholds = _load("thresholds")
util = _load("util")
probe = _load("probe")
capability = _load("capability")
trend = _load("trend")
collectors = _load("collectors")
wlm = _load("wlm")
report = _load("report")

TH = thresholds.default_thresholds()


# --------------------------------------------------------------------------
# probe — view selection across openGauss / GaussDB dialects
# --------------------------------------------------------------------------
def test_select_prefers_highest_priority_view():
    existing = {
        "gs_total_memory_detail": ("memorytype", "memorymbytes"),
        "pv_total_memory_detail": ("memorytype", "memorymbytes"),
    }
    vi = probe.select("instance", existing)
    assert vi.available is True
    assert vi.name == probe.CANDIDATES["instance"][0]  # the preferred one


def test_select_falls_back_when_preferred_view_is_absent():
    """A GaussDB instance may only have the pv_ / dbe_perf flavour."""
    existing = {"pv_total_memory_detail": ("memorytype", "memorymbytes")}
    vi = probe.select("instance", existing)
    assert vi.available is True
    assert vi.name == "pv_total_memory_detail"
    assert vi.columns == ("memorytype", "memorymbytes")


def test_select_reports_every_candidate_when_none_exist():
    vi = probe.select("instance", {})
    assert vi.available is False
    assert vi.name == ""
    for cand in probe.CANDIDATES["instance"]:
        assert cand in vi.reason        # the user learns what was looked for


def test_every_slot_has_candidates():
    for slot in probe.SLOTS:
        assert probe.CANDIDATES[slot], f"slot {slot} has no candidate views"


# --------------------------------------------------------------------------
# probe — column adaptation (the other half of dialect tolerance)
# --------------------------------------------------------------------------
def test_columns_expr_passes_through_existing_columns():
    vi = model.ViewInfo(name="v", columns=("a", "b"), available=True)
    assert probe.columns_expr(vi, ("a", "b")) == "a, b"


def test_columns_expr_nulls_out_missing_columns():
    """gs_wlm_operator_history has no `warning` column on some versions — the
    query must still run, with NULL standing in for it."""
    vi = model.ViewInfo(name="v", columns=("a",), available=True)
    assert probe.columns_expr(vi, ("a", "warning")) == "a, NULL AS warning"


def test_columns_expr_is_case_insensitive():
    vi = model.ViewInfo(name="v", columns=("Warning",), available=True)
    assert probe.columns_expr(vi, ("warning",)) == "warning"


# --------------------------------------------------------------------------
# capability — a GUC that blinds a layer must say so, loudly
# --------------------------------------------------------------------------
def _catalog(**slots):
    views = {s: model.ViewInfo(name=n, columns=("x",), available=True)
             for s, n in slots.items()}
    return model.Catalog(views=views)


def test_operator_layer_blocked_when_resource_track_level_is_query():
    cap = capability.assess(
        {"resource_track_level": "query", "enable_resource_track": "on"},
        _catalog(wlm_operator="gs_wlm_operator_statistics"))
    assert cap.operator_available is False
    reason = cap.reasons["L5"]
    assert "resource_track_level" in reason      # names the GUC
    assert "operator" in reason                  # names the target value


def test_operator_layer_available_when_guc_and_view_both_present():
    cap = capability.assess(
        {"resource_track_level": "operator", "enable_resource_track": "on"},
        _catalog(wlm_operator="gs_wlm_operator_statistics"))
    assert cap.operator_available is True
    assert not cap.reasons.get("L5")


def test_operator_layer_blocked_when_view_missing_even_if_guc_is_on():
    cap = capability.assess(
        {"resource_track_level": "operator", "enable_resource_track": "on"},
        _catalog())            # no wlm_operator view at all
    assert cap.operator_available is False
    assert "视图" in cap.reasons["L5"]


def test_resource_track_off_blocks_both_sql_and_operator_layers():
    cap = capability.assess(
        {"enable_resource_track": "off", "resource_track_level": "operator"},
        _catalog(wlm_session="gs_wlm_session_statistics",
                 wlm_operator="gs_wlm_operator_statistics"))
    assert cap.sql_available is False
    assert cap.operator_available is False
    assert "enable_resource_track" in cap.reasons["L4"]


def test_history_needs_enable_resource_record():
    cap = capability.assess(
        {"enable_resource_record": "off", "enable_resource_track": "on",
         "resource_track_level": "operator"},
        _catalog(wlm_session_hist="gs_wlm_session_info"))
    assert cap.history_available is False
    assert "enable_resource_record" in cap.reasons["history"]


# --------------------------------------------------------------------------
# trend — leak vs spike vs flat (watch mode's whole point)
# --------------------------------------------------------------------------
def test_trend_monotonic_rise_is_a_leak():
    verdict, _ = trend.analyze([1000, 1200, 1400, 1600, 1800], TH)
    assert verdict == trend.LEAK


def test_trend_peak_then_fallback_is_a_spike():
    verdict, _ = trend.analyze([1000, 3000, 5000, 2000, 1100], TH)
    assert verdict == trend.SPIKE


def test_trend_noise_around_a_baseline_is_flat():
    verdict, _ = trend.analyze([1000, 1020, 990, 1010, 1000], TH)
    assert verdict == trend.FLAT


def test_trend_needs_at_least_three_samples():
    verdict, detail = trend.analyze([1000, 2000], TH)
    assert verdict == trend.INSUFFICIENT
    assert "3" in detail


def test_trend_detail_quotes_real_numbers():
    _, detail = trend.analyze([1000, 1400, 1800], TH)
    assert "1000" in detail and "1800" in detail


def test_trend_finding_severity_escalates_for_leak():
    f = trend.finding([1000, 1400, 1800, 2200], TH)
    assert f is not None
    assert f.severity >= model.Severity.WARN
    assert f.code == "MEM_TREND_LEAK"


def test_trend_flat_produces_no_finding():
    assert trend.finding([1000, 1010, 995, 1005], TH) is None


# --------------------------------------------------------------------------
# collectors — session correlation (memory row <-> the SQL it is running)
# --------------------------------------------------------------------------
def test_correlate_matches_session_by_sessionid():
    # 协议形态：列名做键、值全是字符串
    mem = [{"sessid": "140737", "init_mem": "10",
            "used_mem": "900", "peak_mem": "1200"}]
    act = {"140737": {"usename": "app", "application_name": "etl",
                      "state": "active", "query": "SELECT 1"}}
    rows = collectors.correlate_sessions(mem, act)
    assert rows[0].query == "SELECT 1"
    assert rows[0].usename == "app"
    assert rows[0].peak_mb == 1200


def test_correlate_matches_openGauss_composite_sessid():
    """openGauss sessid can be `<timestamp>.<threadid>`; the thread id is the pid."""
    mem = [{"sessid": "1663812345.140234", "init_mem": "10",
            "used_mem": "900", "peak_mem": "1200"}]
    act = {"140234": {"usename": "app", "application_name": "etl",
                      "state": "active", "query": "SELECT 2"}}
    rows = collectors.correlate_sessions(mem, act)
    assert rows[0].query == "SELECT 2"


def test_correlate_keeps_unmatched_memory_rows():
    """A session that already ended still ate the memory — do not drop the row."""
    mem = [{"sessid": "999999", "init_mem": "10",
            "used_mem": "900", "peak_mem": "1200"}]
    rows = collectors.correlate_sessions(mem, {})
    assert len(rows) == 1
    assert rows[0].peak_mb == 1200
    assert "未关联" in rows[0].query


# --------------------------------------------------------------------------
# collectors — instance layer findings
# --------------------------------------------------------------------------
class _FakeRunner:
    """按**脚本名**给预置行；fail 里的脚本名抛 access.QueryError。

    行一律是**全字符串的字典** —— 协议就是这个形态。假连接若给原生类型，
    测试就永远发现不了 bool("f")==True、int("3704.0") 抛异常这类
    只在字符串形态下才出现的问题。
    """

    def __init__(self, canned=None, fail=()):
        self.canned = canned or {}
        self.fail = fail
        self.calls = []

    def run(self, script, values=None):
        from common import access
        self.calls.append((script, values))
        for name in self.fail:
            if name in script:
                raise access.QueryError(
                    "permission denied for relation behind %s" % script)
        for name, rows in self.canned.items():
            if name in script:
                return [{k: _s(v) for k, v in row.items()} for row in rows]
        return []


def _s(value):
    """协议把所有值渲染成字符串，NULL 渲染成空串。"""
    if value is None:
        return ""
    if isinstance(value, bool):
        return "t" if value else "f"
    return str(value)


def _instance_catalog():
    return _catalog(instance="gs_total_memory_detail")


def _mem_rows(dynamic_used, max_dynamic=10000, peak=None):
    """列名与 grmp_middleware/grmp_gen_memanalyze.py 的 INSTANCE_COLS 一致。"""
    pairs = [
        ("max_process_memory", 12000),
        ("process_used_memory", dynamic_used + 500),
        ("max_dynamic_memory", max_dynamic),
        ("dynamic_used_memory", dynamic_used),
        ("dynamic_peak_memory", peak if peak is not None else dynamic_used),
        ("shared_used_memory", 400),
        ("other_used_memory", 100),
    ]
    return [{"memorytype": t, "memorymbytes": mb} for t, mb in pairs]


def test_instance_critical_when_dynamic_memory_nearly_exhausted():
    db = _FakeRunner({"memanalyze.instance": _mem_rows(9500)})   # 95%
    d = collectors.collect_instance(db, _instance_catalog(), TH, 10)
    assert d.available is True
    codes = {f.code: f for f in d.findings}
    assert "MEM_DYNAMIC_HIGH" in codes
    assert codes["MEM_DYNAMIC_HIGH"].severity == model.Severity.CRITICAL


def test_instance_healthy_when_dynamic_memory_is_low():
    db = _FakeRunner({"memanalyze.instance": _mem_rows(2000)})   # 20%
    d = collectors.collect_instance(db, _instance_catalog(), TH, 10)
    assert [f for f in d.findings if f.code == "MEM_DYNAMIC_HIGH"] == []


def test_instance_flags_peak_that_already_fell_back():
    """Current usage is fine but the peak nearly hit the ceiling — the spike
    happened, it is just over. Saying nothing here would hide the incident."""
    db = _FakeRunner({"memanalyze.instance": _mem_rows(2000, peak=9500)})
    d = collectors.collect_instance(db, _instance_catalog(), TH, 10)
    codes = [f.code for f in d.findings]
    assert "MEM_PEAK_FALLBACK" in codes


def test_instance_degrades_when_view_unavailable():
    cat = model.Catalog(views={"instance": model.ViewInfo(
        name="", columns=(), available=False, reason="no candidate exists")})
    d = collectors.collect_instance(_FakeRunner(), cat, TH, 10)
    assert d.available is False
    assert "no candidate exists" in d.note


def test_instance_degrades_on_query_failure():
    db = _FakeRunner(fail=("memanalyze.instance",))
    d = collectors.collect_instance(db, _instance_catalog(), TH, 10)
    assert d.available is False
    assert "permission denied" in d.note


# --------------------------------------------------------------------------
# collectors — L6 config
#
# Regression: on openGauss `max_dynamic_memory` is NOT a GUC — it only exists as
# a row in gs_total_memory_detail. Reading it from pg_settings alone meant the
# overcommit check silently never fired on a real instance.
# --------------------------------------------------------------------------
def _cap_with(gucs):
    return capability.assess(gucs, _catalog())


def test_config_overcommit_uses_the_instance_view_when_the_guc_is_absent():
    cap = _cap_with({"work_mem": "65536", "max_connections": "500"})  # 64 MB x 500
    mem = {"max_dynamic_memory": 10240.0}          # MB, straight from L1
    d = collectors.collect_config(None, cap, TH, 10, mem=mem)
    codes = [f.code for f in d.findings]
    assert "MEM_CONFIG_OVERCOMMIT" in codes        # 32 GB theoretical vs 10 GB ceiling


def test_config_overcommit_silent_when_within_the_ceiling():
    cap = _cap_with({"work_mem": "16384", "max_connections": "200"})  # 16 MB x 200
    mem = {"max_dynamic_memory": 4823.0}           # 3.2 GB theoretical < 4.7 GB
    d = collectors.collect_config(None, cap, TH, 10, mem=mem)
    assert [f for f in d.findings if f.code == "MEM_CONFIG_OVERCOMMIT"] == []


def test_config_falls_back_to_max_process_memory():
    """No max_dynamic_memory anywhere — max_process_memory is still a real ceiling."""
    cap = _cap_with({"work_mem": "65536", "max_connections": "500",
                     "max_process_memory": "1048576"})   # 1 GB
    d = collectors.collect_config(None, cap, TH, 10, mem={})
    assert "MEM_CONFIG_OVERCOMMIT" in [f.code for f in d.findings]


# --------------------------------------------------------------------------
# wlm — SQL and operator layers
# --------------------------------------------------------------------------
def test_operator_layer_degrades_with_the_capability_reason_not_an_empty_table():
    """The whole point of the guard: a blind layer must explain itself."""
    cap = capability.assess(
        {"resource_track_level": "query", "enable_resource_track": "on"},
        _catalog(wlm_operator="gs_wlm_operator_statistics"))
    d = wlm.collect_operator(_FakeRunner(), _catalog(wlm_operator="v"), cap, TH, 10)
    assert d.available is False
    assert "resource_track_level" in d.note
    assert d.rows == []


def test_sql_layer_flags_spill_and_estimate_deviation():
    cap = capability.assess(
        {"resource_track_level": "operator", "enable_resource_track": "on"},
        _catalog(wlm_session="gs_wlm_session_statistics"))
    # 列名与 grmp_middleware/grmp_gen_memanalyze.py 的 SQL_COLS 一致
    rows = [{
        "queryid": 101, "query": "SELECT * FROM big", "start_time": "2026-07-12",
        "duration": 9000, "estimate_memory": 100, "used_memory": 4096,
        "max_peak_memory": 2048, "average_peak_memory": 0, "spill_info": "",
    }]
    runner = _FakeRunner({"memanalyze.wlm_sql": rows})
    d = wlm.collect_sql(runner, _catalog(wlm_session="gs_wlm_session_statistics"),
                        cap, TH, 10)
    assert d.available is True
    codes = [f.code for f in d.findings]
    assert "MEM_SQL_SPILL" in codes            # 2048 MB spilled
    assert "MEM_SQL_ESTIMATE_OFF" in codes     # estimated 100 MB, used 4096 MB


# --------------------------------------------------------------------------
# report
# --------------------------------------------------------------------------
def _evidence(**kw):
    base = {
        "conn": "og", "target": "snapshot", "mode": "snapshot",
        "capability": capability.assess({"resource_track_level": "query"}, _catalog()),
        "catalog": _instance_catalog(),
        "dims": [], "findings": [],
    }
    base.update(kw)
    return model.MemEvidence(**base)


def test_report_prints_the_capability_probe_section():
    out = report.render_markdown(_evidence())
    assert "## 能力与视图探测" in out
    assert "gs_total_memory_detail" in out          # which view was chosen
    assert "resource_track_level" in out            # why L5 is blind


def test_report_sorts_findings_worst_first():
    fs = [
        model.Finding("L1", "A", model.Severity.NOTICE, "m", "v", "t", "e"),
        model.Finding("L1", "B", model.Severity.CRITICAL, "m", "v", "t", "e"),
    ]
    out = report.render_markdown(_evidence(findings=fs))
    assert out.index("| B ") < out.index("| A ")


def test_report_json_round_trips():
    import json
    fs = [model.Finding("L1", "A", model.Severity.CRITICAL, "m", "9500", "9000", "e")]
    payload = json.loads(report.render_json(_evidence(findings=fs)))
    assert payload["overall"] == int(model.Severity.CRITICAL)
    assert payload["findings"][0]["code"] == "A"
    assert payload["capability"]["operator_available"] is False


def test_report_empty_run_is_honest():
    out = report.render_markdown(_evidence())
    assert "未发现" in out or "无" in out


# --------------------------------------------------------------------------
# util
# --------------------------------------------------------------------------
# --------------------------------------------------------------------------
# CLI argument parsing
#
# Regression: argparse only accepts parent-parser options *before* the
# subcommand, so `memanalyze.py snapshot -c og` — the form SKILL.md documents —
# blew up with "-c/--conn is required" until each subparser inherited them.
# --------------------------------------------------------------------------
memanalyze = _load("memanalyze")


def test_conn_is_accepted_after_the_subcommand():
    args = memanalyze._parse_args(["snapshot", "-c", "og", "--top", "5"])
    assert args.cmd == "snapshot"
    assert args.conn == "og"
    assert args.top == 5


def test_a_connection_named_like_a_subcommand_is_not_hoisted():
    """`-c watch` must stay a connection name. This is why the parser only
    accepts the subcommand in first position, rather than hunting for it."""
    args = memanalyze._parse_args(["-c", "watch"])
    assert args.cmd == "snapshot"
    assert args.conn == "watch"


def test_snapshot_is_the_default_subcommand():
    args = memanalyze._parse_args(["-c", "og"])
    assert args.cmd == "snapshot"
    assert args.conn == "og"


def test_watch_takes_its_own_options():
    args = memanalyze._parse_args(["watch", "-c", "og", "--interval", "3",
                                   "--count", "20"])
    assert (args.cmd, args.interval, args.count) == ("watch", 3, 20)


def test_watch_rejects_too_few_samples():
    """Fewer than MIN_SAMPLES cannot yield a trend verdict — fail loudly."""
    assert memanalyze.main(["watch", "-c", "og", "--count", "2"]) == 1


# --------------------------------------------------------------------------
# util
# --------------------------------------------------------------------------
def test_pct_guards_against_zero_denominator():
    assert util.pct(5, 0) == 0.0
    assert util.pct(50, 200) == 25.0


def test_human_mb():
    assert util.human_mb(512) == "512 MB"
    assert util.human_mb(2048) == "2.00 GB"


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))


def test_sql_layer_explains_itself_when_the_view_is_present_but_empty():
    """A ✓ capability line plus a blank table reads as 'no problems' — it is not."""
    cap = capability.assess(
        {"resource_track_level": "operator", "enable_resource_track": "on"},
        _catalog(wlm_session="gs_wlm_session_statistics"))
    d = wlm.collect_sql(_FakeRunner(), _catalog(wlm_session="v"), cap, TH, 10)
    assert d.available is True
    assert d.rows == []
    assert "resource_track_cost" in d.note
    assert "history" in d.note              # points at the right next step


def test_operator_layer_explains_itself_when_the_view_is_present_but_empty():
    cap = capability.assess(
        {"resource_track_level": "operator", "enable_resource_track": "on"},
        _catalog(wlm_operator="gs_wlm_operator_statistics"))
    d = wlm.collect_operator(_FakeRunner(), _catalog(wlm_operator="v"), cap, TH, 10)
    assert d.available is True
    assert d.rows == []
    assert "单机" in d.note                  # names the real openGauss limitation
