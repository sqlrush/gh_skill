"""grmp-mock 启动入口。

    GRMP_AUTH_TOKEN=xxx python3 -m tools.grmp_mock --port 8765

令牌只从环境变量读，不落盘、不进代码、不进版本库；缺失即拒绝启动。
"""
from __future__ import annotations

import argparse
import os
import pathlib
import sys
from typing import Sequence

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))

from tools.grmp_mock import instances as inst  # noqa: E402
from tools.grmp_mock.executor import (  # noqa: E402
    DEFAULT_MAX_RESULT_ROWS,
    DEFAULT_STATEMENT_TIMEOUT_SECONDS,
)
from tools.grmp_mock.http_server import BIND_HOST, serve  # noqa: E402
from tools.grmp_mock.server import LIST_PATH, App  # noqa: E402
from common.grmp.settings import Settings  # noqa: E402
from tools.grmp_mock.store import ScriptStore  # noqa: E402

DEFAULT_PORT = 8765
DEFAULT_DB = "~/.gdaa/grmp/script_config.db"
DEFAULT_INSTANCES = "~/.gdaa/grmp/instances.yaml"


def banner(app_settings: Settings, store: ScriptStore, imap: inst.InstanceMap,
           port: int, max_rows: int, timeout: int) -> str:
    lines = [
        "=" * 74,
        "grmp-mock —— GRMP 协议兼容中间件（测试替身）",
        "",
        "⚠️  仅限本机开发调试。只监听 %s，不得部署到共享或生产环境。" % BIND_HOST,
        "    本进程对 {{}} 采用文本替换而非绑定变量 —— 这是为复现客户行为",
        "    而刻意保留的注入面，对外暴露等于把它挂到网上。",
        "",
        "监听      http://%s:%d%s" % (BIND_HOST, port, LIST_PATH),
        "脚本      %d 条（is_valid=1）" % len(store.list_all()),
        "实例映射  %d 条" % imap.count(),
    ]
    for data_ip, conn in imap.items():
        lines.append("            %s -> %s" % (data_ip, conn))
    if imap.count() == 0:
        lines.append("            （空：任何 dataIp 都会返回「查不到实例」）")

    writable = store.writable_names()
    if writable:
        lines.append("⚠️ 可写脚本 %d 条（执行时开可写会话）：" % len(writable))
        lines += ["            %s" % n for n in writable]
    else:
        lines.append("可写脚本  无 —— 全部脚本都以只读会话执行")
    lines += [
        "",
        "本进程当前的假设（未经客户环境证实，猜错多半不报错、只出错值）：",
    ]
    lines += ["  - %s" % line for line in app_settings.assumption_lines()]
    lines += [
        "  - 执行失败的响应：**本实现发明**——不产出 result 键，错误放 msg。",
        "    文档对该分支零样例。这样写是为了让盲目取 result.data 的调用方",
        "    当场报错，而不是安静地拿到空列表、把「执行失败」读成「没有数据」。",
        "  - 结果集上限 %d 行、语句超时 %d 秒：文档均未定义，超限一律报错不截断。"
        % (max_rows, timeout),
        "",
        "未实现且会明确报错的：作用域过滤、异步执行、PYTHON 命令。",
        "=" * 74,
    ]
    return "\n".join(lines)


def main(argv: Sequence[str] = None) -> int:
    parser = argparse.ArgumentParser(description="启动 grmp-mock")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--db", default=DEFAULT_DB)
    parser.add_argument("--instances", default=DEFAULT_INSTANCES)
    parser.add_argument("--bool-style", default="t_f")
    parser.add_argument("--null-text", default="")
    parser.add_argument("--string-param-policy", default="as_is")
    parser.add_argument(
        "--max-result-rows", type=int, default=DEFAULT_MAX_RESULT_ROWS
    )
    parser.add_argument(
        "--statement-timeout", type=int, default=DEFAULT_STATEMENT_TIMEOUT_SECONDS
    )
    args = parser.parse_args(argv)

    token = os.environ.get("GRMP_AUTH_TOKEN")
    if not token:
        print(
            "缺少 GRMP_AUTH_TOKEN 环境变量。令牌不写进代码与配置文件，"
            "启动前请先设置。",
            file=sys.stderr,
        )
        return 2

    settings = Settings(
        bool_style=args.bool_style,
        null_text=args.null_text,
        string_param_policy=args.string_param_policy,
    )
    store = ScriptStore(pathlib.Path(args.db).expanduser())
    imap = inst.load(pathlib.Path(args.instances).expanduser())
    app = App(
        store=store,
        instances=imap,
        token=token,
        settings=settings,
        max_result_rows=args.max_result_rows,
        statement_timeout=args.statement_timeout,
    )

    # 走 stderr 且立刻 flush：横幅是安全警告，重定向到文件时 stdout 会被
    # 块缓冲，进程若被 kill 掉，警告就一个字都不会落盘 —— 等于没有。
    print(
        banner(
            settings, store, imap, args.port,
            args.max_result_rows, args.statement_timeout,
        ),
        file=sys.stderr,
        flush=True,
    )
    httpd = serve(app, args.port)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n已停止")
    finally:
        httpd.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
