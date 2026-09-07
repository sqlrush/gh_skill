"""store_pg / store_graph 的纯函数部分(不连库,CI 常跑)。"""
import pathlib
import sys

import pytest

_ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT))

from common.kb import store_graph as sg, store_pg  # noqa: E402


def test_vector_literal_format():
    assert store_pg.vector_literal([1, 0.5, -2]) == "[1.0,0.5,-2.0]"


def test_lex_literal_shifts_signal_positions():
    assert store_pg.lex_literal(["a", "b"], ["s"]) == "'a':1 'b':2 's':3A"


def test_lex_literal_without_signals():
    assert store_pg.lex_literal(["a"]) == "'a':1"


def test_labels_are_camel_case_and_bijective():
    assert sg.LABELS["wait_event"] == "WaitEvent" and sg.LABELS["rootcause"] == "RootCause"
    for kind, label in sg.LABELS.items():
        assert sg.kind_of_label(label) == kind


def test_no_co_occurrence_relation_exists():
    """设计红线:图里不许有共现边。"""
    assert "co_occurs" not in sg.REL_TYPES


def test_graph_store_repr_has_no_password():
    g = sg.GraphStore("http://h:7474", "neo4j", "secret-pw")
    assert "secret-pw" not in repr(g)


# --- psycopg2 迁移:两条不能踩的线 -------------------------------------------

class _Cur:
    description = None

    def __init__(self):
        self.calls = []

    def execute(self, sql, params=None):
        self.calls.append((sql, params))

    def fetchall(self):
        return []

    def close(self):
        pass


class _Raw:
    def __init__(self):
        self.autocommit = False
        self.cur = _Cur()

    def cursor(self):
        return self.cur


def test_empty_params_become_none_so_literal_percent_survives():
    """psycopg2 收到参数容器(哪怕空元组)就会做 %-插值。建表 DDL 与统计 SQL 里
    出现一个字面百分号就会当场炸,而换掉的 pg8000 不会——迁移时最容易踩的一条。"""
    raw = _Raw()
    store = store_pg.PgStore(raw, dims=4)
    store._query("SELECT 1 WHERE x LIKE 'a%'")
    store._query("SELECT %s", (1,))
    assert [c[1] for c in raw.cur.calls] == [None, (1,)]


def test_sslmode_rejects_unknown_value_instead_of_downgrading():
    with pytest.raises(store_pg.PgStoreError) as ei:
        store_pg._sslmode("verify-none")
    assert "verify-none" in str(ei.value)
    assert store_pg._sslmode("") == "disable" and store_pg._sslmode("require") == "require"
