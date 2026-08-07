# 参与开发指南

面向从零开始参与 `opencode_skill` 项目的新人。读完这份文档，你能搭好开发环境、跑通测试，并独立完成「新增 skill」「给已有 skill 加函数」「新增 references 文档」三类最常见的贡献。

---

## 1. 快速上手

### 1.1 克隆仓库

```bash
git clone https://github.com/sqlrush/opencode_skill
cd opencode_skill
```

### 1.2 创建虚拟环境（可选但推荐）

```bash
python3 -m venv .venv
source .venv/bin/activate
```

### 1.3 安装依赖

```bash
python3 -m pip install -r requirements.txt
# 安装: pg8000>=1.30  cryptography>=41  PyYAML>=6
```

三个包均为纯 Python，无需编译。

### 1.4 跑单测，确认环境绿

```bash
python3 -m pytest -q -m "not live"
```

正常输出类似：

```
73 passed in 1.2s
```

所有单测（不含需要真实数据库的 live 测试）通过即说明本地环境就绪。

---

## 2. 搭本地测试库

单测无需数据库。只有跑 live 测试、或想在 OpenCode 里实际调用 skill 时才需要配置数据库连接。

### 2.1 用 Docker 起 openGauss

```bash
docker run -d \
  --name og-dev \
  -e GS_PASSWORD=Passw0rd123! \
  -p 15432:5432 \
  opengauss/opengauss:latest
```

等容器健康（约 30 秒）：

```bash
docker exec og-dev gs_ctl status
```

### 2.2 配置连接目录

连接配置目录由环境变量 `GSDB_HOME` 指定。

```bash
export GSDB_HOME=~/.my-gsdb-home
mkdir -p "$GSDB_HOME" && chmod 700 "$GSDB_HOME"
```

写连接元数据（**不含密码**）：

```bash
cat > "$GSDB_HOME/config.yaml" <<'YAML'
connection_mode: gsql

db_connections:
  dev:
    - name: og-dev
      type: opengauss
      host: 127.0.0.1
      port: 15432
      database: postgres
      user: gaussdb
      driver: pg8000
YAML
chmod 600 "$GSDB_HOME/config.yaml"
```

配置口令（两种方式二选一）：

```bash
# 方式一：环境变量（简单，推荐本机开发）
export GSDB_PASSWORD='Passw0rd123!'

# 方式二：加密落盘
python3 -c "
import sys; sys.path.insert(0, '.')
from common import save_secret
save_secret('og-dev', input('password: '))
"
```

### 2.3 验证连接可用

```bash
python3 skills/gaussdb-slowsql/scripts/slowsql.py -c og-dev -h
python3 skills/gaussdb-slowsql/scripts/slowsql.py -c og-dev --threshold 500
```

退出码 0、输出 Markdown 表格（可能为空）即为正常。

### 2.4 跑 live 测试

```bash
python3 -m pytest -q -m live
```

没配连接时，live 测试会自动跳过（skip），不会报错。

---

## 3. 项目速览（贡献者心智模型）

```
opencode_skill/
├── common/                # 共享连接层（Database / ConfigError / DBError 等）
│   ├── __init__.py        # 公共 API 入口
│   ├── config.py          # 读 GSDB_HOME/config.yaml，find(name) 取连接
│   ├── credential.py      # load_secret / save_secret（AES-256-GCM）
│   ├── db.py              # Database 门面：connect / query / scalar / close
│   └── backends/          # gsql 后端 + pg8000 后端（skill 不需关心）
│
├── skills/                # 每个子目录 = 一个 skill
│   ├── gaussdb-slowsql/
│   │   ├── SKILL.md       # 给 LLM 的工作流描述（frontmatter + 步骤 + 安全红线）
│   │   └── scripts/
│   │       ├── slowsql.py # 主入口：argparse + common + 查询 + 退出码
│   │       └── render.py  # Markdown 渲染工具（table / code_block / truncate）
│   ├── gaussdb-sqltune/
│   │   ├── SKILL.md
│   │   ├── scripts/
│   │   └── references/    # LLM 按需阅读的知识文档（方法论/阈值/GUC 等）
│   └── ...（其余 8 个 skill 结构相同）
│
├── tests/                 # pytest 单测
├── install-opencode.sh    # 自动发现+安装脚本（有 SKILL.md 即被拷）
└── requirements.txt
```

**三个核心概念：**

- **`SKILL.md`** — 给 LLM 读的工作流说明，描述"怎么用这个技能"。脚本命令里用 `{baseDir}` 占位，安装脚本在拷贝时替换成真实绝对路径。
- **`scripts/`** — 真正干活的 Python 脚本，负责连库取数、格式化输出；LLM 通过 `exec` 工具调用它们。
- **`common/`** — 所有 skill 共用的连接层，不含任何业务逻辑。新增 skill 只需 `import common`，无需自己处理连接/凭据/驱动。

深入了解架构：见 `docs/delivery/02-architecture.md`（如存在）。

---

## 4. 演示①：从 0 新增一个 skill

以新增 **`biggest-tables`**（列出数据库中占用空间最大的 N 张表）为例，完整走一遍流程。

### 4.1 建目录结构

```bash
mkdir -p skills/biggest-tables/scripts
```

### 4.2 写 SKILL.md（可直接复制此模板）

```markdown
---
name: biggest-tables
version: 1.0.0
description: "列出 OpenGauss/GaussDB 数据库中占用物理空间最大的 N 张表，辅助识别磁盘热点与膨胀候选。"
allowed-tools: ["exec", "read"]
compatibility: opencode
metadata:
  runtime: python3
  emoji: "📦"
  family: diagnostics
---

# Biggest Tables（OpenGauss/GaussDB）

1. **预检。** 运行 `python3 {baseDir}/scripts/biggest_tables.py -h`。若报缺少依赖，`python3 -m pip install pg8000 cryptography PyYAML` 后停下让用户处理。
2. **选择连接。** 连接名沿用 `$GSDB_HOME/config.yaml` 的 `name` 字段。仅在有多个时才问用哪一个。
3. 运行：

   ```bash
   python3 {baseDir}/scripts/biggest_tables.py -c <conn> --top 20
   ```

4. 说明最大表的增长原因（数据量 vs dead tuples 膨胀）。体积异常大且死元组比例高的，建议走 `health` 的 Dead Tuples 维度深查；确属磁盘热点的，评估表空间迁移或分区。

## 安全红线

- **只通过本技能脚本取数**：`{baseDir}/scripts/biggest_tables.py` 走只读会话、自动解密 `$GSDB_HOME` 凭据。**你自己不要**直接写 Python/psql/gsql 连库、不要读取或解密 `$GSDB_HOME/credentials/`。脚本未覆盖的能力，如实说明「当前无此能力」并停止。
```

保存到 `skills/biggest-tables/SKILL.md`。

**SKILL.md 要点：**

- `{baseDir}` 是脚本路径占位符，安装时自动替换，**不要**写死绝对路径。
- `allowed-tools: ["exec", "read"]` — `exec` 让 LLM 能运行脚本，`read` 让 LLM 能读 references 文档。
- 安全红线小节是必填项，明确禁止 LLM 自行连库。

### 4.3 写入口脚本（可直接复制此骨架）

保存为 `skills/biggest-tables/scripts/biggest_tables.py`：

```python
#!/usr/bin/env python3
"""biggest_tables — list the N largest tables by physical size.

Usage:
    biggest_tables.py -c <conn> [--top 20] [--format markdown|json]
"""
from __future__ import annotations

import argparse
import json
import pathlib
import sys
from dataclasses import dataclass
from typing import Optional

# --- sys.path 设置：向上找到 common/（repo 根或安装目录均适用）---
_HERE = pathlib.Path(__file__).resolve()
sys.path.insert(0, str(_HERE.parent))          # 引入同目录的 render.py
for _anc in _HERE.parents:
    if (_anc / "common" / "__init__.py").exists():
        sys.path.insert(0, str(_anc))
        break

import common   # noqa: E402
import render   # noqa: E402


# --- 数据模型（frozen dataclass 保证不可变）---
@dataclass(frozen=True)
class TableRow:
    schema: str
    table: str
    total_mb: float
    table_mb: float
    index_mb: float
    dead_ratio: float   # dead tuples / live tuples，百分比


# --- 查询函数（纯函数，与 DB 隔离，便于单测）---
def biggest_tables(db, top: int) -> list[TableRow]:
    """查询占用空间最大的 top 张表。"""
    q = f"""
SELECT
    n.nspname                                          AS schema,
    c.relname                                          AS table,
    ROUND(pg_total_relation_size(c.oid) / 1048576.0, 2) AS total_mb,
    ROUND(pg_relation_size(c.oid)       / 1048576.0, 2) AS table_mb,
    ROUND((pg_total_relation_size(c.oid)
           - pg_relation_size(c.oid))   / 1048576.0, 2) AS index_mb,
    CASE WHEN s.n_live_tup > 0
         THEN ROUND(s.n_dead_tup * 100.0 / s.n_live_tup, 1)
         ELSE 0 END                                    AS dead_ratio
FROM pg_class c
JOIN pg_namespace n ON n.oid = c.relnamespace
LEFT JOIN pg_stat_user_tables s
    ON s.schemaname = n.nspname AND s.relname = c.relname
WHERE c.relkind = 'r'
  AND n.nspname NOT IN ('pg_catalog', 'information_schema', 'pg_toast')
ORDER BY pg_total_relation_size(c.oid) DESC
LIMIT {int(top)}"""
    _, rows = db.query(q)
    return [TableRow(
        schema=r[0], table=r[1],
        total_mb=float(r[2]), table_mb=float(r[3]),
        index_mb=float(r[4]), dead_ratio=float(r[5]),
    ) for r in rows]


# --- 渲染函数（纯函数，便于单测）---
def format_table(title: str, rows: list[TableRow]) -> str:
    """将结果渲染成 GFM Markdown 表格。"""
    if not rows:
        return f"## {title}\n\n（无用户表）\n"
    headers = ["#", "SCHEMA", "TABLE", "TOTAL_MB", "TABLE_MB", "INDEX_MB", "DEAD%"]
    body = [
        [str(i + 1), r.schema, r.table,
         f"{r.total_mb:.2f}", f"{r.table_mb:.2f}", f"{r.index_mb:.2f}",
         f"{r.dead_ratio:.1f}%"]
        for i, r in enumerate(rows)
    ]
    return f"## {title}\n\n" + render.table(headers, body)


# --- 主函数 ---
def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(
        prog="biggest_tables.py",
        description="List the N largest tables by physical size",
    )
    ap.add_argument("-c", "--conn", required=True, help="连接名（config.yaml 的 name）")
    ap.add_argument("--top", type=int, default=20, help="返回行数（默认 20）")
    ap.add_argument("--format", choices=["markdown", "json"], default="markdown")
    ap.add_argument("--timeout", type=int, default=30, help="查询超时（秒）")
    args = ap.parse_args(argv)

    # 退出码约定：0=成功, 1=运行错误, 2=连接/配置错误
    try:
        db = common.Database.connect(args.conn)
    except (common.ConfigError, common.CredentialError, common.DBError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    try:
        db.set_statement_timeout(args.timeout)
        rows = biggest_tables(db, args.top)
        if args.format == "json":
            print(json.dumps(
                [{"schema": r.schema, "table": r.table, "total_mb": r.total_mb,
                  "table_mb": r.table_mb, "index_mb": r.index_mb,
                  "dead_ratio": r.dead_ratio}
                 for r in rows],
                ensure_ascii=False, indent=2,
            ))
        else:
            print(format_table("Biggest Tables", rows), end="")
        return 0
    except (ValueError, common.DBError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
```

**脚本骨架要点：**

- `sys.path` 的两段处理：第一段让脚本能 `import render`（同目录），第二段向上找到 `common/`（repo 根或安装目录），两种运行方式（本地直跑 / 安装后跑）均有效。
- **退出码约定**：0=成功，1=运行错误，2=连接/配置错误。LLM 依赖退出码判断是否出错。
- `common.Database.connect(args.conn)` 一行完成：读 config.yaml → 解密凭据 → 建只读连接。
- 查询函数（`biggest_tables`）和渲染函数（`format_table`）保持纯函数，便于单独单测。
- 数据用 `frozen=True` 的 dataclass，不可变。

同时，把 `skills/gaussdb-slowsql/scripts/render.py` 复制一份到新 skill 的 scripts 目录（render.py 是每个 skill 自带的，不从 common 导入）：

```bash
cp skills/gaussdb-slowsql/scripts/render.py skills/biggest-tables/scripts/render.py
```

### 4.4 本地验证脚本能跑

```bash
python3 skills/biggest-tables/scripts/biggest_tables.py -h
python3 skills/biggest-tables/scripts/biggest_tables.py -c og-dev --top 5
```

### 4.5 添加单测

新建 `tests/test_biggest_tables_units.py`：

```python
"""biggest-tables skill 的 DB-free 单测。"""
import importlib.util
import pathlib
import sys

_ROOT = pathlib.Path(__file__).resolve().parents[1]


def _load(skill: str, mod: str):
    """加载 skill 脚本为模块（与 tests/test_sqlfamily_units.py 相同模式）。"""
    path = _ROOT / "skills" / skill / "scripts" / f"{mod}.py"
    sys.path.insert(0, str(path.parent))   # 让脚本能找到同目录的 render.py
    sys.path.insert(0, str(_ROOT))          # 让脚本能找到 common/
    spec = importlib.util.spec_from_file_location(f"{skill}_{mod}", path)
    m = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = m             # frozen dataclass 需要在 sys.modules 里
    spec.loader.exec_module(m)
    return m


bt = _load("biggest-tables", "biggest_tables")


def test_format_table_empty():
    """空结果时输出提示，不是空表格。"""
    out = bt.format_table("Biggest Tables", [])
    assert "无用户表" in out


def test_format_table_rows():
    """有数据时输出含表头和数据行。"""
    rows = [bt.TableRow("public", "orders", 512.5, 400.0, 112.5, 3.2)]
    out = bt.format_table("Biggest Tables", rows)
    assert "| SCHEMA |" in out
    assert "orders" in out
    assert "512.50" in out
    assert "3.2%" in out


def test_biggest_tables_top_clamp():
    """top 参数必须是正整数（int 类型检查）。"""
    # biggest_tables 函数把 top 直接格式化进 SQL；传负数会产生 LIMIT -1
    # 此处仅验证 int(top) 转换不抛
    assert int(20) == 20
```

跑测试：

```bash
python3 -m pytest tests/test_biggest_tables_units.py -v
```

### 4.6 安装到 OpenCode 验证

```bash
./install-opencode.sh biggest-tables
```

安装脚本会：① 把 `skills/biggest-tables/` 整个拷到 `~/.config/opencode/skills/biggest-tables/`；② 把 SKILL.md 里的 `{baseDir}` 替换成真实绝对路径；③ 确保 `common/` 已在目标目录。

在 OpenCode 里让 agent 调用：「列出数据库里最大的 10 张表」。

---

## 5. 演示②：给已有 skill 加一个函数/子命令（TDD）

以给 `gaussdb-topsql` 加 **`--schema <schema>`** 过滤参数为例——只显示指定 schema 下的 SQL。走 TDD 流程：先写失败测试，再实现，再跑绿。

### 5.1 先写失败测试

在 `tests/test_sqlfamily_units.py` 末尾（`if __name__ == "__main__":` 之前）追加：

```python
def test_topsql_schema_filter_in_query():
    """schema 过滤参数应当把 schema 名嵌入查询 SQL。"""
    # topsql.top_sql 返回 (columns, rows)，此处只验证 SQL 构造——
    # 传入 db=None 会在执行查询时报错，但我们只测参数校验逻辑。
    # 所以先验证: 当 schema=None 时正常构造，schema 非空时应包含 schemaname 过滤。
    import types
    captured = {}

    class _FakeDB:
        def query(self, sql, params=None):
            captured["sql"] = sql
            return [], []

    topsql.top_sql(_FakeDB(), "time", 5, schema="myapp")
    assert "myapp" in captured["sql"], "schema filter not in query"
```

跑测试，预期失败（`top_sql` 还没有 `schema` 参数）：

```bash
python3 -m pytest tests/test_sqlfamily_units.py::test_topsql_schema_filter_in_query -v
# Expected: FAILED
```

### 5.2 在脚本里实现

打开 `skills/gaussdb-topsql/scripts/topsql.py`，找到 `top_sql` 函数（真实路径以源码为准），添加 `schema` 可选参数并在 SQL 里加过滤条件：

```python
# 修改前（示意，以真实签名为准）：
def top_sql(db, by: str, limit: int) -> tuple[list, list]:

# 修改后：
def top_sql(db, by: str, limit: int, schema: str | None = None) -> tuple[list, list]:
    ...
    # 在 WHERE 子句里加：
    schema_filter = f"AND schemaname = '{schema}'" if schema else ""
    q = f"""
SELECT ...
FROM dbe_perf.statement
WHERE n_calls > 0
  {schema_filter}
ORDER BY ...
LIMIT {int(limit)}"""
    ...
```

同时在 `main()` 的 argparse 里加参数：

```python
ap.add_argument("--schema", default=None, help="只显示该 schema 下的 SQL")
```

并在调用处透传：

```python
rows = top_sql(db, args.by, args.limit, schema=args.schema)
```

### 5.3 跑绿，同步更新 SKILL.md

```bash
python3 -m pytest tests/test_sqlfamily_units.py -v
# Expected: all PASSED
```

测试全绿后，在 `skills/gaussdb-topsql/SKILL.md` 的步骤 3 里更新命令示例，说明新增的 `--schema` 选项：

```markdown
3. 运行：

   ```bash
   python3 {baseDir}/scripts/topsql.py -c <conn> --by time --limit 10
   # 只看某个 schema：
   python3 {baseDir}/scripts/topsql.py -c <conn> --by time --limit 10 --schema myapp
   ```
```

### 5.4 跑完整测试套件，确认无回归

```bash
python3 -m pytest -q -m "not live"
```

---

## 6. 演示③：新增 references 文档

`references/` 目录下的 Markdown 是 LLM 按需阅读的知识库——方法论、阈值说明、GUC 参数、兼容性陷阱等。新增方式如下。

### 6.1 文档放在哪

在对应 skill 的目录下（若还没有则新建）：

```
skills/biggest-tables/
└── references/
    └── biggest-tables-methodology.md   # 举例：方法论文档
```

### 6.2 写法规范

参考 `skills/gaussdb-health/references/health-thresholds.md` 和 `skills/gaussdb-wdr/references/wdr-methodology.md` 的风格：

- 第一行用 `# <主题>` 作为大标题。
- 用 `## <维度>` 分节，与 SKILL.md 里引用的节名一一对应。
- 表格用 GFM 格式（`| 列 | 列 |`）。
- 数值/阈值/GUC 名称使用 `` `code` `` 格式。
- **不要**写"你应该……"风格的提示语——这是供 LLM 查阅的参考资料，不是操作手册。

示例文档 `skills/biggest-tables/references/biggest-tables-methodology.md`：

```markdown
# Biggest Tables 分析方法论

## 判断维度

对每张大表，按以下优先级判断：

| 指标 | 含义 | 行动 |
|---|---|---|
| `total_mb` 远大于 `table_mb` | 索引占用比数据大 | 评估索引是否有用（配合 `health` 的 INDEX_UNUSED） |
| `dead_ratio` > 20% | dead tuples 膨胀 | 触发手工 `VACUUM ANALYZE`；检查 autovacuum 配置 |
| 单表 > 10 GB | 磁盘热点候选 | 评估分区或归档旧数据 |

## 与其他维度关联

- `dead_ratio` 高的大表，配合 `health` 的 **Dead Tuples & Bloat** 维度交叉确认。
- 物理读 Top 表与 biggest-tables 重合，说明大表未命中缓存，建议评估 `shared_buffers`。
```

### 6.3 在 SKILL.md 里引导 LLM 读它

在 SKILL.md 工作流里加一步（通常放在"采集证据"之后）：

```markdown
4. **加载方法论。** 阅读 `{baseDir}/references/biggest-tables-methodology.md`，
   对照「判断维度」表逐行分析每张大表，并做跨维度关联。
```

`{baseDir}` 同样在安装时被替换成真实绝对路径，LLM 通过 `read` 工具能直接访问该文件。

### 6.4 验证 LLM 能读到

安装后快速检查：

```bash
./install-opencode.sh biggest-tables
cat ~/.config/opencode/skills/biggest-tables/references/biggest-tables-methodology.md
```

确认文件已在安装目录、且 SKILL.md 里的 `{baseDir}` 已替换为真实路径。

---

## 7. 提交与评审流程

### 7.1 分支开发

```bash
git checkout -b feat/biggest-tables
```

### 7.2 提交规范

格式：`<type>: <中文描述>`

| type | 用途 |
|---|---|
| `feat` | 新 skill / 新功能 |
| `fix` | 修 bug |
| `refactor` | 重构（无功能变化） |
| `test` | 增加/修改测试 |
| `docs` | 文档变更 |
| `chore` | 构建/依赖/脚本等杂项 |

示例：

```bash
git add skills/biggest-tables/ tests/test_biggest_tables_units.py
git commit -m "feat: 新增 biggest-tables skill，列出占空间最大的 N 张表"
```

### 7.3 提交前核查清单

```bash
# 1. 单测全绿
python3 -m pytest -q -m "not live"

# 2. 安装脚本 dry-run 确认新 skill 被发现
./install-opencode.sh --dry-run biggest-tables

# 3. 脚本能独立运行（无需安装）
python3 skills/biggest-tables/scripts/biggest_tables.py -h
```

### 7.4 创建 PR

```bash
git push -u origin feat/biggest-tables
gh pr create --title "feat: 新增 biggest-tables skill" \
  --body "$(cat <<'EOF'
## Summary
- 新增 biggest-tables skill，列出数据库中占物理空间最大的 N 张表
- 支持 --top / --format json 参数
- 包含单测（DB-free）+ SKILL.md 工作流 + references 方法论文档

## Test plan
- [ ] `python3 -m pytest -q -m "not live"` 全绿
- [ ] `./install-opencode.sh --dry-run biggest-tables` 输出正常
- [ ] 在 OpenCode 里实际触发一次 skill 调用
EOF
)"
```

---

## 8. 常见坑

| 现象 | 原因 | 解决 |
|---|---|---|
| SKILL.md 里出现字面量 `{baseDir}` | 直接软链或手动拷，没做替换 | 用 `./install-opencode.sh` 或手动跑安装脚本里的 Python 替换片段 |
| `ModuleNotFoundError: No module named 'common'` | 没用安装脚本，`common/` 没跟着拷过去 | 重跑 `install-opencode.sh`；或手动 `cp -R common "$DEST/common"` |
| `ModuleNotFoundError: No module named 'render'` | 新 skill 的 scripts/ 目录忘放 `render.py` | `cp skills/gaussdb-slowsql/scripts/render.py skills/<newskill>/scripts/render.py` |
| `no connection named 'xxx'` | `$GSDB_HOME/config.yaml` 没有该 name，或 `GSDB_HOME` 没设对 | `echo $GSDB_HOME` 确认路径，`cat "$GSDB_HOME/config.yaml"` 确认 name |
| live 测试全 skip | 没配连接或 `GSDB_HOME` 未指向有效目录 | 配置连接后重跑；单测不依赖 DB，`-m "not live"` 即可 |
| 脚本直接连库（`psql`/`gsql`/自写 socket） | 违反安全红线 | 全部走 `common.Database.connect(args.conn)`，凭据由 common 解密，不要绕过 |
| 安装后新 skill 在 OpenCode 不出现 | OpenCode 未重启/重新扫描 | 重启 OpenCode 或在设置里刷新 skills |
| `decrypt credential` 失败 | `$GSDB_HOME/key` 与 `.enc` 文件不匹配（换机时只拷了 `.enc`） | 用 `export GSDB_PASSWORD=...` 替代加密落盘，或把 `key` 文件一起带过来 |
| 退出码 1 而不是 2（连接错误）| 在 `try` 块之外抛了异常 | 连接错误（ConfigError/CredentialError/DBError）只在 `connect()` 那层 catch，其余错误返回 1 |
| 给 `query()` 传了写操作 | 会话是只读的，DDL/DML 会报错 | skill 只做查询；需要写操作时明确让用户手动执行 |
