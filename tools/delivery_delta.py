#!/usr/bin/env python3
"""生成白名单的**增量导入包** —— 相对客户已导入的那一版,只含新增与变更。

    python3 tools/delivery_delta.py --base origin/main --out docs/delivery/scripts-delta-YYYYMMDD.sql

**为什么要有这个工具。** 2026-10-18 第一次出增量包时,新增/变更清单是手写的,
结果漏了 4 条正文变更 —— 其中 wdr.file_io 修的是物理读少报 62%、写得最多的那个
文件在报告里完全不存在。手写清单靠记忆,而记忆恰恰会漏掉「顺手改过的那几条」。

所以清单一律**自动算**:拿 `--base` 那个 git 版本里的 docs/delivery/scripts.sql
(= 客户在用的那份)对比当前的 docs/delivery/scripts.sql(= 交付集),逐条比脚本名
与 SQL 正文。说明文字从 docs/delivery/delta-notes.yaml 读;**任何一条新增或变更
在那里没有说明,直接报错退出** —— 不许静默地出一个缺条目的包。

输出的 DML 用**客户真实形态**(与 scripts.sql 一致:表 grmp.script_config、
id 走 seq.nextval、uuid()、now()…),不是 grmp_register 的生成器形态 ——
后者客户导不进去(同一天踩过,见 tests/test_delivery_drift_units.py 末尾那段)。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import pathlib
import re
import subprocess
import sys

_ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT))

import yaml  # noqa: E402

from common.grmp.script import load_script  # noqa: E402

SHIPPED = "docs/delivery/scripts.sql"
NOTES = _ROOT / "docs" / "delivery" / "delta-notes.yaml"

COLS = ('id, script_type, script_name, database_type, refered_appbusiness, kernel_version, '
        'region, deployment_form, execute_node_type, cluster_deployment_mode, script_content, '
        'parameter_config, scene, is_valid, create_user, create_time, last_modify_user, '
        'last_modify_time, is_asyn, "extend", compliance_mode, uuid')

_NAME = re.compile(r"VALUES \([^,]+, '[A-Z]+', '([a-z_]+\.[a-z_0-9]+)'")
_BODY = re.compile(r"'centralization', '(.*?)', '\[", re.S)
_PARAMS = re.compile(r"'centralization', '.*?', '(\[.*?\])', 'AGENT'", re.S)


def bodies(dml: str) -> dict:
    """DML 文本 → {脚本名: SQL 正文}。正文里的 '' 还原成 '。"""
    out = {}
    for b in re.split(r"(?=INSERT INTO )", dml):
        n, m = _NAME.search(b), _BODY.search(b)
        if n and m:
            out[n.group(1)] = m.group(1).replace("''", "'")
    return out


def params(dml: str) -> dict:
    """DML 文本 → {脚本名: parameter_config 原文}。"""
    out = {}
    for b in re.split(r"(?=INSERT INTO )", dml):
        n, m = _NAME.search(b), _PARAMS.search(b)
        if n and m:
            out[n.group(1)] = m.group(1).replace("''", "'")
    return out


def _param_keys(text: str) -> list:
    """参数定义只比「有哪些参数、什么类型」:value / autoAcquire 这些占位字段不算变更。"""
    try:
        return [(p.get("key"), p.get("type")) for p in json.loads(text or "[]")]
    except (ValueError, AttributeError):
        return [("?", text)]


def registry() -> dict:
    return {load_script(p).script_name: load_script(p)
            for p in sorted((_ROOT / "scripts" / "registry").glob("*/*.yaml"))}


def _base_dml(base_ref: str) -> str:
    return subprocess.run(["git", "show", "%s:%s" % (base_ref, SHIPPED)], cwd=_ROOT,
                          capture_output=True, text=True, check=True).stdout


def diff(base_ref: str):
    """(新增, 变更, 消失) —— 三个排好序的脚本名列表。正文或参数定义变了都算变更。"""
    base = _base_dml(base_ref)
    now = (_ROOT / SHIPPED).read_text(encoding="utf-8")
    b, n, reg = bodies(base), bodies(now), registry()
    added = sorted(set(n) - set(b))
    gone = sorted(set(b) - set(n))
    pchanged = params_changed(base_ref, sorted(set(n) & set(b)), base=base, reg=reg)
    changed = sorted(k for k in set(n) & set(b)
                     if b[k].strip() != reg[k].script_content.strip() or k in pchanged)
    return added, changed, gone, len(b), len(n)


def params_changed(base_ref: str, names, base: str = "", reg: dict = None) -> set:
    """参数定义变了的脚本。**只改正文不改参数,客户的中间件不认新参数** —— 2026-10-24 health.stale_stats
    加了 min_pages,第一版增量包只 UPDATE 了 script_content,导进去之后健康检查一调就会被拒。"""
    pb = params(base or _base_dml(base_ref))
    reg = reg or registry()
    return {k for k in names if k in pb and _param_keys(pb[k]) != _param_keys(reg[k].parameter_config)}


def _esc(sql: str) -> str:
    s = sql.replace("'", "''")
    return s if s.endswith("\n") else s + "\n"


def _params(rec) -> str:
    return ",".join('{"key":"%s","value":"","type":"%s","autoAcquire":false}'
                    % (p.key, p.type) for p in (rec.params or []))


def _stored(sql: str) -> str:
    """导入后库里存的正文(UPDATE / INSERT 写进去的那个字面量还原后)。"""
    return sql if sql.endswith("\n") else sql + "\n"


def render(added, changed, notes, n_base, n_now, date, pchanged=frozenset()) -> str:
    reg = registry()
    out = ["""-- =====================================================================
-- GRMP 诊断脚本白名单 · 增量导入（%(date)s）
-- =====================================================================
--
-- 适用前提：已导入过上一版的 %(nb)d 条脚本。本文件只含相对那一版的**增量**，
-- 不要整份重导（整份见同目录 scripts.sql，共 %(nn)d 条）。
--
--   新增   %(na)d 条 INSERT
--   更新   %(nc)d 条 UPDATE
--
-- 全部脚本均为**只读查询**，只访问系统视图与系统表，不读业务数据、
-- 不含 DML/DDL、不修改任何对象。
--
-- 导入前请确认两处与贵方规范的对齐：
--   1. `create_user` / `last_modify_user` 当前填 '999999999'，与上一版保持一致；
--      若贵方要求真实工号，请统一替换。
--   2. id 用 `grmp.script_config_seq.nextval`，不硬编码，避免与现有记录冲突。
--
-- =====================================================================
-- 一、新增 %(na)d 条
-- =====================================================================
""" % {"date": date, "nb": n_base, "nn": n_now, "na": len(added), "nc": len(changed)}]
    for name in added:
        rec = reg[name]
        title, src, why = notes["added"][name]
        out.append("\n-- [%s] %s\n--   读取：%s\n--   用途：%s\n" % (name, title, src, why))
        out.append("INSERT INTO grmp.script_config (%s) VALUES "
                   "(grmp.script_config_seq.nextval, 'SQL', '%s', 'appbusiness', 1, 'ALL', "
                   "NULL, NULL, NULL, 'centralization', '%s', '[%s]', 'AGENT', 1, "
                   "'999999999', now(), '999999999', NULL, 0, NULL, 'ALL', uuid());\n"
                   % (COLS, name, _esc(rec.script_content), _params(rec)))
    out.append("""
-- =====================================================================
-- 二、更新 %d 条已有脚本的 SQL 正文
-- =====================================================================
""" % len(changed))
    for name in changed:
        out.append("\n-- [%s]\n--   %s\n" % (name, notes["changed"][name]))
        pset = ("       parameter_config = '%s',\n" % reg[name].parameter_config.replace("'", "''")
                if name in pchanged else "")
        out.append("UPDATE grmp.script_config\n"
                   "   SET script_content   = '%s',\n"
                   "%s"
                   "       last_modify_user = '999999999',\n"
                   "       last_modify_time = now()\n"
                   " WHERE script_name = '%s';\n" % (_esc(reg[name].script_content), pset, name))
    names = ",".join("'%s'" % n for n in added)
    # 自检按这次的包现算:每条更新后的正文的 md5(以前写死的是 10-18 那一包的特征串,换一包就失效)
    md5s = ",\n--     ".join("('%s', '%s')" % (n, hashlib.md5(_stored(reg[n].script_content).encode("utf-8")).hexdigest())
                            for n in changed) or "('-', '-')"
    pnames = ",".join("'%s'" % n for n in sorted(pchanged)) or "'-'"
    out.append("""
-- =====================================================================
-- 三、导入后自检
-- =====================================================================
-- 应为 %d：
--   SELECT count(*) FROM grmp.script_config WHERE scene = 'AGENT';
%s-- 更新应已生效（应返回 0 行；库编码为 UTF8 时 md5 与这里一致）：
--   SELECT c.script_name FROM grmp.script_config c
--     JOIN (VALUES
--     %s) AS v(name, sum) ON c.script_name = v.name
--    WHERE md5(c.script_content) <> v.sum;
-- 参数定义也一起更新了的（应列出这些脚本、且 parameter_config 与 scripts.sql 里一致）：
--   SELECT script_name, parameter_config FROM grmp.script_config WHERE script_name IN (%s);
""" % (n_now, ("-- 新增的 %d 条应全部在列：\n--   SELECT script_name FROM grmp.script_config WHERE script_name IN (%s);\n"
                 % (len(added), names)) if added else "-- 本次没有新增脚本。\n", md5s, pnames))
    return "".join(out)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--base", default="origin/main",
                    help="客户在用的那一版所在的 git 版本(默认 origin/main)")
    ap.add_argument("--out", required=True)
    ap.add_argument("--date", required=True, help="写进文件头的日期,如 2026-10-18")
    a = ap.parse_args(argv)

    added, changed, gone, nb, nn = diff(a.base)
    notes = yaml.safe_load(NOTES.read_text(encoding="utf-8")) or {}
    miss = ([n for n in added if n not in (notes.get("added") or {})]
            + [n for n in changed if n not in (notes.get("changed") or {})])
    if miss:
        print("error: 这些脚本有新增/变更,但 %s 里没有说明 —— 补上再生成,"
              "不许出一个缺条目的包:\n  %s" % (NOTES.name, "\n  ".join(miss)), file=sys.stderr)
        return 2
    if gone:
        # 删除脚本需要客户手工处置(DELETE 有风险,不自动生成),但必须让人看见
        print("warning: 这些脚本在客户那版里有、现在没了,增量包**不**生成 DELETE,"
              "请人工确认是否要客户删除:\n  %s" % "\n  ".join(gone), file=sys.stderr)
    pchanged = params_changed(a.base, changed) if changed else set()
    pathlib.Path(a.out).write_text(render(added, changed, notes, nb, nn, a.date, pchanged), encoding="utf-8")
    print("写出 %s:新增 %d、更新 %d(其中参数定义也变了 %d:%s)(基线 %s,%d → %d 条)"
          % (a.out, len(added), len(changed), len(pchanged), "、".join(sorted(pchanged)) or "无", a.base, nb, nn))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
