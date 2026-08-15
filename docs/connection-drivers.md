# 连接驱动（connection-drivers）

`common/` 连接层支持两种后端驱动，通过 `$GSDB_HOME/config.yaml` 的 `driver` 字段配置，也可通过环境变量覆盖。

---

## `driver` 字段

```yaml
# $GSDB_HOME/config.yaml
#
# 首行决定所有 skill 怎么连数据库。gaussdb-login 按它决定交互方式：
#   gsql  直连，连接在 db_connections 下按应用分组预先配好，登录时列菜单让用户挑
#   api   走 GRMP 中间件，连接由 gaussdb-login 按用户给的库名现场构造
connection_mode: gsql

db_connections:
  app1:                       # 应用分组。**跨应用不能重名** —— `-c conn1`
    - name: og-pri            # 无从判断该用哪一个，取第一个会连到另一个应用
      type: opengauss         # 的库上，执行成功、结果无关、不报错
      host: 127.0.0.1
      port: 5435
      database: postgres
      user: gaussdb
      driver: gsql            # gsql（默认）| pg8000 | grmp
```

`connection_mode: api` 时改用 `api_connection`：

```yaml
connection_mode: api

api_connection:
  - host: ucmp-grmp-web-d.sdc.cs.icbc
    host_env: GRMP_API_HOST      # 推荐；也支持内联 host:，但环境变量优先
    port: 80
    token_env: GRMP_AUTH_TOKEN   # 推荐；也支持内联 token:，但环境变量优先
```

> **旧的平铺 `connections:` 列表仍然可用**，与 `db_connections` 合并解析，
> 只是没有应用分组。不强制迁移。

口令三个来源，优先级从高到低：环境变量 `GSDB_PASSWORD` > 配置里的内联
`password`（`encrypted: true` 时是 base64 密文，与 `credentials/` 同一把钥匙、
同一个 AAD）> `credentials/<name>.enc`。

| 值 | 说明 |
|---|---|
| `gsql`（默认） | 本机 `gsql` 命令行客户端，每条查询起一个子进程，通过 `-c` 执行 |
| `pg8000` | 纯 Python 驱动，TCP 直连，无需安装 gsql 二进制 |

旧版 config.yaml 不含 `driver` 字段时，默认视为 `gsql`（向后兼容）。

---

## 环境变量

| 变量 | 说明 |
|---|---|
| `GDAA_GSQL` | 覆盖 gsql 二进制路径（默认在 `PATH` 查找 `gsql`）。示例：`GDAA_GSQL=/usr/local/bin/gsql` |
| `PGSSLMODE` | 覆盖 SSL 模式（若 config.yaml 里也有 `sslmode` 字段，配置文件优先，未设置时 `PGSSLMODE` 生效）；gsql 后端通过 `env["PGSSLMODE"]` 传入子进程 |
| `GSDB_HOME` | 连接配置目录路径（任意名/路径，默认 `$GSDB_HOME`，优先级 `GSDB_HOME` > `GDAA_HOME`） |
| `GSDB_PASSWORD` | 临时覆盖存储的密码（一次性使用 / CI 用；旧 `GDAA_PASSWORD` 仍兼容，优先级 `GSDB_PASSWORD` > `GDAA_PASSWORD`） |

---

## 连接级自动兜底（fallback）

`Database.open()` 门面实现了**连接级自动兜底**：

1. 优先尝试 `conn.driver`（config.yaml 里配置的驱动）。
2. 若该驱动抛出 `DBError`（二进制缺失、连接拒绝、认证失败），自动尝试另一个驱动。
3. 两个驱动都失败时，合并两个错误消息，抛出 `DBError`。

```
gsql 不可用（DBError）
    ↓ 自动兜底
pg8000 连接成功
    ↓
返回 Database（透明，调用方无感知）
```

**示例**：在 macOS 开发机（无 `gsql` 二进制）上，配置了 `driver: gsql` 的连接会自动降为 pg8000，所有 skill 照常工作，无需改 config.yaml。

如果你希望**跳过 gsql 尝试**（节省延迟 / 避免日志噪音），在 config.yaml 中显式设置 `driver: pg8000`。

---

## ⚠️ 限制:gsql 不支持 hypopg 多语句验证(已修:改为显式报错)

> **原为 Critical（gaussdb-sqltune/gaussdb-proctune verify 步骤在真 gsql 下静默假负例）；现已修:
> verify 入口在无持久会话的后端下会明确报错，不再静默。**

**根因**：gsql 后端是「每查询一个 `gsql -c` 子进程」的无状态模型；会话级状态（GUC、
hypopg 虚拟索引）**无法跨多次 `db.*` 调用存活**。

**具体影响**：`gaussdb-sqltune` / `gaussdb-proctune` 的索引验证（`hypoindex.py`）步骤依赖以下会话内顺序：

1. `SET enable_hypo_index = on;`
2. `SELECT hypopg_create_index(...);`
3. `EXPLAIN ... <query>;`（必须在同一会话中看见虚拟索引）
4. `SELECT hypopg_drop_index(...);`

在**真 gsql** 下（Linux 主机，gsql 二进制可用），每步都是一个独立子进程，虚拟索引在
下一个子进程里消失 → `EXPLAIN` 看不到虚拟索引 → `hypo_cost == orig_cost` →
speedup ≈ 1.0 → 所有候选会被否决。**修复前**这是一个静默的假负例。

**已修（守卫）**：`Backend.provides_session`（pg8000=True / gsql=False）暴露后端是否
提供持久会话；`gaussdb-sqltune` / `gaussdb-proctune` 的 `verify_indexes()` 入口检查 `db.provides_session`，
无持久会话时**抛 `DBError` 明确报错**（不再静默返回 speedup≈1.0）。skill 的验证降级块会把
该错误转成「索引验证不可用：…请用 driver: pg8000」的 note 展示给用户。

**影响范围**：

- 仅当 `driver: gsql` **真正生效**（Linux 主机装了 gsql 二进制）时触发。
- macOS / 无 gsql 主机会自动兜底到 pg8000，pg8000 使用持久 TCP 连接，hypopg 正常工作，**不受影响**。
- CI 容器（无 gsql）同 macOS，不受影响。

**规避方法**：对依赖 hypopg 的 skill（`gaussdb-sqltune`、`gaussdb-proctune` 的 verify 阶段）使用
`driver: pg8000`：

```yaml
# $GSDB_HOME/config.yaml
db_connections:
  app1:
    - name: og-pri
      ...
      driver: pg8000   # 确保 hypopg verify 阶段使用持久连接
```

**状态：已修**（commit `6b3e811`）。采用「显式报错」方案：`verify_indexes` 在无持久
会话的后端下抛 `DBError`，而非静默返回 speedup≈1.0。要在真 gsql 主机上实际跑 hypopg
验证,请对该连接设 `driver: pg8000`。（备选的「自动钉 pg8000」方案未采用——显式报错更
透明,不会在用户配了 gsql 时悄悄换后端。）后续 Linux+gsql 的 parity diff 仍建议包含
`gaussdb-sqltune verify` / `gaussdb-proctune verify` 用例作回归。

---

## json_agg 类型保真与已知差异

> **待验证（理论分析，未经 Linux + gsql 实证）**：本节基于两后端的代码路径推演，
> 尚未在具备 gsql 二进制的真库上做过 parity diff，请勿当作已确认结论。

**关键洞察**：gsql 后端把可包裹的 SELECT 包成
`SELECT json_agg(row_to_json(_t)) FROM (...) _t`，再用
`json.loads(text, parse_float=Decimal)` 解析。这意味着——对 **JSON 原生标量类型**，
gsql 解析出的 Python 值与 pg8000 返回的**完全一致**：

| 数据库类型 | JSON 中间形态 | gsql 后端 Python 值 | pg8000 后端 Python 值 | 是否一致 |
|---|---|---|---|---|
| 整数（`int`/`bigint`） | JSON number（整数） | `int` | `int` | ✅ 一致 |
| 数值/浮点（`numeric`/`float`/`double`） | JSON number（小数，有小数位） | `Decimal`（`parse_float=Decimal`） | `Decimal` | ✅ 一致（待验证） |
| 布尔（`bool`） | JSON `true`/`false` | `bool` | `bool` | ✅ 一致 |
| `NULL` | JSON `null` | `None` | `None` | ✅ 一致 |

> **numeric 细化说明（待验证）**：`numeric` 类型仅在**有小数位**时（如 `3.14`）在
> JSON 中呈现为小数，触发 `parse_float=Decimal` → `Decimal`。若无小数位（如
> `count(*)::numeric` 返回整数值 `42`），JSON 中表现为整数 → gsql 下解析为 `int`，
> 而 pg8000 返回 `Decimal('42')`。若探针对 `count(*)::numeric` 做精确类型检查，
> 需注意此差异。

> **jsonb 列说明（待验证）**：`jsonb` 列经 `row_to_json` 嵌入后，JSON 文档本身作为
> 嵌套 JSON 对象/数组存在 → gsql 下解析为 Python `dict`/`list`；pg8000 依版本不同
> 可能返回字符串或已解码对象。如探针涉及 `jsonb` 列，建议显式 `::text` 转型。

**EXPLAIN(FORMAT JSON) 文本旁路行形差异**：`EXPLAIN (FORMAT JSON)` 等以非 SELECT
关键字开头的语句走**文本旁路**（`parse_text_result`）：

- gsql 后端：`parse_text_result` 返回**逐行单元素 tuple** `[(line,), ...]`，JSON 文档
  按行切割。消费方须 `"".join(r[0] for r in rows) + json.loads(...)` 重组。
- pg8000 后端：返回单个已解码 Python 对象（`dict`）。
- `skills/gaussdb-sqltune/scripts/cost.py` 已兼容两路。新探针若用 `EXPLAIN(FORMAT JSON)` 必须
  同样处理，而非假设返回单个对象。

**真正的残留差异**只出现在 **非 JSON 原生类型**——这些类型在 `row_to_json` 里被
渲染成 ISO/文本字符串，于是经 gsql 解析后是 Python `str`，而 pg8000 返回的是带类型的对象：

| 数据库类型 | gsql 后端 Python 值 | pg8000 后端 Python 值 |
|---|---|---|
| 时间戳（`timestamp`/`timestamptz`） | `str`（ISO 串，如 `"2024-01-01T12:00:00"`） | `datetime.datetime` 对象 |
| 日期（`date`） | `str`（如 `"2024-01-01"`） | `datetime.date` 对象 |
| 时间（`time`） | `str` | `datetime.time` 对象 |
| 时间间隔（`interval`） | `str` | 类型化对象 |
| 数组（`array`） | `list`（JSON 数组，元素再按上表规则） | 类型化 `list` |
| 字节串（`bytea`） | `str`（文本表示） | `bytes` 对象 |

各 skill 的探针 SQL 均设计为对这些差异**不敏感**（比较前统一转为字符串，或只使用数值
大小而不依赖具体类型）。若新增探针涉及时间戳/日期/数组/bytea 等非 JSON 原生类型，
建议在 SQL 中显式 `CAST(... AS text)` 以消除差异。

---

## 主机要求（HOST REQUIREMENT）

**gsql 是 Linux 原生客户端，无 macOS 原生版本。**

| 场景 | 说明 |
|---|---|
| Linux 生产主机（含 gsql） | `driver: gsql` 完全可用；若不存在则兜底 pg8000 |
| macOS 开发机 | gsql 二进制不存在，`shutil.which("gsql")` 返回空，自动兜底到 pg8000；或显式配置 `driver: pg8000` 跳过尝试 |
| CI/容器（无 gsql） | 同 macOS，兜底 pg8000 |

> 若要固定只用 pg8000（推荐 macOS 开发 / CI 环境）：在 config.yaml 对应连接中设置 `driver: pg8000`，消除兜底路径的日志干扰。

---

## 排错

### gsql 二进制缺失

```
DBError: gsql binary not found (set GDAA_GSQL or add gsql to PATH)
```

**原因**：`PATH` 中找不到 `gsql`，且未设置 `GDAA_GSQL`。

**解决**：
- macOS / CI：改用 pg8000（`driver: pg8000` 或等待自动兜底）。
- Linux：安装 openGauss 客户端包，确保 `gsql` 在 `PATH`，或 `export GDAA_GSQL=/path/to/gsql`。

### 连接被拒（Connection refused）

```
DBError: pg8000: ... Connection refused
DBError: gsql: ... could not connect to server: Connection refused
```

**原因**：数据库未启动，或 host/port 配置错误。

**检查**：
```bash
# 确认端口可达
nc -zv 127.0.0.1 5435
# 查 config.yaml
cat $GSDB_HOME/config.yaml
```

### 认证失败

```
DBError: password authentication failed for user "gaussdb"
```

**解决**：用 `$GSDB_HOME connect add <name> ...` 重新保存凭据，或 `export GSDB_PASSWORD=...`临时覆盖。

---

## 待验证 — gsql vs pg8000 真库 parity diff

在配备 gsql 二进制的 **Linux 主机**上，可执行如下 parity 验证：

```bash
# gsql 路径
python3 skills/gaussdb-health/scripts/health.py --conn og-pri > /tmp/health.gsql.txt

# 切换 driver
# 临时改 config.yaml driver: pg8000，或用一个不存在的路径强制 gsql 走 "binary not found" 分支兜底到 pg8000：
GDAA_GSQL=/nonexistent/path python3 skills/gaussdb-health/scripts/health.py --conn og-pri > /tmp/health.pg.txt

diff /tmp/health.gsql.txt /tmp/health.pg.txt || true
```

**当前状态**：本机为 macOS，gsql 不可用，parity diff **延迟至具备 gsql 的 Linux 主机后再执行**。预期差异仅限上文「已知差异」表中**非 JSON 原生类型**的差异（时间戳/日期/数组/bytea 的 `str` vs 类型化对象）；int/Decimal/bool/NULL 在两后端一致。各 skill 已设计为对其不敏感，输出内容应完全一致。

> **重要**：Linux + gsql 的 parity diff **必须**包含 `gaussdb-sqltune verify` / `gaussdb-proctune verify`
> 用例（当前示例仅列 gaussdb-health，无法暴露上文所述的 hypopg 静默否决 Critical）。在 Linux 主机
> 上执行 parity 时，需分别对 `driver: gsql` 和 `driver: pg8000` 运行完整的 gaussdb-sqltune/gaussdb-proctune
> 流程（含索引验证），对比推荐结果是否一致——若 gsql 下索引验证均被否决而 pg8000 下有推荐，
> 即可确认该 Critical 已复现，需在修复 `require_session` 语义后重测。
