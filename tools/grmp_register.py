"""脚本注册工具 —— 对应客户侧的「版本 DML 发布」环节。

**这是发布期工具，不是运行期接口。** 客户的 GRMP 没有脚本管理 API
（「由于安全原因，目前脚本仅能通过版本 dml 带出」），白名单的写权限归
发布流程，读与执行权限归 agent。本工具因此刻意做成命令行，不挂 HTTP ——
挂上去就等于本地放开了客户不给的权限，explain 之类的脚本在本地能动态
注册、到客户环境注册不了，又是一个本地测不出来的落差。

用法：
    python3 -m tools.grmp_register --registry scripts/registry \\
        --db ~/.gdaa/grmp/script_config.db --dml-out docs/delivery/scripts.sql
"""
from __future__ import annotations

import argparse
import datetime
import os
import pathlib
import sys
from typing import List, Sequence, Tuple

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from common.grmp import script as sc  # noqa: E402
from tools.grmp_mock import dml, risk, store as st  # noqa: E402
from common.grmp.placeholder import ParamError  # noqa: E402

DEFAULT_REGISTRY = "scripts/registry"
DEFAULT_DB = "~/.gdaa/grmp/script_config.db"


def discover(registry: pathlib.Path) -> List[pathlib.Path]:
    """按路径排序，使注册顺序稳定 —— id 分配才可复现。"""
    return sorted(registry.rglob("*.yaml"))


def _timestamp() -> str:
    """客户样例的 create_time 精确到毫秒：2026-07-03 19:36:58.978。"""
    return datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]


def load_all(paths: Sequence[pathlib.Path]) -> Tuple[List[sc.ScriptRecord], List[str]]:
    """加载并硬校验。返回 (记录, 错误列表)；有错时不入库任何一条。"""
    records: List[sc.ScriptRecord] = []
    errors: List[str] = []
    seen_names = {}
    for path in paths:
        try:
            rec = sc.load_script(path)
        except (sc.ScriptError, ParamError) as exc:
            errors.append(str(exc))
            continue
        if rec.script_name in seen_names:
            errors.append(
                "%s: 逻辑名 %s 与 %s 重复"
                % (path, rec.script_name, seen_names[rec.script_name])
            )
            continue
        seen_names[rec.script_name] = path
        records.append(rec)
    return records, errors


def report_risks(records: Sequence[sc.ScriptRecord]) -> List[str]:
    """风险标注 —— 只报告，不拦截。客户环境能跑通的脚本，本地不能拒绝。"""
    lines: List[str] = []
    for rec in records:
        for item in risk.assess(rec.script_content):
            lines.append("  [%s] %s：%s" % (item.code, rec.script_name, item.detail))
    return lines


def main(argv: Sequence[str] = None) -> int:
    parser = argparse.ArgumentParser(description="注册 GRMP 诊断脚本")
    parser.add_argument("--registry", default=DEFAULT_REGISTRY)
    parser.add_argument("--db", default=DEFAULT_DB)
    parser.add_argument(
        "--replace",
        action="store_true",
        help="重名时覆盖（保持原 id 不变）。不给此参数时重名一律拒绝。",
    )
    parser.add_argument("--dml-out", default=None, help="导出客户格式 INSERT DML 的路径")
    parser.add_argument(
        "--user",
        default=os.environ.get("GRMP_REGISTER_USER", "grmp-register"),
        help="写入 create_user/last_modify_user。交付给客户的 DML 应填真实工号。",
    )
    parser.add_argument("--dry-run", action="store_true", help="只校验与出报告，不写库")
    args = parser.parse_args(argv)

    registry = pathlib.Path(args.registry).expanduser()
    if not registry.is_dir():
        print("脚本目录不存在：%s" % registry, file=sys.stderr)
        return 2

    paths = discover(registry)
    if not paths:
        print("脚本目录为空：%s" % registry, file=sys.stderr)
        return 2

    records, errors = load_all(paths)
    if errors:
        print("校验失败，未注册任何脚本：", file=sys.stderr)
        for line in errors:
            print("  - %s" % line, file=sys.stderr)
        return 1

    print("校验通过：%d 条脚本" % len(records))
    risks = report_risks(records)
    if risks:
        print("\n风险标注（放行，但需人工确认 —— 这些脚本在客户环境同样存在该风险）：")
        for line in risks:
            print(line)
    else:
        print("风险标注：无")

    if args.dry_run:
        print("\n--dry-run：未写库")
        return 0

    when = _timestamp()
    store = st.ScriptStore(pathlib.Path(args.db).expanduser())
    stored: List[sc.ScriptRecord] = []
    try:
        for rec in records:
            stored.append(
                store.register(rec.stamped(args.user, when), replace=args.replace)
            )
    except st.StoreError as exc:
        print("\n注册中止：%s" % exc, file=sys.stderr)
        return 1

    print("\n已注册：")
    for rec in stored:
        print("  id=%-4s %s" % (rec.id, rec.script_name))

    if args.dml_out:
        out = pathlib.Path(args.dml_out).expanduser()
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(dml.script_file(stored), encoding="utf-8")
        print("\n交付 DML 已写出：%s" % out)
        print("  注意：create_user 当前为 %r，交付前应改成真实工号。" % args.user)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
