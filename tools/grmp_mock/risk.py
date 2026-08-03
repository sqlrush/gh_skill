"""注册期风险标注 —— 标注，不拦截。

区分标准只有一条：**客户环境也会失败的才拦，客户环境能跑通的一律放行。**
占位符落在表名位或 ORDER BY 位，在客户的文本替换中间件上是能正常执行的，
所以我们不能拒绝它 —— 拒绝就等于本地比客户更严，一条客户那边天天在跑的
脚本在本地注册不进去，本地测试反而失去参照价值。

我们能做的是把它**变得可见**：出具一份「哪些脚本的参数存在注入面」的清单。
这不改变运行时行为，只是把本来看不见的差异摆到台面上。

检测是词法层面的启发式，不是 SQL 解析器。宁可漏报也尽量不误报 ——
误报会淹没真正需要人工判断的条目，让清单没人看。
"""
from __future__ import annotations

import dataclasses
import re
from typing import List, Tuple

CODE_IDENT_POSITION = "IDENT_POSITION"
CODE_MULTI_VALUE = "MULTI_VALUE"
CODE_NON_READONLY = "NON_READONLY"


@dataclasses.dataclass(frozen=True)
class Risk:
    code: str
    detail: str


_PLACEHOLDER = r"\{\{([A-Za-z_][A-Za-z0-9_]*)\}\}"
_PLACEHOLDER_RE = re.compile(_PLACEHOLDER)

# 表名位：FROM / JOIN / INTO / UPDATE 之后紧跟占位符
_AFTER_KEYWORD_RE = re.compile(
    r"\b(?:from|join|into|update)\s+" + _PLACEHOLDER, re.IGNORECASE
)

# ORDER BY / GROUP BY 列表：从关键字起，到下一个子句关键字或语句结束为止
_SORT_CLAUSE_RE = re.compile(
    r"\b(?:order|group)\s+by\b(?P<body>.*?)"
    r"(?=\b(?:order\s+by|group\s+by|having|limit|offset|fetch|for|union|"
    r"intersect|except)\b|;|$)",
    re.IGNORECASE | re.DOTALL,
)

# SELECT 列表：SELECT 之后到第一个子句关键字为止。没有 FROM 的
# `select 1 where x > {{n}}` 必须靠 WHERE 收住，否则会把取值位误判成列名位。
_SELECT_LIST_RE = re.compile(
    r"\bselect\b(?:\s+distinct)?(?P<body>.*?)"
    r"(?=\b(?:from|where|group\s+by|order\s+by|limit|offset)\b|;|$)",
    re.IGNORECASE | re.DOTALL,
)

# IN (...) 内的占位符
_IN_LIST_RE = re.compile(
    r"\bin\s*\((?P<body>[^()]*)\)", re.IGNORECASE | re.DOTALL
)

# 注释与前导空白，用于找出真正的首个关键字
_LEADING_NOISE_RE = re.compile(r"^(?:\s|--[^\n]*\n?|/\*.*?\*/)+", re.DOTALL)

_READ_ONLY_STARTERS = ("select", "with", "explain", "show", "values", "table")


def _leading_keyword(sql: str) -> str:
    stripped = _LEADING_NOISE_RE.sub("", sql).lstrip()
    match = re.match(r"[A-Za-z_]+", stripped)
    return match.group(0).lower() if match else ""


def _names_in(text: str) -> List[str]:
    return [m.group(1) for m in _PLACEHOLDER_RE.finditer(text)]


def assess(sql: str) -> Tuple[Risk, ...]:
    """返回该 SQL 的风险标注。无风险时返回空元组。"""
    risks: List[Risk] = []

    if _leading_keyword(sql) not in _READ_ONLY_STARTERS:
        risks.append(
            Risk(
                CODE_NON_READONLY,
                "语句不是只读查询，需单独审批后才能注册（执行器默认只读会话）",
            )
        )

    ident_names: List[str] = []
    for match in _AFTER_KEYWORD_RE.finditer(sql):
        ident_names.append(match.group(1))
    for match in _SORT_CLAUSE_RE.finditer(sql):
        ident_names.extend(_names_in(match.group("body")))
    for match in _SELECT_LIST_RE.finditer(sql):
        ident_names.extend(_names_in(match.group("body")))

    unique_idents = sorted(set(ident_names))
    if unique_idents:
        risks.append(
            Risk(
                CODE_IDENT_POSITION,
                "占位符 %s 位于表名/列名/排序位：类型校验对标识符位无效，"
                "该脚本在客户环境同样存在注入面"
                % ", ".join(unique_idents),
            )
        )

    multi_names: List[str] = []
    for match in _IN_LIST_RE.finditer(sql):
        multi_names.extend(_names_in(match.group("body")))
    unique_multi = sorted(set(multi_names))
    if unique_multi:
        risks.append(
            Risk(
                CODE_MULTI_VALUE,
                "占位符 %s 位于 IN (...) 内：多值展开语义客户中间件未定义，"
                "本实现按单值文本替换处理" % ", ".join(unique_multi),
            )
        )

    return tuple(risks)
