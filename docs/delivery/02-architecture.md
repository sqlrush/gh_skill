# 代码结构详解

> 面向新人（会 Python，不熟悉本项目）。以真实源码为准；存疑或未实证处均已标注。

---

## 1. 总体架构与分层

```
┌─────────────────────────────────────────────────────────────┐
│ SKILL.md（给 LLM 的工作流指令 + 安全红线 + {baseDir} 占位符）  │
└─────────────────────────┬───────────────────────────────────┘
                          │ LLM 解析 → 执行
                          ▼
┌─────────────────────────────────────────────────────────────┐
│ 入口脚本  scripts/slowsql.py / sqltune.py / health.py …     │
│ argparse 参数 → 调用 common + 自身 helper → 调 render       │
└───────────────┬─────────────────────────────────────────────┘
                │ from common import Database, DBError, …
                ▼
┌─────────────────────────────────────────────────────────────┐
│ common/db.py  Database 门面                                  │
│   connect(name) → find(config) + load_secret(credential)    │
│   open(conn, password) → 选后端 → 连接级兜底                 │
│   query / scalar / execute / query_in_rollback / …          │
└───────────────┬─────────────────────────────────────────────┘
                │ 委托
       ┌────────┴────────┐
       ▼                 ▼
┌─────────────┐  ┌──────────────────────────────────────────┐
│ Pg8000Backend│  │ GsqlBackend                              │
│ 持久 TCP 连接│  │ 每查询一个 gsql -c 子进程（无状态）       │
│ pg8000 驱动  │  │ gsql_protocol.py 纯函数层                │
│ provides_    │  │ provides_session = False                 │
│ session=True │  │                                          │
└─────────────┘  └──────────────────────────────────────────┘
       │                 │
       └────────┬────────┘
                ▼
┌─────────────────────────────────────────────────────────────┐
│ openGauss / GaussDB                                          │
└─────────────────────────────────────────────────────────────┘
                │ 结果
                ▼
┌─────────────────────────────────────────────────────────────┐
│ render.py（各 skill 各自 vendored）                           │
│ table() / code_block() / truncate() → 纯文本 / Markdown 报告 │
└─────────────────────────────────────────────────────────────┘
```

数据流一句话总结：**SKILL.md 驱动 LLM → LLM 执行入口脚本 → 入口脚本通过 `common.Database` 门面查 DB → Backend 执行 → render 组装输出**。

---

## 2. 目录总览

```
opencode_skill/
├── common/                   # 共享连接层（所有 skill 依赖）
│   ├── __init__.py           # 对外公开的 API 汇总
│   ├── config.py             # 命名连接配置（读 config.yaml）
│   ├── credential.py         # AES-256-GCM 密钥/凭据管理
│   ├── db.py                 # Database 门面
│   └── backends/
│       ├── base.py           # Backend ABC + DBError
│       ├── pg8000_backend.py # pg8000 驱动后端
│       ├── gsql_protocol.py  # gsql 协议纯函数层
│       └── gsql_backend.py   # gsql 子进程后端
│
├── skills/                   # 13 个 skill（每个自包含）
│   ├── gaussdb-slowsql/              # 慢 SQL 发现
│   ├── gaussdb-topsql/               # 按资源消耗排名 SQL
│   ├── gaussdb-sqlfetch/             # 按 SQL_ID 取完整 SQL 文本
│   ├── gaussdb-explain/              # 获取执行计划
│   ├── gaussdb-sqltune/              # SQL 调优（最复杂 skill）
│   ├── gaussdb-topproc/              # Top 存储过程排名
│   ├── gaussdb-procinfo/             # 存储过程执行分析
│   ├── gaussdb-proctune/             # 存过调优（镜像 sqltune 结构）
│   ├── gaussdb-health/               # 12 维健康检查
│   └── gaussdb-wdr/                  # WDR 快照解读
│
├── tests/                    # 单测（14 个测试文件，73 用例）
├── docs/
│   ├── connection-drivers.md # 双后端/兜底/json_agg/hypopg 限制
│   └── delivery/             # 本文档所在目录
├── requirements.txt
├── pytest.ini
└── install-opencode.sh       # 安装到 OpenCode 的脚本（替换 {baseDir}）
```

每个 skill 的子目录结构：

```
skills/<name>/
├── SKILL.md            # LLM 工作流指令（frontmatter + 步骤 + 安全红线）
├── scripts/            # Python 模块（入口 + helpers + render.py）
└── references/         # 可选：参考文档（*.md），不含可执行代码
```

---

## 3. `common/` 逐模块详解

### 3.1 `common/__init__.py` — 公开 API 汇总

**职责**：把各子模块的核心类型和函数统一导出，使调用方只需 `import common` 即可访问。

公开符号（`__all__`）：
- 配置相关：`Connection`、`ConfigError`、`find`、`load`、`validate`
- 凭据相关：`CredentialError`、`load_secret`、`save_secret`
- 数据库相关：`Database`、`DBError`

注释强调：**只有连接/凭据/驱动管道在此层**，skill 专用逻辑不进 common。

---

### 3.2 `common/config.py` — 命名连接配置

**职责**：读取 `$GSDB_HOME/config.yaml`（或 `$GSDB_HOME/config.yaml`），把每条连接解析并校验为不可变的 `Connection` 对象。

**关键类型与函数**：

```python
@dataclass(frozen=True)
class Connection:
    name: str; type: str; host: str; port: int
    database: str; user: str; sslmode: str = ""; driver: str = "gsql"

def validate(conn: Connection) -> None: ...   # 边界校验，快速失败
def load() -> list[Connection]: ...           # 读 config.yaml，每条都经 validate
def find(name: str) -> Connection: ...        # 按名查找，不存在抛 ConfigError
def state_dir() -> pathlib.Path: ...          # GSDB_HOME > GDAA_HOME
def ensure_dir() -> pathlib.Path: ...         # 创建目录（0700）
```

**实现技术方案**：
- `Connection` 是 `frozen=True` 的 dataclass：不可变，`replace()` 产生新对象（`with_sslmode` 示例）。
- `state_dir()` 按优先级读环境变量（`GSDB_HOME` → `GDAA_HOME` ），向后兼容旧 。
- `load()` 用 `yaml.safe_load` 解析，每条连接都调 `validate()` 边界校验，拒绝非法值（port 范围、sslmode 枚举、driver 枚举等），做到外部输入（用户配置文件）的快速失败。
- `driver` 默认值为 `"gsql"`；旧版 config.yaml 不含该字段时等价于 `"gsql"`，向后兼容。

**与其它模块关系**：被 `credential.py`（共享 `state_dir`、`_NAME_RE`）、`db.py`（`find`）直接引用。

---

### 3.3 `common/credential.py` — AES-256-GCM 凭据管理

**职责**：加密存储和解密数据库密码，字节兼容 Go 版 （`internal/config/credential.go`）。

**关键类型与函数**：

```python
class CredentialError(Exception): ...
def load_secret(name: str) -> str: ...   # 解密，或读 GSDB_PASSWORD env
def save_secret(name: str, secret: str) -> None: ...  # 加密写入
```

**实现技术方案**：
- 密钥文件：`$state_dir/key`，32 字节，权限 `0600`，首次使用时用 `os.urandom(32)` 原子生成（`O_CREAT | O_EXCL` 防止并发竞争）。
- 密文文件：`$state_dir/credentials/<name>.enc`，布局为 `nonce(12 bytes) || GCM(ciphertext || 16-byte tag)`。
- AAD（附加认证数据）= 连接名字节串，与 Go 端的 `gcm.Seal(..., []byte(name))` 完全对应，保证跨语言字节兼容。
- `GSDB_PASSWORD` 或 `GDAA_PASSWORD` 环境变量优先（CI / 一次性使用），不读磁盘密钥。
- 使用 `cryptography` 库的 `AESGCM`。

**与其它模块关系**：`db.py` 的 `Database.connect()` 内调用 `load_secret(conn.name)` 取密码，之后密码仅存活于内存中，绝不进日志或 argv。

---

### 3.4 `common/db.py` — Database 门面

**职责**：对所有 skill 暴露统一的数据库操作 API，内部将请求委托给具体 Backend；负责后端选择、连接级兜底、`scalar` 便捷方法、上下文管理（`with` 语句支持）。

**关键类型与函数**：

```python
class Database:
    @classmethod
    def connect(cls, name: str, read_only: bool = True) -> "Database": ...
    @classmethod
    def open(cls, conn: Any, password: str, read_only: bool = True) -> "Database": ...

    def query(self, sql, params=None) -> tuple[list[str], list[tuple]]: ...
    def scalar(self, sql, params=None) -> Any: ...          # 取第一行第一列
    def execute(self, sql, params=None) -> None: ...
    def query_in_rollback(self, sql, params=None): ...      # BEGIN…ROLLBACK 包裹
    def set_statement_timeout(self, seconds: int) -> None: ...
    def close(self) -> None: ...

    @property
    def provides_session(self) -> bool: ...  # 透传后端的持久会话能力
```

**后端选择与兜底逻辑**（`open` 方法）：

```
preferred = conn.driver  # 来自 config.yaml（默认 "gsql"）
order = [preferred] + [其余驱动]

for drv in order:
    try:
        backend = _load_backend(drv).open(conn, password, read_only)
        return Database(backend, conn)
    except DBError as exc:
        errors.append(f"{drv}: {exc}")

raise DBError("all drivers failed [...]")  # 两个都失败才报错
```

首选驱动失败（如 macOS 无 gsql 二进制）时自动尝试另一个，调用方无感知。

**`scalar`** 是 `query` 的派生方法：取结果的 `rows[0][0]`，无行时返回 `None`，不进 backends 层。

**`read_only=True`** 为默认值，绝大多数 skill 只读（`connect` 的默认值）；`gaussdb-sqltune --analyze` 模式传 `read_only=False`。

**惰性导入**：`_load_backend(driver)` 按需 import backend 模块，gsql-only 环境无需安装 pg8000，反之亦然。

**与其它模块关系**：公开 `DBError`（从 `backends.base` 再导出）保证调用方 `from common.db import DBError` 的向后兼容；所有 skill 入口脚本只依赖 `common.Database` + `common.DBError`，不直接引用 backends。

---

### 3.5 `common/backends/base.py` — Backend 抽象与 DBError

**职责**：定义所有后端必须实现的统一接口（ABC），以及全局唯一的异常类型 `DBError`。

**关键类型**：

```python
class DBError(Exception): ...

class Backend(abc.ABC):
    name: str
    provides_session: bool = True   # 子类按实际情况覆盖

    @classmethod
    @abc.abstractmethod
    def open(cls, conn, password, read_only=True) -> "Backend": ...

    @abc.abstractmethod
    def query(self, sql, params=None) -> tuple[list[str], list[tuple]]: ...

    @abc.abstractmethod
    def execute(self, sql, params=None) -> None: ...

    @abc.abstractmethod
    def query_in_rollback(self, sql, params=None) -> tuple[list[str], list[tuple]]: ...

    @abc.abstractmethod
    def set_statement_timeout(self, seconds: int) -> None: ...

    @abc.abstractmethod
    def close(self) -> None: ...
```

`provides_session`：默认 `True`（持久连接）；`GsqlBackend` 覆盖为 `False`（无状态子进程）。该属性是 hypopg 守卫的核心依据（见第 4 节）。

---

### 3.6 `common/backends/pg8000_backend.py` — pg8000 后端

**职责**：通过 pg8000 库建立持久 TCP 连接，直接走 PostgreSQL wire 协议，无需 gsql 二进制。

**关键类型**：

```python
class Pg8000Backend(Backend):
    name = "pg8000"
    provides_session = True

    @classmethod
    def open(cls, conn, password, read_only=True) -> "Pg8000Backend": ...
    def query(self, sql, params=None): ...
    def query_in_rollback(self, sql, params=None): ...
    def execute(self, sql, params=None): ...
    def set_statement_timeout(self, seconds: int) -> None: ...
    def close(self) -> None: ...
```

**实现技术方案**：
- 建连：`pg8000.dbapi.connect(host, port, database, user, password, timeout=15, ssl_context=...)`，超时 15 秒（对齐 gsql）。
- `raw.autocommit = True`（打开后立即设置），之后每条语句自动提交。
- **只读钉**：`open` 时若 `read_only=True`，先尝试 `SET SESSION CHARACTERISTICS AS TRANSACTION READ ONLY`，失败则退回 `SET default_transaction_read_only = on`。
- **`query_in_rollback`**：临时关闭 `autocommit`，执行后调 `raw.rollback()`，在 `finally` 里恢复 `autocommit`（用于 EXPLAIN ANALYZE 不提交实际写入）。
- **`set_statement_timeout`**：执行 `SET statement_timeout = {ms}`（pg8000 端接受毫秒）。
- **SSL**：`sslmode` 为 `allow/prefer/require/verify-ca/verify-full` 时构建 `ssl.SSLContext`；`disable` 时传 `None`。
- **错误格式化**：`_format_pg_error` 从 `exc.args[0]`（pg8000 返回的 dict）里提取 `M`（消息）和 `C`（SQLSTATE code），格式化为 `"ERROR: {msg} (SQLSTATE {code})"`，与 gsql 的错误输出格式保持一致。
- `provides_session = True`：单条持久连接，会话级 GUC（如 `enable_hypo_index`）和 hypopg 虚拟索引在多次 `db.*` 调用间留存。

---

### 3.7 `common/backends/gsql_protocol.py` — gsql 协议纯函数层

**职责**：处理 gsql 的所有协议细节，无 I/O，纯函数，可独立测试。包括参数改写、语句判别、结果解析、错误解析。

**关键函数**：

```python
def rewrite_params(sql: str, params: Sequence[Any]) -> tuple[str, dict]:
    """把 %s 占位符改写成 gsql 变量引用，返回 (新SQL, 变量映射)。"""

def is_wrappable_select(sql: str) -> bool:
    """首关键字是否为 SELECT/WITH/VALUES/TABLE（可被 json_agg 包裹）。"""

def wrap_select_json(sql: str) -> str:
    """包装为 SELECT json_agg(row_to_json(_t)) FROM (...) _t"""

def parse_json_result(stdout: str) -> tuple[list[str], list[tuple]]:
    """解析 json_agg 输出 → (cols, rows)。"""

def parse_text_result(stdout: str) -> tuple[list[str], list[tuple]]:
    """解析 -At 文本输出 → ([], [(line,), ...])。"""

def parse_gsql_error(stderr: str) -> str:
    """解析 stderr 中的 ERROR 行 → 标准格式字符串。"""
```

**参数注入细节**（`rewrite_params` + `_inline_or_var`）：
- `None` → 直接内联 `NULL`（无变量）。
- `bool` → `TRUE` / `FALSE`（必须在 int 之前判断，因为 Python `bool` 是 `int` 子类）。
- `int / float / Decimal` → gsql 变量 `:pN`（裸值，数值上下文安全）。
- `str` → gsql 变量 `:'pN'`（gsql 自行做带引号转义，防 SQL 注入）。
- 其他类型 → 抛 `DBError`。

变量名 `pN`（N 为位置索引），通过 `-v pN=<value>` 参数传入 gsql 子进程。

**类型保真关键**：`parse_json_result` 用 `json.loads(text, parse_float=Decimal)` 解析，使浮点数以 `Decimal` 形式返回，避免精度损失。

---

### 3.8 `common/backends/gsql_backend.py` — gsql 子进程后端

**职责**：将每次数据库操作翻译为一次 `gsql -c` 子进程调用，完全无状态。

**关键类型**：

```python
class GsqlBackend(Backend):
    name = "gsql"
    provides_session = False

    @classmethod
    def open(cls, conn, password, read_only=True) -> "GsqlBackend": ...
    def query(self, sql, params=None): ...
    def execute(self, sql, params=None): ...
    def query_in_rollback(self, sql, params=None): ...
    def set_statement_timeout(self, seconds: int) -> None: ...
    def close(self) -> None: ...  # 无常驻连接，空实现
```

**核心方法 `_run(full_sql, vars_)` 的技术方案**：

gsql 参数列表（固定部分）：
```
gsql -h <host> -p <port> -U <user> -d <database>
     -A -t -q
     -v ON_ERROR_STOP=1
     -v VERBOSITY=verbose
     [-v pN=<value> ...]
     -c <full_sql>
```

关键点：
- **密码传递**：通过 `env["PGPASSWORD"] = password` 注入子进程环境，绝不写入 `argv`（防止 `ps aux` 泄露）。
- **SSL**：通过 `env["PGSSLMODE"] = sslmode` 传入。
- **超时**：`subprocess.run(timeout=CONNECT_TIMEOUT + timeout_ms/1000)`，防止子进程挂住。
- `-A`：无对齐格式（Unaligned），每行字段以 `|` 分隔。
- `-t`：去掉列头和行数摘要（Tuples only）。
- `-q`：安静模式，去掉欢迎信息。
- `-v ON_ERROR_STOP=1`：SQL 出错时退出码非零，配合 `cp.returncode != 0` 检测错误。

**`query` 的路由逻辑**：

```python
def query(self, sql, params=None):
    body, vars_ = gp.rewrite_params(sql, params or ())
    if gp.is_wrappable_select(body):
        # SELECT/WITH/VALUES → json_agg 包裹 → parse_json_result
        stmt = gp.wrap_select_json(body)
        full = f"{self._prefix(read_only=self._read_only)} {stmt}".strip()
        return gp.parse_json_result(self._run(full, vars_))
    else:
        # EXPLAIN/SHOW/INSERT/SET 等 → 文本旁路 → parse_text_result
        full = f"{self._prefix(read_only=self._read_only)} {body}".strip()
        return gp.parse_text_result(self._run(full, vars_))
```

**只读前缀（`_prefix`）**：每次调用前在 SQL 前拼接：
```sql
SET default_transaction_read_only = on;   -- 只读模式时
SET statement_timeout = <ms>;             -- 有超时设置时
```

每个子进程独立执行这些前缀 SET，因为 gsql 后端完全无状态（`set_statement_timeout` 只是把超时值存在实例里，不发送任何命令到 DB）。

**`query_in_rollback`**：拼接 `BEGIN; <prefix_无只读钉>; <body>; ROLLBACK;` 为单条命令，一次子进程完成事务（EXPLAIN ANALYZE 场景）。注意：此路径走文本旁路，返回格式为 `([], [(line,), ...])`。

---

## 4. 双后端与类型保真专题

### 4.1 json_agg 类型保真方案

gsql 是命令行工具，原生输出为文本，类型信息丢失。为恢复原生类型，对可包裹的 SELECT 采用：

```sql
SELECT json_agg(row_to_json(_t)) FROM (<原始SQL>) _t
```

输出为单行 JSON 文本，再用 `json.loads(text, parse_float=Decimal)` 解析，保证：

| 数据库类型 | JSON 中间形态 | Python 值 |
|---|---|---|
| `int` / `bigint` | JSON number（整数） | `int` |
| `numeric` / `float`（有小数位） | JSON number（小数） | `Decimal` |
| `bool` | `true` / `false` | `bool` |
| `NULL` | `null` | `None` |
| `text` / `varchar` | JSON string | `str` |

> **待验证**：`numeric` 无小数位时（如 `count(*)::numeric` 返回 `42`），JSON 中为整数，gsql 解析为 `int`，而 pg8000 返回 `Decimal('42')`。各 skill 探针均设计为对此不敏感，但新增探针须注意。

非 JSON 原生类型（时间戳、日期、时间间隔、数组、bytea）在 `row_to_json` 里被渲染为文本字符串，gsql 得到 `str`，pg8000 返回 `datetime.datetime` 等带类型对象。各 skill 已设计为不依赖这些类型的具体形态。新探针建议对这类列显式 `CAST(... AS text)`。

### 4.2 按类型参数注入

`gsql_protocol.rewrite_params` 对不同参数类型采用不同注入形式，防止 SQL 注入：
- 字符串：`:'pN'`（gsql 带引号转义，完全安全）
- 数值：`:pN`（裸值，数值上下文中我方可控，安全）
- NULL / bool：直接内联（无需变量）

### 4.3 只读与回滚模式

| 模式 | pg8000 | gsql |
|---|---|---|
| 只读钉 | `SET SESSION CHARACTERISTICS AS TRANSACTION READ ONLY` | 每条命令前拼 `SET default_transaction_read_only=on;` |
| 回滚执行 | 临时 `autocommit=False` + `raw.rollback()` | 单条命令 `BEGIN; ...; ROLLBACK;` |

### 4.4 连接级兜底

`Database.open()` 按 `[preferred] + [其余]` 顺序尝试，首选驱动连接失败时静默切换，两个都失败才抛错。典型场景：macOS 无 gsql 二进制，`driver: gsql` 自动降为 pg8000。

### 4.5 hypopg 守卫（gsql 后端限制）

hypopg 虚拟索引验证依赖以下会话内顺序调用：

```
SET enable_hypo_index = on
SELECT hypopg_create_index(...)    -- 创建虚拟索引
EXPLAIN ... <query>               -- 必须在同一会话中看见虚拟索引
SELECT hypopg_reset_index()
```

gsql 后端每条语句独立子进程，会话级状态无法跨调用留存，虚拟索引在下一条命令时消失。

**解决方案**（守卫）：`hypoindex.verify_indexes()` 在入口处检查：

```python
if not getattr(db, "provides_session", True):
    raise DBError("hypopg 索引验证需要持久会话 … 请改用 driver: pg8000")
```

skill 入口（`sqltune.py`、`proctune.py`）捕获该 `DBError`，将其转化为「索引验证不可用」的 note 提示用户，不终止整体流程（best-effort 降级）。

**影响范围**：`driver: gsql` 且 Linux 主机有 gsql 二进制时触发；macOS 自动兜底到 pg8000，不受影响。

### 4.6 gsql vs pg8000 差异汇总

| 特性 | gsql 后端 | pg8000 后端 |
|---|---|---|
| 连接方式 | 每查询一个子进程 | 持久 TCP 连接 |
| `provides_session` | `False` | `True` |
| hypopg 验证 | 不支持（守卫报错） | 支持 |
| 类型保真 | json_agg 路径 | 原生 wire protocol |
| `EXPLAIN(FORMAT JSON)` 返回形式 | 逐行 `(line,)` tuple（需调用方重组） | 单个已解码 Python 对象（pg8000 自动解析 JSON 列）— *待验证* |
| 时间戳/日期类型 | `str`（ISO 格式） | `datetime.datetime` / `datetime.date` 对象 |
| 安装依赖 | 需要 gsql 二进制（Linux 原生） | 需要 `pg8000` Python 包 |

> **"待验证"标注**：gsql 二进制仅存在于 Linux 主机，macOS 开发环境下的 parity diff 尚未完成。`docs/connection-drivers.md` 详细记录了理论分析与验证方法。

---

## 5. Skill 结构解剖

### 5.1 SKILL.md 结构

每个 skill 目录都有一个 `SKILL.md`，是给 LLM 的操作手册，由三部分构成：

**frontmatter（YAML 头）**：

```yaml
---
name: slowsql
version: 2.0.0
description: "在 OpenGauss/GaussDB 上发现慢 SQL …"
allowed-tools: ["exec", "read"]
compatibility: opencode
metadata:
  runtime: python3
  emoji: "🐢"
  family: sql-optimization
---
```

**工作流步骤（正文）**：逐步指令，引用 `python3 {baseDir}/scripts/<script>.py` 执行脚本。`{baseDir}` 是安装时 `install-opencode.sh` 替换的绝对路径占位符，使 skill 在安装后可在任意位置运行。

**安全红线（独立节）**：严格限制 LLM 的操作边界，例如：
- 只通过本 skill 脚本取数，LLM 自己不得直接写 Python/psql/gsql 连库
- 不得读取 `$GSDB_HOME/credentials/`
- 脚本未覆盖的能力如实说「无此能力」

### 5.2 scripts/ 目录分工

以 `gaussdb-slowsql`（最简）和 `gaussdb-sqltune`（最复杂）为对比：

**简单 skill（gaussdb-slowsql）**：

```
scripts/
├── slowsql.py    # 入口：argparse → common.Database.connect → 查询 → render → 打印
└── render.py     # vendored 渲染工具（table / code_block / truncate）
```

**复杂 skill（gaussdb-sqltune）**：

```
scripts/
├── sqltune.py    # 入口：组织调用链，输出 markdown / json
├── evidence.py   # 证据收集（plan + schema + GUC + findings）
├── placeholder.py # SQL 占位符自动替换（纯文本启发式，无 DB 调用）
├── cost.py       # EXPLAIN cost 提取 + SQL 引号工具
├── hypoindex.py  # gs_index_advise → hypopg 创建 → re-EXPLAIN（需持久会话）
├── verify.py     # 改写验证（cost 对比 + 结果集等价性 md5 采样）
├── sqlfetch.py   # （vendored 副本）按 SQL_ID 取完整 SQL 文本
└── render.py     # vendored 渲染工具
```

### 5.3 render.py — 各 skill vendored 渲染工具

每个 skill 在 `scripts/` 目录下各自携带一个 `render.py`（内容完全相同，47 行），不从 common 导入，原因是 skill 安装后可能与 common 路径解耦。

```python
def table(headers: list[str], rows: list[list[str]]) -> str: ...
    # 生成 GFM 表格，自动转义 | 字符

def code_block(lang: str, body: str) -> str: ...
    # 围栏代码块，自动扩展围栏长度（防止内容含多个反引号破坏格式）

def truncate(s: str, max_len: int) -> str: ...
    # 按字符数截断，尾部加 … 省略号
```

### 5.4 references/ 目录

仅部分 skill（如 sqltune）含 `references/` 子目录，存放与该 skill 相关的参考文档（如 openGauss 官方文档节录、调优建议等），供 LLM 上下文增强使用，不含可执行代码。

### 5.5 {baseDir} 安装机制

`install-opencode.sh` 将 `{baseDir}` 占位符替换为 skill 实际安装的绝对路径后写入 OpenCode 的 skill 配置。这使得同一份 SKILL.md 在开发目录和安装目录下都能正确引用脚本，无需硬编码路径。

---

## 6. 10 个 Skill 按族介绍

### 6.1 SQL 优化族（4 个 skill）

**gaussdb-slowsql** — 慢 SQL 发现

- 入口：`skills/gaussdb-slowsql/scripts/slowsql.py`
- 查询：`dbe_perf.statement`，按 `total_elapse_time / n_calls`（平均耗时）降序，支持 `--threshold`（ms 阈值）和 `--limit`。
- 输出：`StmtRow` dataclass 列表，markdown 表格或 JSON；含 `cpu_sec` 字段（CPU 时间），`avg_ms` 高但 `cpu_sec` ≈ 0 提示锁等待而非 CPU 瓶颈。
- 关键实现：使用 `NULLIF(n_calls, 0)` 防止除零；`REGEXP_REPLACE(query, '\\s+', ' ', 'g')` + `LEFT(..., 180)` 规范化显示文本。

**gaussdb-topsql** — 按资源消耗排名 SQL

- 入口：`skills/gaussdb-topsql/scripts/topsql.py`
- 查询：同样来自 `dbe_perf.statement`，但按多种资源维度（总耗时、CPU 时间等）排名，输出 Top N。
- 结构：2 文件（入口 + render.py），与 slowsql 类似。

**gaussdb-sqlfetch** — 按 SQL_ID 取完整 SQL 文本

- 入口：`skills/gaussdb-sqlfetch/scripts/sqlfetch.py`
- 数据源：优先查 `statement_history`（含字面值），次选 `dbe_perf.statement`（规范化形式，含 `$N` 占位符）。
- 关键实现：`looks_truncated()` 通过启发式（未闭合括号、以 SQL 关键字结尾）检测 `track_activity_query_size` 导致的 SQL 截断，截断时拒绝进一步分析并报错，避免对半截 SQL 做无效调优。`count_placeholders()` 统计 `?`、`$N`、`:N` 占位符数量（被 sqltune 引用）。

**gaussdb-sqltune** — SQL 调优（最复杂 skill）

- 入口：`skills/gaussdb-sqltune/scripts/sqltune.py`
- 流程（`_tune` 函数）：
  1. 取 SQL（`gaussdb-sqlfetch` → 按 ID，或 `--sql-stdin` 直接传入）
  2. 占位符替换（`placeholder.substitute`）
  3. 证据收集（`evidence.collect`）
  4. hypopg 虚拟索引验证（`hypoindex.verify_indexes`，best-effort）
- `evidence.py` 收集内容：DB 版本 → EXPLAIN 计划 → 确定性 findings（Seq Scan/Hash Join/行估算偏差等规则）→ 表统计信息（`pg_stat_user_tables`）→ 索引统计（`pg_stat_user_indexes`）→ 列统计（`pg_stats`）→ GUC 参数。
- `placeholder.py`：纯文本启发式，识别 `?`、`$N`、`:N` 占位符，根据左侧上下文（`LIMIT`、`DATE`、`LIKE` 等关键字）选择合理的代入值，支持 `--bind` 用户覆盖。
- `cost.py`：`explain_cost(db, sql)` 执行 `EXPLAIN (FORMAT JSON, COSTS TRUE)` 并提取根节点 `Total Cost`；兼容 gsql 文本旁路（多行拼接后 `json.loads`）和 pg8000 自动解码两种形态。
- `hypoindex.py`：`verify_indexes(db, sql, min_speedup=1.3)` — 首先检查 `db.provides_session`（守卫），然后 `gs_index_advise` 获取候选，逐个 `hypopg_create_index` → re-EXPLAIN → 计算 speedup，只返回 speedup ≥ 1.3× 的候选。
- `verify.py`：`verify_rewrite` — cost 对比 + md5 行哈希等价性采样（1000 行）；`verify_combined` — 同时加载 hypopg 虚拟索引后验证 rewrite + index 组合收益。

**gaussdb-explain** — 获取执行计划

- 入口：`skills/gaussdb-explain/scripts/explain.py`
- 对给定 SQL 执行 EXPLAIN（TEXT / JSON 格式），支持 `--analyze` 模式。
- 结构：2 文件（入口 + render.py）。

### 6.2 存储过程族（3 个 skill）

**gaussdb-topproc** — Top 存储过程排名

- 入口：`skills/gaussdb-topproc/scripts/topproc.py`
- 查询 `dbe_perf.statement` 或专用存过统计视图，按资源消耗排名存储过程。
- 结构：2 文件（入口 + render.py）。

**gaussdb-procinfo** — 存储过程执行详情

- 入口：`skills/gaussdb-procinfo/scripts/procinfo.py`
- helper：`procanalyze.py`（大模块，约 18K，分析存过内部结构和执行特征）
- 结构：3 文件（入口 + procanalyze.py + render.py）。

**gaussdb-proctune** — 存储过程调优

- 入口：`skills/gaussdb-proctune/scripts/proctune.py`
- 结构镜像 gaussdb-sqltune：`cost.py` / `evidence.py` / `hypoindex.py` / `procanalyze.py` / `sqlfetch.py` / `verify.py` / `render.py` 共 8 个文件（加入口共 9 文件）。
- 关键点：`hypoindex.py`（proctune 版）同样有 `provides_session` 守卫，与 sqltune 版守卫逻辑一致；`procanalyze.py` 是与 procinfo 共享的大模块（但 vendored 到各自目录，内容相同）。

### 6.3 gaussdb-health — 12 维健康检查

- 入口：`skills/gaussdb-health/scripts/health.py`
- 架构层次：
  - `model.py`：数据类型（`Severity` IntEnum、`Finding`、`DimResult`、`HealthEvidence`、`degraded()`）
  - `thresholds.py`：阈值配置（`Thresholds` dataclass + `default_thresholds()`）
  - `util.py`：格式工具（`f2`、`human_bytes`、`i64`、`trunc`、`sev_by_duration`）
  - `collectors.py`：12 个采集器，每个对应一个维度，都遵循 `(db, thresholds, top) → DimResult` 签名；查询失败时返回 `degraded(dim, reason)` 不抛异常
  - `report.py`：将 `HealthEvidence` 渲染为 markdown 或 JSON
  - `render.py`：vendored 渲染工具
- 12 个维度：`overview` / `waits` / `slowsql` / `xact` / `bloat` / `lwlock` / `locks` / `conn` / `logs` / `repl` / `schema` / `concurrency`
- Severity 四档：`🟢健康(0)` / `🟡关注(1)` / `🟠告警(2)` / `🔴严重(3)`
- **降级设计**：每个 collector 用 `try/except common.DBError` 捕获查询失败，返回 `degraded(dim, reason)`（`available=False`），不中止整体健康检查。最终对所有 findings 按 severity 降序稳定排序，`overall` = 所有 findings 中最严重的等级。

### 6.4 gaussdb-wdr — WDR 快照解读

- 入口：`skills/gaussdb-wdr/scripts/wdr.py`（三子命令：`snaps` / `collect` / `render`）
- 架构层次（15 个文件，最复杂 skill）：
  - `model.py`：数据类型（`Severity` / `Finding` / `DimResult` / `Evidence` / `Window` / `NativeInfo` / `Options`），含 `to_dict` / `from_dict` 双向序列化（JSON 字段名与 Go 端完全一致）
  - `thresholds.py`：WDR 专用阈值
  - `snaps.py`：`snaps(db, limit)` — 列出 WDR 快照，检查 `enable_wdr_snapshot`
  - `collectors.py`：7 维证据采集（Load Profile / DB Stat / Top SQL / Wait Events / Checkpoint / Cache / File IO），基于快照间 delta 计算
  - `interp.py`：`load_evidence(path)` / `load_interp(path)` — 从文件加载证据 JSON 和 LLM 解读 JSON
  - `recheck.py`：机械锚定复核（对照 evidence 数字验证 LLM interp 结论，防止 LLM 捏造数字）
  - `finalreport.py`：`render_report(ev, interp, fmt, no_color)` — 最终全景报告
  - `report.py`：`render_evidence` / `render_evidence_json` — collect 阶段的中间报告
  - `native.py`：调用 `generate_wdr_report` 生成原生 WDR HTML（best-effort）
  - `ansi.py`：ANSI 颜色代码（终端渲染模式）
  - `util.py`：工具函数
  - `render.py`：vendored 渲染工具
- **三阶段工作流**：
  1. `wdr.py snaps` → 列快照，选 begin/end ID
  2. `wdr.py collect --begin B --end E --format json > ev.json` → 采集证据
  3. LLM 读取 `ev.json`，生成 `interp.json`（分析与建议）
  4. `wdr.py render --evidence ev.json --interp interp.json` → 机械复核 + 全景报告
- **逐字呈现纪律**：finalreport 渲染时强制逐字输出 evidence 中的多维 Top SQL 数据，禁止 LLM 用自述文字替代。

---

## 7. 关键设计取舍小结

### 7.1 为何用门面 + 后端分层？

`Database` 门面对所有 skill 暴露稳定 API（query/scalar/execute 等），不论底层使用 gsql 还是 pg8000。skill 代码不感知后端选择，后端可以独立演进或增加新实现而不影响 skill 代码。连接级兜底也集中在门面层，skill 无需实现重试逻辑。

### 7.2 为何用 json_agg 包裹？

gsql 是命令行工具，文本输出无类型信息（整数和字符串无法区分，NULL 显示为空行）。json_agg 把结果集序列化为 JSON，通过 JSON 类型系统保全了 int/bool/NULL 的类型信息，`parse_float=Decimal` 进一步保证了数值精度。这是在无法修改 gsql 二进制的约束下还原类型保真的最可靠方案。

### 7.3 为何 gsql 后端每查询起一个子进程？

gsql 是无持久连接语义的 CLI 工具，每次 `-c` 执行一条命令后即退出。这不是设计选择，而是 gsql 自身的工作模式。好处是无需管理连接池、无状态容易推理；坏处是进程启动开销略高，且会话级状态无法跨语句留存（hypopg 守卫的根本原因）。

### 7.4 为何用 provides_session 守卫而非自动切换后端？

当用户配置了 `driver: gsql` 时，若需要 hypopg 验证，**显式报错比静默切换后端更透明**：用户知道为什么索引验证不可用，以及如何修复（改 `driver: pg8000`）。自动切换会在用户不知情的情况下更改实际使用的后端，破坏可预期性。

### 7.5 为何 render.py 在每个 skill 中 vendored 而非共享？

skill 安装后的路径结构由 `{baseDir}` 决定，每个 skill 的 `scripts/` 目录作为一个独立单元被 LLM 通过 `python3 {baseDir}/scripts/<script>.py` 调用。入口脚本通过 `sys.path.insert(0, str(_HERE.parent))` 将自身目录加入路径，从而能 `import render`。若 render 放在 common 层，则每个入口脚本还需要额外的路径逻辑且增加 common 的职责范围，与「common 只做连接管道」的设计原则相悖。vendored 副本虽有冗余，但使每个 skill 完全自包含。

---

*文档生成时间：2026-07-01。源码基于 opencode_skill 仓库 main 分支。存疑处（gsql vs pg8000 parity diff）详见 `docs/connection-drivers.md`。*
