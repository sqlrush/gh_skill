#!/usr/bin/env python3
"""建立本次会话要连的数据库 —— 其余 skill 的取数入口。

配置的首行 `connection_mode` 决定走哪条路：

    gsql  直连。db_connections 下按应用分组，逐条列出来让用户挑。
    api   走 GRMP 中间件。连接不是预先配好的，而是登录时按用户给的
          目标库现场构造 —— 所以要问用户连哪个库。

选定之后写进会话文件（common/session.py），其余 skill 不带 `-c` 时从那里取。

**只读**：本 skill 不改配置、不存口令、不建库。它做的唯一「写」是那个会话
文件，内容是「当前连哪个库」，不含任何凭据。
"""
from __future__ import annotations

import argparse
import re
import pathlib
import sys
from typing import List, Optional

_HERE = pathlib.Path(__file__).resolve()
sys.path.insert(0, str(_HERE.parent))
for _anc in _HERE.parents:
    if (_anc / "common" / "__init__.py").exists():
        sys.path.insert(0, str(_anc))
        break

import render  # noqa: E402
from common import access, config, session  # noqa: E402
from common.config import MODE_API, MODE_GSQL, ConfigError, Connection  # noqa: E402
from common.credential import secret_for  # noqa: E402
from common.db import Database  # noqa: E402


def _fail(message: str) -> int:
    print(message, file=sys.stderr)
    return 1


# --- gsql：从配置里挑一条 -----------------------------------------------------

def _candidates() -> List[Connection]:
    return sorted(config.load(), key=lambda c: (c.app, c.name))


def _render_menu(conns: List[Connection]) -> str:
    rows = []
    for i, c in enumerate(conns, 1):
        target = "%s:%s/%s" % (c.host, c.port, c.database)
        rows.append([str(i), c.app or "—", c.name, c.type, target,
                     c.user, c.driver])
    return render.table(["#", "应用", "连接名", "类型", "目标", "用户", "驱动"],
                        rows)


def _pick(conns: List[Connection], app: Optional[str],
          name: Optional[str]) -> Optional[Connection]:
    """按 --app / --conn 缩小范围。唯一命中才返回，否则 None（交给上层列菜单）。"""
    hits = [c for c in conns
            if (not app or c.app == app) and (not name or c.name == name)]
    return hits[0] if len(hits) == 1 else None


# --- api：现场构造一条 --------------------------------------------------------

_SAFE_NAME_RE = re.compile(r"[^a-z0-9_-]+")


def _safe_name(raw: str) -> str:
    """把用户给的目标标识派生成一个合法连接名。

    dataIp 常常是 `10.0.0.9` 这种带点的值，而连接名规则不许有点 ——
    **不能为此放松规则**：同一个名字还用来拼凭据文件名
    `credentials/<name>.enc`，放开点和斜杠等于给路径穿越开口子。
    所以派生一个安全名字，原值原样留在 data_ip 里发给中间件。
    """
    slug = _SAFE_NAME_RE.sub("-", raw.strip().lower()).strip("-")
    if not slug:
        raise ConfigError("目标标识 %r 里没有可用字符，无法派生连接名" % raw)
    return slug if slug[0].isalnum() else "api-" + slug


def _api_connection(target: str, endpoint) -> Connection:
    """按用户给的目标库/实例构造一条走中间件的连接。

    target 原样进 data_ip —— 客户的 GRMP 用 dataIp 定位实例，那个值在他们
    那边就是实例地址，这里不另造一套映射。连接名由 target 派生（见 _safe_name）。
    """
    return Connection(
        name=_safe_name(target), type="gaussdb",
        host=endpoint.host, port=endpoint.port,
        database=target, user="grmp", driver="grmp",
        data_ip=target, app="api",
    )


# --- 连通性验证 ---------------------------------------------------------------

def _verify(conn: Connection) -> tuple:
    """真连一次，返回 (是否成功, 说明)。

    **登录必须验连通性。** 只把选择记下来的话，失败会推迟到下一个 skill
    取数时才发生，那时用户已经在问别的问题了，错误看起来像是那个 skill 坏了。

    两条路验的东西不同，刻意不统一：
      api  调接口一取命令清单 —— 一次验掉端点可达、令牌有效、dataIp 被受理，
           且不依赖任何一条具体脚本是否注册过。
      gsql 直接连库跑 SELECT 1 —— 验的是 host/port/口令，同样不经过 registry。
           拿某条注册脚本去验的话，脚本没注册会被报成「连不上」。
    """
    if conn.driver == "grmp":
        try:
            runner = access.runner_for(conn)
        except Exception as exc:
            return False, "建立取数通道失败：%s" % exc
        try:
            ops = runner.client.list_operations()
            return True, "中间件应答正常，可用脚本 %d 条" % len(ops)
        except Exception as exc:
            return False, ("调用中间件接口一失败：%s\n"
                           "常见原因：令牌未设置或过期、端点不通、"
                           "dataIp 不被受理。" % exc)

    try:
        db = Database.open(conn, secret_for(conn), read_only=True)
    except Exception as exc:
        return False, "连库失败：%s" % exc
    try:
        _, rows = db.query("SELECT version()")
        version = str(rows[0][0]) if rows else ""
        return True, "连接正常%s" % (("；" + version[:70]) if version else "")
    except Exception as exc:
        return False, "连上了但查询失败：%s" % exc
    finally:
        db.close()


# --- 输出 ---------------------------------------------------------------------

def _describe(conn: Connection, note: str, path) -> str:
    out = "# 已登录\n\n"
    out += render.table(
        ["项", "值"],
        [["模式", "api（GRMP 中间件）" if conn.driver == "grmp" else "gsql（直连）"],
         ["应用", conn.app or "—"],
         ["连接名", conn.name],
         ["目标", "%s:%s/%s" % (conn.host, conn.port, conn.database)],
         ["驱动", conn.driver],
         ["验证", note]])
    out += "\n会话已写入 `%s`。\n\n" % path
    out += ("后续 13 个 skill **不带 `-c` 就会用这条连接**；要临时换一个，"
            "仍可显式传 `-c <连接名>`。\n")
    if conn.driver == "grmp":
        out += ("\n> 中间件模式下 hypopg 虚拟索引验证不可用（白名单模型没有"
                "跨语句持久会话）。sqltune 会改用代价推演给证据。\n")
    return out


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(
        prog="login.py", description="建立本次会话要连的数据库")
    ap.add_argument("--app", help="gsql 模式：应用分组名")
    ap.add_argument("--conn", help="gsql 模式：连接名")
    ap.add_argument("--database", help="api 模式：要访问的数据库/实例标识")
    ap.add_argument("--list", action="store_true", help="只列出可选项，不登录")
    ap.add_argument("--status", action="store_true", help="显示当前会话")
    ap.add_argument("--logout", action="store_true", help="清除当前会话")
    ap.add_argument("--no-verify", action="store_true",
                    help="跳过连通性验证（不建议：失败会推迟到下一个 skill）")
    args = ap.parse_args(argv)

    try:
        if args.logout:
            print("已清除会话。" if session.clear() else "本来就没有会话。")
            return 0

        if args.status:
            live = session.current()
            if live is None:
                print("当前没有会话。运行 gaussdb-login 选一个数据库。")
                return 0
            print(render.table(
                ["项", "值"],
                [["应用", live.app or "—"], ["连接名", live.name],
                 ["目标", "%s:%s/%s" % (live.host, live.port, live.database)],
                 ["驱动", live.driver]]))
            return 0

        current_mode = config.mode()

        if current_mode == MODE_API:
            endpoint = config.api_endpoint()
            if not endpoint.resolve_token():
                return _fail(
                    "中间件端点 %s:%s 已配置，但取不到令牌。\n"
                    "把它放进环境变量 %s（推荐），或写在 api_connection.token 里。\n"
                    "令牌是长期有效、无重放保护的静态凭据，放环境变量能少一份"
                    "落盘副本。"
                    % (endpoint.host, endpoint.port, endpoint.token_env))
            if args.list:
                print("模式：api（GRMP 中间件）\n端点：%s:%s\n"
                      % (endpoint.host, endpoint.port))
                print("api 模式没有预置的连接清单 —— 目标库由你指定。\n"
                      "用 `--database <库名>` 登录。")
                return 0
            if not args.database:
                print("模式：api（GRMP 中间件）")
                print("端点：%s:%s\n" % (endpoint.host, endpoint.port))
                print("请提供要访问的数据库（中间件按它路由到目标实例）：")
                print("    gaussdb-login --database <数据库名>")
                return 2
            conn = _api_connection(args.database.strip(), endpoint)
        else:
            conns = _candidates()
            if not conns:
                return _fail(
                    "配置里一条连接都没有。config.yaml 的 db_connections 下"
                    "按应用分组填写，格式见 docs/connection-drivers.md。")
            if args.list:
                print("模式：gsql（直连）\n")
                print(_render_menu(conns))
                print("\n用 `--app <应用> --conn <连接名>` 登录。")
                return 0
            conn = _pick(conns, args.app, args.conn)
            if conn is None:
                print("模式：gsql（直连）。请选择要登录的数据库：\n")
                print(_render_menu(conns))
                print("\n然后：`gaussdb-login --app <应用> --conn <连接名>`")
                if args.app or args.conn:
                    print("\n（--app %r --conn %r 没有唯一命中）"
                          % (args.app, args.conn))
                return 2

        note = "已跳过（--no-verify）"
        if not args.no_verify:
            ok, note = _verify(conn)
            if not ok:
                return _fail(
                    "连接 %s 验证失败，**未建立会话**：\n%s\n\n"
                    "不把失败的连接记进会话是有意的 —— 记了的话，下一个 skill "
                    "取数时才失败，那时错误看起来像是那个 skill 坏了。"
                    % (conn.qualified, note))

        path = session.save(conn)
        print(_describe(conn, note, path))
        return 0

    except ConfigError as exc:
        return _fail("配置有问题：%s" % exc)
    except KeyboardInterrupt:
        return _fail("已取消。")


if __name__ == "__main__":
    raise SystemExit(main())
