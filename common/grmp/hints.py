"""把数据库 / 中间件的原始报错翻成一句现场 DBA 看得懂的话。

只做「已知模式 → 提示」的**追加**:原文一字不动、不吞,提示跟在后面。
原因:现场把「HTTP Error 400:」后面一片空白追成了参数名问题,两天没碰到真因
(备机读不了 unlogged 表)。原文是证据,提示是方向,两样都要在。

两条访问路径共用:中间件路径(client.py)与直连路径(runner.py)。
"""
from __future__ import annotations

from typing import Sequence, Tuple

# (任一子串命中(不分大小写) → 提示)。顺序即优先级,只取第一条命中的。
_HINTS: Sequence[Tuple[Tuple[str, ...], str]] = (
    (("cannot be accessed on the standby",),
     "当前连接的是备机:dbe_perf.statement_history 这类 unlogged 表在备机上读不到。"
     "用主库 IP 重新 gaussdb-login;或接受降级——取 SQL 文本时会退到 dbe_perf.statement"
     "(归一化文本,参数值是占位符,可用 --bind 补)"),
    (("enable_stmt_track", "track_stmt_stat_level", "track_stmt_parameter"),
     "实例未开启语句跟踪(enable_stmt_track / track_stmt_stat_level),statement 类视图没有数据;请 DBA 开启后再查"),
    (("permission denied",),
     "执行账号没有该对象的权限:请 DBA 给执行账号授 dbe_perf 相关视图的查询权限"),
    (("canceling statement due to statement timeout", "statement timeout", "query timeout"),
     "语句超时被取消:缩小时间窗或提高阈值让结果变少;中间件侧的超时由 GRMP 配置决定"),
    (("查询不到对应高斯实例", "instance not found"),
     "中间件按 dataIp 找不到实例:登录用的 IP 必须是 GRMP 里登记的实例 IP,不是主机管理 IP"),
    (("does not exist",),
     "对象不存在:多半是版本差异(视图 / 列名不同)或脚本注册到了别的库——对照 whitelist.md 里的 SQL 与目标实例版本"),
)


def explain(text: str) -> str:
    """已知报错模式对应的中文提示;认不出来返回空串,由调用方决定要不要追加。"""
    low = (text or "").lower()
    for needles, hint in _HINTS:
        if any(n.lower() in low for n in needles):
            return hint
    return ""


def with_hint(message: str) -> str:
    """原文 + 换行 + 「提示:…」;认不出来就原样返回。"""
    hint = explain(message)
    return f"{message}\n提示:{hint}" if hint else message
