"""kill 语句的生成 —— **只生成，不执行**。

三条规矩：
  1. 只对根 holder 生成 —— 杀中间节点不解堵
  2. 按 holder 状态选函数 —— active 用 cancel（保住会话），
     idle in transaction 用 terminate（cancel 对它无效）
  3. 每条旁边注明会杀掉谁 —— 让人能自己判断代价，而不是照抄

实测结论（Step 1，og5，openGauss-lite 5.0.3，enable_thread_pool=on）：
  pg_cancel_backend(pid)               pronargs=1  —— 官方文档只有单参数形式
  pg_terminate_backend(pid)            pronargs=1  —— 同样存在，但……
  pg_terminate_session(pid, sessionid) pronargs=2  —— **也存在**，官方文档确认
                                        签名与参数顺序：pg_terminate_session(pid int64, sessionid int64)

  两者都在，不是「二选一」的替代关系。选 pg_terminate_session 而不是单参数
  pg_terminate_backend 的理由：pairs.yaml 已经测出 pid 在本环境是线程号，
  是会被复用的易变量，sessionid 才是稳定标识；线程池开启时，诊断报告生成
  到人工执行之间有时间差，pid 有被复用给另一个会话的风险。
  pg_terminate_session 同时校验 pid 和 sessionid，能防住这个场景——
  单参数版本无法防。

  cancel 没有做同样的替换：官方文档没有 pg_cancel_session（pg_proc 里虽然
  存在，但未见文档记录参数顺序，不敢在没有确认语义的情况下拿来生成 DBA
  要执行的语句），所以 active 分支保留单参数 pg_cancel_backend(pid)。
"""
import pathlib
import sys

_ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT))
sys.path.insert(0, str(_ROOT / "skills" / "gaussdb-lockwait" / "scripts"))

from recovery import kill_for, render_kills  # noqa: E402


def _holder(**kw):
    base = dict(holder_pid=281440306779808, holder_sessionid=2259,
                holder_state="active", holder_user="gaussdb",
                holder_app="gsql", holder_xact_age_s=35.4,
                holder_query="UPDATE accounts SET bal = bal - 1 WHERE id = 7")
    base.update(kw)
    return base


def test_active_holder_gets_cancel_not_terminate():
    """正在跑语句的：取消语句就够了，保住会话 —— 代价小得多。"""
    k = kill_for(_holder(holder_state="active"))
    assert k.function == "pg_cancel_backend"
    assert "pg_cancel_backend(281440306779808)" in k.sql


def test_idle_in_transaction_holder_needs_terminate():
    """**cancel 对它无效** —— 它没在跑语句，只是攥着锁不放事务。

    用 pg_terminate_session(pid, sessionid)，不是单参数 pg_terminate_backend：
    Step 1 测出 openGauss 上两个函数都存在，选两参数版本是因为它同时校验
    pid 与 sessionid —— 本环境 pid 是线程号、会被复用，单校验 pid 有杀错
    会话的风险（见 pairs.yaml 的测量记录）。
    """
    k = kill_for(_holder(holder_state="idle in transaction"))
    assert k.function == "pg_terminate_session"
    assert "pg_terminate_session(281440306779808, 2259)" in k.sql


def test_idle_in_transaction_aborted_also_needs_terminate():
    k = kill_for(_holder(holder_state="idle in transaction (aborted)"))
    assert k.function == "pg_terminate_session"


def test_unknown_state_falls_back_to_terminate():
    """状态取不到时选更强的那个 —— 选错成 cancel 的话操作看似成功、
    锁还在，人会以为处理过了。选 terminate 至少确实解堵。"""
    k = kill_for(_holder(holder_state=""))
    assert k.function == "pg_terminate_session"


def test_cancel_statement_uses_pid_only_not_sessionid():
    """cancel 分支：官方文档只有单参数 pg_cancel_backend(pid)，
    openGauss 的 pid 是线程号；语句本身不该带上 sessionid。"""
    k = kill_for(_holder(holder_pid=123, holder_sessionid=999, holder_state="active"))
    assert "(123)" in k.sql
    assert "999" not in k.sql.split("--")[0], "语句本身不该出现 sessionid"


def test_terminate_statement_uses_both_pid_and_sessionid():
    """terminate 分支：pg_terminate_session 需要 (pid, sessionid) 两个参数，
    顺序为 pid 在前、sessionid 在后（官方文档确认的签名顺序）。"""
    k = kill_for(_holder(holder_pid=123, holder_sessionid=999,
                          holder_state="idle in transaction"))
    assert "pg_terminate_session(123, 999)" in k.sql


def test_impact_names_who_gets_killed():
    """照抄之前得看得见代价。"""
    k = kill_for(_holder())
    for token in ("gaussdb", "gsql", "35.4", "2259"):
        assert token in k.impact, "impact 里缺 %s" % token


def test_impact_includes_the_running_sql():
    k = kill_for(_holder())
    assert "UPDATE accounts" in k.impact


def test_render_says_do_not_execute():
    out = render_kills([kill_for(_holder())])
    assert "不要直接执行" in out or "不得执行" in out


def test_render_of_nothing_is_explicit():
    """**没有可生成的语句要明说**，不能返回空串 —— 空白会被读成「这段没生成」。"""
    out = render_kills([])
    assert out.strip(), "空结果必须有明确文字"
    assert "无" in out
