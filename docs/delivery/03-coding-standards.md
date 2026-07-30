# opencode_skill 编码规范

> **定位**：本规范面向向 `/Users/sqlrush/opencode_skill` 项目（装进 OpenCode 的 openGauss/GaussDB DBA 技能集）新增代码的开发者。所有规范均从项目真实源码归纳，不写项目未实际采用的约定。规范优先于"通用最佳实践"——如有冲突，以本文档为准。

---

## 目录

1. [不可变数据](#1-不可变数据)
2. [文件与模块组织](#2-文件与模块组织)
3. [命名与函数规模](#3-命名与函数规模)
4. [错误处理](#4-错误处理)
5. [输入校验与边界](#5-输入校验与边界)
6. [安全规范](#6-安全规范)
7. [依赖约束](#7-依赖约束)
8. [测试规范](#8-测试规范)
9. [Backend 接口契约](#9-backend-接口契约)
10. [SKILL.md 与文档约定](#10-skillmd-与文档约定)
11. [Git 提交规范](#11-git-提交规范)
12. [提交前检查清单](#12-提交前检查清单)

---

## 1. 不可变数据

**原则**：总是构造新对象，绝不就地改现有对象。

### 1.1 dataclass 一律使用 `frozen=True`

项目中所有"值对象"类型都声明为冻结 dataclass，防止字段被意外修改：

```python
# file: common/config.py
@dataclass(frozen=True)
class Connection:
    """One named database target (immutable)."""
    name: str
    type: str
    host: str
    port: int
    database: str
    user: str
    sslmode: str = ""
    driver: str = "gsql"
```

同样模式：`skills/gaussdb-slowsql/scripts/slowsql.py` 的 `StmtRow`、`skills/gauusdb-sqltune/scripts/sqltune.py` 的 `TuneResult`、`skills/gaussdb-health/scripts/model.py` 的 `Finding`。

### 1.2 "修改"字段时返回新副本，不改原对象

用 `dataclasses.replace()` 返回新实例：

```python
# file: common/config.py — Connection.with_sslmode
def with_sslmode(self, sslmode: str) -> "Connection":
    """Return a new Connection with sslmode replaced (no mutation)."""
    return replace(self, sslmode=sslmode)
```

**反例（禁止）**：

```python
# 错误：就地修改 frozen dataclass 会抛 FrozenInstanceError
conn.sslmode = "require"

# 错误：用可变字典代替 dataclass 后随意改字段
conn_dict["sslmode"] = "require"
```

### 1.3 白名单集合用 `frozenset`

枚举类合法值时用 `frozenset`，不用 `set` 或列表，防止运行时被意外扩充：

```python
# file: common/config.py
_VALID_SSLMODES = frozenset(
    {"disable", "allow", "prefer", "require", "verify-ca", "verify-full"}
)
_VALID_TYPES  = frozenset({"opengauss", "gaussdb"})
_VALID_DRIVERS = frozenset({"gsql", "pg8000"})
```

```python
# file: common/backends/pg8000_backend.py
_SSL_MODES = frozenset({"allow", "prefer", "require", "verify-ca", "verify-full"})
```

---

## 2. 文件与模块组织

### 2.1 按 feature/domain 小文件组织，不按类型聚合

典型的 skill 目录结构：

```
skills/health/scripts/
    health.py       # 入口 + argparse（≈100 行）
    collectors.py   # 12 个采集器（≈450 行）
    model.py        # 数据类型 + Severity（≈120 行）
    render.py / report.py  # 纯渲染，无 I/O
    thresholds.py   # 阈值常量
    util.py         # 小工具函数
```

同一个 skill 内，相同名字的 `model.py` / `thresholds.py` / `util.py` 各自服务于本 skill，不共享实现。

**上限**：单文件 ≤ 800 行。典型目标 200–400 行。一旦某模块接近 600 行就应拆分。

### 2.2 纯函数层与 I/O 层分离

`gsql_protocol.py` 是纯函数层的典范——**无任何 I/O、无副作用**，只做参数注入、SQL 改写、结果解析：

```python
# file: common/backends/gsql_protocol.py — 模块 docstring
"""gsql 协议层（纯函数，无 I/O）：参数注入、语句判别、结果与错误解析。"""
```

`GsqlBackend._run()` 负责 subprocess I/O，其余方法把 `_run` 的字符串结果交给 `gsql_protocol` 的纯函数处理。

**原则**：采集逻辑（collectors）、类型模型（model）、渲染（render/report）各自独立，函数之间通过数据结构传递，不通过全局状态。

### 2.3 render 与业务逻辑分离

渲染函数只接受数据对象，不持有数据库连接或执行任何查询。查阅：

- `skills/gaussdb-health/scripts/report.py` — 接收 `HealthEvidence` 返回 str，不连库
- `skills/gaussdb-slowsql/scripts/render.py` — 纯字符串操作，`table()`/`truncate()`/`code_block()`
- `skills/gaussdb-wdr/scripts/finalreport.py` — 接收 evidence + interp JSON，离线渲染

### 2.4 sys.path 约定

每个 skill 入口脚本在文件顶部按以下固定模式定位 `common/`，**不硬编码路径**：

```python
# file: skills/health/scripts/health.py（所有 skill 入口同样模式）
_HERE = pathlib.Path(__file__).resolve()
sys.path.insert(0, str(_HERE.parent))          # sibling modules
for _anc in _HERE.parents:                      # locate common/ (repo root or install dir)
    if (_anc / "common" / "__init__.py").exists():
        sys.path.insert(0, str(_anc))
        break
```

测试文件用：

```python
# file: tests/test_config_units.py（所有测试同样模式）
_ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT))
```

---

## 3. 命名与函数规模

### 3.1 命名风格

- 模块、函数、变量：`snake_case`
- 类：`PascalCase`
- 常量（模块级）：`_UPPER_SNAKE_CASE`（私有前缀 `_`）
- 私有方法：`_前缀`（如 `_run`、`_env`、`_prefix`）
- 错误类：`XxxError`（如 `ConfigError`、`CredentialError`、`DBError`）

真实例子：`_NAME_RE`、`_VALID_SSLMODES`、`_KEY_SIZE`、`_NONCE_SIZE`、`CONNECT_TIMEOUT`（公开常量无下划线前缀）。

### 3.2 函数 ≤ 50 行，职责单一

项目中典型小函数：

| 函数 | 文件 | 职责 |
|------|------|------|
| `validate(conn)` | `common/config.py` | 边界校验，抛 `ConfigError`，约 25 行 |
| `_load_key()` | `common/credential.py` | 读或原子生成密钥，约 20 行 |
| `rewrite_params()` | `common/backends/gsql_protocol.py` | %s → gsql 变量改写，约 25 行 |
| `_inline_or_var()` | `common/backends/gsql_protocol.py` | 单参数类型判别，约 12 行 |
| `parse_json_result()` | `common/backends/gsql_protocol.py` | json_agg 输出解析，约 10 行 |
| `degraded()` | `skills/health/scripts/model.py` | 构造降级 DimResult，约 4 行 |
| `worst()` | `skills/health/scripts/model.py` | 求最大 Severity，约 6 行 |

超过 50 行的函数须拆分。以 `_cmd_collect` 在 wdr.py 为例，它只做 argparse 分发 + 异常包裹，不混入采集逻辑（采集在 `collectors.collect_evidence`）。

### 3.3 参数不超过 6 个；参数多时用 dataclass 封装

```python
# file: skills/wdr/scripts/model.py
@dataclass
class Options:
    begin: int
    end: int
    scope: str
    node: str
    top: int
    save_html: str
    thresholds: ...
```

`collect_evidence(db, opt)` 只传两个参数，不把 7 个字段平铺成位置参数。

### 3.4 嵌套不超过 4 层

函数体内 if/for 嵌套超过 4 层时，提取内层为独立函数。

---

## 4. 错误处理

### 4.1 三层错误类型，统一来源

| 错误类 | 定义位置 | 含义 |
|--------|---------|------|
| `ConfigError` | `common/config.py` | 连接配置缺失或格式非法 |
| `CredentialError` | `common/credential.py` | 凭据缺失、解密失败、名称非法 |
| `DBError` | `common/backends/base.py` | 连接失败、查询失败（与后端无关） |

所有后端（`GsqlBackend`、`Pg8000Backend`）只抛 `DBError`，不透传原始库异常：

```python
# file: common/backends/pg8000_backend.py — Pg8000Backend.query
except Exception as exc:
    raise DBError(_format_pg_error(exc)) from exc
```

```python
# file: common/backends/gsql_backend.py — GsqlBackend._run
if cp.returncode != 0:
    raise DBError(gp.parse_gsql_error(cp.stderr))
```

### 4.2 入口区分连接错误与运行时错误，退出码不同

**连接错误（配置/凭据/连接）→ 退出码 2**，**运行时 DBError → 退出码 1**：

```python
# file: skills/health/scripts/health.py — main()
try:
    db = common.Database.connect(args.conn)
except (common.ConfigError, common.CredentialError, common.DBError) as exc:
    print(f"error: {exc}", file=sys.stderr)
    return 2
try:
    ...
    return 0
except common.DBError as exc:
    print(f"error: {exc}", file=sys.stderr)
    return 1
finally:
    db.close()
```

同样模式：`skills/gaussdb-slowsql/scripts/slowsql.py`、`skills/wdr/scripts/wdr.py` 的每个子命令。

### 4.3 采集器降级（degrade），不中止整体

单个维度查询失败时，返回降级 `DimResult` 而非抛出异常——一个维度无权限不应让整个健康检查崩掉：

```python
# file: skills/health/scripts/collectors.py — collect_overview
try:
    _, rows = db.query(_OVERVIEW_Q)
except common.DBError as exc:
    return degraded(DIM_OVERVIEW, summarize_err(exc))
```

```python
# file: skills/health/scripts/model.py — degraded()
def degraded(dim: str, reason: str) -> DimResult:
    return DimResult(dimension=dim, available=False, note=reason,
                     headline="不可用：" + reason)
```

**不允许**在采集器里 `print(exc)` 后继续——要么正常返回，要么 `return degraded(...)`。

### 4.4 绝不吞异常

以下写法**禁止**：

```python
try:
    ...
except Exception:
    pass  # 禁止：吞掉所有异常
```

`Pg8000Backend.close()` 是唯一例外，因为关闭时连接可能已断开，且关闭失败对调用方无意义：

```python
# file: common/backends/pg8000_backend.py — close()
def close(self) -> None:
    try:
        self._raw.close()
    except Exception:
        pass  # 关闭失败不影响调用方，唯一允许吞的场景
```

**其余所有地方**，`except Exception` 必须要么重新抛出，要么转换为有意义的错误类型。

### 4.5 错误消息要具体，不要只说"出错了"

```python
# 好：告诉用户怎么修
raise ConfigError(
    f"no connection named {name!r}: run `connect add {name} ...` first "
    f"(or check `connect list`)"
)

# 差：
raise ConfigError("connection not found")
```

---

## 5. 输入校验与边界

### 5.1 外部输入必须在边界处校验，fail fast

`config.yaml` 是用户可编辑的外部文件，`load()` 在解析后立即对每个连接调用 `validate()`：

```python
# file: common/config.py — load()
for item in raw.get("connections", []) or []:
    conn = Connection(...)
    validate(conn)      # 每条记录解析后立即校验，而不是等到用图时报错
    conns.append(conn)
```

### 5.2 `validate()` 覆盖所有字段，用 frozenset 白名单

```python
# file: common/config.py — validate()
def validate(conn: Connection) -> None:
    if not conn.name or not _NAME_RE.match(conn.name):
        raise ConfigError(...)
    if conn.type not in _VALID_TYPES:          # frozenset 白名单
        raise ConfigError(...)
    if not isinstance(conn.port, int) or conn.port < 1 or conn.port > 65535:
        raise ConfigError(...)
    if conn.sslmode and conn.sslmode not in _VALID_SSLMODES:
        raise ConfigError(...)
    if conn.driver not in _VALID_DRIVERS:
        raise ConfigError(...)
```

### 5.3 凭据名称在使用前校验

```python
# file: common/credential.py — load_secret()
def load_secret(name: str) -> str:
    if not name or not _NAME_RE.match(name):
        raise CredentialError(f"invalid credential name {name!r}")
    ...
```

```python
# file: common/credential.py — save_secret()
def save_secret(name: str, secret: str) -> None:
    if not name or not _NAME_RE.match(name):
        raise CredentialError(f"invalid credential name {name!r}")
    ...
```

### 5.4 CLI 参数在 main() 顶部校验，不推到业务函数内部

```python
# file: skills/wdr/scripts/wdr.py — _cmd_collect()
if args.begin <= 0 or args.end <= 0 or args.end <= args.begin:
    print(f"error: --begin/--end required with end>begin ...", file=sys.stderr)
    return 1
```

### 5.5 数值参数用 `int()` 强制转换防类型混入

```python
# file: common/backends/gsql_backend.py
def set_statement_timeout(self, seconds: int) -> None:
    self._timeout_ms = int(seconds) * 1000   # 防止 float 混入

# file: common/backends/pg8000_backend.py
def set_statement_timeout(self, seconds: int) -> None:
    self.execute(f"SET statement_timeout = {int(seconds) * 1000}")
```

---

## 6. 安全规范

### 6.1 密码绝不进 argv，只走环境变量

gsql 后端通过 `PGPASSWORD` 传密码，明确排除在 argv 之外：

```python
# file: common/backends/gsql_backend.py — _env() + _run()
def _env(self) -> dict:
    env = dict(os.environ)
    env["PGPASSWORD"] = self._password   # 密码进环境变量
    ...
    return env

def _run(self, full_sql: str, vars_: dict) -> str:
    argv = [self._binary, "-h", ..., "-U", self.conn.user, ...]
    # 密码不在 argv 中
    cp = subprocess.run(argv, ..., env=self._env(), ...)
```

单测钉住这条约束：

```python
# file: tests/test_gsql_backend_units.py — test_password_goes_via_env_not_argv()
assert "secretpw" not in " ".join(argv)
assert kw["env"]["PGPASSWORD"] == "secretpw"
```

### 6.2 密码来源优先级：环境变量 > 加密存储，绝不明文写文件

```python
# file: common/credential.py — load_secret()
env = os.environ.get("GSDB_PASSWORD") or os.environ.get("GDAA_PASSWORD")
if env:
    return env
# 否则走 AES-256-GCM 解密
```

环境变量优先支持 CI/容器场景，不需要落盘凭据文件。

### 6.3 凭据文件权限 0600，目录权限 0700

```python
# file: common/credential.py — _load_key()（密钥文件）
fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)

# file: common/credential.py — save_secret()（凭据文件）
cred_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
path.write_bytes(sealed)
os.chmod(path, 0o600)

# file: common/config.py — ensure_dir()
base.mkdir(mode=0o700, parents=True, exist_ok=True)
os.chmod(base, 0o700)
```

### 6.4 密钥文件原子写，防并发竞态

```python
# file: common/credential.py — _load_key()
try:
    fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
except FileExistsError:
    # 另一个进程已创建，直接读
    return _load_key()
```

`O_EXCL` 保证只有一个进程写成功，其余进程读已有文件。

### 6.5 参数化查询防 SQL 注入——两种形式

**pg8000 后端**：原生参数化，占位符 `%s`，值以 tuple 传递，驱动负责转义：

```python
# file: common/backends/pg8000_backend.py — query()
cur.execute(sql, params or ())   # pg8000 处理转义
```

**gsql 后端**：字符串参数走 `:'pN'`（gsql 自行转义为带引号字面量），数值走 `:pN`（裸值，调用方已确保来自我方可控的 int/float/Decimal）：

```python
# file: common/backends/gsql_protocol.py — _inline_or_var()
if isinstance(val, (int, float, Decimal)):
    vars_[name] = str(val)
    return f":{name}"       # 数值裸值
if isinstance(val, str):
    vars_[name] = val
    return f":'{name}'"     # gsql 自行加引号转义
```

**禁止**用 Python f-string 或 `%` 格式化拼用户提供的字符串进 SQL。

### 6.6 默认只读会话（绝不执行变更）

pg8000 后端在 `open()` 后立即钉只读：

```python
# file: common/backends/pg8000_backend.py — open()
if read_only:
    try:
        b.execute("SET SESSION CHARACTERISTICS AS TRANSACTION READ ONLY")
    except DBError:
        b.execute("SET default_transaction_read_only = on")
```

gsql 后端在每次查询前缀注入：

```python
# file: common/backends/gsql_backend.py — _prefix()
if read_only:
    parts.append("SET default_transaction_read_only = on;")
```

`Database.connect()` 默认 `read_only=True`，明确传 `False` 才允许写操作。

### 6.7 凭据不回显

任何面向用户的输出（`print`、日志、错误消息）中不得出现密码、DSN 字符串、`$GSDB_HOME/credentials/` 文件内容。错误消息只包含连接名称和端点信息，不包含凭据：

```python
# 好：
raise DBError(
    f"connect to {conn.name} "
    f"({conn.user}@{conn.host}:{conn.port}/{conn.database}): {exc}"
)
# 差：含 password= 的 DSN
```

### 6.8 SKILL.md 安全红线段必须写入

每个 skill 的 `SKILL.md` 必须包含 `## 安全红线` 小节，明确声明：

- 只通过本技能脚本取数，LLM 不直接写连库代码
- 不读取或解密 `$GSDB_HOME/credentials/`
- 只读诊断，任何变更操作只作建议、注明 `[需人工执行]`

参见：`skills/health/SKILL.md`、`skills/wdr/SKILL.md` 的 `## 安全红线` 节。

---

## 7. 依赖约束

### 7.1 运行时依赖仅三个包

```
# file: requirements.txt
pg8000>=1.30       # PostgreSQL wire 驱动
cryptography>=41   # AES-256-GCM 凭据解密
PyYAML>=6          # config.yaml 解析
```

**禁止**在不修改 `requirements.txt` 并经过讨论的情况下新增运行时 pip 依赖。

### 7.2 gsql 后端纯 stdlib，无需第三方库

`gsql_backend.py` 和 `gsql_protocol.py` 只用标准库（`os`、`subprocess`、`json`、`re`、`decimal`），在没有安装 pg8000 的环境下也能使用 gsql 后端。新增代码维持这个约束。

### 7.3 惰性导入——按需加载后端

`common/db.py` 不在模块顶层导入两个后端：

```python
# file: common/db.py — _load_backend()
def _load_backend(driver: str):
    if driver == "pg8000":
        from .backends.pg8000_backend import Pg8000Backend
        return Pg8000Backend
    if driver == "gsql":
        from .backends.gsql_backend import GsqlBackend
        return GsqlBackend
```

只在实际使用时才导入，gsql-only 环境不会因 `import pg8000` 失败而崩溃。

---

## 8. 测试规范

### 8.1 TDD：先写测试，再写实现

新增功能或修 bug 前先写覆盖目标行为的测试，测试失败后再实现，实现完成后测试通过。

### 8.2 命名约定

| 类型 | 命名规则 | 示例 |
|------|---------|------|
| 单元测试 | `test_<module>_units.py` | `test_gsql_protocol_units.py` |
| 集成/实机测试 | `test_<scope>_live.py` | `test_common_live.py` |
| TDD 守卫 / 边界测试 | `test_<what>_<guard>.py` | `test_hypopg_session_guard.py` |
| 单个测试函数 | `test_<具体行为>()` | `test_password_goes_via_env_not_argv()` |

测试函数名称描述行为，不描述实现细节。

### 8.3 sys.path 引导

所有测试文件顶部使用统一的路径引导模式，不用相对导入：

```python
import sys, pathlib
_ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT))
```

多 skill 测试须先清除同名模块缓存，防止 health 的 `model` 和 wdr 的 `model` 互相污染：

```python
# file: tests/test_health_units.py
for _m in ("model", "thresholds", "util", "collectors", "report", ...):
    sys.modules.pop(_m, None)
sys.path.insert(0, str(_SCRIPTS))
```

### 8.4 live 测试用 marker 隔离

需要真实数据库连接的测试加 `@pytest.mark.live`：

```python
# pytest.ini
[pytest]
markers =
    live: tests that require a configured live DB connection (auto-skip otherwise)
```

CI 默认跑 `-m "not live"`，只跑单元测试。开发者在本机手动运行实机测试。

### 8.5 mock 约定

- mock subprocess（gsql 后端）：`monkeypatch.setattr(gb.subprocess, "run", fake_run)`
- mock pg8000 连接：`monkeypatch.setattr`/`unittest.mock.MagicMock`
- mock 环境变量：`monkeypatch.setenv` / `monkeypatch.delenv`
- mock 文件系统：`tmp_path` fixture + `monkeypatch.setenv("GDAA_HOME", str(tmp_path))`

不 mock 纯函数模块（`gsql_protocol`、`model`、`thresholds`）——直接调用真实实现。

### 8.6 钉住安全关键约束的测试

安全规范对应的测试必须存在并保持通过：

```python
# file: tests/test_gsql_backend_units.py
def test_password_goes_via_env_not_argv(monkeypatch):
    # 密码不在 argv，只在 env["PGPASSWORD"]
    assert "secretpw" not in " ".join(argv)
    assert kw["env"]["PGPASSWORD"] == "secretpw"

# file: tests/test_gsql_backend_units.py
def test_read_only_prefix_present(monkeypatch):
    assert "default_transaction_read_only = on" in sent
```

删除或修改这类测试需要明确的理由。

### 8.7 覆盖率要求

- `common/` 层：语句覆盖率 ≥ 90%
- skill 入口 `main()`、分支异常路径：至少有一个测试覆盖连接错误路径（退出码 2）和运行时错误路径（退出码 1）

---

## 9. Backend 接口契约

### 9.1 新后端必须实现全部抽象方法

```python
# file: common/backends/base.py — Backend
class Backend(abc.ABC):
    name: str            # 后端标识符，如 "gsql" / "pg8000"
    provides_session: bool = True  # 是否提供跨语句持久会话

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

### 9.2 `provides_session` 语义

| 值 | 含义 | 现有实现 |
|----|------|---------|
| `True` | 单条持久连接，GUC 设置和 hypopg 虚拟索引跨语句留存 | `Pg8000Backend` |
| `False` | 每次查询独立子进程/连接，会话级状态不留存 | `GsqlBackend` |

依赖 hypopg 虚拟索引的代码（`skills/gaussdb-sqltune/scripts/hypoindex.py`、`skills/proctune/scripts/hypoindex.py`）在入口处检查此属性：

```python
# file: skills/gaussdb-sqltune/scripts/hypoindex.py
def verify_indexes(db, sql, min_speedup=MIN_SPEEDUP):
    if not db.provides_session:
        raise DBError(
            "hypopg 索引验证需要持久会话（请改用 pg8000 驱动）"
        )
```

### 9.3 `query()` 和 `query_in_rollback()` 返回值约定

- 返回 `(cols: list[str], rows: list[tuple])`
- 空结果返回 `([], [])` 而非 `None`
- gsql 的 `query_in_rollback` 走文本旁路，返回 `([], [(line,), ...])`（每行一元组）

调用方须清楚知道自己用的是哪种后端以处理此差异，或只在已知支持 `provides_session=True` 的后端上调用 `query_in_rollback`。

### 9.4 所有异常统一为 `DBError`

后端内部抛出的所有错误（连接失败、查询失败、超时）必须包装为 `DBError`，不得让原始库异常泄露到后端外部：

```python
# 正确：
except Exception as exc:
    raise DBError(_format_pg_error(exc)) from exc

# 错误：
except pg8000.DatabaseError:
    raise  # 泄露 pg8000 内部类型
```

---

## 10. SKILL.md 与文档约定

### 10.1 必须包含的 frontmatter 字段

```yaml
---
name: <skill名>
version: <semver>
description: "<中文描述，一句话说清适用场景>"
allowed-tools: ["exec", "read"]   # 按需，write 需额外审批
compatibility: opencode
metadata:
  runtime: python3
  emoji: "<单个 emoji>"
  family: diagnostics | tuning | ops
---
```

参见：`skills/health/SKILL.md`、`skills/wdr/SKILL.md`。

### 10.2 脚本路径用 `{baseDir}` 占位符

SKILL.md 内所有脚本路径使用 `{baseDir}`，不写绝对路径：

```markdown
# 正确：
python3 {baseDir}/scripts/health.py -c <conn>

# 错误：
python3 /home/user/opencode_skill/skills/health/scripts/health.py -c <conn>
```

### 10.3 工作流写法：确定性归脚本，判断归 LLM

工作流小节须明确区分：
- **脚本采集**（只读、确定性、数值与阈值）
- **LLM 判断**（解读、归因、建议）
- **证据锚定校验门**（LLM 的判断必须对脚本的 `## Deterministic Findings` 逐条核对）

不写"AI 会自动判断最合适的操作"类表述。

### 10.4 全部文档写中文

SKILL.md 正文、`## 工作流`、`## 规则`、`## 安全红线` 均用中文。frontmatter `description` 字段用中文。代码注释可中英混用，但关键约束注释用中文。

### 10.5 references/ 目录

每个 skill 的方法论文档放在 `skills/<name>/references/` 下，工作流步骤中显式引用：

```markdown
4. **加载方法论。** 阅读 `{baseDir}/references/gaussdb-health-methodology.md`
```

不在 SKILL.md 正文中内嵌长篇方法论，保持 SKILL.md 可读。

---

## 11. Git 提交规范

### 11.1 提交消息格式

```
<type>: <中文描述>

<可选正文，说明背景或不显而易见的原因>
```

**type 枚举**：

| type | 使用场景 |
|------|---------|
| `feat` | 新功能、新 skill、新采集维度 |
| `fix` | 缺陷修复 |
| `refactor` | 重构（不改外部行为） |
| `docs` | 文档、SKILL.md、注释 |
| `test` | 新增或修改测试 |
| `chore` | 构建、CI、依赖更新 |
| `perf` | 性能优化 |

**描述用中文，简明扼要**。参考项目近期提交：

```
feat: 环境变量改 GSDB_HOME/GSDB_PASSWORD(旧 GDAA_* 兜底兼容)
fix: hypopg 索引验证在无会话后端(gsql)下明确报错,不再静默失效
refactor: db.py 门面化 + pg8000 后端搬迁(行为不变)
test: 双后端 live 参数化(本机 gsql 自动兜底 pg8000)
docs: 记录 gsql hypopg verify 已知限制(待修)
```

### 11.2 无署名

`~/.claude/settings.json` 已全局关闭 Claude 署名。提交消息中不加 `Co-Authored-By` 行。

### 11.3 原子提交

每个提交只做一件事（对应一个 type）。不把功能代码和文档混在同一 commit 里，除非文档是该功能不可分割的部分（如 SKILL.md 新增对应新 skill）。

### 11.4 分支

- `main`：随时可发布
- feature 分支：`feat/<描述>`
- 修复分支：`fix/<描述>`

---

## 12. 提交前检查清单

在 `git commit` 前逐项确认：

**不可变与数据设计**
- [ ] 新值对象类型使用 `@dataclass(frozen=True)`
- [ ] "修改"操作返回新对象（`dataclasses.replace()`），不就地改
- [ ] 白名单集合使用 `frozenset`

**文件与模块**
- [ ] 单文件不超过 800 行（目标 200–400 行）
- [ ] render/report 函数与数据库操作完全分离
- [ ] sys.path 引导使用项目标准模式，不硬编码路径

**函数质量**
- [ ] 函数体不超过 50 行
- [ ] 嵌套不超过 4 层
- [ ] 无硬编码业务常量（改用模块级命名常量）
- [ ] 命名清晰（函数名描述行为，变量名描述内容）

**错误处理**
- [ ] 新后端只抛 `DBError`，不泄露底层库异常
- [ ] 入口 `main()` 区分连接错误（退出码 2）与运行时错误（退出码 1）
- [ ] 采集器失败用 `degraded()` 降级，不抛出
- [ ] 无 `except Exception: pass` 吞异常（`close()` 除外）

**输入校验**
- [ ] 所有来自外部的输入（config.yaml、CLI 参数、环境变量名）在边界处校验
- [ ] 字符串参数入 SQL 前走参数化，不用 f-string 拼接

**安全**
- [ ] 密码只走环境变量或加密存储，不进 argv，不进日志
- [ ] 凭据文件 0600，目录 0700
- [ ] 数据库连接默认 `read_only=True`
- [ ] 错误消息不含密码或解密后的凭据内容
- [ ] 新 SKILL.md 包含 `## 安全红线` 节

**依赖**
- [ ] 未新增 `requirements.txt` 之外的运行时依赖
- [ ] gsql 相关代码未引入第三方库（stdlib only）

**测试**
- [ ] 新功能/修复有对应单元测试（`test_*_units.py`）
- [ ] 需要真实数据库的测试标注 `@pytest.mark.live`
- [ ] `pytest -m "not live"` 全量通过
- [ ] 安全关键约束有对应钉住测试（密码不进 argv、只读前缀等）

**新增 Backend**
- [ ] 实现 `Backend` 全部抽象方法
- [ ] 正确设置 `provides_session`
- [ ] 所有异常包装为 `DBError`
- [ ] 在 `common/db.py` 的 `_load_backend()` 注册

**SKILL.md**
- [ ] frontmatter 包含 `name`/`version`/`description`/`allowed-tools`/`compatibility`/`metadata`
- [ ] 脚本路径用 `{baseDir}`，不用绝对路径
- [ ] 包含 `## 安全红线` 节
- [ ] 正文中文

**提交消息**
- [ ] 格式为 `<type>: <中文描述>`
- [ ] type 是枚举内的合法值
- [ ] 无 Co-Authored-By 署名行
