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


def bodies(dml: str) -> dict:
    """DML 文本 → {脚本名: SQL 正文}。正文里的 '' 还原成 '。"""
    out = {}
    for b in re.split(r"(?=INSERT INTO )", dml):
        n, m = _NAME.search(b), _BODY.search(b)
        if n and m:
            out[n.group(1)] = m.group(1).replace("''", "'")
    return out


def registry() -> dict:
    return {load_script(p).script_name: load_script(p)
            for p in sorted((_ROOT / "scripts" / "registry").glob("*/*.yaml"))}


def diff(base_ref: str):
    """(新增, 变更, 消失) —— 三个排好序的脚本名列表。"""
    base = subprocess.run(["git", "show", "%s:%s" % (base_ref, SHIPPED)], cwd=_ROOT,
                          capture_output=True, text=True, check=True).stdout
    now = (_ROOT / SHIPPED).read_text(encoding="utf-8")
    b, n, reg = bodies(base), bodies(now), registry()
    added = sorted(set(n) - set(b))
    gone = sorted(set(b) - set(n))
    changed = sorted(k for k in set(n) & set(b)
                     if b[k].strip() != reg[k].script_content.strip())
    return added, changed, gone, len(b), len(n)


def _esc(sql: str) -> str:
    s = sql.replace("'", "''")
    return s if s.endswith("\n") else s + "\n"


def _params(rec) -> str:
    return ",".join('{"key":"%s","value":"","type":"%s","autoAcquire":false}'
                    % (p.key, p.type) for p in (rec.params or []))


def render(added, changed, notes, n_base, n_now, date) -> str:
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
        out.append("UPDATE grmp.script_config\n"
                   "   SET script_content   = '%s',\n"
                   "       last_modify_user = '999999999',\n"
                   "       last_modify_time = now()\n"
                   " WHERE script_name = '%s';\n" % (_esc(reg[name].script_content), name))
    names = ",".join("'%s'" % n for n in added)
    out.append("""
-- =====================================================================
-- 三、导入后自检
-- =====================================================================
-- 应为 %d：
--   SELECT count(*) FROM grmp.script_config WHERE scene = 'AGENT';
-- 新增的 %d 条应全部在列：
--   SELECT script_name FROM grmp.script_config WHERE script_name IN (%s);
-- 更新应已生效（下面两条都应返回 0 行）：
--   SELECT script_name FROM grmp.script_config
--    WHERE script_name LIKE '%%plan_text_analyze' AND script_content LIKE '%%fasle%%';
--   SELECT script_name FROM grmp.script_config
--    WHERE script_name IN ('wdr.top_sql','wdr.file_io','wdr.cache','wdr.db_stat','wdr.db_summary')
--      AND script_content NOT LIKE '%%LEFT JOIN%%';
""" % (n_now, len(added), names))
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
    pathlib.Path(a.out).write_text(render(added, changed, notes, nb, nn, a.date), encoding="utf-8")
    print("写出 %s:新增 %d、更新 %d(基线 %s,%d → %d 条)"
          % (a.out, len(added), len(changed), a.base, nb, nn))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
