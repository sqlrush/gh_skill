---
name: gaussdb-proctune
version: 2.0.0
description: "通过内置脚本对 OpenGauss/GaussDB 存储过程做深度调优。用户要优化慢过程、深入分析某个过程、调优过程里的游标 SELECT、验证索引或改写是否真的有效、或输出有证据支撑的过程优化建议时使用，包括“优化这个存储过程”“调一下这个过程”“看看游标 SQL 怎么优化”“这个过程有没有可验证的优化方案”等请求。触发后运行 scripts/proctune.py 和 scripts/verify.py，输出已验证的调优结果，不要只列出推测性建议。"
allowed-tools: ["exec", "read"]
compatibility: opencode
metadata:
  runtime: python3
  emoji: "⚙️"
  family: stored-procedure
---

# Proc Tune（OpenGauss/GaussDB 存储过程）

存储过程深度调优工作流。**第一版只对只读游标（cursor）的 SELECT 做经验证的自动改写；写逻辑、循环结构、逐行 DML、游标 FOR UPDATE 等一律只给循证建议、绝不自动改写。**
**你呈现的每条游标 SELECT 改写都必须有 `verify.py` 的 ACCEPTED 背书；其余建议放进明确分开的「建议（未验证）」小节。**

本技能用 Python 脚本（`{baseDir}/scripts/`）取数与验证：`proctune.py collect` 出建议层证据，`proctune.py tune-cursor` 对每个合规游标出证据+索引硬验证，`verify.py` 验游标 SELECT 改写。连接元数据读取 `$GSDB_HOME/config.yaml`（默认 `~/.gdaa/config.yaml`），凭据由脚本从 `{baseDir}/../common/credentials/` 自动解密。

命中以下请求时，必须使用本 skill 并实际执行脚本，不要只做概念解释：

- 用户要调优某个慢存储过程
- 用户要验证游标 SQL 的索引或改写是否真的有效
- 用户要拿到有证据背书的过程优化结果，而不是方向性建议

典型触发语句：

- 优化这个存储过程
- 调一下这个过程
- 看看游标 SQL 怎么优化
- 这个过程有没有可验证的优化方案

## 工作流

1. **选择连接。** 连接名沿用 `$GSDB_HOME/config.yaml`（默认 `~/.gdaa/config.yaml`） 的 `name` 字段。若不确定有哪些连接，看 name 列表；只在有多个时才问用哪一个。该文件**只含连接元数据，无密码**——口令在 `{baseDir}/../common/credentials/*.enc`，由脚本解密，**你不要去读/解密它**。
2. **采集证据——两条命令，中途不停。**

   ```bash
   python3 {baseDir}/scripts/proctune.py collect      -c <conn> <schema.proc>   # 结构发现 + 运行时归因（建议层）
   python3 {baseDir}/scripts/proctune.py tune-cursor  -c <conn> <schema.proc>   # 每个合规游标的证据 + 索引硬验证
   ```

   `collect` 产 `## Procedure Source`、`## Structural Findings`、`## Embedded Statements`、`## Runtime Attribution`。
   `tune-cursor` 对每个只读游标产 `## Cursor <name>`、`## Variable Substitution`、`## SQL`、`## Execution Plan`、`## Verified Index Candidates`，并把不合规游标列进 `## Skipped Cursors`。
   **不要向用户索要游标变量的值。** 需要精确选择性时用 `--bind <var=value>`（命名式，可重复）。

3. **合成值提醒。** `## Variable Substitution` 一节说明游标变量被按声明类型填了合成值——计划「形状」可靠，行数/选择性是近似值。把这点说清楚，并提示可用 `--bind` 传真实值做精确验证。

4. **加载方法论。** 阅读 `{baseDir}/references/proc-tuning-methodology.md`，对照证据各节按其检查清单分析。涉及 OpenGauss 内幕（嵌套语句统计、子事务成本、A 兼容游标语义）查 `{baseDir}/references/proc-internals.md`。**游标 SELECT 的优化等同单 SQL**，按需查 GaussDB 专项知识：CBO 与诊断 → `{baseDir}/references/gaussdb-cbo-and-diagnosis.md`；改写候选 → `{baseDir}/references/gaussdb-rewrite-patterns.md`；A 兼容 → `{baseDir}/references/gaussdb-a-compat-gotchas.md`；分区/分布 → `{baseDir}/references/gaussdb-partition-distribution.md`。

5. **索引建议——只用已验证的那一节。** 每个游标的 `## Verified Index Candidates` 是 hypopg 假设索引硬验证过的。
   - **只推荐出现在那张已验证表里的索引**，引用真实的 `Orig → Hypo cost (N×)`。
   - 若显示 "No index candidate passed verification"，索引「思路」必须明确标注「未验证（合成值下未确认收益，可试 `--bind`）」。

7. **游标 SELECT 改写——每条先验证再呈现。** 对每个想改的只读游标 SELECT：

   ```bash
   python3 {baseDir}/scripts/verify.py -c <conn> \
     --original '<substituted cursor SELECT>' \
     --rewrite  '<your rewrite>'
   ```

   - 两侧都用**替换后**的 SQL（不含变量/占位符）——verify 会拒绝带占位符的 SQL。
   - **只有 `verify.py` 判 ACCEPTED 时**（加速 ≥ 1.3× 且结果集等价）才把改写当成确定优化呈现，引用真实的 `cost X → Y (N×)` 与等价性结果。
   - **改写必须保持游标的输出列名与列序**（循环体用 `rec.col`）。列序/值变化会被 md5 等价校验挡下；**列改名 md5 抓不到——你要自己确保不改列名**。
   - REJECT 的移入「建议（未验证）」并注明驳回原因。

8. **建议（未验证，明确分区）。** 以下**只给建议、不自动改写**：
   - `## Skipped Cursors`：FOR UPDATE / 被 `WHERE CURRENT OF` 消费的游标、动态游标、依赖过程内临时表的游标，以及参数化/包内/REF CURSOR。
   - `## Structural Findings`：循环里跑 SQL、逐行 DML、循环内 EXCEPTION、动态 SQL（EXECUTE）、循环内不变查询等。
   每条建议引用 `## Structural Findings` 或 `## Runtime Attribution` 里的具体数字，按 `{baseDir}/references/proc-tuning-methodology.md` 的改法给方向，并明确标注「未验证，落地前需人工或测试实例确认」。

9. **报告。** 按以下顺序产出：
   - **被分析的存储过程** —— 签名 + 语言、volatility、是否 rollback-safe（来自 `## Procedure Source`）。
   - **结构热点图** —— 把过程源码原样放进一个普通 ``` 代码块复现，在每个反模式节点行末尾追加内联标记 `[H1]`、`[H2]`…（按严重度从重到轻编号，参考 `## Runtime Attribution` 的耗时排序）。
   - **热点走查表** —— 第一列就用同样的 `[Hn]` 标签，交叉引用行号、反模式类型、运行时归因（calls / avg / total）。
   - **根因** —— 引用结构发现 + 真实运行时数字。
   - **已验证推荐** —— **只**放 `verify.py` 判 ACCEPTED 的游标 SELECT 改写、以及 `## Verified Index Candidates` 里硬验证过的索引，各带真实 cost 差值。REJECT / 验证超时 / 未验证的改写**不**进此节。
   - **建议（未验证）** —— 第 8 步的内容，外加所有 REJECT / 验证超时 / 合成值下不达标的改写，明确分区、注明原因。
   - **风险与落地顺序** —— 低风险（加索引 / 改游标 SELECT）→ 中风险（外提不变查询 / 去动态 SQL）→ 高风险（结构重写，需充分测试）；以及 CREATE INDEX 的锁时间、计划回退。

## 规则

- 自动改写**仅限只读游标 SELECT**，且必须有 `verify.py` ACCEPTED 背书。任何会写数据的逻辑（DML、循环结构、游标 FOR UPDATE）**只给建议，绝不当成确定优化呈现**。
- 一次 `proctune.py collect` + 一次 `proctune.py tune-cursor` 产出整个证据包。绝不中途停下来索要变量值。
- 不要编造统计信息：每个结论都要引用脚本输出里的某个数字。`## Runtime Attribution` 不可用时，**不要**用「假设每游标 N 行」之类估算冒充证据——如实声明运行时数据缺失并降级为纯静态结构分析。
- **报告只呈现结论，不呈现推演。** 「等等 / 换个角度 / 让我重新想」这类自我纠正、中途假设、被推翻的判断一律不得出现在交付报告里。分析中若改了结论，回头同步改正对应的热点标记与严重度，使报告自洽——绝不把互相矛盾的两种说法同时留在报告里（例如某热点既标 🔴 最重又在根因里说「不是热点」）。
- **「已验证推荐」只放经背书的结论。** 只有 `verify.py` 判 ACCEPTED 的改写、`## Verified Index Candidates` 里硬验证过的索引才放这里；任何 REJECT / 验证超时 / 未验证（含合成值下不达标、等价校验未完成）的改写一律归入「建议（未验证）」，**严禁挂在「已验证推荐」标题下**——即使内联写了「未验」也不行。
- **逐字誊抄，严禁自算倍数。** 「已验证推荐」里的索引与 cost 倍数必须**原样誊抄** `proctune.py` 的 `## Verified Index Candidates` 表（DDL / Orig Cost / Hypo Cost / Speedup 照搬）；改写的倍数只能取自对应那一条 `verify.py` ACCEPTED 输出的真实数字。**严禁自行重算、估算或改写倍数。**
- **一个 cost 倍数只能归属于验证它的那一个对象。** 严禁把某索引（或多索引组合 verify）的战果安到另一条改写/另一个索引上；严禁把同一条验证结果当成两条独立推荐重复计数。组合（改写+索引）的倍数标注为「组合」，不拆给单独的改写或单独的索引。
- **严禁编造未经 verify 的因果。** "必须和某改写一起落地""索引隐含消除 Sort/排序"这类断言，除非有对应 verify/EXPLAIN 证据否则不得写——一条 verify 只证明它自己那一条；尤其当某索引**单独**经 `## Verified Index Candidates` 即达标时，不得反过来声称"单加索引无效、必须配合改写"。
- **索引去冗余。** 推荐多个索引时，前缀已被覆盖的不重复推荐（已荐 `(a,b)` 就不再单列 `(a)`），并说明各索引覆盖哪些游标。
- **合成值 caveat。** 倍数基于 `## Variable Substitution` 的合成值时，在「已验证推荐」里附一句：真实参数选择性不同、倍数会变，可 `--bind` 精确化。
- 默认**不**执行存储过程，也**不**执行任何 DML。
- 绝不在对话中回显密码或 DSN。
- 遇到脚本报错，查阅 `{baseDir}/references/proc-setup.md` 里的症状对照表。

## 安全红线

- **只通过本技能的脚本取数与验证**：`{baseDir}/scripts/` 下的 `proctune.py` / `verify.py` 是唯一通道，它们走只读会话、自动解密 `{baseDir}/../common/credentials/` 凭据，**你自己不要**直接写 Python/psql/gsql 连库、不要读取或解密 `{baseDir}/../common/credentials/`。脚本未覆盖的能力，如实说明「当前无此能力」并停止。
- 脚本对数据库的会话是**只读**的（存储过程不被执行、写/DDL 被会话级 `READ ONLY` 拦截）。

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

