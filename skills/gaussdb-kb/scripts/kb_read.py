"""kb 只读命令:渐进式披露的四层读取入口(诊断环境与导入环境都有)。

    overview                 L0 总索引:知识范围(八类各多少)、适用环境(画像)、专题入口
    topic [<专题>] [--all]   L1 专题导航:同义词、本行确认过的候选原因与区分证据、相关条目摘要
    topic --list             专题清单(slug、名称、条目数)
    show <ID> [--brief]      L2 一条条目的全文(--brief 只到摘要);一次只取一条
    evidence <ID>            L3 这条条目的原始证据:原文片段、照片 / 日志的位置
    template <类型>          新条目的骨架(frontmatter + 小节),导入时照着写

每条命令的输出都有上限(L0 3000 字、L1 6000 字、show / evidence 8000 字),超出写明截断。
文件是真相:每次现读,知识库目录只读也能用,不依赖 index 产物新不新。
"""
from __future__ import annotations

import argparse
import pathlib
import re
import sys
from typing import Any, List, Optional, Tuple

import yaml

from common.kb import catalog as kbcat
from common.kb import config as kbconfig
from common.kb import graphfiles as gf
from common.kb import layers as kblayers
from common.kb import topics as kbtopics
from common.kb import types as kbtypes
from common.kb import versions as kbversions

SHOW_LIMIT = 8000
EVIDENCE_LIMIT = 8000
_IMAGE_SUFFIXES = (".png", ".jpg", ".jpeg", ".gif", ".webp", ".heic", ".heif", ".bmp", ".tif", ".tiff")
_TEXT_SUFFIXES = (".md", ".txt", ".log", ".csv", ".yaml", ".yml", ".json", ".sql")


class ReadCmdError(Exception):
    """用户可读的错误(目录不存在、ID 不存在)。"""


def _kb(args: argparse.Namespace) -> pathlib.Path:
    kb = kbconfig.resolve_kb_dir(getattr(args, "kb", None))
    if not kb.is_dir():
        raise ReadCmdError(f"知识库目录不存在:{kb}")
    return kb


def _version(kb: pathlib.Path) -> str:
    try:
        return (kb / "VERSION").read_text(encoding="utf-8").strip()
    except OSError:
        return "unknown"


def _cap(text: str, limit: int, hint: str) -> str:
    if len(text) <= limit:
        return text
    return text[:limit].rsplit("\n", 1)[0] + f"\n(超出 {limit} 字已截断:{hint})\n"


# ---------------------------------------------------------------- overview / topic

def cmd_overview(args: argparse.Namespace) -> int:
    kb = _kb(args)
    cat = kbcat.load(kb)
    print(kblayers.render_l0(kb, cat, _version(kb)), end="")
    if args.alias:
        prof = kbcat.profile_for_alias(cat, args.alias)
        print(f"\n本实例({args.alias}):" + (f"{prof.id}《{prof.title}》" if prof else "未登记环境画像(适用性按「未知」处理)"))
    for w in cat.warnings:
        print(f"[warn ] {w}")
    return 0


def cmd_topic(args: argparse.Namespace) -> int:
    kb = _kb(args)
    cat = kbcat.load(kb)
    if args.list or not args.slug:
        print("| 专题 | slug | 条目 |\n|---|---|---|")
        for t in list(cat.topics) + [kbtopics.Topic(kbtopics.OTHER, kbtopics.OTHER_NAME)]:
            n = len(cat.in_topic(t.slug))
            if n or t.slug != kbtopics.OTHER:
                print(f"| {t.name} | `{t.slug}` | {n} |")
        print("\n用法:kb.py topic <slug>(看某个专题的候选原因与相关条目)")
        return 0
    slugs = {t.slug for t in cat.topics} | {kbtopics.OTHER}
    slug = args.slug.strip()
    if slug not in slugs:
        by_name = [t.slug for t in cat.topics if t.name == slug or slug in t.synonyms]
        if not by_name:
            raise ReadCmdError(f"没有专题 `{slug}`;可用:{'、'.join(sorted(slugs))}(kb.py topic --list)")
        slug = by_name[0]
    triples, _ = gf.load_triples(kb, case_ids=[e.id for e in cat.of_type(kbtypes.CASE)])
    view = kblayers.topic_view(cat, triples, slug)
    print(kblayers.render_l1(view, _version(kb), show_all=args.all), end="")
    return 0


# ---------------------------------------------------------------- show

def _suggest(cat: kbcat.Catalog, key: str) -> List[str]:
    k = key.lower()
    return [e.id for e in cat.entries if k in e.id.lower()][:8]


def _clause_text(e: kbcat.Entry) -> str:
    return yaml.safe_dump([{k: v for k, v in e.meta.items()}], allow_unicode=True, sort_keys=False)


def _brief_md(text: str, entry: kbcat.Entry) -> str:
    """frontmatter + 摘要(案例没有摘要节,给根因与处置的一句话)。"""
    m = re.match(r"^---\s*\n.*?\n---\s*\n?", text, re.S)
    front = m.group(0) if m else ""
    return f"{front}\n## 摘要\n{entry.summary}\n"


def cmd_show(args: argparse.Namespace) -> int:
    kb = _kb(args)
    cat = kbcat.load(kb)
    e = cat.find(args.id)
    if e is None:
        near = _suggest(cat, args.id)
        raise ReadCmdError(f"知识库里没有 `{args.id}`" + (f";相近的:{'、'.join(near)}" if near else "")
                           + "——没有就是没有,不得编造(回答里引用了的 ID 先跑 cite-check)")
    head = [f"# {e.cite} {e.title}",
            f"> 类型:{kbtypes.TYPE_NAMES.get(e.type, (e.type,))[0]} · 文件:`{e.file}`"
            + (f" · 专题:{'、'.join(e.topics)}" if e.topics else "")
            + (f" · {e.verification}" if e.verification else "")]
    if e.id != args.id.strip():
        head.append(f"> `{args.id}` 是旧 ID,已改为 `{e.id}`(引用写新 ID)")
    if e.archived:
        head.append("> ⚠ **已废止条款**,仅供追溯,不得用于判定")
    if args.alias and e.type not in (kbtypes.CASE, "profile"):
        prof = kbcat.profile_for_alias(cat, args.alias)
        if e.type == "defect":
            head.append(f"> 适用性:{kbversions.defect_verdict(e.meta, prof.meta if prof else None)}")
        else:
            v = kbversions.judge(e.meta, prof.meta if prof else None)
            head.append(f"> 适用性:{v.state}" + (f"({v.reason})" if v.reason else ""))
    if e.type in (kbtypes.NORM,):
        body = _clause_text(e)
    else:
        try:
            body = (kb / e.file).read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            raise ReadCmdError(f"{e.file} 读不了:{exc}")
        if args.brief:
            body = _brief_md(body, e)
    n_ev = len(e.evidence) + (1 if e.source else 0)
    foot = f"\n(原始证据 {n_ev} 处:kb.py evidence {e.id})\n" if n_ev else ""
    print(_cap("\n".join(head) + "\n\n" + body.rstrip() + "\n" + foot, SHOW_LIMIT, f"kb.py evidence {e.id} 看原文"), end="")
    return 0


# ---------------------------------------------------------------- evidence

def _split_ref(ref: Any) -> Tuple[str, str, str]:
    """(路径, 定位, 引句)。ref 可以是 'sources/x.docx#第3节' 或 {file, at, quote}。"""
    if isinstance(ref, dict):
        return str(ref.get("file") or ""), str(ref.get("at") or ""), str(ref.get("quote") or "")
    s = str(ref or "")
    path, _, loc = s.partition("#")
    return path.strip(), loc.strip(), ""


def _resolve(kb: pathlib.Path, rel: str) -> Optional[pathlib.Path]:
    if not rel:
        return None
    cands = [kb / rel] + ([kb / "sources" / rel, kb / "evidence" / rel, kb / "inbox" / "uploads" / rel] if "/" not in rel else [])
    for c in cands:
        try:
            c.resolve().relative_to(kb.resolve())        # 不许 ../ 跑出知识库目录
        except ValueError:
            continue
        if c.is_file():
            return c
    return None


def _text_of(path: pathlib.Path) -> Optional[str]:
    suffix = path.suffix.lower()
    try:
        if suffix in _TEXT_SUFFIXES:
            return path.read_text(encoding="utf-8", errors="replace")
        from common.kb import extract
        return extract.extract_source(path)
    except Exception:                                    # noqa: BLE001  抽不出文字就只给路径
        return None


def _excerpt(text: str, loc: str, quote: str) -> str:
    lines = text.splitlines()
    if quote:
        i = text.find(quote.strip()[:40])
        if i >= 0:
            return text[max(0, i - 300):i + len(quote) + 300].strip()
    m = re.fullmatch(r"(?:item=)(\d+)", loc)
    if m:
        blocks = [b for b in re.split(r"\n-{3,}\n", text) if b.strip()]
        k = int(m.group(1))
        return blocks[k - 1].strip() if 0 < k <= len(blocks) else ""
    m = re.fullmatch(r"L?(\d+)(?:-L?(\d+))?", loc)
    if m:
        a = int(m.group(1)); b = int(m.group(2) or a + 20)
        return "\n".join(lines[a - 1:b])
    if loc:
        for i, ln in enumerate(lines):
            if loc in ln:
                return "\n".join(lines[i:i + 25])
    return "\n".join(lines[:40])


def cmd_evidence(args: argparse.Namespace) -> int:
    kb = _kb(args)
    cat = kbcat.load(kb)
    e = cat.find(args.id)
    if e is None:
        raise ReadCmdError(f"知识库里没有 `{args.id}`")
    refs = list(e.evidence) + ([e.source] if e.source and e.source not in e.evidence else [])
    out = [f"# {e.cite} 的原始证据(L3)", ""]
    if not refs:
        out.append("(这条没有登记出处或证据;只能按条目本身引用,并说明「未附原始证据」)")
    for ref in refs:
        rel, loc, quote = _split_ref(ref)
        path = _resolve(kb, rel)
        label = f"`{rel}`" + (f" · 定位:{loc}" if loc else "")
        if path is None:
            out += [f"## {label}", "⚠ 知识库里找不到这个文件(原件没存档,或是外部出处),只能按条目引用", ""]
            continue
        if path.suffix.lower() in _IMAGE_SUFFIXES:
            out += [f"## {label}", f"图片:{path}(需要核对时用 read 工具查看这张图)", ""]
            continue
        text = _text_of(path)
        if text is None:
            out += [f"## {label}", f"文件:{path}(抽不出文字;docx/pdf 以外的格式请让知识库管理员转成文本)", ""]
            continue
        snippet = _excerpt(text, loc, quote)
        out += [f"## {label}", "```text", snippet[:2500] or "(定位没找到,下面是开头)\n" + text[:1500], "```", ""]
    print(_cap("\n".join(out) + "\n", EVIDENCE_LIMIT, "证据太长,按定位逐条看"), end="")
    return 0


# ---------------------------------------------------------------- template

_TEMPLATE_META = {
    "runbook": {"applies": {"product": "GaussDB 集中式", "versions": ">= 503", "systems": ["*"]},
                "not_applies": "<什么情况下不能用>", "verification": "未验证", "approval": ["<要的审批 / 规范 ID>"]},
    "defect": {"affected_versions": "<受影响版本,如 505.1–505.2>", "fixed_in": "<修复版本 / 补丁>",
               "vendor_status": "待确认"},
    "baseline": {"system": "<系统>", "metric": "<指标名,如 active_sessions>", "unit": "<单位>",
                 "metric_source": "<哪个技能 / 视图>", "window": "<采样窗口>", "value_kind": "差值"},
    "product": {"product": "GaussDB 集中式", "versions": "<版本范围,如 505.*>", "deployment": "<部署形态>"},
    "wiki": {"verification": "未验证", "as_of": "<YYYY-MM>"},
    "profile": {"system": "<系统简称>", "aliases": ["<连接别名,如 og5>"], "product": "GaussDB 集中式",
                "version": "<如 505.2.0>", "deployment": "<如 一主两备>"},
}


def template_text(type_code: str) -> str:
    kt = kbtypes.TYPED[type_code]
    meta = {"id": "auto", "type": kt.code, "title": "<一句话标题>"}
    if kt.needs_topics:
        meta["topics"] = ["<专题 slug,如 lock>"]
    if kt.needs_source:
        meta["source"] = "<出处:sources/原文.docx#章节>"
    meta.update(_TEMPLATE_META.get(type_code, {}))
    meta["evidence"] = ["<可选:sources/... 或 evidence/... 的原文、照片、日志>"]
    sections = ["## 摘要", "<≤200 字,结论在前;L1 专题导航里只显示这一段>", ""]
    for s in kt.recommended_sections:
        sections += [f"## {s}", "<…>", ""]
    if type_code != "profile":
        sections += ["## 判断边界", "<什么情况下不适用>", ""]
    return ("---\n" + yaml.safe_dump(meta, allow_unicode=True, sort_keys=False).strip() + "\n---\n"
            + "\n".join(sections))


def cmd_template(args: argparse.Namespace) -> int:
    print(f"# {kbtypes.TYPED[args.type].name}({args.type},放 {kbtypes.TYPED[args.type].dir}/,"
          f"ID {kbtypes.TYPED[args.type].id_hint};id 写 auto 由 kb.py add 分配)\n")
    print(template_text(args.type), end="")
    return 0


# ---------------------------------------------------------------- wiring

def add_subcommands(sub: "argparse._SubParsersAction") -> None:
    p = sub.add_parser("overview", help="L0 总索引:知识范围、适用环境、专题入口")
    p.add_argument("--kb")
    p.add_argument("--alias", help="当前连接别名:顺带说本实例匹配到哪份环境画像")
    p.set_defaults(func=cmd_overview)
    p = sub.add_parser("topic", help="L1 专题导航:候选原因与区分证据、相关条目摘要")
    p.add_argument("slug", nargs="?")
    p.add_argument("--kb")
    p.add_argument("--all", action="store_true", help="列出本专题全部条目(默认按上限截断)")
    p.add_argument("--list", action="store_true", help="只列专题清单")
    p.set_defaults(func=cmd_topic)
    p = sub.add_parser("show", help="L2 一条条目的全文(引用前先读)")
    p.add_argument("id")
    p.add_argument("--kb")
    p.add_argument("--brief", action="store_true", help="只看元数据与摘要")
    p.add_argument("--alias", help="当前连接别名:顺带判断这条对本实例适不适用")
    p.set_defaults(func=cmd_show)
    p = sub.add_parser("evidence", help="L3 一条条目的原始证据(核对细节或处理冲突时)")
    p.add_argument("id")
    p.add_argument("--kb")
    p.set_defaults(func=cmd_evidence)
    p = sub.add_parser("template", help="新条目的骨架(导入时照着写,再 kb.py add)")
    p.add_argument("type", choices=sorted(kbtypes.TYPED))
    p.set_defaults(func=cmd_template)


if __name__ == "__main__":                              # pragma: no cover  只经 kb.py 调用
    sys.exit("请通过 kb.py 调用")
