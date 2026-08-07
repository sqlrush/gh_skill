"""SKILL.md 的结构闸。

这 14 个文件是**交付物**，而且被脚本批量改过两次（连接指引、安全红线）。
批量改的坏法很安静：正则少匹配一个文件、插错位置、把 YAML frontmatter 顶飞、
或者把 kbimport 管理的契约块切成两半 —— 都不会让任何测试变红，只会在客户
那边表现成「某个 skill 行为跟别的不一样」。
"""
import pathlib
import re

import pytest
import yaml

_ROOT = pathlib.Path(__file__).resolve().parents[1]
_SKILLS = sorted(_ROOT.glob("skills/*/SKILL.md"))


def _frontmatter(text: str) -> dict:
    """取 YAML frontmatter。允许开头有 BOM。"""
    body = text.lstrip("﻿")
    if not body.startswith("---"):
        return {}
    end = body.find("\n---", 3)
    if end < 0:
        return {}
    return yaml.safe_load(body[3:end]) or {}


def test_every_skill_dir_has_a_skill_md():
    dirs = {p.name for p in (_ROOT / "skills").iterdir() if p.is_dir()}
    have = {p.parent.name for p in _SKILLS}
    assert dirs == have, "缺 SKILL.md 的目录：%s" % (dirs - have)


@pytest.mark.parametrize("path", _SKILLS, ids=lambda p: p.parent.name)
def test_frontmatter_is_intact(path):
    """批量改文件最容易把 frontmatter 顶飞 —— 而 OpenCode 靠它发现 skill。

    顶飞之后 skill 直接从列表里消失，不报错。
    """
    fm = _frontmatter(path.read_text(encoding="utf-8"))
    assert fm, "%s 的 YAML frontmatter 解析不出来" % path.parent.name
    for key in ("name", "version", "description"):
        assert fm.get(key), "%s 缺 frontmatter.%s" % (path.parent.name, key)
    assert fm["name"] == path.parent.name, (
        "frontmatter.name (%s) 与目录名 (%s) 不一致 —— OpenCode 按 name 索引"
        % (fm["name"], path.parent.name))


@pytest.mark.parametrize("path", _SKILLS, ids=lambda p: p.parent.name)
def test_has_exactly_one_security_section(path):
    """两个「## 安全红线」的话，后一个多半是批量插入插重了，
    而读的人只会看到第一个。"""
    text = path.read_text(encoding="utf-8")
    assert text.count("\n## 安全红线") == 1, (
        "%s 的「## 安全红线」出现 %d 次"
        % (path.parent.name, text.count("\n## 安全红线")))


@pytest.mark.parametrize("path", _SKILLS, ids=lambda p: p.parent.name)
def test_plaintext_password_redline_present_once(path):
    """**每个 skill 都要有这一条，且只有一条。**

    它是 v4 的核心约束：配置里出现明文口令时加载会直接失败，模型得知道
    该提示用户怎么改，而不是把这个错当成配置坏了。
    """
    text = path.read_text(encoding="utf-8")
    n = text.count("配置文件里绝不允许出现明文口令")
    assert n == 1, "%s 出现 %d 次（应恰好 1 次）" % (path.parent.name, n)
    assert "credential_cli" in text, (
        "%s 的红线没告诉用户下一步跑什么" % path.parent.name)


@pytest.mark.parametrize("path", _SKILLS, ids=lambda p: p.parent.name)
def test_kb_contract_block_is_balanced(path):
    """契约块被切成两半的话，kbimport 下次刷新会写坏整个文件。"""
    text = path.read_text(encoding="utf-8")
    begins = text.count("KB-CONTRACT:BEGIN")
    ends = text.count("KB-CONTRACT:END")
    assert begins == ends, (
        "%s 的契约块 BEGIN=%d END=%d 不配平" % (path.parent.name, begins, ends))
    if begins:
        assert text.index("KB-CONTRACT:BEGIN") < text.index("KB-CONTRACT:END")


@pytest.mark.parametrize("path", _SKILLS, ids=lambda p: p.parent.name)
def test_no_leftover_edit_artifacts(path):
    """批量编辑残留：冲突标记、连续三个空行、重复的整段。"""
    text = path.read_text(encoding="utf-8")
    for marker in ("<<<<<<<", ">>>>>>>", "======="):
        assert marker not in text, "%s 残留冲突标记 %s" % (path.parent.name, marker)
    assert "\n\n\n\n" not in text, "%s 有连续空行，多半是插入时多带了换行" % path.parent.name


def test_data_skills_all_mention_login():
    """要连库的 skill 都得指向 gaussdb-login —— 漏掉的那个，模型会自己猜连接名。

    kbimport 不连库，login 自己不必自指。
    """
    exempt = {"gaussdb-kbimport", "gaussdb-login"}
    missing = [p.parent.name for p in _SKILLS
               if p.parent.name not in exempt
               and "gaussdb-login" not in p.read_text(encoding="utf-8")]
    assert not missing, "没有指向 gaussdb-login 的 skill：%s" % missing
