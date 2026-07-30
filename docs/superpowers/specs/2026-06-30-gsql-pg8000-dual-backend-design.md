# common 连接层 gsql + pg8000 双后端设计

- 日期：2026-06-30
- 目标模块：`common/`（Python 共享连接层）
- 影响面：各 skill 代码**零改动**（`Database` 公开接口不变）
- 上游：现状 `common/db.py` 仅 pg8000；本设计加入 gsql 后端并使其成为默认
- 状态：待审核（review 通过后进入写实现计划）

---

## 1. 背景与目标

当前 `common/db.py` 用 `pg8000.dbapi`（纯 Python wire 协议）直连 openGauss/GaussDB，返回原生类型（`int/Decimal/datetime/None`），默认把会话钉成 READ ONLY。各 skill 通过统一的 `Database` 门面消费它。

需求：让连接层**同时支持 `gsql`（openGauss 原生命令行客户端）与 `pg8000`，并以 gsql 为默认/主用，pg8000 退为可选**。动机由用户确认为「gsql 当默认/主用」。

gsql 与 pg8000 的本质差异决定了难点：gsql 是命令行客户端，输出是**纯文本**、**无类型信息**、**NULL 与空串不可区分**、**会话状态不跨进程保留**，而下游 health/proctune/slowsql 等要对结果做**数值比较和运算**、并使用**参数化查询**（`format`/`%s` 风格，且字符串与数值参数混用）。本设计正面解决这些差异，使 gsql 路径对各 skill 透明。

### 1.1 调用面（必须对等的 `Database` 公开接口）

| 接口 | 用法 | 消费方（示例） |
|---|---|---|
| `Database.connect(name, read_only=True)` | 命名连接工厂 | slowsql / explain / wdr / proctune / health |
| `Database.open(conn, password, read_only=True)` | 显式打开 | 同上底层 |
| `db.query(sql, params=None) -> (cols, rows)` | **参数化**查询 | proctune `(name, schema, schema)`、health `(th, top)` |
| `db.scalar(sql, params=None)` | 取首行首列 | wdr/health `SHOW ...` |
| `db.execute(sql, params=None)` | 无结果语句（SET/调用） | 各处 |
| `db.query_in_rollback(sql, params=None)` | EXPLAIN ANALYZE on DML，包回滚事务 | explain `--analyze` |
| `db.set_statement_timeout(seconds)` | 限服务端执行时长 | slowsql/explain/wdr/proctune |
| `db.close()` / 上下文管理器 | 关闭 | 各处 |

实证：`grep` 确认绝大多数调用为 `_, rows = db.query(...)`（**忽略 cols**）；占位符为 `format`（`pg8000.dbapi.paramstyle == 'format'`，即 `%s`），且同批查询里 `proname = %s`（字符串）与 `LIMIT %s` / `> %s`（数值）**混用**。

---

## 2. 目标 / 非目标

**目标**
1. `common` 连接层支持 gsql 与 pg8000 两个后端，**默认 gsql**，pg8000 可选。
2. `Database` 公开接口与返回类型语义保持不变 —— **各 skill 不改一行代码**。
3. gsql 路径下：参数化安全（无注入）、结果回原生类型（含 NULL/Decimal 保真）、只读钉、回滚验证、超时均与 pg8000 路径行为对齐。
4. 后端按 `config.yaml` 每连接 `driver` 字段选择，首选连不上**自动兜底**到另一个。

**非目标**
- 不改动 Go 侧 `openclaw_dbaa`（pgx / gaussdb-go）——它不用 pg8000，不在范围内。
- 不支持 docker exec / ssh 传输（本轮仅**本机 TCP 直连** gsql；传输可插拔留作未来）。
- 不引入环境变量级全局后端切换（YAGNI；`driver` 字段 + 兜底已够）。
- 不改各 skill 的展示/渲染逻辑。

---

## 3. 关键决策（含被否方案）

### 决策 A — gsql 会话状态：无状态 + 前缀注入（A1，采纳）
gsql「一次 `-c` 一个进程」，会话级设置不跨进程保留。

| 方案 | 做法 | 结论 |
|---|---|---|
| **A1（采纳）** | 每次查询 `gsql -c "<前缀 SET...>; <sql>"`，把只读钉、`statement_timeout` 作为 Python 端状态，每次拼到 SQL 前；`query_in_rollback` 用 `BEGIN; ...; ROLLBACK;` 单进程 | 健壮、可调试、输出边界清晰；代价是每查询一子进程（探针查询量有限，可接受） |
| A2（否） | 常驻交互 gsql 进程，stdin 喂 SQL / stdout 读结果 | 交互式输出（提示符、无记录分隔）解析极脆，否决 |

### 决策 B — gsql 文本结果回原生类型：json_agg 包裹（B1，采纳）

| 方案 | 做法 | 结论 |
|---|---|---|
| **B1（采纳）** | 对 SELECT 改写为 `SELECT json_agg(row_to_json(_t)) FROM (<原SQL>) _t`，gsql 取回单个 JSON，`json.loads(..., parse_float=Decimal)` 解析；非 SELECT（SHOW/SET/EXPLAIN/DDL）走文本旁路 | 类型/NULL/列序保真，数值用 `Decimal` 不丢精度；SQL 全是一手写的，靠首关键字判别旁路是安全的 |
| B2（否） | `-F $'\x01' -P null=$'\x02'` 自定义分隔与 NULL 标记，按字节切 | 能分清 NULL，但**类型仍是文本**，得靠猜（如 `"9.2"` 误判为 float），否决 |

---

## 4. 架构 / 文件布局

`Database` 由「直接持有 pg8000 连接」改为「门面 + 后端委托」。后端封装底层操作，门面提供稳定公开接口、只读钉、错误格式化、后端选择与兜底。

```
common/
  db.py                  # Database 门面：connect/open（选后端+兜底）、只读钉、公开 API（不变）
  backends/
    __init__.py          # 后端注册：{"gsql": GsqlBackend, "pg8000": Pg8000Backend}
    base.py              # Backend 抽象基类/协议
    pg8000_backend.py    # 现有 pg8000 逻辑搬迁至此
    gsql_backend.py      # 新增：subprocess gsql + json_agg + 参数注入
  config.py              # Connection 加 driver 字段 + 校验
  credential.py          # 不变
  __init__.py            # 导出不变（Database/DBError/...）
```

符合「多小文件、高内聚低耦合」风格；`db.py` 不再臃肿。

### 4.1 Backend 抽象（`base.py`）
每个后端是不可变的薄对象，持有「打开后的句柄 + 会话设置」，实现：

| 方法 | 语义 |
|---|---|
| `open(conn, password, read_only) -> Backend` | 连接并验活（连不上抛 `DBError`，供兜底判别） |
| `query(sql, params) -> (cols, rows)` | 类型化结果 |
| `execute(sql, params) -> None` | 无结果语句 |
| `query_in_rollback(sql, params) -> (cols, rows)` | 回滚事务内执行 |
| `set_statement_timeout(seconds) -> None` | 记录/应用语句超时 |
| `close() -> None` | 释放 |

门面 `Database` 持有一个 `Backend` 实例并转发；只读钉与 `_format_pg_error` 风格在门面层统一。

> 不可变约定：`set_statement_timeout` 等「会话设置」对 gsql 后端是内部前缀状态。为遵循不可变风格，后端的会话设置以「返回携带新设置的新实例」或集中在一个独立 `GsqlSession` 值对象里承载，避免就地改字段；具体形态在实现计划里定（优先值对象，回避 in-place mutation）。

---

## 5. config 变更（`config.py`）

`Connection` 增加 `driver` 字段：

```python
@dataclass(frozen=True)
class Connection:
    name: str
    type: str           # opengauss | gaussdb（不变）
    host: str
    port: int
    database: str
    user: str
    sslmode: str = ""
    driver: str = "gsql"   # 新增：gsql（默认） | pg8000
```

- 校验：`driver in {"gsql", "pg8000"}`，否则 `ConfigError`。
- `load()` 解析 `item.get("driver", "gsql")`，对**缺失字段的旧 config 向后兼容**（缺省即 gsql）。
- `config.yaml` 样例新增 `driver: gsql|pg8000`（可省，省即 gsql）。

---

## 6. gsql 后端实现细节（`gsql_backend.py`）

### 6.1 传输
本机 TCP 直连：`subprocess.run(["gsql", "-h", host, "-p", str(port), "-U", user, "-d", database, ...], ...)`。
- gsql 二进制：`GDAA_GSQL` 环境变量覆盖，缺省走 PATH 找 `gsql`；找不到给清晰 `DBError`。
- 子进程墙钟超时：连通探针 15s（对齐 pg8000 `CONNECT_TIMEOUT`）；查询取 `statement_timeout + 缓冲`。

### 6.2 凭据 / SSL（安全）
- 密码经 **`PGPASSWORD` 环境变量**传子进程（gsql 遵循 PGPASSWORD，itest 已证）——**绝不进 argv**，防 `ps`/日志泄露（合规 security 规则：secrets 不入命令行）。
- `sslmode` → `PGSSLMODE` 环境变量。
- 子进程 `env` 仅注入所需变量，不继承多余敏感环境。

### 6.3 参数化（按类型注入，杜绝注入）
把 `%s`（左→右）改写为 gsql 变量引用，按 Python 实参**类型**选注入形式，并以 `-v pN=<value>` 传值：

| 实参类型 | 注入形式 | gsql 渲染 |
|---|---|---|
| `str` | `:'pN'` | gsql **自行安全转义**为带引号字面量 |
| `int` / `float` / `Decimal` | `:pN`（裸值） | 数值上下文（如 `LIMIT`）正确；值我方可控 |
| `bool` | 直接注入 `TRUE`/`FALSE` | 布尔字面量 |
| `None` | 直接注入 `NULL` | SQL NULL |
| 其它（bytes 等） | 抛 `DBError` | 探针不使用 |

- `%%` → 字面 `%`（防误判为占位符）。
- 占位符计数与 `params` 长度不符 → `DBError`（fail fast）。
- 安全要点：字符串走 `:'pN'`（由 gsql 转义，非手搓拼接），数值走裸值（类型受控），两者都不可被注入。

### 6.4 结果取回与类型恢复
按首关键字（去注释/空白）判别语句类别：

- **SELECT / WITH / VALUES / TABLE** → 包裹 `SELECT json_agg(row_to_json(_t)) FROM (<sql>) _t`，
  - gsql `-A -t -q -c "..."` 取回单个 JSON 串；
  - `json.loads(s, parse_float=Decimal)` 解析为 `list[dict]`；
  - `cols = list(rows[0].keys())`（PG `row_to_json` 保列序，dict 保插入序）；空集 → json_agg 返回 NULL → `([], [])`（与「忽略 cols」调用方兼容）；
  - rows 转 `list[tuple]`（按 cols 序）。
- **SHOW / SET / 其它非 SELECT** → 文本旁路：`-A -t`，按行/`|` 切，单值场景供 `scalar()`。
- **EXPLAIN / `query_in_rollback`** → 文本旁路：保留 EXPLAIN 的逐行文本（explain skill 本就按文本行处理计划）。

> 类型保真已知差：JSON 无 datetime —— `row_to_json` 把时间戳渲染为 ISO 字符串，而 pg8000 返回 `datetime` 对象。见 §8 风险，靠测试校验下游是否实际依赖 `datetime` 类型。

### 6.5 只读 & 回滚
- `read_only=True`：每次查询前缀 `SET default_transaction_read_only = on;`（单 `-c` 内同事务生效）。
- `query_in_rollback`：`gsql -c "BEGIN; <EXPLAIN ANALYZE ...>; ROLLBACK;"`，`-q -A -t` 抑制 `BEGIN`/`ROLLBACK` 状态行，只留 EXPLAIN 输出；任何情况都不提交。
- `set_statement_timeout(n)`：记录到会话状态，作为前缀 `SET statement_timeout = n*1000;` 注入后续每次查询。

### 6.6 错误格式化
- gsql 加 `-v ON_ERROR_STOP=1`（出错非零退出）与 `-v VERBOSITY=verbose`（stderr 带 SQLSTATE）。
- 解析 stderr，尽量还原成 `ERROR: <msg> (SQLSTATE <code>)`，与 pg8000 路径 `_format_pg_error` 对齐（best-effort；无法提取时退回原文）。
- 非零退出但非 SQL 错误（如 gsql 不存在、连接被拒）→ 连接级 `DBError`，供兜底判别。

---

## 7. 后端选择与自动兜底（`db.py`）

```
Database.open(conn, password, read_only):
    preferred = conn.driver            # 默认 "gsql"
    other     = "pg8000" if preferred == "gsql" else "gsql"
    try:
        backend = REGISTRY[preferred].open(conn, password, read_only)   # 含验活 SELECT 1
    except DBError as e1:
        try:
            backend = REGISTRY[other].open(conn, password, read_only)   # 自动兜底
        except DBError as e2:
            raise DBError(合并 e1 + e2，注明两后端均失败)
    return Database(backend, conn)      # 选中谁，后续查询走谁
```

- 兜底仅在**连接级失败**触发（验活失败/二进制缺失/连接被拒），不在普通查询错误时切换（避免行为不确定、便于排查）。
- `Database.connect(name, ...)` 经 `config.find(name)` 拿到带 `driver` 的 `Connection`，再走 `open`。

---

## 8. 风险与 parity

| 风险 | 说明 | 缓解 |
|---|---|---|
| **动摇 gdaa 二进制 parity** | memory 记 health/wdr 对 gdaa 逐字节 parity —— 那是 **pg8000 路径**下的。默认切 gsql 后，JSON 对**时间戳**（ISO 串 vs `datetime`）与数值格式可能微差 | 现有 73 单测保绿（pg8000 路径回归）+ 新增 gsql 后端测试 + OpenCode 实跑 diff 两路输出，parity 基准重新确认；如有差异登记为「可接受」或在门面层归一 |
| gsql 版本兼容 | openGauss 内核 9.2 系，`json_agg`/`row_to_json`/`-P`/`VERBOSITY`/`:'var'` 支持需实测 | 真库（og5 / og-pri）冒烟；不支持则旁路降级并报清晰错误 |
| 每查询一进程开销 | 探针查询量小 | 接受；不做常驻进程优化 |
| `datetime` 依赖 | 下游若对结果做日期运算而非展示，ISO 串会破坏 | 测试覆盖；必要时在 gsql 后端检测 ISO 时间戳列并 `datetime.fromisoformat` 还原 |

---

## 9. 测试 / 验证计划（TDD）

1. **后端抽象 + pg8000 搬迁**：重构后跑现有 **73 单测全绿**（回归基线，证明门面化无行为变化）。
2. **gsql 后端单测**（mock `subprocess`）：
   - json_agg 解析（含嵌套、NULL、Decimal 精度、空集 → `([], [])`）；
   - 参数类型注入（str→`:'pN'`、int→`:pN`、bool/None、`%%`、计数不符报错）；
   - 只读前缀、`query_in_rollback` 的 BEGIN/ROLLBACK 拼装；
   - SHOW/EXPLAIN 文本旁路；
   - 错误解析（SQLSTATE 提取、二进制缺失、连接被拒）；
   - 密码不出现在 argv（断言走 env）。
3. **config 单测**：`driver` 字段校验、缺省 gsql、旧 config 向后兼容。
4. **兜底单测**：首选失败→兜底成功 / 两者皆败→合并报错。
5. **真库实测**：本机 og5 + og-pri 上 gsql 路径跑通 health/wdr/slowsql/proctune/explain，与 pg8000 路径 diff 输出，确认 parity 或登记可接受差异。

---

## 10. 交付物

- `common/backends/{base,pg8000_backend,gsql_backend}.py` + `__init__.py`
- `common/db.py` 改为门面（接口不变）
- `common/config.py` 加 `driver` 字段
- `tests/` 新增 gsql 后端 / 兜底 / config 用例
- 文档：config.yaml `driver` 字段说明、README 连接方式段落更新
