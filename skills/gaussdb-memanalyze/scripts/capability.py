"""GUC probing: which layers can produce data, and — when they cannot — why.

A layer that is blind must say *why* it is blind, naming the GUC and its target
value. Rendering an empty operator table because `resource_track_level = query`
would read as "no operator problems", which is a lie. Same discipline as the
hypopg `provides_session` guard in sqltune.
"""
from __future__ import annotations

import common
from model import Capability, Catalog

GUCS = (
    "enable_resource_track",
    "resource_track_level",
    "resource_track_cost",
    "enable_resource_record",
    "memory_tracking_mode",
    "max_process_memory",
    "max_dynamic_memory",
    "work_mem",
    "maintenance_work_mem",
    "max_connections",
    "shared_buffers",
)

_ON = frozenset({"on", "true", "1", "yes"})

# GUC names are our own constants, never user input — safe to inline.
_GUC_Q = (
    "SELECT name, setting FROM pg_settings WHERE name IN ("
    + ", ".join("'" + g + "'" for g in GUCS)
    + ")"
)


def read_gucs(db) -> dict:
    """Read the memory-relevant GUCs. Missing ones are simply absent."""
    try:
        _, rows = db.query(_GUC_Q)
    except common.DBError:
        return {}
    return {str(r[0]): str(r[1]) for r in rows}


def _on(gucs: dict, name: str, default: str = "on") -> bool:
    return str(gucs.get(name, default)).strip().lower() in _ON


def assess(gucs: dict, catalog: Catalog) -> Capability:
    """Decide layer availability from the GUCs and the probed catalog (pure)."""
    reasons: dict[str, str] = {}

    track_on = _on(gucs, "enable_resource_track", "on")
    level = str(gucs.get("resource_track_level", "query")).strip().lower()
    record_on = _on(gucs, "enable_resource_record", "off")

    track_off_reason = (
        f"enable_resource_track = {gucs.get('enable_resource_track', 'off')}"
        f"（需设为 on）[需人工执行]"
    )

    # --- L4: SQL-level memory ------------------------------------------------
    sql_available = True
    if not track_on:
        sql_available = False
        reasons["L4"] = track_off_reason
    elif not catalog.has("wlm_session"):
        sql_available = False
        reasons["L4"] = catalog.reason("wlm_session")

    # --- L5: operator-level memory -------------------------------------------
    operator_available = True
    if not track_on:
        operator_available = False
        reasons["L5"] = track_off_reason
    elif level != "operator":
        operator_available = False
        reasons["L5"] = (
            f"resource_track_level = {level}（需设为 operator，否则算子级内存不采集）"
            f"[需人工执行]"
        )
    elif not catalog.has("wlm_operator"):
        operator_available = False
        reasons["L5"] = catalog.reason("wlm_operator")

    # --- history -------------------------------------------------------------
    history_available = True
    if not record_on:
        history_available = False
        reasons["history"] = (
            f"enable_resource_record = {gucs.get('enable_resource_record', 'off')}"
            f"（需设为 on，否则 gs_wlm_*_info 历史表无数据）[需人工执行]"
        )
    elif not catalog.has("wlm_session_hist"):
        history_available = False
        reasons["history"] = catalog.reason("wlm_session_hist")

    # --- L2: memory contexts -------------------------------------------------
    context_available = catalog.has("session_ctx") or catalog.has("thread_ctx")
    if not context_available:
        reasons["L2"] = catalog.reason("session_ctx")

    return Capability(
        gucs=gucs,
        sql_available=sql_available,
        operator_available=operator_available,
        history_available=history_available,
        context_available=context_available,
        reasons=reasons,
    )
