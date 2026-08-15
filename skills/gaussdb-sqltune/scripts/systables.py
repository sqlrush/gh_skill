"""System-SQL policy gate for sqltune.

策略:GaussDB/openGauss 系统表/系统视图上的慢 SQL 不做调优——系统对象的
结构与访问路径由内核维护,用户不能也不应在其上建索引或改写内核/监控查询;
此类慢通常反映采集频率或系统压力,不是 SQL 本身的问题。

判定必须**保守**:只有 SQL 引用的全部关系都能确定是系统对象时才判定为
系统 SQL。误放行只是多出一份无害的分析;误拦截会吞掉用户的真实调优请求,
所以拿不准一律放行(如用户 schema 下恰好叫 pg_* 的表)。
"""
from __future__ import annotations

from dataclasses import dataclass

import evidence

# openGauss/GaussDB 内置 schema(含 A 兼容与自带工具包)。schema 限定的引用
# 只按这张表判——不在表里的 schema 一律视为用户对象,前缀不再参与判断。
_SYSTEM_SCHEMAS = frozenset({
    "pg_catalog", "information_schema", "sys",
    "dbe_perf", "dbe_pldeveloper", "dbe_pldebugger", "dbe_sql_util",
    "snapshot", "blockchain", "db4ai", "sqladvisor",
    "pkg_service", "pkg_util", "cstore", "pmk",
})

# 未限定 schema 时:openGauss 系统表/系统视图统一以 pg_/gs_ 开头。
_SYSTEM_NAME_PREFIXES = ("pg_", "gs_")

# 前缀盖不住的零散系统对象。
_SYSTEM_NAMES = frozenset({"dual", "sys_dummy", "statement_history"})


@dataclass(frozen=True)
class SystemVerdict:
    is_system: bool
    system_objects: list[str]


class SystemSQLSkipped(Exception):
    """Raised by the tuning pipeline when the target SQL is system-only."""

    def __init__(self, objects: list[str]):
        super().__init__("system SQL — tuning skipped by policy")
        self.objects = objects


def _is_system_object(ref: str) -> bool:
    if "." in ref:
        schema = ref[:ref.index(".")]
        return schema in _SYSTEM_SCHEMAS
    return ref.startswith(_SYSTEM_NAME_PREFIXES) or ref in _SYSTEM_NAMES


def system_verdict(sql_text: str) -> SystemVerdict:
    """System iff every referenced relation is a recognized system object."""
    refs = evidence.extract_table_refs(sql_text)
    hits = [r for r in refs if _is_system_object(r)]
    return SystemVerdict(is_system=bool(refs) and len(hits) == len(refs),
                         system_objects=hits)


def skip_report(objects: list[str]) -> str:
    lines = "\n".join(f"- `{o}`" for o in objects)
    return f"""# SQL Tune — 系统对象 SQL,按策略跳过

该 SQL 只访问 GaussDB/openGauss 系统表/系统视图:

{lines}

按既定策略,系统对象上的慢 SQL **不做调优**:

- 系统表/系统视图的结构与访问路径由内核维护,用户侧不能也不应在其上建索引、改写内核/监控查询;
- 此类 SQL 变慢通常反映监控采集频率过高或系统整体压力,应从采集来源与系统负载入手,而不是调这条 SQL。

未采集证据,未生成任何优化建议。这是确定性结论——对同一条 SQL 重试不会得到不同结果。
"""


def skip_json(objects: list[str]) -> dict:
    return {"skipped": True, "reason": "system-sql", "system_objects": objects}
