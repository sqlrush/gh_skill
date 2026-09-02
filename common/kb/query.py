"""混合检索编排 —— 按"发现"查库:词法 ∥ 向量 ∥ 图扩展 → RRF 融合 → 分类型 top-k → 阈值。

给两类调用方:
  · 诊断 skill 的脚本:`from_findings(findings)`——确定性入口,每条 finding 一组引用;
  · 会话里的模型:`from_text(q)`——纯问答路径。
两者都**永不抛异常**:知识库不可达 / 没配 / 没索引,都变成 KbStatus.attached=False + 原因,
由 render.py 写成「知识库未接入(原因)」——skill 本身照常。

检索层的两条纪律:
  · 向量给入口(像什么),图给链路(现象→根因→处置,只走 confidence=1 且生效的边);
  · 分数阈值——低于门槛整类返回空,由渲染层明写「无」,不凑数。
"""
from __future__ import annotations

import datetime
import pathlib
import re
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from . import config as kbconfig
from . import store_graph as sg
from . import store_pg as spg
from . import text as kbtext
from .embed import Embedder, EmbedError
from .indexer import read_state

RRF_K = 60
TIME_BUDGET_S = 3.0
_IDENT_RE = re.compile(r"^[a-z_][a-z0-9_]*(?:[.:][a-z0-9_]+)*$")


@dataclass(frozen=True)
class Ref:
    id: str                 # 文档 id,如 case:S1-… / rule:GS-…
    kind: str
    title: str
    score: float
    snippet: str = ""
    source: str = ""
    meta: Dict[str, Any] = field(default_factory=dict)
    sections: Dict[str, str] = field(default_factory=dict)

    @property
    def short_id(self) -> str:
        return self.id.split(":", 1)[1] if ":" in self.id else self.id


@dataclass(frozen=True)
class PathRef:
    symptom: str
    rootcause: str
    action: str
    cases: Tuple[str, ...]
    sources: Tuple[str, ...]

    @property
    def support(self) -> int:
        return len(self.cases)


@dataclass(frozen=True)
class FindingRefs:
    key: str                # finding code 或 "q"
    label: str              # 渲染标题,如 "🟠 VAC_FREQ(…)"
    query: str
    clauses: Tuple[Ref, ...] = ()
    cases: Tuple[Ref, ...] = ()
    paths: Tuple[PathRef, ...] = ()
    raws: Tuple[Ref, ...] = ()
    guides: Tuple[Ref, ...] = ()
    notes: Tuple[str, ...] = ()

    @property
    def empty(self) -> bool:
        return not (self.clauses or self.cases or self.paths or self.raws or self.guides)


@dataclass(frozen=True)
class KbStatus:
    attached: bool
    reason: str = ""
    version: str = ""
    counts: Dict[str, int] = field(default_factory=dict)
    vector: str = "未启用"
    graph: str = "未配置"


@dataclass(frozen=True)
class QueryResult:
    status: KbStatus
    items: Tuple[FindingRefs, ...] = ()
    elapsed_ms: int = 0


# ---------------------------------------------------------------- fusion (pure)

def rrf(lists: Sequence[Sequence[spg.Hit]], k: int = RRF_K) -> Dict[str, Tuple[float, spg.Hit, float, float]]:
    """id → (归一融合分, 代表 hit, 词法原始分, 向量原始分)。

    归一:除以"在每个列表都排第一"的分,两列表下单列表第一 = 0.5,单列表模式第一 = 1.0。
    """
    active = [lst for lst in lists if lst is not None]
    if not active:
        return {}
    fused: Dict[str, float] = {}
    best: Dict[str, spg.Hit] = {}
    raw = {}
    for li, lst in enumerate(active):
        for rank, hit in enumerate(lst, 1):
            fused[hit.id] = fused.get(hit.id, 0.0) + 1.0 / (k + rank)
            if hit.id not in best or hit.score > best[hit.id].score:
                best[hit.id] = hit
            lex, vec = raw.get(hit.id, (0.0, 0.0))
            if li == 0:
                lex = max(lex, hit.score)
            else:
                vec = max(vec, hit.score)
            raw[hit.id] = (lex, vec)
    top = len(active) / (k + 1)
    return {i: (s / top, best[i], raw[i][0], raw[i][1]) for i, s in fused.items()}


def identifiers_in(text: str) -> List[str]:
    """finding 证据里像对象名 / GUC / 等待事件的整词,用来直接查图里的约束条款。"""
    out = []
    for tok in kbtext.tokenize(text):
        if _IDENT_RE.match(tok) and ("." in tok or "_" in tok or ":" in tok) and tok not in out:
            out.append(tok)
    return out


# ---------------------------------------------------------------- session

class KbSession:
    """一次调用内共用的连接。open() 永不抛:连不上就是 attached=False。"""

    def __init__(self, cfg: Optional[kbconfig.KbConfig], pg: Optional[spg.PgStore],
                 graph: Optional[sg.GraphStore], embedder: Optional[Embedder],
                 reason: str = "", notes: Sequence[str] = ()):
        self.cfg = cfg
        self.pg = pg
        self.graph = graph
        self.embedder = embedder
        self.reason = reason
        self.notes: List[str] = list(notes)
        self._vector_failed = False

    @classmethod
    def open(cls, kb_dir: Optional[pathlib.Path] = None,
             password_lookup: Optional[Callable[[str], str]] = None) -> "KbSession":
        kb = pathlib.Path(kb_dir) if kb_dir else kbconfig.resolve_kb_dir(None)
        if not kb.is_dir():
            return cls(None, None, None, None, reason=f"知识库目录不存在:{kb}")
        try:
            cfg = kbconfig.load(kb)
        except kbconfig.KbConfigError as exc:
            return cls(None, None, None, None, reason=f"kb.yaml 无效:{exc}")
        if cfg.store.pg is None:
            return cls(cfg, None, None, None, reason="kb.yaml 未配置 store.pg(向量/词法存储)")
        lookup = password_lookup or _default_password_lookup
        try:
            pw = lookup(cfg.store.pg.credential)
        except Exception as exc:
            return cls(cfg, None, None, None, reason=f"取不到存储口令 {cfg.store.pg.credential}:{exc}")
        try:
            pg = spg.PgStore.connect(cfg.store.pg.host, cfg.store.pg.port, cfg.store.pg.database,
                                     cfg.store.pg.user, pw, dims=cfg.embeddings.dims,
                                     sslmode=cfg.store.pg.sslmode)
            if not pg.has_index():
                pg.close()
                return cls(cfg, None, None, None, reason="存储里还没有索引(先运行 kb.py index)")
            caps = pg.capabilities()
        except spg.PgStoreError as exc:
            return cls(cfg, None, None, None, reason=str(exc))

        notes: List[str] = []
        graph: Optional[sg.GraphStore] = None
        if cfg.store.graph is not None:
            try:
                gpw = lookup(cfg.store.graph.credential)
                graph = sg.GraphStore(cfg.store.graph.url, cfg.store.graph.user, gpw,
                                      database=cfg.store.graph.database, timeout_s=5.0)
                graph.ping()
            except Exception as exc:
                notes.append(f"图:不可用({exc})")
                graph = None
        embedder: Optional[Embedder] = None
        if caps.vector:
            try:
                embedder = Embedder.from_config(cfg)
                if embedder is None:
                    notes.append("向量:未启用(kb.yaml 未配 embeddings)")
            except (EmbedError, kbconfig.KbConfigError) as exc:
                notes.append(f"向量:未启用({exc})")
        else:
            notes.append("向量:未启用(存储引擎无 vector 类型)")
        return cls(cfg, pg, graph, embedder, notes=notes)

    def close(self) -> None:
        if self.pg is not None:
            self.pg.close()

    @property
    def attached(self) -> bool:
        return self.pg is not None

    def status(self) -> KbStatus:
        if not self.attached or self.cfg is None or self.pg is None:
            return KbStatus(attached=False, reason=self.reason or "未接入")
        counts = self.pg.counts()
        state = read_state(self.cfg.kb_dir) or {}
        version = ""
        try:
            version = (self.cfg.kb_dir / "VERSION").read_text(encoding="utf-8").strip()
        except OSError:
            pass
        caps = self.pg.capabilities()
        if caps.vector and self.embedder is not None:
            total, done = self.pg.coverage()
            engine = self.pg.meta_get("vector_engine") or "vector"
            pct = f"{done * 100 // total}%" if total else "0%"
            vector = f"{engine}(覆盖 {pct})" + ("·本次超时未用" if self._vector_failed else "")
        else:
            vector = next((n.split(":", 1)[1] for n in self.notes if n.startswith("向量:")), "未启用")
        if self.graph is not None:
            try:
                gc = self.graph.counts()
                graph = f"Neo4j {gc.get('edges.confirmed', 0)} 条已确认边"
            except sg.GraphStoreError as exc:
                graph = f"不可用({exc})"
        else:
            graph = next((n.split(":", 1)[1] for n in self.notes if n.startswith("图:")), "未配置")
        return KbStatus(attached=True, version=version or str(state.get("kb_version", "")),
                        counts=counts, vector=vector, graph=graph)

    # --- 检索 -------------------------------------------------------------

    def search(self, key: str, label: str, q_text: str, objects: Sequence[str] = ()) -> FindingRefs:
        if not self.attached or self.cfg is None or self.pg is None:
            return FindingRefs(key=key, label=label, query=q_text)
        th = self.cfg.thresholds
        started = time.monotonic()
        notes: List[str] = []
        idents = identifiers_in(q_text + " " + " ".join(objects))
        tokens = kbtext.query_tokens(q_text + " " + " ".join(objects))

        emb: Optional[List[float]] = None
        if self.embedder is not None:
            try:
                emb = self.embedder.embed_one(q_text, timeout_s=self.cfg.embeddings.query_timeout_s)
            except Exception:
                emb = None
            if emb is None:
                self._vector_failed = True
                notes.append("向量本次超时/失败,只用词法 + 图")

        lex = self._safe(lambda: self.pg.search_chunks_lexical(tokens, k=30)) or []
        vec = self._safe(lambda: self.pg.search_chunks_vector(emb, k=30)) if emb is not None else None
        fused = rrf([lex, vec] if vec is not None else [lex])
        by_kind: Dict[str, List[Tuple[float, spg.Hit]]] = {}
        seen_docs: Dict[str, float] = {}
        for _id, (score, hit, lex_s, vec_s) in fused.items():
            if lex_s < th.lexical_min and vec_s < th.vector_min:
                continue
            if score > seen_docs.get(hit.doc_id, -1.0):
                seen_docs[hit.doc_id] = score
                by_kind.setdefault(hit.kind, [])
                by_kind[hit.kind] = [(s, h) for s, h in by_kind[hit.kind] if h.doc_id != hit.doc_id] + [(score, hit)]
        for k in by_kind:
            by_kind[k].sort(key=lambda p: (-p[0], p[1].doc_id))

        clauses = self._refs(by_kind.get("rule", []), th.clause, th.top_clause)
        cases = self._refs(by_kind.get("case", []), th.case, th.top_case, with_sections=True)
        raws = self._refs(by_kind.get("raw", []), th.chunk, th.top_raw)
        guides = self._refs(by_kind.get("guide", []) + by_kind.get("errata", []), th.chunk, th.top_guide)

        # 图:现象节点 → 路径;对象 → 条款
        paths: List[PathRef] = []
        if self.graph is not None and time.monotonic() - started < TIME_BUDGET_S:
            nl = self._safe(lambda: self.pg.search_nodes_lexical(tokens, k=10, kinds=["symptom"])) or []
            nv = self._safe(lambda: self.pg.search_nodes_vector(emb, k=10, kinds=["symptom"])) if emb is not None else None
            nfused = rrf([nl, nv] if nv is not None else [nl])
            symptom_ids = [i for i, (s, h, l, v) in sorted(nfused.items(), key=lambda p: -p[1][0])
                           if s >= th.symptom and (l >= th.lexical_min or v >= th.vector_min)][:5]
            today = datetime.date.today().isoformat()
            hits = self._safe(lambda: self.graph.paths(symptom_ids, today=today)) or []
            for p in hits[:th.top_path]:
                paths.append(PathRef(symptom=p.symptom, rootcause=p.rootcause, action=p.action,
                                     cases=tuple(c.split(":", 1)[1] if c.startswith("case:") else c for c in p.cases),
                                     sources=p.sources))
            if idents:
                node_ids = [f"{kind}:{i}" for i in idents for kind in ("object", "guc", "wait_event", "error")]
                clause_map = self._safe(lambda: self.graph.clauses_for(node_ids)) or {}
                extra = [cid for ids in clause_map.values() for cid in ids]
                have = {c.id for c in clauses}
                extra_docs = self._safe(lambda: self.pg.docs_by_ids(
                    [f"rule:{c.split(':', 1)[1]}" for c in extra if f"rule:{c.split(':', 1)[1]}" not in have])) or {}
                for doc in extra_docs.values():
                    clauses = clauses + (Ref(id=doc.id, kind="rule", title=doc.title, score=1.0,
                                             source=doc.source, meta=doc.meta),)
        elif self.graph is None:
            pass

        refs = FindingRefs(key=key, label=label, query=q_text, clauses=tuple(clauses), cases=tuple(cases),
                           paths=tuple(paths), raws=tuple(raws), guides=tuple(guides), notes=tuple(notes))
        if not refs.clauses and not refs.cases and not refs.paths:
            self._log_miss(key, q_text)
        return refs

    def _refs(self, ranked: Sequence[Tuple[float, spg.Hit]], floor: float, top: int,
              with_sections: bool = False) -> Tuple[Ref, ...]:
        out: List[Ref] = []
        for score, hit in ranked:
            if score < floor or len(out) >= top:
                break
            sections = self._safe(lambda: self.pg.doc_sections(hit.doc_id)) if with_sections else None
            snippet = hit.content.split("\n", 1)[1] if " › " in hit.content.split("\n", 1)[0] else hit.content
            out.append(Ref(id=hit.doc_id, kind=hit.kind, title=hit.title, score=round(score, 3),
                           snippet=snippet[:160], source=hit.source, meta=hit.meta, sections=sections or {}))
        return tuple(out)

    def _safe(self, fn: Callable[[], Any]) -> Any:
        try:
            return fn()
        except (spg.PgStoreError, sg.GraphStoreError, OSError) as exc:
            self.notes.append(f"检索降级:{exc}")
            return None

    def _log_miss(self, key: str, q_text: str) -> None:
        if self.cfg is None:
            return
        try:
            path = self.cfg.kb_dir / "index" / "misses.log"
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("a", encoding="utf-8") as fh:
                fh.write(f"{datetime.date.today().isoformat()}\t{key}\t{q_text[:80]}\n")
        except OSError:
            pass


def _default_password_lookup(name: str) -> str:
    from ..credential import load_secret
    return load_secret(name)


# ---------------------------------------------------------------- entry points

def finding_query_text(f: Any) -> str:
    """Finding → 检索文本:code + 指标 + 证据 + 维度(共用 common.finding.Finding 的字段名)。"""
    parts = [str(getattr(f, "code", "") or ""), str(getattr(f, "dimension", "") or ""),
             str(getattr(f, "metric", "") or ""), str(getattr(f, "evidence", "") or "")]
    return " ".join(p for p in parts if p)


def finding_label(f: Any) -> str:
    sev = getattr(f, "severity", None)
    label = sev.label() if hasattr(sev, "label") else str(sev or "")
    metric = str(getattr(f, "metric", "") or "")
    value = str(getattr(f, "value", "") or "")
    detail = f"{metric} = {value}" if metric and value else metric or value
    return f"{label} {getattr(f, 'code', '')}" + (f"({detail})" if detail else "")


def from_findings(findings: Sequence[Any], kb_dir: Optional[pathlib.Path] = None,
                  session: Optional[KbSession] = None) -> QueryResult:
    """诊断 skill 的确定性入口:每条 finding 一组引用;永不抛。"""
    started = time.monotonic()
    own = session is None
    sess = session or KbSession.open(kb_dir)
    try:
        items = tuple(sess.search(str(getattr(f, "code", "") or f"#{i}"), finding_label(f),
                                  finding_query_text(f), objects=())
                      for i, f in enumerate(findings)) if sess.attached else ()
        status = sess.status()
    except Exception as exc:                      # 兜底:知识库任何异常都不许炸掉 skill
        status = KbStatus(attached=False, reason=f"知识库检索异常:{exc}")
        items = ()
    finally:
        if own:
            sess.close()
    return QueryResult(status=status, items=items, elapsed_ms=int((time.monotonic() - started) * 1000))


def from_text(q: str, kb_dir: Optional[pathlib.Path] = None,
              session: Optional[KbSession] = None) -> QueryResult:
    """纯问答入口(kb.py query --q)。"""
    started = time.monotonic()
    own = session is None
    sess = session or KbSession.open(kb_dir)
    try:
        items = (sess.search("q", q[:60], q),) if sess.attached else ()
        status = sess.status()
    except Exception as exc:
        status = KbStatus(attached=False, reason=f"知识库检索异常:{exc}")
        items = ()
    finally:
        if own:
            sess.close()
    return QueryResult(status=status, items=items, elapsed_ms=int((time.monotonic() - started) * 1000))
