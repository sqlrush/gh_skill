"""报告存档 —— 技能跑完把 JSON 留在本人 NAS 目录,大盘从这里读。

只在设了 GSDB_REPORTS_DIR 时工作(容器里 entrypoint 设成 $NAS_ME/reports);
没设就什么都不做,非容器线的行为一字不变。

**存档失败不能拖垮技能。** 报告已经打到 stdout 了,存档只是附加动作:
失败打一行 warn 让人知道大盘会缺这一份,然后照常返回。
"""
from __future__ import annotations

import datetime as _dt
import json
import os
import pathlib
import re
import sys
from typing import Any, Dict, List, Optional

ENV_DIR = "GSDB_REPORTS_DIR"
LATEST = "latest.json"
INDEX = "index.json"
TARGETS = "targets.json"
DEFAULT_KEEP = 50
_KEY_BAD = re.compile(r"[^A-Za-z0-9._-]")


# 说明类字段:采集失败的原因、维度一句话、子技能报错、证据说明。只在这几类字段里隐去地址,
# SQL 文本、表格单元格一律不动 —— 那里的 `a/b/c`、'http://x' 是用户的数据。
_PROSE_KEYS = frozenset({"note", "headline", "error", "evidence", "reason"})
_URL = re.compile(r"(?:https?|jdbc|grpc)://[^\s（）()，,；;、\"'<>]+", re.I)
_ENDPOINT = re.compile(r"(?<![\w.])/(?:[A-Za-z0-9_-]+/){2,}[A-Za-z0-9_.-]*")


def redact(text: str) -> str:
    """红线第 5 条:中间件 / 模型服务的地址与内部接口路径是机密。大盘没有模型那道输出自检,
    存档时就隐去。只动说明文字,不动数据。"""
    return _ENDPOINT.sub("[接口已隐去]", _URL.sub("[地址已隐去]", text))


def _redact_prose(obj: Any, key: str = "") -> Any:
    if isinstance(obj, dict):
        return {k: _redact_prose(v, k) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_redact_prose(v, key) for v in obj]
    if isinstance(obj, str) and key in _PROSE_KEYS:
        return redact(obj)
    return obj


def instance_key(conn: str) -> str:
    """实例键:conn 规整成能当路径段的串。

    三个大盘(健康 / Top SQL / WDR)都是按库统计的,报告必须按实例分目录 —— 否则一个人上午
    看 A 库下午看 B 库,「最近 12 次巡检」会把两个库串成一条线。容器里 conn 形如
    api/10-0-0-9-postgres(会话句柄解析出来的,已含 dataIp 与库名),直连模式是配置里的连接名。
    """
    key = _KEY_BAD.sub("_", (conn or "").strip())
    return key or "_default"


def utc_stamp(now: Optional[str] = None) -> str:
    return now or _dt.datetime.now(_dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def reports_dir() -> Optional[pathlib.Path]:
    raw = (os.environ.get(ENV_DIR) or "").strip()
    return pathlib.Path(raw).expanduser() if raw else None


def _warn(msg: str) -> None:
    print("[warn] 报告未存档:%s" % msg, file=sys.stderr)


def _write_atomic(path: pathlib.Path, text: str) -> None:
    """同 common/kb/atomic.py:读的一方(Pod 内只读端口)任何时刻拿到的都是完整文件。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def _counts(payload: Any) -> Dict[str, int]:
    out: Dict[str, int] = {}
    findings = payload.get("findings") if isinstance(payload, dict) else None
    for f in findings or []:
        sev = str(f.get("severity")) if isinstance(f, dict) else None
        if sev is not None:
            out[sev] = out.get(sev, 0) + 1
    return out


def _dims_summary(payload: Any) -> Dict[str, Any]:
    """index 里记下这份报告有几个维度、几个不可用。

    健康检查全部采集失败时 overall 是 0(没有发现),历史条若只看 overall 就画成绿色「正常」——
    用户会以为库是健康的。有了这两个数,大盘能把「没采到」和「正常」分开画。
    """
    dims = payload.get("dims") if isinstance(payload, dict) else None
    if not isinstance(dims, list):
        return {}
    na = sum(1 for x in dims if isinstance(x, dict) and x.get("available") is False)
    return {"dims": {"total": len(dims), "na": na}}


def _load_index(path: pathlib.Path) -> List[Dict[str, Any]]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except (OSError, ValueError):
        return []


def describe_instance(conn: str) -> Dict[str, str]:
    """侧栏显示的实例名与深挖首句里的登录说法。

    conn 形如 api/10-0-0-9-postgres,用户认不出来;深挖开的新会话还没登录,首句不说是哪个库,
    模型只能反过来问。红线第 5 条允许出现用户自己连接的实例 IP 与库名。

    解析不到、或解析出来的不是这个 conn(比如会话已经切到别的库),就退回 conn 本身、不给登录说法 ——
    宁可让模型去问,也不能把 A 库的名字挂到 B 库的报告上。
    """
    try:
        from common import config
    except ImportError:
        return {"label": conn, "login": ""}
    c = None
    for cand in (None, conn):
        try:
            got = config.resolve(cand)
        except Exception:        # 没登录、没配置、名字不认识 —— 都退回
            continue
        if got.qualified == conn:
            c = got
            break
    if c is None:
        return {"label": conn, "login": ""}
    if c.data_ip:
        return {"label": "%s / %s" % (c.data_ip, c.database),
                "login": "登录 %s 的 %s 库" % (c.data_ip, c.database)}
    return {"label": "%s / %s" % (c.qualified, c.database), "login": "用连接 %s 登录" % c.qualified}


def _update_targets(skill_dir: pathlib.Path, key: str, conn: str, stamp: str, count: int) -> None:
    """<skill>/targets.json:这个人跑过哪些实例、各自最近一次是什么时候 —— 大盘靠它切实例。"""
    path = skill_dir / TARGETS
    items = [t for t in _load_index(path) if t.get("key") != key]
    items.append({"key": key, "conn": conn, "last_at": stamp, "count": count, **describe_instance(conn)})
    items.sort(key=lambda t: t.get("last_at", ""), reverse=True)
    _write_atomic(path, json.dumps(items, ensure_ascii=False, indent=2))


def archive(skill: str, payload: Any, *, name: Optional[str] = None,
            keep: int = DEFAULT_KEEP, now: Optional[str] = None,
            instance: Optional[str] = None) -> Optional[pathlib.Path]:
    """写 <dir>/<skill>/[<实例键>/]<name or 时间戳>.json,同时覆盖 latest.json、维护 index.json。

    instance 给了就分实例目录并维护 <skill>/targets.json;不给(知识库,全体共享一份)则落在技能根目录。
    index 只登记本函数写过的文件,超过 keep 份时也只删登记过的 —— 目录里别的东西不动。
    """
    base = reports_dir()
    if base is None:
        return None
    stamp = utc_stamp(now)
    fname = (name or stamp) + ".json"
    d = base / skill
    key = instance_key(instance) if instance is not None else ""
    if key:
        d = d / key
    payload = _redact_prose(payload)
    try:
        text = json.dumps(payload, ensure_ascii=False, indent=2)
        _write_atomic(d / fname, text)
        _write_atomic(d / LATEST, text)
        idx = [e for e in _load_index(d / INDEX) if e.get("file") != fname]
        idx.append({"at": stamp, "file": fname,
                    "overall": payload.get("overall") if isinstance(payload, dict) else None,
                    "counts": _counts(payload), **_dims_summary(payload)})
        idx.sort(key=lambda e: e.get("at", ""))
        for old in idx[:-keep] if keep > 0 else []:
            try:
                (d / str(old.get("file"))).unlink()      # 只删 index 里登记过的
            except OSError:
                pass
        idx = idx[-keep:] if keep > 0 else idx
        _write_atomic(d / INDEX, json.dumps(idx, ensure_ascii=False, indent=2))
        if key:
            _update_targets(base / skill, key, instance or "", stamp, len(idx))
        return d / fname
    except (OSError, TypeError, ValueError) as exc:
        _warn("%s/%s:%s" % (skill, fname, exc))
        return None


def append_jsonl(skill: str, name: str, row: Dict[str, Any]) -> bool:
    base = reports_dir()
    if base is None:
        return False
    path = base / skill / (name + ".jsonl")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")
        return True
    except (OSError, TypeError, ValueError) as exc:
        _warn("%s/%s.jsonl:%s" % (skill, name, exc))
        return False
