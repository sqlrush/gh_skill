"""连库:自带连接参数,不读 skill 的 config.yaml。

**为什么不复用 common/db.Database:** faultlab 是用来**测这些 skill** 的。如果它也从
`$GSDB_HOME/config.yaml` 取连接,那么「skill 的配置被改成 api 模式」这种再正常不过的事
就会让造故障这一步先失败,人会以为是故障没造出来。所以这里只认自己的 `~/.faultlab/conn.json`,
口令随机生成、0600、永不进仓库、永不打印。

**管理动作走容器:** 建 / 删专用用户要 omm 权限,经 `docker exec … su - omm -c gsql` 完成;
造故障与观测走 psycopg2(要跨语句保持事务,gsql 每条语句一个子进程做不到)。
"""
from __future__ import annotations

import json
import secrets
import string
import subprocess
from typing import Any, Dict, List, Optional, Sequence, Tuple

from . import config as C


class LabError(RuntimeError):
    """faultlab 自己的错误:环境不对、指纹不符、残留清不掉。"""


# psycopg2 **只在真要连库时才需要**。
#
# 这里曾经在 import 失败时直接 SystemExit —— 结果本工具的单元测试在任何没装 psycopg2 的
# 机器上都跑不起来(连 pytest 自己都被 SystemExit 打断),而那些单测测的全是纯函数:
# 排期预算、规模校验、残留登记簿。驱动装没装和它们毫无关系。
# 所以改成:import 不成功就记下原因,等到真的要建连接时再报,报的还是同一句人话。
try:
    import psycopg2
except ImportError as _exc:     # pragma: no cover - 环境问题,不是代码问题
    psycopg2 = None
    _PSYCOPG2_ERROR = str(_exc)
else:
    _PSYCOPG2_ERROR = ""


def require_driver() -> None:
    """真要连库之前调一次。"""
    if psycopg2 is None:
        raise LabError("faultlab 连库需要 psycopg2(%s):请用装了它的解释器跑,"
                       "例如 ~/p2venv/bin/python" % _PSYCOPG2_ERROR)


# openGauss 开了线程池(og5 上 enable_thread_pool=on)时,**pg_stat_activity 里还会列出
# 空闲的工作线程**:这类条目 sessionid = 0、client_port 与 backend_start 都是空,
# application_name 还留着它上一次服务过的那个会话的名字。
#
# 2026-10-17 实测踩到的坑:清理完所有连接(lsof 看 5433 上一条 TCP 都没有)之后,
# `pg_stat_activity WHERE application_name LIKE 'fl:%'` 仍然返回 10 行,
# 而 `pg_terminate_backend` 对它们返回 t —— 看起来像「杀不掉的残留会话」。
# 它们**不持任何锁**(pg_locks 里一条都没有),只是线程池的槽位。
#
# 所以凡是数会话、杀会话,都要带上这个条件;数「真会话」只认 sessionid <> 0。
REAL_SESSION = "sessionid <> 0"


# ---------------------------------------------------------------- 连接参数

def conn_params() -> Dict[str, Any]:
    """连接参数:默认值 + ~/.faultlab/conn.json 覆盖。口令单独放,不在这里。"""
    out = dict(C.DEFAULT_CONN)
    if C.CONN_FILE.exists():
        try:
            out.update(json.loads(C.CONN_FILE.read_text(encoding="utf-8")))
        except (OSError, ValueError) as exc:
            raise LabError("%s 读不出来:%s" % (C.CONN_FILE, exc)) from exc
    for key in ("host", "port", "dbname", "container"):
        if not str(out.get(key) or "").strip():
            raise LabError("连接参数缺 %s(改 %s)" % (key, C.CONN_FILE))
    return out


def _password() -> str:
    """专用用户的口令。没有就报错 —— 由 bootstrap 负责生成,不在取用时偷偷造一个。"""
    p = C.STATE_DIR / "fl_app.pw"
    if not p.exists():
        raise LabError("还没有 %s;先跑 `faultlab.py prepare` 建专用用户" % p)
    pw = p.read_text(encoding="utf-8").strip()
    if not pw:
        raise LabError("%s 是空的,删掉它再跑一次 prepare" % p)
    return pw


def new_password() -> str:
    """生成一个满足 openGauss 口令策略的随机口令(8 位以上、四类字符里占三类)。"""
    pool = string.ascii_letters + string.digits
    body = "".join(secrets.choice(pool) for _ in range(14))
    return "Fl" + body + secrets.choice("#%*+-_") + secrets.choice(string.digits)


def save_password(pw: str) -> None:
    C.STATE_DIR.mkdir(parents=True, exist_ok=True)
    C.STATE_DIR.chmod(0o700)
    p = C.STATE_DIR / "fl_app.pw"
    p.write_text(pw, encoding="utf-8")
    p.chmod(0o600)


# ---------------------------------------------------------------- 经容器执行

def omm_sql(sql: str, timeout_s: int = 60) -> str:
    """以 omm 身份在容器里跑一段 SQL(建用户、删用户这类管理动作)。

    返回 stdout+stderr 原文 —— 调用方要能看到 NOTICE 与报错原文,不然
    「用户已存在」和「口令策略不过」看起来一样。
    """
    params = conn_params()
    cmd = ["docker", "exec", "-i", str(params["container"]),
           "su", "-", "omm", "-c", "gsql -d %s -At -q" % params["dbname"]]
    try:
        p = subprocess.run(cmd, input=sql, capture_output=True, text=True, timeout=timeout_s)
    except FileNotFoundError as exc:
        raise LabError("找不到 docker 命令:%s" % exc) from exc
    except subprocess.TimeoutExpired as exc:
        raise LabError("容器里执行 SQL 超过 %d 秒没返回:%s" % (timeout_s, exc)) from exc
    return (p.stdout or "") + (p.stderr or "")


# ---------------------------------------------------------------- psycopg2

class Conn:
    """一条 psycopg2 连接 + faultlab 要的几个便利方法。

    **故意不做连接池。** 每个场景的会话必须是**独立的后端**(锁、事务、等待状态都是
    按后端算的),池化会把两个场景的事务搅在一条连接上,造出来的现象就不是设计的那个。
    """

    def __init__(self, app: str, autocommit: bool = True, guards: bool = True):
        require_driver()
        params = conn_params()
        self.app = app
        self._raw = psycopg2.connect(
            host=params["host"], port=int(params["port"]), dbname=params["dbname"],
            user=C.LAB_USER, password=_password(), connect_timeout=10,
            application_name=C.APP_PREFIX + app,
        )
        self._raw.autocommit = autocommit
        if guards:
            self._apply_guards()

    def _apply_guards(self) -> None:
        """会话级兜底超时。

        **这是编排器之外的第二道保险:** 编排器被 Ctrl-C / 被 kill / Mac 睡过去,
        这些会话也会自己到期退出,不会把 og5 永久堵住。实例级参数一个都不改 ——
        og5 上还跑着别人的测试。
        """
        with self._raw.cursor() as cur:
            cur.execute("SET statement_timeout = '%ds'" % C.STATEMENT_TIMEOUT_S)
            cur.execute("SET idle_in_transaction_session_timeout = '%ds'" % C.IDLE_XACT_TIMEOUT_S)
            cur.execute("SET lockwait_timeout = '%ds'" % C.STATEMENT_TIMEOUT_S)
            cur.execute("SET search_path = %s, public" % C.SCHEMA)

    # -- 执行 --------------------------------------------------------------

    def exec(self, sql: str, args: Optional[Sequence] = None) -> None:
        with self._raw.cursor() as cur:
            cur.execute(sql, args)

    def query(self, sql: str, args: Optional[Sequence] = None) -> List[Tuple]:
        with self._raw.cursor() as cur:
            cur.execute(sql, args)
            return cur.fetchall() if cur.description else []

    def one(self, sql: str, args: Optional[Sequence] = None) -> Any:
        rows = self.query(sql, args)
        return rows[0][0] if rows and rows[0] else None

    def session_id(self) -> int:
        return int(self.one("SELECT sessionid FROM pg_stat_activity WHERE pid = pg_backend_pid()") or 0)

    def set(self, name: str, value: str) -> None:
        """会话级参数。只允许标识符形式的名字 —— 这里拼的是 SQL,不能接任意串。"""
        if not name.replace("_", "").isalnum():
            raise LabError("参数名不合法:%r" % name)
        self.exec("SET %s = %%s" % name, (value,))

    def rollback(self) -> None:
        try:
            self._raw.rollback()
        except Exception:       # noqa: BLE001 —— 清理路径,连接可能已经没了
            pass

    def close(self) -> None:
        try:
            self._raw.close()
        except Exception:       # noqa: BLE001
            pass

    @property
    def raw(self):
        return self._raw


# ---------------------------------------------------------------- 环境核对

def fingerprint(c: Conn) -> Dict[str, Any]:
    """这到底是哪个库。prepare / run 前核对,对不上就不动手。"""
    return {
        "version": str(c.one("SELECT version()") or ""),
        "port": int(c.one("SELECT setting::int FROM pg_settings WHERE name='port'") or 0),
        "dbname": str(c.one("SELECT current_database()") or ""),
        "data_dir": str(c.one("SELECT setting FROM pg_settings WHERE name='data_directory'") or ""),
    }


def assert_expected_instance(c: Conn) -> Dict[str, Any]:
    """核对实例指纹。

    **为什么要有这一关:** 这套脚本会关表级 autovacuum、建复制槽、做 VACUUM FULL、
    留孤儿两阶段事务。跑在客户库或别人的测试库上就是事故,而连错库在命令行上
    看不出任何异样 —— 所以宁可多一道核对。
    """
    fp = fingerprint(c)
    if fp["port"] != C.EXPECT_FINGERPRINT["port"]:
        raise LabError("实例端口是 %s,期望 %s —— 可能连错库了,不动手。"
                       % (fp["port"], C.EXPECT_FINGERPRINT["port"]))
    if C.EXPECT_FINGERPRINT["version_like"] not in fp["version"]:
        raise LabError("version() 里没有 %r:%s —— 不动手。"
                       % (C.EXPECT_FINGERPRINT["version_like"], fp["version"][:80]))
    return fp
