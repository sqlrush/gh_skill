"""DB-free unit tests for the kbimport skill.

Written after a review found four defects that a mocked-DB suite would never
catch but plain unit tests would have: a contract injection that could delete
the body of a SKILL.md, a template backslash blowing up re.sub, a GBK file
crashing validate, and a .doc conversion timeout escaping as a traceback.
Each of those has a test here.
"""
import importlib.util
import pathlib
import subprocess
import sys

import pytest

_ROOT = pathlib.Path(__file__).resolve().parents[1]
_SCRIPT = _ROOT / "skills" / "gaussdb-kbimport" / "scripts" / "kbimport.py"

spec = importlib.util.spec_from_file_location("kbimport", _SCRIPT)
kb = importlib.util.module_from_spec(spec)
sys.modules["kbimport"] = kb
spec.loader.exec_module(kb)


def _template() -> str:
    return kb.load_contract_template()


def _skill_md(tmp_path, body: str) -> pathlib.Path:
    p = tmp_path / "SKILL.md"
    p.write_text(body, encoding="utf-8")
    return p


# --------------------------------------------------------------------------
# contract — the injection must never touch anything outside its markers
# --------------------------------------------------------------------------
_BODY = (
    "---\nname: demo\n---\n\n# Demo\n\n## 工作流\n\n1. 步骤 A\n2. 步骤 B\n\n"
    "## 安全红线\n\n- 绝不删库\n- 绝不改数据\n"
)


def test_apply_appends_block_and_keeps_the_body(tmp_path):
    md = _skill_md(tmp_path, _BODY)
    kb.apply_contract(md, _template())
    out = md.read_text(encoding="utf-8")
    assert "## 安全红线" in out and "绝不删库" in out
    assert kb.CONTRACT_BEGIN in out and kb.CONTRACT_END in out


def test_apply_is_idempotent(tmp_path):
    md = _skill_md(tmp_path, _BODY)
    kb.apply_contract(md, _template())
    once = md.read_text(encoding="utf-8")
    kb.apply_contract(md, _template())
    assert md.read_text(encoding="utf-8") == once
    assert once.count(kb.CONTRACT_BEGIN) == 1


def test_apply_refuses_a_broken_marker_instead_of_eating_the_body(tmp_path):
    """Regression: a BEGIN with no END made apply append a second block; the next
    apply then matched BEGIN..END *across the body* and replaced the lot — the
    '## 安全红线' section silently vanished. A half-written marker is a human
    problem: fail loudly, never guess."""
    md = _skill_md(tmp_path, _BODY.replace(
        "## 安全红线", kb.CONTRACT_BEGIN + " -->\n## 半截契约\n\n## 安全红线"))
    before = md.read_text(encoding="utf-8")

    with pytest.raises(kb.KbError) as exc:
        kb.apply_contract(md, _template())

    assert md.read_text(encoding="utf-8") == before, "拒绝写入时必须一字不改"
    assert "BEGIN" in str(exc.value) or "标记" in str(exc.value)


def test_apply_survives_a_backslash_in_the_template(tmp_path):
    """Regression: pattern.sub() parses backslash escapes in the replacement, so
    a regex in the template (`grep -E "\\d{3}"`) raised re.error. references/ is
    meant to be edited by users, and the template literally teaches grep."""
    md = _skill_md(tmp_path, _BODY)
    kb.apply_contract(md, _template())          # existing block -> update path
    tricky = (kb.CONTRACT_BEGIN + " -->\n## 契约\n\n"
              r'定位：grep -E "\d{3}" <kb>/rules' + "\n" + kb.CONTRACT_END + "\n")
    kb.apply_contract(md, tricky)
    assert r'\d{3}' in md.read_text(encoding="utf-8")


def test_contract_status_transitions(tmp_path):
    md = _skill_md(tmp_path, _BODY)
    assert kb.contract_status(md, _template()) == "missing"
    kb.apply_contract(md, _template())
    assert kb.contract_status(md, _template()) == "current"
    stale = kb.CONTRACT_BEGIN + " -->\n## 旧契约\n" + kb.CONTRACT_END + "\n"
    assert kb.contract_status(md, stale) == "stale"


def test_contract_status_flags_a_broken_marker(tmp_path):
    md = _skill_md(tmp_path, _BODY + "\n" + kb.CONTRACT_BEGIN + " -->\n## 半截\n")
    assert kb.contract_status(md, _template()) == "broken"


# --------------------------------------------------------------------------
# contract — scope: only skills that actually make normative judgements
# --------------------------------------------------------------------------
def test_only_governance_and_diagnostic_skills_get_the_contract():
    """gaussdb-slowsql/gaussdb-sqlfetch/gaussdb-explain just fetch rows — telling them to consult a
    standards KB before answering is noise. The judging skills get it."""
    for want in ("gaussdb-sqlreview", "gaussdb-health", "gaussdb-wdr", "gaussdb-memanalyze", "gaussdb-sqltune", "gaussdb-proctune"):
        assert want in kb.CONTRACT_SKILLS, f"{want} 应当注入契约"
    for skip in ("gaussdb-slowsql", "gaussdb-topsql", "gaussdb-sqlfetch", "gaussdb-explain", "gaussdb-topproc", "gaussdb-procinfo"):
        assert skip not in kb.CONTRACT_SKILLS, f"{skip} 是纯取数 skill,不应注入"
    assert "gaussdb-kbimport" not in kb.CONTRACT_SKILLS       # 管理者不是消费者


def test_contract_targets_are_filtered_by_the_whitelist(tmp_path):
    for name in ("gaussdb-sqlreview", "gaussdb-slowsql", "gaussdb-kbimport"):
        d = tmp_path / name
        d.mkdir()
        (d / "SKILL.md").write_text(_BODY, encoding="utf-8")
    picked = [p.parent.name for p in kb.contract_targets(tmp_path)]
    assert picked == ["gaussdb-sqlreview"]


# --------------------------------------------------------------------------
# contract — the precedence the block states must match the agreed governance
# --------------------------------------------------------------------------
def test_contract_puts_the_skill_above_the_kb():
    """Agreed boundary: a skill's own SKILL.md and its script's deterministic
    verdict outrank the KB; the KB outranks the model's own knowledge. The KB
    governs *what the standards say*, not *how a skill works*."""
    text = _template()
    assert "SKILL.md" in text, "契约必须点明 skill 自身策略的位置"
    # 不能再出现「一律以知识库为准」这种压过 skill 判定的措辞
    assert "一律以知识库为准" not in text
    assert "自带知识" in text                     # KB 仍高于模型自带知识


# --------------------------------------------------------------------------
# encoding — a Chinese spec is very likely GBK
# --------------------------------------------------------------------------
def _kb_dir(tmp_path) -> pathlib.Path:
    d = tmp_path / "kb"
    kb.ensure_kb_skeleton(d)
    return d


def test_validate_tolerates_a_gbk_guide(tmp_path):
    """Regression: read_text(encoding='utf-8') raised UnicodeDecodeError and the
    whole validate died. ingest already handles gb18030 — the two halves of the
    same tool disagreed about encoding."""
    d = _kb_dir(tmp_path)
    (d / "guides" / "gbk.md").write_bytes(
        "---\nid: G-1\ndescription: 中文指南\n---\n正文\n".encode("gb18030"))
    findings = []
    kb.validate_guides(d, findings)              # 不得抛异常
    assert not [m for lvl, m in findings if lvl == "error"]


def test_search_tolerates_a_gbk_file(tmp_path, capsys):
    d = _kb_dir(tmp_path)
    (d / "errata" / "e.md").write_bytes("索引必须以 idx_ 开头\n".encode("gb18030"))
    args = type("A", (), {"kb": str(d), "keyword": "idx_"})()
    assert kb.cmd_search(args) == 0
    assert "idx_" in capsys.readouterr().out


# --------------------------------------------------------------------------
# .doc conversion
# --------------------------------------------------------------------------
def test_doc_conversion_timeout_becomes_a_kberror(monkeypatch, tmp_path):
    """A 120s hang must reach the user as '请先另存为 .txt/.docx', not a traceback."""
    monkeypatch.setattr(kb.shutil, "which", lambda _c: "/usr/bin/fake")

    def _hang(*_a, **_k):
        raise subprocess.TimeoutExpired(cmd="textutil", timeout=120)

    monkeypatch.setattr(kb.subprocess, "run", _hang)
    with pytest.raises(kb.KbError):
        kb.extract_doc(tmp_path / "x.doc")


# --------------------------------------------------------------------------
# validate — rule schema
# --------------------------------------------------------------------------
def _write_rules(d: pathlib.Path, entries) -> None:
    import yaml
    (d / "rules" / "t.yaml").write_text(
        yaml.safe_dump(entries, allow_unicode=True), encoding="utf-8")


_GOOD = {"id": "GS-IDX-001", "severity": "error", "check": "deterministic",
         "rule": "索引名必须以 idx_ 开头", "source": "第 3 章"}


def test_validate_accepts_a_well_formed_rule(tmp_path):
    d = _kb_dir(tmp_path)
    _write_rules(d, [_GOOD])
    findings = []
    kb.validate_rules(d, findings)
    assert [m for lvl, m in findings if lvl == "error"] == []


@pytest.mark.parametrize("mutation, needle", [
    ({"id": "IDX-1"}, "格式"),
    ({"severity": "fatal"}, "severity"),
    ({"check": "magic"}, "check"),
    ({"rule": ""}, "必填"),
])
def test_validate_rejects_bad_rules(tmp_path, mutation, needle):
    d = _kb_dir(tmp_path)
    _write_rules(d, [dict(_GOOD, **mutation)])
    findings = []
    kb.validate_rules(d, findings)
    errors = [m for lvl, m in findings if lvl == "error"]
    assert any(needle in m for m in errors), errors


def test_validate_rejects_duplicate_rule_ids(tmp_path):
    d = _kb_dir(tmp_path)
    _write_rules(d, [_GOOD, dict(_GOOD, rule="另一条")])
    findings = []
    kb.validate_rules(d, findings)
    assert any("重复" in m for lvl, m in findings if lvl == "error")


# --------------------------------------------------------------------------
# pure helpers
# --------------------------------------------------------------------------
def test_split_frontmatter():
    meta, err = kb.split_frontmatter("---\nid: G1\n---\n正文\n")
    assert err is None and meta["id"] == "G1"
    _, err = kb.split_frontmatter("---\nid: G1\n正文\n")
    assert err is not None                       # 起始 --- 没有结束 ---
    assert kb.split_frontmatter("# 无 frontmatter\n") == ({}, None)


def test_slugify_never_escapes_the_kb_directory():
    assert kb.slugify("../../etc/passwd") == "etc-passwd"
    assert kb.slugify("客户规范 v2.1") == "客户规范-v2-1"
    assert kb.slugify("!!!") == "imported"


def test_detect_outline_picks_chinese_headings():
    text = "第一章 总则\n正文一段\n1.2 索引规范\n" + "x" * 80 + "\n"
    titles = [t for _, t in kb.detect_outline(text)]
    assert "第一章 总则" in titles
    assert "1.2 索引规范" in titles
    assert not any(len(t) > kb.MAX_HEADING_LEN for t in titles)


# --------------------------------------------------------------------------
# end-to-end (still DB-free)
# --------------------------------------------------------------------------
def test_ingest_index_validate_round_trip(tmp_path):
    src = tmp_path / "spec.md"
    src.write_text("# 规范\n\n## 1. 索引\n\n索引名必须以 idx_ 开头。\n", encoding="utf-8")
    d = tmp_path / "kb"

    def run(*argv):
        return subprocess.run([sys.executable, str(_SCRIPT), *argv, "--kb", str(d)],
                              capture_output=True, text=True)

    assert run("ingest", str(src)).returncode == 0
    assert (d / "inbox" / "spec" / "source.md").is_file()
    assert (d / "sources" / "spec.md").is_file()      # 原文快照可追溯
    assert run("index").returncode == 0
    assert (d / "INDEX.md").is_file()
    assert run("validate").returncode == 0            # 只有 inbox 未清空的 warn


# --------------------------------------------------------------------------
# governance — the boundary must hold in the SKILL.md files themselves
# --------------------------------------------------------------------------
def test_sqlreview_scopes_its_single_source_claim():
    """The injected contract used to contradict sqlreview head-on: line 15 said
    rules.yaml was 「规范的唯一来源」 while the contract said the KB was
    「唯一权威来源」, and a third rule forbade reporting anything the script did
    not. Whichever way the wording drifts, the two must stay reconcilable."""
    md = (_ROOT / "skills" / "gaussdb-sqlreview" / "SKILL.md").read_text(encoding="utf-8")
    assert "规范的唯一来源" not in md, "无限定的「规范的唯一来源」会与知识库契约打架"
    assert "确定性判定规则**的唯一来源" in md or "确定性判定规则" in md
    assert "不得凭知识库补报" in md          # 判定边界写死在 skill 里


def test_every_contracted_skill_carries_exactly_one_block():
    for name in sorted(kb.CONTRACT_SKILLS):
        md = _ROOT / "skills" / name / "SKILL.md"
        if not md.is_file():
            continue
        text = md.read_text(encoding="utf-8")
        assert text.count(kb.CONTRACT_BEGIN) == 1, f"{name}: 契约块数量异常"
        assert text.count(kb.CONTRACT_END) == 1, f"{name}: 契约块数量异常"


def test_fetch_only_skills_stay_clean():
    for name in ("gaussdb-slowsql", "gaussdb-topsql", "gaussdb-sqlfetch", "gaussdb-explain", "gaussdb-topproc", "gaussdb-procinfo"):
        md = _ROOT / "skills" / name / "SKILL.md"
        assert kb.CONTRACT_BEGIN not in md.read_text(encoding="utf-8"), \
            f"{name} 是纯取数 skill,不该被注入契约"


# --------------------------------------------------------------------------
# KB 落点：与 skill 装在一起（skills/ 的同级目录，不是 skill 目录内部）
#
# 不能放 skills/<name>/kb —— install-opencode.sh 每次重装 `rm -rf` 整个 skill
# 目录，客户导入的知识库会被删光。同级目录 install 从不触碰(已实测)。
# --------------------------------------------------------------------------
import os


@pytest.mark.parametrize("script, want_root", [
    ("/Users/x/.config/opencode/skills/gaussdb-kbimport/scripts/kbimport.py",
     "/Users/x/.config/opencode"),                      # 全局安装
    ("/Users/x/proj/.opencode/skills/gaussdb-kbimport/scripts/kbimport.py",
     "/Users/x/proj/.opencode"),                        # 项目安装
    ("/Users/x/opencode_skill/skills/gaussdb-kbimport/scripts/kbimport.py",
     "/Users/x/opencode_skill"),                        # 源码仓直跑
])
def test_install_root_is_derived_from_the_script_location(script, want_root):
    assert kb.install_root(pathlib.Path(script)) == pathlib.Path(want_root)


def test_kb_defaults_to_the_install_root(monkeypatch):
    monkeypatch.delenv("GSDB_KB_DIR", raising=False)
    monkeypatch.delenv("GSDB_HOME", raising=False)
    monkeypatch.delenv("GDAA_HOME", raising=False)
    assert kb.resolve_kb_dir(None) == kb.install_root() / "kb"


def test_kb_dir_env_var_overrides_the_default(monkeypatch, tmp_path):
    monkeypatch.setenv("GSDB_KB_DIR", str(tmp_path / "custom"))
    assert kb.resolve_kb_dir(None) == tmp_path / "custom"


def test_explicit_kb_flag_beats_everything(monkeypatch, tmp_path):
    monkeypatch.setenv("GSDB_KB_DIR", str(tmp_path / "env"))
    assert kb.resolve_kb_dir(str(tmp_path / "cli")) == tmp_path / "cli"


def test_kb_never_lands_inside_a_skill_directory(monkeypatch):
    """The whole point: a reinstall must not be able to delete the KB."""
    monkeypatch.delenv("GSDB_KB_DIR", raising=False)
    parts = kb.resolve_kb_dir(None).parts
    assert "skills" not in parts, "知识库落在 skills/ 内 —— 重装会被 rm -rf 删掉"


def test_contract_points_skills_at_the_new_kb_location():
    text = _template()
    assert "~/.gdaa/kb" not in text, "契约块仍指向旧路径"
    assert "kb" in text


def test_contract_uses_a_placeholder_the_installer_substitutes():
    """{kbDir} 必须被 install-opencode.sh 替换,否则装完 SKILL.md 里会留下字面量。"""
    assert "{kbDir}" in _template()
    installer = (_ROOT / "install-opencode.sh").read_text(encoding="utf-8")
    assert "{kbDir}" in installer, "安装脚本没有替换 {kbDir}"


# --------------------------------------------------------------------------
# 编码：非 UTF-8 文件会被各 skill 的 grep 静默漏掉
#
# 消费者 skill 走的是契约块里的 `grep -rn "<关键词>" <kb>/...`。grep 拿 LLM 敲的
# UTF-8 字节去比对一个 GB18030 文件,字节层面对不上——**不报错,就是"没找到"**。
# 于是客户明明把规范写进了库里,模型却回「知识库未覆盖,以下为通用经验」,
# 正是本 skill 要防的那种谎话。validate 必须在导入时就把它拦下来。
# --------------------------------------------------------------------------
_GBK_RULE = "- id: GS-IDX-001\n  severity: warn\n  check: deterministic\n  rule: 索引命名\n"
_GBK_MD = "---\nid: G-1\ndescription: 中文指南\n---\n索引命名必须遵循前缀规范\n"


@pytest.mark.parametrize("sub, name, body", [
    ("guides", "gbk.md", _GBK_MD),
    ("errata", "gbk.md", "索引命名的例外情况\n"),
    ("rules", "gbk.yaml", _GBK_RULE),
])
def test_validate_rejects_non_utf8_files_in_searchable_dirs(tmp_path, sub, name, body):
    d = _kb_dir(tmp_path)
    (d / sub / name).write_bytes(body.encode("gb18030"))
    findings = []
    kb.validate_encoding(d, findings)
    errors = [m for lvl, m in findings if lvl == "error"]
    assert any(name in m for m in errors), f"{sub}/{name} 未被报为编码错误：{findings}"
    assert any("UTF-8" in m for m in errors)
    assert any("grep" in m for m in errors), "报错必须说清后果：grep 会静默漏掉它"


def test_validate_accepts_utf8_files(tmp_path):
    d = _kb_dir(tmp_path)
    (d / "guides" / "ok.md").write_text(_GBK_MD, encoding="utf-8")
    (d / "rules" / "ok.yaml").write_text(_GBK_RULE, encoding="utf-8")
    findings = []
    kb.validate_encoding(d, findings)
    assert findings == []


def test_original_snapshots_keep_their_encoding(tmp_path):
    """sources/ 是原文快照,本来就该保留客户给的编码;检索也不扫它,不该报错。"""
    d = _kb_dir(tmp_path)
    (d / "sources" / "客户规范.txt").write_bytes("索引命名".encode("gb18030"))
    findings = []
    kb.validate_encoding(d, findings)
    assert findings == []


def test_cmd_validate_wires_in_the_encoding_check(tmp_path):
    """端到端:validate 子命令必须真的跑这一项,退出码为 2。"""
    d = _kb_dir(tmp_path)
    (d / "guides" / "gbk.md").write_bytes(_GBK_MD.encode("gb18030"))
    (d / "INDEX.md").write_text("# idx\n- `guides/gbk.md` — x\n", encoding="utf-8")
    args = type("A", (), {"kb": str(d)})()
    assert kb.cmd_validate(args) == 2


def test_encoding_error_suggests_a_command_that_actually_runs(tmp_path):
    """回归:原先建议 `iconv ... -o file` —— macOS/BSD 的 iconv 没有 -o,
    而且原地覆写会先把文件截断成空。跑不通的修复建议比不给更糟。"""
    d = _kb_dir(tmp_path)
    (d / "guides" / "gbk.md").write_bytes(_GBK_MD.encode("gb18030"))
    findings = []
    kb.validate_encoding(d, findings)
    msg = findings[0][1]
    assert " -o " not in msg, "iconv -o 在 macOS/BSD 上不存在"
    assert ">" in msg and "mv" in msg, "应给出可移植的重定向 + mv 写法"
