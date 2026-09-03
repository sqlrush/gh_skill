# 新增三个 skill：gaussdb-sqlreview / gaussdb-memanalyze / gaussdb-kb

> 面向使用者与开发者。这三个是本项目在 Go 版 `gdaa` 之外**新增**的能力，无 Go 版对应实现。
> 通用的分层、`common/` 连接层、编码规范见 [02-代码结构详解](02-architecture.md) 与
> [03-编码规范](03-coding-standards.md)，本文不重复。

## 目录

1. [三者的定位与关系](#1-三者的定位与关系)
2. [gaussdb-sqlreview —— SQL 规范审查](#2-sqlreview--sql-规范审查)
3. [gaussdb-memanalyze —— 动态内存冲高分析](#3-memanalyze--动态内存冲高分析)
4. [gaussdb-kb —— 用户知识库导入与治理](#4-kb--用户知识库导入与治理)
5. [知识库落点与优先级链（跨 skill）](#5-知识库落点与优先级链跨-skill)
6. [测试](#6-测试)

---

## 1. 三者的定位与关系

| skill               | family | 一句话 | 连库 |
|---------------------|---|---|---|
| `gaussdb-sqlreview` | `sql-governance` | 按规则审查 DDL/DML/DQL 是否合规 | 部分（文本审查不连） |
| `gaussdb-memanalyze`        | `diagnostics` | 动态内存冲高六层下钻 | 是 |
| `gaussdb-kb`          | `sql-governance` | 把客户规范文档导入知识库，供各 skill 参考 | **否** |

关系：`gaussdb-kb` 建立的**用户知识库**，被 6 个会做规范/阈值判断的 skill 参考
（`gaussdb-sqlreview` / `gaussdb-health` / `gaussdb-wdr` / `gaussdb-memanalyze` / `gaussdb-sqltune` / `gaussdb-proctune`）——但**只作参考**，
不改变这些 skill 的判定逻辑。详见[第 5 节](#5-知识库落点与优先级链跨-skill)。

---

## 2. gaussdb-sqlreview —— SQL 规范审查

### 2.1 使用说明

规范写在 `references/rules.yaml`，**用户可自由编辑**。判定由脚本做，模型只解读。

**三个输入源（三选一）**：

```bash
# a) 静态审查 SQL 文件 —— 上线前评审，不连库
python3 {baseDir}/scripts/sqlreview.py --file changes.sql
cat changes.sql | python3 {baseDir}/scripts/sqlreview.py --stdin

# b) 审查线上跑过的 SQL —— 需要连接
python3 {baseDir}/scripts/sqlreview.py -c <conn> --sql-id <unique_sql_id>
python3 {baseDir}/scripts/sqlreview.py -c <conn> --top 20

# c) 审查库中存量的表与索引 —— 需要连接
python3 {baseDir}/scripts/sqlreview.py -c <conn> --schema public
```

其它参数：`--format markdown|json`（默认 markdown）、`--timeout <秒>`（默认 30）。

**退出码**：`0` = 脚本跑成功（**不代表没有违规**，违规结论在 stdout）、`1` = 运行错误
（规则文件非法、SQL 读取失败）、`2` = 连接/配置错误。**不要把退出码 0 解释为「审查通过」。**

### 2.2 规范怎么改

改 `references/rules.yaml`，四种改法都不用碰 Python：

| 想做什么 | 怎么改 |
|---|---|
| 换成自家命名前缀 | 改该条的 `pattern` |
| 某条规则不适用 | 加 `enabled: false` |
| 调整严重程度 | 改 `severity`（`error` / `warn` / `info`） |
| 新增一条文本规则 | `check: regex` + `pattern` |

**注意**：`install-opencode.sh` 每次重装会 `rm -rf` 整个 skill 目录，所以要改**源码仓**里的
`skills/gaussdb-sqlreview/references/rules.yaml`，改完重跑 `./install-opencode.sh gaussdb-sqlreview`；
直接改安装目录下的副本会在下次安装时丢失。

### 2.3 规则模型

`check` 是**白名单**，写白名单之外的名字会在加载时报错并指出是哪条 `id`（边界处 fail fast）：

| check | 作用 | 参数 | 适用 |
|---|---|---|---|
| `regex` | 逃生舱：用户自由新增文本规则 | `pattern`、可选 `on: normalized\|raw` | 文本 |
| `advisory` | 脚本不判定，取证后交模型 | `criteria` | 两者 |
| `table_no_primary_key` | 建表/存量表无主键 | — | 两者 |
| `table_has_foreign_key` | 存在外键约束 | — | 两者 |
| `naming_pattern` | 表/索引/列命名 | `target`、`pattern` | 两者 |
| `index_column_count` | 索引列数上限 | `max` | 两者 |
| `stmt_forbidden` | 禁用某类语句 | `kind: delete\|truncate\|drop` | 文本 |
| `dml_without_where` | UPDATE/DELETE 无 WHERE | — | 文本 |
| `select_star` | `SELECT *` | — | 文本 |
| `leading_wildcard_like` | 前置通配 `LIKE '%x'` | — | 文本 |
| `index_redundant` | 索引前缀被另一索引覆盖 | — | 仅对象 |

内置基线 **14 条**：`TBL001`–`TBL003`（表设计）、`COL001`（列命名）、`IDX001`–`IDX004`（索引）、
`DML001`–`DML002`、`DDL001`–`DDL002`、`DQL001`–`DQL002`。

### 2.4 代码结构

```
skills/gaussdb-sqlreview/
├── SKILL.md
├── references/rules.yaml      规范清单（用户编辑的唯一文件）
└── scripts/
    ├── sqlreview.py   176 行  入口：argparse、三 source 分发、退出码
    ├── lexer.py       321 行  纯函数：剥注释 → 字面量占位 → 切句 → 识别类型与对象名
    ├── rules.py       169 行  纯函数：加载 + 校验 rules.yaml → Rule
    ├── checks.py      316 行  纯函数：Statement / ObjectFacts + Rule → Finding
    ├── objects.py      98 行  只做 I/O：连库查系统表 → ObjectFacts
    ├── model.py       189 行  frozen dataclass：Severity/Rule/Statement/Finding/…
    ├── report.py      117 行  纯函数：Finding → markdown / json
    ├── sqlfetch.py    122 行  vendored 副本（按 sql_id 取 SQL 文本）
    └── render.py       46 行  vendored 副本（与其余 skill md5 一致）
```

边界：`objects.py` **只采集不判定**；`checks.py` **只判定不连库**。规则引擎全是纯函数，
单测不需要数据库。

数据流（三个输入源在 lexer 处汇成一股）：

```
--file / --stdin       ─┐
--sql-id / --top (连库) ─┴→ SQL 文本 → lexer → [Statement] ─┐
                                                            ├→ checks → [Finding] → report
--schema (连库) → objects → ObjectFacts ────────────────────┘
```

### 2.5 lexer：为什么要自研

项目 `requirements.txt` 只允许 pg8000 / cryptography / PyYAML **三个运行时依赖**，
不能引入 `sqlparse` / `sqlglot`。所以 `lexer.py` 是纯 stdlib 的轻量扫描器：

1. **span 扫描**：一遍扫出 `code / line_comment / block_comment / string / dollar_string /
   quoted_ident` 的区间。
2. **mask**：产出与原文**等长**的掩码串（注释→空格，字符串内部→填充字符，保留换行），
   用于安全地按 `;` 切句并回算行号。
3. **normalize**：把字符串字面量替换为 `:s1 / :s2` 占位符，并保留 `占位符 → 原字面量` 映射。
   正则规则默认跑在归一化文本上——**注释里的 `DELETE`、字符串里的 `%` 都不会误报**。
4. **classify + 抽名**：识别 `kind`（ddl/dml/dql）、`verb`、表名、索引名、索引列。

**关键设计点**：字面量被占位后 `LIKE '%abc'` 变成 `LIKE :s1`，朴素正则 `LIKE\s+'%` 会失效。
因此前置模糊匹配必须由结构化 checker `leading_wildcard_like` 实现——它在归一化文本里定位
`LIKE :sN`，再回查 `:sN` 的原值是否以 `%` 开头。需要匹配原文的 `regex` 规则显式写 `on: raw`。

### 2.6 能力边界

- **没有 SQL 语法解析器**。注释与字符串已正确剥离，但深层语义（子查询里的表别名归属、
  函数索引的实际列）判不了。
- `--sql-id` 取到的线上 SQL 可能是**归一化文本**（字面量变占位符）或**被截断**
  （`track_activity_query_size` 限制），脚本会出 note。此时依赖字面量的规则会失效，
  **不能断言「没有违规」**。
- `--schema` 看到的是**服务端折叠后的名字**（未加引号的 `OrderItems` 在库里就是
  `orderitems`），所以大小写类命名违规**只能在 DDL 文本审查中发现**。

---

## 3. gaussdb-memanalyze —— 动态内存冲高分析

### 3.1 使用说明

```bash
# a) 现场：内存此刻正高（默认子命令）
python3 {baseDir}/scripts/memanalyze.py snapshot -c <conn> --top 20

# b) 事后：冲高已经过去，读 WLM 历史表
python3 {baseDir}/scripts/memanalyze.py history -c <conn> --top 20

# c) 判泄漏还是尖峰：持续采样
python3 {baseDir}/scripts/memanalyze.py watch -c <conn> --interval 5 --count 12
```

`-c` 可写在子命令**前后皆可**；不写子命令时默认 `snapshot`。
其它参数：`--top`（默认 20）、`--format markdown|json`、`--timeout`（默认 60）。

**退出码**：`0` = 脚本跑成功（**不代表内存没问题**）、`1` = 运行错误、`2` = 连接/配置错误。

### 3.2 六层下钻

六层有**因果顺序**，报告按此顺序读：

| 层 | 回答什么 | 典型确定性发现 |
|---|---|---|
| **L1 实例级** | 冲的是动态/共享/other 内存？峰值回落没？ | `MEM_DYNAMIC_HIGH`、`MEM_PEAK_FALLBACK` |
| **L2 上下文级** | **泄漏/缓存膨胀** 还是 **真在干活**？ | `MEM_CONTEXT_DOMINANT`、`MEM_CONTEXT_FRAGMENT` |
| **L3 会话级** | 内存用在**哪些会话**（用户/应用/状态） | `MEM_SESSION_HOG`、`MEM_SESSION_IDLE_XACT` |
| **L4 SQL 级** | 用在**哪条 SQL** 上 | `MEM_SQL_HOG`、`MEM_SQL_SPILL`、`MEM_SQL_ESTIMATE_OFF` |
| **L5 算子级** | 用在 SQL 的**哪个算子**上 | `MEM_OP_HOG`、`MEM_OP_SPILL`、`MEM_OP_ROWS_OFF`、`MEM_OP_SKEW` |
| **L6 配置面** | 是不是**参数本身**不合理 | `MEM_CONFIG_OVERCOMMIT` |

**L2 是分水岭**：缓存类 context（`CacheMemoryContext`）占大头 → 指向**泄漏/元数据膨胀**；
执行器类 context 占大头 → 指向**真在干活**，继续下钻 L3→L4→L5。这两类根因的**整改方向完全相反**。

L3→L4→L5 是同一条线索的收敛，报告用 `query_id` 串起来。归因时必须讲出整条链：
「动态内存 95% → etl 会话峰值 4.1 GB → query_id 90210 → 算子 #3 Vector Sort 峰值 3.8 GB、下盘 2.5 GB」。

### 3.3 运行时视图探测（兼容 openGauss 与 GaussDB 的核心）

openGauss 与 GaussDB、集中式与分布式、不同版本的内存视图**命名与列集都不一致**，
所以**不硬编码任何视图名**。每层给一个按优先级排序的候选清单，启动时查系统表
（`pg_class` + `pg_attribute`）拿到**视图是否存在以及它真实有哪些列**：

| 层 | 候选视图（优先级从高到低） |
|---|---|
| L1 | `gs_total_memory_detail` → `pv_total_memory_detail` → `dbe_perf.global_memory_node_detail` |
| L2 | `gs_thread_memory_context` / `gs_session_memory_detail` / `gs_shared_memory_detail`（各自有 `pv_` 备选） |
| L3 | `dbe_perf.session_memory` → `gs_session_memory`；恒定叠加 `pg_stat_activity` |
| L4 | `gs_wlm_session_statistics` → `pgxc_…`；历史 `gs_wlm_session_history` → `_info` |
| L5 | `gs_wlm_operator_statistics` → `pgxc_…`；历史 `gs_wlm_operator_history` → `_info` |

拿到真实列集后，采集器**按列名构建查询**，缺失的列填 `NULL`——这样
`gs_wlm_operator_history` 在某些版本没有 `warning` 列也不会让整条 SQL 报错。

**报告开头必须印出探测结果**，让使用者顺便摸清自己的环境：

```
## 能力与视图探测
- L1 实例级   ✓ gs_total_memory_detail (3 列)
- L5 算子级   ✗ 不可用：resource_track_level = query（需设为 operator）[需人工执行]
```

### 3.4 盲区必须自陈原因（本 skill 的核心纪律）

**标 ✗ 的层是盲区，不是「没问题」。** 三种盲法都会说明**为什么**：

| 情况 | 报告怎么说 |
|---|---|
| GUC 没开 | `resource_track_level = query（需设为 operator，否则算子级内存不采集）` |
| 视图不存在 | `候选视图均不存在：gs_thread_memory_context、pv_thread_memory_context` |
| **视图可用但返回 0 行** | 列出三种可能原因，并声明**不能据此认为算子层没有问题** |

第三种最阴险：capability 显示 ✓、表格却是空的，读起来像「算子层无异常」。
**实测 openGauss-lite 5.0.3（单机版）根本不填充算子级视图**（`gs_wlm_operator_*` 恒为 0 行，
即使 `resource_track_level=operator` 也一样）——算子级资源跟踪主要面向分布式 GaussDB。

### 3.5 前置 GUC

| GUC | 默认 | 影响 |
|---|---|---|
| `enable_resource_track` | `on` | 关掉则 L4/L5 全无数据 |
| `resource_track_level` | `query` | **`operator` 才记算子级** |
| `resource_track_cost` | 100000 | 代价低于此值的作业不被跟踪 |
| `resource_track_duration` | 60 | **跑不满该时长的作业不写历史表** |
| `enable_resource_record` | `off` | **开了才写 `gs_wlm_*_info` 持久化历史表** |

这些是**环境限制，不是脚本缺陷**。脚本只报出 GUC 名与目标值并标注 `[需人工执行]`，
**绝不代为修改**。

### 3.6 代码结构

```
skills/gaussdb-memanalyze/
├── SKILL.md
├── references/
│   ├── gaussdb-memory-internals.md   内存架构背景（动态/共享/other、context 树、WLM）
│   └── memory-methodology.md          根因判定树 + 阈值表 + work_mem 调优的克制原则
└── scripts/
    ├── memanalyze.py  215 行  入口：snapshot / history / watch 三子命令
    ├── probe.py       122 行  视图探测 → Catalog（视图名 + 真实列集）
    ├── capability.py  111 行  GUC 探测 → Capability（哪些层可用；不可用的**原因**）
    ├── collectors.py  321 行  L1 实例 / L2 上下文 / L3 会话 / L6 配置
    ├── wlm.py         197 行  L4 SQL / L5 算子（实时与历史共用列自适应逻辑）
    ├── trend.py        75 行  纯函数：采样序列 → 泄漏 / 尖峰回落 / 平稳
    ├── model.py       192 行  Severity / Finding / DimResult / ViewInfo / Capability / …
    ├── thresholds.py   41 行  阈值
    ├── util.py         52 行  单位换算与格式化
    ├── report.py       91 行  MemEvidence → markdown / json
    └── render.py       46 行  vendored 副本
```

数据流：

```
snapshot: probe → capability → [L1 L2 L3 L6] + [L4 L5] ──┐
history:  probe → capability → [L4 L5 历史表]            ─┼→ MemEvidence → report
watch:    probe → 循环采 [L1 L3] ×N → trend.analyze      ─┘
```

**降级纪律**沿用 health：每个采集器 `try/except common.DBError` → `degraded(dim, reason)`，
一层采不到不影响其余五层。`watch` 中途单次采样失败 → 记 note 继续，不中断整个序列。

### 3.7 能力边界

- **`history` 模式下 L1/L2/L3 必然不可用**——它们是实时视图，冲高过去就查不到了。
  这是**事实陈述，不是采集失败**。
- **单次 `snapshot` 无法区分泄漏与尖峰**。要下「泄漏」结论必须跑 `watch`
  （`MEM_TREND_LEAK`）且 L2 显示是缓存类 context 在增长。
- 阈值（`thresholds.py`）可能需要按真实环境校准。

---

## 4. gaussdb-kb —— 用户知识库导入与治理

### 4.1 分工铁律

**确定性工作由脚本做**（格式转换、原文快照、重建索引、校验、检索、契约注入）；
**语义工作由模型做**（条款分类、起草、ID 分配）。

脚本**不会**把 `.txt` 自动变成 `.yaml`——那是语义判断。但**对客户来说是全自动的**：
在 opencode 里说一句「把这份规范导进来」，agent 一路做完，客户全程不碰 yaml。

### 4.2 五个子命令

```bash
# 1) 导入（脚本）：格式转换 + 原文快照 + 标题大纲
python3 {baseDir}/scripts/kb.py ingest 客户规范.docx

# 2) 条款化（模型）：读 source.md，分类写入 rules/ guides/ errata/，然后删掉 inbox/

# 3) 重建索引（脚本）
python3 {baseDir}/scripts/kb.py index

# 4) 校验（脚本）：ID 格式/唯一性、schema、INDEX 一致性
python3 {baseDir}/scripts/kb.py validate

# 5) 检索（脚本）：errata > rules > guides 优先级
python3 {baseDir}/scripts/kb.py search 索引命名

# 6) 契约注入（脚本）：让做判断的 skill 先查知识库
python3 {baseDir}/scripts/kb.py contract            # 先扫描
python3 {baseDir}/scripts/kb.py contract --apply    # 确认后执行
```

**退出码**：`0` = 成功、`1` = 运行错误（格式不支持、转换失败、路径不存在）、
`2` = **有待处理项**（validate 有 error / contract 扫描发现缺契约）。
退出码 2 不是失败，是「有活没干完」。

> 注：`2` 在其余 skill 里表示「连接/配置错误」。kbimport 不连数据库，故不会真正撞车，
> 但跨 skill 阅读退出码时请留意这一处差异。

### 4.3 支持的格式

| 格式 | 怎么转 |
|---|---|
| `.txt` / `.md` / `.sql` | 直接读，**UTF-8 与 GB18030 都支持** |
| `.docx` | 解 zip 抠 `word/document.xml`，纯 stdlib |
| `.doc` | 调系统转换器：macOS `textutil` → `antiword`；都没有时请用户另存格式 |
| `.pdf` | **不支持**，请先转成文本 |

### 4.4 知识库目录结构

```
<kb>/
├── INDEX.md          脚本自动生成，勿手改（模型每次先读它选文件）
├── VERSION           版本号，如 2026.07
├── rules/            机器可判定条款（yaml），稳定 GS-* ID
├── guides/           语义指南（md，frontmatter 带 id/description）
├── errata/           修正与例外（md）—— 查询时优先级最高
├── sources/          原文快照，只读，保证每条都能指回原文
└── inbox/            待条款化的中转区，处理完要删掉
```

**条款 schema**（`rules/*.yaml`，顶层是 list）：

```yaml
- id: GS-IDX-001              # 必填，^GS-[A-Z]{2,4}-\d{3}$，永不复用
  severity: warn              # 必填，error | warn | info
  check: deterministic        # 必填，deterministic | advisory
  rule: 索引名必须以 idx_ 开头  # 必填
  source: 第二章 2.1           # 强烈建议：指回原文，保证可追溯
  keywords: [索引命名, idx_, index naming]   # 3-6 个同义词，让字面检索跨越叫法差异
  criteria: |                 # advisory 条款建议提供：模型的判断依据
    ...
```

`validate` 会校验：ID 格式与唯一性、必填字段、`severity`/`check` 枚举、guides 的
frontmatter、INDEX 与实际文件的一致性、inbox 是否还有未处理项。

### 4.5 代码结构

```
skills/gaussdb-kb/
├── SKILL.md
├── references/
│   ├── kb-contract.md    注入各 SKILL.md 的契约块模板（用户可编辑）
│   └── kb-layout.md      条款格式与 ID 规范（模型条款化时必读）
└── scripts/
    └── kb.py  641 行   五个子命令，纯 stdlib + PyYAML，**不连数据库**
```

单文件，按职责分区：`kb layout`（路径解析/骨架）、`ingest`（格式转换）、
`shared parsing`（frontmatter/yaml）、`index`、`validate`、`search`、`contract`、`main`。

### 4.6 契约注入的安全性

`contract --apply` 会**写别的 skill 的 SKILL.md**，所以有两条硬约束：

1. **只改 `KB-CONTRACT` 标记区内的内容**，标记区外一字不动。
2. **标记区损坏时（BEGIN 没有对应 END，或有多对）拒绝写入并报错**，跳过该文件。
   定位用**纯索引**（`block_span()`），不用正则——早期版本用 `BEGIN.*?END` 惰性匹配，
   遇到残缺标记会从孤立的 BEGIN 一路吃到末尾的 END，**把中间的正文一起删掉**。

### 4.7 能力边界

- 条款分类是**模型**的语义判断，不是脚本判定——写入前须经用户确认，且每条都要能指回原文。
- `search` 是**关键词匹配，不是语义检索**；没命中不代表库里没有相关内容。
- `.doc` 依赖系统转换器；PDF 不支持。

---

## 5. 知识库落点与优先级链（跨 skill）

### 5.1 落点：与 skill 装在一起

```
--kb <目录>  >  $GSDB_KB_DIR  >  <安装根>/kb
```

`<安装根>` 从脚本自身位置推导（`skills/<name>/scripts/x.py` 往上找到 `skills` 的父目录）：

| 安装方式 | 知识库落点 |
|---|---|
| 全局安装 | `~/.config/opencode/kb/` |
| 项目安装 | `<项目>/.opencode/kb/` |
| 源码仓直跑 | `<仓库>/kb/`（已 gitignore，客户规范不入库） |

**客户零配置。** 项目级安装天然得到项目级知识库，不同客户项目各带各的规范。

> ⚠️ **知识库在 `skills/` 的同级目录，绝不能放进 `skills/<name>/` 内部。**
> `install-opencode.sh` 每次重装都 `rm -rf` 整个 skill 目录，放里面会被删光。
> 同级目录 install 从不触碰（已实测：重装 13 个 skill 后知识库完好）。

### 5.2 优先级链

```
本 SKILL.md 与 references/ 的内容  >  用户知识库  >  模型自带知识
```

**知识库是「参考」，不是「指令」。** 它管「客户的规范条款说了什么」，管不着「skill 怎么工作」：

- **不能**推翻 SKILL.md 的工作流与证据锚定纪律；
- **不能**推翻 `references/` 里的方法论、阈值与规则基线（gaussdb-sqlreview 的 14 条内置规则即在此列）；
- **不能**推翻脚本的确定性判定——脚本没报的违规，模型不得凭知识库补报；
  脚本报了的，不得凭知识库抹掉。

两边不一致时：**如实并列呈现，说明差异，交用户裁决**，不自行选边。

### 5.3 注入范围

| 注入（会做规范/阈值判断） | 不注入（纯取数） |
|---|---|
| `gaussdb-sqlreview`、`gaussdb-health`、`gaussdb-wdr`、`gaussdb-memanalyze`、`gaussdb-sqltune`、`gaussdb-proctune` | `gaussdb-slowsql`、`gaussdb-topsql`、`gaussdb-sqlfetch`、`gaussdb-explain`、`gaussdb-topproc`、`gaussdb-procinfo` |

纯取数的 skill 只是列个表、把 `sql_id` 还原成文本，要求它们「先查知识库再作答」只是噪音与延迟。

### 5.4 一个已知的空白

客户导入的规范里若有一条 `rules.yaml` **没覆盖**的（比如「表名必须 `ods_`/`dwd_` 前缀」），
**gaussdb-sqlreview 的脚本不会自动检查它**——模型会读到该条款并在报告里指出差异，但那是模型的提醒，
不是脚本的确定性发现。

这是 §5.2 优先级原则的**必然结果**，不是实现缺陷。要让客户规范真正被脚本执行，
需把该条编入 `references/rules.yaml`。

---

## 6. 测试

三个 skill 共 **120 个 DB-free 单测**（另有 2 个 gaussdb-sqlreview live 测试，无库时自动 skip）：

| 测试文件 | 用例 | 重点覆盖 |
|---|---|---|
| `tests/test_sqlreview_units.py` | 41 | lexer（注释/字面量/切句/行号）、rules 加载校验、每个 checker 的命中与不命中、report |
| `tests/test_memanalyze_units.py` | 45 | probe 视图选择与列自适应、capability 的 GUC 判定、trend 泄漏/尖峰/平稳、会话关联、采集器降级、CLI 参数解析 |
| `tests/test_kb_units.py` | 34 | 契约注入的幂等与**标记损坏时拒写**、模板反斜杠、GBK 编码、`.doc` 超时、rule schema 校验、KB 路径推导 |
| `tests/test_sqlreview_live.py` | 2 | **方言 SQL 必须能在真库上跑通**——FakeDB 单测原理上抓不到方言语法错误 |

跑法：

```bash
python3 -m pytest -q -m "not live"    # 单测（不需要数据库）
python3 -m pytest -q -m live          # 实机测试（无连接时自动 skip）
```

> `test_sqlreview_live.py` 之所以存在，是因为单测漏掉过一个真 bug：索引采集用了
> `WITH ORDINALITY`（PostgreSQL 9.4+），而 openGauss 基于 9.2，整条查询语法错、
> 索引层静默变成盲区。**mock 掉数据库的单测，原理上就抓不到方言 SQL 错误。**

---

*文档生成于 2026-07-13，对应 gaussdb-sqlreview 1.0.0 / gaussdb-memanalyze 1.0.0 / gaussdb-kb 2.0.0(见 docs/delivery/09-客户知识库.md)。*
