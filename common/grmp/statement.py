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


class ExplainNotAllowed(Exception):
    """这条 SQL 不能递进 EXPLAIN 模板。"""


def ensure_explainable(sql: str, analyze: bool = False) -> None:
    """把用户 SQL 递进 `EXPLAIN (...) {{sql}}` 模板前的守卫。

    白名单模型下，EXPLAIN 用户临时给的 SQL 只有这一条路:注册一条模板，
    用户 SQL 落进参数位。而中间件是**文本替换**不是绑定变量，参数位就是
    注入面 —— 本函数是第一道防线。

    三道防线缺一不可:
      1. 这里:单语句 + 只读
      2. 脚本标 readonly: true，只读会话里执行 —— DML/DDL 被数据库挡掉
      3. 模板里 ANALYZE 写死 —— 不 analyze 时用户 SQL 根本不被执行

    **为什么不放行 DML 的 EXPLAIN ANALYZE**:那要 `BEGIN; ...; ROLLBACK;`
    多语句模板加可写会话。实测载荷

        SELECT 1 AS n; COMMIT; CREATE TABLE x(i int); --

    用一个 `--` 注释掉模板末尾的 ROLLBACK，表真建出来了 —— 回滚包装拦不住。
    而那条脚本必须可写，逃出来就是带写权限的任意 SQL 通道。收益配不上代价。

    analyze 参数在这里只影响错误措辞:只读 SQL 两种都放行，非只读两种都拒。
    留着它是为了让调用方的意图出现在调用点上。
    """
    if not sql or not sql.strip():
        raise ExplainNotAllowed("SQL 为空。")

    statements = split_statements(sql)
    if not statements:
        raise ExplainNotAllowed("SQL 里没有可执行的语句（只有注释或空白）。")
    if len(statements) > 1:
        raise ExplainNotAllowed(
            "SQL 含多条语句（%d 条），不能递进 EXPLAIN 模板。\n"
            "模板是文本替换，多语句会整串拼进去 —— 实测能用一个 `--` "
            "注释掉模板尾部，绕过原本的限制。请一次只给一条语句。"
            % len(statements)
        )

    if not is_read_only(statements[0]):
        raise ExplainNotAllowed(
            "只受理只读语句，本次是 %s。\n"
            "EXPLAIN 一条写语句需要把它包在回滚事务里真执行，而回滚包装"
            "实测可被注释绕过，那条通道等于开放写权限。\n"
            "写语句的执行计划请走直连（driver: pg8000）。"
            % (leading_keyword(statements[0]).upper() or "非查询语句")
        )
