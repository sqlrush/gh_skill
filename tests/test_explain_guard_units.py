"""把用户 SQL 递进 EXPLAIN 模板之前的守卫。

白名单模型下，EXPLAIN 用户临时给的 SQL 只有一条路:注册一条
`EXPLAIN (...) {{sql}}` 模板，用户 SQL 落进那个参数位。而中间件是**文本替换**
不是绑定变量，所以参数位就是注入面 —— 守卫是第一道防线。

三道防线合起来才成立(缺一不可):
  1. 本模块:单语句 + 只读 校验
  2. 脚本标 readonly: true，在只读会话里执行 —— DML/DDL 被数据库本身挡掉
  3. 模板里 ANALYZE 写死，用户 SQL 不被执行(只读那条例外，见下)

**为什么不做 DML 的 EXPLAIN ANALYZE**:那要 `BEGIN; ...; ROLLBACK;` 多语句
模板 + 可写会话。实测载荷 `SELECT 1 AS n; COMMIT; CREATE TABLE x(i int); --`
用一个 `--` 注释掉末尾的 ROLLBACK，表真建出来了 —— 回滚包装拦不住，
而那条脚本必须可写，逃出来就是带写权限的任意 SQL 通道。
"""
import pathlib
import sys

import pytest

_ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT))

from common.grmp.statement import (  # noqa: E402
    ExplainNotAllowed,
    ensure_explainable,
)


# ===========================================================================
# 放行的
# ===========================================================================

@pytest.mark.parametrize("sql", [
    "SELECT 1",
    "SELECT count(*) FROM pg_class",
    "  select a from t where b = 'x;y'  ",          # 字符串里的分号不算分隔
    "SELECT 1;",                                     # 末尾单个分号
    "WITH c AS (SELECT 1) SELECT * FROM c",
    "-- 注释\nSELECT 1",
])
def test_single_read_only_statement_passes(sql):
    ensure_explainable(sql)      # 不抛就算过


# ===========================================================================
# 多语句 —— 注入的主要形态
# ===========================================================================

@pytest.mark.parametrize("sql", [
    "SELECT 1; DROP TABLE t",
    "SELECT 1; SELECT 2",                            # 即便两条都只读也拒
    "SELECT 1 AS n; COMMIT; CREATE TABLE x(i int); --",   # 实测逃逸成功的那条
])
def test_multiple_statements_are_rejected(sql):
    with pytest.raises(ExplainNotAllowed) as ei:
        ensure_explainable(sql)
    assert "多条语句" in str(ei.value)


# ===========================================================================
# 非只读
# ===========================================================================

@pytest.mark.parametrize("sql", [
    "UPDATE t SET a = 1",
    "DELETE FROM t",
    "INSERT INTO t VALUES (1)",
    "CREATE TABLE t(i int)",
    "DROP TABLE t",
    "TRUNCATE t",
])
def test_write_statements_are_rejected(sql):
    with pytest.raises(ExplainNotAllowed) as ei:
        ensure_explainable(sql)
    assert "只读" in str(ei.value)


def test_write_statement_still_rejected_when_analyze_requested():
    """DML 的 EXPLAIN ANALYZE 在白名单下不可用 —— 不是「降级成不 analyze」。

    静默降级会让用户以为拿到的是实际执行的计划,而实际是估算计划。
    实测过两者能差 2.3 倍(cost 1046000 vs 2448304)。
    """
    with pytest.raises(ExplainNotAllowed) as ei:
        ensure_explainable("UPDATE t SET a = 1", analyze=True)
    assert "只读" in str(ei.value)


def test_read_only_statement_allowed_with_analyze():
    """只读 SQL 的 EXPLAIN ANALYZE 可以 —— 真执行,但会话只读,写不了。"""
    ensure_explainable("SELECT count(*) FROM pg_class", analyze=True)


# ===========================================================================
# 空输入
# ===========================================================================

@pytest.mark.parametrize("sql", ["", "   ", "-- 只有注释\n", ";"])
def test_empty_input_is_rejected(sql):
    with pytest.raises(ExplainNotAllowed):
        ensure_explainable(sql)
