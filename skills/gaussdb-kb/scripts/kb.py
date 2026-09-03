#!/usr/bin/env python3
"""kb — build and govern the GaussDB spec knowledge base (kb).

Deterministic half of the KB pipeline. The model (per SKILL.md) does the
semantic half — classifying clauses into rules / guides / errata and drafting
entries. This script owns everything that must not depend on a model:

    ingest <file>       convert txt/md/docx/doc into <kb>/inbox/<slug>/source.md
                        (+ heading outline), snapshot the original into <kb>/sources/
    index               rebuild <kb>/INDEX.md from rules/*.yaml + guides/ + errata/
    validate            check encoding (non-UTF-8 is invisible to the skills' grep),
                        rule IDs, yaml schema, frontmatter, INDEX consistency,
                        and that withdrawn clauses actually left rules/
    search <keyword>    grep across errata/ rules/ guides/ (errata first)
    contract [--apply]  inject the KB-reference contract block into the judging skills
    cite-check          verify that case / clause IDs quoted in an answer exist (kb_cite.py)

Withdrawing a clause is a *move*, not a flag. The contract (references/kb-contract.md)
sends the skills at the KB with `grep -rn <kw> <kb>/errata <kb>/rules <kb>/guides`, and
grep prints only the matching *line* — a clause tagged `status: deprecated` still gets
hit on its `rule:` line, and the model never sees the tag. So a withdrawn clause moves
to <kb>/archive/, which is deliberately outside that grep range. `status` stays on the
entry so validate can enforce both halves: tagged-but-not-moved, and moved-but-not-tagged.
The ID is never deleted or reused — reports that cited it must stay traceable.

KB directory resolution (first hit wins):
    --kb DIR  >  $GSDB_KB_DIR  >  <install root>/kb

The KB sits *beside* the skills, never inside one — install-opencode.sh does
`rm -rf` on each skill directory, so a KB under skills/<name>/ would be wiped on
the next reinstall. Global install -> ~/.config/opencode/kb; project install ->
<project>/.opencode/kb. The customer configures nothing.

Exit codes: 0 = ok, 1 = runtime error, 2 = findings (validate errors, or
contract scan showing missing/stale blocks). Findings go to stdout.
"""
from __future__ import annotations

import argparse
import html
import os
import pathlib
import re
import shutil
import subprocess
import sys
from datetime import date

try:
    import yaml
except ImportError:  # pragma: no cover
    print("缺少依赖 PyYAML:python3 -m pip install PyYAML", file=sys.stderr)
    sys.exit(1)

_HERE = pathlib.Path(__file__).resolve()
sys.path.insert(0, str(_HERE.parent))           # 兄弟模块 kb_store / kb_cases
for _anc in _HERE.parents:                       # common/(仓库根,或装好后的 skills/ 下)
    if (_anc / "common" / "kb" / "__init__.py").exists():
        sys.path.insert(0, str(_anc))
        break

KB_SUBDIRS = ("errata", "rules", "guides", "archive", "sources", "inbox")
RULE_ID_RE = re.compile(r"^GS-[A-Z]{2,4}-\d{3}$")
SEVERITIES = frozenset({"error", "warn", "info"})
CHECK_KINDS = frozenset({"deterministic", "advisory"})
RULE_REQUIRED_FIELDS = ("id", "severity", "check", "rule")

# A clause is either in force or withdrawn. Legacy clauses have no `status` field
# at all, so its absence must mean active — see rule_status().
STATUS_ACTIVE = "active"
STATUS_DEPRECATED = "deprecated"
STATUSES = frozenset({STATUS_ACTIVE, STATUS_DEPRECATED})
# archive/ is scanned first on purpose: it is settled history, so when an ID
# collides it is the *new* clause that must be blamed, not the withdrawn one it
# collided with. Scan rules/ first and validate points the operator at archive/ —
# the exact opposite of the file they need to fix.
RULE_DIRS = ("archive", "rules")
CONTRACT_BEGIN = "<!-- KB-CONTRACT:BEGIN"
CONTRACT_END = "<!-- KB-CONTRACT:END -->"
# 只有会做「规范判断 / 阈值判断」的 skill 才需要知识库契约。slowsql / topsql /
# sqlfetch / explain / topproc / procinfo 是纯取数(列表格、还原 SQL 文本),
# 要求它们「先查知识库再作答」只是噪音与延迟。
CONTRACT_SKILLS = frozenset({
    "sqlreview", "health", "wdr", "memanalyze", "sqltune", "proctune",
    "lockwait", "waitevent", "vacuum",        # 有 findings 的诊断 skill,同样按发现查库
})
# 部署仓的 skill 目录带 gaussdb- 前缀(gaussdb-health),主干不带;白名单按去前缀后的名字比。
SKILL_PREFIX = "gaussdb-"


def skill_base_name(name: str) -> str:
    return name[len(SKILL_PREFIX):] if name.startswith(SKILL_PREFIX) else name
SEARCH_HIT_CAP = 200

HEADING_PATTERNS = (
    re.compile(r"^#{1,6}\s+\S"),
    re.compile(r"^第[零一二三四五六七八九十百0-9]+[章节部分篇]"),
    re.compile(r"^[一二三四五六七八九十]+、"),
    re.compile(r"^[((][一二三四五六七八九十0-9]+[))]"),
    re.compile(r"^\d+(?:\.\d+){0,3}[..、\s]\s*\S"),
)
MAX_HEADING_LEN = 60


class KbError(Exception):
    """Operator-facing failure; message is printed as-is."""


# ---------------------------------------------------------------- kb layout

def install_root(start: pathlib.Path | None = None) -> pathlib.Path:
    """The directory the skills were installed into.

        ~/.config/opencode/skills/kb/scripts/kb.py -> ~/.config/opencode
        <项目>/.opencode/skills/.../kb.py                 -> <项目>/.opencode
        <仓库>/skills/.../kb.py                           -> <仓库>
    """
    here = (start or _HERE).resolve()
    for anc in here.parents:
        if anc.name == "skills":
            return anc.parent
    return here.parent.parent.parent          # 非标准布局:退回三级


def resolve_kb_dir(cli_value: str | None) -> pathlib.Path:
    """--kb > $GSDB_KB_DIR > <安装根>/kb

    The KB lives *next to* the skills, never inside one: install-opencode.sh
    does `rm -rf` on each skill directory, so a KB under skills/<name>/ would be
    destroyed on the next reinstall. A sibling of skills/ is never touched.
    """
    if cli_value:
        return pathlib.Path(cli_value).expanduser()
    env_dir = os.environ.get("GSDB_KB_DIR")
    if env_dir:
        return pathlib.Path(env_dir).expanduser()
    return install_root() / "kb"


def ensure_kb_skeleton(kb: pathlib.Path) -> None:
    for sub in KB_SUBDIRS:
        (kb / sub).mkdir(parents=True, exist_ok=True)
    version = kb / "VERSION"
    if not version.exists():
        version.write_text(date.today().strftime("%Y.%m") + "\n", encoding="utf-8")


def read_version(kb: pathlib.Path) -> str:
    try:
        return (kb / "VERSION").read_text(encoding="utf-8").strip()
    except OSError:
        return "unknown"


# ---------------------------------------------------------------- ingest

def read_text_file(path: pathlib.Path) -> str:
    raw = path.read_bytes()
    for enc in ("utf-8", "gb18030"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", "replace")


def extract_docx(path: pathlib.Path) -> str:
    import zipfile

    try:
        with zipfile.ZipFile(path) as zf:
            xml = zf.read("word/document.xml").decode("utf-8", "replace")
    except (zipfile.BadZipFile, KeyError) as exc:
        raise KbError(f".docx 解析失败({exc});文件可能损坏或是旧版 .doc 改的后缀") from exc
    lines = []
    for para in re.split(r"</w:p>", xml):
        para = re.sub(r"<w:tab[^>]*/>", "\t", para)
        para = re.sub(r"<w:br[^>]*/>", "\n", para)
        text = "".join(re.findall(r"<w:t[^>]*>(.*?)</w:t>", para, re.S))
        lines.append(html.unescape(text))
    return "\n".join(lines)


def extract_doc(path: pathlib.Path) -> str:
    converters = (
        (("textutil", "-convert", "txt", "-stdout", str(path)), "textutil(macOS)"),
        (("antiword", str(path)), "antiword"),
    )
    tried = []
    for cmd, label in converters:
        if not shutil.which(cmd[0]):
            tried.append(f"{label}:未安装")
            continue
        try:
            proc = subprocess.run(cmd, capture_output=True, timeout=120)
        except subprocess.TimeoutExpired:
            tried.append(f"{label}:转换超时(120s)")
            continue
        if proc.returncode == 0 and proc.stdout.strip():
            return proc.stdout.decode("utf-8", "replace")
        tried.append(f"{label}:退出码 {proc.returncode}")
    raise KbError(
        ".doc 转换失败(" + ";".join(tried) + ")。"
        "请先手工另存为 .txt 或 .docx 再导入"
    )


# --- PDF ---------------------------------------------------------------------
# A scanned PDF holds pictures of words, not words: it has no text operators at
# all, and pdftotext reports success (exit 0) while printing nothing. Copying the
# .doc pattern (exit 0 == success) would write an empty source.md, and the model
# would then state that the customer's spec "contains no clauses" — while the
# clauses sit right there in 30 pages of images. No error, no crash, just silence.
# Chinese specs are scanned far more often than not (anything with a red chop is).
#
# So extraction is only half the job; the other half is refusing to import a
# result we cannot vouch for. Uncertain == unusable, and we say so.
_PDF_MIN_CHARS = 100          # 绝对下限:低于此值的「提取成功」不可信
_PDF_MIN_CHARS_PER_PAGE = 50  # 每页字符数下限:混合扫描件(页眉是文本、正文是图)
_PDF_MAX_GARBAGE = 0.10       # 乱码字符占比上限

_PDF_TIMEOUT = 120


def pdf_page_count(raw: bytes) -> int:
    """Page count from the raw bytes, or 0 when it cannot be told (pure).

    PDF 1.5+ can bury page objects inside compressed object streams, where this
    finds nothing. Returning 0 says exactly that — the quality gate falls back to
    its absolute character floor rather than guessing a number.
    """
    return len(re.findall(rb"/Type\s*/Page[^s]", raw))


def _is_plain(ch: str) -> bool:
    """CJK, ASCII, and the punctuation a spec actually uses."""
    o = ord(ch)
    return (
        ch.isspace()
        or 0x20 <= o <= 0x7E                 # ASCII printable
        or 0x3000 <= o <= 0x303F             # CJK punctuation
        or 0x4E00 <= o <= 0x9FFF             # CJK unified ideographs
        or 0xFF00 <= o <= 0xFFEF             # fullwidth forms
    )


def pdf_extraction_problem(text: str, pages: int) -> str | None:
    """Why this extraction must not be imported, or None if it looks sound (pure).

    Two failure modes, both of which come back as a *successful* conversion:
      - scanned pages   -> empty or near-empty text
      - CID font with no ToUnicode CMap -> private-use / replacement characters,
        i.e. text that looks like content but is garbage. Importing that is worse
        than importing nothing: the model reads the garbage as clauses.
    """
    body = "".join(text.split())
    if not body:
        return _scanned_msg(pages, 0)
    if len(body) < _PDF_MIN_CHARS:
        return _scanned_msg(pages, len(body))
    if pages > 0 and len(body) / pages < _PDF_MIN_CHARS_PER_PAGE:
        return _scanned_msg(pages, len(body))

    garbage = sum(1 for ch in body if not _is_plain(ch))
    if garbage / len(body) > _PDF_MAX_GARBAGE:
        return (
            f"PDF 提取结果疑似**乱码**({garbage}/{len(body)} 个字符无法识别,"
            f"超过 {_PDF_MAX_GARBAGE:.0%})。常见于 PDF 内嵌 CID 字体但缺少 ToUnicode "
            f"映射表——抠出来的是私用区码位,看着像内容,实际是垃圾。"
            f"**乱码入库比空文档更糟**:模型会把它当成规范条款。"
            f"请用 Adobe/WPS 的「PDF 转 Word」重新导出,或向客户索要 .docx 原件。"
        )
    return None


def _scanned_msg(pages: int, chars: int) -> str:
    where = f"{pages} 页,共 {chars} 个字符" if pages else f"共 {chars} 个字符"
    return (
        f"PDF 提取到的文本过少({where})。这几乎可以肯定是**扫描件/图片型 PDF**——"
        f"里面的字是图片,不是文本。**本工具不做 OCR**。\n"
        f"请先 OCR 转成文本(Adobe/WPS 的「PDF 转 Word」、macOS 预览的实时文字识别、"
        f"或向客户索要 .docx/.txt 原件),再导入。\n"
        f"绝不入库空文档:那会让模型误以为「这份规范没有条款」。"
    )


def extract_pdf(path: pathlib.Path) -> str:
    converters = (
        (("pdftotext", "-layout", "-enc", "UTF-8", str(path), "-"), "pdftotext(poppler)"),
        (("mutool", "draw", "-F", "txt", str(path)), "mutool(mupdf)"),
    )
    tried = []
    for cmd, label in converters:
        if not shutil.which(cmd[0]):
            tried.append(f"{label}:未安装")
            continue
        try:
            proc = subprocess.run(cmd, capture_output=True, timeout=_PDF_TIMEOUT)
        except subprocess.TimeoutExpired:
            tried.append(f"{label}:转换超时({_PDF_TIMEOUT}s)")
            continue
        if proc.returncode != 0:
            tried.append(f"{label}:退出码 {proc.returncode}")
            continue

        text = proc.stdout.decode("utf-8", "replace")
        # Exit code 0 does NOT mean the text is usable — see the note above.
        problem = pdf_extraction_problem(text, pdf_page_count(path.read_bytes()))
        if problem:
            raise KbError(problem)
        return text

    raise KbError(
        "PDF 转换失败(" + ";".join(tried) + ")。\n"
        "请安装 poppler:macOS `brew install poppler`,"
        "Debian/Ubuntu `apt install poppler-utils`,CentOS `yum install poppler-utils`。\n"
        "或先手工把 PDF 转成 .txt / .docx 再导入。"
    )


def extract_source(path: pathlib.Path) -> str:
    suffix = path.suffix.lower()
    if suffix in (".txt", ".md", ".sql"):
        return read_text_file(path)
    if suffix == ".docx":
        return extract_docx(path)
    if suffix == ".doc":
        return extract_doc(path)
    if suffix == ".pdf":
        return extract_pdf(path)
    raise KbError(f"不支持的格式 {suffix}(支持 .txt/.md/.docx/.doc/.pdf)")


def normalize_text(text: str) -> str:
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"[ \t]+$", "", text, flags=re.M)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip() + "\n"


def detect_outline(text: str) -> list[tuple[int, str]]:
    outline = []
    for lineno, line in enumerate(text.splitlines(), 1):
        stripped = line.strip()
        if not stripped or len(stripped) > MAX_HEADING_LEN:
            continue
        if any(pat.match(stripped) for pat in HEADING_PATTERNS):
            outline.append((lineno, stripped))
    return outline


def slugify(stem: str) -> str:
    slug = re.sub(r"[^0-9A-Za-z一-鿿]+", "-", stem).strip("-").lower()
    return slug or "imported"


def unique_path(target: pathlib.Path) -> pathlib.Path:
    if not target.exists():
        return target
    for n in range(2, 100):
        candidate = target.with_name(f"{target.stem}-{n}{target.suffix}")
        if not candidate.exists():
            return candidate
    raise KbError(f"同名文件过多,无法为 {target.name} 分配快照名")


def cmd_ingest(args: argparse.Namespace) -> int:
    src = pathlib.Path(args.file).expanduser()
    if not src.is_file():
        raise KbError(f"文件不存在:{src}")
    kb = resolve_kb_dir(args.kb)
    ensure_kb_skeleton(kb)

    text = normalize_text(extract_source(src))
    outline = detect_outline(text)

    snapshot = unique_path(kb / "sources" / src.name)
    shutil.copy2(src, snapshot)

    inbox = kb / "inbox" / slugify(src.stem)
    # Same filename -> same slug -> source.md is overwritten. That is the right
    # default (same name means a new edition of the same document, and the latest
    # is what you want), but doing it *silently* is not: whatever was still waiting
    # to be turned into clauses just vanished, with nothing in the output to say so.
    # The original is still snapshotted in sources/, so nothing is actually lost —
    # only the pending work. Say that much.
    superseded = 0
    if (inbox / "source.md").is_file():
        superseded = read_text_file(inbox / "source.md").count("\n")
    inbox.mkdir(parents=True, exist_ok=True)
    (inbox / "source.md").write_text(text, encoding="utf-8")
    outline_lines = [f"- L{lineno}: {title}" for lineno, title in outline]
    (inbox / "outline.md").write_text(
        f"# outline of {src.name}(自动探测,仅供分段参考)\n\n"
        + ("\n".join(outline_lines) + "\n" if outline_lines else "(未探测到标题)\n"),
        encoding="utf-8",
    )

    total_lines = text.count("\n")
    print(f"KB 目录        : {kb}")
    print(f"原始快照       : {snapshot.relative_to(kb)}")
    print(f"待条款化文本   : {(inbox / 'source.md').relative_to(kb)}({total_lines} 行)")
    print(f"标题大纲       : {(inbox / 'outline.md').relative_to(kb)}({len(outline)} 个候选标题)")

    if superseded:
        print()
        print(f"⚠ 覆盖:inbox/{inbox.name}/ 里原有上次导入、**尚未条款化**的内容"
              f"({superseded} 行),已被本次的新版覆盖。")
        print(f"  上一版原文快照仍在 sources/ 下(未删除),需要时可重新导入。")

    existing = sum(len(load_rule_file(p)[0])
                   for p in iter_files(kb, "rules", (".yaml", ".yml")))
    if existing:
        # The script cannot tell which clauses the new edition dropped — that is a
        # semantic diff. What it *can* do deterministically is refuse to let a
        # re-import look like a first import, which is how stale clauses survive.
        print()
        print(f"⚠ 换版导入:知识库里已有 {existing} 条现行条款。")
        print("  条款化前必须先读 INDEX.md,把新版原文与现有条款逐条比对:"
              "新增 / 沿用 / 修改 / 废止。")
        print("  废止的条款移进 archive/ 并标 status: deprecated —— **不要直接删除**"
              "(ID 需永远可追溯,且永不复用)。")
        print("  漏掉这一步,各 skill 会继续按已废止的规范判客户违规,而 validate 查不出来。")
        print()

    print("Next: 按 SKILL.md 工作流分段阅读 source.md,把条款分类写入 rules/ guides/ errata/,"
          "然后运行 index 与 validate。")
    return 0


# ---------------------------------------------------------------- shared parsing

def split_frontmatter(text: str) -> tuple[dict | None, str | None]:
    """Return (meta, error). meta is {} when no frontmatter block exists."""
    if not text.startswith("---"):
        return {}, None
    match = re.match(r"^---\s*\n(.*?)\n---\s*\n?", text, re.S)
    if not match:
        return None, "frontmatter 起始 --- 没有对应的结束 ---"
    try:
        meta = yaml.safe_load(match.group(1))
    except yaml.YAMLError as exc:
        return None, f"frontmatter YAML 解析失败:{exc}"
    if meta is None:
        return {}, None
    if not isinstance(meta, dict):
        return None, "frontmatter 不是键值映射"
    return meta, None


def load_rule_file(path: pathlib.Path) -> tuple[list, str | None]:
    try:
        data = yaml.safe_load(read_text_file(path))
    except (OSError, yaml.YAMLError) as exc:
        return [], f"YAML 解析失败:{exc}"
    if data is None:
        return [], None
    if not isinstance(data, list):
        return [], "顶层必须是条款列表(yaml list)"
    return data, None


def rule_status(entry: dict) -> str:
    """`active` unless the entry says otherwise.

    Legacy clauses were written before the field existed; treating a missing
    `status` as anything but active would retroactively withdraw the whole
    existing knowledge base.
    """
    return str(entry.get("status") or STATUS_ACTIVE).strip().lower()


def iter_files(kb: pathlib.Path, sub: str, suffixes: tuple[str, ...]) -> list[pathlib.Path]:
    root = kb / sub
    if not root.is_dir():
        return []
    return sorted(p for p in root.rglob("*") if p.is_file() and p.suffix in suffixes)


def first_heading(path: pathlib.Path) -> str:
    try:
        text = read_text_file(path)
    except OSError:
        return path.stem
    meta, _ = split_frontmatter(text)
    if meta and meta.get("description"):
        return str(meta["description"])
    for line in text.splitlines():
        stripped = line.strip().lstrip("#").strip()
        if stripped and not stripped.startswith("---"):
            return stripped
    return path.stem


# ---------------------------------------------------------------- index

def describe_rule_file(path: pathlib.Path) -> str:
    entries, err = load_rule_file(path)
    if err:
        return f"⚠ {err}"
    ids = [e.get("id", "?") for e in entries if isinstance(e, dict)]
    span = f"{ids[0]}..{ids[-1]}" if ids else "空"
    comment = ""
    try:
        head = read_text_file(path).splitlines()[0]
        if head.startswith("#"):
            comment = ":" + head.lstrip("#").strip()
    except (OSError, IndexError):
        pass
    return f"{len(ids)} 条({span}){comment}"


# RULES.md is the rule-level L1 index: one line per *active* clause, always loaded
# by the consuming skills so the model picks by reading the full set instead of
# guessing what keyword to grep. INDEX.md stops at the file — the semantic-drift
# gap lives in the file→clause jump, and this is what closes it.
_LISTED_ID_RE = re.compile(r"`(GS-[A-Z]{2,4}-\d{3})`")


def iter_active_rules(kb: pathlib.Path):
    """Yield (path, err, active_entries) for each rules/*.yaml, in file order.

    Deprecated clauses are dropped here — RULES.md is the judge-against list, and a
    withdrawn clause must never enter it (that is the whole point of archive/).
    """
    for path in iter_files(kb, "rules", (".yaml", ".yml")):
        entries, err = load_rule_file(path)
        if err:
            yield path, err, []
            continue
        active = [e for e in entries
                  if isinstance(e, dict) and rule_status(e) == STATUS_ACTIVE]
        yield path, None, active


def _rule_file_comment(path: pathlib.Path) -> str:
    try:
        head = read_text_file(path).splitlines()[0]
    except (OSError, IndexError):
        return ""
    return head.lstrip("#").strip() if head.startswith("#") else ""


def render_rules_listing(kb: pathlib.Path) -> str:
    total = 0
    body: list[str] = []
    for path, err, active in iter_active_rules(kb):
        rel = path.relative_to(kb / "rules")
        comment = _rule_file_comment(path)
        body += ["", f"## rules/{rel}" + (f" — {comment}" if comment else ""), ""]
        if err:
            body.append(f"- ⚠ {err}(修好后重跑 index)")
            continue
        for entry in active:
            rid = entry.get("id", "?")
            sev = entry.get("severity", "?")
            rule = str(entry.get("rule", "")).strip() or "(空条款)"
            body.append(f"- `{rid}` · {sev} · {rule}")
            total += 1

    header = [
        "# 现行条款速查(RULES)",
        "",
        f"> 由 `kb.py index` 自动生成,勿手工编辑。版本 {read_version(kb)} · "
        f"现行条款 {total} 条。",
        "> **这是全部现行条款的逐条清单。** 判定前先在此逐条判断相关性,"
        "再去 `rules/` 读选中条款全文(rationale/criteria/keywords)。",
        "> 不必猜关键词 —— 条款都在这里。已废止条款不在此列"
        "(见 `INDEX.md` 的 archive 段,仅供 ID 追溯,**不得据此判定**)。",
    ]
    return "\n".join(header + (body or ["", "(暂无现行条款)"])) + "\n"


def cmd_index(args: argparse.Namespace) -> int:
    kb = resolve_kb_dir(args.kb)
    if not kb.is_dir():
        raise KbError(f"KB 目录不存在:{kb}(先运行 ingest 或手工创建)")
    ensure_kb_skeleton(kb)

    errata = iter_files(kb, "errata", (".md",))
    rules = iter_files(kb, "rules", (".yaml", ".yml"))
    guides = iter_files(kb, "guides", (".md",))
    archive = iter_files(kb, "archive", (".yaml", ".yml"))
    rule_total = sum(len(load_rule_file(p)[0]) for p in rules)
    archive_total = sum(len(load_rule_file(p)[0]) for p in archive)

    lines = [
        "# 规范知识库索引(INDEX)",
        "",
        f"> 由 `kb.py index` 自动生成,勿手工编辑。版本 {read_version(kb)} · "
        f"勘误 {len(errata)} 篇 · 条款 {rule_total} 条 · 指南 {len(guides)} 篇 · "
        f"已废止 {archive_total} 条。",
        "> 查询优先级:errata/ > rules/ > guides/ > 模型自带知识。",
        "> **现行条款的逐条清单见 `RULES.md`**(判定前先读它);本索引到文件级为止。",
        "",
        "## errata/(修正与例外 —— 最高优先级)",
        "",
    ]
    lines += [f"- `errata/{p.relative_to(kb / 'errata')}` — {first_heading(p)}" for p in errata] or ["(暂无)"]
    lines += ["", "## rules/(机器可判定条款,稳定 GS-* ID)", ""]
    lines += [f"- `rules/{p.relative_to(kb / 'rules')}` — {describe_rule_file(p)}" for p in rules] or ["(暂无)"]
    lines += ["", "## guides/(语义指南,模型判断依据)", ""]
    lines += [f"- `guides/{p.relative_to(kb / 'guides')}` — {first_heading(p)}" for p in guides] or ["(暂无)"]
    lines += [
        "",
        "## archive/(已废止条款 —— 仅供追溯,**不得据此判定**)",
        "",
        "> 这些条款已被客户的新版规范废止,**不在检索范围内**,也不得用来认定任何违规。",
        "> 列在这里只为一件事:历史报告引用过这些 ID,ID 必须永远可追溯、且永不复用。",
        "",
    ]
    lines += [f"- `archive/{p.relative_to(kb / 'archive')}` — {describe_rule_file(p)}" for p in archive] or ["(暂无)"]
    lines += ["", "定位方式:先按本索引选文件,再 `grep -rn \"<关键词>\" <kb>/{errata,rules,guides}/`"
              "(archive/ 不在其中,是有意为之)。", ""]

    (kb / "INDEX.md").write_text("\n".join(lines), encoding="utf-8")
    listing = render_rules_listing(kb)
    (kb / "RULES.md").write_text(listing, encoding="utf-8")
    print(f"INDEX.md 已重建:{kb / 'INDEX.md'}({len(lines)} 行);"
          f"RULES.md 已重建:{kb / 'RULES.md'}({rule_total} 条现行条款)")
    return 0


# ---------------------------------------------------------------- validate

def _duplicate_id_msg(where: str, rid: str, first_seen: str) -> str:
    """Name the offender, and say which kind of collision it is."""
    if first_seen.startswith("archive/"):
        return (f"{where}: ID `{rid}` 复用了 archive/ 中**已废止**条款的 ID"
                f"({first_seen})—— ID 永不复用:历史报告引用过它,复用会让追溯指向"
                f"一条完全不同的条款。请分配新号。")
    return f"{where}: ID `{rid}` 与 {first_seen} 重复"


def _validate_placement(where: str, rid: str, sub: str, entry: dict,
                        findings: list) -> None:
    """A withdrawn clause must be in archive/, and archive/ must hold only those.

    Both halves are silent failures if unchecked. Tagged-but-not-moved: the skills
    grep rules/ and keep hitting the clause's `rule:` line, never seeing the tag —
    they go on judging the customer against a spec that was withdrawn. The reverse,
    moved-but-not-tagged, quietly drops a clause that is still in force out of the
    searchable range. Neither shows up as an error anywhere else.
    """
    status = rule_status(entry)
    if status not in STATUSES:
        findings.append((
            "error",
            f"{where}: status `{entry.get('status')}` 非法,必须是 {sorted(STATUSES)}"))
        return

    if sub == "rules" and status == STATUS_DEPRECATED:
        findings.append((
            "error",
            f"{where}: 条款 `{rid}` 标了 status: deprecated 却仍留在 rules/ —— "
            f"各 skill 用 `grep -rn` 检索 rules/,只看得到命中行、看不到 status,"
            f"**照样会命中它**并按已废止的规范判定。请移到 archive/(ID 保留,不要删除)"))
    elif sub == "archive" and status != STATUS_DEPRECATED:
        findings.append((
            "error",
            f"{where}: 条款 `{rid}` 放在 archive/ 却不是 status: deprecated —— "
            f"archive/ 不在检索范围内,一条现行条款搁在这里等于被静默弃用。"
            f"要么补 status: deprecated,要么移回 rules/"))
    elif sub == "archive" and not entry.get("superseded_by"):
        findings.append((
            "warn",
            f"{where}: 废止条款建议填写 superseded_by(被哪条取代,或为何废止),"
            f"否则日后无法追溯当初为什么下架"))


def validate_rules(kb: pathlib.Path, findings: list) -> None:
    seen: dict[str, str] = {}
    for sub in RULE_DIRS:                    # ID 唯一性必须跨目录:废止的 ID 也不得复用
        for path in iter_files(kb, sub, (".yaml", ".yml")):
            rel = path.relative_to(kb)
            entries, err = load_rule_file(path)
            if err:
                findings.append(("error", f"{rel}: {err}"))
                continue
            for i, entry in enumerate(entries):
                where = f"{rel}[{i}]"
                if not isinstance(entry, dict):
                    findings.append(("error", f"{where}: 条款必须是键值映射"))
                    continue
                missing = [f for f in RULE_REQUIRED_FIELDS if not entry.get(f)]
                if missing:
                    findings.append(("error", f"{where}: 缺少必填字段 {missing}"))
                rid = str(entry.get("id", ""))
                if rid and not RULE_ID_RE.match(rid):
                    findings.append(("error", f"{where}: ID `{rid}` 不符合 GS-<域>-NNN 格式"))
                if rid in seen:
                    findings.append(("error", _duplicate_id_msg(where, rid, seen[rid])))
                elif rid:
                    seen[rid] = str(where)
                if entry.get("severity") not in SEVERITIES:
                    findings.append(("error", f"{where}: severity 必须是 {sorted(SEVERITIES)}"))
                if entry.get("check") not in CHECK_KINDS:
                    findings.append(("error", f"{where}: check 必须是 {sorted(CHECK_KINDS)}"))
                if entry.get("check") == "advisory" and not entry.get("criteria"):
                    findings.append(("warn", f"{where}: advisory 条款建议提供 criteria(判定依据)"))
                if not entry.get("source"):
                    findings.append(("warn", f"{where}: 建议填写 source(原文出处),保证可追溯"))
                _validate_placement(where, rid, sub, entry, findings)


def validate_guides(kb: pathlib.Path, findings: list) -> None:
    seen: dict[str, str] = {}
    for path in iter_files(kb, "guides", (".md",)):
        rel = str(path.relative_to(kb))
        try:
            text = read_text_file(path)
        except OSError as exc:
            findings.append(("error", f"{rel}: 读取失败:{exc}"))
            continue
        meta, err = split_frontmatter(text)
        if err:
            findings.append(("error", f"{rel}: {err}"))
            continue
        if not meta:
            findings.append(("error", f"{rel}: 缺少 frontmatter(需要 id 与 description)"))
            continue
        for field in ("id", "description"):
            if not meta.get(field):
                findings.append(("error", f"{rel}: frontmatter 缺少 {field}"))
        gid = str(meta.get("id", ""))
        if gid and gid in seen:
            findings.append(("error", f"{rel}: guide id `{gid}` 与 {seen[gid]} 重复"))
        elif gid:
            seen[gid] = rel


# Directories the consuming skills grep. MUST stay in step with the grep range in
# references/kb-contract.md. archive/ is absent on purpose — that omission is the
# entire mechanism by which a withdrawn clause stops reaching the skills.
# sources/ is excluded too: it holds the customer's original files and keeps
# whatever encoding they arrived in; nothing greps it.
_SEARCHABLE = (("errata", (".md",)), ("rules", (".yaml", ".yml")), ("guides", (".md",)))

# archive/ is out of the grep range but validate still parses it, so it has to be
# UTF-8 like the rest — a GB18030 archive file would make ID-reuse checks miss it.
_ENCODED = _SEARCHABLE + (("archive", (".yaml", ".yml")),)


def validate_encoding(kb: pathlib.Path, findings: list) -> None:
    """Non-UTF-8 files in the searchable dirs are a silent trap.

    The consuming skills locate clauses with `grep -rn "<关键词>" <kb>/...`, per the
    contract block. grep compares the LLM's UTF-8 bytes against the file's bytes —
    against a GB18030 file that simply does not match, and grep does not complain:
    it just finds nothing. The model then answers「知识库未覆盖,以下为通用经验」
    even though the customer's rule *is* in the KB — exactly the lie this skill
    exists to prevent. Catch it at import time, loudly.

    (kb's own `search` decodes gb18030 and would find the file, which is
    what makes the failure so easy to miss during testing.)
    """
    for sub, suffixes in _ENCODED:
        for path in iter_files(kb, sub, suffixes):
            try:
                path.read_bytes().decode("utf-8")
            except UnicodeDecodeError:
                findings.append((
                    "error",
                    f"{path.relative_to(kb)}: 不是 UTF-8 编码 —— 各 skill 用 "
                    f"grep 检索知识库时会**静默漏掉**这个文件(不报错,只是搜不到),"
                    f"导致模型误以为「知识库未覆盖」。请转存为 UTF-8:"
                    f"`iconv -f gb18030 -t utf-8 {path.name} > {path.name}.utf8 "
                    f"&& mv {path.name}.utf8 {path.name}`"
                ))


def validate_index(kb: pathlib.Path, findings: list) -> None:
    index = kb / "INDEX.md"
    if not index.is_file():
        findings.append(("error", "INDEX.md 不存在(运行 kb.py index)"))
        return
    index_text = read_text_file(index)
    # archive/ is indexed too (its IDs must stay traceable), so it is checked here
    # as well — otherwise deleting an archived file leaves a dangling INDEX entry
    # that nothing complains about.
    kb_files = (
        iter_files(kb, "errata", (".md",))
        + iter_files(kb, "rules", (".yaml", ".yml"))
        + iter_files(kb, "guides", (".md",))
        + iter_files(kb, "archive", (".yaml", ".yml"))
    )
    newest = 0.0
    for path in kb_files:
        rel = str(path.relative_to(kb))
        newest = max(newest, path.stat().st_mtime)
        if rel not in index_text:
            findings.append(("error", f"INDEX.md 缺少条目:{rel}(重新运行 index)"))
    for rel in re.findall(r"`((?:errata|rules|guides|archive)/[^`]+)`", index_text):
        if not (kb / rel).is_file():
            findings.append(("error", f"INDEX.md 引用了不存在的文件:{rel}"))
    if kb_files and index.stat().st_mtime < newest:
        findings.append(("warn", "INDEX.md 比库内容旧(重新运行 index)"))


def validate_rules_listing(kb: pathlib.Path, findings: list) -> None:
    """RULES.md 必须与 rules/ 的现行条款**逐一对齐**,两个方向都当 error。

    这一层引入了一个新的静默失效点:RULES.md 是模型判定时逐条筛选的候选清单,
    清单漏掉一条,那条条款对模型就等同于不存在 —— 跟 grep 没命中一样,不报错、查不出。
    所以校验双向卡死:
      - 现行条款缺席清单 → 模型永远看不见它;
      - 清单里的 ID 在 rules/ 已无对应现行条款 → 幽灵条款(多半是废止/删除后忘了重跑 index),
        会让模型据一条并不存在的规范去判客户违规。
    """
    listing = kb / "RULES.md"
    if not listing.is_file():
        findings.append(("error", "RULES.md 不存在(运行 kb.py index 生成现行条款清单)"))
        return
    listed = set(_LISTED_ID_RE.findall(read_text_file(listing)))

    active: set[str] = set()
    newest = 0.0
    for path, err, entries in iter_active_rules(kb):
        newest = max(newest, path.stat().st_mtime)
        if err:
            continue                      # 坏文件由 validate_rules 另行报错,这里不重复
        active.update(str(e["id"]) for e in entries if e.get("id"))

    for rid in sorted(active - listed):
        findings.append((
            "error",
            f"RULES.md 缺少现行条款 `{rid}` —— 模型逐条筛选时看不到它,"
            f"等同于这条规范不存在(重新运行 index)"))
    for rid in sorted(listed - active):
        findings.append((
            "error",
            f"RULES.md 列出了 `{rid}`,但 rules/ 里已无此现行条款 —— 幽灵条款会让模型"
            f"据一条不存在的规范判违规(废止/删除后重新运行 index)"))
    if active and listing.stat().st_mtime < newest:
        findings.append(("warn", "RULES.md 比 rules/ 旧(重新运行 index)"))


def validate_cases_and_graph(kb: pathlib.Path, findings: list) -> None:
    """案例文件与三元组文件:schema、结论强度、出处、ID 唯一、边的端点/置信度/状态。"""
    from common.kb import cases as kbcases
    from common.kb import graphfiles as gf

    cases, case_findings = kbcases.load_cases(kb)
    findings.extend(case_findings)
    _, tri_findings = gf.load_triples(kb, case_ids=[c.id for c in cases])
    findings.extend(tri_findings)


def cmd_validate(args: argparse.Namespace) -> int:
    kb = resolve_kb_dir(args.kb)
    if not kb.is_dir():
        raise KbError(f"KB 目录不存在:{kb}")
    findings: list[tuple[str, str]] = []
    validate_encoding(kb, findings)      # 先查编码:非 UTF-8 会被 grep 静默漏掉
    validate_rules(kb, findings)
    validate_guides(kb, findings)
    validate_index(kb, findings)
    validate_rules_listing(kb, findings)
    validate_cases_and_graph(kb, findings)
    for sub in sorted((kb / "inbox").glob("*")) if (kb / "inbox").is_dir() else []:
        if not sub.is_dir():
            continue
        if (sub / "manifest.json").is_file():          # 工单批次,不是规范
            items = list((sub / "items").glob("*.md")) if (sub / "items").is_dir() else []
            if items:
                findings.append(("warn", f"inbox/{sub.name} 还有 {len(items)} 单工单未处理(propose → review → apply)"))
            continue
        findings.append(("warn", f"inbox/{sub.name} 尚未条款化(处理完后删除该目录)"))

    errors = [m for lvl, m in findings if lvl == "error"]
    warns = [m for lvl, m in findings if lvl == "warn"]
    for msg in errors:
        print(f"[error] {msg}")
    for msg in warns:
        print(f"[warn ] {msg}")
    print(f"validate: {len(errors)} error, {len(warns)} warn(KB={kb},版本 {read_version(kb)})")
    return 2 if errors else 0


# ---------------------------------------------------------------- search

def _grep_file(path: pathlib.Path, kb: pathlib.Path, needle: str,
               prefix: str = "") -> list[str]:
    """Literal, case-insensitive line matches — the same thing the skills' grep does."""
    try:
        content = read_text_file(path)
    except OSError as exc:
        print(f"{path.relative_to(kb)}: 读取失败:{exc}", file=sys.stderr)
        return []
    return [f"{prefix}{path.relative_to(kb)}:{lineno}: {line.strip()}"
            for lineno, line in enumerate(content.splitlines(), 1)
            if needle in line.lower()]


def cmd_search(args: argparse.Namespace) -> int:
    kb = resolve_kb_dir(args.kb)
    if not kb.is_dir():
        raise KbError(f"KB 目录不存在:{kb}")
    needle = args.keyword.lower()

    # Mirrors the skills' grep range exactly — archive/ is not in it.
    hits: list[str] = []
    for sub, suffixes in _SEARCHABLE:
        for path in iter_files(kb, sub, suffixes):
            hits += _grep_file(path, kb, needle)

    archived = [h for p in iter_files(kb, "archive", (".yaml", ".yml"))
                for h in _grep_file(p, kb, needle, prefix="[已废止] ")]

    with_archive = bool(getattr(args, "include_archived", False))
    shown = hits + (archived if with_archive else [])
    for line in shown[:SEARCH_HIT_CAP]:
        print(line)
    if len(shown) > SEARCH_HIT_CAP:
        print(f"(命中超过 {SEARCH_HIT_CAP} 条,已截断——换更具体的关键词)")

    if hits:
        return 0

    # Nothing *current* matched. Which line to print depends on whether we just
    # listed withdrawn ones: saying 「未命中」 directly under a list of hits is a
    # self-contradiction, and 「未命中」 is the line carrying the discipline (never
    # pass your own knowledge off as the customer's spec), so it must land on the
    # right case rather than being sprayed at both.
    miss = (f"未命中:'{args.keyword}'(KB={kb})。"
            "知识库未覆盖时必须如实说明,不得用自带知识冒充规范。")

    if with_archive and archived:
        print(f"现行条款未命中:'{args.keyword}' —— 上列 {len(archived)} 行均为"
              "**已废止**条款,仅供追溯,不得用于判定。")
    elif archived:
        # The re-import trap: without this note the model concludes 「知识库没这条」
        # while a withdrawn clause on exactly that topic sits in archive/. Say that it
        # exists; never print its text, or it becomes usable for judging.
        print(miss)
        print(f"注:archive/ 中另有 {len(archived)} 行**已废止**条款命中该关键词"
              "(不得用于判定;确需查阅历史加 --include-archived)。")
    else:
        print(miss)
    return 0


# ---------------------------------------------------------------- contract

def load_contract_template() -> str:
    tpl = _HERE.parent.parent / "references" / "kb-contract.md"
    if not tpl.is_file():
        raise KbError(f"契约模板不存在:{tpl}")
    text = read_text_file(tpl).strip() + "\n"
    if CONTRACT_BEGIN not in text or CONTRACT_END not in text:
        raise KbError("契约模板缺少 KB-CONTRACT:BEGIN/END 标记")
    return text


def block_span(text: str) -> tuple[int, int] | None:
    """Locate the contract block by index — never by regex.

    Returns (start, end), or None when no block exists. Raises KbError when the
    markers are malformed, because a half-written marker is a human problem and
    guessing where the block ends is how you delete someone's SKILL.md: a lone
    BEGIN made apply append a second block, and the next run's `BEGIN.*?END`
    then spanned the body in between and replaced the lot.
    """
    begins = [m.start() for m in re.finditer(re.escape(CONTRACT_BEGIN), text)]
    ends = [m.end() for m in re.finditer(re.escape(CONTRACT_END), text)]
    if not begins and not ends:
        return None
    if len(begins) != 1 or len(ends) != 1 or ends[0] <= begins[0]:
        raise KbError(
            f"KB-CONTRACT 标记区损坏(BEGIN×{len(begins)},END×{len(ends)}):"
            "请人工修复成恰好一对、且 BEGIN 在 END 之前,再重跑。"
            "为避免误删正文,本次未做任何写入。"
        )
    return begins[0], ends[0]


def contract_status(skill_md: pathlib.Path, template: str) -> str:
    text = read_text_file(skill_md)
    try:
        span = block_span(text)
    except KbError:
        return "broken"
    if span is None:
        return "missing"
    start, end = span
    return "current" if text[start:end].strip() == template.strip() else "stale"


def apply_contract(skill_md: pathlib.Path, template: str) -> None:
    r"""Rewrite only the marker region. Plain slicing — not re.sub, whose
    replacement string parses backslashes: a `grep -E "\d{3}"` in the
    user-editable template blew up with `re.error: bad escape \d`."""
    text = read_text_file(skill_md)
    span = block_span(text)                   # raises on a broken marker
    block = template.strip()
    if span is None:
        new_text = text.rstrip() + "\n\n" + block + "\n"
    else:
        start, end = span
        new_text = text[:start] + block + text[end:]
    skill_md.write_text(new_text, encoding="utf-8")


def contract_targets(skills_dir: pathlib.Path) -> list[pathlib.Path]:
    """SKILL.md files that should carry the contract (whitelist: CONTRACT_SKILLS)."""
    return sorted(
        p / "SKILL.md"
        for p in skills_dir.iterdir()
        if p.is_dir() and skill_base_name(p.name) in CONTRACT_SKILLS and (p / "SKILL.md").is_file()
    )


def cmd_contract(args: argparse.Namespace) -> int:
    skills_dir = pathlib.Path(args.skills_dir).expanduser() if args.skills_dir \
        else _HERE.parent.parent.parent
    if not skills_dir.is_dir():
        raise KbError(f"skills 目录不存在:{skills_dir}")
    template = load_contract_template()
    targets = contract_targets(skills_dir)
    if not targets:
        raise KbError(
            f"{skills_dir} 下没有找到需要注入契约的 skill"
            f"(白名单:{', '.join(sorted(CONTRACT_SKILLS))})"
        )

    pending = applied = 0
    broken: list[pathlib.Path] = []
    for skill_md in targets:
        status = contract_status(skill_md, template)
        if status == "current":
            print(f"[ok     ] {skill_md}")
            continue
        if not args.apply:
            print(f"[{status:<7}] {skill_md}")
            pending += 1
            continue
        try:
            apply_contract(skill_md, template)
        except KbError as exc:            # 标记区损坏:跳过该文件,绝不猜着写
            print(f"[skipped] {skill_md}: {exc}", file=sys.stderr)
            broken.append(skill_md)
            continue
        print(f"[applied] {skill_md}")
        applied += 1

    if args.apply:
        print(f"contract: 实际写入 {applied} 个,已是最新 {len(targets) - applied - len(broken)} 个,"
              f"标记区损坏跳过 {len(broken)} 个(标记区外内容一字未动)。"
              "注意:安装目录副本会被下次 install 覆盖,源码仓也要 apply。")
        return 2 if broken else 0
    print(f"contract: {pending} 个待注入/待刷新(--apply 执行)")
    return 2 if pending else 0


# ---------------------------------------------------------------- main

def build_parser() -> argparse.ArgumentParser:
    import kb_cases
    import kb_cite
    import kb_store

    parser = argparse.ArgumentParser(
        prog="kb.py",
        description="GaussDB 客户知识库:规范/工单导入 · 选择列表确认 · 索引进高斯/PG 与 Neo4j · 检索 · 契约注入",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_ingest = sub.add_parser("ingest", help="导入规范(txt/md/docx/doc/pdf → 条款化)或工单(xlsx/csv/md/txt/docx → 一单一文件)")
    p_ingest.add_argument("file", help="文档/工单路径")
    p_ingest.add_argument("--kb", help="KB 目录(默认 $GSDB_KB_DIR 或 <安装根>/kb)")
    p_ingest.add_argument("--kind", choices=("spec", "tickets"),
                          help="材料类型;默认 xlsx/csv 视为工单,其余视为规范")
    p_ingest.add_argument("--slug", help="工单批次名(默认取文件名)")
    p_ingest.add_argument("--redact", action="store_true", help="工单原文脱敏:IP/手机号/证件号/邮箱")
    p_ingest.set_defaults(func=cmd_ingest_any)

    p_index = sub.add_parser("index", help="重建 INDEX.md/RULES.md,并把文件索引进高斯/PG 与 Neo4j(kb.yaml 配了存储才写库)")
    p_index.add_argument("--kb")
    p_index.add_argument("--rebuild", action="store_true", help="清两库重灌(文件是真相)")
    p_index.add_argument("--fill-missing", action="store_true", help="只补没向量的块")
    p_index.set_defaults(func=cmd_index_all)
    kb_store.add_subcommands(sub)
    kb_cases.add_subcommands(sub)
    kb_cite.add_subcommands(sub)

    p_validate = sub.add_parser("validate", help="校验 ID/schema/INDEX 一致性")
    p_validate.add_argument("--kb")
    p_validate.set_defaults(func=cmd_validate)

    p_search = sub.add_parser("search", help="检索知识库(errata 优先;不含已废止条款)")
    p_search.add_argument("keyword")
    p_search.add_argument("--kb")
    p_search.add_argument("--include-archived", action="store_true",
                          help="连 archive/ 里的已废止条款一并列出(仅供人工追溯,不得用于判定)")
    p_search.set_defaults(func=cmd_search)

    p_contract = sub.add_parser("contract", help="向做规范/阈值判断的 skill 注入知识库参考契约")
    p_contract.add_argument("--apply", action="store_true", help="实际写入(默认只扫描)")
    p_contract.add_argument("--skills-dir", help="skills 根目录(默认本 skill 的上级目录)")
    p_contract.set_defaults(func=cmd_contract)
    return parser


def cmd_ingest_any(args: argparse.Namespace) -> int:
    """按 --kind(或后缀)分流:工单 → kb_cases;规范 → 原条款化路径。"""
    import kb_cases
    kind = args.kind or ("tickets" if pathlib.Path(args.file).suffix.lower() in (".xlsx", ".csv") else "spec")
    if kind == "tickets":
        kb = resolve_kb_dir(args.kb)
        ensure_kb_skeleton(kb)
        return kb_cases.cmd_ingest_tickets(args, kb)
    return cmd_ingest(args)


def cmd_index_all(args: argparse.Namespace) -> int:
    """文件级索引(INDEX.md/RULES.md)永远做;配了存储再写高斯/PG 与 Neo4j。"""
    import kb_store
    from common.kb import config as kbconfig

    rc = cmd_index(args)
    if rc != 0:
        return rc
    kb = resolve_kb_dir(args.kb)
    cfg = kbconfig.load(kb)
    if cfg.store.pg is None:
        print("存储        : kb.yaml 未配置 store.pg——只重建了文件索引;配好后重跑 index 即写库")
        return 0
    return kb_store.cmd_index(args)


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    import kb_cases
    import kb_cite
    import kb_store
    from common.kb import config as kbconfig
    try:
        return args.func(args)
    except (KbError, kb_store.StoreCmdError, kb_cases.CaseCmdError, kb_cite.CiteCmdError,
            kbconfig.KbConfigError) as exc:
        print(f"错误:{exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
