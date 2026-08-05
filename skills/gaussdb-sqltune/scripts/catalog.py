"""把证据包里的采集结果整理成可按名字查的目录，并守住两道前置门。

两道门都是「不满足就整段作废」，不是「打个折扣继续算」：

**门一：名字歧义。** evidence.extract_tables() 给的是**不带 schema** 的表名，
采集脚本也是 `WHERE relname IN (...)`。两个 schema 下有同名表时会返回两行，
而代码无从知道 SQL 用的是哪一张。此时若「取第一行」，推演会拿另一张表的
页数和统计信息算出一个精确的错数 —— 不报错、不告警。所以歧义即拒绝。

**门二：统计信息陈旧。** 推演的全部输入都是上次 ANALYZE 时冻结的快照。
表在那之后翻了十倍，这些数不会报错，只会让推演算出一个精确的错数。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional, Sequence


class CatalogError(Exception):
    """目录不足以支撑推演。调用方必须停止，不能退化成「尽量算」。"""


# 冻结值与近实时值偏离多少算陈旧。10% 是个工程判断：再小会被 autovacuum 的
# 正常滞后频繁触发，再大就失去意义。这个阈值必须**写进报告**，否则读的人
# 无从判断「陈旧」是按什么标准说的。
STALE_DRIFT_THRESHOLD = 0.10


@dataclass(frozen=True)
class FreshnessVerdict:
    table: str
    fresh: bool
    reason: str
    reltuples: float          # pg_class 里的冻结值
    live_tuples: float        # 统计收集器的近实时值
    drift: Optional[float]    # 相对偏离，无法计算时 None
    last_analyze: str
    last_autoanalyze: str


class Catalog:
    """按名字查表/索引/列统计。构造时就把歧义挡掉。"""

    def __init__(self, tables, indexes, columns, freshness):
        self._tables = _unique_by(tables, lambda t: t.name, "表")
        self._indexes = _unique_by(indexes, lambda i: i.name, "索引")
        self._columns: Dict[tuple, object] = {}
        for col in columns:
            self._columns[(col.table, col.column)] = col
        self._freshness = _unique_by(freshness, lambda f: f.table, "统计新鲜度行")

    # --- 查 ---------------------------------------------------------------

    def table(self, name: str):
        found = self._tables.get(_norm(name))
        if found is None:
            raise CatalogError(
                "表 %r 不在证据包里 —— 它可能是视图、CTE 或别名，"
                "也可能是表名提取漏了。无论哪种，都拿不到页数/行数，不能复算。"
                % name)
        return found

    def index(self, name: str):
        found = self._indexes.get(_norm(name))
        if found is None:
            raise CatalogError("索引 %r 不在证据包里" % name)
        return found

    def column(self, table: str, column: str):
        found = self._columns.get((_norm(table), _norm(column)))
        if found is None:
            raise CatalogError(
                "列 %s.%s 没有统计信息（pg_stats 里没有这一行）。"
                "该列可能从未被 ANALYZE 覆盖 —— 缺 correlation 就没法算回表 IO。"
                % (table, column))
        return found

    def total_table_pages(self) -> float:
        """本查询涉及的所有表的页数之和 —— Mackert-Lohman 的缓存摊分基数。

        用**实时**块数：规划器算缓存摊分时用的就是它，与 cost_index 里传的
        baserel->pages 是同一个来源。混用冻结值会让摊分基数偏小，b 偏大，
        于是「缓存装得下」被判成立，索引扫描的 IO 被低估。
        """
        return float(sum(t.cur_pages for t in self._tables.values()))

    # --- 门二 -------------------------------------------------------------

    def freshness(self, name: str) -> FreshnessVerdict:
        table = self.table(name)
        row = self._freshness.get(_norm(name))
        if row is None:
            return FreshnessVerdict(
                table=name, fresh=False,
                reason="pg_stat_user_tables 里没有这张表的行 —— 可能是系统表或"
                       "从未被访问过。拿不到近实时行数，无法判断快照有多旧，"
                       "按「不确定」处理，即不通过。",
                reltuples=float(table.tuples), live_tuples=0.0, drift=None,
                last_analyze="", last_autoanalyze="")

        if row.last_analyze == "never" and row.last_autoanalyze == "never":
            return FreshnessVerdict(
                table=name, fresh=False,
                reason="从未 ANALYZE 过（last_analyze 与 last_autoanalyze 均为 "
                       "never）。此时 pg_class.reltuples 多半是建表时的估值，"
                       "推演的每一个输入都不可信。",
                reltuples=float(table.tuples), live_tuples=float(row.live_tuples),
                drift=None, last_analyze=row.last_analyze,
                last_autoanalyze=row.last_autoanalyze)

        frozen = float(table.tuples)
        live = float(row.live_tuples)
        if frozen <= 0:
            return FreshnessVerdict(
                table=name, fresh=False,
                reason="pg_class.reltuples 为 %g —— 冻结值本身就是空的，"
                       "没有可比对的基准。" % frozen,
                reltuples=frozen, live_tuples=live, drift=None,
                last_analyze=row.last_analyze,
                last_autoanalyze=row.last_autoanalyze)

        drift = abs(live - frozen) / frozen
        if drift > STALE_DRIFT_THRESHOLD:
            return FreshnessVerdict(
                table=name, fresh=False,
                reason="冻结值 %g 行与近实时 %g 行相差 %.1f%%，超过阈值 %.0f%% "
                       "—— 上次 ANALYZE（%s）之后数据变化太大，"
                       "推演要用的那份统计已经不代表现状。"
                       % (frozen, live, drift * 100.0,
                          STALE_DRIFT_THRESHOLD * 100.0, row.last_analyze),
                reltuples=frozen, live_tuples=live, drift=drift,
                last_analyze=row.last_analyze,
                last_autoanalyze=row.last_autoanalyze)

        return FreshnessVerdict(
            table=name, fresh=True,
            reason="冻结值 %g 行与近实时 %g 行相差 %.1f%%，在阈值 %.0f%% 之内"
                   % (frozen, live, drift * 100.0, STALE_DRIFT_THRESHOLD * 100.0),
            reltuples=frozen, live_tuples=live, drift=drift,
            last_analyze=row.last_analyze,
            last_autoanalyze=row.last_autoanalyze)

    def freshness_report(self, names: Sequence[str]) -> List[FreshnessVerdict]:
        return [self.freshness(n) for n in names]


def from_evidence(ev) -> Catalog:
    return Catalog(ev.tables, ev.indexes, ev.columns,
                   getattr(ev, "freshness", []))


# --- 内部 --------------------------------------------------------------------

def _norm(name: str) -> str:
    return str(name or "").strip().lower()


def _unique_by(rows, key, what: str) -> Dict[str, object]:
    """同名多行即拒绝 —— 见模块头「门一」。"""
    out: Dict[str, object] = {}
    dupes: Dict[str, int] = {}
    for row in rows or []:
        name = _norm(key(row))
        if name in out:
            dupes[name] = dupes.get(name, 1) + 1
            continue
        out[name] = row
    if dupes:
        listed = "、".join("%s(%d 份)" % (n, c) for n, c in sorted(dupes.items()))
        raise CatalogError(
            "%s名字有歧义：%s。采集脚本按 relname 匹配、不带 schema，"
            "多个 schema 下有同名对象时无从知道 SQL 用的是哪一个。"
            "此时取第一个会拿另一张表的统计信息算出一个精确的错数 —— "
            "不报错也不告警，所以这里直接拒绝。" % (what, listed))
    return out
