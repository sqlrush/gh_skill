"""skill 侧统一入口：按连接的 driver 选路，两条路径对外形状一致。

    runner = access.for_conn("og")
    rows = runner.run("slowsql.slow_sql", {"threshold_ms": 200, ...})

skill 不感知自己走的是中间件还是直连。这一点是本阶段的全部价值：
skill 代码在本地与客户环境完全相同，不需要为两边各留一套。
"""
import sys
import pathlib

_ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT))

import pytest  # noqa: E402

from common import access  # noqa: E402
from common.config import Connection, ConfigError, validate  # noqa: E402
from common.grmp import registry, script as sc  # noqa: E402
from common.grmp.params import ParamValueError, to_param_value  # noqa: E402
from common.grmp.placeholder import ParamError, ParamDef  # noqa: E402
from common.grmp.runner import DirectRunner, RunError  # noqa: E402
from common.grmp.settings import Settings  # noqa: E402


class FakeDB:
    def __init__(self, cols=("n",), rows=((1,),)):
        self.cols = list(cols)
        self.rows = [tuple(r) for r in rows]
        self.executed = []
        self.closed = False

    def query(self, sql, params=None):
        self.executed.append(sql)
        return self.cols, self.rows

    def set_statement_timeout(self, seconds):
        pass

    def close(self):
        self.closed = True


def _registry(tmp_path, name="slowsql.slow_sql", sql=None, params=None):
    """写一个临时脚本仓库目录。"""
    body = ["name: %s" % name, "description: d"]
    body.append("sql: |")
    body.append("  " + (sql or "select 1 where a > {{n}};"))
    if params is None:
        params = [("n", "INTEGER")]
    if params:
        body.append("params:")
        for key, typ in params:
            body += ["  - key: %s" % key, "    type: %s" % typ,
                     "    description: x"]
    d = tmp_path / "registry" / name.split(".")[0]
    d.mkdir(parents=True, exist_ok=True)
    (d / (name.split(".")[1] + ".yaml")).write_text(
        "\n".join(body) + "\n", encoding="utf-8"
    )
    return tmp_path / "registry"


# ===========================================================================
# 1. 脚本仓库：按逻辑名查，不按 ID
# ===========================================================================

def test_registry_finds_script_by_logical_name(tmp_path):
    reg = registry.Registry(_registry(tmp_path))
    assert reg.find("slowsql.slow_sql").script_name == "slowsql.slow_sql"


def test_unknown_logical_name_error_lists_available_scripts(tmp_path):
    """报错要能直接用：列出有哪些脚本，而不是只说「找不到」。"""
    reg = registry.Registry(_registry(tmp_path))
    with pytest.raises(registry.RegistryError) as exc:
        reg.find("nope.nope")
    assert "slowsql.slow_sql" in str(exc.value)


def test_registry_rejects_duplicate_logical_names(tmp_path):
    """逻辑名是跨环境的匹配键，重名会让「按名解析 ID」变得不确定。"""
    root = _registry(tmp_path)
    dup = root / "other"
    dup.mkdir()
    (dup / "dup.yaml").write_text(
        "name: slowsql.slow_sql\ndescription: d\nsql: |\n  select 2 where a > {{n}};\n"
        "params:\n  - key: n\n    type: INTEGER\n    description: x\n",
        encoding="utf-8",
    )
    with pytest.raises(registry.RegistryError):
        registry.Registry(root).names()


# ===========================================================================
# 2. 取值转换：skill 传原生类型，协议要字符串
# ===========================================================================

def test_int_becomes_decimal_string():
    assert to_param_value(200) == "200"
    assert to_param_value(-2) == "-2"


def test_bool_becomes_lowercase_literal():
    """必须是 "true"/"false"。str(True) 得到 "True"，协议不认。"""
    assert to_param_value(True) == "true"
    assert to_param_value(False) == "false"


def test_string_passes_through_unchanged():
    assert to_param_value("2024-10-19 11:10:25") == "2024-10-19 11:10:25"


@pytest.mark.parametrize("bad", [1.5, None, [1], {"a": 1}])
def test_unsupported_type_is_rejected_rather_than_stringified(bad):
    """str() 兜底会把 None 变成 "None"、把 1.5 变成 "1.5" 送进 INTEGER 校验。

    前者会被当成合法字符串塞进 SQL，后者虽然会被拒但错误信息指向错的地方。
    在入口处就拒绝，错误信息才指得准。
    """
    with pytest.raises(ParamValueError):
        to_param_value(bad)


# ===========================================================================
# 3. 直连路径
# ===========================================================================

def test_direct_runner_renders_and_returns_stringified_rows(tmp_path):
    """直连路径同样把结果字符串化 —— 与中间件路径形状一致。

    保留原生类型会让本地写出的解析代码到客户环境全部失效：
    客户那边拿到的一律是字符串。
    """
    db = FakeDB(cols=("datname", "oid"), rows=[("postgres", 16384)])
    runner = DirectRunner(
        conn_name="og",
        registry=registry.Registry(_registry(tmp_path)),
        settings=Settings(),
        open_db=lambda name, read_only=True: db,
    )
    rows = runner.run("slowsql.slow_sql", {"n": 10})
    assert rows == [{"datname": "postgres", "oid": "16384"}]
    # YAML 块标量自带尾换行，比对时不计首尾空白
    assert [s.strip() for s in db.executed] == ["select 1 where a > 10;"]
    assert db.closed is True


def test_direct_runner_rejects_missing_param(tmp_path):
    runner = DirectRunner(
        conn_name="og",
        registry=registry.Registry(_registry(tmp_path)),
        settings=Settings(),
        open_db=lambda name, read_only=True: FakeDB(),
    )
    with pytest.raises(ParamError):
        runner.run("slowsql.slow_sql", {})


def test_direct_runner_rejects_injection_before_connecting(tmp_path):
    opened = []

    def open_db(name):
        opened.append(name)
        return FakeDB()

    runner = DirectRunner(
        conn_name="og",
        registry=registry.Registry(_registry(tmp_path)),
        settings=Settings(),
        open_db=open_db,
    )
    with pytest.raises(ParamError):
        runner.run("slowsql.slow_sql", {"n": "1 OR 1=1"})
    assert opened == []


def test_statement_without_result_set_raises_instead_of_returning_empty(tmp_path):
    """无结果集时报错，不返回 []。

    返回 [] 会让调用方把「这条语句根本没有结果集」读成「查到 0 行」——
    两条路径都必须这样，否则一致性比对会掩盖差异。
    """
    runner = DirectRunner(
        conn_name="og",
        registry=registry.Registry(_registry(tmp_path)),
        settings=Settings(),
        open_db=lambda name, read_only=True: FakeDB(cols=(), rows=[]),
    )
    with pytest.raises(RunError):
        runner.run("slowsql.slow_sql", {"n": 1})


# ===========================================================================
# 4. 选路与配置
# ===========================================================================

def _conn(**kw):
    base = dict(
        name="og", type="opengauss", host="h", port=5432,
        database="d", user="u",
    )
    base.update(kw)
    return Connection(**base)


def test_grmp_is_a_valid_driver():
    validate(_conn(driver="grmp", data_ip="10.0.0.9"))


def test_grmp_driver_requires_data_ip():
    """dataIp 是中间件路由到实例的唯一依据，缺了必须启动即报错。"""
    with pytest.raises(ConfigError):
        validate(_conn(driver="grmp"))


def test_data_ip_is_ignored_for_direct_drivers():
    validate(_conn(driver="pg8000"))


def test_existing_drivers_still_validate():
    validate(_conn(driver="pg8000"))
    validate(_conn(driver="gsql"))


@pytest.mark.parametrize("driver", ["pg8000", "gsql"])
def test_direct_drivers_select_the_direct_runner(driver, tmp_path, monkeypatch):
    monkeypatch.setenv("GRMP_REGISTRY", str(_registry(tmp_path)))
    runner = access.runner_for(_conn(driver=driver))
    assert isinstance(runner, DirectRunner)


def test_grmp_driver_selects_the_middleware_runner(tmp_path, monkeypatch):
    monkeypatch.setenv("GRMP_REGISTRY", str(_registry(tmp_path)))
    monkeypatch.setenv("GRMP_AUTH_TOKEN", "x" * 32)
    runner = access.runner_for(_conn(driver="grmp", data_ip="10.0.0.9"))
    assert not isinstance(runner, DirectRunner)
    assert runner.data_ip == "10.0.0.9"


def test_middleware_runner_requires_the_token_at_construction(tmp_path, monkeypatch):
    """令牌只从环境变量读；缺失即报错，不等到第一次请求才失败。"""
    monkeypatch.setenv("GRMP_REGISTRY", str(_registry(tmp_path)))
    monkeypatch.delenv("GRMP_AUTH_TOKEN", raising=False)
    with pytest.raises(access.AccessError):
        access.runner_for(_conn(driver="grmp", data_ip="10.0.0.9"))
