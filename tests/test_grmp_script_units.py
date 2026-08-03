"""脚本定义：YAML → ScriptRecord（script_config 21 列）→ API 响应结构。

YAML 是仓库内的单一事实源，两条执行路径共用；ScriptRecord 的字段刻意与
客户 script_config 的 21 列对齐，使导出的 DML 可直接用于客户版本发布。
"""
import sys
import pathlib
import json

_ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT))

import pytest  # noqa: E402

from common.grmp import script as sc  # noqa: E402
from common.grmp.placeholder import ParamError  # noqa: E402


MINIMAL_YAML = """
name: slowsql.slow_sql
description: 查询平均耗时超阈值的 SQL
sql: |
  select 1 where runtime > {{threshold_seconds}}
params:
  - key: threshold_seconds
    type: INTEGER
    description: 平均耗时阈值（秒）
"""

NO_PARAM_YAML = """
name: health.db_info
description: 查看数据库相关信息
sql: |
  select datname from pg_database
"""


def _load(text, tmp_path, filename="s.yaml"):
    path = tmp_path / filename
    path.write_text(text, encoding="utf-8")
    return sc.load_script(path)


# ===========================================================================
# 1. YAML → ScriptRecord
# ===========================================================================

def test_load_maps_yaml_to_script_record(tmp_path):
    rec = _load(MINIMAL_YAML, tmp_path)
    assert rec.script_name == "slowsql.slow_sql"
    assert rec.script_content.strip() == "select 1 where runtime > {{threshold_seconds}}"
    assert [p.key for p in rec.params] == ["threshold_seconds"]
    assert rec.params[0].type == "INTEGER"


def test_defaults_match_customer_sample_row(tmp_path):
    """【实】客户 id=527 那条记录的作用域/场景字段取值，作为本仓库默认值。"""
    rec = _load(MINIMAL_YAML, tmp_path)
    assert rec.script_type == "SQL"
    assert rec.database_type == "postgres"
    assert rec.scene == "AGENT"
    assert rec.is_valid == 1
    assert rec.is_asyn == 0
    assert rec.kernel_version == "ALL"
    assert rec.compliance_mode == "ALL"
    assert rec.cluster_deployment_mode == "centralization"


def test_stamped_follows_customer_audit_column_pattern(tmp_path):
    """【实】客户样例的四个审计列：

        create_user      = 工号
        create_time      = '2026-07-03 19:36:58.978'   毫秒精度
        last_modify_user = 同一个工号，创建时就填上
        last_modify_time = NULL

    last_modify_user 留 NULL 与客户不符 —— 交付的 DML 要与客户既有记录同形。
    """
    rec = _load(MINIMAL_YAML, tmp_path).stamped("123456789", "2026-07-03 19:36:58.978")
    assert rec.create_user == "123456789"
    assert rec.create_time == "2026-07-03 19:36:58.978"
    assert rec.last_modify_user == "123456789"
    assert rec.last_modify_time is None


def test_scope_columns_default_to_none(tmp_path):
    """【实】样例中 region/deployment_form/execute_node_type/extend 均为 NULL。"""
    rec = _load(MINIMAL_YAML, tmp_path)
    assert rec.region is None
    assert rec.deployment_form is None
    assert rec.execute_node_type is None
    assert rec.extend is None
    assert rec.last_modify_time is None


def test_script_record_exposes_all_twenty_one_columns(tmp_path):
    """【实】script_config 共 21 列，导出的 DML 要能直接用于客户环境。"""
    rec = _load(MINIMAL_YAML, tmp_path)
    assert len(sc.SCRIPT_CONFIG_COLUMNS) == 21
    row = rec.as_row()
    assert set(row.keys()) == set(sc.SCRIPT_CONFIG_COLUMNS)


def test_extend_column_name_is_quoted_in_column_list(tmp_path):
    """【实】样例 DML 里 "extend" 带引号，说明是保留字或大小写敏感。"""
    assert "extend" in sc.SCRIPT_CONFIG_COLUMNS
    assert sc.quoted_column("extend") == '"extend"'
    assert sc.quoted_column("script_name") == "script_name"


def test_param_type_is_normalised_to_upper_case(tmp_path):
    """YAML 写驼峰也接受，落库一律全大写（与 parameter_config 的样例一致）。"""
    rec = _load(MINIMAL_YAML.replace("type: INTEGER", "type: Integer"), tmp_path)
    assert rec.params[0].type == "INTEGER"


def test_script_without_params_yields_empty_param_list(tmp_path):
    # 元组而非列表：ScriptRecord 是不可变的，params 也不该给调用方留下改的余地
    rec = _load(NO_PARAM_YAML, tmp_path)
    assert rec.params == ()


# ===========================================================================
# 2. parameter_config —— 必须与客户样例逐字一致
# ===========================================================================

def test_parameter_config_matches_customer_sample_verbatim(tmp_path):
    """【实】客户样例：[{"key":"threshold_seconds","value":"","type":"INTEGER","autoAcquire":false}]

    键名、键序、无空格都要一致 —— 这份 JSON 是双方共享的数据资产，
    导出的 DML 要能被客户直接执行。
    """
    rec = _load(MINIMAL_YAML, tmp_path)
    assert rec.parameter_config == (
        '[{"key":"threshold_seconds","value":"","type":"INTEGER",'
        '"autoAcquire":false}]'
    )


def test_parameter_config_is_empty_array_when_no_params(tmp_path):
    rec = _load(NO_PARAM_YAML, tmp_path)
    assert rec.parameter_config == "[]"


def test_parameter_config_auto_acquire_is_real_boolean_not_string(tmp_path):
    """【实】样例中 autoAcquire 是 JSON 布尔 false，不是字符串 "false"。"""
    rec = _load(MINIMAL_YAML, tmp_path)
    parsed = json.loads(rec.parameter_config)
    assert parsed[0]["autoAcquire"] is False


# ===========================================================================
# 3. ScriptRecord → API 响应结构（CommonOmOperationDetail）
# ===========================================================================

def test_api_detail_has_documented_field_names(tmp_path):
    """【规】+【例】响应中的命令详情为 id/cmd/cmd_name/description/cmd_type/param。"""
    rec = _load(MINIMAL_YAML, tmp_path).with_id("527")
    detail = rec.to_api_detail()
    assert set(detail.keys()) == {
        "id", "cmd", "cmd_name", "description", "cmd_type", "param"
    }
    assert detail["id"] == "527"
    assert detail["cmd_type"] == "SQL"
    assert detail["cmd_name"] == "slowsql.slow_sql"


def test_api_detail_id_is_string_not_int(tmp_path):
    """【规】id 是 String。用数字会让客户端的 id 比较静默失配。"""
    detail = _load(MINIMAL_YAML, tmp_path).with_id("527").to_api_detail()
    assert isinstance(detail["id"], str)


def test_api_detail_cmd_carries_raw_sql_with_placeholders(tmp_path):
    """【规】cmd 是命令正文，含 {{}} 占位符 —— 调用方需要知道自己在执行什么。"""
    detail = _load(MINIMAL_YAML, tmp_path).with_id("1").to_api_detail()
    assert "{{threshold_seconds}}" in detail["cmd"]


def test_api_detail_param_uses_operation_param_shape(tmp_path):
    """【规】响应里的 param 元素是 OperationParam，四个字段。

    注意与请求里的 OperationValue（param_name+param_value）不是同一个结构 ——
    接口文档 3.2 的示例正是把这两者搞混了。
    """
    detail = _load(MINIMAL_YAML, tmp_path).with_id("1").to_api_detail()
    assert detail["param"] == [
        {
            "param_name": "threshold_seconds",
            "data_type": "Integer",
            "required": True,
            "description": "平均耗时阈值（秒）",
        }
    ]


def test_api_detail_data_type_is_camel_case_not_upper(tmp_path):
    """【规】API 侧 data_type 是驼峰 Integer，与 parameter_config 的 INTEGER 不同。"""
    detail = _load(MINIMAL_YAML, tmp_path).with_id("1").to_api_detail()
    assert detail["param"][0]["data_type"] == "Integer"


def test_api_detail_required_is_real_boolean(tmp_path):
    """【规】required 是真布尔值，不是字符串。"""
    detail = _load(MINIMAL_YAML, tmp_path).with_id("1").to_api_detail()
    assert detail["param"][0]["required"] is True


def test_api_detail_param_is_empty_array_not_null(tmp_path):
    """【规】+【例】无参数时 param 为 [] 而非 null。"""
    detail = _load(NO_PARAM_YAML, tmp_path).with_id("56").to_api_detail()
    assert detail["param"] == []


def test_api_detail_description_falls_back_to_cmd_name(tmp_path):
    """【例】客户响应里 description 与 cmd_name 完全相同，且 script_config 无该列。

    本实现照此处理：description 由脚本名兜底，调用方不应依赖它有独立含义。
    """
    detail = _load(MINIMAL_YAML, tmp_path).with_id("1").to_api_detail()
    assert detail["description"] == detail["cmd_name"]


# ===========================================================================
# 4. YAML 是边界输入 —— 结构性错误一律 fail fast
# ===========================================================================

def test_missing_name_is_rejected(tmp_path):
    with pytest.raises(sc.ScriptError):
        _load(MINIMAL_YAML.replace("name: slowsql.slow_sql", "x: y"), tmp_path)


def test_missing_sql_is_rejected(tmp_path):
    with pytest.raises(sc.ScriptError):
        _load("name: a.b\ndescription: d\n", tmp_path)


def test_unknown_top_level_key_is_rejected(tmp_path):
    """拼错的键必须报错，不能被静默忽略成默认值。"""
    with pytest.raises(sc.ScriptError) as exc:
        _load(MINIMAL_YAML + "\nis_async: 1\n", tmp_path)
    assert "is_async" in str(exc.value)


def test_unknown_param_key_is_rejected(tmp_path):
    with pytest.raises(sc.ScriptError):
        _load(MINIMAL_YAML + "    requird: true\n", tmp_path)


def test_bad_param_type_is_rejected(tmp_path):
    with pytest.raises(ParamError):
        _load(MINIMAL_YAML.replace("type: INTEGER", "type: NUMERIC"), tmp_path)


def test_logical_name_must_be_dotted_lowercase(tmp_path):
    """逻辑名是跨环境的匹配键，格式必须收紧，否则大小写/空格差异会静默失配。"""
    with pytest.raises(sc.ScriptError):
        _load(MINIMAL_YAML.replace("slowsql.slow_sql", "SlowSQL Slow Sql"), tmp_path)


def test_declared_param_unused_in_sql_is_rejected(tmp_path):
    """声明了却没用到的参数：调用方必须传它，但它不影响 SQL —— 属于定义错误。"""
    with pytest.raises(sc.ScriptError) as exc:
        _load(MINIMAL_YAML + "  - key: unused\n    type: INTEGER\n    description: x\n", tmp_path)
    assert "unused" in str(exc.value)


def test_placeholder_without_declaration_is_rejected(tmp_path):
    """SQL 里有未声明的占位符，渲染时必然残留 —— 注册期就挡住。"""
    with pytest.raises(sc.ScriptError) as exc:
        _load(
            MINIMAL_YAML.replace(
                "runtime > {{threshold_seconds}}",
                "runtime > {{threshold_seconds}} limit {{n}}",
            ),
            tmp_path,
        )
    assert "n" in str(exc.value)


def test_duplicate_param_key_is_rejected(tmp_path):
    with pytest.raises(sc.ScriptError):
        _load(
            MINIMAL_YAML + "  - key: threshold_seconds\n    type: INTEGER\n"
            "    description: dup\n",
            tmp_path,
        )
