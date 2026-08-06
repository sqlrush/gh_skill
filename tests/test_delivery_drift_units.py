"""交付物与 registry 的防漂移闸。

`docs/delivery/scripts*.sql` 是要发给客户、逐条灌进生产库 script_config 的
DML。它们由 registry 生成，但**生成之后就是两份独立的文件** —— 改了 registry
不重新生成，两边就悄悄分家：

  · 新增脚本没进 DML   → 客户环境缺脚本，表现成某个 skill 突然报「脚本不存在」
  · 改了 SQL 没进 DML  → 客户跑的还是旧 SQL，而本地测试全绿
  · 删了脚本没进 DML   → 客户库里留着没人维护的死 SQL

三种都不会让任何测试变红，这正是要在这里钉死的原因。

**这道闸是本轮改动带出来的：** 本轮给 sqltune 加了 stats_freshness / plan_json、
给 health 加了 stats_window，registry 从 87 条变成 90 条，而 scripts.sql 还停在
74 条 —— 全程没有任何东西报警。

不在测试里重新生成再比全文：DML 里每条 INSERT 都带 id，而 id 来自脚本库，
测试环境没有那个库。所以比的是**内容**（脚本名集合 + 每条 SQL 正文），
id 差异不算漂移。
"""
import pathlib
import re
import sys

import pytest

_ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT))

from common.grmp.script import load_script  # noqa: E402
from grmp_middleware import grmp_register  # noqa: E402

_REGISTRY = _ROOT / "scripts" / "registry"
_DELIVERY = _ROOT / "docs" / "delivery"

_REGEN = ("python3 -m grmp_middleware.grmp_register --registry scripts/registry \\\n"
          "    --db ~/.gdaa/grmp/script_config.db --replace %s \\\n"
          "    --dml-out docs/delivery/%s")


def _all_scripts():
    return {load_script(p).script_name: load_script(p)
            for p in sorted(_REGISTRY.glob("*/*.yaml"))}


def _shipped_names(text):
    """DML 头部清单里的脚本逻辑名。"""
    return set(re.findall(r"^--\s+([a-z_]+\.[a-z_0-9]+)\s+->\s+id=", text,
                          re.MULTILINE))


def _delivery_text(filename):
    path = _DELIVERY / filename
    if not path.exists():
        pytest.skip("%s 不存在" % filename)
    return path.read_text(encoding="utf-8")


# scripts.sql 剔掉无人调用的脚本，scripts-full.sql 全带
_ARTIFACTS = {"scripts.sql": True, "scripts-full.sql": False}


@pytest.mark.parametrize("filename,exclude_unused", sorted(_ARTIFACTS.items()))
def test_shipped_script_names_match_registry(filename, exclude_unused):
    text = _delivery_text(filename)
    have = _shipped_names(text)

    want = set(_all_scripts())
    if exclude_unused:
        want -= set(grmp_register.scripts_no_skill_calls(_REGISTRY))

    missing, extra = want - have, have - want
    assert not missing, (
        "%s 里缺了 %d 条脚本：%s\n改了 registry 之后没重新生成交付物 —— "
        "客户环境会缺这些脚本。重新生成：\n%s"
        % (filename, len(missing), "、".join(sorted(missing)),
           _REGEN % ("--exclude-unused" if exclude_unused else "--include-unused",
                     filename)))
    assert not extra, (
        "%s 里多了 %d 条 registry 里已经没有的脚本：%s\n"
        "它们会被灌进客户生产库却没人维护。"
        % (filename, len(extra), "、".join(sorted(extra))))


@pytest.mark.parametrize("filename,exclude_unused", sorted(_ARTIFACTS.items()))
def test_shipped_sql_bodies_match_registry(filename, exclude_unused):
    """名单对得上还不够 —— SQL 正文改了没重新生成，客户跑的是旧 SQL。

    这种漂移最难发现：名单一致、条数一致、报告一切正常，只有客户那边的
    行为跟测试环境不一样。
    """
    text = _delivery_text(filename)
    shipped = _shipped_names(text)
    drifted = []
    for name, script in sorted(_all_scripts().items()):
        if name not in shipped:
            continue
        # DML 里的字符串字面量把单引号写成两个
        body = script.script_content.strip().replace("'", "''")
        if body not in text:
            drifted.append(name)
    assert not drifted, (
        "%s 里这 %d 条脚本的 SQL 正文与 registry 不一致：%s\n"
        "名单和条数都对得上，只有正文变了 —— 客户跑的会是旧 SQL，"
        "而本地测试全绿。重新生成：\n%s"
        % (filename, len(drifted), "、".join(drifted),
           _REGEN % ("--exclude-unused" if exclude_unused else "--include-unused",
                     filename)))


def test_delivery_doc_states_the_current_count():
    """08-初始化白名单.md 里写的条数要跟实际 DML 对得上。

    那个数字是给客户做变更评审用的 —— 对不上时，评审按文档批了 74 条，
    实际灌进去 77 条，差额没有任何人看过。
    """
    doc = _DELIVERY / "08-初始化白名单.md"
    if not doc.exists():
        pytest.skip("08-初始化白名单.md 不存在")
    actual = len(re.findall(r"^INSERT INTO", _delivery_text("scripts.sql"),
                            re.MULTILINE))
    assert str(actual) in doc.read_text(encoding="utf-8"), (
        "文档里找不到实际条数 %d —— 交付 DML 变了，说明书没跟着改。"
        "客户会按文档里的旧数字做变更评审。" % actual)


def test_dry_run_refuses_to_pretend_it_exported():
    """`--dry-run --dml-out X` 原先静默什么都不做还返回 0。

    调用方以为交付 DML 重新生成了，其实文件没动 —— 本轮就被它骗过一次。
    """
    rc = grmp_register.main(["--registry", str(_REGISTRY), "--dry-run",
                             "--exclude-unused", "--dml-out", "/tmp/never.sql"])
    assert rc == 2, "--dry-run 配 --dml-out 必须报错，不能静默成功"
    assert not pathlib.Path("/tmp/never.sql").exists()
