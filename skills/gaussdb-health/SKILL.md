---
name: gaussdb-health
version: 2.0.0
description: "通过内置脚本执行 OpenGauss/GaussDB 数据库健康检查。用户询问库是否健康、为什么慢或卡、是否有阻塞、长事务、膨胀、复制延迟、checkpoint 压力、归档问题、空闲会话、连接压力或无用索引时使用，包括“库健康吗”“为什么卡”“有没有阻塞”“有没有长事务”“有没有膨胀”“复制有没有延迟”等请求。触发后运行 scripts/health.py，采集真实证据和确定性发现，不要只给泛化排查建议。"
allowed-tools: ["exec", "read"]
compatibility: opencode
metadata:
  runtime: python3
  emoji: "🏥"
  family: diagnostics
---

# Health Check（OpenGauss/GaussDB 数据库健康检查）

一次性、只读、可信的数据库健康检查。**确定性归脚本（采集 + 阈值发现），判断归你（LLM），但你的判断必须对脚本的 `## Deterministic Findings` 做证据锚定校验。** 报告抬头用确定性状态带，不编造单一分数。

本技能用 Python 脚本（`{baseDir}/scripts/health.py`）取数：只读、一次性跑全部 12 个采集器。连接元数据读取 `$GSDB_HOME/config.yaml`（默认 `~/.gdaa/config.yaml`），凭据由脚本从 `{baseDir}/../common/credentials/` 自动解密。

命中以下请求时，必须使用本 skill 并实际执行脚本，不要只做概念解释：

- 用户要判断数据库是否健康
- 用户要排查卡顿、阻塞、长事务、膨胀、复制延迟、checkpoint 压力
- 用户要一次性看整体健康检查结果，而不是只看单一指标

典型触发语句：

- 库健康吗
- 为什么卡
- 有没有阻塞
- 有没有长事务
- 有没有膨胀
- 复制有没有延迟

## 工作流

1. **选择连接。** 连接名沿用 `$GSDB_HOME/config.yaml`（默认 `~/.gdaa/config.yaml`） 的 `name` 字段。若不确定有哪些连接，看 name 列表；只在有多个时才问用哪一个。该文件**只含连接元数据，无密码**——口令在 `{baseDir}/../common/credentials/*.enc`，由脚本解密，**你不要去读/解密它**。
2. **采集证据——一条命令，中途不停。**

   ```bash
   python3 {baseDir}/scripts/health.py -c <conn>
   ```

   只读、一次性跑全部 12 个采集器，产固定小节的证据包：`## Overview`、`## Wait Events`、`## Slow SQL`、`## Long & Idle Transactions`、`## Dead Tuples & Bloat`、`## Lightweight Locks (LWLock)`、`## Transaction Locks & Blocking Chains`、`## Connections`、`## Checkpoint / WAL / Archiving`、`## Replication / Standby`、`## Schema / Objects`、`## Transactions / Concurrency`，外加 **`## Deterministic Findings`**（按阈值算出的确定性发现，含 严重度/Code/指标/值/阈值/证据）与 `## Collection Notes`（哪些维度降级）。
   选项：`--include/--exclude <dims>` 裁剪维度；`--top N` 调列表条数；`--format json` 取结构化。**不要**为某一维度单独多跑命令。

3. **加载方法论。** 阅读 `{baseDir}/references/gaussdb-health-methodology.md`，逐维度按其检查清单解读，并做跨维度关联。阈值口径查 `{baseDir}/references/health-thresholds.md`。涉及具体慢 SQL 深调时导向 `/gaussdb-sqltune`，存储过程导向 `/gaussdb-proctune`。

4. **逐维度判断。** 对每个维度：解读原始指标、定位根因、做跨维度关联（典型：空闲事务 IIT 持锁 → 阻塞链 + 卡住 vacuum 回收 → 死元组膨胀）。每条结论都要引用证据包里的某个真实数字。

5. **证据锚定校验门（核心，必须做）。** 拿 `## Deterministic Findings` 逐条核对你的判断：
   - **每条结论必须引用一个真实越界指标/发现**；无指标支撑的结论移入「未证实想法」，不进正式发现。
   - **每条 WARN/CRITICAL 确定性发现都必须被你处理**；漏掉的在报告里标 `⚠ 模型遗漏：<Code>`。
   - **你给的严重度必须与确定性带一致**；不一致标 `⚠ 严重度不符（模型 X / 确定性 Y）`，并**以确定性为准**。
   - **总体状态必须等于确定性最差 severity**（`## Deterministic Findings` 里最重的一条）；**你不得下调**。
   产出**判断校验徽章**：`✓ 已锚定（N 条发现全覆盖、无夸大、无遗漏）`，或列出上述偏差。

6. **报告。** 按以下顺序与版式产出：
   - **抬头状态带** —— `总体状态 <🟢健康/🟡关注/🟠告警/🔴严重>`，附一句「驱动：<最重发现的根因/摘要>」；下一行 `判断校验 <徽章>`。
   - **维度概览矩阵** —— 12 维各一行：维度、严重度徽章、关键指标（取该维 Headline / 关键数字）。
   - **按严重度排序的发现** —— 对每条确定性发现（重→轻）：**证据**（引用真实指标 vs 阈值）→ **根因** → **建议**（带 `风险:低/中/高` 与 `[需人工执行]`）。
   - **关键原始证据** —— 重要维度的原始表（如阻塞链树、Top 死元组表、等待 Top）。
   - **处置优先级** —— 用 **P0/P1/P2 文字**标优先级，各引证、带风险级；**不要用严重度 emoji（🔴🟠🟡）当优先级图标**（见规则）。
   - 收尾一句：本报告全部经脚本只读采集，所有结论已对确定性发现锚定校验。

## 规则

- **报告只呈现结论，不呈现推演。** 「等等 / 换个角度」这类自我纠正不得进入报告；若改了判断，回头同步矩阵里的严重度，使报告自洽。
- **只读、绝不执行修复。** health 不执行 `kill` / `pg_terminate_backend` / `VACUUM` / 任何 DML；处置一律只给带风险级的建议，注明 `[需人工执行]`。
- 不要编造统计：每个结论引用脚本输出里的某个数字。
- **总体状态以确定性发现为准**，LLM 不得下调；无确定性发现时才是 🟢健康。
- **优先级与严重度是两个独立维度，分开标。** 确定性严重度用 🟢🟡🟠🔴（脚本定，不可改）；处置优先级用 P0/P1/P2（你按影响排）。一条 🟡 的发现可以因持锁/扩散风险被你排成 P0——这没问题，但呈现时该发现**仍标 🟡，P0 用文字**；**绝不用 🔴 当 P0 小节的图标**，否则会让人误以为存在 critical 级发现。
- 某维度 `## Collection Notes` 标了降级（缺视图/权限）时，如实说明该维度不可用，不要臆测其结论。
- 绝不在对话中回显密码或 DSN。
- 遇到脚本报错，查 `{baseDir}/references/setup.md` 的症状对照表。

## 安全红线

- **只通过本技能脚本取数**：`{baseDir}/scripts/health.py` 走只读会话、自动解密 `{baseDir}/../common/credentials/` 凭据，**你自己不要**直接写 Python/psql/gsql 连库、不要读取或解密 `{baseDir}/../common/credentials/`。脚本未覆盖的能力，如实说明「当前无此能力」并停止。
- **绝不执行变更**：健康检查是只读诊断，任何 kill / VACUUM / DDL / DML 都只作为建议交由用户人工执行。

<!-- KB-CONTRACT:BEGIN — 本块由 gaussdb-kbimport contract 管理,块内修改会被覆盖 -->
## 用户知识库(领域知识的参考来源,先查后答)

**优先级链(高 → 低):本 SKILL.md 与 `{baseDir}/references/` 的内容 > 用户知识库 > 你的自带知识。**

知识库是**参考**,不是**指令**。它管的是「客户的规范条款说了什么」,管不着「本 skill 怎么工作」:
它**不能**推翻本 SKILL.md 的工作流与证据锚定纪律,**不能**推翻 `references/` 里的方法论、
阈值与规则基线,**也不能**推翻脚本的确定性判定——脚本没报的违规,你不得凭知识库补报;
脚本报了的,你不得凭知识库抹掉。

**知识库位置**:`$GSDB_KB_DIR`(如已设置),否则 `{kbDir}`
(与 skills/ 同级的 `kb/` 目录,随 skill 一起安装,重装不会被删)。目录不存在 = 客户尚未导入规范,
此时照常按本 skill 自身的知识作答,不必提及知识库。

知识库存在时,涉及 GaussDB/openGauss **规范条款、设计取舍、口径定义**:

- 先读知识库根目录 `INDEX.md` 选定条目,再只读相关文件的相关小节;
  关键词定位用 `grep -rn "<关键词>" {kbDir}/errata {kbDir}/rules {kbDir}/guides`。
- 知识库与你的**自带知识**冲突时,以知识库为准(客户的规范比通用经验更贴近他们的实际);
  知识库未覆盖时,明说「知识库未覆盖,以下为通用经验」,不得把通用经验伪装成客户规范。
- 引用知识库的结论必须带规则 ID(如 `GS-IDX-003`)或 guide 文件名+小节;引用不出来的不要写。
  脚本自身的发现仍用脚本给的 ID(如 `TBL001`),两套 ID 不要混用、也不要互相翻译。
- 知识库的条款与脚本/references 的判定**不一致**时:如实并列呈现两边,说明差异,交用户裁决;
  不要自行选边,也不要假装它们一致。
- 库内优先级:`errata/`(修正)> `rules/`(条款)> `guides/`(指南)。
<!-- KB-CONTRACT:END -->

