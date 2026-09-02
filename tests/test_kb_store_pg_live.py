"""common.kb.store_pg —— 打真库(pgvector 与 openGauss 7 DataVec 各跑一遍)。

环境变量给连接(口令不进仓库、不进聊天):
    KB_TEST_PGVECTOR = host:port:database:user:password
    KB_TEST_OG7      = host:port:database:user:password
没设的引擎**跳过并说明原因**(不是静默 skip)。同一套断言两种引擎都要过——
这就是"一套 SQL 两种引擎通用"的证据。
"""
import os
import pathlib
import sys

import pytest

_ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT))

from common.kb import store_pg, text as kbtext  # noqa: E402

pytestmark = pytest.mark.live

_ENGINES = {"pgvector": "KB_TEST_PGVECTOR", "og7": "KB_TEST_OG7"}
_DIMS = 4      # 测试用小维度,断言余弦排序时人脑能算


def _parse(spec: str):
    host, port, db, user, pw = spec.split(":", 4)
    return dict(host=host, port=int(port), database=db, user=user, password=pw)


@pytest.fixture(params=sorted(_ENGINES))
def store(request):
    env = _ENGINES[request.param]
    spec = os.environ.get(env)
    if not spec:
        pytest.skip(f"未配置 {env}(引擎 {request.param} 不可达/未配置,不是通过)")
    s = store_pg.PgStore.connect(dims=_DIMS, **_parse(spec))
    s.rebuild()                      # 专用测试库,每个用例从空表开始
    yield s
    s.close()


def _doc(i: str, kind="case", title="t"):
    return store_pg.DocRow(id=i, kind=kind, title=title, source="src.v1", version="v1",
                           meta={"system": "CBST"}, content_hash="h" + i)


def _chunk(cid, doc_id, seq, content, signals=(), emb=None):
    return store_pg.ChunkRow(id=cid, doc_id=doc_id, seq=seq, section="现场", content=content,
                             content_hash="c" + cid, tokens=kbtext.tokenize(content),
                             signal_tokens=kbtext.tokenize(" ".join(signals)), embedding=emb)


# ---------------------------------------------------------------- setup

def test_setup_reports_engine_and_vector_capability(store):
    caps = store.capabilities()
    assert caps.engine in ("opengauss", "postgresql")
    assert caps.vector is True, "两个测试引擎都该有 vector 类型;没有说明容器/扩展没就绪"
    assert store.meta_get("vector_engine") in ("datavec", "pgvector")
    assert store.meta_get("schema_version") == "1"


def test_setup_is_idempotent(store):
    store.setup()
    store.setup()
    assert store.counts()["chunks"] == 0


# ---------------------------------------------------------------- lexical

def test_lexical_search_hits_chinese_bigrams_and_identifiers(store):
    store.upsert_doc(_doc("d1"), [
        _chunk("c1", "d1", 0, "业务偶现单条update走索引执行耗时3s"),
        _chunk("c2", "d1", 1, "针对 cbst.cosp_asyn_task_dtl 小表调大 autovacuum_vacuum_threshold"),
    ])
    hits = store.search_chunks_lexical(kbtext.query_tokens("单条update偶现"))
    assert hits and hits[0].id == "c1"
    hits = store.search_chunks_lexical(kbtext.query_tokens("autovacuum_vacuum_threshold"))
    assert hits and hits[0].id == "c2"
    hits = store.search_chunks_lexical(kbtext.query_tokens("cosp_asyn_task_dtl"))
    assert hits and hits[0].id == "c2", "对象名去掉 schema 前缀也要命中"


def test_lexical_signal_weight_ranks_higher(store):
    """同样命中「autovacuum」,带信号权重的块排前面。"""
    store.upsert_doc(_doc("d1"), [
        _chunk("plain", "d1", 0, "autovacuum 参数说明"),
        _chunk("signal", "d1", 1, "无关正文", signals=("autovacuum 频繁触发",)),
    ])
    hits = store.search_chunks_lexical(["autovacuum"])
    assert [h.id for h in hits][0] == "signal"


def test_lexical_no_match_returns_empty_not_error(store):
    store.upsert_doc(_doc("d1"), [_chunk("c1", "d1", 0, "索引膨胀")])
    assert store.search_chunks_lexical(kbtext.query_tokens("完全无关的词")) == []
    assert store.search_chunks_lexical([]) == []


def test_kind_filter(store):
    store.upsert_doc(_doc("r1", kind="rule"), [_chunk("rc", "r1", 0, "索引列数不超过 4 列")])
    store.upsert_doc(_doc("k1", kind="case"), [_chunk("kc", "k1", 0, "索引列数过多导致写放大")])
    only_rules = store.search_chunks_lexical(["索引"], kinds=["rule"])
    assert [h.id for h in only_rules] == ["rc"]


# ---------------------------------------------------------------- vector

def test_vector_search_orders_by_cosine(store):
    store.upsert_doc(_doc("d1"), [
        _chunk("east", "d1", 0, "a", emb=[1, 0, 0, 0]),
        _chunk("north", "d1", 1, "b", emb=[0, 1, 0, 0]),
        _chunk("diag", "d1", 2, "c", emb=[1, 1, 0, 0]),
    ])
    hits = store.search_chunks_vector([1, 0.1, 0, 0], k=3)
    assert [h.id for h in hits] == ["east", "diag", "north"]
    assert hits[0].score > hits[1].score > hits[2].score
    assert 0.99 < hits[0].score <= 1.0001


def test_vector_search_skips_null_embeddings_and_coverage_counts(store):
    store.upsert_doc(_doc("d1"), [
        _chunk("has", "d1", 0, "a", emb=[1, 0, 0, 0]),
        _chunk("none", "d1", 1, "b"),
    ])
    assert [h.id for h in store.search_chunks_vector([1, 0, 0, 0])] == ["has"]
    assert store.coverage() == (2, 1)
    assert store.missing_embeddings() == [("none", "b")]
    store.set_chunk_embedding("none", [0, 1, 0, 0])
    assert store.coverage() == (2, 2)
    assert store.missing_embeddings() == []


# ---------------------------------------------------------------- nodes

def test_node_vectors_lexical_and_vector(store):
    store.upsert_node_vectors([
        store_pg.NodeVectorRow(node_id="symptom:update_slow", kind="symptom", name="单条 update 偶发秒级",
                               tokens=kbtext.tokenize("单条 update 偶发秒级"),
                               signal_tokens=kbtext.tokenize("autovacuum 频繁触发"),
                               embedding=[1, 0, 0, 0]),
        store_pg.NodeVectorRow(node_id="symptom:lock_wait", kind="symptom", name="锁等待冲高",
                               tokens=kbtext.tokenize("锁等待冲高"), embedding=[0, 1, 0, 0]),
    ])
    lex = store.search_nodes_lexical(kbtext.query_tokens("autovacuum 触发频繁"), kinds=["symptom"])
    assert lex and lex[0].id == "symptom:update_slow"
    vec = store.search_nodes_vector([0, 1, 0, 0], k=1)
    assert vec[0].id == "symptom:lock_wait"
    store.delete_node_vectors(["symptom:lock_wait"])
    assert store.counts()["nodes"] == 1


# ---------------------------------------------------------------- doc lifecycle

def test_upsert_replaces_old_chunks_and_delete_cascades(store):
    store.upsert_doc(_doc("d1"), [_chunk("old", "d1", 0, "旧内容")])
    store.upsert_doc(_doc("d1"), [_chunk("new", "d1", 0, "新内容")])
    assert store.search_chunks_lexical(["旧内"]) == []
    assert store.search_chunks_lexical(["新内"])[0].id == "new"
    assert store.doc_hashes() == {"d1": "hd1"}
    store.delete_doc("d1")
    assert store.counts()["chunks"] == 0 and "docs.case" not in store.counts()


def test_upsert_is_atomic_on_failure(store):
    """第二块故意撞主键:整篇回滚,不能留下半截文档。"""
    store.upsert_doc(_doc("other"), [_chunk("dup", "other", 0, "x")])
    with pytest.raises(store_pg.PgStoreError):
        store.upsert_doc(_doc("d1"), [_chunk("fresh", "d1", 0, "a"), _chunk("dup", "d1", 1, "b")])
    assert "d1" not in store.doc_hashes()
    assert store.search_chunks_lexical(["a"]) == []


def test_rebuild_empties_everything(store):
    store.upsert_doc(_doc("d1"), [_chunk("c", "d1", 0, "x")])
    store.rebuild()
    assert store.counts() == {"chunks": 0, "nodes": 0}
    assert store.meta_get("schema_version") == "1"


# ---------------------------------------------------------------- no-vector path

def test_no_vector_engine_path(request):
    """强制走「引擎无 vector 类型」路径:表里没 embedding 列,向量查询返回空,覆盖率 (n, 0)。"""
    spec = os.environ.get("KB_TEST_PGVECTOR")
    if not spec:
        pytest.skip("未配置 KB_TEST_PGVECTOR")
    s = store_pg.PgStore.connect(dims=_DIMS, force_no_vector=True, **_parse(spec))
    try:
        s.rebuild()
        assert s.capabilities().vector is False
        assert s.meta_get("vector_engine") == "none"
        s.upsert_doc(_doc("d1"), [_chunk("c1", "d1", 0, "索引膨胀", emb=[1, 0, 0, 0])])
        assert s.search_chunks_vector([1, 0, 0, 0]) == []
        assert s.search_chunks_lexical(["索引"])[0].id == "c1"
        assert s.coverage() == (1, 0)
        assert s.missing_embeddings() == []
    finally:
        s.rebuild()
        s.close()


# ---------------------------------------------------------------- pure helpers (no db)

@pytest.mark.parametrize("_", [0])
def test_vector_literal_format(_):
    assert store_pg.vector_literal([1, 0.5, -2]) == "[1.0,0.5,-2.0]"


def test_lex_literal_shifts_signal_positions():
    lit = store_pg.lex_literal(["a", "b"], ["s"])
    assert lit == "'a':1 'b':2 's':3A"
