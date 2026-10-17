"""造数据(一次性):跑 sql/schema.sql、建分区表、按设计收集统计信息。

**统计信息是分两类处理的,这是设计不是疏漏:**
  · 大部分表 prepare 完就 ANALYZE —— 不然连「正常的计划」都没有,慢 SQL 场景
    分不清是设计的坏计划还是单纯没统计信息;
  · `import_stage` 永不 ANALYZE(V7 / S5 要的就是它的统计信息停在 0 行)。

**og5 上实测:** 100 万行约 1.6 秒、300 万行约 3.3 秒,整个 prepare 一分钟内完事。
所以 prepare 不做增量、不做断点续传 —— 每次整个重建,比维护「上次到哪了」可靠。
"""
from __future__ import annotations

import time
from typing import Dict, List

from . import config as C
from .db import Conn, LabError, assert_expected_instance

# 这些表 prepare 完就收集统计信息。import_stage 不在列 —— 见模块 docstring。
ANALYZE_TABLES = tuple(t.name for t in C.TABLES if t.name != "import_stage")


def _schema_sql() -> str:
    path = C.SQL_DIR / "schema.sql"
    try:
        return path.read_text(encoding="utf-8")
    except OSError as exc:
        raise LabError("读不到 %s:%s" % (path, exc)) from exc


def partition_ddl(parts: int = C.PARTITIONS, rows: int = 100_000) -> List[str]:
    """L4 的千分区表。

    分区 DDL 不写在 schema.sql 里:1000 个分区手写一遍没人看得下去,而且分区数要能改
    (机器小就调小)。这里生成,`parts` 只接受正整数 —— 它直接拼进 DDL。
    """
    if not isinstance(parts, int) or parts < 2 or parts > 10_000:
        raise LabError("分区数要在 2~10000 之间,给的是 %r" % (parts,))
    if not isinstance(rows, int) or rows < 0:
        raise LabError("行数不能是 %r" % (rows,))
    # 第 i 个分区收 bucket < i,所以 bucket 取 0 … parts-1(不是 1 … parts ——
    # 那样最大的那个值会落在所有分区之外,插入时报「没有匹配的分区」)。
    body = ",\n  ".join("PARTITION p%d VALUES LESS THAN (%d)" % (i, i) for i in range(1, parts + 1))
    return [
        "DROP TABLE IF EXISTS %s.part_log CASCADE" % C.SCHEMA,
        "CREATE TABLE %s.part_log (id bigint, bucket int, payload varchar(100))\n"
        "  PARTITION BY RANGE (bucket) (\n  %s\n)" % (C.SCHEMA, body),
        "INSERT INTO %s.part_log SELECT g, g %% %d, 'p-' || g FROM generate_series(1, %d) g"
        % (C.SCHEMA, parts, rows),
        "CREATE INDEX idx_part_log_bucket ON %s.part_log (bucket) LOCAL" % C.SCHEMA,
    ]


def import_batch(c: Conn, rows: int = C.IMPORT_ROWS) -> None:
    """V7:往 import_stage 导一批数据,**导完不做 ANALYZE**。

    prepare 与 V7 场景共用这一个函数 —— 两处各写一遍的话,很容易有一处顺手加上
    ANALYZE,那 S5 的「估错一个数量级」就没了,而且看不出是哪一边改坏的。
    """
    if not isinstance(rows, int) or rows <= 0 or rows > 5_000_000:
        raise LabError("导入行数不合法:%r" % (rows,))
    c.exec("TRUNCATE %s.import_stage" % C.SCHEMA)
    # cust_id 故意倾斜:三分之一的行都挂在 BIG_CUSTOMER_ID 上。S5 要的「估少 60 倍」
    # 来自这个倾斜 —— 没有统计信息时优化器按默认选择率(约 0.5%)猜,实际有三分之一。
    c.exec("INSERT INTO %s.import_stage "
           "SELECT g, (g %% 1000000) + 1, "
           "       CASE WHEN g %% 3 = 0 THEN %d ELSE (g %% 200000) + 1 END, "
           "       ((g %% 5000) + 1)::numeric(12,2), now() "
           "  FROM generate_series(1, %d) g" % (C.SCHEMA, C.BIG_CUSTOMER_ID, rows))


def run(verbose: bool = True) -> Dict[str, object]:
    """建全部库对象并造数。返回每一步的耗时,方便看是哪一步慢了。"""
    c = Conn("prepare")
    steps: Dict[str, object] = {}
    try:
        fp = assert_expected_instance(c)
        steps["实例"] = fp["data_dir"]
        _timed(steps, "建表造数", verbose, lambda: c.exec(_schema_sql()))
        _timed(steps, "千分区表", verbose, lambda: [c.exec(s) for s in partition_ddl()])
        _timed(steps, "收集统计信息", verbose,
               lambda: [c.exec("ANALYZE %s.%s" % (C.SCHEMA, t)) for t in ANALYZE_TABLES])
        # 放在 ANALYZE **之后**:这张表要的就是没有统计信息(见函数 docstring)
        _timed(steps, "导入 import_stage(不收集统计信息)", verbose, lambda: import_batch(c))
        steps["占用空间"] = size_report(c)
        steps["行数核对"] = _verify_rows(c)
    finally:
        c.close()
    return steps


def _timed(steps: Dict[str, object], label: str, verbose: bool, fn) -> None:
    t0 = time.time()
    if verbose:
        print("  %s …" % label, flush=True)
    fn()
    steps[label] = "%.1fs" % (time.time() - t0)


def size_report(c: Conn) -> str:
    total = c.one(
        "SELECT pg_size_pretty(sum(pg_total_relation_size(c.oid))) FROM pg_class c "
        "JOIN pg_namespace n ON n.oid = c.relnamespace WHERE n.nspname = %s AND c.relkind = 'r'",
        (C.SCHEMA,))
    return str(total or "?")


def _verify_rows(c: Conn) -> str:
    """造完核对行数。

    **不核对的话,「场景没造出现象」会被误当成场景逻辑的问题** —— 实际可能只是
    造数那一步某条 SQL 失败了而已(整个 schema.sql 是一次执行的,中间失败只会
    留下半套表)。这里只核对量级,不追求精确。
    """
    bad: List[str] = []
    for t in C.TABLES:
        if t.rows == 0:
            continue
        got = c.one("SELECT count(*) FROM %s.%s" % (C.SCHEMA, t.name)) or 0
        if abs(int(got) - t.rows) > max(10, t.rows * 0.02):
            bad.append("%s 实际 %s 行、期望约 %s 行" % (t.name, got, t.rows))
    return "全部相符" if not bad else "对不上:" + ";".join(bad)
