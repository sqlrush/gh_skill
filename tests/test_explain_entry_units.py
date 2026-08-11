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
    # --- 以下每条都是实测绕过，不是补齐用的等价变形 ---
    # 前导注释让 DML 蒙混过关：og5 上 gsql 与 pg8000 两条直连都真写了库
    ("/* c */ UPDATE t SET a = 1", 1),
    ("-- c\nUPDATE t SET a = 1", 1),
    ("/* c */ DELETE FROM t", 1),
    # 恰好一个分号的两条语句：原先数分号 `> 1`，这条漏了过去
    ("SELECT 1; SELECT pg_backend_pid()", 1),
    ("SELECT 1; GRANT ALL ON t TO public", 1),
    # 白名单挡住黑名单漏掉的那些
    ("COPY (SELECT 1) TO PROGRAM 'true'", 1),
    ("VACUUM t", 1),
    ("GRANT ALL ON t TO public", 1),
    ("-- 只有注释", 1),
])
def test_shape_checks_run_before_connecting(monkeypatch, sql, expect):
    """DML/非只读/多语句是**纯文本检查**，必须在连库之前就拒。

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


@pytest.mark.parametrize("sql", [
    "SELECT 'drop' AS x",
    "SELECT relname FROM pg_class WHERE relname = 'alter'",
    "SELECT comment FROM (SELECT 1 AS comment) t",
    "SELECT 1 AS x -- a;b;c",
    "SELECT 1 /* ; */ AS x",
    "SELECT 1; -- 收尾注释",
    "SELECT relname FROM t WHERE relname LIKE '%status%'",
])
def test_ordinary_queries_are_not_falsely_rejected(monkeypatch, sql):
    """**误拒也是 bug。**

    原先的 DDL 判定不带锚点、扫整串原文：任何叫 comment 的列、任何值是
    'drop' 的过滤条件都被拒 —— 而 comment 是真实业务表里极常见的列名。
    注释里的分号则让合法单语句在中间件模式下被判成多语句。
    """
    monkeypatch.setattr(explain_mod.access, "for_conn",
                        lambda *a, **k: _Runner())
    import io
    monkeypatch.setattr(sys, "stdin", io.StringIO(sql))
    assert explain_mod.main(["--sql-stdin"]) == 0


def test_analyze_never_gets_a_writable_raw_session(monkeypatch):
    """**explain 不再建原始连接 —— 中间件与直连同一条路。**

    原先 `--analyze` 走回落时拿的是 read_only=False 的原始会话，用户 SQL
    不经 EXPLAIN 包裹直接下发（实测 default_transaction_read_only = off）。
    那条旁路是 `/* c */ UPDATE ...` 能写库的载体，已删除。
    """
    def _boom(*a, **k):
        raise AssertionError("explain 不该再去建原始连接")

    monkeypatch.setattr(explain_mod.access, "connection_for", _boom)
    monkeypatch.setattr(explain_mod.access, "for_conn",
                        lambda *a, **k: _Runner())
    import io
    monkeypatch.setattr(sys, "stdin", io.StringIO("SELECT 1"))
    assert explain_mod.main(["--sql-stdin", "--analyze"]) == 0


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
