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
