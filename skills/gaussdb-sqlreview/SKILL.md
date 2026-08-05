---
name: gaussdb-sqlreview
version: 1.0.0
description: "通过内置脚本对 OpenGauss/GaussDB SQL 做治理审查。用户要判断 SQL 或 DDL 是否符合规范、上线前评审 SQL、审查表/索引/列命名和主键等规则，或者检查线上 SQL 和已有 schema 对象是否有违规时使用，包括“这段 SQL 合不合规”“上线前审一下”“库里哪些表不合规范”“看看索引设计合不合规”等请求。触发后运行 scripts/sqlreview.py，输出真实规则检查结果，不要只给通用最佳实践建议。"
allowed-tools: ["exec", "read"]
compatibility: opencode
metadata:
  runtime: python3
  emoji: "📏"
  family: sql-governance
---

# SQL Review（OpenGauss/GaussDB 规范审查）

**确定性判定规则**的唯一来源是 `{baseDir}/references/rules.yaml`，**判定由脚本做，不由你做**——
你负责解读结果、排优先级、判 advisory 规则、给整改方案。

用户知识库（见文末 KB-CONTRACT）是客户规范的**参考**来源，**不改变本 skill 的判定**：
`rules.yaml` 与脚本判定优先，脚本没报的违规，你不得凭知识库补报；脚本报了的，你不得凭
知识库抹掉。两边不一致时，如实并列呈现并交用户裁决（例如「客户规范 GS-TBL-005 要求表名
以 ods_/dwd_ 开头，但 rules.yaml 的 TBL003 未覆盖此要求——脚本不会检查该条，建议人工确认
或把该条编入 rules.yaml」）。

命中以下请求时，必须使用本 skill 并实际执行脚本，不要只做概念解释：

- 用户要审查 SQL 或 DDL 是否合规
- 用户要做上线前 SQL 评审
- 用户要检查库里表、索引、列命名和键约束是否违规

典型触发语句：

- 这段 SQL 合不合规
- 上线前审一下
- 库里哪些表不合规范
- 看看索引设计合不合规

## 工作流

1. **选输入源。** 三选一，按用户意图挑：

   ```bash
   # a) 审查 SQL 文件（上线前评审，不连库）
   python3 {baseDir}/scripts/sqlreview.py --file changes.sql

   # b) 审查线上跑过的 SQL（需要连接）
   python3 {baseDir}/scripts/sqlreview.py -c <conn> --sql-id <unique_sql_id>
   python3 {baseDir}/scripts/sqlreview.py -c <conn> --top 20

   # c) 审查库中存量的表与索引（需要连接）
   python3 {baseDir}/scripts/sqlreview.py -c <conn> --schema public
   ```

   **选择连接。** 连接名沿用 `$GSDB_HOME/config.yaml`（默认 `~/.gdaa/config.yaml`） 的 `name` 字段。若不确定有哪些连接，看 name 列表；只在有多个时才问用哪一个。该文件**只含连接元数据，无密码**——口令在 `{baseDir}/../common/credentials/*.enc`，由脚本解密，**你不要去读/解密它**。
   需要机器可读结果时加 `--format json`。

2. **读脚本输出，不要自己重新判定。**
   - `## Deterministic Findings` —— 脚本已确定性判定的违规，**逐条如实呈现**，
     不得增删、不得改写规则 id 与级别。
   - `## Advisory（需结合证据判断）` —— 脚本判不了的规则，已附 `依据（criteria）`
     和采到的证据。你**逐条**对照证据给结论：是否违规、为什么。证据不足以下结论时，
     明说「证据不足」并指出还需要什么数据，**不要猜**。

3. **证据锚定校验。** 你写进报告的每个数字（行号、列数、表名、索引名）必须能在脚本
   输出里逐字找到。找不到就不要写。禁止凭印象补充脚本没报的"违规"。

4. **给整改方案。** 按 error → warn → info 排序。每条整改都标注 `[需人工执行]`。
   涉及索引优化时，可转 `gaussdb-sqltune` skill 做 hypopg 实证；涉及存量表膨胀时转 `gaussdb-health`。

5. **退出码语义。** `0` = 脚本跑成功（**不代表没有违规**，违规结论在 stdout）；
   `1` = 运行错误（规则文件非法、SQL 读取失败）；`2` = 连接/配置错误。
   不要把退出码 0 解释为「审查通过」。

## 规范怎么改

规范全部在 `{baseDir}/references/rules.yaml`，用户可以自由编辑：

- 换成自家命名前缀 → 改 `pattern`
- 某条规则不适用 → 加 `enabled: false`
- 调整严重程度 → 改 `severity`
- 新增文本规则 → `check: regex` + `pattern`，**不用改 Python 代码**

用户问「你们的规范有哪些」时，用 `read` 工具读 `{baseDir}/references/rules.yaml`
列清单。**注意**：安装脚本会重拷整个 skill 目录，所以规范要改**源码仓**里的
`skills/gaussdb-sqlreview/references/rules.yaml`，改完重跑 `./install-opencode.sh gaussdb-sqlreview`；
直接改安装目录下的副本会在下次安装时丢失。

## 能力边界（如实说明，不要假装）

- 脚本**没有** SQL 语法解析器，用的是轻量分词 + 规则匹配。注释与字符串字面量已被正确
  剥离（注释里的 `DELETE` 不会误报），但深层语义（子查询里的表别名归属、函数索引的
  实际列）判不了。遇到判不了的，如实说「当前规则无法覆盖」。
- `--sql-id` 取到的线上 SQL 可能是**归一化文本**（字面量变成占位符）或**被截断**
  （`track_activity_query_size` 限制），脚本会在报告里出 note。此时前置模糊匹配这类
  依赖字面量的规则会失效，必须如实说明，不要断言"没有违规"。
- 存量对象审查（`--schema`）看到的是**服务端折叠后的名字**（未加引号的 `OrderItems`
  在库里就是 `orderitems`），所以大小写类命名违规只能在 DDL 文本审查中发现。

## 安全红线

- **只通过本技能脚本取数**：`{baseDir}/scripts/sqlreview.py` 走只读会话、自动解密 `{baseDir}/../common/credentials/` 凭据，**你自己不要**直接写 Python/psql/gsql 连库、不要读取或解密 `{baseDir}/../common/credentials/`。脚本未覆盖的能力，如实说明「当前无此能力」并停止。
- **只读审查**：本技能不执行任何变更。所有整改建议（加主键、删外键、删冗余索引、
  改逻辑删除）一律只给 SQL 文本并标注 `[需人工执行]`，绝不代为执行。
- **不得替脚本判定**：不要绕过 `rules.yaml` 自行认定某条 SQL "不合规"，也不要
  隐瞒脚本报出的违规。你的判断只作用于 `Advisory` 区，且必须基于脚本给出的证据。

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

