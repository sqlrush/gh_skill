---
name: gaussdb-kb
version: 2.0.0
description: "客户知识库(原 kbimport):把客户的 GaussDB/OpenGauss 规范文档(txt/md/docx/doc/pdf)与故障工单/问题分析报告(md/docx/csv/xlsx)导入知识库——规范条款化进 rules/guides/errata,工单结构化成案例并抽成图谱关系,关键数据写入前一律生成编号选择列表交用户确认;向量进高斯/PG 向量库、关系进 Neo4j,各诊断 skill 按发现检索并优先引用客户先例。脚本负责转换、快照、校验、索引、检索、契约注入;你负责条款分类、案例抽取、呈现选择列表与收集确认。用户说「导入规范 / 导入工单 / 建知识库 / 把 xxx 加进知识库 / 更新规范库 / 知识库里有没有类似案例 / 让 skill 按我们的经验来」即用。"
allowed-tools: ["exec", "read", "write"]
compatibility: opencode
metadata:
  runtime: python3
  emoji: "📚"
  family: sql-governance
---

# KB Import(规范知识库导入与治理)

分工铁律:**确定性工作由脚本做**(转换/快照/索引/校验/检索/契约注入),
**语义工作由你做**(条款分类、起草、ID 分配)。你写入的每一条都必须能指回原文。

## 工作流

1. **预检。** 运行 `python3 {baseDir}/scripts/kb.py -h`。报缺 PyYAML 就提示
   `python3 -m pip install PyYAML`,然后停下。

2. **导入(脚本)。**

   ```bash
   python3 {baseDir}/scripts/kb.py ingest 客户规范.docx
   ```

   产出:`<kb>/sources/` 原文快照、`<kb>/inbox/<slug>/source.md`(归一化文本)、
   `outline.md`(标题大纲)。`.doc` / `.pdf` 转换失败时,把脚本给出的原因和建议
   **如实转告用户,停下,不要自己猜内容**。

   **PDF 的特殊性**:扫描件 PDF 里的字是图片,`pdftotext` 对它**成功退出却输出空文本**。
   脚本有质量闸门,提取过少或疑似乱码时**直接报错拒绝导入**(退出码 1),不会写出
   空的 source.md。撞上这个,如实告诉用户「这是扫描件,需要先 OCR」,**不要试图绕过**。
   客户若同时有 `.docx` 和 `.pdf`,**永远优先要 `.docx`**——PDF 的提取质量差得多
   (双栏排版会错乱、表格结构会丢),主动向用户要原件。
   KB 位置:`--kb <目录>` > `$GSDB_KB_DIR` > **`<安装根>/kb`**(脚本会打印实际路径)。
   `<安装根>` 从脚本自身位置推导,与 skill **装在一起**:全局安装 → `~/.config/opencode/kb/`,
   项目安装 → `<项目>/.opencode/kb/`。**知识库在 `skills/` 的同级目录,不在 skill 目录内部**
   ——install 每次重装会 `rm -rf` 整个 skill 目录,放里面会被删光。客户无需配任何环境变量。

3. **条款化(你的核心工作)。** 先读 `{baseDir}/references/kb-layout.md`(格式与 ID 规范,
   必须遵守),再按 `outline.md` 分段读 `source.md`,逐条分类:
   - 能写成「看到 X 即违规」→ `rules/<域>.yaml` 条款;拿不准 → `check: advisory`;
   - 讲设计方法/权衡 → `guides/*.md`;
   - 与库内既有条款矛盾/版本特例 → `errata/`。

   硬性要求:每条带 `source` 指回原文小节;每条 rules 条款补 3-6 个 `keywords`
   同义词(客户用语 + 通用叫法 + 英文,让运行时字面检索能跨越叫法差异);
   分配 ID 前先 `kb.py search GS-<域>- --include-archived` 查最大号
   (**必须带 `--include-archived`**,否则会漏掉已废止的号段而重复分配),**ID 永不复用**;
   条款超过 10 条时,先给用户看「ID + 一句话 + 去向文件」清单,确认后再写入;
   原文模糊、前后矛盾的条款单独列出问用户,**不要替客户定规范**。

3b. **换版分支(ingest 打印了「⚠ 换版导入」时,这一步不可跳过)。**

   知识库里已有条款,说明这是**规范升版**,不是首次导入。**先读 `<kb>/INDEX.md`**,
   把新版原文与库里现有条款**逐条比对**,给用户一张表:
   `ID | 一句话 | 新增 / 沿用 / 修改 / 废止`,**确认后**再动手:

   - **新增** → 分配新 ID 写进 `rules/`;
   - **修改** → 原地改 `rules/` 里那条(ID 不变,`source` 更新到新版小节);
   - **废止** → **整条移进 `archive/<域>.yaml`**,补 `status: deprecated` 与
     `superseded_by`,**绝不直接删除**;
   - **沿用** → 不动。

   最后手工递增 `<kb>/VERSION`。

   **为什么废止必须移走而不是打标记**:各 skill 用 `grep -rn` 检索 `rules/`,而 grep
   只输出**命中行**——一条留在 `rules/` 里仅标了 `status: deprecated` 的条款,模型搜
   「外键」时看到的是 `rules/table.yaml:12: rule: 禁止使用外键约束`,**看不到 status 那行**,
   照样会按已作废的规范判客户违规,且**不会有任何报错**。`archive/` 不在 grep 范围内,
   物理隔离才拦得住。`validate` 会双向校验:标了没移、移了没标,都是 `[error]`。

   **漏掉这一步的后果**:客户已经废止的规范继续被用来判违规,报告还煞有介事地引用
   一份客户自己都作废了的原文出处。这是纯粹的静默失效,必须避免。

4. **写入。** 用 write 工具把草稿写进 `<kb>/rules|guides|errata/`(废止的写进 `<kb>/archive/`),
   然后删除处理完的 `inbox/<slug>/`。

5. **索引 + 校验(脚本)。**

   ```bash
   python3 {baseDir}/scripts/kb.py index
   python3 {baseDir}/scripts/kb.py validate
   ```

   validate 报 `[error]` 必须改到 0 才算导入完成;`[warn]` 逐条向用户说明。
   校验项:文件编码、rule schema 与 ID 唯一性(跨 `rules/` 与 `archive/`)、
   **废止条款的摆放位置**(标了没移 / 移了没标,都是 error)、guides frontmatter、
   INDEX 一致性、inbox 是否还有未处理项。

   **编码检查**:`rules/` `guides/` `errata/` `archive/` 里的文件必须是 UTF-8。非 UTF-8(如 GBK)
   会被各 skill 的 `grep` **静默漏掉**(不报错,只是搜不到),导致模型误以为「知识库未覆盖」
   而拿通用经验作答。validate 会报 `[error]` 并给出可直接执行的转码命令。
   (`sources/` 是原文快照,保留客户原编码,不检查。)

6. **契约注入(脚本,让做规范判断的 skill 先查知识库)。**

   ```bash
   python3 {baseDir}/scripts/kb.py contract            # 先扫描,给用户看状态
   python3 {baseDir}/scripts/kb.py contract --apply    # 用户确认后执行
   ```

   契约块(见 `{baseDir}/references/kb-contract.md`)幂等注入到 `KB-CONTRACT` 标记区,
   标记区外一字不动;标记区损坏时**跳过该文件并报错**,绝不猜着写。

   **只注入会做规范/阈值判断的 skill**:`sqlreview` / `health` / `wdr` / `memanalyze` /
   `sqltune` / `proctune`。`slowsql` / `topsql` / `sqlfetch` / `explain` / `topproc` /
   `procinfo` 是纯取数(列表格、还原 SQL 文本),不注入。

   **治理边界(务必向用户讲清)**:优先级链是
   **skill 自身 SKILL.md 与脚本的确定性判定 > 知识库 > 模型自带知识**。
   知识库管「规范条款说了什么」,管不着「skill 怎么工作」——它不能推翻 sqlreview 的
   `rules.yaml` 判定结果,两边不一致时并列呈现、交用户裁决。

   **提醒用户**:安装目录(`~/.config/opencode/skills`)的副本会被下次 install 覆盖,
   源码仓要一并 `--apply --skills-dir <仓库>/skills` 并提交。

7. **验证闭环。** 挑 1-2 条新入库条款演示 `kb.py search <关键词>` 能命中;
   建议用户按 kb-layout.md 埋 2-3 条金丝雀条款并记录 ID,定期抽查各 skill
   是否真按知识库作答。

## 退出码语义

`0` = 成功;`1` = 运行错误(格式不支持、转换失败、路径不存在);
`2` = 有待处理项(validate 有 error / contract 扫描发现缺契约)。
退出码 2 不是失败,是「有活没干完」,逐条处理即可。

## 能力边界(如实说明,不要假装)

- 条款分类是**你**做的语义判断,不是脚本判定——写入前必须经用户确认,
  且每条都要能指回原文;指不回去的不入库。
- `.doc` 依赖系统转换器(macOS textutil / antiword),`.pdf` 依赖 poppler 的 pdftotext
  (或 mupdf 的 mutool);都没有时如实告知安装命令,或请用户转成 .txt/.docx。
- **PDF 只能提取「文本型」PDF**。扫描件(图片型)不做 OCR,脚本会拒绝导入并说明原因——
  这是刻意的:入库一个空文档或一堆乱码,比明确报错危险得多。双栏排版会错乱、
  表格结构会丢失,所以 `.docx` 原件永远优于 `.pdf`。
- 脚本的 search 是关键词匹配,不是语义检索;没命中不代表库里没有相关内容,
  可换关键词或读 INDEX.md 后定向读文件。search **不含已废止条款**(与各 skill 的
  grep 范围一致);命中了已废止条款时它会提示条数,但不显示内容——废止条款**不得用于判定**。
- 「哪条该废止」是**你**的语义比对,脚本判定不了。脚本只能确定性地告诉你
  「这是换版导入,库里已有 N 条」,并在你标错位置(标了没移 / 移了没标)时报 error。

## 安全红线

- 本技能**不连数据库**、不读取 `~/.gdaa/credentials/`。
- 只写知识库目录(`<kb>/` 下)与各 SKILL.md 的 `KB-CONTRACT` 标记区,
  不改任何 skill 的其他内容、不改脚本代码。
- `sources/` 里的原文快照只读,条款化时不得改写原文;规范内容有疑义时问用户,
  不得自行"修正"客户的规范。

<!-- KB-CONTRACT 说明:本 skill 是知识库的管理者而非消费者,自身不注入契约块。 -->
