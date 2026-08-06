#!/usr/bin/env python3
"""explain — EXPLAIN a statement with deterministic risk findings.

Port of internal/probe/explain.go + internal/analyze/risks.go + cli/explain.go.

Usage:
    explain.py -c <conn> --sql-stdin [--analyze] [--format json] <<'SQL'
    SELECT ...
    SQL
"""
from __future__ import annotations

import argparse
import json
import pathlib
import re
import sys
from dataclasses import dataclass
from typing import Optional

_HERE = pathlib.Path(__file__).resolve()
sys.path.insert(0, str(_HERE.parent))          # sibling modules
for _anc in _HERE.parents:                      # locate common/ (repo root or install dir)
    if (_anc / "common" / "__init__.py").exists():
        sys.path.insert(0, str(_anc))
        break

import common  # noqa: E402
from common import access  # noqa: E402
from common.grmp.statement import (  # noqa: E402
    ExplainNotAllowed,
    ensure_explainable,
)
import render  # noqa: E402

# 本 skill 唯一的查询点就是「对用户给的任意 SQL 做 EXPLAIN」。
# 它**没有可迁到 scripts/registry/ 的部分**：白名单模型按逻辑脚本名放行
# 预注册的 SQL，而这里的 SQL 每次都不同，注册不进去。
#
# 唯一能让它走中间件的写法是注册一条 `EXPLAIN {{user_sql}}` 的直通脚本
# （实测可行，见 docs/test/2026-08-03-gh_skill-经中间件访问测试报告.md 发现 4）。
# 本实现**不这么做**：那等于在白名单上开一个通用入口，任何 SQL 都能从这条
# 脚本进去，白名单的意义被架空。要不要开这个口子是客户的安全策略决策，
# 不是技术决策，不该由交付方替客户定。
#
# 所以 grmp 连接在入口处明确报错，而不是静默降级或偷偷绕过；
# 直连连接（pg8000 / gsql）行为与迁移前完全一致。
# 「路给不了」由连接模块说（access.require_unregistered_sql），这里只补
# 本 skill 的策略：为什么不绕过去。
_WHY_NO_PASSTHROUGH = (
    "本 skill 不为此注册「EXPLAIN {{user_sql}}」这类直通脚本 —— 那会让任何 SQL "
    "都能从这一条脚本进入，白名单形同虚设。是否开这个口子属于客户的安全策略决策。\n"
    "可选做法：为任意 SQL 类诊断保留一条直连通道（driver: pg8000 / gsql），"
    "或在客户环境不提供本 skill。"
)


def require_direct_sql_path(conn_name: str) -> None:
    """任意 SQL 只能走直连。走不了就当场报错，绝不降级成别的结果。

    **刻意不用 access.session_for()**：那个口子除了白名单型驱动，还会拒掉
    provides_session=False 的 gsql（每条语句起独立子进程）。而 explain
    根本不需要跨语句会话 —— 单条 EXPLAIN，DML 的 ANALYZE 也是在一次
    调用里 BEGIN/ROLLBACK 包住的，gsql 今天跑得好好的。用会话守卫会把
    一批能用的连接一并拒掉，属于借来的约束。这里只判它真正的边界：
    能不能执行未注册的 SQL。

    判断本身交给连接模块 —— skill 里不该出现 driver 名字，否则客户再换
    一种白名单型中间件，这句判断会静默放行。
    """
    common.find(conn_name)                             # ConfigError 由调用方接
    try:
        access.require_unregistered_sql(conn_name)
    except access.UnregisteredSqlUnsupported as exc:
        raise access.AccessError("%s\n%s" % (exc, _WHY_NO_PASSTHROUGH)) from exc


_DML_RE = re.compile(r"(?i)^\s*(insert|update|delete|merge)\b")
_CTE_RE = re.compile(r"(?i)^\s*with\b")
_CTE_DML_RE = re.compile(r"(?i)\b(insert|update|delete|merge)\b")


@dataclass(frozen=True)
class Finding:
    kind: str
    severity: str
    detail: str
    advice: str


def is_dml(sql_text: str) -> bool:
    if _DML_RE.search(sql_text):
        return True
    return bool(_CTE_RE.search(sql_text) and _CTE_DML_RE.search(sql_text))


def explain_via_script(runner, sql_text: str, analyze: bool) -> str:
    """走已注册的 EXPLAIN 模板。中间件与直连共用这条路。

    调用前必须先过 ensure_explainable() —— 模板是文本替换，参数位就是注入面。
    """
    script = "explain.plan_text_analyze" if analyze else "explain.plan_text"
    rows = runner.run(script, {"sql": sql_text})
    # 结果行是列名到值的字典；EXPLAIN 只有一列，取那一列的值
    return "\n".join(str(next(iter(r.values()), "")) for r in rows)


def explain(db, sql_text: str, analyze: bool) -> str:
    stmt = (f"EXPLAIN (ANALYZE {str(analyze).lower()}, "
            f"BUFFERS {str(analyze).lower()}, FORMAT TEXT) {sql_text}")
    if analyze and is_dml(sql_text):
        _, rows = db.query_in_rollback(stmt)
    else:
        _, rows = db.query(stmt)
    return "\n".join(r[0] for r in rows)


def scan_plan(plan_text: str) -> list[Finding]:
    lower = plan_text.lower()
    orig_lines = plan_text.split("\n")
    out: list[Finding] = []
    for i, line in enumerate(lower.split("\n")):
        trimmed = line.strip()
        if trimmed.startswith("->"):
            trimmed = trimmed[2:].strip()
        detail = orig_lines[i].strip()
        if trimmed.startswith("seq scan"):
            out.append(Finding("seq_scan", "warn", detail,
                "Full table scan; consider an index on the Filter columns if "
                "the table is large and selectivity is high."))
        elif trimmed.startswith("sort"):
            out.append(Finding("sort", "warn", detail,
                "Explicit sort; an index matching ORDER BY may remove it. "
                "Check work_mem if the sort spills to disk."))
    if "nested loop" in lower and "seq scan" in lower:
        out.append(Finding("nestloop_seqscan", "warn",
            "Nested Loop combined with Seq Scan",
            "Inner-side full scans inside a nested loop multiply cost; "
            "consider an index on the join key."))
    if "hash join" in lower:
        out.append(Finding("hash_join", "info", "Hash Join present",
            "Usually fine for large joins; verify hash memory fits work_mem."))
    return out


def explain_report(sql_text: str, plan: str, findings: list[Finding]) -> str:
    out = ("## SQL\n\n" + render.code_block("sql", render.truncate(sql_text, 2000)) +
           "\n## Execution Plan\n\n" + render.code_block("", plan))
    if not findings:
        return out + "\n## Findings\n\nNo deterministic risk patterns detected.\n"
    out += "\n## Findings\n\n"
    for f in findings:
        out += f"- **[{f.severity}] {f.kind}**: {f.detail} — {f.advice}\n"
    return out


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(prog="explain.py",
                                 description="EXPLAIN a statement with risk findings")
    ap.add_argument("-c", "--conn", default="", help="连接名（省略则用 gaussdb-login 建立的会话）")
    ap.add_argument("--sql-stdin", action="store_true", required=True,
                    help="read SQL text from stdin")
    ap.add_argument("--analyze", action="store_true",
                    help="EXPLAIN ANALYZE (executes; DML wrapped in rollback)")
    ap.add_argument("--format", choices=["markdown", "json"], default="markdown")
    ap.add_argument("--timeout", type=int, default=None)
    args = ap.parse_args(argv)

    sql_text = sys.stdin.read()
    if not sql_text.strip():
        ap.error("empty SQL on stdin")

    # 先看这条 SQL 能不能走注册模板。模板只受理单条只读语句 —— 它是文本替换，
    # 参数位就是注入面，多语句和写语句一律挡在外面。
    #
    # 走不通**不等于做不了**：直连有原始会话，`EXPLAIN UPDATE ...` 一直是能出
    # 计划的（不带 --analyze 时 DML 根本不执行）。所以这里回落到直连，而不是
    # 直接失败 —— 把模板的限制当成 skill 的限制，会砍掉直连本来就有的能力。
    try:
        ensure_explainable(sql_text, analyze=args.analyze)
        template_blocked = None
    except ExplainNotAllowed as exc:
        template_blocked = exc

    db = None
    try:
        if template_blocked is None:
            runner = access.for_conn(args.conn, timeout=args.timeout)
            plan = explain_via_script(runner, sql_text, args.analyze)
        else:
            # 回落到原始会话。中间件给不了，connection_for 会在这里明确报错
            # 而不是静默降级 —— 降级成「不 analyze」会让用户拿到估算计划却
            # 以为是实际计划，实测两者能差 2.3 倍。
            db = access.connection_for(args.conn, read_only=not args.analyze)
            db.set_statement_timeout(
                args.timeout if args.timeout is not None
                else access.DEFAULT_SKILL_TIMEOUT_SECONDS)
            plan = explain(db, sql_text, args.analyze)
    except (common.ConfigError, common.CredentialError, common.DBError,
            access.AccessError) as exc:
        # 回落到直连又失败时，把「模板为什么走不通」一并说出来。只报后半句
        # （「白名单只执行预注册脚本」）会让人以为是配置问题，而真正的原因是
        # 这条 SQL 的形态本身就进不了模板。
        if template_blocked is not None:
            print(f"error: {template_blocked}\n{exc}", file=sys.stderr)
        else:
            print(f"error: {exc}", file=sys.stderr)
        return 2
    except (ValueError, access.QueryError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    finally:
        if db is not None:
            db.close()

    findings = scan_plan(plan)
    if args.format == "json":
        print(json.dumps({"sql": sql_text, "plan": plan,
                          "findings": [f.__dict__ for f in findings]},
                         ensure_ascii=False, indent=2))
    else:
        print(explain_report(sql_text, plan, findings), end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
