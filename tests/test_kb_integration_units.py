"""health / sqltune 脚本层接入「客户知识库参照」——接入不可静默(无库)。

两条断言贯穿:① 知识库不存在时小节仍在,只是「未接入(原因)」;② 有命中时每条 finding 一段,
且小节位置正确(health:两段固定小节之后、维度正文之前;sqltune:证据之后、结论性小节之前)。
"""
import importlib
import pathlib
import sys
from types import SimpleNamespace

import pytest

_ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT))

from common.finding import Finding, Severity  # noqa: E402
from common.kb import query as kbquery  # noqa: E402


def _is_skill_module(mod) -> bool:
    file = getattr(mod, "__file__", "") or ""
    return "/skills/" in file and "/scripts/" in file


@pytest.fixture
def load(request):
    """按真实模块名从某个 skill 的 scripts/ 目录导入(它们互相 import 兄弟模块)。

    十几个 skill 都有 report.py / model.py / render.py:全套里谁先导入谁占名。这里把来自
    skills/*/scripts 的已加载模块暂时挪开、把目标目录钉到 sys.path[0];**测试结束原样放回**——
    上一版直接清掉,让后面 wdr 的懒加载找不到模块。
    """
    saved_modules = {n: m for n, m in sys.modules.items() if _is_skill_module(m)}
    saved_path = list(sys.path)

    def restore():
        for n in [n for n, m in sys.modules.items() if _is_skill_module(m)]:
            sys.modules.pop(n, None)
        sys.modules.update(saved_modules)
        sys.path[:] = saved_path
    request.addfinalizer(restore)

    def _load(scripts_dir: str, module: str):
        for n in list(saved_modules):
            sys.modules.pop(n, None)
        path = str(_ROOT / "skills" / scripts_dir / "scripts")
        while path in sys.path:
            sys.path.remove(path)
        sys.path.insert(0, path)
        return importlib.import_module(module)
    return _load


def _finding(code="VAC_FREQ"):
    return Finding(dimension="vacuum", code=code, severity=Severity.WARN, metric="autovacuum 次数/h",
                   value="37", threshold="20", evidence="cbst.cosp_asyn_task_dtl autovacuum 次数异常高")


def _canned(findings, kb_dir=None):
    items = tuple(kbquery.FindingRefs(key=getattr(f, "code", "x"), label=f"🟠 {getattr(f, 'code', 'x')}",
                                      query="q",
                                      cases=(kbquery.Ref(id="case:S2-20250224-CBST-偶现单条update慢", kind="case",
                                                         title="偶现单条update慢", score=0.9,
                                                         meta={"conclusion": "已确认", "occurred_at": "2025-02-24"},
                                                         sections={"处置": "调大 autovacuum_vacuum_threshold"}),))
                  for f in findings)
    return kbquery.QueryResult(status=kbquery.KbStatus(attached=True, version="2026.09",
                                                       counts={"docs.rule": 1, "docs.case": 1, "docs.raw": 0},
                                                       vector="未启用", graph="Neo4j 5 条已确认边"), items=items)


# ---------------------------------------------------------------- health

def test_health_report_has_kb_section_even_when_unattached(monkeypatch, load):
    monkeypatch.setenv("GSDB_KB_DIR", "/nonexistent/kb")
    report = load("gaussdb-health", "report")
    model = sys.modules["model"]
    ev = model.HealthEvidence(conn="og", dims=[], findings=[_finding()], overall=Severity.WARN)
    out = report.render_health(ev)
    assert "## 客户知识库参照" in out
    assert "> 知识库未接入(" in out and "不存在" in out
    # 位置:两段固定小节之后、Deterministic Findings 之前
    assert out.index("## 未纳入汇总的能力") < out.index("## 客户知识库参照") < out.index("## Deterministic Findings")
    import json
    d = json.loads(report.render_health_json(ev))
    assert d["kb_refs"]["status"]["attached"] is False


def test_health_report_renders_hits_per_finding(monkeypatch, load):
    report = load("gaussdb-health", "report")
    model = sys.modules["model"]
    monkeypatch.setattr(kbquery, "from_findings", _canned)
    ev = model.HealthEvidence(conn="og", dims=[], findings=[_finding("VAC_FREQ"), _finding("XACT_LONG")],
                              overall=Severity.WARN)
    out = report.render_health(ev)
    assert "### 对 🟠 VAC_FREQ" in out and "### 对 🟠 XACT_LONG" in out
    assert "**历史相似** S2-20250224-CBST-偶现单条update慢(结论强度:已确认,2025-02-24):处置 = 调大 autovacuum_vacuum_threshold" in out
    assert "> 知识库 v2026.09 · 条款 1 · 案例 1" in out


# ---------------------------------------------------------------- sqltune

def _tr():
    ev = SimpleNamespace(sql="SELECT * FROM cbst.cosp_asyn_task_dtl WHERE id = 1", version="", plan="",
                         tables=[SimpleNamespace(schema="cbst", name="cosp_asyn_task_dtl")],
                         findings=[SimpleNamespace(kind="seq_scan", severity="warn",
                                                   detail="Seq Scan on cosp_asyn_task_dtl", advice="")],
                         indexes=[], columns=[], gucs=[], analyzed=False)
    return SimpleNamespace(sql_id="", source="", schema="", original_sql=ev.sql, evidence=ev,
                           substitution=SimpleNamespace(sql=ev.sql, placeholders=0, substitutions=[]),
                           verified_indexes=[], index_verify_note="", derivation_report="")


def test_sqltune_kb_items_cover_sql_and_plan_findings(load):
    sqltune = load("gaussdb-sqltune", "sqltune")
    items = sqltune.kb_items(_tr())
    assert items[0].code == "SQL" and "cbst.cosp_asyn_task_dtl" in items[0].evidence
    assert items[1].code == "PLAN_SEQ_SCAN" and "Seq Scan" in items[1].evidence and items[1].severity == "warn"


def test_sqltune_report_places_kb_section_before_verified_indexes(monkeypatch, load):
    sqltune = load("gaussdb-sqltune", "sqltune")
    monkeypatch.setattr(sqltune, "evidence_report", lambda ev: "## Evidence\n\n")
    monkeypatch.setattr(kbquery, "from_findings", _canned)
    out = sqltune.sqltune_report(_tr())
    assert "## 客户知识库参照" in out
    assert out.index("## Evidence") < out.index("## 客户知识库参照") < out.index("## Verified Index Candidates")
    assert "### 对 🟠 SQL" in out and "### 对 🟠 PLAN_SEQ_SCAN" in out
    assert "kb_refs" in sqltune._to_jsonable(_tr())


def test_sqltune_report_unattached_is_explicit(monkeypatch, load):
    monkeypatch.setenv("GSDB_KB_DIR", "/nonexistent/kb")
    sqltune = load("gaussdb-sqltune", "sqltune")
    monkeypatch.setattr(sqltune, "evidence_report", lambda ev: "")
    out = sqltune.sqltune_report(_tr())
    assert "> 知识库未接入(" in out


# ---------------------------------------------------------------- lockwait

def test_lockwait_report_has_kb_section_even_when_empty(monkeypatch, load):
    """锁等待为空时也要有小节——空白会被读成「这项没查」。"""
    monkeypatch.setenv("GSDB_KB_DIR", "/nonexistent/kb")
    lockwait = load("gaussdb-lockwait", "lockwait")
    rep = lockwait.LockReport(pairs=[], edges=[], deadlocks=[], findings=[])
    out = lockwait.render_markdown(rep)
    assert "当前无锁等待" in out and "## 客户知识库参照" in out and "> 知识库未接入(" in out


def test_lockwait_report_renders_hits_before_detail(monkeypatch, load):
    lockwait = load("gaussdb-lockwait", "lockwait")
    monkeypatch.setattr(kbquery, "from_findings", _canned)
    pair = {"waiter_sessionid": 2, "holder_sessionid": 1, "waiter_mode": "ShareLock", "holder_mode": "ExclusiveLock",
            "locktype": "transactionid", "lock_object": "", "waiter_wait_s": 3.0, "holder_query": "UPDATE t",
            "waiter_pid": 20, "holder_pid": 10, "waiter_query": "UPDATE t", "holder_state": "idle in transaction",
            "holder_user": "u", "holder_app": "cbst-batch-adjust", "holder_xact_age_s": 40.0, "waiter_user": "u",
            "waiter_app": "cbst-online", "locktag": ""}
    f = Finding(dimension="locks", code="LOCK_ROOT_IDLE_XACT", severity=Severity.WARN, metric="根阻塞会话",
                value="1", threshold="0", evidence="idle in transaction 40s cbst-batch-adjust")
    rep = lockwait.LockReport(pairs=[pair], edges=[(2, 1)], deadlocks=[], findings=[f])
    out = lockwait.render_markdown(rep)
    assert "## 客户知识库参照" in out and "### 对 🟠 LOCK_ROOT_IDLE_XACT" in out
    assert out.index("## 客户知识库参照") < out.index("## 阻塞明细")
