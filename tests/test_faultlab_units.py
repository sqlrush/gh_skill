"""faultlab 的纯函数单测:排期预算、慢 SQL 规模校验、残留登记簿、报告渲染、分区 DDL。

**不连库。** 连库的部分(造故障、观测)靠 `faultlab.py run` 自己的自证(probe)与
`expected.yaml` 的人工核对,不在单测里 —— 单测要能在任何机器上随全量测试一起跑。

这里钉的都是「改坏了不会立刻被发现」的地方:
  · 预期发现矩阵 expected.yaml 读得通、每项都有 actual、编号与排期表一一对应(它是人手填的);
  · 排期表改完之后 CPU 预算是不是还在上限内(手写的排期很容易不小心排满);
  · 规模 cap 直接拼进 SQL,校验必须挡住非整数;
  · 残留登记簿是清理的依据,加了又撤的抵消逻辑错了会漏清;
  · 库对象名拼进 DDL,必须挡住奇怪的名字。
"""
from __future__ import annotations

import json
import pathlib
import sys

import pytest

_ROOT = pathlib.Path(__file__).resolve().parents[1]
_LAB = _ROOT / "tools" / "faultlab"
sys.path.insert(0, str(_LAB))

from lab import config as C                       # noqa: E402
from lab import expected, prepare, report, state  # noqa: E402
from lab.cleanup import _safe                     # noqa: E402
from lab.scenarios import ALL as REGISTERED       # noqa: E402
from lab.scenarios import slow                    # noqa: E402


# ---------------------------------------------------------------- 排期表

def test_every_scheduled_scenario_has_an_implementation():
    """排期表里的每个编号都要有实现 —— 少一个就是「这个场景从此没跑过」,
    而终端上什么都看不出来。"""
    missing = [s.code for s in C.ALL_SCENARIOS if s.code not in REGISTERED]
    assert not missing, "排期表里有 %s,但 scenarios/ 下没有实现" % missing


def test_cpu_budget_stays_within_the_limit():
    budget = C.cpu_budget(C.ALL_SCENARIOS)
    assert budget["peak"] <= budget["limit"], (
        "同时吃 CPU 的会话峰值 %d 超过上限 %d,og5 只有 4 个核" % (budget["peak"], budget["limit"]))


def test_cpu_budget_counts_only_overlapping_scenarios():
    """预算算的是**同时**在跑的,不是总和 —— 依次上的两个场景不该被算成两份。"""
    a = C.Scenario("A", "slow", "", 1, 1, 0, 100)
    b = C.Scenario("B", "slow", "", 1, 1, 100, 200)     # 紧接着,不重叠
    assert C.cpu_budget((a, b))["peak"] == 1
    c = C.Scenario("C", "slow", "", 1, 1, 50, 200)      # 与 a 重叠
    assert C.cpu_budget((a, c))["peak"] == 2


def test_each_lwlock_slot_sits_inside_one_snapshot_window():
    """要钉的是**意图**:每个快照窗口里只有一种轻量锁争用,WDR 的多窗口分解才看得出
    「这 4 分钟是谁在争、下 4 分钟换成了谁」。

    所以判的是「这一段不跨快照边界」,不是「起点正好等于边界」—— L1 故意比窗口起点
    晚 30 秒开始(让基线那个快照是干净的),它仍然完整落在第一个窗口里。
    """
    seen = {}
    for s in C.LWLOCK:
        end = s.stop_s or C.WINDOW_S
        first = s.start_s // C.SNAPSHOT_EVERY_S
        last = (end - 1) // C.SNAPSHOT_EVERY_S
        assert first == last, "%s(%d~%d 秒)跨了快照边界" % (s.code, s.start_s, end)
        assert first not in seen, "%s 与 %s 落在同一个快照窗口里" % (s.code, seen[first])
        seen[first] = s.code


def test_lwlock_slots_do_not_overlap_each_other():
    slots = sorted(((s.start_s, s.stop_s or C.WINDOW_S, s.code) for s in C.LWLOCK))
    for (a0, a1, ca), (b0, b1, cb) in zip(slots, slots[1:]):
        assert a1 <= b0, "%s 与 %s 的时间段重叠了" % (ca, cb)


def test_scenario_codes_are_unique():
    codes = [s.code for s in C.ALL_SCENARIOS]
    assert len(codes) == len(set(codes)), "排期表里有重复编号:%s" % codes


def test_by_group_filters_and_rejects_unknown_names():
    assert {s.group for s in C.by_group(("lock",))} == {"lock"}
    assert C.by_group(()) == C.ALL_SCENARIOS
    with pytest.raises(ValueError):
        C.by_group(("nosuchgroup",))


def test_every_group_has_at_least_one_scenario():
    for g in C.GROUPS:
        assert C.by_group((g,)), "分类 %s 下一个场景都没有" % g


# ---------------------------------------------------------------- 慢 SQL 规模

@pytest.mark.parametrize("bad", [0, -1, 1.5, "3000", None, 10_000_001])
def test_sql_for_rejects_a_bad_cap(bad):
    """cap 直接拼进 SQL,这是唯一一处把外部数值拼进 SQL 的地方,必须挡住。"""
    from lab.db import LabError
    with pytest.raises(LabError):
        slow.sql_for("S3", bad)


def test_sql_for_substitutes_the_cap_and_leaves_no_placeholder():
    sql = slow.sql_for("S3", 1234)
    assert "1234" in sql
    assert "{cap}" not in sql and "{code}" not in sql


@pytest.mark.parametrize("code", ["S1", "S2", "S3", "S4", "S5", "S6", "S7"])
def test_the_sql_sent_to_the_database_carries_no_root_cause_comment(code):
    """注释写的是成因,发过去就进了语句统计 —— 模型会照抄它当结论(2026-10-18 测试)。"""
    sql = slow.sql_for(code, 1234)
    assert "--" not in sql and "成因" not in sql
    assert sql.lstrip().split()[0].upper() in ("SELECT", "WITH", "DO", "INSERT", "UPDATE", "DELETE")


def test_strip_comments_keeps_code_lines_intact():
    assert slow.strip_comments("-- why\n  -- more\nSELECT 1\n  FROM t -- not a full-line comment\n") == \
        "SELECT 1\n  FROM t -- not a full-line comment\n"


def test_sql_for_rejects_an_unknown_scenario():
    from lab.db import LabError
    with pytest.raises(LabError):
        slow.sql_for("S99", 100)


def test_every_slow_scenario_has_a_sql_file_and_a_spec():
    for s in C.SLOW:
        assert s.code in slow.SPECS, "%s 没有 spec" % s.code
        path = C.SQL_DIR / "slow" / slow.SPECS[s.code].sql
        assert path.exists(), "%s 的 SQL 文件不在:%s" % (s.code, path)


def test_slow_sql_files_join_at_least_five_tables():
    """「5 张表以上多层嵌套」是这批慢 SQL 的设计要求,别改着改着就简化没了。"""
    for code, spec in slow.SPECS.items():
        if code == "S7":                       # S7 是高频点查,只有两张表,设计如此
            continue
        text = (C.SQL_DIR / "slow" / spec.sql).read_text(encoding="utf-8")
        tables = {t for t in ("orders", "order_items", "customers", "payments", "shipments",
                              "regions", "channels", "promos", "warehouses", "couriers",
                              "products", "import_stage") if "fl." + t in text}
        assert len(tables) >= 5, "%s 只用到 %d 张表:%s" % (code, len(tables), sorted(tables))


def test_load_caps_survives_a_broken_file(tmp_path, monkeypatch):
    """校准文件坏了不该让运行失败 —— 退回默认规模,但要说一声。"""
    bad = tmp_path / "caps.json"
    bad.write_text("{这不是 json", encoding="utf-8")
    monkeypatch.setattr(slow, "CAPS_FILE", bad)
    caps = slow.load_caps()
    assert caps["S3"] == slow.SPECS["S3"].cap


def test_load_caps_ignores_nonsense_values(tmp_path, monkeypatch):
    f = tmp_path / "caps.json"
    f.write_text(json.dumps({"S3": -5, "S4": "big", "S5": 999, "NOPE": 1}), encoding="utf-8")
    monkeypatch.setattr(slow, "CAPS_FILE", f)
    caps = slow.load_caps()
    assert caps["S3"] == slow.SPECS["S3"].cap    # 负数被忽略
    assert caps["S4"] == slow.SPECS["S4"].cap    # 字符串被忽略
    assert caps["S5"] == 999                     # 正整数采纳
    assert "NOPE" not in caps


# ---------------------------------------------------------------- 残留登记簿

@pytest.fixture()
def lab_state(tmp_path, monkeypatch):
    monkeypatch.setattr(C, "STATE_DIR", tmp_path)
    monkeypatch.setattr(C, "STATE_FILE", tmp_path / "state.json")
    state.clear()
    return tmp_path


def test_residues_round_trip(lab_state):
    state.add_residue("slot", "fl_slot_v4")
    state.add_residue("prepared_xact", "fl_orphan_k10")
    assert ("slot", "fl_slot_v4") in state.residues()
    state.drop_residue("slot", "fl_slot_v4")
    assert ("slot", "fl_slot_v4") not in state.residues()
    assert ("prepared_xact", "fl_orphan_k10") in state.residues()


def test_adding_the_same_residue_twice_is_one_item(lab_state):
    """K5 每一轮都会重建索引,登记两次不该变成要删两次。"""
    state.add_residue("index", "fl.idx_x")
    state.add_residue("index", "fl.idx_x")
    assert state.residues().count(("index", "fl.idx_x")) == 1


def test_unknown_residue_kind_is_refused(lab_state):
    with pytest.raises(ValueError):
        state.add_residue("什么东西", "x")


def test_a_broken_state_file_is_not_read_as_empty(lab_state):
    """登记簿坏了**不能**当成「没有残留」—— 那会让真实残留被永久忽略。"""
    (lab_state / "state.json").write_text("{坏掉的", encoding="utf-8")
    assert ("_broken", str(lab_state / "state.json")) in state.residues()


# ---------------------------------------------------------------- 库对象名

@pytest.mark.parametrize("ident", ["fl.payments", "fl.idx_fl_k5_items_note", "acct"])
def test_safe_accepts_plain_identifiers(ident):
    assert _safe(ident) == ident


@pytest.mark.parametrize("ident", ["", "fl.t; DROP TABLE x", 'fl."t"', "fl.t--", "fl t"])
def test_safe_rejects_anything_else(ident):
    from lab.db import LabError
    with pytest.raises(LabError):
        _safe(ident)


# ---------------------------------------------------------------- 分区 DDL

def test_partition_ddl_buckets_fit_inside_the_partitions():
    """第 i 个分区收 bucket < i,所以写入的 bucket 必须是 0 … parts-1。
    取 1 … parts 的话最大那个值落在所有分区之外,插入时报「没有匹配的分区」
    (2026-10-17 第一次 prepare 就是这么失败的)。"""
    stmts = prepare.partition_ddl(parts=4, rows=10)
    insert = next(s for s in stmts if s.startswith("INSERT"))
    assert "g % 4" in insert
    create = next(s for s in stmts if s.startswith("CREATE TABLE"))
    assert "VALUES LESS THAN (4)" in create
    assert "VALUES LESS THAN (5)" not in create


@pytest.mark.parametrize("parts", [0, 1, -3, 10_001, "1000", 2.5])
def test_partition_ddl_rejects_a_bad_partition_count(parts):
    from lab.db import LabError
    with pytest.raises(LabError):
        prepare.partition_ddl(parts=parts)


# ---------------------------------------------------------------- 报告

def test_report_renders_scenario_windows(tmp_path):
    rows = [
        {"offset_s": 0, "wall": "10:00:00", "db_time": "2026-10-17 10:00:00",
         "event": "开始", "detail": "窗口 1200 秒"},
        {"offset_s": 1, "wall": "10:00:01", "db_time": "", "event": "WDR 快照",
         "detail": "基线前 snap_id=2960"},
        {"offset_s": 20, "wall": "10:00:20", "db_time": "2026-10-17 10:00:20",
         "event": "起 K1", "detail": "行锁阻塞链"},
        {"offset_s": 300, "wall": "10:05:00", "db_time": "2026-10-17 10:05:00",
         "event": "停 K1", "detail": "按排期结束"},
        {"offset_s": 301, "wall": "10:05:01", "db_time": "", "event": "清理",
         "detail": '{"残留": "无"}'},
    ]
    f = tmp_path / "timeline.jsonl"
    f.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows), encoding="utf-8")
    out = report.render(str(f))
    assert "K1" in out and "2960" in out
    assert "2026-10-17 10:00:20" in out and "2026-10-17 10:05:00" in out


def test_report_skips_a_truncated_last_line(tmp_path):
    """时间线是边跑边写的,进程被 kill 时最后一行可能只写了一半 ——
    报告不该因此整个读不出来。"""
    f = tmp_path / "timeline.jsonl"
    good = json.dumps({"offset_s": 0, "wall": "10:00:00", "event": "开始", "detail": ""},
                      ensure_ascii=False)
    f.write_text(good + "\n{\"offset_s\": 5, \"ev", encoding="utf-8")
    assert "faultlab 运行报告" in report.render(str(f))


def test_report_without_a_timeline_says_so(tmp_path):
    from lab.db import LabError
    with pytest.raises(LabError):
        report.render(str(tmp_path / "nope.jsonl"))


# ---------------------------------------------------------------- 预期发现矩阵(expected.yaml)

def test_expected_matrix_parses_and_every_item_has_an_actual_slot():
    """矩阵是人手填的:读得通、每项都有 actual(待填 = 空串)、编号与排期表一一对应。

    第一版里 S7 那一行的值以 ** 开头,整份文件解析不了 —— 一直没人发现,因为没有任何代码读它。
    """
    data = expected.load()
    assert expected.problems(data, [s.code for s in C.ALL_SCENARIOS]) == []
    assert all("actual" in s for s in data["scenarios"]) and all("actual" in c for c in data["cross_cutting"])
    assert {s["code"] for s in data["scenarios"]} == {s.code for s in C.ALL_SCENARIOS}


def test_expected_problems_catch_missing_or_malformed_actual_and_unregistered_codes():
    data = {"scenarios": [{"code": "S1", "title": "t", "signals": "s", "expect": ["x"]},
                          {"code": "S2", "title": "t", "signals": "s", "expect": ["x"], "actual": ["列表"]},
                          {"code": "S2", "title": "t", "signals": "s", "expect": ["x"], "actual": ""}],
            "cross_cutting": [{"项": "两张榜"}]}
    msgs = expected.problems(data, ["S1", "S2", "S9"])
    assert any("S1 缺 actual" in m for m in msgs)
    assert any("S2 的 actual 要写成一段文字" in m for m in msgs)
    assert any("S2 重复登记" in m for m in msgs)
    assert any("跨场景项 #1 缺 actual" in m for m in msgs)
    assert any("S9" in m and "没登记" in m for m in msgs)


def test_expected_progress_counts_filled_and_pending():
    data = {"scenarios": [{"code": "S1", "actual": "health 报了 ACTIVE_SQL_HOT"}, {"code": "S2", "actual": ""},
                          {"code": "S3", "actual": None}],
            "cross_cutting": [{"项": "两张榜", "actual": "  "}]}
    assert expected.progress(data) == (1, ["S2", "S3", "跨场景:两张榜"])


def test_expected_load_explains_an_unquoted_star(tmp_path):
    from lab.db import LabError
    bad = tmp_path / "expected.yaml"
    bad.write_text("scenarios:\n  - code: S7\n    expect:\n      - topsql: **总耗时**榜首\n    actual: \"\"\n",
                   encoding="utf-8")
    with pytest.raises(LabError, match="加引号"):
        expected.load(bad)


def test_expected_render_reports_progress_and_problems(tmp_path):
    ok_text, ok = expected.render()                     # 仓库里的矩阵:格式没问题(填了几项随测试进度变,不钉)
    assert ok and "格式问题" not in ok_text and "actual 已填" in ok_text
    f = tmp_path / "expected.yaml"
    f.write_text("scenarios:\n  - code: S1\n    title: t\n    signals: s\n    expect: [x]\n    actual: 报了\n",
                 encoding="utf-8")
    text, ok = expected.render(f)
    assert not ok and "已填 1、待填 0" in text and "排期表里有、矩阵里没登记" in text


# ---------------------------------------------------------------- 数据规模

def test_total_rows_stay_under_ten_million():
    """user 2026-10-17 的要求:og5 放不下太多测试数据,总行数控制在千万以内。"""
    total = sum(t.rows for t in C.TABLES) + C.IMPORT_ROWS
    assert total < 10_000_000, "造的数据总行数 %d 超过千万" % total


def test_analyze_list_excludes_the_stale_stats_table():
    """import_stage 永远不能被 ANALYZE —— 它的统计信息停在空表就是 S5 的成因。"""
    assert "import_stage" not in prepare.ANALYZE_TABLES


# ---------------------------------------------------------------- WDR 快照号(2026-10-21:时间线上的编号差 1)

class _FakeTl:
    def __init__(self):
        self.rows = []

    def add(self, event, detail="", db_time=""):
        self.rows.append((event, detail, db_time))


class _FakeConn:
    def one(self, sql, params=None):
        return "2026-10-21 10:00:05"


def _fake_omm(latest_rows, create_out="WDR snapshot request has been submitted\n"):
    """替身 omm_sql:按顺序回答「查最新快照」;create_wdr_snapshot 单独回答。记下收到的每段 SQL。"""
    calls, answers = [], list(latest_rows)

    def omm_sql(sql, timeout_s=60):
        calls.append(sql)
        if "create_wdr_snapshot" in sql:
            return create_out
        return answers.pop(0) if len(answers) > 1 else answers[0]
    return omm_sql, calls


@pytest.fixture
def snap_runner(monkeypatch):
    from lab import runner
    monkeypatch.setattr(runner, "SNAPSHOT_POLL_S", 0)
    return runner


def test_snapshot_waits_for_the_new_id_instead_of_taking_the_previous_one(snap_runner, monkeypatch):
    """快照是异步落表的:调用后第一次查还是旧号 2960,第二次才出现 2961——记的必须是 2961,并带上它的 start_ts。"""
    omm, calls = _fake_omm(["2960|2026-10-21 09:00:00\n",          # 调用前的最大号
                            "2960|2026-10-21 09:00:00\n",          # 调用后第一次:还没落表
                            "2961|2026-10-21 10:00:03\n"])         # 第二次:新快照出现
    monkeypatch.setattr(snap_runner, "omm_sql", omm)
    tl = _FakeTl()
    assert snap_runner._snapshot(_FakeConn(), tl, "基线前") == 2961
    assert tl.rows == [("WDR 快照", "基线前 start_ts=2026-10-21 10:00:03 snap_id=2961", "2026-10-21 10:00:05")]
    assert "create_wdr_snapshot" in calls[1] and len(calls) == 4      # 先查 max、再建、再轮询两次


def test_snapshot_timeout_records_unconfirmed_not_a_wrong_id(snap_runner, monkeypatch):
    omm, _ = _fake_omm(["2960|2026-10-21 09:00:00\n"])               # 一直是旧号
    monkeypatch.setattr(snap_runner, "omm_sql", omm)
    monkeypatch.setattr(snap_runner, "SNAPSHOT_WAIT_S", 0)
    tl = _FakeTl()
    assert snap_runner._snapshot(_FakeConn(), tl, "故障结束") is None
    (event, detail, _), = tl.rows
    assert event == "WDR 快照" and "编号未确认" in detail and detail.endswith("snap_id=未确认")
    assert "2960" in detail and "snap_id=2960" not in detail


def test_snapshot_error_is_reported_as_a_failure_not_waited_on(snap_runner, monkeypatch):
    omm, calls = _fake_omm(["2960|2026-10-21 09:00:00\n"],
                           create_out='ERROR:  WDR snapshot request can not be accepted, please enable "enable_wdr_snapshot".\n')
    monkeypatch.setattr(snap_runner, "omm_sql", omm)
    tl = _FakeTl()
    assert snap_runner._snapshot(_FakeConn(), tl, "基线前") is None
    assert tl.rows[0][0] == "WDR 快照失败" and "enable_wdr_snapshot" in tl.rows[0][1]
    assert len(calls) == 2                                            # 建快照报错就不再轮询


def test_snapshot_on_an_empty_snapshot_table_starts_from_zero(snap_runner, monkeypatch):
    omm, _ = _fake_omm(["\n", "1|2026-10-21 10:00:03\n"])
    monkeypatch.setattr(snap_runner, "omm_sql", omm)
    assert snap_runner._snapshot(_FakeConn(), _FakeTl(), "基线前") == 1


def test_report_lists_the_confirmed_id_and_flags_the_unconfirmed(tmp_path):
    rows = [
        {"offset_s": 0, "wall": "10:00:00", "db_time": "", "event": "开始", "detail": ""},
        {"offset_s": 1, "wall": "10:00:01", "db_time": "", "event": "WDR 快照",
         "detail": "基线前 start_ts=2026-10-21 10:00:01 snap_id=2961"},
        {"offset_s": 9, "wall": "10:00:09", "db_time": "", "event": "WDR 快照",
         "detail": "故障结束 编号未确认:调用后 15 秒内没出现大于 2961 的快照号 snap_id=未确认"},
    ]
    f = tmp_path / "timeline.jsonl"
    f.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows), encoding="utf-8")
    assert "- WDR 快照:2961, 未确认" in report.render(str(f))
