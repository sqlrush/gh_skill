"""kb.py 的存储侧子命令:setup / index / query / health / feedback / eval。

全部确定性:连库、建表、索引、检索、打分。模型不参与。
口令来自 common.credential(凭据名写在 kb.yaml),这里不接收、不打印口令。
"""
from __future__ import annotations

import argparse
import datetime
import json
import pathlib
import sys
from typing import Any, Dict, List, Optional, Tuple

import yaml

from common.kb import config as kbconfig
from common.kb import indexer, query as kbquery, render
from common.kb import store_graph as sg
from common.kb import store_pg as spg
from common.kb.embed import Embedder, EmbedError


class StoreCmdError(Exception):
    pass


def _cfg(kb: pathlib.Path) -> kbconfig.KbConfig:
    try:
        return kbconfig.load(kb)
    except kbconfig.KbConfigError as exc:
        raise StoreCmdError(str(exc))


def _password(name: str) -> str:
    from common.credential import CredentialError, load_secret
    try:
        return load_secret(name)
    except CredentialError as exc:
        raise StoreCmdError(
            f"取不到凭据 {name!r}:{exc}\n先运行 `python3 -m common.credential_cli set {name}` 加密保存口令")


def open_pg(cfg: kbconfig.KbConfig) -> spg.PgStore:
    if cfg.store.pg is None:
        raise StoreCmdError("kb.yaml 未配置 store.pg(高斯/PG 向量存储)。参考 references/storage-setup.md")
    p = cfg.store.pg
    try:
        return spg.PgStore.connect(p.host, p.port, p.database, p.user, _password(p.credential),
                                   dims=cfg.embeddings.dims, sslmode=p.sslmode)
    except spg.PgStoreError as exc:
        raise StoreCmdError(str(exc))


def open_graph(cfg: kbconfig.KbConfig, required: bool = False) -> Optional[sg.GraphStore]:
    if cfg.store.graph is None:
        if required:
            raise StoreCmdError("kb.yaml 未配置 store.graph(Neo4j)")
        return None
    g = cfg.store.graph
    store = sg.GraphStore(g.url, g.user, _password(g.credential), database=g.database)
    try:
        store.ping()
    except sg.GraphStoreError as exc:
        if required:
            raise StoreCmdError(str(exc))
        print(f"警告:图库不可用({exc}),本次只写高斯/PG;修好后重跑 index", file=sys.stderr)
        return None
    return store


def open_embedder(cfg: kbconfig.KbConfig, caps: spg.Capabilities) -> Optional[Embedder]:
    if not caps.vector:
        print("提示:存储引擎没有 vector 类型,本库只做词法 + 图(kb_meta.vector_engine=none)", file=sys.stderr)
        return None
    try:
        emb = Embedder.from_config(cfg)
    except (EmbedError, kbconfig.KbConfigError) as exc:
        print(f"警告:embedding 未启用({exc});向量列留空,检索只走词法 + 图", file=sys.stderr)
        return None
    if emb is None:
        print("提示:kb.yaml 未配 embeddings,向量列留空;配好后 `kb.py index --fill-missing` 补齐", file=sys.stderr)
    return emb


def _version(kb: pathlib.Path) -> str:
    try:
        return (kb / "VERSION").read_text(encoding="utf-8").strip()
    except OSError:
        return ""


# ---------------------------------------------------------------- setup

def cmd_setup(args: argparse.Namespace) -> int:
    kb = kbconfig.resolve_kb_dir(args.kb)
    cfg = _cfg(kb)
    pg = open_pg(cfg)
    try:
        caps = pg.setup()
    finally:
        pg.close()
    print(f"高斯/PG   : {caps.engine} · {caps.version.split(',')[0][:60]}")
    if caps.vector:
        engine = "datavec" if caps.engine == "opengauss" else "pgvector"
        index_note = "hnsw" if caps.hnsw else "无 hnsw(顺序扫描)"
        print(f"向量      : 有({engine}) · {index_note} · 维度 {caps.dims}")
    else:
        print("向量      : 无——只做词法 + 图")
    graph = open_graph(cfg)
    if graph is not None:
        graph.setup()
        print(f"Neo4j     : {graph.ping()} · 约束已建")
    else:
        print("Neo4j     : 未配置/不可用(图相关能力关闭)")
    print(f"embedding : {cfg.embeddings.source}" + (f" · {cfg.embeddings.model} · {cfg.embeddings.dims} 维"
                                                     if cfg.embeddings.source != 'none' else "(未启用)"))
    print("Next: kb.py index")
    return 0


# ---------------------------------------------------------------- index

def cmd_index(args: argparse.Namespace) -> int:
    kb = kbconfig.resolve_kb_dir(args.kb)
    if not kb.is_dir():
        raise StoreCmdError(f"KB 目录不存在:{kb}")
    cfg = _cfg(kb)
    pg = open_pg(cfg)
    try:
        caps = pg.setup()
        graph = open_graph(cfg)
        embedder = open_embedder(cfg, caps)
        rep = indexer.run_index(kb, pg, graph, embedder, kb_version=_version(kb),
                                rebuild=args.rebuild, fill_missing=args.fill_missing)
    finally:
        pg.close()
    print(f"文档      : 新写 {rep.docs_indexed} · 未变 {rep.docs_unchanged} · 删除 {rep.docs_removed} · 切块 {rep.chunks_written}")
    print(f"向量      : 引擎 {rep.vector_engine} · 新算 {rep.embedded} · 缓存 {rep.embed_cached} · 失败 {rep.embed_failed} · "
          f"覆盖 {rep.chunk_embedded}/{rep.chunk_total}")
    print(f"图        : {rep.graph} · 节点 {rep.nodes} · 边 {rep.edges}(已确认 {rep.edges_confirmed})")
    for w in rep.warnings:
        print(f"[warn ] {w}")
    if not rep.coverage_ok:
        print(f"[error] 向量覆盖率 {rep.chunk_embedded}/{rep.chunk_total} < 100%——"
              f"embedding 失败或未配置;修好后 `kb.py index --fill-missing`。未补齐前状态行会如实显示。")
        return 2
    if rep.graph == "unavailable":
        return 2
    return 0


# ---------------------------------------------------------------- query

def _load_findings(path: pathlib.Path) -> List[Any]:
    from common.finding import findings_from_json
    return findings_from_json(path.read_text(encoding="utf-8"))


def cmd_query(args: argparse.Namespace) -> int:
    kb = kbconfig.resolve_kb_dir(args.kb)
    if args.from_findings:
        findings = _load_findings(pathlib.Path(args.from_findings))
        result = kbquery.from_findings(findings, kb_dir=kb)
    else:
        result = kbquery.from_text(args.q, kb_dir=kb)
    if args.json:
        print(json.dumps(kbquery.result_to_dict(result), ensure_ascii=False, indent=2))
    else:
        print(render.render_section(result), end="")
    return 0 if result.status.attached else 2


# ---------------------------------------------------------------- health

def _misses_top(kb: pathlib.Path, n: int = 10) -> List[Tuple[str, int]]:
    path = kb / "index" / "misses.log"
    if not path.is_file():
        return []
    counts: Dict[str, int] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        parts = line.split("\t")
        if len(parts) >= 2:
            counts[parts[1]] = counts.get(parts[1], 0) + 1
    return sorted(counts.items(), key=lambda p: (-p[1], p[0]))[:n]


def _pending(kb: pathlib.Path) -> List[str]:
    out = []
    inbox = kb / "inbox"
    if not inbox.is_dir():
        return out
    for slug_dir in sorted(p for p in inbox.iterdir() if p.is_dir()):
        items = list((slug_dir / "items").glob("*.md")) if (slug_dir / "items").is_dir() else []
        cand = slug_dir / "candidates.json"
        dec = slug_dir / "decisions.yaml"
        if items and not cand.exists():
            out.append(f"inbox/{slug_dir.name}: {len(items)} 单待 propose")
        elif cand.exists() and not dec.exists():
            out.append(f"inbox/{slug_dir.name}: 候选待 review/确认")
        elif dec.exists():
            out.append(f"inbox/{slug_dir.name}: 有 decisions 待 apply")
        elif (slug_dir / "source.md").exists():
            out.append(f"inbox/{slug_dir.name}: 规范待条款化")
    return out


def cmd_health(args: argparse.Namespace) -> int:
    kb = kbconfig.resolve_kb_dir(args.kb)
    if not kb.is_dir():
        raise StoreCmdError(f"KB 目录不存在:{kb}")
    sess = kbquery.KbSession.open(kb)
    try:
        status = sess.status()
    finally:
        sess.close()
    print(render.status_line(status))
    state = indexer.read_state(kb) or {}
    if state:
        print(f"上次索引  : {state.get('indexed_at', '?')} · 文档新写 {state.get('docs_indexed', '?')} · "
              f"覆盖 {state.get('chunk_embedded', '?')}/{state.get('chunk_total', '?')} · 图 {state.get('graph', '?')}")
    pending = _pending(kb)
    print("待处理    : " + ("; ".join(pending) if pending else "无"))
    misses = _misses_top(kb)
    if misses:
        print("缺口清单  : 近期查不到条款/案例的发现 Top —— " +
              "、".join(f"{code}×{n}" for code, n in misses) + "(补这类材料收益最大)")
    else:
        print("缺口清单  : 无记录")
    if not status.attached:
        print(f"[error] 知识库未接入:{status.reason}")
        return 2
    return 2 if pending else 0


# ---------------------------------------------------------------- feedback

def cmd_feedback(args: argparse.Namespace) -> int:
    kb = kbconfig.resolve_kb_dir(args.kb)
    path = kb / "eval" / "feedback.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    entry = {"id": args.id, "verdict": "useful" if args.useful else "irrelevant",
             "at": datetime.date.today().isoformat(), "note": args.note or ""}
    with path.open("a", encoding="utf-8") as fh:
        fh.write("- " + json.dumps(entry, ensure_ascii=False) + "\n")
    print(f"已记录:{entry['id']} → {entry['verdict']}(采纳率加权在下次 index 后生效)")
    return 0


# ---------------------------------------------------------------- eval

def cmd_eval(args: argparse.Namespace) -> int:
    """eval/queries.yaml:[{q, expect: [doc_id…], canary: bool}] → recall@k 与金丝雀命中。"""
    kb = kbconfig.resolve_kb_dir(args.kb)
    path = kb / "eval" / "queries.yaml"
    if not path.is_file():
        raise StoreCmdError(f"没有黄金查询集:{path}")
    try:
        cases = yaml.safe_load(path.read_text(encoding="utf-8")) or []
    except yaml.YAMLError as exc:
        raise StoreCmdError(f"queries.yaml 解析失败:{exc}")
    sess = kbquery.KbSession.open(kb)
    if not sess.attached:
        raise StoreCmdError(f"知识库未接入:{sess.reason}")
    k = int(args.k)
    hit = total = 0
    canary_miss: List[str] = []
    try:
        for c in cases:
            q = str(c.get("q") or "")
            expect = [str(e) for e in (c.get("expect") or [])]
            res = kbquery.from_text(q, session=sess)
            got: List[str] = []
            for it in res.items:
                got += [r.id for r in it.clauses] + [r.id for r in it.cases] + [r.id for r in it.raws]
            got = got[:k]
            ok = any(e in got for e in expect) if expect else not got
            total += 1
            hit += int(ok)
            mark = "✓" if ok else "✗"
            print(f"[{mark}] {q[:50]} → {got[:3]}")
            if c.get("canary") and not ok:
                canary_miss.append(q)
    finally:
        sess.close()
    recall = hit / total if total else 0.0
    print(f"recall@{k}: {hit}/{total} = {recall:.2f}" + (f" · 金丝雀未中 {len(canary_miss)}" if canary_miss else ""))
    if canary_miss:
        for q in canary_miss:
            print(f"[error] 金丝雀未命中:{q}")
        return 2
    return 0 if recall >= float(args.min_recall) else 2


# ---------------------------------------------------------------- parser wiring

def add_subcommands(sub: "argparse._SubParsersAction") -> None:
    p = sub.add_parser("setup", help="连接高斯/PG 与 Neo4j,建表建约束,报告引擎能力")
    p.add_argument("--kb")
    p.set_defaults(func=cmd_setup)


    p = sub.add_parser("query", help="检索:--q 自然语言,或 --from-findings findings.json")
    p.add_argument("--kb")
    p.add_argument("--q", help="问题文本")
    p.add_argument("--from-findings", help="skill 输出的 findings json 文件")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_query)

    p = sub.add_parser("health", help="文本大盘:接入状态、条款/案例/边数、覆盖率、待处理、缺口清单")
    p.add_argument("--kb")
    p.set_defaults(func=cmd_health)

    p = sub.add_parser("feedback", help="DBA 对一次引用打分:--useful / --irrelevant")
    p.add_argument("id")
    p.add_argument("--kb")
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--useful", action="store_true")
    g.add_argument("--irrelevant", action="store_true")
    p.add_argument("--note")
    p.set_defaults(func=cmd_feedback)

    p = sub.add_parser("eval", help="跑 eval/queries.yaml,报 recall@k 与金丝雀")
    p.add_argument("--kb")
    p.add_argument("--k", default=3)
    p.add_argument("--min-recall", default=0.8)
    p.set_defaults(func=cmd_eval)
