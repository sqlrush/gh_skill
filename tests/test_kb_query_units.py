"""common.kb.query / render —— 假存储钉住融合、阈值、降级与渲染纪律(无库)。"""
import pathlib
import sys
from dataclasses import dataclass

import pytest

_ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT))

from common.kb import config as kbconfig, query, render, store_graph as sg, store_pg as spg  # noqa: E402

try:                                              # 部署仓有 common.finding;主干还没有,用同形的本地替身
    from common.finding import Finding, Severity  # noqa: E402
except ImportError:                               # pragma: no cover
    from enum import IntEnum

    class Severity(IntEnum):
        OK = 0; NOTICE = 1; WARN = 2; CRITICAL = 3

        def label(self):
            return {3: "🔴严重", 2: "🟠告警", 1: "🟡关注"}.get(int(self), "🟢健康")

    @dataclass(frozen=True)
    class Finding:
        dimension: str; code: str; severity: Severity; metric: str; value: str
        threshold: str; evidence: str; sql_id: str = ""; skill: str = ""


def _hit(id, doc_id, kind, score, content="内容", title="标题", section="现场", meta=None, source="src"):
    return spg.Hit(id=id, doc_id=doc_id, kind=kind, score=score, content=content, title=title,
                   section=section, meta=meta or {}, source=source, seq=0)


class FakePg:
    def __init__(self, lex=(), vec=(), nodes_lex=(), nodes_vec=(), sections=None, docs=None, vector=True):
        self.lex, self.vec, self.nodes_lex, self.nodes_vec = list(lex), list(vec), list(nodes_lex), list(nodes_vec)
        self._sections = sections or {}
        self._docs = docs or {}
        self._vector = vector
        self.closed = False

    # 只对含 autovacuum 的查询命中,别的查询一无所获——这样才能同时测"有命中"和"明写无"。
    @staticmethod
    def _relevant(tokens): return any("autovacuum" in t for t in tokens)
    def has_index(self): return True
    def capabilities(self): return spg.Capabilities("postgresql", "16", self._vector, True, 4)
    def search_chunks_lexical(self, tokens, k=10, kinds=None): return self.lex[:k] if self._relevant(tokens) else []
    def search_chunks_vector(self, emb, k=10, kinds=None): return self.vec[:k]
    def search_nodes_lexical(self, tokens, k=10, kinds=None): return self.nodes_lex[:k] if self._relevant(tokens) else []
    def search_nodes_vector(self, emb, k=10, kinds=None): return self.nodes_vec[:k]
    def doc_sections(self, doc_id): return self._sections.get(doc_id, {})
    def docs_by_ids(self, ids): return {i: self._docs[i] for i in ids if i in self._docs}
    def coverage(self): return (10, 10)
    def counts(self): return {"docs.rule": 2, "docs.case": 3, "docs.raw": 1, "chunks": 10, "nodes": 4}
    def meta_get(self, k): return "pgvector" if k == "vector_engine" else None
    def close(self): self.closed = True


class FakeGraph:
    def __init__(self, paths=(), clauses=None, fail=False):
        self._paths, self._clauses, self.fail = list(paths), clauses or {}, fail
        self.asked = []

    def ping(self): return "Neo4j 5"
    def paths(self, ids, today, min_confidence=1.0, limit=20):
        self.asked.append(list(ids))
        if self.fail:
            raise sg.GraphStoreError("Neo4j 不可达(测试)")
        return self._paths if ids else []
    def clauses_for(self, ids, min_confidence=1.0): return {i: self._clauses[i] for i in ids if i in self._clauses}
    def counts(self): return {"edges": 7, "edges.confirmed": 6}


class FakeEmbedder:
    def __init__(self, fail=False): self.fail = fail
    def embed_one(self, text, timeout_s=None): return None if self.fail else [1, 0, 0, 0]


def _session(tmp_path, pg, graph=None, embedder=None, thresholds=None):
    cfg = kbconfig.KbConfig(kb_dir=tmp_path, store=kbconfig.StoreConfig(), embeddings=kbconfig.EmbeddingConfig(),
                            thresholds=thresholds or kbconfig.Thresholds(), defaults={})
    (tmp_path / "VERSION").write_text("2026.09\n", encoding="utf-8")
    return query.KbSession(cfg, pg, graph, embedder)


CASE_ID = "S1-20250224-CBST-偶现单条update慢"


def _rich_pg():
    return FakePg(
        lex=[_hit("case:%s#1" % CASE_ID, "case:" + CASE_ID, "case", 0.30, "偶现 update › 现场\n业务偶现单条update耗时3s",
                  "偶现单条 update 慢", meta={"conclusion": "已确认", "occurred_at": "2025-02-24"}),
             _hit("rule:GS-VAC-002#0", "rule:GS-VAC-002", "rule", 0.20, "GS-VAC-002 小表按表级调大阈值",
                  "小表 autovacuum 阈值", meta={"severity": "warn"}, source="《运维规范》v5 §6.2"),
             _hit("raw:q1/T-100#0", "raw:q1/T-100", "raw", 0.05, "工单 T-100 原文", "工单 T-100")],
        vec=[_hit("case:%s#2" % CASE_ID, "case:" + CASE_ID, "case", 0.80, "偶现 update › 判断\n持8级锁",
                  "偶现单条 update 慢", meta={"conclusion": "已确认", "occurred_at": "2025-02-24"})],
        nodes_lex=[spg.Hit(id="symptom:update_slow", kind="symptom", title="单条 update 偶发秒级", score=0.4)],
        sections={"case:" + CASE_ID: {"处置": "针对小表调大 autovacuum_vacuum_threshold", "现场": "x"}},
        docs={"rule:GS-VAC-002": spg.DocRow(id="rule:GS-VAC-002", kind="rule", title="小表 autovacuum 阈值",
                                             source="《运维规范》v5 §6.2", version="", meta={"severity": "warn"})})


def _path():
    return sg.PathHit("symptom:update_slow", "单条 update 偶发秒级", "rootcause:x", "autovacuum 持 8 级锁",
                      "action:y", "表级调大 autovacuum_vacuum_threshold", ("case:" + CASE_ID,), ("s1", "s2"), 1.0)


# ---------------------------------------------------------------- rrf (pure)

def test_rrf_normalizes_to_one_for_top_of_every_list():
    a = _hit("x", "d", "case", 0.9)
    fused = query.rrf([[a], [a]])
    assert fused["x"][0] == pytest.approx(1.0)
    only_first = query.rrf([[a], [_hit("y", "e", "case", 0.9)]])
    assert only_first["x"][0] == pytest.approx(0.5)
    single = query.rrf([[a]])
    assert single["x"][0] == pytest.approx(1.0)


def test_rrf_keeps_raw_scores_per_source():
    a_lex = _hit("x", "d", "case", 0.1)
    a_vec = _hit("x", "d", "case", 0.9)
    fused = query.rrf([[a_lex], [a_vec]])
    assert fused["x"][2] == 0.1 and fused["x"][3] == 0.9


def test_identifiers_in_picks_objects_and_gucs():
    ids = query.identifiers_in("XACT_LONG 长事务 cbst.cosp_asyn_task_dtl autovacuum_vacuum_threshold LWLock:WALWriteLock 3s")
    assert "cbst.cosp_asyn_task_dtl" in ids and "autovacuum_vacuum_threshold" in ids and "lwlock:walwritelock" in ids
    assert "3s" not in ids


# ---------------------------------------------------------------- search

def test_search_returns_clause_case_and_path(tmp_path):
    sess = _session(tmp_path, _rich_pg(), FakeGraph([_path()]), FakeEmbedder())
    refs = sess.search("VAC_FREQ", "🟠 VAC_FREQ", "VAC_FREQ autovacuum 次数异常高 cbst.cosp_asyn_task_dtl")
    assert [c.short_id for c in refs.clauses] == ["GS-VAC-002"]
    assert [c.short_id for c in refs.cases] == [CASE_ID]
    assert refs.cases[0].sections["处置"].startswith("针对小表")
    assert len(refs.paths) == 1 and refs.paths[0].cases == (CASE_ID,) and refs.paths[0].support == 1
    assert refs.raws and refs.raws[0].short_id == "q1/T-100"
    assert not refs.notes


def test_search_drops_weak_hits_and_logs_miss(tmp_path):
    """词法分低于下限、又没向量佐证的命中不许凑数;整条查不到要记进 misses.log。"""
    weak = FakePg(lex=[_hit("case:a#0", "case:a", "case", 0.001)])
    sess = _session(tmp_path, weak, FakeGraph(), None)
    refs = sess.search("IDX_UNUSED", "🟡 IDX_UNUSED", "IDX_UNUSED 未使用索引")
    assert refs.empty
    log = (tmp_path / "index" / "misses.log").read_text(encoding="utf-8")
    assert "IDX_UNUSED" in log


def test_search_without_graph_has_no_paths_but_still_cases(tmp_path):
    sess = _session(tmp_path, _rich_pg(), None, None)
    refs = sess.search("k", "l", "autovacuum 次数异常高")
    assert refs.cases and refs.paths == ()


def test_vector_timeout_degrades_with_note(tmp_path):
    sess = _session(tmp_path, _rich_pg(), FakeGraph([_path()]), FakeEmbedder(fail=True))
    refs = sess.search("k", "l", "autovacuum 次数异常高")
    assert refs.cases                                   # 词法仍然命中
    assert any("超时" in n for n in refs.notes)
    assert "本次超时" in sess.status().vector


def test_graph_failure_degrades_not_raises(tmp_path):
    sess = _session(tmp_path, _rich_pg(), FakeGraph(fail=True), None)
    refs = sess.search("k", "l", "autovacuum 次数异常高")
    assert refs.cases and refs.paths == ()
    assert any("降级" in n for n in sess.notes)


def test_graph_clauses_for_objects_added_even_without_lexical_hit(tmp_path):
    pg = _rich_pg(); pg.lex = []; pg.vec = []
    graph = FakeGraph(clauses={"object:cbst.cosp_asyn_task_dtl": ["clause:GS-VAC-002"]})
    sess = _session(tmp_path, pg, graph, None)
    refs = sess.search("k", "l", "VAC_FREQ cbst.cosp_asyn_task_dtl")
    assert [c.short_id for c in refs.clauses] == ["GS-VAC-002"]


# ---------------------------------------------------------------- entry points

def _finding(code="VAC_FREQ", sev=Severity.WARN):
    if code == "IDX_UNUSED":
        return Finding(dimension="index", code=code, severity=sev, metric="未使用索引", value="3",
                       threshold="0", evidence="idx_order_ts 自上次统计以来 idx_scan = 0")
    return Finding(dimension="vacuum", code=code, severity=sev, metric="autovacuum 次数/h", value="37",
                   threshold="20", evidence="cbst.cosp_asyn_task_dtl autovacuum 次数异常高")


def test_from_findings_uses_session_and_labels(tmp_path):
    sess = _session(tmp_path, _rich_pg(), FakeGraph([_path()]), None)
    res = query.from_findings([_finding(), _finding("IDX_UNUSED", Severity.NOTICE)], session=sess)
    assert res.status.attached and res.status.counts["docs.case"] == 3
    assert [it.key for it in res.items] == ["VAC_FREQ", "IDX_UNUSED"]
    assert res.items[0].label.startswith("🟠告警 VAC_FREQ(autovacuum 次数/h = 37)")
    assert not sess.pg.closed                            # 外部会话由调用方关


def test_from_findings_never_raises_when_unattached(tmp_path):
    res = query.from_findings([_finding()], kb_dir=tmp_path / "nope")
    assert res.status.attached is False and "不存在" in res.status.reason and res.items == ()


def test_open_reports_missing_store_config(tmp_path):
    (tmp_path / "kb.yaml").write_text("embeddings: {source: none}\n", encoding="utf-8")
    sess = query.KbSession.open(tmp_path)
    assert not sess.attached and "store.pg" in sess.reason


def test_open_reports_credential_failure(tmp_path):
    (tmp_path / "kb.yaml").write_text(
        "store:\n  pg: {host: 127.0.0.1, port: 1, database: d, user: u, credential: kb}\n", encoding="utf-8")
    sess = query.KbSession.open(tmp_path, password_lookup=lambda n: (_ for _ in ()).throw(RuntimeError("no cred")))
    assert not sess.attached and "口令" in sess.reason


# ---------------------------------------------------------------- render

def test_render_unattached_is_title_plus_reason_only(tmp_path):
    res = query.from_findings([_finding()], kb_dir=tmp_path / "nope")
    out = render.render_section(res)
    assert out.startswith("## 客户知识库参照\n> 知识库未接入(")
    assert "贵行规范" not in out


def test_render_item_has_all_four_lines_and_explicit_none(tmp_path):
    sess = _session(tmp_path, _rich_pg(), FakeGraph([_path()]), None)
    res = query.from_findings([_finding(), _finding("IDX_UNUSED", Severity.NOTICE)], session=sess)
    out = render.render_section(res)
    assert "> 知识库 v2026.09 · 条款 2 · 案例 3 · 原始工单 1 · 向量:" in out
    assert "### 对 🟠告警 VAC_FREQ" in out
    assert "- **贵行规范** GS-VAC-002《小表 autovacuum 阈值》(warn) ——《运维规范》v5 §6.2" in out
    assert f"- **历史相似** {CASE_ID}(结论强度:已确认,2025-02-24):处置 = 针对小表调大 autovacuum_vacuum_threshold" in out
    assert "- **本行历史路径** 单条 update 偶发秒级 → autovacuum 持 8 级锁 → 表级调大 autovacuum_vacuum_threshold (1 案例支持:" in out
    assert "- **原始工单** q1/T-100(未结构化)" in out
    # 第二条发现没命中:必须明写「无」,不能省略
    assert "### 对 🟡关注 IDX_UNUSED" in out
    assert "- 贵行规范:无对应条款 · 历史相似:无相似案例 · 路径:无" in out
    assert "违规汇总" not in out


def test_render_partial_hits_state_missing_kinds(tmp_path):
    sess = _session(tmp_path, _rich_pg(), None, None)
    res = query.from_findings([_finding()], session=sess)
    out = render.render_section(res)
    assert "- 本行历史路径:无(没有已确认的 现象→根因→处置 链)" in out
    assert "- **历史相似**" in out
