"""没有持久会话时的降级契约。

改动前是「拿不到会话就整条停」。但 7 处取数里 5 处本来就走 runner，
EXPLAIN 模板化之后是 6 处 —— 只有 hypopg 索引验证真的要会话。
什么都不给，比给一份「带真实执行计划、索引建议标注未验证」的分析差得多。

这里钉住降级的两条底线：
  1. 索引建议**必须**带未验证标注 —— 丢了标注，降级就变成了静默降级
  2. DML + --analyze **必须**报错，不能悄悄不 analyze —— 估算计划与实际计划
     实测能差 2.3 倍，用户会拿着估算计划当实际计划用
"""
import pathlib
import sys

import pytest

_ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT))


def _load(skill, mod):
    """按真实安装形态加载 skill 模块（同级模块要能互相 import）。"""
    import importlib.util

    path = _ROOT / "skills" / ("gaussdb-" + skill) / "scripts" / (mod + ".py")
    sys.path.insert(0, str(path.parent))
    sys.path.insert(0, str(_ROOT))
    spec = importlib.util.spec_from_file_location("%s_%s" % (skill, mod), path)
    m = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = m
    spec.loader.exec_module(m)
    return m


# ===========================================================================
# 未验证标注不能丢
# ===========================================================================

@pytest.mark.parametrize("skill", ["sqltune", "proctune"])
def test_no_hypopg_note_exists_and_says_unverified(skill):
    """两个 skill 都得有这条标注，且必须点明「未经验证」。

    措辞会变，但「未经验证」这四个字是给 DBA 看的核心信号，不能改没了。
    """
    m = _load(skill, skill)
    note = getattr(m, "_NO_HYPOPG_NOTE", None)
    assert note, "%s 缺 _NO_HYPOPG_NOTE —— 降级时索引建议会不带标注" % skill
    assert "未经验证" in note
    assert "人工验证" in note, "得告诉 DBA 下一步该做什么，不能只说坏消息"
    assert "pg8000" in note, "得给出拿到验证背书的具体办法"


@pytest.mark.parametrize("skill", ["sqltune", "proctune"])
def test_degrade_path_is_taken_when_session_missing(skill):
    """db is None 时走标注分支，而不是去调 hypopg。

    直接检查源码里的分支存在 —— 这条真跑起来要一整套连接，
    而它要防的退化（有人把 if db is None 删掉）静态就能看出来。
    """
    src = (_ROOT / "skills" / ("gaussdb-" + skill) / "scripts"
           / (skill + ".py")).read_text(encoding="utf-8")
    assert "if db is None:" in src
    assert "_NO_HYPOPG_NOTE" in src


# ===========================================================================
# DML + --analyze 必须硬失败
# ===========================================================================

def test_dml_with_analyze_is_rejected_not_downgraded():
    """不能悄悄变成「不 analyze」。

    静默降级会让用户以为拿到的是实际执行的计划，而实际是估算计划。
    实测两者能差 2.3 倍（cost 1046000 vs 2448304）。
    """
    from common.grmp.statement import ExplainNotAllowed, ensure_explainable

    with pytest.raises(ExplainNotAllowed) as ei:
        ensure_explainable("UPDATE t SET a = 1", analyze=True)
    assert "只读" in str(ei.value)


# ===========================================================================
# EXPLAIN 模板的形态 —— 防护写在模板里，改坏了就没了
# ===========================================================================

_REG = _ROOT / "scripts" / "registry"


@pytest.mark.parametrize("rel", [
    "explain/plan_text.yaml",
    "sqltune/plan_text.yaml",
    "proctune/plan_text.yaml",
    "explain/plan_text_analyze.yaml",
    "sqltune/plan_text_analyze.yaml",
    "proctune/plan_text_analyze.yaml",
    "sqltune/plan_json.yaml",
    "proctune/plan_json.yaml",
])
def test_explain_templates_are_read_only(rel):
    """必须标 readonly: true。

    这是三道防线里唯一由数据库强制的一道：只读会话让 DML/DDL 直接失败。
    去掉它，注入载荷就能穿透到写操作。
    """
    from common.grmp.script import load_script

    rec = load_script(_REG / rel)
    assert rec.readonly is True, "%s 不是只读脚本 —— 注入面失去数据库侧兜底" % rel


# ===========================================================================
# 模板的限制不能变成 skill 的限制 —— 实际发生过的回归
# ===========================================================================

def test_template_block_falls_back_to_a_session_instead_of_failing():
    """模板受理不了的 SQL，要回落到直连会话，不能直接失败。

    真实回归:我把注入守卫无条件加在所有路径上，把直连也一起拒了 ——
    `EXPLAIN UPDATE ...`（不带 --analyze，DML 根本不执行）改动前一直能出计划，
    改完变成退出 2。用新写的测试发现不了，因为新测试是照着新行为写的；
    是把旧版本取出来逐项对照才看到的。

    这条钉的是源码结构:模板走不通时必须还有一条回落路径。
    """
    src = (_ROOT / "skills" / "gaussdb-explain" / "scripts"
           / "explain.py").read_text(encoding="utf-8")
    assert "template_blocked" in src, "模板受阻的分支没了"
    assert "connection_for" in src, "没有回落到原始会话的路径"
    # 回落不能只在 analyze 时发生 —— 那正是当初判错的地方
    assert "needs_rollback" not in src, (
        "又把回落条件收窄成「只有 analyze 才回落」了 —— "
        "不带 analyze 的 DML 同样过不了只读模板，但直连能跑")


@pytest.mark.parametrize("rel", [
    "explain/plan_text.yaml",
    "sqltune/plan_text.yaml",
    "proctune/plan_text.yaml",
    "sqltune/plan_json.yaml",
    "proctune/plan_json.yaml",
])
def test_non_analyze_templates_never_execute_user_sql(rel):
    """不带 analyze 的模板里 ANALYZE 必须写死为 false 或干脆不出现。

    写成 true 或做成参数，用户 SQL 就会被真执行 —— 那是完全不同的风险等级。
    """
    from common.grmp.script import load_script

    sql = load_script(_REG / rel).script_content.upper()
    assert "ANALYZE TRUE" not in sql, "%s 会真执行用户 SQL" % rel
    assert "{{" not in sql.split("EXPLAIN", 1)[1].split(")", 1)[0], (
        "%s 的 EXPLAIN 选项里有占位符 —— 选项不能由调用方控制" % rel)
