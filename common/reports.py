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
import sys
from typing import Any, Dict, List, Optional

ENV_DIR = "GSDB_REPORTS_DIR"
LATEST = "latest.json"
INDEX = "index.json"
DEFAULT_KEEP = 50


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


def _load_index(path: pathlib.Path) -> List[Dict[str, Any]]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except (OSError, ValueError):
        return []


def archive(skill: str, payload: Any, *, name: Optional[str] = None,
            keep: int = DEFAULT_KEEP, now: Optional[str] = None) -> Optional[pathlib.Path]:
    """写 <dir>/<skill>/<name or 时间戳>.json,同时覆盖 latest.json、维护 index.json。

    index 只登记本函数写过的文件,超过 keep 份时也只删登记过的 —— 目录里别的东西不动。
    """
    base = reports_dir()
    if base is None:
        return None
    stamp = utc_stamp(now)
    fname = (name or stamp) + ".json"
    d = base / skill
    try:
        text = json.dumps(payload, ensure_ascii=False, indent=2)
        _write_atomic(d / fname, text)
        _write_atomic(d / LATEST, text)
        idx = [e for e in _load_index(d / INDEX) if e.get("file") != fname]
        idx.append({"at": stamp, "file": fname,
                    "overall": payload.get("overall") if isinstance(payload, dict) else None,
                    "counts": _counts(payload)})
        idx.sort(key=lambda e: e.get("at", ""))
        for old in idx[:-keep] if keep > 0 else []:
            try:
                (d / str(old.get("file"))).unlink()      # 只删 index 里登记过的
            except OSError:
                pass
        idx = idx[-keep:] if keep > 0 else idx
        _write_atomic(d / INDEX, json.dumps(idx, ensure_ascii=False, indent=2))
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
