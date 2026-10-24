"""增量导入包生成器(tools/delivery_delta.py)。

**这个工具存在的原因:** 2026-10-18 第一次出增量包时清单是手写的,漏了 4 条正文变更 ——
其中 wdr.file_io 修的是物理读少报 62%、写得最多的那个文件在报告里完全不存在。
下面钉住三件事:清单是**算**出来的、缺说明会**报错**、输出是**客户真实形态**。
"""
from __future__ import annotations

import pathlib
import sys

import pytest

_ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT / "tools"))
sys.path.insert(0, str(_ROOT))

import delivery_delta as DD  # noqa: E402

_NOTES = {"added": {"x.new": ["标题", "pg_x", "用途"]},
          "changed": {"x.old": "改了什么"}}


def _ins(name, body):
    return ("INSERT INTO grmp.script_config (id, script_type, script_name) VALUES "
            "(grmp.script_config_seq.nextval, 'SQL', '%s', 'appbusiness', 1, 'ALL', "
            "NULL, NULL, NULL, 'centralization', '%s', '[]', 'AGENT');\n"
            % (name, body.replace("'", "''")))


def test_bodies_unescape_doubled_quotes():
    """DML 里的 '' 要还原成 ' —— 不还原的话每条带字符串常量的脚本都会被判成「正文变了」。"""
    out = DD.bodies(_ins("a.b", "SELECT 'x' FROM t"))
    assert out == {"a.b": "SELECT 'x' FROM t"}


def test_bodies_parse_every_statement():
    text = _ins("a.one", "SELECT 1") + _ins("a.two", "SELECT 2")
    assert set(DD.bodies(text)) == {"a.one", "a.two"}


def _ins_p(name, body, params):
    return ("INSERT INTO grmp.script_config (id, script_type, script_name) VALUES "
            "(grmp.script_config_seq.nextval, 'SQL', '%s', 'appbusiness', 1, 'ALL', "
            "NULL, NULL, NULL, 'centralization', '%s', '%s', 'AGENT');\n"
            % (name, body.replace("'", "''"), params))


class _Rec:
    def __init__(self, body, params):
        self.script_content, self.parameter_config = body, params


def test_a_parameter_change_is_a_change_and_the_update_carries_it(monkeypatch):
    """2026-10-24:health.stale_stats 加了参数 min_pages,第一版增量包只 UPDATE 了正文 ——
    客户的中间件不认新参数,导进去健康检查一调就被拒。参数定义变了也算变更,UPDATE 要一并带上。"""
    old_p = '[{"key":"limit","value":"","type":"INTEGER","autoAcquire":false}]'
    new_p = ('[{"key":"limit","value":"","type":"INTEGER","autoAcquire":false},'
             '{"key":"min_pages","value":"","type":"INTEGER","autoAcquire":false}]')
    base = _ins_p("x.p", "SELECT {{limit}}", old_p) + _ins_p("x.same", "SELECT 1", "[]")
    reg = {"x.p": _Rec("SELECT {{limit}}", new_p), "x.same": _Rec("SELECT 1", "[]")}
    monkeypatch.setattr(DD, "_base_dml", lambda ref: base)
    monkeypatch.setattr(DD, "registry", lambda: reg)
    assert DD.params_changed("b", ["x.p", "x.same"]) == {"x.p"}
    out = DD.render([], ["x.p"], {"changed": {"x.p": "加参数"}}, 2, 2, "d", {"x.p"})
    assert "parameter_config = '%s'" % new_p in out and "WHERE script_name = 'x.p';" in out


def test_the_self_check_is_computed_for_this_package(monkeypatch):
    """自检按这次的包现算:每条更新后正文的 md5(以前写死的是 10-18 那一包的特征串)。"""
    import hashlib
    reg = {"x.a": _Rec("SELECT 'a'", "[]")}
    monkeypatch.setattr(DD, "registry", lambda: reg)
    out = DD.render([], ["x.a"], {"changed": {"x.a": "改"}}, 1, 1, "d")
    want = hashlib.md5("SELECT 'a'\n".encode("utf-8")).hexdigest()
    assert "('x.a', '%s')" % want in out and "md5(c.script_content) <> v.sum" in out
    assert "plan_text_analyze" not in out and "wdr.file_io" not in out


def test_a_missing_note_refuses_to_produce_a_package(monkeypatch, tmp_path, capsys):
    """**本组最重要的一条。** 某条有变更却没写说明 → 直接报错退出,不许出一个缺条目的包。"""
    monkeypatch.setattr(DD, "diff", lambda base: (["x.new"], ["x.old", "x.undocumented"], [], 1, 2))
    notes = tmp_path / "n.yaml"
    import yaml
    notes.write_text(yaml.safe_dump(_NOTES, allow_unicode=True), encoding="utf-8")
    monkeypatch.setattr(DD, "NOTES", notes)
    rc = DD.main(["--out", str(tmp_path / "o.sql"), "--date", "d"])
    assert rc == 2
    assert "x.undocumented" in capsys.readouterr().err
    assert not (tmp_path / "o.sql").exists(), "报错时不该留下一个半成品文件"


def test_removed_scripts_are_warned_about_not_silently_ignored(monkeypatch, tmp_path, capsys):
    """客户那版里有、现在没了 —— 不自动生成 DELETE(有风险),但必须让人看见。"""
    monkeypatch.setattr(DD, "diff", lambda base: ([], [], ["x.gone"], 1, 0))
    monkeypatch.setattr(DD, "render", lambda *a, **k: "")
    notes = tmp_path / "n.yaml"
    notes.write_text("added: {}\nchanged: {}\n", encoding="utf-8")
    monkeypatch.setattr(DD, "NOTES", notes)
    assert DD.main(["--out", str(tmp_path / "o.sql"), "--date", "d"]) == 0
    assert "x.gone" in capsys.readouterr().err


@pytest.mark.parametrize("p", sorted((_ROOT / "docs" / "delivery").glob("scripts-delta-*.sql")),
                         ids=lambda p: p.name)
def test_the_real_package_matches_the_customer_shape(p):
    """仓库里每一份真实的增量包都必须是客户形态 —— 生成器形态客户导不进去。"""
    text = p.read_text(encoding="utf-8")
    n_ins = text.count("INSERT INTO grmp.script_config (")
    assert "grmp.grmp.script_config" not in text
    # 只数 VALUES 里的 —— 文件头注释里也提到了 seq.nextval(「id 用 … 不硬编码」),
    # 直接数全文会多数一个(第一版就这么错了)。
    assert text.count("VALUES (grmp.script_config_seq.nextval,") == n_ins
    assert text.count("uuid());") == n_ins
    assert "VALUES ('" not in text, "有硬编码的 id"


def test_the_real_package_is_complete_against_its_baseline():
    """**直接回答「这次有没有漏」。** 用工具重新算一遍,和仓库里那份包逐条比 ——
    手写清单漏掉的那 4 条,这条测试会抓住。"""
    try:
        added, changed, _gone, _nb, _nn = DD.diff("origin/main")
    except Exception as exc:          # 没有远端引用(浅克隆等)时跳过,不误报
        pytest.skip("取不到 origin/main:%s" % exc)
    # 相对 main 的增量可能分几次出包(10-18 一包、10-22 又补 5 条):合起来要齐
    text = "".join(p.read_text(encoding="utf-8")
                   for p in sorted((_ROOT / "docs" / "delivery").glob("scripts-delta-*.sql")))
    for n in added:
        assert "'%s', 'appbusiness'" % n in text, "增量包里缺新增:%s" % n
    for n in changed:
        assert "WHERE script_name = '%s';" % n in text, "增量包里缺更新:%s" % n
