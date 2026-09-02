"""高斯 DataVec / PostgreSQL pgvector —— 同一套 SQL 的向量 + 词法存储层。

两种引擎的向量语法同形:`vector(n)` 类型、`<=>` 余弦距离、`USING hnsw (col vector_cosine_ops)`。
差别只在"向量类型从哪来":pgvector 要 `CREATE EXTENSION vector`,openGauss 7 内核自带。
启动时探测 `pg_type` 里有没有 `vector`,没有就退化为纯词法(表里不建 embedding 列),
并把结论记进 kb_meta——状态行要如实显示,不能假装有向量。

词法列 `lex tsvector` 的内容由 common.kb.text 在 Python 侧切好、以字面量写入,
不经引擎的 to_tsvector(见 text.py 的说明)。

这里只有确定性 I/O:建表、写块、查向量/词法、覆盖率、重建。分数解释与融合在 query.py。
"""
from __future__ import annotations

import json
import ssl
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from . import text as kbtext

CONNECT_TIMEOUT = 15
_SSL_MODES = frozenset({"allow", "prefer", "require", "verify-ca", "verify-full"})

DOC_KINDS = ("rule", "guide", "errata", "case", "raw")


class PgStoreError(Exception):
    """存储层的用户可读错误(连不上、建表失败、SQL 报错)。"""


@dataclass(frozen=True)
class Capabilities:
    engine: str            # opengauss | postgresql
    version: str
    vector: bool           # 有 vector 类型(DataVec 或 pgvector)
    hnsw: bool             # hnsw 索引建成功
    dims: int


@dataclass(frozen=True)
class DocRow:
    id: str
    kind: str              # rule | guide | errata | case | raw
    title: str
    source: str
    version: str
    meta: Dict[str, Any] = field(default_factory=dict)
    content_hash: str = ""


@dataclass(frozen=True)
class ChunkRow:
    id: str
    doc_id: str
    seq: int
    section: str
    content: str
    content_hash: str
    tokens: Sequence[str]
    signal_tokens: Sequence[str] = ()       # 复发标志等,tsvector 里权重 A
    embedding: Optional[Sequence[float]] = None


@dataclass(frozen=True)
class NodeVectorRow:
    node_id: str
    kind: str
    name: str
    tokens: Sequence[str]
    signal_tokens: Sequence[str] = ()
    signals: str = ""                       # 复发标志原文,查询时做相关度门槛用
    embedding: Optional[Sequence[float]] = None


@dataclass(frozen=True)
class Hit:
    id: str
    score: float           # 0..1,越大越相关(向量 = 1 - 余弦距离;词法 = ts_rank_cd 归一)
    kind: str
    doc_id: str = ""
    seq: int = 0
    section: str = ""
    content: str = ""
    title: str = ""
    source: str = ""
    meta: Dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------- literals

def vector_literal(values: Sequence[float]) -> str:
    """`[0.1,0.2,…]` —— DataVec 与 pgvector 共同的向量输入格式。"""
    return "[" + ",".join(repr(float(v)) for v in values) + "]"


def lex_literal(tokens: Sequence[str], signal_tokens: Sequence[str] = ()) -> str:
    """正文 token 无权重,信号 token 权重 A;两段拼成一个 tsvector 字面量。"""
    parts = [kbtext.tsvector_literal(tokens)]
    if signal_tokens:
        offset = len(list(tokens))
        shifted = kbtext.tsvector_literal(signal_tokens, weight="A")
        # tsvector 字面量里同一位置号可重复,但为可读性把信号段位置错开
        parts.append(_shift_positions(shifted, offset))
    return " ".join(p for p in parts if p)


def _shift_positions(literal: str, offset: int) -> str:
    import re

    def bump(m: "re.Match") -> str:
        return str(int(m.group(1)) + offset) + m.group(2)
    # 位置片段形如 :1A,3A —— 只动数字
    return re.sub(r"(?<=[:,])(\d+)([A-D]?)", bump, literal)


def _ssl_context(sslmode: str) -> Optional[ssl.SSLContext]:
    if sslmode not in _SSL_MODES:
        return None
    ctx = ssl.create_default_context()
    if sslmode in ("allow", "prefer", "require"):
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
    return ctx


def _pg_error(exc: Exception) -> str:
    args = getattr(exc, "args", None)
    if args and isinstance(args[0], dict):
        fields = args[0]
        code = fields.get("C", "")
        return f"{fields.get('M', exc)}" + (f" (SQLSTATE {code})" if code else "")
    return str(exc)


# ---------------------------------------------------------------- store

class PgStore:
    """一条 pg8000 连接上的知识库表操作。不是线程安全的;每个进程开一个。"""

    def __init__(self, raw: Any, dims: int, force_no_vector: bool = False):
        self._raw = raw
        self.dims = int(dims)
        self._force_no_vector = force_no_vector
        self._caps: Optional[Capabilities] = None

    # --- 连接 ---------------------------------------------------------------

    @classmethod
    def connect(cls, host: str, port: int, database: str, user: str, password: str,
                dims: int = 1024, sslmode: str = "", force_no_vector: bool = False) -> "PgStore":
        try:
            import pg8000.dbapi
        except ImportError as exc:  # pragma: no cover
            raise PgStoreError("缺少依赖 pg8000(requirements.txt 里有,离线环境用 wheel 装)") from exc
        try:
            raw = pg8000.dbapi.connect(host=host, port=int(port), database=database, user=user,
                                       password=password, timeout=CONNECT_TIMEOUT,
                                       ssl_context=_ssl_context(sslmode or "disable"))
        except Exception as exc:
            raise PgStoreError(f"连不上知识库存储 {user}@{host}:{port}/{database}:{_pg_error(exc)}") from exc
        raw.autocommit = True
        sock = getattr(raw, "_usock", None)
        if sock is not None and hasattr(sock, "settimeout"):
            sock.settimeout(None)
        return cls(raw, dims, force_no_vector=force_no_vector)

    def close(self) -> None:
        try:
            self._raw.close()
        except Exception:
            pass

    def __enter__(self) -> "PgStore":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    # --- 底层 ---------------------------------------------------------------

    def _query(self, sql: str, params: Sequence[Any] = ()) -> List[Tuple]:
        cur = self._raw.cursor()
        try:
            cur.execute(sql, tuple(params))
            return [tuple(r) for r in cur.fetchall()] if cur.description else []
        except Exception as exc:
            raise PgStoreError(f"{_pg_error(exc)}\nSQL: {sql.strip()[:200]}") from exc
        finally:
            cur.close()

    def _execute(self, sql: str, params: Sequence[Any] = ()) -> None:
        self._query(sql, params)

    def _transaction(self, statements: Iterable[Tuple[str, Sequence[Any]]]) -> None:
        """多条语句一个事务:全成或全败,不留半截文档。"""
        self._raw.autocommit = False
        cur = self._raw.cursor()
        try:
            for sql, params in statements:
                cur.execute(sql, tuple(params))
            self._raw.commit()
        except Exception as exc:
            try:
                self._raw.rollback()
            finally:
                pass
            raise PgStoreError(f"{_pg_error(exc)}") from exc
        finally:
            cur.close()
            self._raw.autocommit = True

    # --- 能力探测与建表 -----------------------------------------------------

    def capabilities(self) -> Capabilities:
        if self._caps is not None:
            return self._caps
        version = str(self._query("SELECT version()")[0][0])
        low = version.lower()
        engine = "opengauss" if ("opengauss" in low or "gaussdb" in low) else "postgresql"
        has_vector = False
        if not self._force_no_vector:
            has_vector = bool(self._query("SELECT 1 FROM pg_type WHERE typname = 'vector'"))
            if not has_vector and engine == "postgresql":
                try:
                    self._execute("CREATE EXTENSION IF NOT EXISTS vector")
                    has_vector = bool(self._query("SELECT 1 FROM pg_type WHERE typname = 'vector'"))
                except PgStoreError:
                    has_vector = False
        hnsw = self._meta_get("hnsw") == "true"
        self._caps = Capabilities(engine=engine, version=version, vector=has_vector,
                                  hnsw=hnsw, dims=self.dims)
        return self._caps

    def _has_table(self, name: str) -> bool:
        return bool(self._query(
            "SELECT 1 FROM information_schema.tables WHERE table_name = %s", (name,)))

    def _has_column(self, table: str, column: str) -> bool:
        return bool(self._query(
            "SELECT 1 FROM information_schema.columns WHERE table_name = %s AND column_name = %s",
            (table, column)))

    def _has_index(self, name: str) -> bool:
        return bool(self._query("SELECT 1 FROM pg_indexes WHERE indexname = %s", (name,)))

    def setup(self) -> Capabilities:
        """幂等建表建索引。返回能力;hnsw 建不成只告警(记进 meta),不吞。"""
        caps = self.capabilities()
        vec = f"vector({self.dims})"
        self._execute("""
            CREATE TABLE IF NOT EXISTS kb_docs (
                id text PRIMARY KEY, kind text NOT NULL, title text, source text, version text,
                meta jsonb, content_hash text, created_at timestamptz DEFAULT now())""")
        self._execute("""
            CREATE TABLE IF NOT EXISTS kb_chunks (
                id text PRIMARY KEY,
                doc_id text NOT NULL REFERENCES kb_docs(id) ON DELETE CASCADE,
                seq int NOT NULL, section text, content text NOT NULL, content_hash text,
                lex tsvector)""")
        self._execute("""
            CREATE TABLE IF NOT EXISTS kb_node_vectors (
                node_id text PRIMARY KEY, kind text NOT NULL, name text NOT NULL, lex tsvector, signals text)""")
        if not self._has_column("kb_node_vectors", "signals"):
            self._execute("ALTER TABLE kb_node_vectors ADD COLUMN signals text")
        self._execute("CREATE TABLE IF NOT EXISTS kb_meta (key text PRIMARY KEY, value text)")
        if not self._has_index("kb_chunks_doc_idx"):
            self._execute("CREATE INDEX kb_chunks_doc_idx ON kb_chunks (doc_id, seq)")
        if not self._has_index("kb_chunks_lex_idx"):
            self._execute("CREATE INDEX kb_chunks_lex_idx ON kb_chunks USING gin (lex)")
        if not self._has_index("kb_node_vectors_lex_idx"):
            self._execute("CREATE INDEX kb_node_vectors_lex_idx ON kb_node_vectors USING gin (lex)")

        hnsw = False
        if caps.vector:
            for table in ("kb_chunks", "kb_node_vectors"):
                if not self._has_column(table, "embedding"):
                    self._execute(f"ALTER TABLE {table} ADD COLUMN embedding {vec}")
            hnsw = True
            for table in ("kb_chunks", "kb_node_vectors"):
                idx = f"{table}_emb_idx"
                if self._has_index(idx):
                    continue
                try:
                    self._execute(
                        f"CREATE INDEX {idx} ON {table} USING hnsw (embedding vector_cosine_ops) "
                        f"WITH (m = 16, ef_construction = 64)")
                except PgStoreError as exc:
                    hnsw = False
                    import sys
                    print(f"警告:{table} 的 hnsw 索引建不成({exc});向量检索退化为顺序扫描,"
                          f"数据量大了会慢——引擎版本可能不支持 hnsw。", file=sys.stderr)
        self._meta_set("schema_version", "1")
        self._meta_set("engine", caps.engine)
        self._meta_set("vector_engine", ("datavec" if caps.engine == "opengauss" else "pgvector")
                       if caps.vector else "none")
        self._meta_set("hnsw", "true" if hnsw else "false")
        self._meta_set("dims", str(self.dims))
        self._caps = Capabilities(engine=caps.engine, version=caps.version, vector=caps.vector,
                                  hnsw=hnsw, dims=self.dims)
        return self._caps

    def rebuild(self) -> None:
        """清空派生索引(文件才是真相)。"""
        for table in ("kb_chunks", "kb_node_vectors", "kb_docs", "kb_meta"):
            self._execute(f"DROP TABLE IF EXISTS {table} CASCADE")
        self._caps = None
        self.setup()

    # --- meta -------------------------------------------------------------

    def _meta_get(self, key: str) -> Optional[str]:
        if not self._has_table("kb_meta"):
            return None
        rows = self._query("SELECT value FROM kb_meta WHERE key = %s", (key,))
        return str(rows[0][0]) if rows else None

    def _meta_set(self, key: str, value: str) -> None:
        self._execute("DELETE FROM kb_meta WHERE key = %s", (key,))
        self._execute("INSERT INTO kb_meta (key, value) VALUES (%s, %s)", (key, value))

    def meta_get(self, key: str) -> Optional[str]:
        return self._meta_get(key)

    def meta_set(self, key: str, value: str) -> None:
        self._meta_set(key, value)

    # --- 写 -----------------------------------------------------------------

    def upsert_doc(self, doc: DocRow, chunks: Sequence[ChunkRow]) -> None:
        """整篇替换:旧块删光再写新块,一个事务。"""
        has_vec = self.capabilities().vector
        stmts: List[Tuple[str, Sequence[Any]]] = [
            ("DELETE FROM kb_chunks WHERE doc_id = %s", (doc.id,)),
            ("DELETE FROM kb_docs WHERE id = %s", (doc.id,)),
            ("INSERT INTO kb_docs (id, kind, title, source, version, meta, content_hash) "
             "VALUES (%s, %s, %s, %s, %s, %s::jsonb, %s)",
             (doc.id, doc.kind, doc.title, doc.source, doc.version,
              json.dumps(doc.meta, ensure_ascii=False), doc.content_hash)),
        ]
        for ch in chunks:
            lex = lex_literal(ch.tokens, ch.signal_tokens)
            if has_vec:
                stmts.append((
                    "INSERT INTO kb_chunks (id, doc_id, seq, section, content, content_hash, lex, embedding) "
                    "VALUES (%s, %s, %s, %s, %s, %s, %s::tsvector, %s::vector)",
                    (ch.id, ch.doc_id, ch.seq, ch.section, ch.content, ch.content_hash, lex,
                     vector_literal(ch.embedding) if ch.embedding is not None else None)))
            else:
                stmts.append((
                    "INSERT INTO kb_chunks (id, doc_id, seq, section, content, content_hash, lex) "
                    "VALUES (%s, %s, %s, %s, %s, %s, %s::tsvector)",
                    (ch.id, ch.doc_id, ch.seq, ch.section, ch.content, ch.content_hash, lex)))
        self._transaction(stmts)

    def delete_doc(self, doc_id: str) -> None:
        self._transaction([
            ("DELETE FROM kb_chunks WHERE doc_id = %s", (doc_id,)),
            ("DELETE FROM kb_docs WHERE id = %s", (doc_id,)),
        ])

    def upsert_node_vectors(self, rows: Sequence[NodeVectorRow]) -> None:
        has_vec = self.capabilities().vector
        stmts: List[Tuple[str, Sequence[Any]]] = []
        for r in rows:
            stmts.append(("DELETE FROM kb_node_vectors WHERE node_id = %s", (r.node_id,)))
            lex = lex_literal(r.tokens, r.signal_tokens)
            if has_vec:
                stmts.append((
                    "INSERT INTO kb_node_vectors (node_id, kind, name, lex, signals, embedding) "
                    "VALUES (%s, %s, %s, %s::tsvector, %s, %s::vector)",
                    (r.node_id, r.kind, r.name, lex, r.signals,
                     vector_literal(r.embedding) if r.embedding is not None else None)))
            else:
                stmts.append((
                    "INSERT INTO kb_node_vectors (node_id, kind, name, lex, signals) VALUES (%s, %s, %s, %s::tsvector, %s)",
                    (r.node_id, r.kind, r.name, lex, r.signals)))
        if stmts:
            self._transaction(stmts)

    def delete_node_vectors(self, node_ids: Sequence[str]) -> None:
        if node_ids:
            self._transaction([("DELETE FROM kb_node_vectors WHERE node_id = %s", (n,)) for n in node_ids])

    def set_chunk_embedding(self, chunk_id: str, embedding: Sequence[float]) -> None:
        self._execute("UPDATE kb_chunks SET embedding = %s::vector WHERE id = %s",
                      (vector_literal(embedding), chunk_id))

    def set_node_embedding(self, node_id: str, embedding: Sequence[float]) -> None:
        self._execute("UPDATE kb_node_vectors SET embedding = %s::vector WHERE node_id = %s",
                      (vector_literal(embedding), node_id))

    # --- 读 -----------------------------------------------------------------

    def _kind_filter(self, kinds: Optional[Sequence[str]]) -> Tuple[str, List[Any]]:
        if not kinds:
            return "", []
        marks = ", ".join(["%s"] * len(kinds))
        return f" AND d.kind IN ({marks})", list(kinds)

    def search_chunks_lexical(self, query_tokens: Sequence[str], k: int = 10,
                              kinds: Optional[Sequence[str]] = None) -> List[Hit]:
        tsq = kbtext.tsquery_literal(query_tokens)
        if not tsq:
            return []
        where, params = self._kind_filter(kinds)
        rows = self._query(
            "SELECT c.id, c.doc_id, c.seq, c.section, c.content, d.kind, d.title, d.source, d.meta, "
            "       ts_rank_cd(c.lex, %s::tsquery, 32) AS score "
            "  FROM kb_chunks c JOIN kb_docs d ON d.id = c.doc_id "
            f" WHERE c.lex @@ %s::tsquery{where} "
            " ORDER BY score DESC, c.doc_id, c.seq LIMIT %s",
            [tsq, tsq] + params + [int(k)])
        return [self._chunk_hit(r) for r in rows]

    def search_chunks_vector(self, embedding: Sequence[float], k: int = 10,
                             kinds: Optional[Sequence[str]] = None) -> List[Hit]:
        if not self.capabilities().vector:
            return []
        where, params = self._kind_filter(kinds)
        lit = vector_literal(embedding)
        rows = self._query(
            "SELECT c.id, c.doc_id, c.seq, c.section, c.content, d.kind, d.title, d.source, d.meta, "
            "       1 - (c.embedding <=> %s::vector) AS score "
            "  FROM kb_chunks c JOIN kb_docs d ON d.id = c.doc_id "
            f" WHERE c.embedding IS NOT NULL{where} "
            " ORDER BY c.embedding <=> %s::vector LIMIT %s",
            [lit] + params + [lit, int(k)])
        return [self._chunk_hit(r) for r in rows]

    def search_nodes_lexical(self, query_tokens: Sequence[str], k: int = 10,
                             kinds: Optional[Sequence[str]] = None) -> List[Hit]:
        tsq = kbtext.tsquery_literal(query_tokens)
        if not tsq:
            return []
        where, params = "", []
        if kinds:
            where = " AND kind IN (" + ", ".join(["%s"] * len(kinds)) + ")"
            params = list(kinds)
        rows = self._query(
            "SELECT node_id, kind, name, ts_rank_cd(lex, %s::tsquery, 32) AS score, signals "
            "  FROM kb_node_vectors "
            f" WHERE lex @@ %s::tsquery{where} ORDER BY score DESC, node_id LIMIT %s",
            [tsq, tsq] + params + [int(k)])
        return [Hit(id=r[0], kind=r[1], title=r[2], score=float(r[3]), content=r[4] or "") for r in rows]

    def search_nodes_vector(self, embedding: Sequence[float], k: int = 10,
                            kinds: Optional[Sequence[str]] = None) -> List[Hit]:
        if not self.capabilities().vector:
            return []
        where, params = "", []
        if kinds:
            where = " AND kind IN (" + ", ".join(["%s"] * len(kinds)) + ")"
            params = list(kinds)
        lit = vector_literal(embedding)
        rows = self._query(
            "SELECT node_id, kind, name, 1 - (embedding <=> %s::vector) AS score, signals "
            "  FROM kb_node_vectors "
            f" WHERE embedding IS NOT NULL{where} ORDER BY embedding <=> %s::vector LIMIT %s",
            [lit] + params + [lit, int(k)])
        return [Hit(id=r[0], kind=r[1], title=r[2], score=float(r[3]), content=r[4] or "") for r in rows]

    @staticmethod
    def _chunk_hit(r: Tuple) -> Hit:
        meta = r[8]
        if isinstance(meta, str):
            try:
                meta = json.loads(meta)
            except ValueError:
                meta = {}
        return Hit(id=r[0], doc_id=r[1], seq=int(r[2]), section=r[3] or "", content=r[4] or "",
                   kind=r[5] or "", title=r[6] or "", source=r[7] or "", meta=meta or {},
                   score=float(r[9]))

    # --- 覆盖率与统计 -------------------------------------------------------

    def missing_embeddings(self, limit: int = 500) -> List[Tuple[str, str]]:
        """(chunk_id, content) —— 还没向量的块;没有向量引擎时返回空。"""
        if not self.capabilities().vector:
            return []
        return [(r[0], r[1]) for r in self._query(
            "SELECT id, content FROM kb_chunks WHERE embedding IS NULL ORDER BY doc_id, seq LIMIT %s",
            (int(limit),))]

    def missing_node_embeddings(self, limit: int = 500) -> List[Tuple[str, str]]:
        if not self.capabilities().vector:
            return []
        return [(r[0], r[1]) for r in self._query(
            "SELECT node_id, name FROM kb_node_vectors WHERE embedding IS NULL ORDER BY node_id LIMIT %s",
            (int(limit),))]

    def coverage(self) -> Tuple[int, int]:
        """(切块总数, 有向量的块数)。无向量引擎时第二项恒 0。"""
        total = int(self._query("SELECT count(*) FROM kb_chunks")[0][0])
        if not self.capabilities().vector:
            return total, 0
        done = int(self._query("SELECT count(*) FROM kb_chunks WHERE embedding IS NOT NULL")[0][0])
        return total, done

    def counts(self) -> Dict[str, int]:
        out: Dict[str, int] = {}
        for kind, n in self._query("SELECT kind, count(*) FROM kb_docs GROUP BY kind"):
            out[f"docs.{kind}"] = int(n)
        out["chunks"] = int(self._query("SELECT count(*) FROM kb_chunks")[0][0])
        out["nodes"] = int(self._query("SELECT count(*) FROM kb_node_vectors")[0][0])
        return out

    def doc_hashes(self) -> Dict[str, str]:
        """id → content_hash,增量索引靠它判断哪些文件变了。"""
        return {r[0]: (r[1] or "") for r in self._query("SELECT id, content_hash FROM kb_docs")}

    def docs_by_ids(self, ids: Sequence[str]) -> Dict[str, DocRow]:
        if not ids:
            return {}
        marks = ", ".join(["%s"] * len(ids))
        out: Dict[str, DocRow] = {}
        for r in self._query(f"SELECT id, kind, title, source, version, meta, content_hash FROM kb_docs WHERE id IN ({marks})",
                             list(ids)):
            meta = r[5]
            if isinstance(meta, str):
                try:
                    meta = json.loads(meta)
                except ValueError:
                    meta = {}
            out[r[0]] = DocRow(id=r[0], kind=r[1] or "", title=r[2] or "", source=r[3] or "",
                               version=r[4] or "", meta=meta or {}, content_hash=r[6] or "")
        return out

    def doc_sections(self, doc_id: str) -> Dict[str, str]:
        """section → 正文(去掉面包屑首行);案例的「处置」「复发标志」渲染时要用。"""
        out: Dict[str, str] = {}
        for section, content in self._query(
                "SELECT section, content FROM kb_chunks WHERE doc_id = %s ORDER BY seq", (doc_id,)):
            body = content or ""
            if "\n" in body and " › " in body.split("\n", 1)[0]:
                body = body.split("\n", 1)[1]
            out.setdefault(section or "", body)
        return out

    def has_index(self) -> bool:
        return self._has_table("kb_docs")
