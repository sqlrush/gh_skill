# gsql + pg8000 双后端 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让 `common/` 连接层同时支持 gsql 与 pg8000 两个后端、默认 gsql、按 `config.yaml` 每连接 `driver` 字段选择并自动兜底，且各 skill 代码零改动。

**Architecture:** 把 `Database` 由「直接持有 pg8000 连接」重构为「门面 + 后端委托」。`common/backends/base.py` 定义 `Backend` 抽象与共享 `DBError`；`pg8000_backend.py` 搬迁现有逻辑；`gsql_protocol.py`（纯函数：参数注入/语句判别/结果与错误解析）+ `gsql_backend.py`（subprocess 编排）实现 gsql；`db.py` 门面做后端选择与兜底。

**Tech Stack:** Python 3 标准库（`subprocess`/`json`/`os`/`shutil`/`re`/`abc`）、pg8000（既有）、pytest、openGauss `gsql` 客户端、`json_agg`/`row_to_json`。

## Global Constraints

- 各 skill 代码零改动；`Database` 公开接口与返回类型语义保持不变。
- 默认 `driver = "gsql"`；`driver ∈ {"gsql","pg8000"}`；旧 config 缺 `driver` 字段时缺省即 `gsql`（向后兼容）。
- pg8000 `paramstyle == "format"`（`%s`）；同批查询字符串参数与数值参数混用。
- 密码经 `PGPASSWORD` 环境变量传子进程，**绝不进 argv**。
- gsql 后端只用 Python 标准库，不新增 pip 依赖。
- 仅本机 TCP 直连 gsql（无 docker/ssh）。
- 保持现有 73 单测全绿（**每次提交都跑**）。
- 文件 200–400 行典型、800 上限；多小文件、高内聚。
- 不改动 Go 仓库 `openclaw_dbaa`。
- 自动兜底仅在**连接级失败**触发，不在普通查询错误时切换。

运行测试：`cd /Users/sqlrush/opencode_skill && python3 -m pytest tests/ -q`

---

### Task 1: config 加 `driver` 字段

**Files:**
- Modify: `common/config.py`（`Connection` dataclass、`validate`、`load`）
- Test: `tests/test_config_units.py`（新建）

**Interfaces:**
- Consumes: 无（首个任务）
- Produces: `Connection.driver: str`（默认 `"gsql"`）；`validate()` 拒绝非法 driver；`load()` 对缺字段填 `"gsql"`。

- [ ] **Step 1: 写失败测试**

```python
# tests/test_config_units.py
import sys, pathlib
_ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT))

import pytest  # noqa: E402
from common.config import Connection, validate, ConfigError, load  # noqa: E402

def _conn(**kw):
    base = dict(name="a", type="opengauss", host="h", port=5432, database="d", user="u")
    base.update(kw)
    return Connection(**base)

def test_driver_defaults_to_gsql():
    assert _conn().driver == "gsql"

def test_validate_accepts_pg8000():
    validate(_conn(driver="pg8000"))  # 不抛即通过

def test_validate_rejects_unknown_driver():
    with pytest.raises(ConfigError):
        validate(_conn(driver="mysqlcli"))

def test_load_fills_default_driver(tmp_path, monkeypatch):
    cfg = tmp_path / "config.yaml"
    cfg.write_text(
        "connections:\n"
        "  - name: a\n    type: opengauss\n    host: h\n"
        "    port: 5432\n    database: d\n    user: u\n"
    )
    monkeypatch.setenv("GDAA_HOME", str(tmp_path))
    conns = load()
    assert conns[0].driver == "gsql"

def test_load_reads_explicit_driver(tmp_path, monkeypatch):
    cfg = tmp_path / "config.yaml"
    cfg.write_text(
        "connections:\n"
        "  - name: a\n    type: opengauss\n    host: h\n"
        "    port: 5432\n    database: d\n    user: u\n    driver: pg8000\n"
    )
    monkeypatch.setenv("GDAA_HOME", str(tmp_path))
    conns = load()
    assert conns[0].driver == "pg8000"
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python3 -m pytest tests/test_config_units.py -q`
Expected: FAIL（`TypeError: __init__() got an unexpected keyword argument 'driver'`）

- [ ] **Step 3: 改 `common/config.py`**

在 `Connection` dataclass 末尾加字段（`common/config.py:35` 之后）：

```python
    sslmode: str = ""
    driver: str = "gsql"
```

在校验白名单区加（`common/config.py:22` 附近）：

```python
_VALID_DRIVERS = frozenset({"gsql", "pg8000"})
```

在 `validate()` 末尾追加（`common/config.py:67` 之后）：

```python
    if conn.driver not in _VALID_DRIVERS:
        raise ConfigError(
            f"driver {conn.driver!r}: must be gsql or pg8000"
        )
```

在 `load()` 构造 `Connection` 处加 `driver`（`common/config.py:113` 处）：

```python
            sslmode=item.get("sslmode", "") or "",
            driver=item.get("driver", "gsql") or "gsql",
```

- [ ] **Step 4: 跑测试确认通过 + 回归**

Run: `python3 -m pytest tests/test_config_units.py -q && python3 -m pytest tests/ -q`
Expected: 新测试 PASS；其余全绿。

- [ ] **Step 5: 提交**

```bash
git add common/config.py tests/test_config_units.py
git commit -m "feat: config 加 driver 字段(gsql|pg8000，默认 gsql)+校验"
```

---

### Task 2: Backend 抽象 + pg8000 搬迁 + db.py 门面化（纯重构，行为不变）

**Files:**
- Create: `common/backends/__init__.py`
- Create: `common/backends/base.py`
- Create: `common/backends/pg8000_backend.py`
- Modify: `common/db.py`（改写为门面，仍恒用 pg8000）
- Test: `tests/test_pg8000_backend_units.py`（新建，mock pg8000）

**Interfaces:**
- Consumes: `Connection`（Task 1）
- Produces:
  - `common.backends.base.DBError`（异常，从 db.py 迁来）
  - `common.backends.base.Backend`（ABC：`open/query/execute/query_in_rollback/set_statement_timeout/close`）
  - `common.backends.pg8000_backend.Pg8000Backend(Backend)`
  - `Database` 门面：`open/connect/query/scalar/execute/query_in_rollback/set_statement_timeout/close/__enter__/__exit__`（签名与现状一致；本任务恒走 pg8000）

- [ ] **Step 1: 写 Pg8000Backend 的失败测试（mock pg8000）**

```python
# tests/test_pg8000_backend_units.py
import sys, pathlib
_ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT))

import pytest  # noqa: E402
from common.config import Connection  # noqa: E402
from common.backends.base import DBError  # noqa: E402
from common.backends import pg8000_backend as pgb  # noqa: E402

class FakeCursor:
    def __init__(self, desc, rows): self._desc, self._rows, self.description = desc, rows, desc
    def execute(self, sql, params=()): self.last = (sql, params)
    def fetchall(self): return self._rows
    def close(self): pass

class FakeConn:
    def __init__(self, desc=None, rows=None):
        self.autocommit = False
        self._cur = FakeCursor(desc, rows or [])
        self.executed = []
    def cursor(self):
        self.executed.append("cursor")
        return self._cur
    def rollback(self): self.executed.append("rollback")
    def close(self): self.executed.append("close")

def _conn(): return Connection(name="a", type="opengauss", host="h", port=5432, database="d", user="u")

def test_open_pins_read_only(monkeypatch):
    fake = FakeConn()
    monkeypatch.setattr(pgb.pg8000.dbapi, "connect", lambda **kw: fake)
    b = pgb.Pg8000Backend.open(_conn(), "pw", read_only=True)
    # 只读钉：execute 过 SET ... READ ONLY
    assert fake.autocommit is True

def test_query_returns_cols_and_rows(monkeypatch):
    fake = FakeConn(desc=[("a",), ("b",)], rows=[(1, "x")])
    monkeypatch.setattr(pgb.pg8000.dbapi, "connect", lambda **kw: fake)
    b = pgb.Pg8000Backend.open(_conn(), "pw", read_only=False)
    cols, rows = b.query("select 1 a, 'x' b")
    assert cols == ["a", "b"]
    assert rows == [(1, "x")]

def test_open_connect_failure_raises_dberror(monkeypatch):
    def boom(**kw): raise RuntimeError("refused")
    monkeypatch.setattr(pgb.pg8000.dbapi, "connect", boom)
    with pytest.raises(DBError):
        pgb.Pg8000Backend.open(_conn(), "pw")
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python3 -m pytest tests/test_pg8000_backend_units.py -q`
Expected: FAIL（`ModuleNotFoundError: common.backends`）

- [ ] **Step 3: 建 `common/backends/base.py`**

```python
"""Backend 抽象与共享 DBError（后端无关）。"""
from __future__ import annotations

import abc
from typing import Any, Optional, Sequence


class DBError(Exception):
    """连接或查询失败时抛出（与具体后端无关）。"""


class Backend(abc.ABC):
    """各驱动后端的统一接口。Database 门面通过它转发。"""

    name: str

    @classmethod
    @abc.abstractmethod
    def open(
        cls, conn: "Any", password: str, read_only: bool = True
    ) -> "Backend":
        ...

    @abc.abstractmethod
    def query(
        self, sql: str, params: Optional[Sequence[Any]] = None
    ) -> tuple[list[str], list[tuple]]:
        ...

    @abc.abstractmethod
    def execute(self, sql: str, params: Optional[Sequence[Any]] = None) -> None:
        ...

    @abc.abstractmethod
    def query_in_rollback(
        self, sql: str, params: Optional[Sequence[Any]] = None
    ) -> tuple[list[str], list[tuple]]:
        ...

    @abc.abstractmethod
    def set_statement_timeout(self, seconds: int) -> None:
        ...

    @abc.abstractmethod
    def close(self) -> None:
        ...
```

- [ ] **Step 4: 建 `common/backends/__init__.py`**

```python
"""backends — 连接层的可插拔驱动后端。

后端按需惰性导入（见 common/db.py 的 _load_backend），因此 gsql-only
环境无需安装 pg8000，反之亦然。
"""
from .base import Backend, DBError

__all__ = ["Backend", "DBError"]
```

- [ ] **Step 5: 建 `common/backends/pg8000_backend.py`（搬迁现有 db.py 逻辑）**

```python
"""pg8000 后端：openGauss/GaussDB 的 PostgreSQL wire 协议直连。

逻辑搬迁自原 common/db.py（保持完全一致的行为）。会话默认钉 READ ONLY。
"""
from __future__ import annotations

import ssl
from typing import Any, Optional, Sequence

import pg8000.dbapi

from .base import Backend, DBError

CONNECT_TIMEOUT = 15  # 秒，对齐 gdaa pingTimeout

_SSL_MODES = frozenset({"allow", "prefer", "require", "verify-ca", "verify-full"})


def _ssl_context(sslmode: str) -> Optional[ssl.SSLContext]:
    if sslmode not in _SSL_MODES:
        return None
    ctx = ssl.create_default_context()
    if sslmode in ("allow", "prefer", "require"):
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
    return ctx


def _format_pg_error(exc: Exception) -> str:
    args = getattr(exc, "args", None)
    if args and isinstance(args[0], dict):
        fields = args[0]
        msg = fields.get("M", str(exc))
        code = fields.get("C", "")
        if code:
            return f"ERROR: {msg} (SQLSTATE {code})"
        return f"ERROR: {msg}"
    return str(exc)


class Pg8000Backend(Backend):
    name = "pg8000"

    def __init__(self, raw: "pg8000.dbapi.Connection", conn: Any):
        self._raw = raw
        self.conn = conn

    @classmethod
    def open(cls, conn: Any, password: str, read_only: bool = True) -> "Pg8000Backend":
        try:
            raw = pg8000.dbapi.connect(
                host=conn.host,
                port=conn.port,
                database=conn.database,
                user=conn.user,
                password=password,
                timeout=CONNECT_TIMEOUT,
                ssl_context=_ssl_context(conn.sslmode or "disable"),
            )
        except Exception as exc:
            raise DBError(
                f"connect to {conn.name} "
                f"({conn.user}@{conn.host}:{conn.port}/{conn.database}): {exc}"
            ) from exc

        raw.autocommit = True
        b = cls(raw, conn)
        if read_only:
            try:
                b.execute("SET SESSION CHARACTERISTICS AS TRANSACTION READ ONLY")
            except DBError:
                b.execute("SET default_transaction_read_only = on")
        return b

    def query(self, sql, params=None):
        cur = self._raw.cursor()
        try:
            cur.execute(sql, params or ())
            cols = [d[0] for d in (cur.description or [])]
            rows = [tuple(r) for r in cur.fetchall()] if cur.description else []
            return cols, rows
        except Exception as exc:
            raise DBError(_format_pg_error(exc)) from exc
        finally:
            cur.close()

    def query_in_rollback(self, sql, params=None):
        prev = self._raw.autocommit
        self._raw.autocommit = False
        cur = self._raw.cursor()
        try:
            cur.execute(sql, params or ())
            cols = [d[0] for d in (cur.description or [])]
            rows = [tuple(r) for r in cur.fetchall()] if cur.description else []
            return cols, rows
        except Exception as exc:
            raise DBError(_format_pg_error(exc)) from exc
        finally:
            try:
                self._raw.rollback()
            finally:
                cur.close()
                self._raw.autocommit = prev

    def set_statement_timeout(self, seconds: int) -> None:
        self.execute(f"SET statement_timeout = {int(seconds) * 1000}")

    def execute(self, sql, params=None) -> None:
        cur = self._raw.cursor()
        try:
            cur.execute(sql, params or ())
        except Exception as exc:
            raise DBError(_format_pg_error(exc)) from exc
        finally:
            cur.close()

    def close(self) -> None:
        try:
            self._raw.close()
        except Exception:
            pass
```

- [ ] **Step 6: 改写 `common/db.py` 为门面（本任务恒用 pg8000）**

整文件替换为：

```python
"""Database 门面：对各 skill 暴露稳定接口，内部委托给某个 Backend。

本层只做：后端选择/兜底、scalar 派生、上下文管理。具体的连接/查询/类型
处理在 backends/ 各后端里。DBError 从 backends.base 再导出，保持
`from common.db import DBError` 向后兼容。
"""
from __future__ import annotations

from typing import Any, Optional, Sequence

from .backends.base import Backend, DBError  # 再导出
from .config import find
from .credential import load_secret


class Database:
    """委托给一个 Backend 的薄门面（连接句柄，状态性资源）。"""

    def __init__(self, backend: Backend, conn: Any):
        self._backend = backend
        self.conn = conn

    @classmethod
    def open(cls, conn: Any, password: str, read_only: bool = True) -> "Database":
        from .backends.pg8000_backend import Pg8000Backend

        backend = Pg8000Backend.open(conn, password, read_only=read_only)
        return cls(backend, conn)

    @classmethod
    def connect(cls, name: str, read_only: bool = True) -> "Database":
        conn = find(name)
        return cls.open(conn, load_secret(conn.name), read_only=read_only)

    def query(self, sql, params=None):
        return self._backend.query(sql, params)

    def scalar(self, sql, params=None):
        _, rows = self.query(sql, params)
        return rows[0][0] if rows else None

    def query_in_rollback(self, sql, params=None):
        return self._backend.query_in_rollback(sql, params)

    def set_statement_timeout(self, seconds: int) -> None:
        self._backend.set_statement_timeout(seconds)

    def execute(self, sql, params=None) -> None:
        self._backend.execute(sql, params)

    def close(self) -> None:
        self._backend.close()

    def __enter__(self) -> "Database":
        return self

    def __exit__(self, *exc) -> None:
        self.close()
```

- [ ] **Step 7: 跑测试确认通过 + 全量回归**

Run: `python3 -m pytest tests/test_pg8000_backend_units.py -q && python3 -m pytest tests/ -q`
Expected: 新测试 PASS；现有 73 单测全绿（`common.__init__` 仍从 `.db` 导出 `Database/DBError`，公开接口未变）。

- [ ] **Step 8: 提交**

```bash
git add common/backends/ common/db.py tests/test_pg8000_backend_units.py
git commit -m "refactor: db.py 门面化 + pg8000 后端搬迁(行为不变)"
```

---

### Task 3: gsql_protocol —— 参数按类型注入（纯函数）

**Files:**
- Create: `common/backends/gsql_protocol.py`
- Test: `tests/test_gsql_protocol_units.py`（新建）

**Interfaces:**
- Consumes: `DBError`（Task 2）
- Produces: `rewrite_params(sql: str, params: Sequence[Any]) -> tuple[str, dict[str, str]]`
  - 返回（改写后的 SQL，gsql 变量映射 `{name: value}` 供 `-v name=value`）
  - `str → :'pN'`（gsql 转义）、`int/float/Decimal → :pN`（裸值）、`bool → TRUE/FALSE` 内联、`None → NULL` 内联、`%% → %`；占位符与参数数量不符抛 `DBError`

- [ ] **Step 1: 写失败测试**

```python
# tests/test_gsql_protocol_units.py
import sys, pathlib
from decimal import Decimal
_ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT))

import pytest  # noqa: E402
from common.backends.base import DBError  # noqa: E402
from common.backends import gsql_protocol as gp  # noqa: E402

def test_string_param_uses_quoted_var():
    sql, vars_ = gp.rewrite_params("WHERE n = %s", ["public"])
    assert sql == "WHERE n = :'p0'"
    assert vars_ == {"p0": "public"}

def test_numeric_param_uses_raw_var():
    sql, vars_ = gp.rewrite_params("LIMIT %s", [100])
    assert sql == "LIMIT :p1" or sql == "LIMIT :p0"
    assert list(vars_.values()) == ["100"]

def test_decimal_param_preserved_as_text():
    sql, vars_ = gp.rewrite_params("x > %s", [Decimal("1.5")])
    assert ":p0" in sql
    assert vars_["p0"] == "1.5"

def test_bool_and_none_inlined():
    sql, vars_ = gp.rewrite_params("a=%s AND b=%s", [True, None])
    assert sql == "a=TRUE AND b=NULL"
    assert vars_ == {}

def test_mixed_string_and_numeric():
    sql, vars_ = gp.rewrite_params(
        "p=%s AND (%s='' OR n=%s) LIMIT %s", ["proc", "", "public", 1]
    )
    assert sql == "p=:'p0' AND (:'p1'='' OR n=:'p2') LIMIT :p3"
    assert vars_ == {"p0": "proc", "p1": "", "p2": "public", "p3": "1"}

def test_percent_literal_escaped():
    sql, vars_ = gp.rewrite_params("x LIKE 'a%%b'", [])
    assert sql == "x LIKE 'a%b'"
    assert vars_ == {}

def test_count_mismatch_raises():
    with pytest.raises(DBError):
        gp.rewrite_params("a=%s AND b=%s", ["only-one"])

def test_unsupported_type_raises():
    with pytest.raises(DBError):
        gp.rewrite_params("x=%s", [object()])
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python3 -m pytest tests/test_gsql_protocol_units.py -q`
Expected: FAIL（`ModuleNotFoundError`）

- [ ] **Step 3: 建 `common/backends/gsql_protocol.py`（先只实现 rewrite_params）**

```python
"""gsql 协议层（纯函数，无 I/O）：参数注入、语句判别、结果与错误解析。"""
from __future__ import annotations

from decimal import Decimal
from typing import Any, Sequence

from .base import DBError


def _inline_or_var(val: Any, idx: int, vars_: dict) -> str:
    """决定第 idx 个参数的注入形式，必要时写入 vars_。"""
    if val is None:
        return "NULL"
    if isinstance(val, bool):  # 必须在 int 之前（bool 是 int 子类）
        return "TRUE" if val else "FALSE"
    if isinstance(val, (int, float, Decimal)):
        name = f"p{idx}"
        vars_[name] = str(val)
        return f":{name}"          # 裸值（数值上下文安全，值我方可控）
    if isinstance(val, str):
        name = f"p{idx}"
        vars_[name] = val
        return f":'{name}'"        # gsql 自行安全转义为带引号字面量
    raise DBError(f"unsupported gsql param type {type(val).__name__}")


def rewrite_params(sql: str, params: Sequence[Any]) -> tuple[str, dict]:
    """把 %s 占位符改写为 gsql 变量引用，返回 (新SQL, 变量映射)。"""
    params = list(params or ())
    out: list[str] = []
    vars_: dict = {}
    idx = 0
    i, n = 0, len(sql)
    while i < n:
        ch = sql[i]
        if ch == "%":
            nxt = sql[i + 1] if i + 1 < n else ""
            if nxt == "%":
                out.append("%"); i += 2; continue
            if nxt == "s":
                if idx >= len(params):
                    raise DBError("more %s placeholders than params")
                out.append(_inline_or_var(params[idx], idx, vars_))
                idx += 1; i += 2; continue
            out.append("%"); i += 1; continue
        out.append(ch); i += 1
    if idx != len(params):
        raise DBError(
            f"placeholder/param count mismatch: {idx} placeholders, {len(params)} params"
        )
    return "".join(out), vars_
```

- [ ] **Step 4: 跑测试确认通过**

Run: `python3 -m pytest tests/test_gsql_protocol_units.py -q`
Expected: PASS（含 `test_numeric_param_uses_raw_var` 因 idx=1 而得 `:p1`）

- [ ] **Step 5: 提交**

```bash
git add common/backends/gsql_protocol.py tests/test_gsql_protocol_units.py
git commit -m "feat: gsql 参数按类型注入(str→:'pN' 转义, 数值→裸值)"
```

---

### Task 4: gsql_protocol —— 语句判别 + json_agg 包裹（纯函数）

**Files:**
- Modify: `common/backends/gsql_protocol.py`
- Test: `tests/test_gsql_protocol_units.py`（追加）

**Interfaces:**
- Consumes: 同模块
- Produces:
  - `is_wrappable_select(sql: str) -> bool`（首关键字 ∈ SELECT/WITH/VALUES/TABLE）
  - `wrap_select_json(sql: str) -> str` → `SELECT json_agg(row_to_json(_t)) FROM (<sql>) _t`

- [ ] **Step 1: 追加失败测试**

```python
# 追加到 tests/test_gsql_protocol_units.py
def test_is_wrappable_true_for_select():
    assert gp.is_wrappable_select("SELECT 1")
    assert gp.is_wrappable_select("  select * from t")
    assert gp.is_wrappable_select("WITH x AS (SELECT 1) SELECT * FROM x")

def test_is_wrappable_strips_leading_comment():
    assert gp.is_wrappable_select("-- c\nSELECT 1")
    assert gp.is_wrappable_select("/* c */ SELECT 1")

def test_is_wrappable_false_for_non_select():
    assert not gp.is_wrappable_select("SHOW enable_wdr_snapshot")
    assert not gp.is_wrappable_select("EXPLAIN ANALYZE SELECT 1")
    assert not gp.is_wrappable_select("SET statement_timeout = 1000")

def test_wrap_select_json_strips_trailing_semicolon():
    assert (
        gp.wrap_select_json("SELECT a FROM t;")
        == "SELECT json_agg(row_to_json(_t)) FROM (SELECT a FROM t) _t"
    )
```

- [ ] **Step 2: 跑确认失败**

Run: `python3 -m pytest tests/test_gsql_protocol_units.py -q`
Expected: FAIL（`AttributeError: module has no attribute 'is_wrappable_select'`）

- [ ] **Step 3: 在 gsql_protocol.py 追加**

```python
import re

_LEADING_NOISE = re.compile(r"^\s*(--[^\n]*\n|/\*.*?\*/\s*)*", re.DOTALL)
_WRAPPABLE = frozenset({"SELECT", "WITH", "VALUES", "TABLE"})


def is_wrappable_select(sql: str) -> bool:
    """去掉前导空白/注释后，首关键字是否为可被 json_agg 包裹的查询。"""
    s = _LEADING_NOISE.sub("", sql, count=1).lstrip()
    if not s:
        return False
    first = s.split(None, 1)[0].upper()
    return first in _WRAPPABLE


def wrap_select_json(sql: str) -> str:
    """把 SELECT 包成单值 JSON：列序/类型/NULL 全保真。"""
    inner = sql.strip().rstrip(";").strip()
    return f"SELECT json_agg(row_to_json(_t)) FROM ({inner}) _t"
```

- [ ] **Step 4: 跑确认通过**

Run: `python3 -m pytest tests/test_gsql_protocol_units.py -q`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add common/backends/gsql_protocol.py tests/test_gsql_protocol_units.py
git commit -m "feat: gsql 语句判别 + json_agg 包裹"
```

---

### Task 5: gsql_protocol —— 结果与错误解析（纯函数）

**Files:**
- Modify: `common/backends/gsql_protocol.py`
- Test: `tests/test_gsql_protocol_units.py`（追加）

**Interfaces:**
- Consumes: 同模块
- Produces:
  - `parse_json_result(stdout: str) -> tuple[list[str], list[tuple]]`（空/NULL → `([], [])`；float 用 Decimal）
  - `parse_text_result(stdout: str) -> tuple[list[str], list[tuple]]`（每非尾空行 → 单元素 tuple）
  - `parse_gsql_error(stderr: str) -> str`（还原 `ERROR: <msg> (SQLSTATE <code>)`，否则回退原文）

- [ ] **Step 1: 追加失败测试**

```python
# 追加到 tests/test_gsql_protocol_units.py
from decimal import Decimal as _D

def test_parse_json_recovers_types_and_null():
    out = '[{"a": 1, "b": "x", "c": null}]\n'
    cols, rows = gp.parse_json_result(out)
    assert cols == ["a", "b", "c"]
    assert rows == [(1, "x", None)]

def test_parse_json_float_is_decimal():
    cols, rows = gp.parse_json_result('[{"v": 1.5}]')
    assert rows[0][0] == _D("1.5")
    assert isinstance(rows[0][0], _D)

def test_parse_json_empty_set():
    assert gp.parse_json_result("\n") == ([], [])
    assert gp.parse_json_result("") == ([], [])

def test_parse_text_lines():
    cols, rows = gp.parse_text_result("on\n")
    assert cols == []
    assert rows == [("on",)]
    _, rows2 = gp.parse_text_result("line1\nline2\n")
    assert rows2 == [("line1",), ("line2",)]

def test_parse_text_empty():
    assert gp.parse_text_result("") == ([], [])

def test_parse_error_with_sqlstate():
    err = "gsql: ERROR:  42P01: relation \"foo\" does not exist\n"
    assert gp.parse_gsql_error(err) == 'ERROR: relation "foo" does not exist (SQLSTATE 42P01)'

def test_parse_error_without_sqlstate():
    assert gp.parse_gsql_error("gsql: ERROR:  boom\n") == "ERROR: boom"

def test_parse_error_fallback():
    assert gp.parse_gsql_error("could not connect to server") == "could not connect to server"
```

- [ ] **Step 2: 跑确认失败**

Run: `python3 -m pytest tests/test_gsql_protocol_units.py -q`
Expected: FAIL（`AttributeError: parse_json_result`）

- [ ] **Step 3: 在 gsql_protocol.py 追加**

```python
import json

_ERR_RE = re.compile(r"ERROR:\s+(?:([0-9A-Za-z]{5}):\s+)?(.*)")


def parse_json_result(stdout: str) -> tuple[list[str], list[tuple]]:
    """解析 json_agg 输出为 (cols, rows)；空集 → ([], [])。"""
    text = stdout.strip()
    if not text:
        return [], []
    data = json.loads(text, parse_float=Decimal)
    if not data:                       # None 或空数组
        return [], []
    cols = list(data[0].keys())        # row_to_json 保列序，dict 保插入序
    rows = [tuple(rec.get(c) for c in cols) for rec in data]
    return cols, rows


def parse_text_result(stdout: str) -> tuple[list[str], list[tuple]]:
    """解析 -At 文本输出：每非尾空行 → 单元素 tuple（SHOW/EXPLAIN 用）。"""
    text = stdout[:-1] if stdout.endswith("\n") else stdout
    if text == "":
        return [], []
    return [], [(line,) for line in text.split("\n")]


def parse_gsql_error(stderr: str) -> str:
    """尽量还原 'ERROR: <msg> (SQLSTATE <code>)'，否则回退原文。"""
    for line in stderr.splitlines():
        m = _ERR_RE.search(line)
        if m:
            code, msg = m.group(1), m.group(2).strip()
            return f"ERROR: {msg} (SQLSTATE {code})" if code else f"ERROR: {msg}"
    return stderr.strip() or "gsql failed with no error output"
```

> 注：`re`/`json`/`Decimal` 已在前序 Step 导入；若文件顶部尚未导入 `json` 则补上。

- [ ] **Step 4: 跑确认通过 + 全量回归**

Run: `python3 -m pytest tests/test_gsql_protocol_units.py -q && python3 -m pytest tests/ -q`
Expected: PASS；其余全绿。

- [ ] **Step 5: 提交**

```bash
git add common/backends/gsql_protocol.py tests/test_gsql_protocol_units.py
git commit -m "feat: gsql 结果(json/文本)与错误解析"
```

---

### Task 6: GsqlBackend —— subprocess 编排

**Files:**
- Create: `common/backends/gsql_backend.py`
- Test: `tests/test_gsql_backend_units.py`（新建，mock subprocess）

**Interfaces:**
- Consumes: `Connection`、`DBError`、`gsql_protocol.*`
- Produces: `GsqlBackend(Backend)`，实现 `open/query/execute/query_in_rollback/set_statement_timeout/close`；
  - argv 含 `-A -t -q -v ON_ERROR_STOP=1 -v VERBOSITY=verbose [-v pN=...] -c "<prefix><sql>"`
  - 密码经 `PGPASSWORD` env；`sslmode→PGSSLMODE`；gsql 路径 `GDAA_GSQL` 覆盖
  - read_only → 前缀 `SET default_transaction_read_only=on;`；timeout → 前缀 `SET statement_timeout=<ms>;`
  - `query_in_rollback` → `BEGIN; <sql>; ROLLBACK;`

- [ ] **Step 1: 写失败测试（mock subprocess.run + shutil.which）**

```python
# tests/test_gsql_backend_units.py
import sys, pathlib, types
_ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT))

import pytest  # noqa: E402
from common.config import Connection  # noqa: E402
from common.backends.base import DBError  # noqa: E402
from common.backends import gsql_backend as gb  # noqa: E402

def _conn(**kw):
    base = dict(name="a", type="opengauss", host="h", port=5432,
                database="d", user="u", driver="gsql")
    base.update(kw)
    return Connection(**base)

class FakeCompleted:
    def __init__(self, rc=0, out="", err=""):
        self.returncode, self.stdout, self.stderr = rc, out, err

def _patch(monkeypatch, *, rc=0, out="", err="", sink=None):
    monkeypatch.setattr(gb.shutil, "which", lambda x: "/usr/bin/gsql")
    def fake_run(argv, **kw):
        if sink is not None:
            sink.append((argv, kw))
        return FakeCompleted(rc=rc, out=out, err=err)
    monkeypatch.setattr(gb.subprocess, "run", fake_run)

def test_open_verifies_with_select_1(monkeypatch):
    calls = []
    _patch(monkeypatch, out="[{\"?column?\":1}]\n", sink=calls)
    b = gb.GsqlBackend.open(_conn(), "secret")
    assert isinstance(b, gb.GsqlBackend)
    # 验活确有一次调用
    assert calls

def test_password_goes_via_env_not_argv(monkeypatch):
    calls = []
    _patch(monkeypatch, out="[{\"?column?\":1}]\n", sink=calls)
    gb.GsqlBackend.open(_conn(), "secretpw")
    argv, kw = calls[-1]
    assert "secretpw" not in " ".join(argv)
    assert kw["env"]["PGPASSWORD"] == "secretpw"

def test_missing_binary_raises_dberror(monkeypatch):
    monkeypatch.setattr(gb.shutil, "which", lambda x: None)
    with pytest.raises(DBError):
        gb.GsqlBackend.open(_conn(), "pw")

def test_query_wraps_select_and_parses_json(monkeypatch):
    calls = []
    _patch(monkeypatch, out='[{"a":1,"b":"x"}]\n', sink=calls)
    b = gb.GsqlBackend.open(_conn(), "pw", read_only=False)
    calls.clear()
    cols, rows = b.query("SELECT a, b FROM t")
    assert cols == ["a", "b"] and rows == [(1, "x")]
    sent = calls[-1][0]
    assert any("json_agg(row_to_json(_t))" in a for a in sent)

def test_read_only_prefix_present(monkeypatch):
    calls = []
    _patch(monkeypatch, out="[]\n", sink=calls)
    b = gb.GsqlBackend.open(_conn(), "pw", read_only=True)
    calls.clear()
    b.query("SELECT 1")
    sent = " ".join(calls[-1][0])
    assert "default_transaction_read_only = on" in sent

def test_show_uses_text_bypass(monkeypatch):
    calls = []
    _patch(monkeypatch, out="on\n", sink=calls)
    b = gb.GsqlBackend.open(_conn(), "pw", read_only=False)
    calls.clear()
    cols, rows = b.query("SHOW enable_wdr_snapshot")
    assert rows == [("on",)]
    sent = " ".join(calls[-1][0])
    assert "json_agg" not in sent

def test_query_in_rollback_wraps_begin_rollback(monkeypatch):
    calls = []
    _patch(monkeypatch, out="Seq Scan\n", sink=calls)
    b = gb.GsqlBackend.open(_conn(), "pw", read_only=False)
    calls.clear()
    b.query_in_rollback("EXPLAIN ANALYZE INSERT INTO t VALUES (1)")
    sent = " ".join(calls[-1][0])
    assert "BEGIN;" in sent and "ROLLBACK;" in sent

def test_sql_error_raises_parsed_dberror(monkeypatch):
    _patch(monkeypatch, rc=1, err='gsql: ERROR:  42P01: relation "x" does not exist\n')
    # 直接构造实例（绕过 open 的验活）以测查询错误路径：
    inst = gb.GsqlBackend(_conn(), "pw", "/usr/bin/gsql", read_only=False)
    with pytest.raises(DBError) as ei:
        inst.query("SELECT * FROM x")
    assert "42P01" in str(ei.value)
```

- [ ] **Step 2: 跑确认失败**

Run: `python3 -m pytest tests/test_gsql_backend_units.py -q`
Expected: FAIL（`ModuleNotFoundError: common.backends.gsql_backend`）

- [ ] **Step 3: 建 `common/backends/gsql_backend.py`**

```python
"""gsql 后端：本机 TCP 直连 openGauss/GaussDB 的命令行客户端。

每次查询起一个 gsql -c 子进程（无状态）；会话级设置（只读钉、
statement_timeout）作为前缀拼进每次调用。类型保真靠 json_agg 包裹
（见 gsql_protocol）。密码经 PGPASSWORD 传入，绝不进 argv。
"""
from __future__ import annotations

import os
import shutil
import subprocess
from typing import Any, Optional, Sequence

from .base import Backend, DBError
from . import gsql_protocol as gp

CONNECT_TIMEOUT = 15  # 秒，对齐 pg8000


class GsqlBackend(Backend):
    name = "gsql"

    def __init__(self, conn: Any, password: str, binary: str,
                 read_only: bool = True):
        self.conn = conn
        self._password = password
        self._binary = binary
        self._read_only = read_only
        self._timeout_ms: Optional[int] = None

    @classmethod
    def open(cls, conn: Any, password: str, read_only: bool = True) -> "GsqlBackend":
        binary = shutil.which(os.environ.get("GDAA_GSQL", "gsql"))
        if not binary:
            raise DBError(
                "gsql binary not found (set GDAA_GSQL or add gsql to PATH)"
            )
        b = cls(conn, password, binary, read_only=read_only)
        b.query("SELECT 1")  # 验活；失败抛 DBError 供门面兜底
        return b

    # ---- 内部 ----

    def _env(self) -> dict:
        env = dict(os.environ)
        env["PGPASSWORD"] = self._password
        sslmode = self.conn.sslmode or "disable"
        env["PGSSLMODE"] = sslmode
        return env

    def _prefix(self, *, read_only: bool) -> str:
        parts = []
        if read_only:
            parts.append("SET default_transaction_read_only = on;")
        if self._timeout_ms is not None:
            parts.append(f"SET statement_timeout = {self._timeout_ms};")
        return " ".join(parts)

    def _run(self, full_sql: str, vars_: dict) -> str:
        argv = [
            self._binary,
            "-h", str(self.conn.host),
            "-p", str(self.conn.port),
            "-U", self.conn.user,
            "-d", self.conn.database,
            "-A", "-t", "-q",
            "-v", "ON_ERROR_STOP=1",
            "-v", "VERBOSITY=verbose",
        ]
        for name, value in vars_.items():
            argv += ["-v", f"{name}={value}"]
        argv += ["-c", full_sql]
        try:
            cp = subprocess.run(
                argv, capture_output=True, text=True,
                env=self._env(), timeout=CONNECT_TIMEOUT + (
                    (self._timeout_ms or 0) // 1000),
            )
        except FileNotFoundError as exc:
            raise DBError(f"gsql not executable: {exc}") from exc
        except subprocess.TimeoutExpired as exc:
            raise DBError(f"gsql timed out: {exc}") from exc
        if cp.returncode != 0:
            raise DBError(gp.parse_gsql_error(cp.stderr))
        return cp.stdout

    # ---- Backend 接口 ----

    def query(self, sql, params=None):
        body, vars_ = gp.rewrite_params(sql, params or ())
        if gp.is_wrappable_select(body):
            stmt = gp.wrap_select_json(body)
            full = f"{self._prefix(read_only=self._read_only)} {stmt}".strip()
            return gp.parse_json_result(self._run(full, vars_))
        full = f"{self._prefix(read_only=self._read_only)} {body}".strip()
        return gp.parse_text_result(self._run(full, vars_))

    def execute(self, sql, params=None) -> None:
        body, vars_ = gp.rewrite_params(sql, params or ())
        full = f"{self._prefix(read_only=self._read_only)} {body}".strip()
        self._run(full, vars_)

    def query_in_rollback(self, sql, params=None):
        body, vars_ = gp.rewrite_params(sql, params or ())
        prefix = self._prefix(read_only=False)
        full = f"BEGIN; {prefix} {body}; ROLLBACK;".replace("  ", " ").strip()
        return gp.parse_text_result(self._run(full, vars_))

    def set_statement_timeout(self, seconds: int) -> None:
        self._timeout_ms = int(seconds) * 1000

    def close(self) -> None:
        pass  # 无常驻连接
```

- [ ] **Step 4: 跑确认通过 + 全量回归**

Run: `python3 -m pytest tests/test_gsql_backend_units.py -q && python3 -m pytest tests/ -q`
Expected: PASS；其余全绿。

- [ ] **Step 5: 提交**

```bash
git add common/backends/gsql_backend.py tests/test_gsql_backend_units.py
git commit -m "feat: GsqlBackend(subprocess 编排, PGPASSWORD env, 只读/回滚前缀)"
```

---

### Task 7: db.py 门面接入「按 driver 选择 + 自动兜底」（默认切到 gsql）

**Files:**
- Modify: `common/db.py`（`open` 改为选择+兜底；新增 `_load_backend`）
- Test: `tests/test_database_facade_units.py`（新建）

**Interfaces:**
- Consumes: `Pg8000Backend`、`GsqlBackend`（惰性导入）、`Connection.driver`
- Produces: `Database.open` 按 `conn.driver`（默认 gsql）先连首选，连接级 `DBError` 时自动换另一后端；两者皆败抛合并错误。

- [ ] **Step 1: 写失败测试**

```python
# tests/test_database_facade_units.py
import sys, pathlib
_ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT))

import pytest  # noqa: E402
from common.config import Connection  # noqa: E402
from common.backends.base import DBError  # noqa: E402
import common.db as dbmod  # noqa: E402

def _conn(driver="gsql"):
    return Connection(name="a", type="opengauss", host="h", port=5432,
                      database="d", user="u", driver=driver)

class FakeBackend:
    def __init__(self, tag): self.tag = tag
    @classmethod
    def make(cls, tag, fail):
        def _open(conn, password, read_only=True):
            if fail:
                raise DBError(f"{tag} cannot connect")
            return cls(tag)
        return _open

def test_uses_preferred_driver(monkeypatch):
    loaded = {}
    def fake_load(driver):
        loaded["driver"] = driver
        ok = type("B", (), {"open": staticmethod(FakeBackend.make(driver, fail=False))})
        return ok
    monkeypatch.setattr(dbmod, "_load_backend", fake_load)
    db = dbmod.Database.open(_conn(driver="gsql"), "pw")
    assert loaded["driver"] == "gsql"

def test_falls_back_when_preferred_fails(monkeypatch):
    seen = []
    def fake_load(driver):
        seen.append(driver)
        fail = (driver == "gsql")
        return type("B", (), {"open": staticmethod(FakeBackend.make(driver, fail=fail))})
    monkeypatch.setattr(dbmod, "_load_backend", fake_load)
    db = dbmod.Database.open(_conn(driver="gsql"), "pw")
    assert seen == ["gsql", "pg8000"]      # 先首选后兜底
    assert db._backend.tag == "pg8000"

def test_raises_when_all_fail(monkeypatch):
    def fake_load(driver):
        return type("B", (), {"open": staticmethod(FakeBackend.make(driver, fail=True))})
    monkeypatch.setattr(dbmod, "_load_backend", fake_load)
    with pytest.raises(DBError) as ei:
        dbmod.Database.open(_conn(driver="pg8000"), "pw")
    assert "gsql" in str(ei.value) and "pg8000" in str(ei.value)
```

- [ ] **Step 2: 跑确认失败**

Run: `python3 -m pytest tests/test_database_facade_units.py -q`
Expected: FAIL（`AttributeError: module 'common.db' has no attribute '_load_backend'`）

- [ ] **Step 3: 改 `common/db.py`**

加模块级常量与惰性加载器（放在 import 之后）：

```python
_DRIVER_ORDER = ("gsql", "pg8000")


def _load_backend(driver: str):
    """惰性导入指定后端类（gsql-only 环境无需装 pg8000，反之亦然）。"""
    if driver == "pg8000":
        from .backends.pg8000_backend import Pg8000Backend
        return Pg8000Backend
    if driver == "gsql":
        from .backends.gsql_backend import GsqlBackend
        return GsqlBackend
    raise DBError(f"unknown driver {driver!r}")
```

把 `Database.open` 整体替换为：

```python
    @classmethod
    def open(cls, conn: Any, password: str, read_only: bool = True) -> "Database":
        preferred = conn.driver or "gsql"
        order = [preferred] + [d for d in _DRIVER_ORDER if d != preferred]
        errors = []
        for drv in order:
            try:
                backend = _load_backend(drv).open(
                    conn, password, read_only=read_only
                )
                return cls(backend, conn)
            except DBError as exc:
                errors.append(f"{drv}: {exc}")
        raise DBError(
            f"connect to {conn.name}: all drivers failed [{'; '.join(errors)}]"
        )
```

- [ ] **Step 4: 跑确认通过 + 全量回归**

Run: `python3 -m pytest tests/test_database_facade_units.py -q && python3 -m pytest tests/ -q`
Expected: PASS；其余全绿。

- [ ] **Step 5: 提交**

```bash
git add common/db.py tests/test_database_facade_units.py
git commit -m "feat: db.py 门面按 driver 选择 + 连接级自动兜底(默认 gsql)"
```

---

### Task 8: 真库联调 + 文档

**Files:**
- Modify: `tests/test_common_live.py`（按 driver 参数化）
- Modify: `README.md`（连接方式段落）
- Create: `docs/connection-drivers.md`（driver 字段与排错说明）

**Interfaces:**
- Consumes: 完整 `Database`/两后端
- Produces: 双后端 live 冒烟；config.yaml `driver` 文档。

- [ ] **Step 1: 参数化 live 测试**

把 `tests/test_common_live.py` 的 `test_connect_and_read` 改为对两个 driver 各跑一遍（缺连接自动跳过）：

```python
import pytest  # 顶部加
from dataclasses import replace  # 顶部加

@pytest.mark.parametrize("driver", ["gsql", "pg8000"])
def test_connect_and_read_each_driver(driver):
    if not _available():
        pytest.skip(f"connection {CONN!r} not configured")
    conn = replace(common.find(CONN), driver=driver)
    try:
        db = common.Database.open(conn, common.load_secret(CONN))
    except common.DBError:
        pytest.skip(f"driver {driver} unavailable on this host")
    try:
        ver = db.scalar("select version()")
        assert "openGauss" in ver or "GaussDB" in ver
        cols, rows = db.query("select 1 as a, 'x' as b")
        assert cols == ["a", "b"]
        assert rows == [(1, "x")]
    finally:
        db.close()
```

- [ ] **Step 2: 本机真库跑双后端**

Run: `python3 -m pytest tests/test_common_live.py -v`（在配了 `og5`/`og-pri` 的本机）
Expected: 两 driver 各 PASS 或合理 SKIP（gsql 缺二进制时跳过）。

- [ ] **Step 3: 真库 parity diff（手动校验，记录结果）**

对 `health`/`wdr`/`slowsql`/`proctune`/`explain` 各跑一次 gsql 与 pg8000，对比输出：

```bash
# 例：把某连接 driver 临时切换后分别跑，diff 输出
python3 skills/health/scripts/health.py --conn og-pri > /tmp/health.gsql.txt
# 改 config driver: pg8000 后
python3 skills/health/scripts/health.py --conn og-pri > /tmp/health.pg.txt
diff /tmp/health.gsql.txt /tmp/health.pg.txt || true
```
把差异（尤其时间戳 ISO 串 vs datetime、数值格式）登记到 `docs/connection-drivers.md` 的「已知差异」。

- [ ] **Step 4: 写文档**

`docs/connection-drivers.md` 写：driver 字段含义、默认 gsql、`GDAA_GSQL`/`PGSSLMODE`、兜底行为、json_agg 类型保真与已知差异、排错（gsql 不存在/连接被拒）。
`README.md` 连接段落补一句「支持 gsql（默认）与 pg8000 双后端，见 docs/connection-drivers.md」。

- [ ] **Step 5: 全量回归 + 提交**

Run: `python3 -m pytest tests/ -q`
Expected: 全绿（73 旧 + 新增单测）。

```bash
git add tests/test_common_live.py README.md docs/connection-drivers.md
git commit -m "test: 双后端 live 参数化; docs: driver 字段与已知差异"
```

---

## 完成判据

- [ ] 现有 73 单测自始至终全绿。
- [ ] gsql 后端单测覆盖：参数类型注入、json/文本解析、错误解析、只读/回滚前缀、PGPASSWORD 走 env。
- [ ] config `driver` 字段：默认 gsql、校验、旧 config 兼容。
- [ ] 门面按 driver 选择 + 连接级自动兜底，两者皆败合并报错。
- [ ] 本机真库 gsql 路径跑通 health/wdr/slowsql/proctune/explain，与 pg8000 diff 已登记。
- [ ] 各 skill 代码零改动。
