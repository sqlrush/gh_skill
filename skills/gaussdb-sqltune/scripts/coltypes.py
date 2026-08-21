"""Catalog-driven placeholder typing for sqltune.

替换占位符前用一条已注册的 catalog 查询（sqltune.column_types）取出比较列
的真实类型，让合成值首发就类型正确。现场反复出现的
`invalid input syntax for integer: "test"` 就是列名启发式猜错整数列所致
（列名不在白名单，如 grp / uid / stock_quantity，兜底填了 'test'）。

走 runner（固定查询，注册脚本），中间件与直连两条路径通用；不依赖会话。
任何环节失败都降级回纯文本启发式并在 stderr 说一声——本模块绝不让
sqltune 因它而失败。
"""
from __future__ import annotations

import re
import sys

import evidence
import placeholder

COLUMN_TYPES_SCRIPT = "sqltune.column_types"

_NUMERIC_LITERAL_RE = re.compile(r"^[+-]?\d+(\.\d+)?$")
# openGauss/GaussDB: 老式 "for integer" 与新式 "for type numeric" 两种措辞都有。
_TYPE_ERR_RE = re.compile(r'invalid input syntax for (?:type )?[\w ]+:\s*"([^"]*)"')


def _quoted_list(names: list[str]) -> str:
    """'a','b' 形态的字面量列表；括号在注册脚本的 SQL 里（与 tables.yaml 同约定）。"""
    return ",".join("'" + n.replace("'", "''") + "'" for n in names)


def infer_types(runner, sql_text: str) -> list:
    """Per-placeholder column type (or None), aligned with substitute() order.

    列名取自各占位符的左上下文；同名列在多张表里类型冲突时保守放弃
    （返回 None，交回启发式）。查询失败同样全量降级。
    """
    contexts = placeholder.placeholder_contexts(sql_text)
    if not contexts:
        return []
    columns = [placeholder.comparison_column(c) for c in contexts]
    wanted = sorted({c for c in columns if c})
    tables = evidence.extract_tables(sql_text)
    if not wanted or not tables:
        return [None] * len(contexts)

    try:
        rows = runner.run(COLUMN_TYPES_SCRIPT,
                          {"tables": _quoted_list(tables),
                           "columns": _quoted_list(wanted)})
        mapping = _unambiguous((r["attname"], r["type_name"]) for r in rows)
    except Exception as exc:  # 探测是增强,不是硬依赖——失败就退回启发式
        print(f"warning: 列类型探测失败,占位符替换退回启发式: {exc}",
              file=sys.stderr)
        return [None] * len(contexts)
    return [mapping.get(c) if c else None for c in columns]


def _unambiguous(pairs) -> dict:
    """只保留类型无歧义的列;跨表同名冲突宁可不猜。"""
    seen: dict = {}
    for col, typ in pairs:
        seen.setdefault(col, set()).add(typ)
    return {col: next(iter(types)) for col, types in seen.items() if len(types) == 1}


def validate_binds(substitutions, types: list) -> None:
    """--bind 的值与推断类型明显不符时执行前拦截（治 bind 顺序错位）。

    只校验整数/数值族——字符串塞进整数列必炸且报错难定位；日期等格式
    多样，不硬卡。
    """
    problems = []
    for i, s in enumerate(substitutions):
        if s.source != "bind":
            continue
        t = types[i] if i < len(types) else None
        if t and placeholder.is_numeric_type(t) \
                and not _NUMERIC_LITERAL_RE.match(s.value.strip()):
            problems.append(
                f"  bind #{i + 1} = {s.value!r} 但该占位符对应 {t} 列"
                f"（上下文: …{s.context[-50:]}）")
    if problems:
        raise ValueError(
            "bind 值与占位符类型不符——检查 --bind 顺序是否错位:\n"
            + "\n".join(problems))


def enrich_type_error(message: str, substitutions):
    """EXPLAIN 报类型转换错时,点名坏值出自哪个占位符;不相关则返回 None。"""
    m = _TYPE_ERR_RE.search(message)
    if not m:
        return None
    bad = m.group(1)
    hits = [(i, s) for i, s in enumerate(substitutions)
            if s.value == bad or s.value.strip("'") == bad]
    if not hits:
        return None

    lines = [f"  #{i + 1} {s.token} -> {s.value} ({s.source})"
             f"  上下文: …{s.context[-50:]}" for i, s in hits]
    if all(s.source == "bind" for _, s in hits):
        hint = ("提示: 该值来自 --bind,疑似 bind 顺序错位——"
                "对照下列位置检查传值顺序:")
    else:
        hint = ("提示: 该值是 sqltune 自动填的合成值,列类型猜错了。"
                "可用 --bind 按占位符顺序传真实值绕过猜测。可能位置:")
    return message + "\n" + hint + "\n" + "\n".join(lines)
