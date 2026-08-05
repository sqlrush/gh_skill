"""代价模型：按规划器的公式复算每个算子的 cost。

**这个模块的输出不是「我认为应该是多少」，是「按公式算出来是多少」。**
它存在的唯一理由是让校准闸有东西可比：先用它复算 EXPLAIN 已经给出的那个
计划，与数据库自己报的 cost 逐节点对；对上了，才有资格拿同一套公式去算
假设路径。对不上就是模型在这个实例上不适用，当场停，不出建议。

公式对应 PostgreSQL 的 src/backend/optimizer/path/costsize.c。openGauss 的
内核基线是 9.2.4，优化器有自己的改动 —— 所以这里**不假设**公式一定适用，
由校准闸实测判定。这也是为什么每一项都拆成 Term 单独留痕：对不上的时候，
能看出是哪一项偏了，而不是只知道总数不对。

术语对齐（与 costsize.c 一致，避免和「行数/页数」混淆）：
    T  表的页数（relpages），至少 1
    b  该表能分到的缓存页数（effective_cache_size 按页数占比摊）
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import List, Optional

# btcostestimate 里每下降一层索引页要收的 CPU 费用倍数
# （PostgreSQL 的 DEFAULT_PAGE_CPU_MULTIPLIER）
_PAGE_CPU_MULTIPLIER = 50.0


class ModelError(Exception):
    """输入不足以复算。调用方当作「这个节点没建模」处理，不能填 0 顶替。"""


@dataclass(frozen=True)
class Term:
    """代价里的一项。报告直接渲染这些，读的人能逐项复核。"""
    label: str
    formula: str     # 代入了实际数值的算式，如 "1 × 1234568"
    value: float


@dataclass(frozen=True)
class Variant:
    """两处已知的内核版本分歧点。

    openGauss 的内核基线是 PostgreSQL 9.2.4，但优化器改动很多，我**不知道**
    它的代价函数跟的是哪一版。与其挑一个然后当成事实，不如把分歧点摆出来，
    让校准闸拿真实计划去试：哪个变体能与 EXPLAIN 对上，就用哪个。
    这样「用了哪一版公式」从一个假设变成一次测量。

    min_io_split_seq
        单次索引扫描、完全相关情况下的最小 IO 代价。
        True （9.3+ 的写法）：第一页随机、其余顺序
            min_IO = random_page_cost + (pages-1) × seq_page_cost
        False（9.2 的写法）：全部按随机
            min_IO = pages × random_page_cost
        相关性高的索引上两者差得很明显 —— 恰恰是「加了索引很划算」那类场景。

    btree_page_cpu_cost
        btcostestimate 里 (tree_height+1) × 50 × cpu_operator_cost 这一项。
        9.3 才加的。绝对值很小（树高 3 时约 0.5），大扫描里可以忽略，但
        嵌套循环单行探测的总代价本身也才个位数，这一项就是百分之几。
    """
    min_io_split_seq: bool = True
    btree_page_cpu_cost: bool = True


@dataclass(frozen=True)
class IndexScanInput:
    """索引扫描复算所需的全部输入。

    刻意做成一个显式的结构而不是十个位置参数：这里面每一个数取错了，
    结果都还是一个像样的浮点数。取值处必须一眼看得出各是什么。
    """
    index_pages: float
    index_tuples: float
    table_pages: float
    table_tuples: float
    selectivity: float          # 索引条件的选择率
    correlation: float          # pg_stats.correlation
    total_table_pages: float    # 本查询涉及的所有表的页数之和
    num_index_quals: int = 1
    tree_height: Optional[int] = None   # 拿不到就估，见 estimate_tree_height


@dataclass(frozen=True)
class Estimate:
    node_type: str
    startup_cost: float
    total_cost: float
    terms: List[Term] = field(default_factory=list)
    # 公式里含估算成分（比如 Filter 的操作符个数是从文本数出来的）时置位。
    # 不降低校准标准 —— 只是让对不上的时候先怀疑这里。
    approximate: bool = False
    notes: List[str] = field(default_factory=list)


# --- 页面获取模型（Mackert-Lohman） -----------------------------------------

def index_pages_fetched(tuples_fetched: float, pages: float,
                        index_pages: float, total_table_pages: float,
                        effective_cache_size: int) -> float:
    """取 N 个元组要读多少页 —— Mackert-Lohman 公式，costsize.c 同名函数。

    直觉：随机取 N 行，落在同一页上的会被缓存命中，所以读的页数远少于 N。
    N 很大时趋近于「整表都读一遍」。

    这一项是索引扫描与顺序扫描之间的胜负手：没有它，「取 100 行」会被算成
    100 次随机 IO，而实际上重复页只读一次。算错的方向是**高估索引代价**，
    于是本该推荐的索引被判成没收益 —— 一个安静的错误结论。
    """
    if effective_cache_size <= 0:
        raise ModelError("effective_cache_size 必须为正，取到 %r" % effective_cache_size)

    T = float(pages) if pages > 1 else 1.0

    # 竞争缓存的总页数：查询涉及的所有表 + 本索引。costsize.c 用的是
    # root->total_table_pages（该查询涉及的表的总页数），不是全库。
    total = max(float(total_table_pages) + float(index_pages), 1.0)
    if T > total:
        # 单表页数不该超过总页数；出现说明调用方传错了 total_table_pages
        raise ModelError(
            "表页数 %g 超过参与竞争的总页数 %g —— total_table_pages "
            "应当是本查询涉及的所有表的页数之和，含本表。" % (T, total))

    b = float(effective_cache_size) * T / total
    b = 1.0 if b <= 1.0 else math.ceil(b)

    if T <= b:
        # 缓存装得下整表：读的页数封顶在整表页数，不会比全表扫还多
        pages_fetched = (2.0 * T * tuples_fetched) / (2.0 * T + tuples_fetched)
        if pages_fetched >= T:
            return T
        return math.ceil(pages_fetched)

    # 缓存装不下整表：前 b 页按上面的规律，之后每多取一个元组按比例多读页。
    #
    # **这个分支不封顶到 T，是有意的**（costsize.c 同样不封）。取数足够多时
    # 同一页会被挤出缓存再读一次，物理读页数确实可以超过表的总页数。
    # 顺手加个 min(…, T) 会让「反复重读」这件事从代价里消失，于是嵌套循环
    # 的内层扫描被系统性低估。
    lim = (2.0 * T * b) / (2.0 * T - b)
    if tuples_fetched <= lim:
        pages_fetched = (2.0 * T * tuples_fetched) / (2.0 * T + tuples_fetched)
    else:
        pages_fetched = b + (tuples_fetched - lim) * (T - b) / T
    return math.ceil(pages_fetched)


def clamp_row_est(rows: float) -> float:
    """行数估算的下限。costsize.c 的 clamp_row_est：不小于 1，且取整。

    没有这个下限，选择率极小时会算出 0.0001 行，后面一路乘下去让代价趋近 0
    —— 于是任何索引看起来都收益无穷大。
    """
    if rows != rows:  # NaN
        raise ModelError("行数估算得到 NaN")
    if rows <= 1.0:
        return 1.0
    # C 的 rint() 默认是 round-half-to-even，Python 的 round() 同规则
    return float(round(rows))


# --- Seq Scan ----------------------------------------------------------------

def seq_scan(relpages: float, reltuples: float, cost, qual_operators: int = 0,
             has_filter: bool = False) -> Estimate:
    """顺序扫描。costsize.c: cost_seqscan。

        run  = seq_page_cost × relpages
             + cpu_tuple_cost × reltuples
             + cpu_operator_cost × 过滤条件里的操作符个数 × reltuples

    没有 Filter 时前两项就是全部，公式是**精确**的 —— 校准闸最该拿这类节点
    当锚点。有 Filter 时第三项要知道操作符个数，那是从计划文本里数出来的，
    数错一个在亿行表上就是 25 万的偏差，所以置 approximate 让排查有方向。
    """
    if relpages < 0 or reltuples < 0:
        raise ModelError("relpages/reltuples 不能为负：%r / %r" % (relpages, reltuples))

    io = cost.seq_page_cost * relpages
    cpu = cost.cpu_tuple_cost * reltuples
    terms = [
        Term("顺序读页", "%g × %g" % (cost.seq_page_cost, relpages), io),
        Term("每行 CPU", "%g × %g" % (cost.cpu_tuple_cost, reltuples), cpu),
    ]
    total = io + cpu

    if qual_operators:
        qual = cost.cpu_operator_cost * qual_operators * reltuples
        terms.append(Term(
            "过滤条件求值",
            "%g × %d × %g" % (cost.cpu_operator_cost, qual_operators, reltuples),
            qual))
        total += qual

    notes = []
    if has_filter and not qual_operators:
        notes.append(
            "计划里有 Filter 但没数出操作符个数 —— 该项按 0 计，"
            "复算值会偏低。这不是「没有过滤代价」，是「没数出来」。")

    return Estimate(
        node_type="Seq Scan",
        startup_cost=0.0,
        total_cost=total,
        terms=terms,
        approximate=has_filter,
        notes=notes,
    )


# --- Index Scan --------------------------------------------------------------

def estimate_tree_height(index_pages: float, index_tuples: float) -> int:
    """估算 btree 树高。

    **目录里拿不到真值** —— 规划器用的是 _bt_getrootheight()，那要读索引元页，
    SQL 取不到。这里拿平均每页条目数当扇出反推：

        fanout = index_tuples / index_pages
        height = ceil(log(index_tuples) / log(fanout)) - 1

    误差 ±1 层在大扫描里可以忽略（每层只值 50×cpu_operator_cost≈0.125），
    但嵌套循环单行探测的总代价本身就是个位数，那时它是百分之几。所以用了
    估算值的节点一律置 approximate。
    """
    if index_pages <= 1 or index_tuples <= 1:
        return 0
    fanout = index_tuples / index_pages
    if fanout <= 1.0:
        raise ModelError(
            "索引每页平均条目数 %g ≤ 1（%g 条目 / %g 页）—— 索引膨胀到这个程度，"
            "扇出模型不成立，树高估不出来。这本身就是个值得报出去的发现，"
            "不该在这里凑一个数糊过去。" % (fanout, index_tuples, index_pages))
    return max(0, math.ceil(math.log(index_tuples) / math.log(fanout)) - 1)


def index_scan(inp: IndexScanInput, cost, loop_count: float = 1.0,
               variant: Optional[Variant] = None) -> Estimate:
    """btree 索引扫描。costsize.c: cost_index + genericcostestimate + btcostestimate。

    分两块：**索引侧**（沿树下降、扫叶子页）与**堆侧**（按 tid 回表取行）。
    堆侧那块是整个模型里最容易被想当然的地方 —— 它不是「取 N 行就是 N 次
    随机 IO」，而是在「完全无序」和「完全有序」两个极端之间按 correlation²
    插值。correlation 实测 0.1 和 0.9 能差出几十倍，凭感觉写就是幻觉。
    """
    variant = variant or Variant()
    if inp.correlation is None:
        raise ModelError(
            "correlation 未知（pg_stats.correlation 为 NULL）。"
            "不能当 0 处理 —— 0 是「物理顺序与索引顺序完全无关」这个**结论**，"
            "不是「不知道」。该列没有统计信息时应当拒绝推演。")
    _require_range("selectivity", inp.selectivity, 0.0, 1.0)
    _require_range("correlation", inp.correlation, -1.0, 1.0)
    if inp.index_pages <= 0 or inp.index_tuples <= 0:
        raise ModelError(
            "索引页数/条目数必须为正，取到 %g / %g。为 0 通常意味着索引建好后"
            "还没 ANALYZE 过，此时任何复算都是无意义的。"
            % (inp.index_pages, inp.index_tuples))

    cache = cost.effective_cache_size
    terms: List[Term] = []
    notes: List[str] = []
    approximate = False

    # --- 索引侧 --------------------------------------------------------------
    num_index_tuples = inp.selectivity * inp.index_tuples
    num_index_pages = math.ceil(num_index_tuples * inp.index_pages / inp.index_tuples)

    if loop_count > 1:
        fetched = index_pages_fetched(num_index_pages * loop_count, inp.index_pages,
                                      inp.index_pages, inp.total_table_pages, cache)
        index_io = fetched * cost.random_page_cost / loop_count
        index_io_formula = "%g 页 × %g ÷ %g 次探测" % (
            fetched, cost.random_page_cost, loop_count)
    else:
        index_io = num_index_pages * cost.random_page_cost
        index_io_formula = "%g × %g" % (num_index_pages, cost.random_page_cost)
    terms.append(Term("索引读页", index_io_formula, index_io))

    qual_op_cost = cost.cpu_operator_cost * inp.num_index_quals
    index_cpu = num_index_tuples * (cost.cpu_index_tuple_cost + qual_op_cost)
    terms.append(Term(
        "索引条目 CPU",
        "%g × (%g + %g×%d)" % (num_index_tuples, cost.cpu_index_tuple_cost,
                               cost.cpu_operator_cost, inp.num_index_quals),
        index_cpu))

    index_startup = 0.0
    index_total = index_io + index_cpu

    # 沿树下降的比较次数：log2(条目数)，每次一个 cpu_operator_cost
    if inp.index_tuples > 1:
        descent = math.ceil(math.log(inp.index_tuples) / math.log(2.0)) \
            * cost.cpu_operator_cost
        index_startup += descent
        index_total += descent          # num_sa_scans = 1（简单等值/范围条件）
        terms.append(Term(
            "btree 下降比较",
            "ceil(log2(%g)) × %g" % (inp.index_tuples, cost.cpu_operator_cost),
            descent))

    if variant.btree_page_cpu_cost:
        height = inp.tree_height
        if height is None:
            height = estimate_tree_height(inp.index_pages, inp.index_tuples)
            approximate = True
            notes.append(
                "树高 %d 是估的 —— 真值要读索引元页（_bt_getrootheight），"
                "SQL 取不到。误差 ±1 层约 %.3f，大扫描里可忽略，"
                "单行探测里是百分之几。" % (height, _PAGE_CPU_MULTIPLIER
                                            * cost.cpu_operator_cost))
        descent = (height + 1) * _PAGE_CPU_MULTIPLIER * cost.cpu_operator_cost
        index_startup += descent
        index_total += descent
        terms.append(Term(
            "索引层下降 CPU",
            "(%d+1) × %g × %g" % (height, _PAGE_CPU_MULTIPLIER,
                                  cost.cpu_operator_cost),
            descent))

    # --- 堆侧 ----------------------------------------------------------------
    tuples_fetched = clamp_row_est(inp.selectivity * inp.table_tuples)
    correlated_pages = math.ceil(inp.selectivity * inp.table_pages)

    if loop_count > 1:
        fetched = index_pages_fetched(tuples_fetched * loop_count, inp.table_pages,
                                      inp.index_pages, inp.total_table_pages, cache)
        max_io = fetched * cost.random_page_cost / loop_count
        fetched_c = index_pages_fetched(correlated_pages * loop_count, inp.table_pages,
                                        inp.index_pages, inp.total_table_pages, cache)
        min_io = fetched_c * cost.random_page_cost / loop_count
    else:
        fetched = index_pages_fetched(tuples_fetched, inp.table_pages,
                                      inp.index_pages, inp.total_table_pages, cache)
        max_io = fetched * cost.random_page_cost
        if variant.min_io_split_seq:
            min_io = cost.random_page_cost
            if correlated_pages > 1:
                min_io += (correlated_pages - 1) * cost.seq_page_cost
        else:
            min_io = correlated_pages * cost.random_page_cost

    csquared = inp.correlation ** 2
    heap_io = max_io + csquared * (min_io - max_io)
    terms.append(Term(
        "回表 IO（按 correlation² 插值）",
        "%.2f + %.4f × (%.2f − %.2f)" % (max_io, csquared, min_io, max_io),
        heap_io))

    heap_cpu = cost.cpu_tuple_cost * tuples_fetched
    terms.append(Term(
        "回表每行 CPU", "%g × %g" % (cost.cpu_tuple_cost, tuples_fetched), heap_cpu))

    return Estimate(
        node_type="Index Scan",
        startup_cost=index_startup,
        total_cost=index_total + heap_io + heap_cpu,
        terms=terms,
        approximate=approximate,
        notes=notes,
    )


def _require_range(name: str, value: float, low: float, high: float) -> None:
    if value is None or value != value or not (low <= value <= high):
        raise ModelError("%s 应在 [%g, %g] 内，取到 %r" % (name, low, high, value))
