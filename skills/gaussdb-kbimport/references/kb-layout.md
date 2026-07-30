# 知识库目录与条款格式规范(模型条款化时必须遵守)

## 目录结构(由 `kbimport.py ingest` 自动创建)

```
<kb>/                        # 默认 $GSDB_HOME/kb(随连接配置走,重装 skill 不丢)
  VERSION                    # 如 2026.07;规范大版本更新时手工递增
  INDEX.md                   # 由 kbimport.py index 生成,勿手工编辑
  errata/                    # 修正与例外(最高优先级):实战纠错、版本差异
  rules/                     # 机器可判定条款(yaml,稳定 GS-* ID)
  guides/                    # 语义指南(md + frontmatter)
  sources/                   # 原始文档快照(只读存档,不进上下文)
  inbox/<slug>/              # ingest 产物:source.md + outline.md,条款化完删除
```

## 规则 ID 规范(永不复用,只增不改)

- 格式:`GS-<域>-NNN`,域为 2-4 位大写字母,NNN 三位数字从 001 起。
- 建议域:`NAM` 命名 / `DDL` 建表 / `IDX` 索引 / `DML` 增删改查 / `PRC` 存储过程 /
  `GUC` 参数 / `SEC` 安全 / `MIG` 迁移兼容。新域按需自定,保持 2-4 位大写。
- 分配新 ID 前先 `kbimport.py search GS-<域>-` 查当前最大号;**删除条款保留 ID 空洞,
  绝不把旧 ID 分配给新条款**(报告里引用过的 ID 必须永远可追溯)。

## rules/*.yaml 条款 schema(validate 会强校验)

```yaml
# 索引规范(文件首行注释会进 INDEX)
- id: GS-IDX-003            # 必填,GS-<域>-NNN
  severity: error           # 必填,error | warn | info
  check: deterministic      # 必填,deterministic(脚本可判)| advisory(模型判)
  rule: 单个索引列数不超过 4 列    # 必填,一句话条款
  rationale: 超过后选择率增益递减,写放大显著   # 建议
  criteria: ""              # advisory 必须给:判定依据(看什么证据、怎么判)
  keywords: [组合索引, 联合索引, 复合索引, composite index]   # 强烈建议:3-6 个同义词
                            # (客户用语 + 通用叫法 + 英文),离线做语义扩展,
                            # 运行时 search/grep 纯字面匹配即可命中
  applies: [opengauss>=3.0, gaussdb-a]        # 建议
  source: 《XX规范》v5 §4.2.1                  # 建议(validate 会 warn)
```

## guides/*.md frontmatter schema

```yaml
---
id: guide-index-design       # 必填,库内唯一
description: 索引设计:高基数判断、组合索引列序、局部索引取舍   # 必填(进 INDEX)
scope: [gaussdb-sqltune, gaussdb-sqlreview, gaussdb-explain]   # 建议:主要消费的 skill
source: 《XX规范》v5 §4 + errata/2026-07-xx.md   # 建议
---
```

## errata/*.md 格式

文件名 `YYYY-MM-DD-<slug>.md`;正文首行为一句话结论(会进 INDEX),
必须写明:修正/推翻哪条(规则 ID 或 guide 小节)、环境(版本/部署形态)、依据。

## 条款化判定口径

- **能写成"看到 X 即违规"的 → rules**;拿不准能否确定性判定的,一律 `check: advisory`。
- 讲"怎么设计/怎么权衡/什么场景选什么"的 → guides。
- 与库内既有条款矛盾、或明确是版本特例的 → errata(并在 errata 里引用被修正的 ID)。
- 每条 rules/guides 都必须能指回 `inbox/<slug>/source.md` 的具体小节(填进 source 字段);
  指不回去的内容**不要入库**——知识库只收有出处的规范,不收模型的自由发挥。

## 金丝雀条款(建议)

入库时埋 2-3 条与通用 PostgreSQL 常识**故意相反**的真实条款并记下 ID,
定期用它们提问验证各 skill 是否真按知识库作答(失守 = 契约失效,重查 SKILL.md 契约块)。
