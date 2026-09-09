"""把 SQL 里不带 schema 的表名补全成 schema.表 —— search_path 切不成时的替代路。

现场(客户 2026-09-09 早截图):「SET search_path; EXPLAIN」两语句模板在客户的执行器上没走通(探测脚本没灌 /
执行器不认两条语句 / JDBC 只取第一个结果集),退回提示里的备选②「把表名写全」是模型手工做的。这一步该由脚本做:
确定性、可测、报告注明。补全后的 SQL 解析到同一张表,EXPLAIN 的计划与原 SQL 等价。

只动**表位置**上的名字:FROM / JOIN / UPDATE / INSERT INTO / DELETE FROM 后面的标识符,以及 FROM 列表里逗号后的项;
不动列名、别名、函数调用、CTE 名、已带 schema 的名字、字符串与注释里的东西。拿不准的一律不动——漏补的后果是
EXPLAIN 报对象不存在(看得见),补错的后果是分析了另一张表(看不见)。
"""
from __future__ import annotations

import re
from typing import List, Tuple

from common.search_path import valid_schema

_TOKEN_RE = re.compile(
    r"""
    (?P<ws>\s+)
  | (?P<lcomment>--[^\n]*)
  | (?P<bcomment>/\*.*?\*/)
  | (?P<str>'(?:[^']|'')*')
  | (?P<qident>"(?:[^"]|"")+")
  | (?P<param>\{\{[^}]*\}\}|\$\d+|:[A-Za-z_]\w*|\?)
  | (?P<ident>[A-Za-z_][A-Za-z0-9_$]*)
  | (?P<num>\d+(?:\.\d+)?)
  | (?P<punct>[(),.;=<>+\-*/%|^&!~\[\]])
  | (?P<other>.)
    """,
    re.S | re.X,
)

# 表位置的引导词
_TABLE_LEADERS = {"from", "join", "update", "into"}
# 引导词后面可能先出现的修饰词,越过它们再看名字
_SKIP_AFTER_LEADER = {"only", "lateral"}
# 引导词后跟这些就不是表(子查询 / 关键字)
_NOT_A_TABLE = {
    "select", "values", "lateral", "only", "with", "table", "as", "on", "using", "where", "set", "and", "or",
    "not", "in", "exists", "case", "when", "then", "else", "end", "null", "true", "false", "default",
    "unnest", "dual",
}
# 在 FROM 列表里遇到这些词,列表结束(逗号后不再当表名看)
_FROM_LIST_ENDERS = {
    "where", "on", "using", "group", "order", "limit", "having", "window", "union", "except", "intersect",
    "minus", "set", "returning", "fetch", "offset", "for", "join", "inner", "left", "right", "full", "cross",
    "natural", "select", "values", "into", "update", "delete", "insert", "start", "connect",
}


def _tokens(sql: str):
    pos = 0
    for m in _TOKEN_RE.finditer(sql):
        assert m.start() == pos, "tokenizer left a gap"
        pos = m.end()
        yield m.lastgroup, m.group(0)
    assert pos == len(sql), "tokenizer stopped early"


def _cte_names(toks) -> set:
    """WITH a AS (...), b AS (...)  → {a, b}。只认最外层 WITH 列表的形状:名字 [ (列) ] AS (。"""
    names = set()
    sig = [(k, v) for k, v in toks if k not in ("ws", "lcomment", "bcomment")]
    i = 0
    while i < len(sig):
        k, v = sig[i]
        if k == "ident" and v.lower() == "with":
            j = i + 1
            if j < len(sig) and sig[j][0] == "ident" and sig[j][1].lower() == "recursive":
                j += 1
            while j < len(sig):
                if sig[j][0] not in ("ident", "qident"):
                    break
                name = sig[j][1]
                j += 1
                if j < len(sig) and sig[j] == ("punct", "("):          # 列清单
                    depth = 0
                    while j < len(sig):
                        if sig[j] == ("punct", "("):
                            depth += 1
                        elif sig[j] == ("punct", ")"):
                            depth -= 1
                            if depth == 0:
                                j += 1
                                break
                        j += 1
                if not (j < len(sig) and sig[j][0] == "ident" and sig[j][1].lower() == "as"):
                    break
                j += 1
                if not (j < len(sig) and sig[j] == ("punct", "(")):
                    break
                names.add(name.strip('"').lower() if name.startswith('"') else name.lower())
                depth = 0
                while j < len(sig):                                    # 跳过 CTE 体
                    if sig[j] == ("punct", "("):
                        depth += 1
                    elif sig[j] == ("punct", ")"):
                        depth -= 1
                        if depth == 0:
                            j += 1
                            break
                    j += 1
                if j < len(sig) and sig[j] == ("punct", ","):
                    j += 1
                    continue
                break
            i = j
        else:
            i += 1
    return names


def _schema_prefix(schema: str) -> str:
    return (schema if schema == schema.lower() else '"%s"' % schema) + "."


def qualify_tables(sql: str, schema: str) -> Tuple[str, List[str]]:
    """返回 (补全后的 SQL, 被补全的表名列表——按出现顺序,原样大小写/引号)。没有可补的就原样返回。"""
    if not valid_schema(schema):
        raise ValueError("schema 名 %r 不是合法标识符" % (schema,))
    toks = list(_tokens(sql))
    ctes = _cte_names(toks)
    prefix = _schema_prefix(schema)
    sig_idx = [i for i, (k, _) in enumerate(toks) if k not in ("ws", "lcomment", "bcomment")]
    out = list(v for _, v in toks)
    changed: List[str] = []

    def sig_at(n):        # 第 n 个有效 token,(kind, value) / None
        return toks[sig_idx[n]] if 0 <= n < len(sig_idx) else (None, None)

    def is_name(n):
        k, v = sig_at(n)
        if k == "qident":
            return True
        return k == "ident" and v.lower() not in _NOT_A_TABLE and v.lower() not in _FROM_LIST_ENDERS

    def qualify(n, allow_paren=False):
        k, v = sig_at(n)
        nk, nv = sig_at(n + 1)
        if (nk, nv) == ("punct", ".") or ((nk, nv) == ("punct", "(") and not allow_paren):   # 已带 schema / 函数调用
            return
        key = v.strip('"').lower() if k == "qident" else v.lower()
        if key in ctes:
            return
        out[sig_idx[n]] = prefix + v
        changed.append(v)

    def skip_alias(n):    # 表名后面的 [AS] alias,返回别名之后的位置
        k, v = sig_at(n)
        if k == "ident" and v.lower() == "as":
            n += 1
            k, v = sig_at(n)
        if k in ("ident", "qident") and (k == "qident" or v.lower() not in _FROM_LIST_ENDERS | _NOT_A_TABLE):
            n += 1
        return n

    n = 0
    prev_leader = ""
    while n < len(sig_idx):
        k, v = sig_at(n)
        low = v.lower() if k == "ident" else ""
        if k == "ident" and low in _TABLE_LEADERS and not (low == "into" and prev_leader != "insert"):
            m = n + 1
            while sig_at(m)[0] == "ident" and sig_at(m)[1].lower() in _SKIP_AFTER_LEADER:
                m += 1
            if is_name(m):
                qualify(m, allow_paren=(low == "into"))    # INSERT INTO t (a, b) —— 表名后面的括号是列清单,不是函数
                m = skip_alias(m + 1)
                if low == "from":                          # FROM a x, b y, c —— 逗号列表同层继续
                    depth = 0
                    while m < len(sig_idx):
                        mk, mv = sig_at(m)
                        if mk == "punct" and mv == "(":
                            depth += 1
                        elif mk == "punct" and mv == ")":
                            if depth == 0:
                                break
                            depth -= 1
                        elif depth == 0 and mk == "punct" and mv == ",":
                            m += 1
                            while sig_at(m)[0] == "ident" and sig_at(m)[1].lower() in _SKIP_AFTER_LEADER:
                                m += 1
                            if is_name(m):
                                qualify(m)
                                m = skip_alias(m + 1)
                                continue
                            break
                        elif depth == 0 and mk == "ident" and mv.lower() in _FROM_LIST_ENDERS:
                            break
                        elif mk == "punct" and mv == ";":
                            break
                        m += 1
                n = m
                continue
        if k == "ident" and low in ("insert", "delete", "update", "select"):
            prev_leader = low
        n += 1
    return "".join(out), changed


__all__ = ["qualify_tables"]
