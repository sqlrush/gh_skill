"""分词与 tsvector / tsquery 字面量 —— 词法检索的确定性半边。

为什么不用引擎自带的 to_tsvector:PG 与 openGauss 的默认 parser 都不做中文分词
(整段中文是一个 token),对标点/冒号的处理也不完全一致。这里在 Python 侧切好
「中文二元组 + 标识符整词 + 标识符碎片」,再拼成 tsvector/tsquery 的**字面量**输入
(`'词':位置` / `'词' | '词'`),两种引擎只负责存和匹配,行为一致且可单测。

标识符(对象名 / GUC / 等待事件)是检索的主入口:整词保留,同时吐出按 . : _ 拆开的
碎片,让「cosp_asyn_task_dtl」「dtl」「walwritelock」都能命中。
"""
from __future__ import annotations

import re
import unicodedata
from typing import Iterable, List, Optional

# 只在查询侧剔除:这些字组成的二元组只会拉低排序。索引侧全量保留。
_QUERY_STOP_CHARS = frozenset("的了在是和与及或等有为把被对于从到就也都很个么这那")
# 查询侧再剔一层泛词:提问里的「怎么处理」「什么问题」跟任何工单都沾边,留着只会让无关工单上榜。
_QUERY_STOP_WORDS = frozenset({
    "怎么", "如何", "为什", "什么", "处理", "问题", "办法", "方案", "建议", "情况", "出现",
    "是否", "可以", "需要", "应该", "怎样", "一下", "请问", "帮我", "看看", "分析", "排查",
})

_CJK = "一-鿿㐀-䶿"
# 顺序有讲究:标识符(可带 . 或 : 连接段)> 数字词(3s / 16gb)> 中文段。
_TOKEN_RE = re.compile(
    rf"(?P<ident>[a-z_][a-z0-9_]*(?:[.:][a-z0-9_]+)*)"
    rf"|(?P<num>[0-9]+[a-z]*)"
    rf"|(?P<cjk>[{_CJK}]+)"
)
_IDENT_SPLIT_RE = re.compile(r"[.:_]+")


def normalize(text: str) -> str:
    """NFKC(全角→半角)、小写、空白折叠。"""
    folded = unicodedata.normalize("NFKC", text).lower()
    return " ".join(folded.split())


def _cjk_tokens(run: str) -> List[str]:
    if len(run) == 1:
        return [run]
    return [run[i:i + 2] for i in range(len(run) - 1)]


def _ident_tokens(ident: str) -> List[str]:
    parts = [p for p in _IDENT_SPLIT_RE.split(ident) if p]
    if len(parts) <= 1:
        return [ident]
    # 整词在前,碎片在后;中间层(去掉 schema 前缀的对象名)也要有。
    out = [ident]
    if "." in ident or ":" in ident:
        out += [seg for seg in re.split(r"[.:]", ident) if seg and seg != ident]
    out += [p for p in parts if p not in out]
    return out


def tokenize(text: str) -> List[str]:
    """索引侧分词:顺序保留,允许重复(位置信息靠它)。"""
    tokens: List[str] = []
    for m in _TOKEN_RE.finditer(normalize(text)):
        kind = m.lastgroup
        if kind == "cjk":
            tokens += _cjk_tokens(m.group())
        elif kind == "ident":
            tokens += _ident_tokens(m.group())
        else:
            tokens.append(m.group())
    return tokens


def query_tokens(text: str) -> List[str]:
    """查询侧分词:剔掉含停用字的中文词,去重保序。"""
    seen = set()
    out: List[str] = []
    for tok in tokenize(text):
        is_cjk = tok[0] >= "㐀"
        if is_cjk and (tok in _QUERY_STOP_WORDS or any(ch in _QUERY_STOP_CHARS for ch in tok)):
            continue
        if tok not in seen:
            seen.add(tok)
            out.append(tok)
    return out


def _quote(lexeme: str) -> str:
    return "'" + lexeme.replace("\\", "\\\\").replace("'", "''") + "'"


def tsvector_literal(tokens: Iterable[str], weight: Optional[str] = None) -> str:
    """`'词':1,3 '词':2` —— tsvector 的字面量输入,不经 to_tsvector。

    weight(A/B/C/D)附在每个位置后,ts_rank 按权重加分;复发标志用 A。
    """
    positions: dict = {}
    order: List[str] = []
    for pos, tok in enumerate(tokens, 1):
        if tok not in positions:
            positions[tok] = []
            order.append(tok)
        positions[tok].append(pos)
    suffix = weight or ""
    return " ".join(
        _quote(tok) + ":" + ",".join(f"{p}{suffix}" for p in positions[tok])
        for tok in order
    )


def tsquery_literal(tokens: Iterable[str], op: str = "|") -> str:
    """`'a' | 'b'` —— tsquery 字面量;默认 OR(召回),按需 AND。"""
    if op not in ("|", "&"):
        raise ValueError(f"tsquery op 只能是 | 或 &,拿到 {op!r}")
    seen = set()
    uniq: List[str] = []
    for tok in tokens:
        if tok not in seen:
            seen.add(tok)
            uniq.append(tok)
    return f" {op} ".join(_quote(tok) for tok in uniq)
