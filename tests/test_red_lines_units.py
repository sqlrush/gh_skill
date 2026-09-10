"""公共安全红线:一份正文(common/red_lines.md),17 个 SKILL.md 与 AGENTS.md 各带一份逐字相同的副本。

事故(客户 2026-09-10 截图):09-02 的重构把 14 个 SKILL.md 里的公共红线抽到了仓库根 AGENTS.md,而安装脚本不拷 AGENTS.md
——客户只换了 skill 目录,安全审查一 diff,红线整段「被删了」。银行客户要的是每个 skill 文件里看得见,不是仓库里去重。
所以改成:正文只有一份,由 tools/inject_red_lines.py 注入到每个 SKILL.md 的「## 安全红线」和 AGENTS.md 的标记块里;
这里的测试保证 18 份副本与正文逐字一致——改了正文忘了注入、或哪份被手工改坏,都在这里变红。
"""
import pathlib
import subprocess
import sys

import pytest

_ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT))

from tools import inject_red_lines as rl  # noqa: E402

_SKILLS = sorted((_ROOT / "skills").glob("gaussdb-*/SKILL.md"))
_HEADINGS = ("配置文件里绝不允许出现明文口令", "绝对沉默条款", "强制拒绝机制", "输出屏蔽规则", "通用替代策略", "只通过本技能脚本取数")


def test_canonical_text_has_the_six_clauses_the_customer_signed_off():
    text = rl.canonical_text()
    for h in _HEADINGS:
        assert h in text, "common/red_lines.md 缺「%s」" % h
    assert "{script}" in text and "{baseDir}" in text


def test_every_skill_has_its_own_script_named_in_the_last_clause():
    assert len(_SKILLS) == 17
    for path in _SKILLS:
        script = rl.main_script(path.parent)
        assert (path.parent / "scripts" / script).is_file(), "%s 的主脚本 %s 不存在" % (path.parent.name, script)


@pytest.mark.parametrize("path", _SKILLS, ids=lambda p: p.parent.name)
def test_skill_md_carries_the_block_verbatim(path):
    text = path.read_text(encoding="utf-8")
    assert text.count(rl.BEGIN) == 1 and text.count(rl.END) == 1, "%s 的红线块要恰好一对标记" % path.parent.name
    assert text.count("\n## 安全红线") == 1, "%s 的「## 安全红线」要恰好一节" % path.parent.name
    assert rl.extract_block(text) == rl.render(rl.main_script(path.parent)), (
        "%s 的红线块与 common/red_lines.md 不一致——改正文后跑 tools/inject_red_lines.py" % path.parent.name)
    assert text.index("## 安全红线") < text.index(rl.BEGIN)          # 块在这一节里


def test_agents_md_carries_the_same_block():
    text = (_ROOT / "AGENTS.md").read_text(encoding="utf-8")
    assert text.count(rl.BEGIN) == 1
    assert rl.extract_block(text) == rl.render(rl.AGENTS_SCRIPT)


def test_injector_is_idempotent_and_check_mode_is_clean():
    assert rl.check(_ROOT) == []
    proc = subprocess.run([sys.executable, str(_ROOT / "tools" / "inject_red_lines.py"), "--check", "--root", str(_ROOT)],
                          capture_output=True, text=True)
    assert proc.returncode == 0, proc.stdout + proc.stderr


def test_injector_updates_a_stale_copy(tmp_path):
    """正文改了、副本没跟上:--check 要点名,注入后一致。"""
    root = tmp_path
    (root / "common").mkdir()
    (root / "common" / "red_lines.md").write_text("- **A**:{script} {baseDir}\n", encoding="utf-8")
    sk = root / "skills" / "gaussdb-demo"
    (sk / "scripts").mkdir(parents=True)
    (sk / "scripts" / "demo.py").write_text("", encoding="utf-8")
    (sk / "SKILL.md").write_text("---\nname: gaussdb-demo\nversion: 1.0.0\n---\n\n# Demo\n\n## 安全红线\n\n- 本 skill 特有的一条\n", encoding="utf-8")
    (root / "AGENTS.md").write_text("# agent\n\n## 数据库连接规则\n\n1. x\n", encoding="utf-8")
    assert rl.check(root) != []
    rl.inject(root)
    assert rl.check(root) == []
    text = (sk / "SKILL.md").read_text(encoding="utf-8")
    assert "- **A**:demo.py {baseDir}" in text and "本 skill 特有的一条" in text
    assert text.index(rl.END) < text.index("本 skill 特有的一条")      # 特有条目留在块后面
    before = text
    rl.inject(root)
    assert (sk / "SKILL.md").read_text(encoding="utf-8") == before        # 幂等
    (root / "common" / "red_lines.md").write_text("- **B**:{script}\n", encoding="utf-8")
    assert rl.check(root) != []
