"""common.reports —— 报告存档:目录未设即不存档;原子写;latest/index;保留 N 份;失败只 warn。"""
import json
import pathlib
import sys

import pytest

_ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT))

from common import reports  # noqa: E402


def test_unset_env_means_no_archive(monkeypatch, tmp_path):
    monkeypatch.delenv("GSDB_REPORTS_DIR", raising=False)
    assert reports.reports_dir() is None
    assert reports.archive("health", {"overall": 0}) is None
    assert not list(tmp_path.iterdir())


def test_archive_writes_file_latest_and_index(monkeypatch, tmp_path):
    monkeypatch.setenv("GSDB_REPORTS_DIR", str(tmp_path))
    p = reports.archive("health", {"overall": 2, "findings": [{"severity": 2}, {"severity": 1}]},
                        now="20260922T060312Z")
    assert p == tmp_path / "health" / "20260922T060312Z.json"
    assert json.loads(p.read_text(encoding="utf-8"))["overall"] == 2
    latest = json.loads((tmp_path / "health" / "latest.json").read_text(encoding="utf-8"))
    assert latest["overall"] == 2
    idx = json.loads((tmp_path / "health" / "index.json").read_text(encoding="utf-8"))
    assert idx == [{"at": "20260922T060312Z", "file": "20260922T060312Z.json",
                    "overall": 2, "counts": {"1": 1, "2": 1}}]


def test_explicit_name_is_used_and_index_dedupes_by_file(monkeypatch, tmp_path):
    monkeypatch.setenv("GSDB_REPORTS_DIR", str(tmp_path))
    reports.archive("sqltune", {"a": 1}, name="1a9f", now="20260922T000000Z")
    reports.archive("sqltune", {"a": 2}, name="1a9f", now="20260922T000100Z")
    idx = json.loads((tmp_path / "sqltune" / "index.json").read_text(encoding="utf-8"))
    assert [e["file"] for e in idx] == ["1a9f.json"], "同名覆盖时 index 不该出现两条"
    assert json.loads((tmp_path / "sqltune" / "1a9f.json").read_text())["a"] == 2


def test_keep_prunes_oldest_only_within_skill_dir(monkeypatch, tmp_path):
    monkeypatch.setenv("GSDB_REPORTS_DIR", str(tmp_path))
    (tmp_path / "health").mkdir()
    (tmp_path / "health" / "stranger.json").write_text("{}", encoding="utf-8")   # 不在 index 里,不能被删
    for i in range(4):
        reports.archive("health", {"overall": 0}, keep=3, now="2026092%dT000000Z" % i)
    names = sorted(p.name for p in (tmp_path / "health").glob("*.json"))
    assert "20260920T000000Z.json" not in names, "最旧的一份该被删"
    assert "stranger.json" in names, "只删 index 里登记过的"
    idx = json.loads((tmp_path / "health" / "index.json").read_text(encoding="utf-8"))
    assert len(idx) == 3


def test_archive_failure_warns_and_never_raises(monkeypatch, tmp_path, capsys):
    """**存档失败不能拖垮技能。** 目录是个文件 → mkdir 失败 → 只 warn。"""
    blocker = tmp_path / "blocked"
    blocker.write_text("x", encoding="utf-8")
    monkeypatch.setenv("GSDB_REPORTS_DIR", str(blocker))
    assert reports.archive("health", {"overall": 0}) is None
    assert "[warn] 报告未存档" in capsys.readouterr().err


def test_append_jsonl(monkeypatch, tmp_path):
    monkeypatch.setenv("GSDB_REPORTS_DIR", str(tmp_path))
    assert reports.append_jsonl("kb", "queries", {"q": "慢 SQL"}) is True
    assert reports.append_jsonl("kb", "queries", {"q": "锁"}) is True
    lines = (tmp_path / "kb" / "queries.jsonl").read_text(encoding="utf-8").splitlines()
    assert [json.loads(x)["q"] for x in lines] == ["慢 SQL", "锁"]


def test_write_is_atomic_no_tmp_left_behind(monkeypatch, tmp_path):
    monkeypatch.setenv("GSDB_REPORTS_DIR", str(tmp_path))
    reports.archive("wdr", {"overall": 0}, now="20260922T000000Z")
    assert not list((tmp_path / "wdr").glob("*.tmp"))


# ---- 按实例分目录 ----------------------------------------------------------------

def test_instance_key_sanitizes_conn_name():
    assert reports.instance_key("api/10-0-0-9-postgres") == "api_10-0-0-9-postgres"
    assert reports.instance_key("og5") == "og5"
    assert reports.instance_key("") == "_default"
    assert reports.instance_key("../x") == ".._x", "斜杠必须换掉,键要能当路径段"


def test_archive_with_instance_goes_into_subdir_and_updates_targets(monkeypatch, tmp_path):
    """三个大盘都是按库统计的:一个人上午看 A 库下午看 B 库,latest/index 不能混。"""
    monkeypatch.setenv("GSDB_REPORTS_DIR", str(tmp_path))
    reports.archive("health", {"overall": 1, "conn": "api/10-0-0-9-postgres"},
                    instance="api/10-0-0-9-postgres", now="20260923T010000Z")
    reports.archive("health", {"overall": 2, "conn": "og5"}, instance="og5", now="20260923T020000Z")
    reports.archive("health", {"overall": 0, "conn": "og5"}, instance="og5", now="20260923T030000Z")
    assert (tmp_path / "health" / "api_10-0-0-9-postgres" / "latest.json").is_file()
    assert (tmp_path / "health" / "og5" / "latest.json").is_file()
    assert not (tmp_path / "health" / "latest.json").exists(), "分实例后技能根目录不该再有 latest"
    t = json.loads((tmp_path / "health" / "targets.json").read_text(encoding="utf-8"))
    assert [x["key"] for x in t] == ["og5", "api_10-0-0-9-postgres"], "按最近一次降序"
    assert t[0] == {"key": "og5", "conn": "og5", "last_at": "20260923T030000Z", "count": 2}
    og5_idx = json.loads((tmp_path / "health" / "og5" / "index.json").read_text(encoding="utf-8"))
    assert [e["overall"] for e in og5_idx] == [2, 0], "各实例各自的 index,不混"


def test_archive_without_instance_is_unchanged(monkeypatch, tmp_path):
    monkeypatch.setenv("GSDB_REPORTS_DIR", str(tmp_path))
    reports.archive("kb", {"a": 1}, name="health")
    assert (tmp_path / "kb" / "health.json").is_file() and not (tmp_path / "kb" / "targets.json").exists()
