# sqlreview 设计（SQL 规范审查 skill）

> 日期：2026-07-12。状态：已确认，待实现。

## 1. 目标

新增 `skills/sqlreview`，审查 DDL / DML / DQL 是否符合团队 SQL 规范。规范由用户在
`references/rules.yaml` 中自由编辑，脚本按规则确定性判定并产出 findings，LLM 只负责
解读、排优先级、给整改方案——沿用本项目 health / wdr 的「脚本产确定性发现，LLM 解读」模式。

## 2. 输入源（三个，首版全做）

| source | 参数 | 是否连库 | 判定对象 |
|---|---|---|---|
| SQL 文本 | `--file <f>` / `--stdin` | 否 | 语句文本 |
| 历史 SQL | `--sql-id <id>` / `--top <n>` | 是 | 语句文本（复用 sqlfetch 取文本） |
| 库存量对象 | `--schema <s>` | 是 | 库元数据事实 |

前两者共用同一套文本规则；第三者走元数据规则。两条路产出**同一种 `Finding`**，报告与
JSON 输出只有一套。

## 3. 规范文件：单一 `references/rules.yaml`

**用户面对的规范只有一份文件、一张清单。** 确定性与语义的区分是实现细节，不泄露给用户——
它体现为规则内部的 `check` 字段，而不是两个文件。

```yaml
version: 1
rules:
  - id: DML001
    name: 禁止物理删除
    severity: error            # error | warn | info
    applies_to: [dml]          # ddl | dml | dql | object
    check: stmt_forbidden      # 白名单内的 checker
    kind: delete               # checker 参数
    message: "禁止 DELETE，请改用逻辑删除标记"
    rationale: "物理删除不可追溯，且破坏下游增量同步"
    fix: "UPDATE t SET is_deleted = 1, deleted_at = now() WHERE ..."
    enabled: true              # 可选，默认 true

  - id: IDX003
    name: 索引应建在高基数列上
    severity: warn
    applies_to: [ddl, object]
    check: advisory            # 脚本判不了 → 取证后交 LLM
    criteria: |
      低基数列单独建 B-tree 索引通常无效。判断依据：
      - distinct / 总行数 < 1% 且无组合索引前缀用途 → 应移除
      - 只作为组合索引非首列出现 → 可接受
```

`check` 是**白名单**，不是任意字符串。加载时校验：未知 check 名、非法 severity、
无法编译的 pattern、缺失的必填参数，一律 fail fast 并指明是哪条 `id`。

### 内置 checker 白名单（首版）

| check | 作用 | 参数 | 适用 |
|---|---|---|---|
| `regex` | 逃生舱：用户自由新增文本规则 | `pattern`、`on: normalized\|raw` | 文本 |
| `advisory` | 不判定，取证交 LLM | `criteria` | 两者 |
| `table_no_primary_key` | 建表/存量表无主键 | — | 两者 |
| `table_has_foreign_key` | 存在外键约束 | — | 两者 |
| `naming_pattern` | 表/索引/列命名 | `target`、`pattern` | 两者 |
| `index_column_count` | 索引列数上限 | `max` | 两者 |
| `stmt_forbidden` | 禁用某类语句 | `kind: delete\|truncate\|drop` | 文本 |
| `dml_without_where` | UPDATE/DELETE 无 WHERE | — | 文本 |
| `select_star` | `SELECT *` | — | 文本 |
| `index_redundant` | 索引前缀被另一索引覆盖 | — | 对象 |

### 规则文件的位置约定

规则只有一份：`skills/sqlreview/references/rules.yaml`。`install-opencode.sh` 安装时
`rm -rf` 目标目录后整体重拷，**在安装目录里改 rules.yaml 会在下次安装时丢失**。
约定：改规范 = 改源码仓的 `rules.yaml` + 重跑 `./install-opencode.sh sqlreview`。
SKILL.md 中显式写明这一点。

## 4. 组件划分

```
skills/sqlreview/
├── SKILL.md
├── references/
│   └── rules.yaml        用户编辑的规范（唯一一份）
└── scripts/
    ├── sqlreview.py      入口：argparse、三 source 分发、退出码
    ├── lexer.py          纯函数：剥注释 → 字面量占位 → 切句 → 识别类型与对象名
    ├── rules.py          纯函数：加载 + 校验 rules.yaml → Rule
    ├── checks.py         纯函数：Statement / ObjectFacts + Rule → Finding
    ├── objects.py        只做 I/O：连库查系统表 → ObjectFacts
    ├── model.py          frozen dataclass：Severity/Rule/Statement/Finding/ReviewResult
    ├── report.py         纯函数：Finding → markdown / json
    └── render.py         vendored 副本（与其余 skill md5 一致）
```

边界：`objects.py` **只采集不判定**；`checks.py` **只判定不连库**。规则引擎全为纯函数，
单测不需要数据库。

数据流：

```
--file / --stdin       ─┐
--sql-id / --top (连库) ─┴→ SQL 文本 → lexer → [Statement] ─┐
                                                             ├→ checks → [Finding] → report
--schema (连库) → objects → ObjectFacts ─────────────────────┘
```

## 5. lexer（方案 B 的核心）

无第三方 SQL parser（`requirements.txt` 只允许 pg8000 / cryptography / PyYAML）。
自研纯 stdlib 轻量扫描器，只做四件事：

1. **span 扫描**：一遍扫出 `code / line_comment / block_comment / string / dollar_string /
   quoted_ident` 的区间。这是唯一的扫描器，下面两步都基于它。
2. **mask**：产出与原文**等长**的掩码串（注释→空格，字符串内部→填充字符，保留换行与引号），
   用于安全地按 `;` 切句并回算行号。
3. **normalize**：按 span 把字符串字面量替换为 `:s1 / :s2` 占位符，并保留
   `占位符 → 原字面量` 映射。正则规则默认跑在归一化文本上，注释与字符串里的
   `DELETE` 不会误报。
4. **classify + 抽名**：识别 `kind`（ddl/dml/dql/other）、`verb`（create_table/delete/…）、
   主体表名与索引名。

**关键设计点**：字面量被占位后，`LIKE '%abc'` 变成 `LIKE :s1`，朴素正则 `LIKE\s+'%` 会失效。
因此前置模糊匹配必须由结构化 checker `leading_wildcard_like` 实现——它在归一化文本里定位
`LIKE :sN`，再回查 `:sN` 的原值是否以 `%` 开头。需要匹配原文的 `regex` 规则显式写 `on: raw`。

## 6. 输出

markdown（默认）/ json（`--format json`）。报告分两块，边界对用户清晰：

```
## Deterministic Findings      ← 脚本判的，事实
[error] DML001 禁止物理删除 — stmt#3 line 42
[error] TBL001 表 orders 未定义主键 — stmt#1 line 5

## Advisory (需结合证据判断)    ← 交 LLM，附脚本采到的证据
[warn]  IDX003 索引应建在高基数列上
        证据：idx_orders_status 建在 status，distinct=3 / 1.2M 行
```

findings 按 severity 降序稳定排序。

## 7. 错误处理

沿用项目约定：

- **退出码 0 / 1 / 2**：0 = 脚本执行成功（**无论有没有查出违规**）、1 = 运行错误
  （规则文件非法、SQL 读取失败）、2 = 连接/配置错误。
  违规数**不**改变退出码——LLM 靠退出码判断脚本是否出错，不是判断审查结论；
  审查结论在 stdout。SKILL.md 中写明。
- 规则加载失败 → `RuleError`，消息含文件路径与规则 `id`，退出码 1。
- 库对象采集单个维度失败 → 降级（记 note，`available=False`），不中断整体审查，
  与 `health/collectors.py` 一致。
- 后端只抛 `DBError`；不吞异常。

## 8. 测试（TDD，先写测试）

`tests/test_sqlreview_units.py`，全部 DB-free：

- **lexer**：注释剥离保留行号；字符串占位与还原；`;` 在字符串/注释/`$$` 体内不切句；
  语句类型与 verb 识别；表名/索引名抽取。
- **rules**：合法 YAML 加载；未知 check 名报错并指出 id；非法 severity 报错；
  pattern 编译失败报错；`enabled: false` 被跳过。
- **checks**：每个内置 checker 的命中与不命中各一例；`LIKE '%x'` 命中而
  `LIKE 'x%'` 不命中；注释里的 `DELETE` 不误报。
- **objects**：用 FakeDB（`tests/test_sqlfamily_units.py` 的模式）喂假 rows，验证
  ObjectFacts 构造与单维降级。
- **report**：空 findings 的输出；markdown 表头；severity 排序。

## 9. 明确不做（YAGNI）

- 不引入 SQL parser 依赖，不自研递归下降 parser。
- 不做 `--rules <path>` 多级查找（规则只有内置一份，改源码仓再重装）。
- 不自动改写 SQL、不执行任何变更；整改建议一律标注 `[需人工执行]`。
- 不建第二份规范文件；长篇背景知识若将来需要，作为纯参考资料，不承载规范条目。
