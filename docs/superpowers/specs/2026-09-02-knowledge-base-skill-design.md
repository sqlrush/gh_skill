# 知识库 skill 设计方案 v3：让模型优先按客户经验处理 GaussDB 问题

> 2026-09-02，v3（v2 之上改存储层：向量库 = openGauss DataVec / PostgreSQL pgvector，图库 = Neo4j）。状态：**待 user 评估，未开码**。
> 目标（user 原话汇总）：做一个 skill，驱动模型分析客户的**工单**和**规范**；**关键数据写入之前生成选择列表让用户选择并最终确认**；
> 数据进**向量库 + 图库**；之后既有 gaussdb-* skill 处理问题时**优先按知识库里的客户经验**，而不是模型的通用解法。
> 验收效果：**导入前**，模型用原生能力处理问题（基线）；**导入后**，同样的问题模型优先按知识库经验处理，且能说出依据。
> 参照 `~/dsh-k8s`（opendb-harness）的知识库**实现方案**（不借它的 k8s 架构、不用它的工单和知识内容）。

---

## 0. 一页结论

| 问题 | 结论 | 代价 / 边界 |
|---|---|---|
| 向量库 | **高斯向量库，开源版 = openGauss 7.0 DataVec**（`vector(n)` 类型、HNSW/IVF 索引、`<=>` 余弦）；没有 7.0 的环境用 **PostgreSQL + pgvector**。两者 SQL 同形，`common/kb/store_pg.py` 一套代码，启动时探测引擎 | 客户要为知识库**单独**准备一个 openGauss 7 / PG 实例（不能用被管库，见 §2.4）；本机测试库 og5 是 5.0.3 没有向量，要另起 og7 容器 |
| 图库 | **Neo4j**（社区版即可）。经 HTTP Query API（`/db/neo4j/query/v2`）跑 Cypher，urllib 实现，**不加 pip 依赖** | 客户要部署 Neo4j（Java）；社区版单实例够用，企业版才有集群/向量优化 |
| 向量放哪 | **全部放高斯/PG**：切块向量 + **节点向量**（symptom/rootcause/action 文本）各一张表。Neo4j 只存图，不依赖它的向量索引（社区版很可能没有） | 查询时"向量命中节点 → 拿 id 去 Neo4j 走边"两次往返，毫秒级 |
| 真相在哪 | 仍是 `<kb>/` 下的文件（案例 md / 条款 yaml / 三元组 yaml）。两个库都是**可重建的派生索引**（`kb index --rebuild`） | 库坏了、换引擎、迁移，都不丢知识；选择列表/出处回指都在文件层做 |
| 模型从哪来 | **就是 OpenCode 配置的模型**（user 定）。分析在会话里由它做；**脚本不调任何 LLM**。embedding 复用 **OpenCode 自己的 provider 端点**（见 §2.2，与 opendb-harness 无关） | 该 provider 必须能提供一个嵌入模型；不能就降级词法 + 图，状态行标"向量：未启用" |
| 关键数据怎么确认 | **写入前一律出选择列表**：模型分析 → 脚本校验并渲染编号清单（案例字段 / 实体 / 边 / 条款）→ 用户按编号接受、改、拒 → 脚本写文件 → 索引进库 | 每批一张清单；可"全部接受"，但**边没有默认接受** |
| 模型怎么"优先按知识库" | 三层（§5）：① 脚本层——诊断 skill 算完 findings 自己查库，命中写进输出固定小节；② 会话层——AGENTS.md 最高优先级规则 + 契约要求回答前先 `kb query`；③ 检索层——向量找"像"的现象，Neo4j 给"现象→根因→处置"的可验证路径 | ① 可机械验证；② 靠提示词，金丝雀抽查 |
| 怎么证明效果 | §8 三态对照：导入前通用解法 / 导入后引用案例并按案例处置（5 个金丝雀全中）/ 删库回退 | 检索层全自动，模型作答层半自动 |
| skill 阈值 / 命名 | 阈值**不变**；`kbimport` 升 2.0 改名 **`kb`**（部署名 `gaussdb-kb`）整体替换 | — |

---

## 1. 从 opendb-harness 借什么、避什么

**借（设计定论 + 你在那边定过的口径）**：三库分工"PG 管'是什么、归谁、能不能引用'，向量管'像什么'，图管'和什么有关'"；
公共 DBA 知识不建图，图只存**客户专属、强类型、带出处、带置信度、带生效期**的关系，"上了图库但推不出可信路径"是最差档；
**模型 propose，系统 + 人 dispose**；检索按发现触发、不指望模型想起来查；引用必带出处、查不到写"无对应"、绝不编；
embedding 列可空、文本先落库；检索有超时预算、失败只降级不抛。

**避（它实测出的坑）**：一次性 embed 超时整篇 NULL 且无补齐任务（覆盖率 14%）→ 逐块 embed + 哈希缓存 + `--fill-missing` + 覆盖率门；
"图"只是共现表、一个枢纽 832/878 边 → 强类型边、**无共现边**；实体抽取 ASCII 正则 → 交给模型；`knowledge_search` 从未接进报告 →
脚本层接入是主路径；检索无阈值 → 分类型阈值；同源重灌覆盖 → 版本化。

它当时把图放 PG 边表、判"Neo4j 先不上"，是因为它那边规模小且不想多一个服务；user 这次明确要 Neo4j，本方案照做，
但把它的红线带过来：**边必须强类型 + 出处 + confidence + 生效期，路径只走 confidence=1.0 的边**。

---

## 2. 约束

1. **运行形态是 OpenCode skill**：无常驻进程、无钩子、无 UI。SKILL.md（契约）+ `scripts/*.py`（确定性）+ 模型在会话里做语义工作。
2. **模型 = OpenCode 配置的模型，脚本不调 LLM。** embedding 的"复用 OpenCode provider 端点"意思是：OpenCode 自己的配置
   （`~/.config/opencode/opencode.json` 的 provider 段）里有它调聊天模型用的 base URL 和 key 环境变量；`kb.yaml` 写
   `embeddings.source: opencode` 时脚本读同一个 base URL，向 `/v1/embeddings` 请求 `embeddings.model` 指定的嵌入模型。
   **这与 opendb-harness 无关**——那边是 k8s 集群里的 Ollama + bge-m3，我们不碰。前提是这个 provider 上确实挂了嵌入模型
   （Ollama / vLLM / Xinference 都能顺道挂；纯聊天 API 就没有），没有就降级。脚本只读配置、不打印 key。
3. **依赖白名单只有 pg8000 / cryptography / PyYAML**。⇒ 高斯/PG 走 pg8000（现有 skill 就在用）；Neo4j 走 HTTP（urllib）；
   词法/融合/渲染标准库；numpy 有则加速、无则纯 Python。**不新增 pip 依赖。**
4. **知识库不能放被管库**：现场被管 GaussDB 经 GRMP 白名单，每条 SQL 都是发布 DML。⇒ 客户为知识库单独准备一个
   openGauss 7 / PG 实例 + 一个 Neo4j，skill 直连（连接信息与口令走现有 `common.credential` 加密凭据机制，连接名 `kb` / `kb-graph`）。
   **单机一套、所有 skill 与会话共用**（user 定）；不考虑 k8s / 多副本 / 租户。
5. **安全**：`ingest --redact` 确定性脱敏（IP / 手机号 / 证件号）；`<kb>/` 0700；口令只在 `~/.gdaa/credentials/*.enc`。
6. **skill 现有阈值保持不变。**

---

## 3. 总体结构

```
<kb>/                                   文件 = 唯一真相
  kb.yaml                               存储连接名（无口令）、embedding 来源/模型/维度、检索阈值、默认元数据
  VERSION  INDEX.md  RULES.md           kbimport 1.2 已有
  rules/ guides/ errata/ archive/       规范条款化产物（已有，不动）
  sources/  inbox/                      原文快照（带版本）/ 待处理（工作单、候选、选择列表、决定）
  cases/S1-日期-系统-标题.md            ★案例（§4.1）
  graph/<source-slug>.yaml              ★强类型三元组
  graph/canonical.yaml                  ★实体别名表
  eval/queries.yaml  eval/feedback.yaml ★黄金查询集 / DBA 反馈
  index/state.json  index/misses.log    ★索引水位（哪些文件已进库）/ 查不到的发现

openGauss 7 (DataVec) 或 PG (pgvector)  ← 派生：kb_docs / kb_chunks(lex, embedding) / kb_node_vectors / kb_meta
Neo4j                                   ← 派生：节点标签 = kind，关系类型 = rel，属性 confidence/source/valid_*

common/kb/                              ★所有 skill 共用（纯标准库 + pg8000）
  config.py     读 kb.yaml + OpenCode provider 配置 + 凭据
  store_pg.py   高斯/PG：建表（探测 vector 类型与 hnsw）、写块、向量/词法查询、重建
  store_graph.py Neo4j HTTP Query API：MERGE 节点/边、有界遍历、重建；不可达即降级
  text.py       归一化、中文二元组 + 标识符分词（预分词后交给 tsvector 'simple'，两种引擎通用）
  embed.py      OpenAI 兼容 /v1/embeddings 客户端、内容哈希缓存、逐块失败隔离、超时
  query.py      混合检索编排（词法 ∥ 向量 ∥ 图扩展 → RRF → 分类型 top-k → 阈值）
  render.py     「客户知识库参照」小节 + 状态行

skills/kb/（部署名 gaussdb-kb，取代 kbimport）
  SKILL.md
  scripts/kb.py   ingest | propose | review | apply | validate | index | query | health | feedback | eval | contract | setup
  references/     kb-layout.md  case-format.md  graph-schema.md  selection-list.md  kb-contract.md  storage-setup.md
```

数据流：

```
规范文档 ─ingest─▶ inbox/source.md ─模型条款化─▶ propose 候选条款 ─review 选择列表─▶ apply ─▶ rules/guides/errata
工单/报告 ─ingest─▶ inbox/<slug>/items/*（原文同时以 raw 进高斯/PG，当天可检索）
                 ─propose─▶ 脚本出工作单 → 模型逐条填候选（案例字段 + 实体 + 边，每项附原文摘录）→ candidates.json
                 ─review──▶ 脚本校验（schema / 出处回指 / 实体归一）→ 编号选择列表 → 用户选 → decisions.yaml
                 ─apply───▶ cases/*.md + graph/*.yaml（用户接受的边 confidence=1.0）
                 ─index───▶ 高斯/PG（切块 + 二元组 + 向量 + 节点向量）+ Neo4j（节点 + 边）
诊断 skill 脚本 ─findings─▶ common.kb.query.from_findings() ─▶「客户知识库参照」小节 ─▶ 模型作答
用户直接提问 ─▶ 模型先跑 kb.py query --q ─▶ 同一格式小节 ─▶ 作答
```

---

## 4. 数据模型

### 4.1 案例（`cases/S1-日期-系统-标题.md`）

格式参考 prd 照片里 gaussdb-rootcause 的 S1 文件（现场 / 判断 / 处置 / 复发标志 + 头部字段），不必完全对齐客户文档（user 定），
照片没拍到的字段由我们补齐：

```markdown
---
id: S1-20250224-CBST-偶现单条update慢      # = 文件名，永不复用
title: 偶现单条 update 走索引耗时 3s
system: CBST
instance: 未知
occurred_at: 2025-02-24
engine: gaussdb
severity: S1
primary_factor: autovacuum 尾部空页回收持 8 级锁与 DML 互相 cancel
secondary_factors: []
conclusion: 已确认           # 已确认 | 推测 | 待验证 → 边 confidence 1.0 / 0.6 / 0.3
source: sources/20250224-CBST-问题分析报告.v1.docx#前言
entered_by: <工号>
entered_at: 2026-09-02
objects: [cbst.cosp_asyn_task_dtl, autovacuum_vacuum_threshold]
signals: [单条 update 偶发秒级, autovacuum 频繁触发, 小表尾部空页]      # 复发标志 → 匹配信号
---
## 现场
## 判断
## 处置
## 复发标志
```

| 小节 | 向量（高斯/PG） | 图（Neo4j） |
|---|---|---|
| 现场 | 切块 + 现象节点向量 | `(:Case)-[:EXHIBITS]->(:Symptom)` |
| 判断 | 切块 + 根因节点向量 | `(:Symptom)-[:CAUSED_BY]->(:RootCause)`；对象/GUC/等待事件/错误码 → `(:Case)-[:INVOLVES]->(…)` |
| 处置 | 切块 + 处置节点向量 | `(:RootCause)-[:HANDLED_BY]->(:Action)` |
| 复发标志 | 信号块 | Symptom 节点属性 `signals` |
| 头部 | `kb_docs.meta`（system / occurred_at / conclusion…）过滤 | `conclusion` → 本案例所有边的 `confidence`；`source` → 边属性；`secondary_factors` → 打折的 CAUSED_BY |

### 4.2 图（Neo4j）——只收强类型边，文件 `graph/*.yaml` 是真相

标签 = 节点 kind：`Object` `Symptom` `RootCause` `Action` `Clause` `Case` `Error` `WaitEvent` `Guc` `Component`；
关系类型：`EXHIBITS` `CAUSED_BY` `HANDLED_BY` `INVOLVES` `CONSTRAINS` `REFERENCES` `DEPENDS_ON`。**没有共现关系。**
节点属性：`id`（= canonical）、`name`、`aliases`、`kb_version`；关系属性：`confidence`、`source`、`valid_from`、`valid_to`、`case_id`。
`MERGE` 按 `id`，重建幂等。

```yaml
- src: {kind: symptom, name: 单条 update 偶发秒级, canonical: symptom:update_sporadic_slow}
  rel: caused_by
  dst: {kind: rootcause, name: autovacuum 尾部回收持 8 级锁与 DML 互 cancel}
  confidence: 1.0            # 用户在选择列表里接受 = 1.0；未接受的边保留 <1，不入路径
  source: cases/S1-20250224-CBST-偶现单条update慢.md#判断
  valid_from: 2025-02-24
  valid_to: null
```

路径查询（有界 2–3 跳，只走已确认且生效的边）：

```cypher
MATCH p = (s:Symptom)-[:CAUSED_BY]->(r:RootCause)-[:HANDLED_BY]->(a:Action)
WHERE s.id IN $symptom_ids
  AND ALL(e IN relationships(p) WHERE e.confidence >= 1.0 AND (e.valid_to IS NULL OR e.valid_to > date()))
OPTIONAL MATCH (c:Case)-[:EXHIBITS]->(s)
RETURN s.name, r.name, a.name, collect(DISTINCT c.id) AS cases, [e IN relationships(p) | e.source] AS sources
```

### 4.3 高斯/PG 侧（DataVec 与 pgvector 同一套 DDL）

```sql
CREATE TABLE kb_docs   (id text PRIMARY KEY, kind text, title text, source text, version text,
                        meta jsonb, content_hash text, created_at timestamptz DEFAULT now());
CREATE TABLE kb_chunks (id text PRIMARY KEY, doc_id text REFERENCES kb_docs(id) ON DELETE CASCADE,
                        seq int, section text, content text, content_hash text,
                        lex tsvector,                      -- Python 预分词（二元组 + 标识符）后 to_tsvector('simple', …)
                        embedding vector(1024));           -- 可空；无 vector 类型的引擎建表时省略此列并记 kb_meta.vector_engine=none
CREATE INDEX ON kb_chunks USING gin (lex);
CREATE INDEX ON kb_chunks USING hnsw (embedding vector_cosine_ops) WITH (m = 16, ef_construction = 64);
CREATE TABLE kb_node_vectors (node_id text PRIMARY KEY, kind text, name text, lex tsvector, embedding vector(1024));
CREATE INDEX ON kb_node_vectors USING hnsw (embedding vector_cosine_ops);
CREATE TABLE kb_meta (key text PRIMARY KEY, value text);   -- schema 版本、引擎、嵌入模型/维度、覆盖率、索引水位
```

`store_pg.py` 启动探测：`SELECT 1 FROM pg_type WHERE typname='vector'` → 有则全功能；无则退化为"词法 + 图"并在状态行标明；
HNSW 建索引失败（旧版本）退化为顺序扫描但**打印告警**（不学 opendb-harness 静默吞掉）。
查询：`ORDER BY embedding <=> $1::vector LIMIT k`（DataVec 与 pgvector 同一写法）；词法 `lex @@ to_tsquery('simple', $1)` + `ts_rank`。

**为什么向量不放 Neo4j**：Neo4j 向量索引是 5.11+ 特性且产品页把"向量优化"列为企业版功能，社区版能否用有分歧；
高斯/PG 这边向量是确定的，两库各司其职最稳。要是现场是企业版，`store_graph.py` 留了开关把节点向量也同步进 Neo4j 向量索引，不是必需。

---

## 5. 模型怎么"优先按知识库经验"——三层机制

### 5.1 脚本层（主路径，可验证）

有 findings 的 skill（health 及 lockwait/waitevent/vacuum、sqltune、sqlreview、proctune、wdr、memanalyze）在脚本里调
`common.kb.query.from_findings(findings)`，把命中作为**固定小节**写进输出并随报告落盘（`kb_refs`）：

```markdown
## 客户知识库参照
> 知识库 v2026.09 · 条款 12 · 案例 24 · 向量：DataVec（覆盖 100%）· 图：Neo4j 61 条已确认边

### 对 🟠 VAC_FREQ（cbst.cosp_asyn_task_dtl autovacuum 次数/h = 37）
- **贵行规范** GS-VAC-002《小表 autovacuum 阈值》：行数 < 1 万的热表按表级调大 threshold（建议）——《运维规范》v5 §6.2
- **历史相似** S1-20250224-CBST-偶现单条update慢（结论强度：已确认）：同表同现象，处置 = 调大 autovacuum_vacuum_threshold，效果 = 偶发慢消失
- **本行历史路径** 单条 update 偶发秒级 → autovacuum 尾部回收持 8 级锁与 DML 互 cancel → 表级调大 threshold（2 案例支持）
- **建议·本行口径** 表级 ALTER … SET (autovacuum_vacuum_threshold=…)，走变更单，低峰窗口

### 对 🟡 IDX_UNUSED（未使用索引 3 个）
- 贵行规范：无对应条款 · 历史相似：无相似案例 · 路径：无
```

每条 finding 都有一段；没命中明写"无"；引用带 ID + 出处 + 版本；不做顶部"违规汇总"；知识库不可达时整节只剩
`> 知识库未接入（原因：…）`。模型在**已经看到客户经验**的前提下作答，"有没有看到"可以断言。

### 5.2 检索层：向量给入口，Neo4j 给链路

1. **命中现象**：finding 的 code + metric + evidence 里的对象/等待事件/GUC/错误码 + finding 文本 → 在 `kb_node_vectors`
   上做向量 + 词法（Symptom 节点，signals 加权）；同时在 `kb_chunks` 上召回原文（案例四节、条款、原始工单）。
2. **图扩展**：命中的 Symptom / 实体 id → Neo4j 有界 2–3 跳（§4.2 的 Cypher），取 RootCause → Action → Case，统计"N 案例支持"。
3. **融合与阈值**：RRF → 分类型 top-k（条款 3 / 案例 3 / 路径 2）→ 分类型分数阈值，低于阈值整类"无"。
4. **预算与降级**：整次 ≤ 3 s；embedding 1.5 s 超时则本次不用向量；Neo4j 不可达则无路径、状态行标"图：不可用"；
   高斯/PG 不可达则整节"未接入"。任何失败降级不抛，skill 本身照常。

只有向量 → 找得到"像"的工单，给不出"根因→处置"的可验证链，模型仍会自己归纳；只有图 → 问法与原文用词不同就命不中入口。

### 5.3 会话层（纯问答路径）

- 现场 `AGENTS.md` 第一条"回答任何 GaussDB 问题前必须优先检索本地知识库"的命令换成 `python3 <skills>/gaussdb-kb/scripts/kb.py query --q "<问题>"`。
- `KB-CONTRACT` 块升级（`kb.py contract --apply` 幂等注入 9 个 skill）：知识库结果是处置建议的**首选依据**（客户先例 > 通用经验）、
  引用必带案例/条款 ID + 出处；**不改判**；矛盾时并列呈现；查不到如实说"本行无先例，以下为通用做法"；**绝不编**；未接入不提。
- 这层靠提示词，用 §8 金丝雀抽查。

---

## 6. 导入流程：模型分析 → 选择列表 → 确认 → 写入

| 子命令 | 脚本（确定性） | 模型（会话里） |
|---|---|---|
| `setup` | 探测/建表（高斯/PG）、建约束（Neo4j `CREATE CONSTRAINT … id IS UNIQUE`）、写 `kb_meta`；打印引擎与能力（向量有无、hnsw 有无） | — |
| `ingest <file…> [--redact]` | 快照到 `sources/`（同名升版）；txt/md/docx/doc/pdf（已有）+ **csv/xlsx**（标准库）；扫描件/乱码拒收；一单一文件到 `inbox/<slug>/items/`；原文以 `raw` 进库 | — |
| `propose <slug>` | 出工作单：原文 + 案例 schema + canonical 实体节选 + 首次导入该类材料时 3–5 个策略问题（引擎、默认级别、命名习惯） | **逐条填候选** `candidates.json`（案例字段 + 四节 + objects/signals + 候选边，**每项附原文摘录**）；每轮 5–10 单可续跑 |
| `review <slug>` | 校验候选（schema、**出处回指**、ID 唯一、实体归一建议）→ 渲染**编号选择列表** `review.md`（§6.1） | 原样呈现清单，收回答写 `decisions.yaml` |
| `apply <slug>` | 按 `decisions.yaml` 写文件：案例进 `cases/`、接受的边 confidence=1.0、拒绝的边标 `rejected`、实体合并进 `canonical.yaml` | — |
| `validate` | kbimport 1.2 全部校验 + 案例/边 schema + 出处回指 + UTF-8 | 修 `[error]` |
| `index [--fill-missing] [--rebuild]` | 按 `index/state.json` 增量：切块 → 二元组 → 逐块 embed（哈希缓存、失败隔离）→ 高斯/PG；节点 + 边 → Neo4j `MERGE`；节点向量 → `kb_node_vectors`；覆盖率 < 100% 退出码 2；`--rebuild` 清两库重灌 | — |
| `query --q / --from-findings` | §5.2 | — |
| `health` | 文本大盘：两库连通性与引擎、条款/案例/原始工单/边数、向量覆盖率、待决项、过期条款、缺口清单（`misses.log` Top-10） | 汇报 |
| `feedback <ID> --useful\|--irrelevant` | 采纳率加权（不上 learning-to-rank） | 代填 |
| `eval` | 黄金集 recall@k + 金丝雀检查 | — |
| `contract [--apply]` | 既有 | — |

退出码：`0` 完成 / `1` 运行错误（含存储不可达）/ `2` 有待处理项。

### 6.1 选择列表（`review.md`，写入前的唯一闸门）

按材料批量呈现、逐条可否决；每项带原文摘录；**边没有默认接受**：

```markdown
# 导入确认：tickets-2025Q1（2 单，候选 2 案例 / 7 实体 / 6 边）
回复格式：`接受 1-9,12` / `拒绝 10` / `改 3: 严重级别=S2` / `全部接受（边除外）`

## 案例 A  S1-20250224-CBST-偶现单条update慢
 1. [字段] severity = S1                      摘录:"…影响核心交易…"          建议:接受
 2. [字段] conclusion = 已确认                 摘录:"结论强度: 已确认"          建议:接受
 3. [实体] object cbst.cosp_asyn_task_dtl      摘录:"针对cbst.cosp_asyn_task_dtl小表"
 4. [实体] guc autovacuum_vacuum_threshold
 5. [归一] 「异步任务明细表」≈ cbst.cosp_asyn_task_dtl ?   相似度 0.82    建议:合并
 6. [边] 单条update偶发秒级 —CAUSED_BY→ autovacuum尾部回收持8级锁      摘录:"…持有8级锁…"   ⚠ 无默认
 7. [边] autovacuum尾部回收持8级锁 —HANDLED_BY→ 表级调大threshold        摘录:"…调大autovacuum_vacuum_threshold…"   ⚠ 无默认
## 案例 B  …
```

`apply` 只认 `decisions.yaml` 里明确写了的编号；未决的边不入路径、未决的案例不入 `cases/`。

---

## 7. 接入既有 skill

| skill | 接入点 | 输入 |
|---|---|---|
| health（含 lockwait / waitevent / vacuum） | `report.py` 两段固定小节之后、维度正文之前 | 全部 findings |
| sqltune / sqlreview / proctune | 结论前 | findings + SQL 涉及的表/索引/GUC |
| wdr / memanalyze | findings 之后 | findings |
| explain / slowsql / topsql / sqlfetch / topproc / procinfo | 不接（纯取数） | — |

每个 ≈ 10 行：`from common.kb import query, render` + 一次调用 + 小节插入。不改判定逻辑与阈值。

---

## 8. 验收：导入前 / 导入后 / 删库回退

### 8.1 环境（Mac Docker）

- `opengauss/opengauss-server:7.0.0-RC2.B015`（DataVec）——og5 保留给 skill 自身测试，og7 只做知识库；
- `neo4j:5-community`；
- 端口在 OrbStack 上先探（pgrac 虚拟机占过 5433–5436）；
- 也用一个 PG16 + pgvector 容器跑一遍，证明"同一套 DDL/SQL 两种引擎通用"。

### 8.2 语料（我造）

24 条案例（vacuum / 锁等待 / 等待事件 / 统计信息 / 索引 / 参数 / 分区 / 存储过程 × 3）+ 1 份规范（12 条款）+ 3 条干扰案例；
其中 **5 个金丝雀**：客户做法与通用做法**相反**（小表偶发 update 慢 → 调**大** `autovacuum_vacuum_threshold`；
分区表 exchange 后应用代码显式 ANALYZE；长事务先查批量调度表而非 kill；…）。

### 8.3 三态协议

同一组 12 个问题（6 纯问答 + 6 由 health/sqltune 跑出 findings）：

| 状态 | 期望 |
|---|---|
| **导入前**（两库为空或 `<kb>/` 不存在） | 通用解法；输出无小节或只有"未接入"行 |
| **导入后** | 脚本层：小节出现、命中正确案例 ID（自动断言）；模型层：处置与案例「处置」一致、引用 ID（脚本判 ID 存在 + 人看措辞）；5 金丝雀全按客户做法 |
| **删库回退**（`index --rebuild --empty` 或移走 `<kb>/`） | 回到导入前——证明差异来自知识库 |

自动化：`tests/test_kb_*.py`（text/query/render 纯单测；store_pg 用 docker og7/pg16；store_graph 用 docker neo4j，跳过条件明确写"存储不可达"而不是静默 skip）；
fixture KB 上金丝雀 recall@3 = 1.0；每个接入 skill 的渲染函数必含小节或"未接入"行。**单测绿不算完，三态要真跑。**

---

## 9. 质量守卫

1. 接入不可静默：渲染函数断言必含小节或"未接入"行；存储不可达要写原因。
2. 引用必可验：渲染出的每个 `S1-* / GS-*` 都在库里；模型层靠契约 + 金丝雀。
3. 向量不假绿：覆盖率 < 100% 退出 2；hnsw 建失败打告警；状态行常显引擎与覆盖率。
4. 图不掺假：路径只走 confidence=1.0 且生效的边；未确认边只在"候选"标签下；无共现边；Neo4j `MERGE` 幂等。
5. 检索不凑数：分类型阈值 + 显式"无"；黄金集进 CI。
6. 出处不可断：review 回指检查；ID 永不复用；换版走 `archive/`；两库随时可从文件重建。
7. 矩阵：numpy 有/无 × 向量引擎 DataVec/pgvector/无 × Neo4j 可达/不可达。

---

## 10. 分期

**P1（约 2–3 周，可演示三态对照）**
- `common/kb/`（config / store_pg / store_graph / text / embed / query / render）；`setup`；案例格式与 validate；csv/xlsx ingest；
  `propose / review / apply` 选择列表闭环；`index` 双库写入与重建；kbimport → kb 改名替换；契约块升级；
- health 与 sqltune 接入；
- Mac 上 og7 + neo4j + pg16 三个容器；语料 24 案例 + 1 规范；三态协议真跑；金丝雀 recall@3 = 1.0。

**P2**：其余 7 个 skill 接入；`feedback`；`health` 缺口清单；AGENTS.md 检索命令切换；节点向量与 signals 调优；Neo4j 企业版向量索引开关。

**P3（可选）**：`DEPENDS_ON` 拓扑导入（机器读机器）；运行记忆；多机共享（两库本来就是网络服务，只差凭据分发）。

---

## 11. 决策记录

| 日期 | 决策（user） | 落点 |
|---|---|---|
| 09-02 | 向量库用高斯向量库，开源版没有就先用 PG 向量引擎 → **openGauss 7 DataVec / pgvector 同一套代码** | §0 / §4.3 |
| 09-02 | 图库用 **Neo4j** | §0 / §4.2 |
| 09-02 | 模型 = OpenCode 配置的模型；脚本不调 LLM；embedding 复用 OpenCode 自己的 provider 端点（非 opendb-harness） | §2.2 |
| 09-02 | kbimport 升 2.0 改名 `kb`，旧的全部替换 | §3 |
| 09-02 | 关键数据写入前生成选择列表让用户确认 | §6.1 |
| 09-02 | 案例格式参考 S1，不必完全对齐客户文档 | §4.1 |
| 09-02 | 单机一套知识库共用；不考虑 k8s | §2.4 |
| 09-02 | skill 阈值保持不变 | §0 |
| 09-02 | 只借鉴 opendb-harness 的知识库实现方案 | §1 |
| 09-02 | 工单自己造；验收 = 导入前后对照 | §8 |
