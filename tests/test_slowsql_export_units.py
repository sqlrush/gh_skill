"""slowsql 的 CSV 导出。

实测踩到：慢 SQL 超过 10 条时脚本会写 CSV，但**导出目录不存在就直接崩**，
错误是 `[Errno 2] No such file or directory: .../csv/slow_sql_export_....csv`
—— 看不出是导出出了问题，像是取数失败。而它恰恰在「库里慢 SQL 多」时触发，
也就是最需要这条命令的时候。
"""
import csv
import pathlib
import sys

import pytest

_ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT))
sys.path.insert(0, str(_ROOT / "skills" / "gaussdb-slowsql" / "scripts"))

import slowsql  # noqa: E402


class _Runner:
    """按脚本名给预置行；值全是字符串 —— 协议就是这个形态。"""

    def __init__(self, n):
        self.n = n

    def run(self, script, values=None):
        return [
            {"unique_sql_id": str(i), "query": "SELECT %d" % i, "calls": "1",
             "avg_ms": "1.0", "total_sec": "1.0", "cpu_sec": "1.0", "rows": "1"}
            for i in range(self.n)
        ]


def test_export_creates_its_directory(tmp_path, monkeypatch, capsys):
    """导出目录不存在时自己建，而不是崩掉整条命令。

    这条命令在「慢 SQL 多」时才走导出分支 —— 正是最需要它的时候。
    """
    # _HERE 是**文件**路径（Path(__file__).resolve()），不是目录 ——
    # parents[3] 因此是安装根目录。造假路径时层数要对上。
    monkeypatch.setattr(slowsql, "_HERE", tmp_path / "root" / "skills" / "gaussdb-slowsql" / "scripts" / "slowsql.py")
    rows = slowsql.slow_sql(_Runner(12), 100, 50, "2000-01-01 00:00:00", False)

    out = tmp_path / "root" / "csv"
    files = list(out.glob("slow_sql_export_*.csv"))
    assert files, "导出目录没建出来，或者文件没写成"
    assert "数据已导出到" in capsys.readouterr().out
    assert len(rows) <= 3, "导出后返回的应是摘要行"


def test_exported_csv_holds_every_row_not_just_the_summary(tmp_path, monkeypatch):
    """CSV 里要有全部行 —— 摘要只显示 3 条，全量得能从文件里拿到。"""
    monkeypatch.setattr(slowsql, "_HERE", tmp_path / "root" / "skills" / "gaussdb-slowsql" / "scripts" / "slowsql.py")
    slowsql.slow_sql(_Runner(12), 100, 50, "2000-01-01 00:00:00", False)

    path = next((tmp_path / "root" / "csv").glob("*.csv"))
    with open(path, newline="", encoding="utf-8") as f:
        body = list(csv.reader(f))
    assert len(body) == 13, "表头 1 行 + 数据 12 行"


def test_no_export_below_the_threshold(tmp_path, monkeypatch):
    """行数不多时不该产生文件 —— 免得每次跑都留一堆垃圾。"""
    monkeypatch.setattr(slowsql, "_HERE", tmp_path / "root" / "skills" / "gaussdb-slowsql" / "scripts" / "slowsql.py")
    slowsql.slow_sql(_Runner(3), 100, 50, "2000-01-01 00:00:00", False)
    assert not (tmp_path / "root" / "csv").exists()


@pytest.mark.parametrize("raw", ["false", "False", "0", "no"])
def test_export_flag_rejects_the_bool_string_trap(raw):
    """`--export false` 不能被当成「要导出」。

    argparse 的 type=bool 就是 bool(str)：非空字符串一律为真，于是
    `--export false` 打开了导出。与 bool("f") 是同一个坑，只是换了个地方。
    """
    ap = slowsql.build_parser() if hasattr(slowsql, "build_parser") else None
    if ap is None:
        pytest.skip("入口未拆出 build_parser")
    args = ap.parse_args(["-c", "og", "--export", raw])
    assert args.export is False
