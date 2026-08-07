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


def _api_connection(ip: str, database: str, endpoint) -> Connection:
    """按用户给的「实例 IP + 数据库名」构造一条走中间件的连接。

    **IP 进 data_ip，库名进 database。** 接口一的报文里只有 `dataIp` 一个
    定位字段（文档写明是单 IP，mock 也拒绝逗号/空格分隔的多值），没有库名的
    位置 —— 所以中间件是按 IP 找到实例，库名不参与路由。

    库名仍然必须收：它决定后续取数落在哪个库上，也要写进报告的抬头，
    否则一份诊断报告事后分不清是同一实例上的哪个库。

    **一个待客户确认的点**：若同一实例上有多个库、而白名单按库区分，
    仅凭 dataIp 拿到的清单可能不对。协议没给库名的位置，这条要问开发。
    """
    return Connection(
        name="%s-%s" % (_safe_name(ip), _safe_name(database)),
        type="gaussdb", host=endpoint.host, port=endpoint.port,
        database=database, user="grmp", driver="grmp",
        data_ip=ip, app="api",
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
        except Exception as exc:
            # 接口一同时回答两件事：这个库在不在（中间件按 dataIp 查实例），
            # 以及它能跑哪些脚本。查不到实例时中间件自己会说
            # 「通过dataIp查询不到对应高斯实例信息」—— 原样带出来，
            # 不要包装成「连接失败」，那会让人去查网络和令牌。
            return False, ("调用中间件接口一失败：%s\n"
                           "常见原因：库名（dataIp）不被受理、令牌未设置或"
                           "过期、端点不通。" % exc)
        if not ops:
            return False, ("中间件应答正常，但这个库的白名单是空的 —— "
                           "一条脚本都没注册，任何 skill 都取不到数。"
                           "需要先由发布流程把脚本灌进 script_config。")
        return True, "中间件应答正常，白名单 %d 条脚本" % len(ops)
    return _verify_direct(conn)


def whitelist(conn: Connection) -> List[str]:
    """这个库能跑哪些脚本。api 模式下这是登录要回答的第二个问题。"""
    ops = access.runner_for(conn).client.list_operations()
    return sorted(str(d.get("cmd_name", "")) for d in ops if d.get("cmd_name"))


def _by_skill(names: List[str]) -> str:
    """按 skill 前缀分组呈现 —— 91 条平铺出来没人看得下去，
    而「哪个 skill 有几条」正好回答「这个库支持哪些诊断能力」。"""
    groups: dict = {}
    for n in names:
        groups.setdefault(n.split(".", 1)[0], []).append(n)
    return render.table(
        ["能力域", "脚本数", "脚本名"],
        [[k, str(len(v)), render.truncate("、".join(v), 88)]
         for k, v in sorted(groups.items())])


def _verify_direct(conn: Connection) -> tuple:

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
         ["中间件端点", "%s:%s" % (conn.host, conn.port)]
         if conn.driver == "grmp" else
         ["目标", "%s:%s/%s" % (conn.host, conn.port, conn.database)],
         ["实例 IP（dataIp）", conn.data_ip] if conn.driver == "grmp" else
         ["驱动", conn.driver],
         ["数据库", conn.database] if conn.driver == "grmp" else
         ["用户", conn.user],
         ["验证", note]])
    out += "\n会话已写入 `%s`。\n\n" % path

    if conn.driver == "grmp":
        # 白名单决定了这个库上**哪些 skill 真的能用** —— 客户环境里各库注册的
        # 脚本可以不一样，不摆出来的话，某个 skill 报「脚本不存在」时才发现。
        try:
            names = whitelist(conn)
            out += "## 这个库的 SQL 白名单（%d 条）\n\n" % len(names)
            out += _by_skill(names)
            out += ("\n> 只有清单里的脚本能执行。某个 skill 需要的脚本没注册时，"
                    "它会报「脚本 xxx 不存在」——那是发布问题，不是 skill 坏了。\n")
        except Exception as exc:
            out += "> 白名单取不到：%s\n" % exc

    out += ("\n后续 13 个 skill **不带 `-c` 就会用这条连接**；要临时换一个，"
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
    ap.add_argument("--ip", help="api 模式：目标实例 IP（接口一的 dataIp）")
    ap.add_argument("--database", help="api 模式：要访问的数据库名")
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
                print("api 模式没有预置的连接清单 —— 目标由你指定。\n"
                      "用 `--ip <实例IP> --database <库名>` 登录。")
                return 0
            missing = [f for f, v in (("--ip", args.ip),
                                      ("--database", args.database)) if not v]
            if missing:
                print("模式：api（GRMP 中间件）")
                print("端点：%s:%s\n" % (endpoint.host, endpoint.port))
                print("还需要：%s\n" % "、".join(missing))
                print("请提供**实例 IP** 与**数据库名**：")
                print("    gaussdb-login --ip <实例IP> --database <数据库名>\n")
                print("IP 用于中间件定位实例（接口一的 dataIp）；"
                      "库名决定取数落在哪个库、并写进报告抬头。")
                return 2
            conn = _api_connection(args.ip.strip(), args.database.strip(),
                                   endpoint)
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
