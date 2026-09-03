---
name: gaussdb-kb
version: 2.1.0
description: "客户知识库(原 kbimport):把客户的 GaussDB/OpenGauss 规范文档(txt/md/docx/doc/pdf)与故障工单/问题分析报告(md/docx/csv/xlsx)导入知识库——规范条款化进 rules/guides/errata,工单结构化成案例并抽成图谱关系,关键数据写入前一律生成编号选择列表交用户确认;向量进高斯/PG 向量库、关系进 Neo4j,各诊断 skill 按发现检索并优先引用客户先例。脚本负责转换、快照、校验、索引、检索、契约注入;你负责条款分类、案例抽取、呈现选择列表与收集确认。用户说「导入规范 / 导入工单 / 建知识库 / 把 xxx 加进知识库 / 更新规范库 / 知识库里有没有类似案例 / 让 skill 按我们的经验来」即用。"
allowed-tools: ["exec", "read", "write"]
compatibility: opencode
metadata:
  runtime: python3
  emoji: "📚"
  family: sql-governance
---

# KB(客户知识库:规范 + 工单 → 向量库 + 图库)

分工铁律:**确定性工作由脚本做**(转换 / 快照 / 校验 / 出处回指 / 索引 / 检索 / 契约注入 / 写库),
**语义工作由你做**(条款分类、案例抽取、把选择列表呈现给用户并收集回答)。
**关键数据写入之前,一律先生成选择列表让用户确认**——你只提议,用户按编号定,脚本落盘。
你写入的每一条都必须能指回原文;指不回去的不入库。

命中以下请求时,必须使用本 skill 并实际执行脚本,不要只做概念解释:导入规范 / 导入工单(问题分析报告、ITSM 导出)/
建知识库 / 更新规范库 / 知识库里有没有类似案例 / 让 skill 按我们的经验来。

## 0. 预检

```bash
python3 {baseDir}/scripts/kb.py health
```

状态行第一行说明一切:`知识库未接入(原因)` 时按 `{baseDir}/references/storage-setup.md` 引导用户配 `kb.yaml`
与凭据(`python3 -m common.credential_cli set kb-pg` / `kb-graph`),然后 `kb.py setup`。**不要自己去读凭据文件**。
没配存储也能走规范路径(文件级索引照常),只是各 skill 的「客户知识库参照」小节会写「未接入」。

## 1. 规范路径(txt/md/docx/doc/pdf → 条款)

1. **导入**:`python3 {baseDir}/scripts/kb.py ingest 客户规范.docx`
   产出 `<kb>/sources/` 原文快照、`<kb>/inbox/<slug>/source.md` + `outline.md`。
   `.doc` / `.pdf` 转换失败或 PDF 是扫描件时脚本会**拒绝导入并说明原因**——如实转告用户,停下,不要自己猜内容;
   客户若同时有 `.docx` 和 `.pdf`,永远优先要 `.docx`。
2. **条款化(你的核心工作)**:先读 `{baseDir}/references/kb-layout.md`(格式与 ID 规范),再按 `outline.md` 分段读 `source.md`:
   能写成「看到 X 即违规」→ `rules/<域>.yaml`(拿不准 → `check: advisory`);讲设计方法/权衡 → `guides/*.md`;
   与库内既有条款矛盾/版本特例 → `errata/`。每条带 `source` 指回原文小节,rules 条款补 3-6 个 `keywords` 同义词;
   分配 ID 前 `kb.py search GS-<域>- --include-archived` 查最大号,**ID 永不复用**。
3. **确认后再写**:条款超过 10 条时,先给用户一张「ID + 一句话 + 去向文件 + 新增/沿用/修改/废止」清单,确认后再写入;
   原文模糊、前后矛盾的条款单独列出问用户,**不要替客户定规范**。
   `ingest` 打印「⚠ 换版导入」时,必须先读 `INDEX.md` 逐条比对,废止的**整条移进 `archive/<域>.yaml`** 并标
   `status: deprecated`(各 skill 用 grep 检索 rules/,留在原处只打标记照样会被命中——物理隔离才拦得住),最后递增 `VERSION`。
4. **写入 → 索引 → 校验**:用 write 工具写 `<kb>/rules|guides|errata|archive/`,删掉处理完的 `inbox/<slug>/`,然后
   `kb.py index` 与 `kb.py validate`(`[error]` 必须清零,`[warn]` 逐条向用户说明)。

## 2. 工单路径(xlsx/csv/md/docx → 案例 + 图)

1. **导入**:`python3 {baseDir}/scripts/kb.py ingest 工单导出.xlsx [--redact]`
   一单一文件到 `inbox/<slug>/items/`,脚本猜的列映射会打印出来——**列映射不对就告诉用户改列名或用 `--kind`/`--slug`**。
   `--redact` 确定性脱敏 IP / 手机号 / 证件号 / 邮箱(对象名不动)。原文马上进索引(`kind=raw`),当天可被检索。
2. **首次导入这类材料——写图/写向量之前先定转化策略**:没有 `<kb>/strategies/tickets.yaml` 时 `kb.py propose <slug>`
   **不出工作单**,只打印 8 个策略问题(每题带选项与默认)并退出 2:沉淀成案例还是条款 / 每单抽几条因果链 / 除因果链还抽哪些关系 /
   复发标志从哪取 / 同义现象是否合并到已有节点 / 哪些小节进向量 / 置信度口径 / 缺省元数据。**逐题向用户确认**,把答案按 key
   写进 `strategies/tickets.yaml`(如 `chain: 一单一条主链`);用户说「全按默认」就 `kb.py propose <slug> --use-defaults`,脚本代写。
   **然后重跑 `propose`**——工作单会带上策略与由它翻成的抽取约束,你填候选时必须遵守。之后同类材料不再问。
   不要替用户定策略,也不要在没有工作单的情况下自己编候选。
3. **抽取(你的核心工作)**:`propose` 出的 `inbox/<slug>/work/NNN.json` 每单一份:原文 + `candidate_template` + 已知实体。
   逐单阅读,按模板写 `inbox/<slug>/candidates.json`(JSON 数组)。硬性要求:
   - 每个 `quotes` / `entities[].quote` / `edges[].quote` 都必须是**原文里逐字出现的片段**(review 会逐条核对,对不上整项作废);
   - `quotes.现场` 必填;`conclusion: 已确认` 时 `quotes.primary_factor` 必填——原文没写明根因就写 `推测`,不要编一句当已确认;
   - 拿不准的字段留空,不要编;根因没写明就 `conclusion: 推测`;
   - 实体用原文叫法,`known_entities` 里有同一个东西就用它的名字;
   - 边只写原文能支撑的 现象→根因(`caused_by`)、根因→处置(`handled_by`),`confidence` 是你的把握(0.5–0.9);
   - 每轮 5–10 单,多的下一轮 `propose --offset` 续跑。
4. **选择列表(写库前的唯一闸门)**:`kb.py review <slug>` 生成 `review.md`——**原样呈现给用户**(格式与各类默认见
   `{baseDir}/references/selection-list.md`),收集回答。`[边]` 没有默认接受:用户不答就留候选(可检索,不进「本行历史路径」);
   用户不答的你不要替他答。清单里有 `[error]`(出处回指失败、ID 重复、字段缺失)先修候选再 review。
5. **落盘**:把用户的回答翻成参数执行
   `kb.py apply <slug> --all-but-edges --accept 7,8 [--reject 10] [--edit 2:S1] --user <工号>`,
   然后 `kb.py validate && kb.py index`。处理完的工单会从 `items/` 移走;`health` 显示还剩几单。

案例格式见 `{baseDir}/references/case-format.md`,图的 kind/rel 见 `{baseDir}/references/graph-schema.md`。

## 3. 查询(用户直接问"以前有没有类似情况")

```bash
python3 {baseDir}/scripts/kb.py query --q "<用户的问题>"
```

输出就是各诊断 skill 里同款的「客户知识库参照」小节:贵行规范 / 历史相似(带结论强度与处置)/ 本行历史路径
(只含客户确认过的边,标几个案例支持)/ 原始工单。**引用必带 ID 与出处**;写着「无」就如实说「本行无先例,以下为通用做法」;
绝不编案例或规范。有 findings 的 skill(health / sqltune / …)不用你查——它们的脚本已经把这一节写进输出了。

## 4. 契约注入(让做判断的 skill 先查知识库)

```bash
python3 {baseDir}/scripts/kb.py contract            # 扫描,给用户看状态
python3 {baseDir}/scripts/kb.py contract --apply    # 用户确认后执行
```

契约块(`{baseDir}/references/kb-contract.md`)幂等注入 9 个做规范/阈值/诊断判断的 skill 的 `KB-CONTRACT` 标记区,
标记区外一字不动;标记区损坏时跳过该文件并报错。纯取数的 skill(slowsql / topsql / sqlfetch / explain / topproc / procinfo)不注入。
**治理边界(向用户讲清)**:skill 自身 SKILL.md 与脚本的确定性判定 > 知识库 > 模型自带知识。知识库管「客户怎么说、以前怎么处置」,
管不着「skill 怎么工作」,**不改 severity**;不一致时并列呈现交用户裁决。安装目录副本会被下次 install 覆盖,源码仓也要 apply。

## 5. 验证闭环

- `kb.py health`:状态行、覆盖率、待处理、**缺口清单**(近期查不到条款/案例的发现——提示该补哪类材料);
- `kb.py eval`:跑 `<kb>/eval/queries.yaml` 的黄金查询与金丝雀案例(与通用做法**故意相反**的客户处置),recall 不达标退出 2;
- `kb.py cite-check --text "<回答>"`(或 `--file`、stdin):核对回答里引用的案例 ID / 条款 ID 是否真在库里——未找到的标「疑似编造」
  并退出 2,已废止条款标 ⚠。用户要求复核时跑它;你自己作答引用了 ID,交稿前也先跑一遍,未找到的 ID 从回答里删掉;
- 挑 1–2 条新入库案例演示 `kb.py query --q` 能命中;建议客户埋 2–3 个金丝雀案例定期抽查各 skill 是否真按知识库作答。

## 退出码语义

`0` = 成功;`1` = 运行错误(格式不支持、转换失败、存储/凭据错误);`2` = 有待处理项(validate 有 error、review 有待定项、
覆盖率不足、health 有待处理)。退出码 2 不是失败,是「有活没干完」。

## 能力边界(如实说明,不要假装)

- 条款分类、案例抽取是**你**的语义判断;写入前必须经用户确认(选择列表),且每项都要能指回原文;指不回去的作废。
- `.doc` / `.pdf` 依赖系统转换器(textutil / antiword / pdftotext);扫描件不做 OCR,脚本会拒绝并说明。
- 向量检索依赖 `kb.yaml` 配的 embedding 端点;没配或端点无嵌入模型时只走词法 + 图,状态行会写「向量:未启用」——**不要假装有向量**。
- Neo4j 不可达时路径小节为空、状态行写「图:不可用」;高斯/PG 不可达时整节只剩「未接入」。这些都是降级,不是失败,skill 照常。
- 「哪条边该确认」「哪个实体该归一」是用户的决定;脚本只能确定性地把候选摆出来、把回答落盘。

## 安全红线

- 本技能只连**知识库专用**的高斯/PG 与 Neo4j(口令经 `common.credential` 解密),**不连被管业务库**,不读取或解密 `credentials/`。
- 只写 `<kb>/` 目录与各 SKILL.md 的 `KB-CONTRACT` 标记区,不改任何 skill 的其他内容、不改脚本代码。
- `sources/` 里的原文快照只读;规范或工单内容有疑义时问用户,不得自行"修正"客户的材料。
- 工单原文可能含 IP / 账号 / 人名:导入时用 `--redact`;呈现选择列表与案例时不复述原文里的 IP、端口、接口地址。

<!-- KB-CONTRACT 说明:本 skill 是知识库的管理者而非消费者,自身不注入契约块。 -->
