"""工单导入的确定性半边:csv / xlsx / md / txt → 一单一文件(inbox/<slug>/items/*.md)。

xlsx 用 zipfile + xml.etree 直接读(不引 openpyxl);csv 自动识别 utf-8 / gb18030。
列名靠同义词启发式猜(标题/描述/处理过程/解决方案/根因/系统/时间/工单号/级别),
猜的结果只是**建议**,首次导入时会作为策略问题交给用户确认(propose)。

脱敏(--redact)是确定性正则:IPv4、手机号、身份证、邮箱 → 占位符;对象名/表名不动,那是知识本身。
"""
from __future__ import annotations

import csv
import io
import json
import pathlib
import re
import zipfile
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Tuple
from xml.etree import ElementTree as ET

# 列名同义词(小写比较);顺序即优先级
COLUMN_SYNONYMS: Dict[str, Tuple[str, ...]] = {
    "id": ("工单号", "工单编号", "单号", "事件编号", "编号", "ticket", "ticket_id", "id", "incident"),
    "title": ("标题", "主题", "问题标题", "摘要", "title", "subject", "summary"),
    "system": ("系统", "业务系统", "应用", "应用系统", "system", "app", "application"),
    "occurred_at": ("发生时间", "故障时间", "发现时间", "报告时间", "创建时间", "时间", "occurred", "created", "date", "time"),
    "severity": ("级别", "严重级别", "严重程度", "优先级", "等级", "severity", "priority", "level"),
    "description": ("问题描述", "现象", "故障现象", "描述", "现场", "问题", "description", "symptom", "issue"),
    "process": ("处理过程", "排查过程", "分析过程", "诊断过程", "过程", "process", "analysis", "diagnosis"),
    "root_cause": ("根因", "根本原因", "原因分析", "原因", "判断", "root_cause", "rootcause", "cause"),
    "solution": ("解决方案", "处置", "处理措施", "处置措施", "解决办法", "措施", "solution", "resolution", "fix", "action"),
    "verification": ("验证", "效果", "恢复情况", "verification", "outcome", "result"),
}
_SECTION_TITLES = {"description": "问题描述", "process": "处理过程", "root_cause": "根因",
                   "solution": "解决方案", "verification": "验证与效果"}

_IPV4 = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")
_PHONE = re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)")
_IDCARD = re.compile(r"(?<!\d)\d{17}[\dXx](?!\d)")
_EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")


class IngestError(Exception):
    """格式不支持 / 文件损坏 / 空表。"""


@dataclass(frozen=True)
class Item:
    id: str
    title: str
    text: str                      # 给模型读的 markdown 正文
    locator: str                   # 出处定位,如 tickets.xlsx#row=18
    fields: Dict[str, str] = field(default_factory=dict)


# ---------------------------------------------------------------- 表格读取

def _decode(raw: bytes) -> str:
    for enc in ("utf-8-sig", "utf-8", "gb18030"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", "replace")


def read_csv(path: pathlib.Path) -> Tuple[List[str], List[List[str]]]:
    text = _decode(path.read_bytes())
    rows = list(csv.reader(io.StringIO(text)))
    rows = [r for r in rows if any(c.strip() for c in r)]
    if not rows:
        raise IngestError(f"{path.name}:空表")
    headers = [h.strip() for h in rows[0]]
    return headers, [[c.strip() for c in r] + [""] * (len(headers) - len(r)) for r in rows[1:]]


_NS = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
_COL_RE = re.compile(r"^([A-Z]+)")


def _col_index(ref: str) -> int:
    letters = _COL_RE.match(ref).group(1) if _COL_RE.match(ref) else "A"
    n = 0
    for ch in letters:
        n = n * 26 + (ord(ch) - 64)
    return n - 1


def _cell_text(c: ET.Element, shared: List[str]) -> str:
    t = c.get("t")
    if t == "s":
        v = c.find("m:v", _NS)
        try:
            return shared[int(v.text)] if v is not None and v.text is not None else ""
        except (ValueError, IndexError):
            return ""
    if t == "inlineStr":
        return "".join(x.text or "" for x in c.iter("{%s}t" % _NS["m"]))
    v = c.find("m:v", _NS)
    return (v.text or "") if v is not None else ""


def read_xlsx(path: pathlib.Path, sheet: int = 1) -> Tuple[List[str], List[List[str]]]:
    """第 sheet 张表;共享字符串与内联字符串都认;日期序列号原样保留(不猜格式)。"""
    try:
        zf = zipfile.ZipFile(path)
    except zipfile.BadZipFile as exc:
        raise IngestError(f"{path.name}:不是有效的 xlsx(zip 损坏)") from exc
    with zf:
        shared: List[str] = []
        if "xl/sharedStrings.xml" in zf.namelist():
            root = ET.fromstring(zf.read("xl/sharedStrings.xml"))
            for si in root.findall("m:si", _NS):
                shared.append("".join(t.text or "" for t in si.iter("{%s}t" % _NS["m"])))
        name = f"xl/worksheets/sheet{sheet}.xml"
        if name not in zf.namelist():
            raise IngestError(f"{path.name}:没有第 {sheet} 张工作表")
        root = ET.fromstring(zf.read(name))
        rows: List[List[str]] = []
        for row in root.iter("{%s}row" % _NS["m"]):
            cells: Dict[int, str] = {}
            for c in row.findall("m:c", _NS):
                cells[_col_index(c.get("r") or "A")] = _cell_text(c, shared)
            width = max(cells) + 1 if cells else 0
            rows.append([cells.get(i, "").strip() for i in range(width)])
    rows = [r for r in rows if any(r)]
    if not rows:
        raise IngestError(f"{path.name}:空表")
    headers = rows[0]
    width = len(headers)
    return headers, [r[:width] + [""] * (width - len(r)) for r in rows[1:]]


# ---------------------------------------------------------------- 列映射

def guess_columns(headers: Sequence[str]) -> Dict[str, str]:
    """{字段: 列名} 的建议;同一列只映射一次,按 COLUMN_SYNONYMS 的优先级。"""
    used = set()
    out: Dict[str, str] = {}
    lowered = [(h, h.strip().lower()) for h in headers]
    for fld, syns in COLUMN_SYNONYMS.items():
        for syn in syns:
            hit = next((h for h, low in lowered if h not in used and (low == syn or syn in low)), None)
            if hit is not None:
                out[fld] = hit
                used.add(hit)
                break
    return out


def rows_to_items(headers: Sequence[str], rows: Sequence[Sequence[str]], mapping: Dict[str, str],
                  locator_prefix: str) -> List[Item]:
    idx = {h: i for i, h in enumerate(headers)}
    items: List[Item] = []
    for n, row in enumerate(rows, 2):                       # 表头是第 1 行
        get = lambda fld: (row[idx[mapping[fld]]] if fld in mapping and mapping[fld] in idx and idx[mapping[fld]] < len(row) else "").strip()
        tid = get("id") or f"row{n}"
        title = get("title") or (get("description")[:40] if get("description") else tid)
        parts = [f"# {title}", ""]
        meta = [(k, get(k)) for k in ("id", "system", "occurred_at", "severity") if get(k)]
        if meta:
            parts += [f"- {k}: {v}" for k, v in meta] + [""]
        for fld, heading in _SECTION_TITLES.items():
            val = get(fld)
            if val:
                parts += [f"## {heading}", "", val, ""]
        rest = [(h, row[i]) for h, i in idx.items() if i < len(row) and row[i].strip()
                and h not in mapping.values()]
        if rest:
            parts += ["## 原始字段", ""] + [f"- {h}: {v}" for h, v in rest] + [""]
        fields = {h: (row[i] if i < len(row) else "") for h, i in idx.items()}
        items.append(Item(id=str(tid), title=title, text="\n".join(parts).rstrip() + "\n",
                          locator=f"{locator_prefix}#row={n}", fields=fields))
    return items


# ---------------------------------------------------------------- 文本材料

def split_text_items(text: str, locator_prefix: str, stem: str) -> List[Item]:
    """md/txt:用 `\\n---\\n` 分隔多单;没有分隔符就是一单。标题取首个非空行。"""
    blocks = [b.strip() for b in re.split(r"\n-{3,}\n", text) if b.strip()]
    items: List[Item] = []
    for k, block in enumerate(blocks, 1):
        first = next((ln.strip().lstrip("#").strip() for ln in block.splitlines() if ln.strip()), stem)
        tid = stem if len(blocks) == 1 else f"{stem}-{k}"
        items.append(Item(id=tid, title=first[:80], text=block + "\n",
                          locator=f"{locator_prefix}#item={k}" if len(blocks) > 1 else locator_prefix))
    return items


# ---------------------------------------------------------------- 脱敏

def redact(text: str) -> Tuple[str, int]:
    count = 0
    def sub(pattern, repl, s):
        nonlocal count
        s2, n = pattern.subn(repl, s)
        count += n
        return s2
    out = sub(_EMAIL, "<邮箱>", text)
    out = sub(_IDCARD, "<证件号>", out)
    out = sub(_PHONE, "<手机号>", out)
    out = sub(_IPV4, "<IP>", out)
    return out, count


# ---------------------------------------------------------------- 落盘

_SAFE_ID = re.compile(r"[^0-9A-Za-z一-鿿_.-]+")


def write_items(items_dir: pathlib.Path, items: Sequence[Item], source_name: str,
                do_redact: bool = False) -> List[pathlib.Path]:
    """每单一文件,frontmatter 记出处;同名覆盖(同一材料重导)。"""
    items_dir.mkdir(parents=True, exist_ok=True)
    written: List[pathlib.Path] = []
    for it in items:
        safe = _SAFE_ID.sub("-", it.id).strip("-") or "item"
        body, n = redact(it.text) if do_redact else (it.text, 0)
        front = {"item_id": it.id, "title": it.title, "source": it.locator, "source_file": source_name,
                 "redacted": n}
        text = "---\n" + "\n".join(f"{k}: {json.dumps(v, ensure_ascii=False)}" for k, v in front.items()) + "\n---\n\n" + body
        path = items_dir / f"{safe}.md"
        path.write_text(text, encoding="utf-8")
        written.append(path)
    return written
