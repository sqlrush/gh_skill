"""全库条目的统一视图:规范条款、修正、案例、旧指南、六类类型化条目 → Entry。

L0 / L1 的生成、`kb.py show / evidence` 都只认 Entry,不再各自去读五种存放形态。
文件是真相:每次现读现算(几千条也是毫秒级),诊断环境的知识库目录只读,也不依赖 index 产物新不新。
"""
from __future__ import annotations

import pathlib
import re
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

import yaml

from . import cases as kbcases
from . import topics as kbtopics
from . import types as kbtypes

_FIRST_SENTENCE_RE = re.compile(r"^(.+?[。;;!?!?])")


@dataclass(frozen=True)
class Entry:
    id: str
    type: str                     # norm / errata / case / guide / runbook / defect / baseline / product / wiki / profile
    title: str
    summary: str
    file: str                     # 相对 <kb> 的路径
    topics: Tuple[str, ...] = ()
    topics_inferred: bool = False  # 没写 topics,按同义词推断的
    verification: str = ""        # 案例的结论强度 / 条目的验证状态 / 缺陷的厂商结论
    source: str = ""
    as_of: str = ""
    evidence: Tuple[Any, ...] = ()
    aliases: Tuple[str, ...] = ()
    meta: Dict[str, Any] = field(default_factory=dict)
    archived: bool = False

    @property
    def short(self) -> str:
        return kbtypes.TYPE_NAMES.get(self.type, (self.type, self.type))[1]

    @property
    def cite(self) -> str:
        """引用时的写法:〔手册 RB-LCK-001〕"""
        return f"〔{self.short} {self.id}〕"


@dataclass(frozen=True)
class Catalog:
    entries: Tuple[Entry, ...]
    topics: Tuple[kbtopics.Topic, ...]
    warnings: Tuple[str, ...] = ()

    def by_id(self) -> Dict[str, Entry]:
        out: Dict[str, Entry] = {}
        for e in self.entries:
            out.setdefault(e.id, e)
        for e in self.entries:              # 改过类型 / 迁移前的旧 ID 也要找得到(报告里引用过)
            for a in e.aliases:
                out.setdefault(a, e)
        return out

    def find(self, item_id: str) -> Optional[Entry]:
        key = item_id.strip()
        hit = self.by_id().get(key)
        if hit is not None:
            return hit
        prefixed = [e for e in self.entries if e.id.startswith(key)] if len(key) >= 8 else []
        return prefixed[0] if len(prefixed) == 1 else None

    def of_type(self, *types: str) -> List[Entry]:
        return [e for e in self.entries if e.type in types and not e.archived]

    def in_topic(self, slug: str) -> List[Entry]:
        live = [e for e in self.entries if not e.archived and e.type != "profile"]
        if slug == kbtopics.OTHER:
            return [e for e in live if not e.topics]
        return [e for e in live if slug in e.topics]

    def profiles(self) -> List[Entry]:
        return self.of_type("profile")


def first_sentence(text: str, limit: int = 120) -> str:
    flat = " ".join(str(text or "").split())
    m = _FIRST_SENTENCE_RE.match(flat)
    s = m.group(1) if m and len(m.group(1)) <= limit else flat
    return s if len(s) <= limit else s[:limit - 1] + "…"


# ---------------------------------------------------------------- loaders (各存放形态 → Entry)

def _rule_entries(kb: pathlib.Path, sub: str, topics: Sequence[kbtopics.Topic]) -> List[Entry]:
    out: List[Entry] = []
    root = kb / sub
    if not root.is_dir():
        return out
    for path in sorted(root.rglob("*.y*ml")):
        try:
            data = yaml.safe_load(path.read_text(encoding="utf-8")) or []
        except (OSError, UnicodeDecodeError, yaml.YAMLError):
            continue
        if not isinstance(data, list):
            continue
        for e in data:
            if not isinstance(e, dict) or not e.get("id"):
                continue
            if sub == "rules" and str(e.get("status") or "active").lower() != "active":
                continue
            rule = str(e.get("rule") or "").strip()
            kws = e.get("keywords") or []
            declared = kbtypes.as_list(e.get("topics"))
            text = " ".join([rule, str(e.get("rationale") or ""), " ".join(map(str, kws)) if isinstance(kws, list) else str(kws)])
            tops = declared or kbtopics.infer_topics(text, topics)
            out.append(Entry(
                id=str(e["id"]), type=kbtypes.NORM, title=first_sentence(rule, 60) or str(e["id"]),
                summary=first_sentence(rule, 160), file=path.relative_to(kb).as_posix(), topics=tops,
                topics_inferred=not declared, verification=str(e.get("severity") or ""),
                source=str(e.get("source") or ""), evidence=tuple(kbtypes.as_list(e.get("evidence"))),
                meta=dict(e), archived=(sub == "archive")))
    return out


def _md_meta(path: pathlib.Path) -> Tuple[Dict[str, Any], str]:
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return {}, ""
    meta, body, err = kbcases.split_frontmatter(text)
    return (meta or {}) if not err else {}, body if not err else text


def _errata_and_guides(kb: pathlib.Path, topics: Sequence[kbtopics.Topic]) -> List[Entry]:
    out: List[Entry] = []
    for sub, typ in (("errata", kbtypes.ERRATA), ("guides", kbtypes.GUIDE)):
        root = kb / sub
        if not root.is_dir():
            continue
        for path in sorted(root.rglob("*.md")):
            meta, body = _md_meta(path)
            first = next((ln.strip().lstrip("#").strip() for ln in body.splitlines() if ln.strip()), path.stem)
            desc = str(meta.get("description") or "").strip()
            declared = kbtypes.as_list(meta.get("topics"))
            tops = declared or kbtopics.infer_topics(f"{desc} {body[:2000]}", topics)
            out.append(Entry(
                id=str(meta.get("id") or path.stem), type=typ, title=first_sentence(desc or first, 60),
                summary=first_sentence(desc or first, 160), file=path.relative_to(kb).as_posix(),
                topics=tops, topics_inferred=not declared, source=str(meta.get("source") or ""),
                aliases=kbtypes.as_list(meta.get("aliases")), meta=dict(meta)))
    return out


def case_summary(c: kbcases.Case) -> str:
    judge = c.primary_factor or first_sentence(c.section("判断"), 80)
    act = first_sentence(c.section("处置"), 80)
    return "".join(s for s in (f"根因:{judge}" if judge else "", f";处置:{act}" if act else ""))[:200]


def _case_entries(kb: pathlib.Path, topics: Sequence[kbtopics.Topic]) -> List[Entry]:
    out: List[Entry] = []
    cases, _ = kbcases.load_cases(kb)
    for c in cases:
        declared = c.topics
        text = " ".join([c.title, " ".join(c.signals), c.section("现场"), c.section("复发标志"), c.primary_factor])
        tops = declared or kbtopics.infer_topics(text, topics)
        out.append(Entry(
            id=c.id, type=kbtypes.CASE, title=c.title, summary=case_summary(c),
            file=c.path.relative_to(kb).as_posix(), topics=tops, topics_inferred=not declared,
            verification=c.conclusion, source=c.source, as_of=c.occurred_at, evidence=c.evidence,
            meta={"system": c.system, "signals": list(c.signals), "occurred_at": c.occurred_at,
                  "sections": dict(c.sections)}))
    return out


def _typed_entries(kb: pathlib.Path) -> List[Entry]:
    items, _ = kbtypes.load_items(kb)
    return [Entry(
        id=i.id, type=i.type, title=i.title, summary=first_sentence(i.summary, 200),
        file=i.path.relative_to(kb).as_posix(), topics=i.topics, verification=i.verification,
        source=i.source, as_of=i.as_of, evidence=i.evidence, aliases=i.aliases,
        meta={**i.meta, "sections": dict(i.sections)}) for i in items]


def load(kb: pathlib.Path) -> Catalog:
    topics, warns = kbtopics.load_taxonomy(kb)
    entries = (_rule_entries(kb, "rules", topics) + _rule_entries(kb, "archive", topics)
               + _errata_and_guides(kb, topics) + _case_entries(kb, topics) + _typed_entries(kb))
    order = {t: i for i, t in enumerate(kbtypes.DISPLAY_ORDER)}
    entries.sort(key=lambda e: (order.get(e.type, 99), e.archived, e.id))
    return Catalog(entries=tuple(entries), topics=tuple(topics), warnings=tuple(warns))


def profile_for_alias(cat: Catalog, alias: str) -> Optional[Entry]:
    from . import versions as kbversions
    metas = [dict(e.meta, _id=e.id) for e in cat.profiles()]
    hit = kbversions.first_match(metas, alias)
    return next((e for e in cat.profiles() if hit and e.id == hit["_id"]), None)


def type_counts(cat: Catalog) -> Dict[str, int]:
    out: Dict[str, int] = {}
    for e in cat.entries:
        if not e.archived:
            out[e.type] = out.get(e.type, 0) + 1
    return out


def entries_text(entries: Iterable[Entry]) -> str:
    return " ".join(f"{e.id} {e.title} {e.summary}" for e in entries)
