"""语句形态判定：这条 SQL 是只读的吗。

放在 common/grmp/ 而不是 tools/：注册期的硬拦截（script.py）与运行期的
会话模式（executor / runner）都要用它，tools 里的风险标注也复用同一份。
两处各判各的话，会出现「注册时认为只读、执行时开了写会话」这种错位。

判定是词法层面的启发式，不是 SQL 解析器。宁可把不确定的判成"写"——
判错成只读会让写操作拿到写会话之外的默许，判错成写只是多要一次显式声明。
"""
from __future__ import annotations

import re
from typing import List

# 只读语句的起始关键字。不在此列的一律当作写操作。
READ_ONLY_STARTERS = frozenset(
    {"select", "with", "explain", "show", "values", "table", "fetch"}
)

# 前导注释与空白
_LEADING_NOISE_RE = re.compile(r"^(?:\s|--[^\n]*\n?|/\*.*?\*/)+", re.DOTALL)


def leading_keyword(sql: str) -> str:
    """跳过前导空白与注释，取第一个关键字（小写）。"""
    stripped = _LEADING_NOISE_RE.sub("", sql or "").lstrip()
    match = re.match(r"[A-Za-z_]+", stripped)
    return match.group(0).lower() if match else ""


def split_statements(sql: str) -> List[str]:
    """按分号拆成多条语句，跳过字符串字面量里的分号。

    中间件允许一次调用发多条语句（PREPARE + EXPLAIN EXECUTE 就靠这个），
    所以判定必须逐条来。只看首个关键字的话，
    `select 1; drop table t;` 会被判成只读。
    """
    out, buf, quote = [], [], None
    i = 0
    while i < len(sql):
        ch = sql[i]
        if quote:
            buf.append(ch)
            if ch == quote:
                # 连续两个引号是转义，不结束字面量
                if i + 1 < len(sql) and sql[i + 1] == quote:
                    buf.append(sql[i + 1])
                    i += 2
                    continue
                quote = None
        elif ch in ("'", '"'):
            quote = ch
            buf.append(ch)
        elif ch == ";":
            out.append("".join(buf))
            buf = []
        else:
            buf.append(ch)
        i += 1
    out.append("".join(buf))
    return [s for s in out if s.strip()]


def is_read_only(sql: str) -> bool:
    """整条脚本（可能含多语句）是否全部只读。"""
    parts = split_statements(sql)
    if not parts:
        return True
    return all(leading_keyword(p) in READ_ONLY_STARTERS for p in parts)
