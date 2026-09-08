"""common.search_path —— EXPLAIN 前把 search_path 切到 SQL 原本的 schema。

现场 sqltune 400 的成因:业务 SQL 的表名不带 schema,靠应用账号的 search_path 解析;中间件执行账号的
search_path 是 "$user", public,解析不到 → relation does not exist → HTTP 400。修法是注册一条
「SET search_path TO "<schema>", public; EXPLAIN …」的两语句模板。中间件能不能跑两条语句不确定,
所以先用一条探测脚本验一次(每个 runner 只验一次),不行就退回单语句模板并说明——不让它变成新的 400。
schema 名同时进 SET 语句的引号里,形状不是合法标识符一律不用。
"""
import pathlib
import sys

import pytest

_ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT))

from common import search_path as sp  # noqa: E402
from common.grmp.errors import QueryError  # noqa: E402


class FakeRunner:
    def __init__(self, probe_ok=True, probe_exc=None):
        self.probe_ok, self.probe_exc, self.calls = probe_ok, probe_exc, []

    def run(self, script, values=None):
        self.calls.append((script, dict(values or {})))
        if script == sp.PROBE_SCRIPT:
            if self.probe_exc:
                raise QueryError(self.probe_exc)
            return [{"ok": "1"}] if self.probe_ok else []
        return [{"QUERY PLAN": "Seq Scan on orders"}]


@pytest.fixture(autouse=True)
def _fresh_cache():
    sp.reset_probe_cache()
    yield
    sp.reset_probe_cache()


def test_valid_schema_is_a_plain_identifier():
    assert sp.valid_schema("app_trade") and sp.valid_schema("S1") and sp.valid_schema("a$b")
    for bad in ("", "app;drop", "a b", '"x"', "app.trade", "1abc", "x" * 70):
        assert not sp.valid_schema(bad), bad


def test_no_schema_runs_the_plain_template():
    r = FakeRunner()
    plan, applied, note = sp.explain_plan(r, "sqltune.plan_text", "select 1", "")
    assert plan == "Seq Scan on orders" and applied == "" and note == ""
    assert r.calls == [("sqltune.plan_text", {"sql": "select 1"})]


def test_schema_uses_the_schema_template_when_the_middleware_runs_two_statements():
    r = FakeRunner()
    plan, applied, note = sp.explain_plan(r, "sqltune.plan_text", "select 1", "app_trade")
    assert plan == "Seq Scan on orders" and applied == "app_trade" and note == ""
    assert r.calls[0][0] == sp.PROBE_SCRIPT
    assert r.calls[1] == ("sqltune.plan_text_schema", {"sql": "select 1", "schema": "app_trade"})


def test_probe_failure_falls_back_to_the_plain_template_with_a_note():
    """探测脚本没注册(白名单没灌)/ 中间件拒绝两条语句:退回单语句模板,把原因和**可照做的配置命令**写进 note——
    用户看到就知道该让 DBA 做什么,不用再来问。"""
    r = FakeRunner(probe_exc="脚本 explain.multi_stmt_probe 不存在")
    plan, applied, note = sp.explain_plan(r, "sqltune.plan_text", "select 1", "app_trade")
    assert plan == "Seq Scan on orders" and applied == ""
    assert r.calls[1] == ("sqltune.plan_text", {"sql": "select 1"})
    assert "search_path" in note and "app_trade" in note and "不存在" in note
    assert "白名单" in note and "explain.multi_stmt_probe" in note          # 原因一:探测脚本没灌进白名单
    assert "ALTER ROLE" in note and "SET search_path = app_trade, public" in note   # 可照做的 DBA 命令
    assert "app_trade.<表>" in note                                         # 备选:表名写全


def test_probe_returning_nothing_counts_as_unsupported():
    r = FakeRunner(probe_ok=False)
    _, applied, note = sp.explain_plan(r, "explain.plan_text", "select 1", "app_trade")
    assert applied == "" and note and r.calls[1][0] == "explain.plan_text"


def test_probe_runs_once_per_runner():
    r = FakeRunner()
    sp.explain_plan(r, "sqltune.plan_text", "select 1", "a1")
    sp.explain_plan(r, "sqltune.plan_json", "select 2", "a1")
    assert [c[0] for c in r.calls].count(sp.PROBE_SCRIPT) == 1
    assert r.calls[-1][0] == "sqltune.plan_json_schema"


def test_invalid_schema_never_reaches_a_template():
    r = FakeRunner()
    _, applied, note = sp.explain_plan(r, "x.plan_text", "select 1", "app;drop")
    assert applied == "" and "不是合法" in note
    assert all(c[0] != "x.plan_text_schema" for c in r.calls)


def test_set_search_path_on_a_raw_session_quotes_the_identifier():
    class Db:
        def __init__(self):
            self.executed = []

        def execute(self, sql, params=None):
            self.executed.append(sql)

    db = Db()
    assert sp.set_search_path(db, "app_trade") == "app_trade"
    assert db.executed == ['SET search_path TO "app_trade", public']
    assert sp.set_search_path(db, "") == "" and len(db.executed) == 1
    with pytest.raises(ValueError):
        sp.set_search_path(db, 'app"; drop schema x')


def test_executor_refusing_two_statements_gets_the_same_dba_command():
    """原因二:探测脚本在、但中间件不支持一条脚本跑两条语句——说明要写明这是执行器的限制,并给同一条 DBA 命令。"""
    r = FakeRunner(probe_ok=False)
    _, applied, note = sp.explain_plan(r, "explain.plan_text", "select 1", "gbatch")
    assert applied == "" and "两条语句" in note and "白名单" not in note
    assert "ALTER ROLE" in note and "SET search_path = gbatch, public" in note


def test_fallback_explain_failure_carries_the_note_in_the_error():
    """探测没通过、退回单语句模板后 EXPLAIN 又因为表不在 search_path 里失败:这时没有报告可以放说明,
    说明必须跟在报错后面——用户看到的那一段就得写清是白名单没灌还是执行器不支持,以及 DBA 该跑的命令。"""
    class _R(FakeRunner):
        def run(self, script, values=None):
            if script == "sqltune.plan_text":
                raise QueryError('执行脚本 sqltune.plan_text 失败：ERROR: relation "orders" does not exist')
            return super().run(script, values)

    r = _R(probe_exc="脚本 explain.multi_stmt_probe 不存在")
    with pytest.raises(QueryError) as ei:
        sp.explain_plan(r, "sqltune.plan_text", "select 1", "app_trade")
    msg = str(ei.value)
    assert 'relation "orders" does not exist' in msg and "补充" in msg
    assert "白名单" in msg and "ALTER ROLE" in msg and "SET search_path = app_trade, public" in msg
