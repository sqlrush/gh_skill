"""store_graph 的纯函数部分(不连库,CI 常跑)。"""
import pathlib
import sys


_ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT))

from common.kb import store_graph as sg  # noqa: E402


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
