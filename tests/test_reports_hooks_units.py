"""四个技能的存档钩子:跑一次 main(取数被替换成假的),GSDB_REPORTS_DIR 下就该有 latest.json。

不测取数,不测渲染 —— 那些各有各的测试;这里只钉「输出之后确实存档了」和「格式对得上」。
"""
import json
import pathlib
import sys

import pytest

_ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT))
from common import reports  # noqa: E402,F401
from common.finding import Finding, Severity  # noqa: E402


def _load(scripts: str, *purge: str):
    """按 test_health_units.py 的做法:清掉同名模块缓存再把 scripts 目录压到 sys.path 头。"""
    for m in purge:
        sys.modules.pop(m, None)
    sys.path.insert(0, str(_ROOT / "skills" / scripts / "scripts"))


@pytest.fixture()
def rdir(monkeypatch, tmp_path):
    monkeypatch.setenv("GSDB_REPORTS_DIR", str(tmp_path))
    return tmp_path


_HEALTH_MODS = ("model", "thresholds", "util", "collectors", "report", "health", "aggregate", "render")
_WDR_MODS = ("model", "thresholds", "util", "collectors", "report", "native", "snaps",
             "interp", "recheck", "finalreport", "ansi", "wdr", "render")


def test_health_main_archives_json_shape(rdir, monkeypatch):
    _load("gaussdb-health", *_HEALTH_MODS)
    import health, model  # noqa: E402
    ev = model.HealthEvidence(conn="og", target="10.0.0.9/postgres", overall=Severity.WARN,
                              findings=[Finding("slowsql", "SLOW_AVG_MS", Severity.WARN, "avg_ms", "8412", ">1000", "x")])
    monkeypatch.setattr(health.access, "for_conn", lambda *a, **k: object())
    monkeypatch.setattr(health.aggregate, "collect_all", lambda *a, **k: [])
    monkeypatch.setattr(health, "run_health", lambda *a, **k: ev)
    monkeypatch.setattr(health.common.config, "resolved_name", lambda c: "og")
    assert health.main(["-c", "og", "--format", "json"]) == 0
    latest = json.loads((rdir / "health" / "latest.json").read_text(encoding="utf-8"))
    assert latest["overall"] == 2 and latest["findings"][0]["code"] == "SLOW_AVG_MS"
    assert "sub_skills" in latest and "dims" in latest
    idx = json.loads((rdir / "health" / "index.json").read_text(encoding="utf-8"))
    assert idx[-1]["overall"] == 2 and idx[-1]["counts"] == {"2": 1}


def test_health_markdown_output_still_archives_json(rdir, monkeypatch):
    _load("gaussdb-health", *_HEALTH_MODS)
    import health, model  # noqa: E402
    monkeypatch.setattr(health.access, "for_conn", lambda *a, **k: object())
    monkeypatch.setattr(health.aggregate, "collect_all", lambda *a, **k: [])
    monkeypatch.setattr(health, "run_health", lambda *a, **k: model.HealthEvidence(conn="og"))
    monkeypatch.setattr(health.common.config, "resolved_name", lambda c: "og")
    assert health.main(["-c", "og"]) == 0
    assert (rdir / "health" / "latest.json").is_file(), "markdown 输出时也要存 JSON,大盘只认 JSON"


def test_topsql_main_archives_with_by_in_name(rdir, monkeypatch):
    _load("gaussdb-topsql", "topsql", "render")
    import topsql  # noqa: E402
    rows = [topsql.StmtRow(sql_id="1a9f", query="select 1", calls=3, total_sec=1.5, avg_ms=500.0, rows=3)]
    monkeypatch.setattr(topsql.access, "for_conn", lambda *a, **k: object())
    monkeypatch.setattr(topsql, "top_sql", lambda runner, by, limit: rows)
    assert topsql.main(["-c", "og", "--by", "avg", "--format", "json"]) == 0
    files = sorted(p.name for p in (rdir / "topsql").glob("*.avg.json"))
    assert len(files) == 1, "文件名要带 by:%s" % list((rdir / "topsql").iterdir())
    latest = json.loads((rdir / "topsql" / "latest.json").read_text(encoding="utf-8"))
    assert latest["by"] == "avg" and latest["rows"][0]["sql_id"] == "1a9f"


def test_wdr_collect_archives_evidence(rdir, monkeypatch):
    _load("gaussdb-wdr", *_WDR_MODS)
    import wdr, model  # noqa: E402
    ev = model.Evidence(conn="og", target="t")
    ev.window.begin_id, ev.window.end_id = 1915, 1916
    monkeypatch.setattr(wdr.access, "for_conn", lambda *a, **k: object())
    monkeypatch.setattr(wdr, "collect_evidence", lambda runner, opt: ev)
    monkeypatch.setattr(wdr.common.config, "resolved_name", lambda c: "og")
    assert wdr.main(["collect", "-c", "og", "--begin", "1915", "--end", "1916", "--format", "json"]) == 0
    latest = json.loads((rdir / "wdr" / "latest.json").read_text(encoding="utf-8"))
    assert latest["window"]["begin_id"] == 1915


def test_wdr_native_html_defaults_into_reports_dir(rdir, monkeypatch):
    _load("gaussdb-wdr", *_WDR_MODS)
    import wdr, model  # noqa: E402
    seen = {}

    def fake_collect(runner, opt):
        seen["save_html"] = opt.save_html
        return model.Evidence(conn="og")

    monkeypatch.setattr(wdr.access, "for_conn", lambda *a, **k: object())
    monkeypatch.setattr(wdr, "collect_evidence", fake_collect)
    monkeypatch.setattr(wdr.common.config, "resolved_name", lambda c: "og")
    wdr.main(["collect", "-c", "og", "--begin", "1", "--end", "2", "--format", "json"])
    assert seen["save_html"].startswith(str(rdir / "wdr")) and seen["save_html"].endswith(".native.html")
    assert (rdir / "wdr").is_dir(), "目录要先建好,否则 native.py 落盘会失败并把失败写进 note"
    wdr.main(["collect", "-c", "og", "--begin", "1", "--end", "2", "--save-html", "/tmp/x.html", "--format", "json"])
    assert seen["save_html"] == "/tmp/x.html", "用户显式给的路径优先"


def test_wdr_native_path_empty_without_reports_dir(monkeypatch):
    monkeypatch.delenv("GSDB_REPORTS_DIR", raising=False)
    _load("gaussdb-wdr", *_WDR_MODS)
    import wdr  # noqa: E402
    assert wdr.default_native_path() == ""


def test_sqltune_archive_named_by_sql_id_and_skipped_without_it(rdir, monkeypatch):
    _load("gaussdb-sqltune", "sqltune")
    import sqltune  # noqa: E402
    sqltune.archive_tune({"sql_id": "1a9f", "x": 1})    # 有 sql_id → 存
    assert (rdir / "sqltune" / "1a9f.json").is_file()
    sqltune.archive_tune({"sql_id": "", "x": 2})        # 没有 → 不存,不报错
    assert not (rdir / "sqltune" / ".json").exists()
    assert len(list((rdir / "sqltune").glob("*.json"))) == 3   # 1a9f.json + latest.json + index.json
