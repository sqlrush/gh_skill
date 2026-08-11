"""快速恢复语句的生成 —— **只生成文本，绝不执行**。

按 holder 的状态选函数，理由是这两个函数解决的不是同一件事：

  active                  正在跑语句 → pg_cancel_backend 取消这条语句，
                          会话还在、事务还在，代价最小
  idle in transaction     没在跑语句，只是攥着锁不放事务 →
                          **cancel 对它无效**（没有语句可取消），
                          只能 pg_terminate_session 断掉会话

状态取不到时选 terminate：选错成 cancel 的后果是「命令成功了但锁还在」，
而操作的人会以为已经处理过 —— 那比多断一个会话糟得多。

实测结论（Step 1，og5 / openGauss-lite 5.0.3，enable_thread_pool=on，
查 pg_proc 得出，并核对了官方文档 SQLReference/server-signal-functions）：

  pg_cancel_backend(pid)               pronargs=1
  pg_terminate_backend(pid)            pronargs=1
  pg_terminate_session(pid, sessionid) pronargs=2  —— 官方文档确认签名为
                                        pg_terminate_session(pid int64, sessionid int64)

  三个函数都存在，不是「二选一」。terminate 分支选两参数的
  pg_terminate_session 而不是单参数 pg_terminate_backend：
  scripts/registry/lockwait/pairs.yaml 的表头注释已经测出，本环境里
  pid 是线程号 —— 线程池开启时会被复用；诊断到人工执行之间有时间差，
  单靠 pid 有杀错会话的风险。pg_terminate_session 同时校验 pid 与
  sessionid，能防住这个场景，单参数版本防不住。

  cancel 分支保留单参数 pg_cancel_backend(pid)：pg_proc 里虽然也有一个
  两参数的 pg_cancel_session，但官方文档没有记录它、参数顺序无法确认，
  不能在没把握语义的情况下用它生成要交给 DBA 执行的语句。
"""
from __future__ import annotations

from dataclasses import dataclass

# 这些状态下 cancel 无效，必须 terminate
_NEEDS_TERMINATE_PREFIX = "idle in transaction"


@dataclass(frozen=True)
class KillStatement:
    sql: str
    target_sessionid: int
    target_pid: int
    function: str
    why: str
    impact: str


def kill_for(holder: dict) -> KillStatement:
    """给一个根 holder 生成恢复语句。holder 是 lockwait 的 pairs 查询返回的一行字典。"""
    state = str(holder.get("holder_state") or "").strip().lower()
    pid = int(holder.get("holder_pid") or 0)
    sid = int(holder.get("holder_sessionid") or 0)

    if state == "active":
        fn = "pg_cancel_backend"
        sql = "SELECT %s(%d);" % (fn, pid)
        why = "holder 正在执行语句，取消该语句即可解堵，会话与事务保留"
    else:
        fn = "pg_terminate_session"
        sql = "SELECT %s(%d, %d);" % (fn, pid, sid)
        if state.startswith(_NEEDS_TERMINATE_PREFIX):
            why = ("holder 处于 %s —— 没有正在执行的语句，"
                   "pg_cancel_backend 对它无效，只能断开会话；"
                   "用 pg_terminate_session(pid, sessionid) 而非单参数版本，"
                   "是因为本环境 pid 是线程号、会被复用，加 sessionid 校验"
                   "能防止杀错会话" % state)
        else:
            why = ("holder 状态为 %r，无法确认 cancel 是否有效；"
                   "选用更强的 terminate —— 取消失败而锁仍在，"
                   "会让人误以为已经处理过；用 pg_terminate_session(pid, sessionid) "
                   "而非单参数版本，是为了防止 pid（本环境是线程号，会被复用）"
                   "在诊断与执行之间被复用给别的会话"
                   % (holder.get("holder_state") or ""))

    impact = (
        "会话 %s（pid %s）/ 用户 %s / 应用 %s / 事务已持续 %s 秒；"
        "正在执行：%s"
        % (sid, pid,
           holder.get("holder_user") or "?",
           holder.get("holder_app") or "?",
           holder.get("holder_xact_age_s"),
           (holder.get("holder_query") or "").strip() or "(取不到)")
    )
    return KillStatement(
        sql=sql,
        target_sessionid=sid, target_pid=pid, function=fn,
        why=why, impact=impact,
    )


def render_kills(kills: list) -> str:
    """渲染成报告里的一段。空列表要**明说**，不能返回空串。"""
    if not kills:
        return ("## 快速恢复语句\n\n"
                "无 —— 当前没有需要处理的根阻塞会话。\n")
    out = ["## 快速恢复语句\n",
           "> **这些语句由本 skill 生成，供人工判断后自行执行；"
           "本 skill 不会执行它们，也不要直接执行。**",
           "> 只针对**根**阻塞会话生成 —— 杀链条中间的会话不解堵。\n"]
    for k in kills:
        out.append("```sql\n%s\n```" % k.sql)
        out.append("- 为什么用 `%s`：%s" % (k.function, k.why))
        out.append("- 会杀掉谁：%s\n" % k.impact)
    return "\n".join(out) + "\n"
