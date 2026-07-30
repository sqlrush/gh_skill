# memanalyze 设计（动态内存冲高分析 skill）

> 日期：2026-07-12。状态：已确认，待实现。

## 1. 目标

新增 `skills/memanalyze`，分析 openGauss / GaussDB 的动态内存冲高：内存用在**哪些会话**、
**哪条 SQL**、SQL 的**哪个算子**上，以及冲高的根因是「大查询真用量」「内存泄漏 / 缓存膨胀」
还是「参数配置不合理」。

沿用 health 的模式：**脚本采多维证据 + 按阈值产确定性发现，LLM 只做解读与归因**。
单命令出报告——内存冲高是应急场景，DBA 要的是一条命令十秒出结论，不是三步流程
（这是不选 wdr 三阶段模式的理由：wdr 的快照不可变、可离线反复渲染，而内存现场是易失的）。

## 2. 三个子命令

| 子命令 | 场景 | 数据来源 |
|---|---|---|
| `snapshot` | 现场：内存正在高位 | 实时视图，六层全采（默认命令） |
| `history` | 事后：冲高已过去 | `gs_wlm_session_history` / `_info`、`gs_wlm_operator_history` / `_info` |
| `watch` | 持续采样看趋势 | 按间隔采 N 次 L1 + L3，脚本判定泄漏 / 尖峰 / 平稳 |

三者共用同一套 `MemEvidence` / `Finding` 结构与同一个报告渲染。

## 3. 六层证据

**L0 能力探测（每次必跑，结果印在报告最前面）**：读 `resource_track_level`、
`enable_resource_record`、`enable_resource_track`、`memory_tracking_mode`，算出哪些层有数据。

| 层 | 回答什么 | 确定性发现举例 |
|---|---|---|
| L1 实例级 | 冲的是动态内存 / 共享内存 / other？峰值回落没？ | `dynamic_used` 占 `max_dynamic_memory` ≥90% → 🔴；`dynamic_peak` 远高于当前 → 曾冲高已回落 |
| L2 上下文级 | 泄漏 / 缓存膨胀，还是真在干活？ | 单 context 占动态内存 >15%；`free/total` 比例高 → 内存碎片；`CacheMemoryContext` 过大 → 元数据缓存膨胀 |
| L3 会话级 | 内存用在**哪些会话**（用户 / 应用 / 状态） | 单会话 peak 占动态内存 >20%；`idle in transaction` 却占大内存 → 会话不释放 |
| L4 SQL 级 | 用在**哪条 SQL** 上 | `max_peak_memory` Top N；`estimate_memory` 与实际偏差 >10× → 优化器估算错；`spill_size` >0 → work_mem 不足下盘；`warning` 字段是 GaussDB 自带告警 |
| L5 算子级 | 用在 SQL 的**哪个算子**上 | 定位 `plan_node_id` + `plan_node_name`（Vector Sort / HashAgg / HashJoin）的峰值内存与下盘量；`estimated_rows` vs `tuple_processed` 偏差大；`memory_skew_percent` 高 → 数据倾斜 |
| L6 配置面 | 是不是**参数本身**不合理 | `work_mem × 并发` 理论上限超过 `max_dynamic_memory` → 配置性风险 |

阈值集中在 `thresholds.py`（`Thresholds` frozen dataclass），Severity 四档沿用 health：
🟢健康 / 🟡关注 / 🟠告警 / 🔴严重。

## 4. 运行时视图探测（兼容 openGauss 与 GaussDB 的核心）

openGauss 与 GaussDB（以及集中式 / 分布式、不同版本）的内存视图**命名与列集都不一致**，
且用户无法预先确定自己环境有哪些。因此**不硬编码任何视图名**。

每层给一个按优先级排序的候选清单，`probe.py` 启动时探测，用第一个真正可用的：

| 层 | 候选视图（优先级从高到低） |
|---|---|
| L1 | `gs_total_memory_detail` → `pv_total_memory_detail` → `dbe_perf.global_memory_node_detail` |
| L2 | `gs_thread_memory_context` → `pv_thread_memory_context`；`gs_session_memory_detail` → `pv_session_memory_detail`；`gs_shared_memory_detail` → `pv_shared_memory_detail` |
| L3 | `dbe_perf.session_memory` → `gs_session_memory`；恒定叠加 `pg_stat_activity` |
| L4 | `gs_wlm_session_statistics` → `pgxc_wlm_session_statistics`；历史 `gs_wlm_session_history` → `gs_wlm_session_info` |
| L5 | `gs_wlm_operator_statistics` → `pgxc_wlm_operator_statistics`；历史 `gs_wlm_operator_history` → `gs_wlm_operator_info` |

**探测方式**：查系统表（`regclass` + `pg_attribute`）拿到视图是否存在**以及它真实有哪些列**，
而不是「试查一下看报不报错」。拿到真实列集后，采集器**按列名构建查询**，缺失的列填 `NULL`，
不让整条 SQL 因为一个不存在的列而报错——这同时解决了列名方言差异
（如 `gs_total_memory_detail` 的 `nodename` 列、`gs_wlm_operator_history` 的 `warning` 列
并非所有版本都有）。

「给定哪些视图可用，该选哪个」是纯函数，可单测，不需要数据库。

报告开头必须印出探测结果，让用户顺便摸清自己的环境：

```
## 能力与视图探测
- L1 实例级   ✓ gs_total_memory_detail (14 列)
- L2 上下文   ✓ gs_session_memory_detail｜✗ gs_thread_memory_context（不存在）
- L3 会话级   ✓ dbe_perf.session_memory + pg_stat_activity
- L4 SQL 级   ✓ gs_wlm_session_statistics
- L5 算子级   ✗ 不可用：resource_track_level = query（需设为 operator）[需人工执行]
- L6 配置面   ✓
```

**L5 那行是纪律的体现**：不是「没查到数据」，而是明确说明为什么没有、怎么才能有。
GUC 没开就说 GUC 没开，视图不存在就说不存在——**绝不静默出空表**让人误以为算子层没问题。
这与项目里 hypopg 的 `provides_session` 守卫是同一套原则。

## 5. 组件划分

```
skills/memanalyze/
├── SKILL.md
├── references/
│   ├── gaussdb-memory-internals.md   动态/共享/other 内存、memory context 树、WLM 资源跟踪
│   └── memory-methodology.md          根因判定树 + 阈值表
└── scripts/
    ├── memanalyze.py   入口：snapshot / history / watch
    ├── probe.py        视图探测 → Catalog（视图名 + 真实列集）
    ├── capability.py   GUC 探测 → Capability（哪些层可用；不可用的原因）
    ├── collectors.py   L1 实例 / L2 上下文 / L3 会话 / L6 配置
    ├── wlm.py          L4 SQL / L5 算子（实时与历史共用列自适应逻辑）
    ├── trend.py        纯函数：采样序列 → 泄漏 / 尖峰回落 / 平稳
    ├── model.py        Severity / Finding / DimResult / ViewInfo / Capability / MemEvidence
    ├── thresholds.py   阈值
    ├── util.py         单位换算与格式化
    ├── report.py       MemEvidence → markdown / json
    └── render.py       vendored 副本
```

采集器拆两个文件：L4/L5 有大量共享逻辑（实时表与历史表的列自适应、queryid 关联），
合并会超过 600 行。项目规范单文件上限 800、目标 200–400。

数据流：

```
snapshot: probe → capability → [L1 L2 L3 L6] + [L4 L5] ──┐
history:  probe → capability → [L4 L5 历史表]            ─┼→ MemEvidence → report(md/json)
watch:    probe → 循环采 [L1 L3] ×N → trend.analyze      ─┘
```

`history` 模式下 L1/L2/L3 标注「历史模式不可用：实时视图无历史数据」——这是事实陈述，
不是采集失败。`watch` 只采 L1 与 L3 的轻量指标（采样要快，不能每次全量扫内存上下文）。

## 6. 错误处理

- **退出码** 0 / 1 / 2：0 = 脚本跑成功（**内存有没有问题不影响退出码**，结论在 stdout）、
  1 = 运行错误、2 = 连接/配置错误。
- 每个采集器 `try/except common.DBError` → `degraded(dim, reason)`，一层采不到不影响其余五层。
- `watch` 中途单次采样失败 → 记 note 后继续，不中断整个序列。
- 唯一致命错误：`probe` 连系统表都查不了（连接根本不可用）→ 退出码 1。
- 不吞异常；后端异常统一是 `DBError`。

## 7. 测试（TDD，先写测试，全部 DB-free）

- `probe`：给定「哪些视图存在」，选中的必须是优先级最高的那个；一个都不可用时的 reason
  必须说人话（含候选清单）。
- `capability`：`resource_track_level = query` → L5 标为不可用，且**原因字符串含 GUC 名与目标值**。
- `trend`：单调上升序列 → 泄漏；尖峰回落序列 → 单次大查询；抖动序列 → 平稳；边界值各一例。
- `collectors` / `wlm`：FakeDB 喂假 rows（沿用 `tests/test_sqlfamily_units.py` 的模式），
  验证 DimResult 构造、阈值触发的 Finding、单维失败时的 `degraded`。
- `report`：能力探测节渲染、findings 按 severity 降序、空证据。

## 8. 明确不做（YAGNI）

- 不做 wdr 式三阶段（collect → LLM interp → render 复核）：应急场景太重。
- 不执行任何变更：改 GUC、kill 会话一律只给建议并标注 `[需人工执行]`。
- 不猜测视图名：探测不到就如实报告，不 fallback 到「假装有数据」。

## 9. 已知不确定项（待真库验证）

- L2 内存上下文视图在 `memory_tracking_mode = none` 时是否仍可查——探测机制会如实反映，
  但阈值可能需要在真库上校准。
- L4/L5 实时表与历史表的字段集差异——列自适应机制能容忍，但首轮真库运行后应把
  实际列集记录到 `docs/` 的已知限制中，与 `docs/connection-drivers.md` 标注 gsql parity
  「待验证」是同一做法。
