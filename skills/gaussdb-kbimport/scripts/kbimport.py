#!/usr/bin/env python3
"""kbimport — build and govern the GaussDB spec knowledge base (kb).

Deterministic half of the KB pipeline. The model (per SKILL.md) does the
semantic half — classifying clauses into rules / guides / errata and drafting
entries. This script owns everything that must not depend on a model:

    ingest <file>       convert txt/md/docx/doc into <kb>/inbox/<slug>/source.md
                        (+ heading outline), snapshot the original into <kb>/sources/
    index               rebuild <kb>/INDEX.md from rules/*.yaml + guides/ + errata/
    validate            check encoding (non-UTF-8 is invisible to the skills' grep),
                        rule IDs, yaml schema, frontmatter, INDEX consistency
    search <keyword>    grep across errata/ rules/ guides/ (errata first)
    contract [--apply]  inject the KB-reference contract block into the judging skills

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

KB_SUBDIRS = ("errata", "rules", "guides", "sources", "inbox")
RULE_ID_RE = re.compile(r"^GS-[A-Z]{2,4}-\d{3}$")
SEVERITIES = frozenset({"error", "warn", "info"})
CHECK_KINDS = frozenset({"deterministic", "advisory"})
RULE_REQUIRED_FIELDS = ("id", "severity", "check", "rule")
CONTRACT_BEGIN = "<!-- KB-CONTRACT:BEGIN"
CONTRACT_END = "<!-- KB-CONTRACT:END -->"
# 只有会做「规范判断 / 阈值判断」的 skill 才需要知识库契约。gaussdb-slowsql / gaussdb-topsql /
# gaussdb-sqlfetch / gaussdb-explain / gaussdb-topproc / gaussdb-procinfo 是纯取数(列表格、还原 SQL 文本),
# 要求它们「先查知识库再作答」只是噪音与延迟。
CONTRACT_SKILLS = frozenset({
    "gaussdb-sqlreview", "gaussdb-health", "gaussdb-wdr", "gaussdb-memanalyze", "gaussdb-sqltune", "gaussdb-proctune",
})
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

        ~/.config/opencode/skills/gaussdb-kbimport/scripts/kbimport.py -> ~/.config/opencode
        <项目>/.opencode/skills/.../kbimport.py                 -> <项目>/.opencode
        <仓库>/skills/.../kbimport.py                           -> <仓库>
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


def extract_source(path: pathlib.Path) -> str:
    suffix = path.suffix.lower()
    if suffix in (".txt", ".md", ".sql"):
        return read_text_file(path)
    if suffix == ".docx":
        return extract_docx(path)
    if suffix == ".doc":
        return extract_doc(path)
    raise KbError(f"不支持的格式 {suffix}(支持 .txt/.md/.docx/.doc;PDF 请先转成文本)")


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


def cmd_index(args: argparse.Namespace) -> int:
    kb = resolve_kb_dir(args.kb)
    if not kb.is_dir():
        raise KbError(f"KB 目录不存在:{kb}(先运行 ingest 或手工创建)")
    ensure_kb_skeleton(kb)

    errata = iter_files(kb, "errata", (".md",))
    rules = iter_files(kb, "rules", (".yaml", ".yml"))
    guides = iter_files(kb, "guides", (".md",))
    rule_total = sum(len(load_rule_file(p)[0]) for p in rules)

    lines = [
        "# 规范知识库索引(INDEX)",
        "",
        f"> 由 `kbimport.py index` 自动生成,勿手工编辑。版本 {read_version(kb)} · "
        f"勘误 {len(errata)} 篇 · 条款 {rule_total} 条 · 指南 {len(guides)} 篇。",
        "> 查询优先级:errata/ > rules/ > guides/ > 模型自带知识。",
        "",
        "## errata/(修正与例外 —— 最高优先级)",
        "",
    ]
    lines += [f"- `errata/{p.relative_to(kb / 'errata')}` — {first_heading(p)}" for p in errata] or ["(暂无)"]
    lines += ["", "## rules/(机器可判定条款,稳定 GS-* ID)", ""]
    lines += [f"- `rules/{p.relative_to(kb / 'rules')}` — {describe_rule_file(p)}" for p in rules] or ["(暂无)"]
    lines += ["", "## guides/(语义指南,模型判断依据)", ""]
    lines += [f"- `guides/{p.relative_to(kb / 'guides')}` — {first_heading(p)}" for p in guides] or ["(暂无)"]
    lines += ["", "定位方式:先按本索引选文件,再 `grep -rn \"<关键词>\" <kb>/{errata,rules,guides}/`。", ""]

    (kb / "INDEX.md").write_text("\n".join(lines), encoding="utf-8")
    print(f"INDEX.md 已重建:{kb / 'INDEX.md'}({len(lines)} 行)")
    return 0


# ---------------------------------------------------------------- validate

def validate_rules(kb: pathlib.Path, findings: list) -> None:
    seen: dict[str, str] = {}
    for path in iter_files(kb, "rules", (".yaml", ".yml")):
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
                findings.append(("error", f"{where}: ID `{rid}` 与 {seen[rid]} 重复"))
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


# Directories the consuming skills grep. sources/ is deliberately excluded: it
# holds the customer's original files and should keep whatever encoding they came
# in; nothing greps it.
_SEARCHABLE = (("errata", (".md",)), ("rules", (".yaml", ".yml")), ("guides", (".md",)))


def validate_encoding(kb: pathlib.Path, findings: list) -> None:
    """Non-UTF-8 files in the searchable dirs are a silent trap.

    The consuming skills locate clauses with `grep -rn "<关键词>" <kb>/...`, per the
    contract block. grep compares the LLM's UTF-8 bytes against the file's bytes —
    against a GB18030 file that simply does not match, and grep does not complain:
    it just finds nothing. The model then answers「知识库未覆盖,以下为通用经验」
    even though the customer's rule *is* in the KB — exactly the lie this skill
    exists to prevent. Catch it at import time, loudly.

    (kbimport's own `search` decodes gb18030 and would find the file, which is
    what makes the failure so easy to miss during testing.)
    """
    for sub, suffixes in _SEARCHABLE:
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
        findings.append(("error", "INDEX.md 不存在(运行 kbimport.py index)"))
        return
    index_text = read_text_file(index)
    kb_files = (
        iter_files(kb, "errata", (".md",))
        + iter_files(kb, "rules", (".yaml", ".yml"))
        + iter_files(kb, "guides", (".md",))
    )
    newest = 0.0
    for path in kb_files:
        rel = str(path.relative_to(kb))
        newest = max(newest, path.stat().st_mtime)
        if rel not in index_text:
            findings.append(("error", f"INDEX.md 缺少条目:{rel}(重新运行 index)"))
    for rel in re.findall(r"`((?:errata|rules|guides)/[^`]+)`", index_text):
        if not (kb / rel).is_file():
            findings.append(("error", f"INDEX.md 引用了不存在的文件:{rel}"))
    if kb_files and index.stat().st_mtime < newest:
        findings.append(("warn", "INDEX.md 比库内容旧(重新运行 index)"))


def cmd_validate(args: argparse.Namespace) -> int:
    kb = resolve_kb_dir(args.kb)
    if not kb.is_dir():
        raise KbError(f"KB 目录不存在:{kb}")
    findings: list[tuple[str, str]] = []
    validate_encoding(kb, findings)      # 先查编码:非 UTF-8 会被 grep 静默漏掉
    validate_rules(kb, findings)
    validate_guides(kb, findings)
    validate_index(kb, findings)
    for sub in sorted((kb / "inbox").glob("*")) if (kb / "inbox").is_dir() else []:
        if sub.is_dir():
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

def cmd_search(args: argparse.Namespace) -> int:
    kb = resolve_kb_dir(args.kb)
    if not kb.is_dir():
        raise KbError(f"KB 目录不存在:{kb}")
    needle = args.keyword.lower()
    hits = 0
    for sub, suffixes in (("errata", (".md",)), ("rules", (".yaml", ".yml")), ("guides", (".md",))):
        for path in iter_files(kb, sub, suffixes):
            try:
                content = read_text_file(path)
            except OSError as exc:
                print(f"{path.relative_to(kb)}: 读取失败:{exc}", file=sys.stderr)
                continue
            for lineno, line in enumerate(content.splitlines(), 1):
                if needle in line.lower():
                    print(f"{path.relative_to(kb)}:{lineno}: {line.strip()}")
                    hits += 1
                    if hits >= SEARCH_HIT_CAP:
                        print(f"(命中超过 {SEARCH_HIT_CAP} 条,已截断——换更具体的关键词)")
                        return 0
    if hits == 0:
        print(f"未命中:'{args.keyword}'(KB={kb})。知识库未覆盖时必须如实说明,不得用自带知识冒充规范。")
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
        if p.is_dir() and p.name in CONTRACT_SKILLS and (p / "SKILL.md").is_file()
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
    parser = argparse.ArgumentParser(
        prog="kbimport.py",
        description="GaussDB 规范知识库:导入 / 索引 / 校验 / 检索 / 契约注入",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_ingest = sub.add_parser("ingest", help="导入 txt/md/docx/doc 到 inbox 待条款化")
    p_ingest.add_argument("file", help="规范文档路径")
    p_ingest.add_argument("--kb", help="KB 目录(默认 $GSDB_KB_DIR 或 $GSDB_HOME/kb)")
    p_ingest.set_defaults(func=cmd_ingest)

    p_index = sub.add_parser("index", help="从 rules/guides/errata 重建 INDEX.md")
    p_index.add_argument("--kb")
    p_index.set_defaults(func=cmd_index)

    p_validate = sub.add_parser("validate", help="校验 ID/schema/INDEX 一致性")
    p_validate.add_argument("--kb")
    p_validate.set_defaults(func=cmd_validate)

    p_search = sub.add_parser("search", help="检索知识库(errata 优先)")
    p_search.add_argument("keyword")
    p_search.add_argument("--kb")
    p_search.set_defaults(func=cmd_search)

    p_contract = sub.add_parser("contract", help="向做规范/阈值判断的 skill 注入知识库参考契约")
    p_contract.add_argument("--apply", action="store_true", help="实际写入(默认只扫描)")
    p_contract.add_argument("--skills-dir", help="skills 根目录(默认本 skill 的上级目录)")
    p_contract.set_defaults(func=cmd_contract)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except KbError as exc:
        print(f"错误:{exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
