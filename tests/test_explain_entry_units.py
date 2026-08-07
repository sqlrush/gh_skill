"""explain 入口的收尾路径 —— 走模板时 db 是 None，别拿它调方法。

**这条是回归测试。** e145e14 加了一段语句形态校验，但把它放在取到计划之后，
且无条件调用 `db.set_statement_timeout(...)` / `db.close()`。走 EXPLAIN 模板
（也就是**常规路径**）时 db 一直是 None，于是：

    AttributeError: 'NoneType' object has no attribute 'set_statement_timeout'

1111 条单测一条都没红 —— 因为没有测试覆盖 main() 的这段收尾。是通过 opencode
实跑 explain 才炸出来的。这里补上。
"""
import importlib.util
import pathlib
import sys

import pytest

_ROOT = pathlib.Path(__file__).resolve().parents[1]
_SCRIPTS = _ROOT / "skills" / "gaussdb-explain" / "scripts"
sys.path.insert(0, str(_SCRIPTS))
sys.path.insert(0, str(_ROOT))


def _load():
    spec = importlib.util.spec_from_file_location(
        "explain_entry_for_test", _SCRIPTS / "explain.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


explain_mod = _load()


class _Runner:
    """模板路径的 runner：返回一行计划文本。"""

    def run(self, script, values=None):
        return [{"QUERY PLAN": "Seq Scan on t  (cost=0.00..1.00 rows=1 width=4)"}]


def test_template_path_does_not_touch_the_none_db(monkeypatch, capsys):
    """模板路径下 db 全程为 None —— 收尾不能调它的任何方法。

    崩的就是这条路径，而它是**最常走的那条**（能进 EXPLAIN 模板的只读单语句）。
    """
    monkeypatch.setattr(explain_mod.access, "for_conn",
                        lambda *a, **k: _Runner())

    def _boom(*a, **k):
        raise AssertionError("模板路径不该去建原始连接")

    monkeypatch.setattr(explain_mod.access, "connection_for", _boom)

    import io
    monkeypatch.setattr(sys, "stdin", io.StringIO("SELECT 1"))
    assert explain_mod.main(["--sql-stdin"]) == 0, capsys.readouterr().err


@pytest.mark.parametrize("sql,expect", [
    ("INSERT INTO t VALUES (1)", 1),
    ("CREATE TABLE t(i int)", 1),
    ("SELECT 1; SELECT 2;", 1),
])
def test_shape_checks_run_before_connecting(monkeypatch, sql, expect):
    """DML/DDL/多语句是**纯文本检查**，必须在连库之前就拒。

    原先放在取到计划之后：白跑一次 EXPLAIN、白建一次连接，而且拒绝理由与
    「已经拿到计划」同时出现，读起来自相矛盾。
    """
    def _boom(*a, **k):
        raise AssertionError("形态不合法时不该去建连接或取数")

    monkeypatch.setattr(explain_mod.access, "for_conn", _boom)
    monkeypatch.setattr(explain_mod.access, "connection_for", _boom)

    import io
    monkeypatch.setattr(sys, "stdin", io.StringIO(sql))
    assert explain_mod.main(["--sql-stdin"]) == expect


def test_semicolon_inside_a_literal_is_not_a_second_statement(monkeypatch):
    """'a;b' 里的分号是数据不是语句分隔 —— 不能把它判成多语句。"""
    monkeypatch.setattr(explain_mod.access, "for_conn",
                        lambda *a, **k: _Runner())
    import io
    monkeypatch.setattr(sys, "stdin", io.StringIO("SELECT 'a;b'"))
    assert explain_mod.main(["--sql-stdin"]) == 0


class _FailingRunner:
    """模拟 SQL 到数据库那里执行失败（打错字、表不存在）。"""

    def __init__(self, message):
        self._message = message

    def run(self, script, values=None):
        from common.grmp.errors import QueryError
        raise QueryError(self._message)


@pytest.mark.parametrize("message", [
    'ERROR: syntax error at or near "SELEKT" (SQLSTATE 42601)',
    'ERROR: relation "nosuchtable" does not exist (SQLSTATE 42P01)',
])
def test_sql_execution_failure_is_reported_not_thrown(monkeypatch, capsys,
                                                      message):
    """**回归测试。**

    主 except 原先漏了 access.QueryError —— 它是本项目归一化的「取数失败」
    类型，runner.run() 在 SQL 本身执行失败时抛的就是它。漏掉的后果不是少一条
    错误信息，而是**直接吐 Traceback**：用户粘一条有 typo 的 SQL，看到的是
    Python 栈而不是「syntax error at or near "SELEKT"」。

    而这是最常见的用户路径之一 —— 全量矩阵就是在这里抓到它的。
    """
    monkeypatch.setattr(explain_mod.access, "for_conn",
                        lambda *a, **k: _FailingRunner(message))

    import io
    monkeypatch.setattr(sys, "stdin", io.StringIO("SELECT 1"))
    rc = explain_mod.main(["--sql-stdin"])

    assert rc != 0, "SQL 执行失败必须以非 0 退出"
    err = capsys.readouterr().err
    assert "Traceback" not in err
    assert "SQLSTATE" in err, "要把数据库的原话带给用户，而不是包装成通用失败"
