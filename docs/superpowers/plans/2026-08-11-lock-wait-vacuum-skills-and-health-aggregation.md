# 三个诊断 skill 与 health 汇总层 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 新增 `gaussdb-lockwait` / `gaussdb-waitevent` / `gaussdb-vacuum` 三个诊断 skill，并把 `gaussdb-health` 改造成汇总其他 skill 风险的层。

**Architecture:** 每个 skill 沿用项目既有分层——注册脚本（`scripts/registry/<组>/*.yaml`）负责 SQL，`access.for_conn()` 负责选路（中间件/直连对 skill 透明），判定层是**纯函数、无 I/O**，渲染层出 markdown 或 json。health 通过**子进程**调各 skill 的 `--format json` 收集 `Finding`，不同进程 import（`render.py` 在 14 个 skill 里出现 13 次，同进程会撞名且不报错）。

**Tech Stack:** Python 3.9+（mac 上是 Xcode 自带的 3.9）、pytest、PyYAML、pg8000 / gsql / GRMP 三种驱动。

## Global Constraints

- **开发在 mac 上做**：`ssh sqlrush@192.168.128.1`，仓库 `~/gh_skill/opencode_skill-main-v2-0729`。本机 Linux 容器无 `python3`。
- **中间件（白名单）模式只能执行预注册脚本。** 所有 SQL 必须落在 `scripts/registry/<组>/*.yaml`，不得在 skill 里拼 SQL。
- **config.yaml 里绝不允许出现明文口令**；口令一律加密存 `$GSDB_HOME/credentials/*.enc`。每个 SKILL.md 的「安全红线」必须**恰好出现一次**这条。
- **不打印任何口令或令牌**。
- **任何 Traceback 都是 bug**，不是错误处理。
- **空结果与查不到是两回事**：没有锁堵塞是正常状态，必须显式写「当前无锁等待」，不能留空白。
- **不执行任何 kill / VACUUM / DDL / DML**，只生成语句文本。
- SKILL.md 的结构由 `tests/test_skill_md_structure_units.py` 钉死：frontmatter 必须含 `name`（等于目录名）等字段、`\n## 安全红线` 恰好 1 次、明文口令条款恰好 1 次且含 `credential_cli`、`KB-CONTRACT:BEGIN/END` 必须配对、不得有 4 个连续换行、必须提到 `gaussdb-login`。
- 提交信息格式 `<type>(<scope>): <描述>`，不加 Co-Authored-By（全局已关）。
- **测试环境**：og5 容器（openGauss-lite 5.0.3）。`gsbench_e2e_20260801_100g.plan_data` 死元组 2009 万是验证素材，**只读不清**。
- 三种访问路径的测试连接：`-c og`（pg8000，`GSDB_HOME=~/.gdaa`）、`-c og-gsql`（gsql，`GSDB_HOME=/tmp/gsql-probe`，`PATH` 前置 `/tmp/gsql-probe/bin`）、`-c og-grmp`（中间件，`GSDB_HOME=~/.gdaa` 且 `source ~/.gdaa/grmp.env`）。

---

# Phase 0：共用地基

## Task 1: `common/finding.py`

**Files:**
- Create: `common/finding.py`
- Test: `tests/test_finding_units.py`

**Interfaces:**
- Consumes: 无
- Produces:
  - `Severity(IntEnum)`：`OK=0 NOTICE=1 WARN=2 CRITICAL=3`，方法 `label() -> str`
  - `worst(severities: list[Severity]) -> Severity`
  - `Finding` 冻结 dataclass，字段 `dimension: str, code: str, severity: Severity, metric: str, value: str, threshold: str, evidence: str, sql_id: str = "", skill: str = ""`，方法 `to_dict() -> dict`
  - `findings_to_json(findings: list[Finding], skill: str) -> str`
  - `findings_from_json(text: str) -> list[Finding]`

- [ ] **Step 1: 写失败的测试**

创建 `tests/test_finding_units.py`：

```python
"""统一 Finding —— health / wdr / memanalyze 原先各抄一份同样的 dataclass。

汇总层要靠它做跨 skill 的机器可读通道，所以 to_dict 的形状是**契约**：
health 解析子 skill 的 json 时认的就是这几个键，改键名等于改协议。
"""
import json
import pathlib
import sys

import pytest

_ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT))

from common.finding import (  # noqa: E402
    Finding, Severity, findings_from_json, findings_to_json, worst,
)


def test_severity_ordering_is_worst_last():
    assert Severity.OK < Severity.NOTICE < Severity.WARN < Severity.CRITICAL


def test_worst_of_empty_is_ok():
    assert worst([]) is Severity.OK


def test_worst_picks_highest():
    assert worst([Severity.NOTICE, Severity.CRITICAL, Severity.WARN]) is Severity.CRITICAL


def test_labels_are_stable():
    assert Severity.CRITICAL.label() == "🔴严重"
    assert Severity.WARN.label() == "🟠告警"
    assert Severity.NOTICE.label() == "🟡关注"
    assert Severity.OK.label() == "🟢健康"


def _f(**kw):
    base = dict(dimension="Locks", code="LOCK_BLOCKED", severity=Severity.WARN,
                metric="阻塞会话数", value="3", threshold=">0", evidence="sess 2260 等 2259")
    base.update(kw)
    return Finding(**base)


def test_to_dict_keys_are_the_contract():
    d = _f().to_dict()
    assert set(d) == {"dimension", "code", "severity", "metric", "value",
                      "threshold", "evidence", "sql_id", "skill"}
    assert d["severity"] == 2, "severity 序列化成 int —— 跨进程要能比大小"


def test_findings_survive_a_json_round_trip():
    """**这是跨进程契约。** health 起子进程读 stdout，形状对不上就全盘失效。"""
    src = [_f(), _f(code="LOCK_ROOT", severity=Severity.CRITICAL)]
    back = findings_from_json(findings_to_json(src, skill="gaussdb-lockwait"))
    assert [f.code for f in back] == ["LOCK_BLOCKED", "LOCK_ROOT"]
    assert back[0].severity is Severity.WARN
    assert all(f.skill == "gaussdb-lockwait" for f in back), "来源必须带上"


def test_to_json_stamps_the_skill_name():
    payload = json.loads(findings_to_json([_f()], skill="gaussdb-vacuum"))
    assert payload["skill"] == "gaussdb-vacuum"
    assert payload["findings"][0]["skill"] == "gaussdb-vacuum"


def test_from_json_rejects_a_wrong_shape():
    """形状不对要当场报错，不能返回空列表 —— 空列表会被读成「这个 skill 没风险」。"""
    with pytest.raises(ValueError):
        findings_from_json(json.dumps({"nope": 1}))


def test_finding_is_immutable():
    with pytest.raises(Exception):
        _f().code = "X"
```

- [ ] **Step 2: 跑测试确认它失败**

```bash
ssh sqlrush@192.168.128.1 "cd ~/gh_skill/opencode_skill-main-v2-0729 && python3 -m pytest tests/test_finding_units.py -q"
```

预期：`ModuleNotFoundError: No module named 'common.finding'`

- [ ] **Step 3: 写实现**

创建 `common/finding.py`：

```python
"""跨 skill 的风险表达 —— 一份，不是三份。

health / wdr / memanalyze 原先各存一份字段完全相同的 Finding。
汇总层出现之后这就不只是重复：health 要解析子 skill 的 json，
三份定义一旦哪份多加一个字段，汇总侧读到的东西就和产出侧对不上，
而 json 解析对不上的表现往往是**少一条风险**，不是报错。

severity 是脚本按阈值判定的确定性等级，**LLM 不得更改**。
"""
from __future__ import annotations

import json
from dataclasses import dataclass, replace
from enum import IntEnum
from typing import Any


class Severity(IntEnum):
    OK = 0
    NOTICE = 1
    WARN = 2
    CRITICAL = 3

    def label(self) -> str:
        return {
            Severity.CRITICAL: "🔴严重",
            Severity.WARN: "🟠告警",
            Severity.NOTICE: "🟡关注",
        }.get(self, "🟢健康")


def worst(severities: list[Severity]) -> Severity:
    """取最坏的一档；空列表是 OK。"""
    w = Severity.OK
    for s in severities:
        if s > w:
            w = s
    return w


@dataclass(frozen=True)
class Finding:
    """一条越过阈值的确定性观察。

    code 是稳定标识：报告、SKILL.md 的验收闸、汇总层都靠它交叉引用，
    改 code 等于改对外接口。
    """
    dimension: str
    code: str
    severity: Severity
    metric: str
    value: str
    threshold: str
    evidence: str
    sql_id: str = ""
    skill: str = ""

    def to_dict(self) -> dict:
        return {
            "dimension": self.dimension,
            "code": self.code,
            "severity": int(self.severity),   # 跨进程要能比大小，别给字符串
            "metric": self.metric,
            "value": self.value,
            "threshold": self.threshold,
            "evidence": self.evidence,
            "sql_id": self.sql_id,
            "skill": self.skill,
        }


_REQUIRED = ("dimension", "code", "severity", "metric", "value",
             "threshold", "evidence")


def findings_to_json(findings: list[Finding], skill: str) -> str:
    """序列化成 health 认得的形状，并盖上来源 skill 名。"""
    stamped = [replace(f, skill=skill) for f in findings]
    return json.dumps(
        {"skill": skill, "findings": [f.to_dict() for f in stamped]},
        ensure_ascii=False, indent=2)


def findings_from_json(text: str) -> list[Finding]:
    """解析子 skill 的输出。**形状不对当场抛，不返回空列表。**

    空列表会被汇总层读成「这个 skill 没查出风险」—— 那和「没解析出来」
    是两件事，而前者会让一份漏了整个维度的报告看起来一切正常。
    """
    try:
        payload: Any = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError("findings json 解析失败：%s" % exc) from exc
    if not isinstance(payload, dict) or "findings" not in payload:
        raise ValueError("findings json 缺 'findings' 键，拿到的是：%r"
                         % (list(payload)[:5] if isinstance(payload, dict) else type(payload).__name__))
    out = []
    for i, raw in enumerate(payload["findings"]):
        missing = [k for k in _REQUIRED if k not in raw]
        if missing:
            raise ValueError("第 %d 条 finding 缺字段 %s" % (i, missing))
        out.append(Finding(
            dimension=raw["dimension"], code=raw["code"],
            severity=Severity(int(raw["severity"])),
            metric=raw["metric"], value=raw["value"],
            threshold=raw["threshold"], evidence=raw["evidence"],
            sql_id=raw.get("sql_id", ""),
            skill=raw.get("skill", "") or payload.get("skill", ""),
        ))
    return out
```

- [ ] **Step 4: 跑测试确认通过**

```bash
ssh sqlrush@192.168.128.1 "cd ~/gh_skill/opencode_skill-main-v2-0729 && python3 -m pytest tests/test_finding_units.py -q"
```

预期：10 passed

- [ ] **Step 5: 提交**

```bash
ssh sqlrush@192.168.128.1 "cd ~/gh_skill/opencode_skill-main-v2-0729 && git add common/finding.py tests/test_finding_units.py && git commit -q -m 'feat(common): 抽出统一的 Finding —— 汇总层要靠它做跨进程契约'"
```

---

## Task 2: 让 health / wdr / memanalyze 用同一份 Finding

**Files:**
- Modify: `skills/gaussdb-health/scripts/model.py`
- Modify: `skills/gaussdb-wdr/scripts/model.py`
- Modify: `skills/gaussdb-memanalyze/scripts/model.py`
- Test: `tests/test_degrade_contract_units.py`（新增一条）

**Interfaces:**
- Consumes: Task 1 的 `common.finding.Finding` / `Severity` / `worst`
- Produces: 三个 `model.py` 继续对外暴露同名的 `Finding` / `Severity` / `worst`，**行为不变**

**关键约束：这一步不能改任何行为。** 三个 skill 的现有测试必须一条不动地继续绿。

- [ ] **Step 1: 记录改动前的基线**

```bash
ssh sqlrush@192.168.128.1 "cd ~/gh_skill/opencode_skill-main-v2-0729 && python3 -m pytest tests/ -q 2>&1 | tail -3"
```

把 passed 数记下来（当前应为 1357）。

- [ ] **Step 2: 写一条钉住「只有一份定义」的测试**

在 `tests/test_degrade_contract_units.py` 末尾追加：

```python
@pytest.mark.parametrize("skill", ["health", "wdr", "memanalyze"])
def test_finding_is_not_redefined_per_skill(skill):
    """三个 skill 曾各存一份字段相同的 Finding。

    汇总层出现之后这不只是重复：health 解析子 skill 的 json，三份定义
    哪份多加一个字段，汇总侧读到的就和产出侧对不上 —— 而 json 对不上的
    表现往往是**少一条风险**，不是报错。
    """
    src = (_ROOT / "skills" / ("gaussdb-" + skill) / "scripts"
           / "model.py").read_text(encoding="utf-8")
    code = "\n".join(l for l in src.splitlines() if not l.lstrip().startswith("#"))
    assert "class Finding" not in code, "%s 又自己定义了一份 Finding" % skill
    assert "from common.finding import" in code, "%s 没走共用的 Finding" % skill
```

- [ ] **Step 3: 跑它确认失败**

```bash
ssh sqlrush@192.168.128.1 "cd ~/gh_skill/opencode_skill-main-v2-0729 && python3 -m pytest tests/test_degrade_contract_units.py -q -k redefined"
```

预期：3 failed，报「又自己定义了一份 Finding」

- [ ] **Step 4: 三个 model.py 改成 import**

每个 `model.py` 里删掉 `class Severity` / `class Finding` / `def worst` 的定义，换成：

```python
# Finding 与 Severity 统一在 common/finding.py —— 本文件曾存一份完全相同的
# 定义，health / wdr / memanalyze 三家各一份。汇总层要跨进程解析这个形状，
# 三份定义迟早分叉，而分叉的表现是**少一条风险**，不是报错。
from common.finding import Finding, Severity, worst  # noqa: F401
```

**注意 wdr 的 `Finding` 多一个 `sql_id` 字段** —— Task 1 的统一版本已经包含它，无需额外处理。三个文件里其余的 dataclass（`HealthEvidence` / `Dimension` 等）**保持原样不动**。

- [ ] **Step 5: 跑全量测试，确认数目没掉**

```bash
ssh sqlrush@192.168.128.1 "cd ~/gh_skill/opencode_skill-main-v2-0729 && python3 -m pytest tests/ -q 2>&1 | tail -3"
```

预期：passed 数 = Step 1 的基线 + 3（新增的 parametrize），且 **0 failed**。任何一条原有测试变红都说明行为被改了，回退重来。

- [ ] **Step 6: 三个 skill 端到端各跑一次**

```bash
ssh sqlrush@192.168.128.1 'export GSDB_HOME=~/.gdaa; set -a; . ~/.gdaa/grmp.env; set +a
SK=~/.config/opencode/skills
cd ~/gh_skill && bash opencode_skill-main-v2-0729/install-opencode.sh --dest ~/.config/opencode/skills >/dev/null 2>&1
for s in health wdr memanalyze; do
  out=$(python3 $SK/gaussdb-$s/scripts/$s.py -c og-grmp 2>&1)
  echo "$s rc=$? traceback=$(printf "%s" "$out" | grep -c Traceback)"
done'
```

预期：三个都 `traceback=0`。

- [ ] **Step 7: 提交**

```bash
ssh sqlrush@192.168.128.1 "cd ~/gh_skill/opencode_skill-main-v2-0729 && git add -A && git commit -q -m 'refactor(health,wdr,memanalyze): Finding 归一到 common —— 三份同样的定义迟早分叉'"
```

---

# Phase 1：`gaussdb-lockwait`

## Task 3: 8 级锁互斥矩阵（实测得到，不靠记忆）

**Files:**
- Create: `common/lockmodes.py`
- Create: `tools/probe_lock_matrix.py`
- Test: `tests/test_lockmodes_units.py`

**Interfaces:**
- Consumes: 无
- Produces:
  - `LOCK_MODES: tuple[str, ...]` —— 8 个模式，由弱到强，用 `pg_locks.mode` 的实际拼写（`AccessShareLock` 这种）
  - `conflicts(holder: str, waiter: str) -> bool`
  - `conflict_reason(holder: str, waiter: str) -> str` —— 人话解释，例如「holder 的 AccessExclusiveLock 与任何模式互斥」
  - `typical_statements(mode: str) -> str` —— 哪些语句会取这个模式

- [ ] **Step 1: 写失败的测试**

创建 `tests/test_lockmodes_units.py`：

```python
"""8 级锁互斥矩阵。

矩阵本身由 tools/probe_lock_matrix.py 在真库上实撞 64 对得到，
这里钉住几条**必须成立**的性质与若干实测过的具体格子。
性质比逐格断言更能抓住整表写歪：写歪一格容易，同时满足对称性和
「AccessExclusive 与一切互斥」很难。
"""
import pathlib
import sys

import pytest

_ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT))

from common.lockmodes import (  # noqa: E402
    LOCK_MODES, conflict_reason, conflicts, typical_statements,
)


def test_eight_modes_weakest_to_strongest():
    assert LOCK_MODES == (
        "AccessShareLock", "RowShareLock", "RowExclusiveLock",
        "ShareUpdateExclusiveLock", "ShareLock", "ShareRowExclusiveLock",
        "ExclusiveLock", "AccessExclusiveLock",
    )


def test_matrix_is_symmetric():
    """互斥是对称关系。不对称说明表抄歪了。"""
    for a in LOCK_MODES:
        for b in LOCK_MODES:
            assert conflicts(a, b) == conflicts(b, a), "%s/%s 不对称" % (a, b)


def test_access_exclusive_conflicts_with_everything():
    for m in LOCK_MODES:
        assert conflicts("AccessExclusiveLock", m)


def test_access_share_only_conflicts_with_access_exclusive():
    for m in LOCK_MODES:
        assert conflicts("AccessShareLock", m) is (m == "AccessExclusiveLock")


def test_the_pair_measured_on_og5():
    """实测过的那一对：holder AccessExclusive、waiter AccessShare，被挡住了。"""
    assert conflicts("AccessExclusiveLock", "AccessShareLock")


@pytest.mark.parametrize("a,b", [
    ("RowExclusiveLock", "RowExclusiveLock"),      # 两个 INSERT 不互斥
    ("AccessShareLock", "RowExclusiveLock"),       # SELECT 与 INSERT 不互斥
    ("RowShareLock", "RowExclusiveLock"),
])
def test_common_pairs_do_not_conflict(a, b):
    assert not conflicts(a, b)


@pytest.mark.parametrize("a,b", [
    ("ShareLock", "RowExclusiveLock"),             # 建索引挡住写
    ("ShareUpdateExclusiveLock", "ShareUpdateExclusiveLock"),  # 两个 VACUUM
    ("ExclusiveLock", "RowShareLock"),
])
def test_known_conflicting_pairs(a, b):
    assert conflicts(a, b)


def test_unknown_mode_raises_not_silently_false():
    """不认识的模式必须抛。返回 False 等于说「不冲突」——
    而实际是堵着的，报告会说「没有互斥关系」，那是最糟的形态。"""
    with pytest.raises(KeyError):
        conflicts("NoSuchLock", "AccessShareLock")


def test_reason_names_both_sides():
    r = conflict_reason("AccessExclusiveLock", "AccessShareLock")
    assert "AccessExclusiveLock" in r and "AccessShareLock" in r


def test_typical_statements_cover_all_modes():
    for m in LOCK_MODES:
        assert typical_statements(m), "%s 没有对应的典型语句说明" % m
```

- [ ] **Step 2: 跑测试确认失败**

```bash
ssh sqlrush@192.168.128.1 "cd ~/gh_skill/opencode_skill-main-v2-0729 && python3 -m pytest tests/test_lockmodes_units.py -q"
```

预期：`ModuleNotFoundError: No module named 'common.lockmodes'`

- [ ] **Step 3: 写实现**

创建 `common/lockmodes.py`：

```python
"""8 级表锁的互斥矩阵。

**这张表是实测出来的，不是记出来的。** tools/probe_lock_matrix.py 在真库上
把 64 对模式逐对撞一遍（一条会话持 A、另一条请求 B，看会不会被挡），
产出的结果与本表逐格比对。换到商用 GaussDB 上重跑一遍即可知道有无差异。

拼写用 pg_locks.mode 的原样（AccessShareLock 这种），不做大小写归一 ——
归一就要在两处维护同一套别名，而报告里要显示的本来就是数据库给的那个词。
"""
from __future__ import annotations

LOCK_MODES = (
    "AccessShareLock",            # 1  SELECT
    "RowShareLock",               # 2  SELECT FOR UPDATE / FOR SHARE
    "RowExclusiveLock",           # 3  INSERT / UPDATE / DELETE
    "ShareUpdateExclusiveLock",   # 4  VACUUM(非 FULL) / ANALYZE / CREATE INDEX CONCURRENTLY
    "ShareLock",                  # 5  CREATE INDEX(非 CONCURRENTLY)
    "ShareRowExclusiveLock",      # 6  CREATE TRIGGER / 部分 ALTER TABLE
    "ExclusiveLock",              # 7  REFRESH MATERIALIZED VIEW CONCURRENTLY
    "AccessExclusiveLock",        # 8  DROP / TRUNCATE / VACUUM FULL / 多数 ALTER / LOCK TABLE 默认
)

# 每个模式与哪些模式互斥。按「由弱到强」的下标写，读起来就是标准的三角矩阵。
# 下标从 0 起，与 LOCK_MODES 对应。
_CONFLICTS = {
    0: {7},
    1: {6, 7},
    2: {4, 5, 6, 7},
    3: {3, 4, 5, 6, 7},
    4: {2, 3, 5, 6, 7},
    5: {2, 3, 4, 5, 6, 7},
    6: {1, 2, 3, 4, 5, 6, 7},
    7: {0, 1, 2, 3, 4, 5, 6, 7},
}

_INDEX = {m: i for i, m in enumerate(LOCK_MODES)}

_TYPICAL = {
    "AccessShareLock": "SELECT",
    "RowShareLock": "SELECT ... FOR UPDATE / FOR SHARE",
    "RowExclusiveLock": "INSERT / UPDATE / DELETE",
    "ShareUpdateExclusiveLock": "VACUUM（非 FULL）、ANALYZE、CREATE INDEX CONCURRENTLY",
    "ShareLock": "CREATE INDEX（非 CONCURRENTLY）",
    "ShareRowExclusiveLock": "CREATE TRIGGER、部分 ALTER TABLE",
    "ExclusiveLock": "REFRESH MATERIALIZED VIEW CONCURRENTLY",
    "AccessExclusiveLock": "DROP / TRUNCATE / VACUUM FULL / 多数 ALTER TABLE / LOCK TABLE（默认模式）",
}


def _idx(mode: str) -> int:
    """不认识的模式**抛**，不返回默认值。

    返回 False（不冲突）会让报告说「这两个模式没有互斥关系」，而现场是
    实实在在堵着的 —— 结论与事实相反，且看不出哪里错了。
    """
    try:
        return _INDEX[mode]
    except KeyError:
        raise KeyError(
            "未知锁模式 %r。已知的 8 个：%s。"
            "若数据库给出了新模式，先补进 LOCK_MODES 并用 "
            "tools/probe_lock_matrix.py 实测它与其余模式的互斥关系。"
            % (mode, ", ".join(LOCK_MODES))) from None


def conflicts(holder: str, waiter: str) -> bool:
    """holder 持有 holder 模式时，waiter 请求 waiter 模式会不会被挡。"""
    return _idx(waiter) in _CONFLICTS[_idx(holder)]


def conflict_reason(holder: str, waiter: str) -> str:
    """给报告用的一句人话。"""
    if not conflicts(holder, waiter):
        return "%s 与 %s 不互斥（本次阻塞另有原因，检查是否为行级锁）" % (holder, waiter)
    if holder == "AccessExclusiveLock":
        return ("holder 持有 %s —— 它与全部 8 种模式互斥，任何访问都会被挡；"
                "常见于 DROP / TRUNCATE / VACUUM FULL / ALTER TABLE"
                % holder)
    return ("holder 持有 %s，waiter 请求 %s，两者在 8 级锁矩阵中互斥"
            % (holder, waiter))


def typical_statements(mode: str) -> str:
    """哪些语句会取这个模式。报告里用来解释「它为什么会持有这把锁」。"""
    _idx(mode)   # 未知模式照样抛
    return _TYPICAL[mode]
```

- [ ] **Step 4: 跑测试确认通过**

```bash
ssh sqlrush@192.168.128.1 "cd ~/gh_skill/opencode_skill-main-v2-0729 && python3 -m pytest tests/test_lockmodes_units.py -q"
```

预期：全部 passed

- [ ] **Step 5: 写实测工具**

创建 `tools/probe_lock_matrix.py`：

```python
#!/usr/bin/env python3
"""在真库上把 8x8 锁互斥矩阵撞出来，与 common/lockmodes.py 的表逐格比对。

用法（mac 上）：
    GSDB_HOME=~/.gdaa python3 tools/probe_lock_matrix.py -c og

一条会话持 A 模式，另一条请求 B 模式；能在超时内拿到就是不互斥，
被挡住就是互斥。需要持久会话，所以只能用 driver: pg8000 的连接。

**这是矩阵的事实来源。** common/lockmodes.py 里的表若与本工具的结果不一致，
以本工具为准 —— 表是人写的，撞出来的是数据库说的。
"""
from __future__ import annotations

import argparse
import os
import pathlib
import sys
import threading
import time

_ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT))

from common.db import Database                       # noqa: E402
from common.lockmodes import LOCK_MODES, conflicts   # noqa: E402

TBL = "zz_lock_matrix_probe"
ACQUIRE_TIMEOUT_S = 2.0     # 超过这个时间没拿到，判定为被挡住


def _lock_sql(mode: str) -> str:
    """把 pg_locks 的拼写换成 LOCK TABLE 的语法（AccessShareLock → ACCESS SHARE）。"""
    body = mode[:-4] if mode.endswith("Lock") else mode      # 去掉结尾的 Lock
    out = []
    for ch in body:
        if ch.isupper() and out:
            out.append(" ")
        out.append(ch.upper())
    return "".join(out)


def measure(conn: str, holder_mode: str, waiter_mode: str) -> bool:
    """返回 True 表示实测互斥（waiter 在超时内没拿到锁）。"""
    got = threading.Event()
    stop = threading.Event()

    def holder():
        db = Database.connect(conn, read_only=False)
        try:
            db.execute("BEGIN")
            db.execute("LOCK TABLE %s IN %s MODE" % (TBL, _lock_sql(holder_mode)))
            while not stop.is_set():
                time.sleep(0.05)
            db.execute("ROLLBACK")
        finally:
            db.close()

    def waiter():
        db = Database.connect(conn, read_only=False)
        try:
            db.execute("BEGIN")
            db.execute("LOCK TABLE %s IN %s MODE" % (TBL, _lock_sql(waiter_mode)))
            got.set()
            db.execute("ROLLBACK")
        except Exception:
            pass
        finally:
            db.close()

    th = threading.Thread(target=holder, daemon=True)
    th.start()
    time.sleep(0.4)                       # 让 holder 先拿到
    tw = threading.Thread(target=waiter, daemon=True)
    tw.start()
    blocked = not got.wait(ACQUIRE_TIMEOUT_S)
    stop.set()
    th.join(timeout=10)
    tw.join(timeout=10)
    return blocked


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("-c", "--conn", default="og",
                    help="连接名（必须是 driver: pg8000 —— 需要持久会话）")
    args = ap.parse_args()

    admin = Database.connect(args.conn, read_only=False)
    admin.execute("DROP TABLE IF EXISTS %s" % TBL)
    admin.execute("CREATE TABLE %s (i int)" % TBL)
    mismatches = []
    try:
        print("%-26s %s" % ("holder \\ waiter", " ".join(
            "%2d" % (i + 1) for i in range(len(LOCK_MODES)))))
        for h in LOCK_MODES:
            row = []
            for w in LOCK_MODES:
                actual = measure(args.conn, h, w)
                expected = conflicts(h, w)
                row.append("X" if actual else ".")
                if actual != expected:
                    mismatches.append((h, w, expected, actual))
            print("%-26s %s" % (h, "  ".join(row)))
    finally:
        admin.execute("DROP TABLE IF EXISTS %s" % TBL)
        admin.close()

    if mismatches:
        print("\n!!! 与 common/lockmodes.py 不一致的格子（以实测为准）：")
        for h, w, e, a in mismatches:
            print("  holder=%s waiter=%s 表里=%s 实测=%s" % (h, w, e, a))
        return 1
    print("\n8x8 全部与 common/lockmodes.py 一致")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 6: 在 og5 上实跑，用实测结果校准表**

```bash
ssh sqlrush@192.168.128.1 "cd ~/gh_skill/opencode_skill-main-v2-0729 && GSDB_HOME=~/.gdaa python3 tools/probe_lock_matrix.py -c og"
```

预期：`8x8 全部与 common/lockmodes.py 一致`，退出 0。

**若有不一致：以实测为准改 `common/lockmodes.py` 的 `_CONFLICTS`**，然后重跑 `tests/test_lockmodes_units.py`。若实测结果与「对称性」或「AccessExclusive 与一切互斥」冲突，先怀疑探测工具（超时太短、holder 没拿到锁就开始计时），把 `ACQUIRE_TIMEOUT_S` 调到 5 秒重测。

- [ ] **Step 7: 提交**

```bash
ssh sqlrush@192.168.128.1 "cd ~/gh_skill/opencode_skill-main-v2-0729 && git add common/lockmodes.py tools/probe_lock_matrix.py tests/test_lockmodes_units.py && git commit -q -m 'feat(lockwait): 8 级锁互斥矩阵 —— 表是 64 对实撞出来的，附探测工具'"
```

---

## Task 4: lockwait 的注册脚本

**Files:**
- Create: `scripts/registry/lockwait/pairs.yaml`
- Create: `scripts/registry/lockwait/chain.yaml`
- Test: `tests/test_lockwait_registry_units.py`

**Interfaces:**
- Consumes: 无
- Produces: 两个注册脚本名 `lockwait.pairs`、`lockwait.chain`

`lockwait.pairs` 的列（后续任务按名取值）：
`waiter_pid, waiter_sessionid, waiter_mode, holder_pid, holder_sessionid, holder_mode, locktype, lock_object, locktag, waiter_wait_s, waiter_user, waiter_app, waiter_query, holder_state, holder_user, holder_app, holder_xact_age_s, holder_query`

`lockwait.chain` 的列：`sessionid, block_sessionid`

- [ ] **Step 1: 写失败的测试**

创建 `tests/test_lockwait_registry_units.py`：

```python
"""注册脚本的形态检查 —— 白名单模式下这两条是 lockwait 的全部取数来源。"""
import pathlib
import sys

import pytest

_ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT))
_REG = _ROOT / "scripts" / "registry"

from common.grmp.script import load_script  # noqa: E402


@pytest.mark.parametrize("rel,name", [
    ("lockwait/pairs.yaml", "lockwait.pairs"),
    ("lockwait/chain.yaml", "lockwait.chain"),
])
def test_script_loads_and_is_readonly(rel, name):
    rec = load_script(_REG / rel)
    assert rec.name == name
    assert rec.readonly is True, "%s 不是只读 —— 诊断脚本不该能写" % rel


def test_pairs_returns_every_column_the_report_needs():
    """列名是**契约**。少一列，报告里那一栏会静默变空。"""
    sql = load_script(_REG / "lockwait/pairs.yaml").script_content
    for col in ("waiter_sessionid", "waiter_mode", "holder_sessionid",
                "holder_mode", "locktype", "lock_object", "locktag",
                "waiter_wait_s", "holder_state", "holder_query", "waiter_query"):
        assert col in sql, "pairs.yaml 少了列 %s" % col


def test_pairs_joins_holder_and_waiter_on_the_same_locktag():
    sql = load_script(_REG / "lockwait/pairs.yaml").script_content
    assert "granted" in sql, "要靠 granted 区分持有者与等待者"
    assert "locktag" in sql


def test_chain_gives_the_edge_not_an_aggregate():
    """链要的是**边**（谁等谁），根由 python 侧上溯算 —— 聚合过的链没法找根。"""
    sql = load_script(_REG / "lockwait/chain.yaml").script_content
    assert "block_sessionid" in sql
    assert "count(" not in sql.lower(), "chain 不该在 SQL 里聚合"
```

- [ ] **Step 2: 跑测试确认失败**

```bash
ssh sqlrush@192.168.128.1 "cd ~/gh_skill/opencode_skill-main-v2-0729 && python3 -m pytest tests/test_lockwait_registry_units.py -q"
```

预期：文件不存在导致的失败

- [ ] **Step 3: 写 `scripts/registry/lockwait/pairs.yaml`**

```yaml
# 锁堵塞的**成对明细** —— holder 与 waiter 及双方的锁模式。
#
# 为什么不用 pg_thread_wait_status 一个视图搞定：它给的 lockmode 是
# **waiter 请求的**模式，holder 持有的模式不在里面。而「这一对属于 8 级矩阵
# 里的哪种互斥」需要两边的模式。所以从 pg_locks 自连接：同一个 locktag 上
# granted=false 的是等待者、granted=true 的是持有者。
#
# 实测（og5，造了一次真实堵塞）：
#   waiter AccessShareLock ← holder AccessExclusiveLock，locktag 3985:b2123:0:0:0:0
#   等待时长 4.0 秒，与实际让它等的秒数对得上
#
# pid 在 openGauss 里是线程号（形如 281452581017248），sessionid 才是稳定标识，
# 两个都返回：kill 语句要用 pid，报告里给人看的用 sessionid。
name: lockwait.pairs
description: 锁堵塞的持有者/等待者成对明细（含双方锁模式、锁对象、等待时长、双方 SQL）
readonly: true
sql: |
  SELECT w.pid                         AS waiter_pid,
         COALESCE(w.sessionid, 0)      AS waiter_sessionid,
         w.mode                        AS waiter_mode,
         h.pid                         AS holder_pid,
         COALESCE(h.sessionid, 0)      AS holder_sessionid,
         h.mode                        AS holder_mode,
         w.locktype                    AS locktype,
         COALESCE(n.nspname || '.' || c.relname, '')  AS lock_object,
         COALESCE(w.locktag, '')       AS locktag,
         COALESCE(round(EXTRACT(EPOCH FROM (now() - wa.query_start))::numeric, 1), 0) AS waiter_wait_s,
         COALESCE(wa.usename, '')      AS waiter_user,
         COALESCE(wa.application_name, '') AS waiter_app,
         COALESCE(substr(wa.query, 1, 300), '')       AS waiter_query,
         COALESCE(ha.state, '')        AS holder_state,
         COALESCE(ha.usename, '')      AS holder_user,
         COALESCE(ha.application_name, '') AS holder_app,
         COALESCE(round(EXTRACT(EPOCH FROM (now() - ha.xact_start))::numeric, 1), 0) AS holder_xact_age_s,
         COALESCE(substr(ha.query, 1, 300), '')       AS holder_query
    FROM pg_locks w
    JOIN pg_locks h
      ON h.locktag = w.locktag AND h.granted AND h.pid <> w.pid
    LEFT JOIN pg_class c     ON c.oid = w.relation
    LEFT JOIN pg_namespace n ON n.oid = c.relnamespace
    LEFT JOIN pg_stat_activity wa ON wa.pid = w.pid
    LEFT JOIN pg_stat_activity ha ON ha.pid = h.pid
   WHERE w.granted = false
   ORDER BY waiter_wait_s DESC
   LIMIT {{limit}};
params:
  - key: limit
    type: INTEGER
    description: 返回条数上限
```

- [ ] **Step 4: 写 `scripts/registry/lockwait/chain.yaml`**

```yaml
# 阻塞关系的**边**：谁在等谁。根由 python 侧顺着这些边上溯算出来。
#
# 不在 SQL 里递归求根：递归 CTE 遇到环（死锁）会无限展开，而死锁恰恰是
# 这个 skill 最该报出来的情形。上溯放在 python 里，环能被检测并当场报出，
# 而不是让数据库先跑爆。
name: lockwait.chain
description: 阻塞关系的边（会话 → 阻塞它的会话）
readonly: true
sql: |
  SELECT w.sessionid       AS sessionid,
         w.block_sessionid AS block_sessionid
    FROM pg_thread_wait_status w
   WHERE w.block_sessionid IS NOT NULL
     AND w.block_sessionid <> 0
     AND w.block_sessionid <> w.sessionid;
```

- [ ] **Step 5: 跑测试确认通过**

```bash
ssh sqlrush@192.168.128.1 "cd ~/gh_skill/opencode_skill-main-v2-0729 && python3 -m pytest tests/test_lockwait_registry_units.py -q"
```

预期：全部 passed

- [ ] **Step 6: 两条脚本在真库上各跑一次（无堵塞时应返回 0 行且不报错）**

```bash
ssh sqlrush@192.168.128.1 'cd ~/gh_skill/opencode_skill-main-v2-0729 && GSDB_HOME=~/.gdaa python3 -c "
import sys; sys.path.insert(0, \".\")
from common import access
r = access.for_conn(\"og\")
print(\"pairs:\", len(r.run(\"lockwait.pairs\", {\"limit\": 20})), \"行\")
print(\"chain:\", len(r.run(\"lockwait.chain\", {})), \"行\")
"'
```

预期：两条都返回行数（无堵塞时是 0），**不报错**。若报 `未返回结果集`，说明 `runner.py` 的 `if not cols` 判定被触发——检查 SQL 是否真的有 SELECT 列表。

- [ ] **Step 7: 提交**

```bash
ssh sqlrush@192.168.128.1 "cd ~/gh_skill/opencode_skill-main-v2-0729 && git add scripts/registry/lockwait tests/test_lockwait_registry_units.py && git commit -q -m 'feat(lockwait): 注册脚本 —— pairs 配 holder/waiter 模式对，chain 只给边不求根'"
```

---

## Task 5: 阻塞链上溯（含环必须终止）

**Files:**
- Create: `skills/gaussdb-lockwait/scripts/chain.py`
- Test: `tests/test_lockwait_chain_units.py`

**Interfaces:**
- Consumes: 无（纯函数，输入是边的列表）
- Produces:
  - `Edge = tuple[int, int]`（waiter_sessionid, blocker_sessionid）
  - `roots(edges: list[Edge]) -> dict[int, int]` —— 每个等待者 → 它的**根**阻塞者
  - `cycles(edges: list[Edge]) -> list[list[int]]` —— 检测到的环（死锁）
  - `depth(edges: list[Edge], sessionid: int) -> int` —— 到根的层数
  - `blocked_by_root(edges: list[Edge]) -> dict[int, list[int]]` —— 根 → 它最终挡住的所有会话

- [ ] **Step 1: 写失败的测试**

创建 `tests/test_lockwait_chain_units.py`：

```python
"""阻塞链上溯。**含环必须终止** —— 死锁就是链上有环，而死锁正是最该报的情形。"""
import pathlib
import sys

import pytest

_ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT))
sys.path.insert(0, str(_ROOT / "skills" / "gaussdb-lockwait" / "scripts"))

from chain import blocked_by_root, cycles, depth, roots  # noqa: E402


def test_single_edge():
    assert roots([(2, 1)]) == {2: 1}
    assert depth([(2, 1)], 2) == 1


def test_three_level_chain_finds_the_real_root():
    """3 等 2、2 等 1 —— 3 的根是 1，不是 2。杀 2 不解堵。"""
    edges = [(3, 2), (2, 1)]
    assert roots(edges) == {3: 1, 2: 1}
    assert depth(edges, 3) == 2


def test_two_waiters_on_one_root():
    edges = [(2, 1), (3, 1)]
    assert blocked_by_root(edges) == {1: [2, 3]}


def test_fan_in_through_a_middle_node():
    """4 等 3、5 等 3、3 等 1 —— 根都是 1，且 1 最终挡住 3/4/5。"""
    edges = [(4, 3), (5, 3), (3, 1)]
    assert roots(edges) == {4: 1, 5: 1, 3: 1}
    assert sorted(blocked_by_root(edges)[1]) == [3, 4, 5]


def test_two_node_cycle_terminates():
    """**这条是本模块存在的理由。** 1 等 2、2 等 1，朴素上溯会死循环。"""
    edges = [(1, 2), (2, 1)]
    found = cycles(edges)
    assert found, "没检测到环"
    assert sorted(found[0]) == [1, 2]


def test_three_node_cycle_terminates():
    edges = [(1, 2), (2, 3), (3, 1)]
    assert sorted(cycles(edges)[0]) == [1, 2, 3]


def test_roots_does_not_hang_on_a_cycle():
    """环里的节点没有根 —— 返回它自己，且必须**返回**，不能挂住。"""
    edges = [(1, 2), (2, 1)]
    r = roots(edges)
    assert set(r) == {1, 2}


def test_chain_with_a_tail_into_a_cycle():
    """3 等 1，而 1 与 2 互相等。3 的上溯会走进环里，同样不能挂。"""
    edges = [(3, 1), (1, 2), (2, 1)]
    r = roots(edges)
    assert 3 in r
    assert cycles(edges)


def test_empty_input():
    assert roots([]) == {}
    assert cycles([]) == []
    assert blocked_by_root([]) == {}


def test_depth_of_an_unknown_session_is_zero():
    assert depth([(2, 1)], 99) == 0
```

- [ ] **Step 2: 跑测试确认失败**

```bash
ssh sqlrush@192.168.128.1 "cd ~/gh_skill/opencode_skill-main-v2-0729 && python3 -m pytest tests/test_lockwait_chain_units.py -q"
```

预期：`ModuleNotFoundError: No module named 'chain'`

- [ ] **Step 3: 写实现**

创建 `skills/gaussdb-lockwait/scripts/chain.py`：

```python
"""阻塞链的上溯与环检测 —— 纯函数，不连库。

**为什么根要在这里算，而不是在 SQL 里用递归 CTE：** 死锁就是链上有环，
递归 CTE 撞上环会一路展开到把数据库跑爆，而死锁恰恰是这个 skill 最该报出来的
情形。放在 python 里，环能被检测出来并当场报「这是死锁」。

**为什么必须找到根：** 杀链条中间的节点不解堵。3 等 2、2 等 1 的时候杀掉 2，
3 会立刻改成等 1，现场没有任何变化，而操作的人以为自己处理过了。
"""
from __future__ import annotations

from typing import Dict, List, Tuple

Edge = Tuple[int, int]      # (等待者 sessionid, 阻塞它的 sessionid)


def _blocker_of(edges: List[Edge]) -> Dict[int, int]:
    """等待者 → 直接阻塞它的会话。同一个等待者出现多条边时取第一条。"""
    out: Dict[int, int] = {}
    for waiter, blocker in edges:
        out.setdefault(waiter, blocker)
    return out


def roots(edges: List[Edge]) -> Dict[int, int]:
    """每个等待者 → 它的**根**阻塞者。

    走进环时停下并把当前节点当作根返回 —— 环里本来就没有根，
    但这个函数必须**返回**，不能挂住。环由 cycles() 单独报。
    """
    blocker = _blocker_of(edges)
    out: Dict[int, int] = {}
    for start in blocker:
        seen = {start}
        cur = start
        while cur in blocker:
            nxt = blocker[cur]
            if nxt in seen:          # 成环，停在这里
                cur = nxt
                break
            seen.add(nxt)
            cur = nxt
        out[start] = cur
    return out


def depth(edges: List[Edge], sessionid: int) -> int:
    """从该会话到根走了几层。不在链上的返回 0；环上最多走一圈。"""
    blocker = _blocker_of(edges)
    seen = {sessionid}
    n, cur = 0, sessionid
    while cur in blocker:
        nxt = blocker[cur]
        n += 1
        if nxt in seen:
            break
        seen.add(nxt)
        cur = nxt
    return n


def cycles(edges: List[Edge]) -> List[List[int]]:
    """检测环（死锁）。返回每个环上的会话列表，已去重。"""
    blocker = _blocker_of(edges)
    found: List[List[int]] = []
    seen_cycle = set()
    for start in blocker:
        order: List[int] = []
        pos: Dict[int, int] = {}
        cur = start
        while cur in blocker:
            if cur in pos:                       # 回到走过的节点 → 环
                ring = order[pos[cur]:]
                key = frozenset(ring)
                if key not in seen_cycle:
                    seen_cycle.add(key)
                    found.append(ring)
                break
            pos[cur] = len(order)
            order.append(cur)
            cur = blocker[cur]
    return found


def blocked_by_root(edges: List[Edge]) -> Dict[int, List[int]]:
    """根 → 它最终挡住的所有会话。报告按这个排序：挡得最多的排最前。"""
    out: Dict[int, List[int]] = {}
    for waiter, root in roots(edges).items():
        if waiter == root:            # 环上的节点，不算被自己挡
            continue
        out.setdefault(root, []).append(waiter)
    for k in out:
        out[k].sort()
    return out
```

- [ ] **Step 4: 跑测试确认通过**

```bash
ssh sqlrush@192.168.128.1 "cd ~/gh_skill/opencode_skill-main-v2-0729 && python3 -m pytest tests/test_lockwait_chain_units.py -q"
```

预期：全部 passed，且**不挂住**（有环的三条用例会在超时前返回）

- [ ] **Step 5: 提交**

```bash
ssh sqlrush@192.168.128.1 "cd ~/gh_skill/opencode_skill-main-v2-0729 && git add skills/gaussdb-lockwait/scripts/chain.py tests/test_lockwait_chain_units.py && git commit -q -m 'feat(lockwait): 阻塞链上溯 —— 含环必须终止，死锁正是最该报的那种'"
```

---

## Task 6: kill 语句生成（先实测两个函数的可用性）

**Files:**
- Create: `skills/gaussdb-lockwait/scripts/recovery.py`
- Test: `tests/test_lockwait_recovery_units.py`

**Interfaces:**
- Consumes: 无（纯函数）
- Produces:
  - `KillStatement` 冻结 dataclass：`sql: str, target_sessionid: int, target_pid: int, function: str, why: str, impact: str`
  - `kill_for(holder: dict) -> KillStatement` —— `holder` 是含 `holder_pid/holder_sessionid/holder_state/holder_user/holder_app/holder_xact_age_s/holder_query` 的行字典
  - `render_kills(kills: list[KillStatement]) -> str`

- [ ] **Step 1: 实测两个函数在 openGauss 上的可用性与入参**

这是设计文档里标着「需实测确认」的两项之一。**先测，再写代码。**

```bash
ssh sqlrush@192.168.128.1 'cd ~/gh_skill/opencode_skill-main-v2-0729 && GSDB_HOME=~/.gdaa python3 -c "
import sys; sys.path.insert(0, \".\")
from common.db import Database
db = Database.connect(\"og\", read_only=True)
for sql in [
    \"SELECT proname, pronargs FROM pg_proc WHERE proname IN (\\\"pg_cancel_backend\\\",\\\"pg_terminate_backend\\\",\\\"pg_terminate_session\\\") ORDER BY proname\",
]:
    cols, rows = db.query(sql)
    print(cols)
    for r in rows: print(\" \", r)
db.close()
"'
```

把结果记下来。**若 `pg_terminate_session` 存在且 `pronargs=2`**，说明 openGauss 需要 `(pid, sessionid)` 两个参数，下一步的实现要用它而不是单参数版本。**若只有单参数的 `pg_terminate_backend`**，按单参数写。

- [ ] **Step 2: 按实测结果写失败的测试**

创建 `tests/test_lockwait_recovery_units.py`（下面的断言按「单参数 `pg_terminate_backend(pid)` 可用」写；若 Step 1 测出需要两参数，把 `_expect_terminate` 里的期望串同步改掉）：

```python
"""kill 语句的生成 —— **只生成，不执行**。

三条规矩：
  1. 只对根 holder 生成 —— 杀中间节点不解堵
  2. 按 holder 状态选函数 —— active 用 cancel（保住会话），
     idle in transaction 用 terminate（cancel 对它无效）
  3. 每条旁边注明会杀掉谁 —— 让人能自己判断代价，而不是照抄
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
    """**cancel 对它无效** —— 它没在跑语句，只是攥着锁不放事务。"""
    k = kill_for(_holder(holder_state="idle in transaction"))
    assert k.function == "pg_terminate_backend"
    assert "pg_terminate_backend(281440306779808)" in k.sql


def test_idle_in_transaction_aborted_also_needs_terminate():
    k = kill_for(_holder(holder_state="idle in transaction (aborted)"))
    assert k.function == "pg_terminate_backend"


def test_unknown_state_falls_back_to_terminate():
    """状态取不到时选更强的那个 —— 选错成 cancel 的话操作看似成功、
    锁还在，人会以为处理过了。选 terminate 至少确实解堵。"""
    k = kill_for(_holder(holder_state=""))
    assert k.function == "pg_terminate_backend"


def test_statement_uses_pid_not_sessionid():
    """openGauss 的 pid 是线程号；这两个函数收的是 pid，不是 sessionid。"""
    k = kill_for(_holder(holder_pid=123, holder_sessionid=999))
    assert "(123)" in k.sql
    assert "999" not in k.sql.split("--")[0], "语句本身不该出现 sessionid"


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
```

- [ ] **Step 3: 跑测试确认失败**

```bash
ssh sqlrush@192.168.128.1 "cd ~/gh_skill/opencode_skill-main-v2-0729 && python3 -m pytest tests/test_lockwait_recovery_units.py -q"
```

预期：`ModuleNotFoundError: No module named 'recovery'`

- [ ] **Step 4: 写实现**

创建 `skills/gaussdb-lockwait/scripts/recovery.py`：

```python
"""快速恢复语句的生成 —— **只生成文本，绝不执行**。

按 holder 的状态选函数，理由是这两个函数解决的不是同一件事：

  active                  正在跑语句 → pg_cancel_backend 取消这条语句，
                          会话还在、事务还在，代价最小
  idle in transaction     没在跑语句，只是攥着锁不放事务 →
                          **cancel 对它无效**（没有语句可取消），
                          只能 pg_terminate_backend 断掉会话

状态取不到时选 terminate：选错成 cancel 的后果是「命令成功了但锁还在」，
而操作的人会以为已经处理过 —— 那比多断一个会话糟得多。
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
    """给一个根 holder 生成恢复语句。holder 是 lockwait.pairs 的行字典。"""
    state = str(holder.get("holder_state") or "").strip().lower()
    pid = int(holder.get("holder_pid") or 0)
    sid = int(holder.get("holder_sessionid") or 0)

    if state == "active":
        fn = "pg_cancel_backend"
        why = "holder 正在执行语句，取消该语句即可解堵，会话与事务保留"
    else:
        fn = "pg_terminate_backend"
        if state.startswith(_NEEDS_TERMINATE_PREFIX):
            why = ("holder 处于 %s —— 没有正在执行的语句，"
                   "pg_cancel_backend 对它无效，只能断开会话" % state)
        else:
            why = ("holder 状态为 %r，无法确认 cancel 是否有效；"
                   "选用更强的 terminate —— 取消失败而锁仍在，"
                   "会让人误以为已经处理过" % (holder.get("holder_state") or ""))

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
        sql="SELECT %s(%d);" % (fn, pid),
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
```

- [ ] **Step 5: 跑测试确认通过**

```bash
ssh sqlrush@192.168.128.1 "cd ~/gh_skill/opencode_skill-main-v2-0729 && python3 -m pytest tests/test_lockwait_recovery_units.py -q"
```

预期：全部 passed。若 Step 1 测出需要 `pg_terminate_session(pid, sessionid)`，把 `kill_for` 的 `sql=` 与对应测试同步改掉。

- [ ] **Step 6: 提交**

```bash
ssh sqlrush@192.168.128.1 "cd ~/gh_skill/opencode_skill-main-v2-0729 && git add skills/gaussdb-lockwait/scripts/recovery.py tests/test_lockwait_recovery_units.py && git commit -q -m 'feat(lockwait): kill 语句生成 —— 按 holder 状态选 cancel/terminate，只生成不执行'"
```

---

## Task 7: `lockwait.py` 主体

**Files:**
- Create: `skills/gaussdb-lockwait/scripts/lockwait.py`
- Create: `skills/gaussdb-lockwait/scripts/render.py`（从 `skills/gaussdb-topsql/scripts/render.py` 原样复制）
- Test: `tests/test_lockwait_entry_units.py`

**Interfaces:**
- Consumes: Task 1 `common.finding`、Task 3 `common.lockmodes`、Task 4 两条注册脚本、Task 5 `chain`、Task 6 `recovery`
- Produces:
  - `collect(runner, limit: int) -> LockReport`
  - `LockReport` 冻结 dataclass：`pairs: list[dict], edges: list, roots: dict, deadlocks: list, findings: list[Finding]`
  - `render_markdown(rep: LockReport) -> str`
  - `main(argv=None) -> int`

判定规则（产出 `Finding`，`dimension="Locks"`）：

| code | 条件 | severity |
|---|---|---|
| `LOCK_DEADLOCK` | `cycles()` 非空 | CRITICAL |
| `LOCK_WAIT_LONG` | 任一 `waiter_wait_s >= 60` | CRITICAL |
| `LOCK_WAIT` | 任一 `waiter_wait_s >= 5` | WARN |
| `LOCK_BLOCKED` | 有阻塞对但都短于 5 秒 | NOTICE |
| `LOCK_ROOT_IDLE_XACT` | 根 holder 状态以 `idle in transaction` 开头 | WARN |

- [ ] **Step 1: 写失败的测试**

创建 `tests/test_lockwait_entry_units.py`：

```python
"""lockwait 入口。用假 runner，不连库。"""
import io
import json
import pathlib
import sys

import pytest

_ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT))
sys.path.insert(0, str(_ROOT / "skills" / "gaussdb-lockwait" / "scripts"))

import lockwait  # noqa: E402
from common.finding import Severity  # noqa: E402


def _pair(**kw):
    base = dict(waiter_pid="1002", waiter_sessionid="2260",
                waiter_mode="AccessShareLock",
                holder_pid="1001", holder_sessionid="2259",
                holder_mode="AccessExclusiveLock",
                locktype="relation", lock_object="public.t",
                locktag="3985:b2123:0:0:0:0", waiter_wait_s="4.0",
                waiter_user="app", waiter_app="gsql",
                waiter_query="SELECT count(*) FROM t",
                holder_state="active", holder_user="gaussdb",
                holder_app="gsql", holder_xact_age_s="10.0",
                holder_query="LOCK TABLE t IN ACCESS EXCLUSIVE MODE")
    base.update(kw)
    return base


class _Runner:
    def __init__(self, pairs=None, edges=None):
        self._pairs = pairs if pairs is not None else []
        self._edges = edges if edges is not None else []

    def run(self, script, values=None):
        if script == "lockwait.pairs":
            return self._pairs
        if script == "lockwait.chain":
            return self._edges
        raise AssertionError("没料到的脚本 %s" % script)


def test_no_blocking_is_reported_explicitly(monkeypatch, capsys):
    """**没有锁堵塞是正常状态。** 必须明说，不能留空白 ——
    空白会被读成「这项没查」。"""
    monkeypatch.setattr(lockwait.access, "for_conn", lambda *a, **k: _Runner())
    rc = lockwait.main(["-c", "x"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "当前无锁等待" in out


def test_pair_reports_both_modes_and_the_conflict_reason():
    rep = lockwait.collect(_Runner(pairs=[_pair()]), limit=20)
    md = lockwait.render_markdown(rep)
    assert "AccessExclusiveLock" in md and "AccessShareLock" in md
    assert "互斥" in md


def test_root_is_the_top_of_the_chain_not_the_direct_blocker():
    """3 等 2、2 等 1 —— 根是 1。杀 2 不解堵。"""
    edges = [{"sessionid": "3", "block_sessionid": "2"},
             {"sessionid": "2", "block_sessionid": "1"}]
    rep = lockwait.collect(_Runner(pairs=[_pair(waiter_sessionid="3")], edges=edges), limit=20)
    assert rep.roots[3] == 1


def test_deadlock_is_critical():
    edges = [{"sessionid": "1", "block_sessionid": "2"},
             {"sessionid": "2", "block_sessionid": "1"}]
    rep = lockwait.collect(_Runner(pairs=[_pair()], edges=edges), limit=20)
    codes = {f.code: f.severity for f in rep.findings}
    assert codes.get("LOCK_DEADLOCK") is Severity.CRITICAL


def test_long_wait_is_critical():
    rep = lockwait.collect(_Runner(pairs=[_pair(waiter_wait_s="90.0")]), limit=20)
    assert any(f.code == "LOCK_WAIT_LONG" and f.severity is Severity.CRITICAL
               for f in rep.findings)


def test_short_wait_is_only_a_notice():
    rep = lockwait.collect(_Runner(pairs=[_pair(waiter_wait_s="1.0")]), limit=20)
    sevs = {f.code: f.severity for f in rep.findings}
    assert sevs.get("LOCK_BLOCKED") is Severity.NOTICE
    assert "LOCK_WAIT_LONG" not in sevs


def test_idle_in_transaction_root_is_flagged():
    rep = lockwait.collect(
        _Runner(pairs=[_pair(holder_state="idle in transaction")]), limit=20)
    assert any(f.code == "LOCK_ROOT_IDLE_XACT" for f in rep.findings)


def test_json_output_is_the_finding_contract(monkeypatch, capsys):
    """health 汇总认的就是这个形状。"""
    monkeypatch.setattr(lockwait.access, "for_conn",
                        lambda *a, **k: _Runner(pairs=[_pair(waiter_wait_s="90.0")]))
    rc = lockwait.main(["-c", "x", "--format", "json"])
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["skill"] == "gaussdb-lockwait"
    assert payload["findings"][0]["skill"] == "gaussdb-lockwait"
    assert isinstance(payload["findings"][0]["severity"], int)


def test_query_failure_is_reported_not_thrown(monkeypatch, capsys):
    """取数失败要给错误信息，不能吐 Traceback。"""
    from common.grmp.errors import QueryError

    class _Boom:
        def run(self, *a, **k):
            raise QueryError("ERROR: permission denied (SQLSTATE 42501)")

    monkeypatch.setattr(lockwait.access, "for_conn", lambda *a, **k: _Boom())
    rc = lockwait.main(["-c", "x"])
    err = capsys.readouterr().err
    assert rc == 2
    assert "Traceback" not in err
    assert "SQLSTATE" in err
```

- [ ] **Step 2: 跑测试确认失败**

```bash
ssh sqlrush@192.168.128.1 "cd ~/gh_skill/opencode_skill-main-v2-0729 && python3 -m pytest tests/test_lockwait_entry_units.py -q"
```

预期：`ModuleNotFoundError: No module named 'lockwait'`

- [ ] **Step 3: 复制 render.py**

```bash
ssh sqlrush@192.168.128.1 "cd ~/gh_skill/opencode_skill-main-v2-0729 && mkdir -p skills/gaussdb-lockwait/scripts && cp skills/gaussdb-topsql/scripts/render.py skills/gaussdb-lockwait/scripts/render.py"
```

- [ ] **Step 4: 写 `skills/gaussdb-lockwait/scripts/lockwait.py`**

骨架照 `skills/gaussdb-topsql/scripts/topsql.py`（sys.path 那两段、argparse、错误分类、退出码）逐字沿用，主体如下：

```python
#!/usr/bin/env python3
"""lockwait — 锁堵塞分析：谁挡了谁、挡在什么锁上、挡了多久、根源是谁。

只读。生成的 kill 语句**只输出文本，不执行**。

Usage:
    lockwait.py -c <conn> [--limit 20] [--format json] [--timeout 30]

**能力边界：只能在堵塞发生时抓。** openGauss 的 statement_history 只记
lock_wait_time 总量，不记当时是谁在阻塞 —— 事后追查「某条 SQL 被谁挡了」
在这个内核上做不到。实测记录：sqlid 870461000 等锁 35.4 秒，事后无法定位阻塞者。
"""
from __future__ import annotations

import argparse
import pathlib
import sys
from dataclasses import dataclass, field
from typing import Optional

_HERE = pathlib.Path(__file__).resolve()
sys.path.insert(0, str(_HERE.parent))          # sibling modules
for _anc in _HERE.parents:                      # locate common/
    if (_anc / "common" / "__init__.py").exists():
        sys.path.insert(0, str(_anc))
        break

import common  # noqa: E402
from common import access  # noqa: E402
from common.finding import Finding, Severity, findings_to_json  # noqa: E402
from common.grmp.values import as_float, as_int  # noqa: E402
from common.lockmodes import conflict_reason, typical_statements  # noqa: E402
import chain  # noqa: E402
import recovery  # noqa: E402
import render  # noqa: E402

SKILL = "gaussdb-lockwait"
DIM = "Locks"

WAIT_WARN_S = 5.0
WAIT_CRIT_S = 60.0

PAIRS_SCRIPT = "lockwait.pairs"
CHAIN_SCRIPT = "lockwait.chain"


@dataclass(frozen=True)
class LockReport:
    pairs: list = field(default_factory=list)
    edges: list = field(default_factory=list)
    roots: dict = field(default_factory=dict)
    deadlocks: list = field(default_factory=list)
    findings: list = field(default_factory=list)


def collect(runner, limit: int) -> LockReport:
    pairs = runner.run(PAIRS_SCRIPT, {"limit": int(limit)})
    raw_edges = runner.run(CHAIN_SCRIPT, {})
    edges = [(as_int(e["sessionid"]), as_int(e["block_sessionid"]))
             for e in raw_edges]
    roots = chain.roots(edges)
    deadlocks = chain.cycles(edges)
    findings = _judge(pairs, deadlocks)
    return LockReport(pairs=pairs, edges=edges, roots=roots,
                      deadlocks=deadlocks, findings=findings)


def _judge(pairs: list, deadlocks: list) -> list:
    """阈值判定 —— 确定性的，LLM 不得更改。"""
    out = []
    if deadlocks:
        out.append(Finding(
            DIM, "LOCK_DEADLOCK", Severity.CRITICAL, "死锁环数",
            str(len(deadlocks)), ">0",
            "环上的会话：" + "；".join(
                ", ".join(str(s) for s in ring) for ring in deadlocks)))
    if not pairs:
        return out
    waits = [as_float(p.get("waiter_wait_s") or 0) for p in pairs]
    longest = max(waits)
    if longest >= WAIT_CRIT_S:
        code, sev = "LOCK_WAIT_LONG", Severity.CRITICAL
        thr = ">=%.0fs" % WAIT_CRIT_S
    elif longest >= WAIT_WARN_S:
        code, sev = "LOCK_WAIT", Severity.WARN
        thr = ">=%.0fs" % WAIT_WARN_S
    else:
        code, sev = "LOCK_BLOCKED", Severity.NOTICE
        thr = ">0"
    out.append(Finding(
        DIM, code, sev, "最长锁等待", "%.1fs" % longest, thr,
        "%d 个会话被阻塞；最久的一条等在 %s 上"
        % (len(pairs), pairs[0].get("lock_object") or pairs[0].get("locktype"))))
    for p in pairs:
        if str(p.get("holder_state") or "").startswith("idle in transaction"):
            out.append(Finding(
                DIM, "LOCK_ROOT_IDLE_XACT", Severity.WARN, "空闲事务持锁",
                "会话 %s" % p.get("holder_sessionid"), "不应长期持有",
                "状态 %s，事务已持续 %s 秒，仍持有 %s"
                % (p.get("holder_state"), p.get("holder_xact_age_s"),
                   p.get("holder_mode"))))
            break
    return out


def render_markdown(rep: LockReport) -> str:
    if not rep.pairs:
        # **空结果要明说。** 空白会被读成「这项没查」。
        return ("# 锁堵塞分析\n\n当前无锁等待 —— 查询正常返回，"
                "没有任何会话在等锁。\n")
    out = ["# 锁堵塞分析\n",
           "共 %d 对阻塞关系，涉及 %d 个等待会话。\n"
           % (len(rep.pairs), len({p["waiter_sessionid"] for p in rep.pairs}))]
    if rep.deadlocks:
        out.append("> **检测到死锁**：" + "；".join(
            " → ".join(str(s) for s in ring) + " → …回到起点"
            for ring in rep.deadlocks) + "\n")
    body = []
    for p in rep.pairs:
        body.append([
            str(p.get("waiter_sessionid")), str(p.get("holder_sessionid")),
            "%s ← %s" % (p.get("waiter_mode"), p.get("holder_mode")),
            "%s %s" % (p.get("locktype"), p.get("lock_object") or ""),
            str(p.get("waiter_wait_s")),
            str(rep.roots.get(as_int(p.get("waiter_sessionid")), "")),
            render.truncate(p.get("holder_query") or "", 60),
        ])
    out.append("## 阻塞明细\n")
    out.append(render.table(
        ["等待会话", "持有会话", "模式对(waiter←holder)", "锁对象",
         "等待秒", "根阻塞会话", "持有者正在执行"], body))
    out.append("\n## 互斥关系\n")
    for p in rep.pairs[:5]:
        out.append("- 会话 %s ← %s：%s\n  持有者取这把锁的典型语句：%s"
                   % (p.get("waiter_sessionid"), p.get("holder_sessionid"),
                      conflict_reason(p["holder_mode"], p["waiter_mode"]),
                      typical_statements(p["holder_mode"])))
    kills = [recovery.kill_for(p) for p in _root_holders(rep)]
    out.append("\n" + recovery.render_kills(kills))
    return "\n".join(out)


def _root_holders(rep: LockReport) -> list:
    """只取**根** holder 去生成 kill 语句 —— 杀中间节点不解堵。"""
    root_ids = set(rep.roots.values())
    seen, out = set(), []
    for p in rep.pairs:
        sid = as_int(p.get("holder_sessionid"))
        if sid in root_ids and sid not in seen:
            seen.add(sid)
            out.append(p)
    return out


def main(argv: Optional[list] = None) -> int:
    ap = argparse.ArgumentParser(prog="lockwait.py",
                                 description="锁堵塞分析（只读；kill 语句只生成不执行）")
    ap.add_argument("-c", "--conn", default="", help="连接名（省略则用 gaussdb-login 建立的会话）")
    ap.add_argument("--limit", type=int, default=20, help="阻塞明细条数上限")
    ap.add_argument("--format", choices=["markdown", "json"], default="markdown")
    ap.add_argument("--timeout", type=int, default=None)
    args = ap.parse_args(argv)

    try:
        runner = access.for_conn(args.conn, timeout=args.timeout)
    except (common.ConfigError, common.CredentialError, access.AccessError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    try:
        rep = collect(runner, args.limit)
    except (common.DBError, access.QueryError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    try:
        if args.format == "json":
            print(findings_to_json(rep.findings, skill=SKILL))
        else:
            print(render_markdown(rep), end="")
        return 0
    except (ValueError, KeyError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 5: 跑测试确认通过**

```bash
ssh sqlrush@192.168.128.1 "cd ~/gh_skill/opencode_skill-main-v2-0729 && python3 -m pytest tests/test_lockwait_entry_units.py -q"
```

预期：全部 passed

- [ ] **Step 6: 提交**

```bash
ssh sqlrush@192.168.128.1 "cd ~/gh_skill/opencode_skill-main-v2-0729 && git add skills/gaussdb-lockwait tests/test_lockwait_entry_units.py && git commit -q -m 'feat(lockwait): skill 主体 —— 明细/互斥关系/根阻塞/恢复语句'"
```

---

## Task 8: lockwait 的 SKILL.md

**Files:**
- Create: `skills/gaussdb-lockwait/SKILL.md`

**Interfaces:**
- Consumes: Task 7 的 CLI
- Produces: 无代码接口

- [ ] **Step 1: 写 SKILL.md**

照 `skills/gaussdb-topsql/SKILL.md` 的骨架。**必须满足 `tests/test_skill_md_structure_units.py` 的 7 条**：

1. frontmatter 含 `name: gaussdb-lockwait`（必须等于目录名）与 `description`
2. `\n## 安全红线` 恰好出现 1 次
3. 明文口令那条恰好 1 次，且正文含 `credential_cli`
4. `KB-CONTRACT:BEGIN` / `KB-CONTRACT:END` 成对（或都不出现）
5. 无 `<<<<<<<` 等冲突标记
6. 无 4 个连续换行
7. 正文提到 `gaussdb-login`

内容要点：
- 什么时候用（用户说"卡住了""在等锁""这条 SQL 不动了"）
- **能力边界写在显眼处**：只能在堵塞发生时抓，事后追查不到（附实测记录）
- 输出四段的说明
- 安全红线里除了明文口令那条，再加一条：**生成的 kill 语句不得由你执行**

- [ ] **Step 2: 跑结构测试**

```bash
ssh sqlrush@192.168.128.1 "cd ~/gh_skill/opencode_skill-main-v2-0729 && python3 -m pytest tests/test_skill_md_structure_units.py -q"
```

预期：全部 passed（新 skill 会被自动纳入 parametrize）

- [ ] **Step 3: 提交**

```bash
ssh sqlrush@192.168.128.1 "cd ~/gh_skill/opencode_skill-main-v2-0729 && git add skills/gaussdb-lockwait/SKILL.md && git commit -q -m 'docs(lockwait): SKILL.md —— 把「只能现场抓」这条边界写在显眼处'"
```

---

## Task 9: lockwait 的双模式端到端

**Files:**
- Create: `tools/matrix_lockwait.py`
- Create: `tools/probe_lock_chain_e2e.py`

**Interfaces:**
- Consumes: Task 7 的 CLI
- Produces: 无代码接口（验收工具）

- [ ] **Step 1: 写三层堵塞链的实测脚本**

创建 `tools/probe_lock_chain_e2e.py`：造三条会话 A→B→C（C 持锁、B 等 C、A 等 B），跑 `lockwait.py`，断言：
- 报告里 A 的根阻塞会话是 C（不是 B）
- kill 语句只针对 C 生成
- 退出 0、无 Traceback

结构照 `tools/probe_lock_matrix.py`（`threading` + `Database.connect` + 用完 `ROLLBACK` 并 `DROP TABLE`）。

- [ ] **Step 2: 跑它**

```bash
ssh sqlrush@192.168.128.1 "cd ~/gh_skill/opencode_skill-main-v2-0729 && GSDB_HOME=~/.gdaa python3 tools/probe_lock_chain_e2e.py -c og"
```

预期：断言全过。**若根算成了 B**，回到 Task 5 检查 `roots()`。

- [ ] **Step 3: 写双模式矩阵**

创建 `tools/matrix_lockwait.py`，照本轮 explain 那套：同一批用例在 `api`（`-c og-grmp`）与 `gsql`（`-c og-gsql`）两种模式下各跑一次，逐例断言 rc 与关键字，且**任何 Traceback 判 FAIL**。用例至少含：

| 用例 | 期望 |
|---|---|
| 无堵塞时 | rc=0，输出含「当前无锁等待」 |
| `--format json` | rc=0，合法 JSON，`skill` 字段为 `gaussdb-lockwait` |
| `--limit 1` | rc=0 |
| `--timeout 5` | rc=0；api 模式 stderr 有「无法设置语句超时」，gsql 模式没有 |
| 连接名不存在 | rc=2，无 Traceback |
| 有三层堵塞时（配合 Step 1 的造场景） | rc=0，根算对 |

- [ ] **Step 4: 部署后跑矩阵**

```bash
ssh sqlrush@192.168.128.1 "cd ~/gh_skill && bash opencode_skill-main-v2-0729/install-opencode.sh --dest ~/.config/opencode/skills >/dev/null 2>&1 && cd /tmp && python3 ~/gh_skill/opencode_skill-main-v2-0729/tools/matrix_lockwait.py"
```

预期：失败项 0

- [ ] **Step 5: 提交**

```bash
ssh sqlrush@192.168.128.1 "cd ~/gh_skill/opencode_skill-main-v2-0729 && git add tools/matrix_lockwait.py tools/probe_lock_chain_e2e.py && git commit -q -m 'test(lockwait): 双模式矩阵 + 三层堵塞链实测（根必须算到链顶）'"
```

---

# Phase 2：`gaussdb-waitevent`

## Task 10: DB time 包含关系实测（决定画不画树）

**Files:**
- Create: `tools/probe_dbtime_containment.py`

**Interfaces:**
- Consumes: 无
- Produces: 一个结论——`CPU_TIME + DATA_IO_TIME + NET_SEND_TIME <= EXECUTION_TIME` 是否成立

这是设计文档里标着「需实测确认」的第二项。**先测，结论决定 Task 12 怎么渲染。**

- [ ] **Step 1: 写探测脚本**

创建 `tools/probe_dbtime_containment.py`：对最近 5 个快照窗口，各算一次
`snapshot.snap_global_instance_time` 的增量，打印每个窗口的：
`DB_TIME / EXECUTION_TIME / CPU_TIME / DATA_IO_TIME / NET_SEND_TIME / PARSE_TIME / PLAN_TIME / REWRITE_TIME / PL_EXECUTION_TIME / PL_COMPILATION_TIME`，
以及两个校验：
- `CPU+IO+NET <= EXECUTION` 是否成立
- `EXECUTION + PARSE + PLAN + REWRITE <= DB_TIME` 是否成立

- [ ] **Step 2: 跑它**

```bash
ssh sqlrush@192.168.128.1 "cd ~/gh_skill/opencode_skill-main-v2-0729 && GSDB_HOME=~/.gdaa python3 tools/probe_dbtime_containment.py -c og"
```

- [ ] **Step 3: 按结果确定渲染方式**

- **两条校验在 5 个窗口里都成立** → Task 12 按设计文档的两层树渲染
- **任一窗口不成立** → **不画树**。改为平铺列出各项占 DB_TIME 的比例，并在报告里写明「各项之间的包含关系在本实例上未能验证（窗口 N：CPU+IO+NET = X 超过 EXECUTION = Y），因此不作层级归并」

把实测数字记进 `tools/probe_dbtime_containment.py` 的模块 docstring，后人不必重测。

- [ ] **Step 4: 提交**

```bash
ssh sqlrush@192.168.128.1 "cd ~/gh_skill/opencode_skill-main-v2-0729 && git add tools/probe_dbtime_containment.py && git commit -q -m 'test(waitevent): 实测 DB time 各项的包含关系 —— 验不出来就不画那棵树'"
```

---

## Task 11: waitevent 的注册脚本

**Files:**
- Create: `scripts/registry/waitevent/instance_time.yaml`
- Create: `scripts/registry/waitevent/events.yaml`
- Test: `tests/test_waitevent_registry_units.py`

**Interfaces:**
- Consumes: 无
- Produces: `waitevent.instance_time`（列：`stat_name, delta_us`）、`waitevent.events`（列：`wait_class, event, waits, wait_us`）

**复用 wdr 的**：`wdr.snapshots`（列出可用快照）、`wdr.window`（窗口起止），不重复写。

- [ ] **Step 1: 写失败的测试**

创建 `tests/test_waitevent_registry_units.py`，断言：
- 两条脚本能 `load_script` 且 `readonly is True`
- `instance_time.yaml` 里出现 `snapshot.snap_global_instance_time`、`{{b}}`、`{{e}}`，且做的是**减法**（`sql` 中含 `-`，且有两个 CTE 或自连接）
- `events.yaml` 里 `upper(...) NOT IN ('STATUS','NONE')` —— **这一条必须有**：`STATUS/wait cmd` 累计 68 万秒，不排除会淹没一切
- `events.yaml` 返回 `event` 列（下钻到具体事件，wdr.waits 只到 class）

- [ ] **Step 2: 跑测试确认失败**

```bash
ssh sqlrush@192.168.128.1 "cd ~/gh_skill/opencode_skill-main-v2-0729 && python3 -m pytest tests/test_waitevent_registry_units.py -q"
```

- [ ] **Step 3: 写 `instance_time.yaml`**

```yaml
# 窗口内的 DB time 分解。snapshot 里的值是**累计量**，窗口值 = 后一快照 − 前一快照。
#
# wdr 没有这条 —— 它的 registry 里没有任何 instance_time 查询。等待事件那部分
# 复用 wdr.waits，时间模型这部分是新增的。
#
# 实测（og5，实时视图 gs_instance_time）十项：
#   DB_TIME EXECUTION_TIME CPU_TIME PL_EXECUTION_TIME NET_SEND_TIME
#   PLAN_TIME PARSE_TIME DATA_IO_TIME PL_COMPILATION_TIME REWRITE_TIME
#
# **时间模型里没有「锁」这一项。** 锁与轻量锁的耗时只能从等待事件来，
# 所以「DB time 花在哪」必须合并 waitevent.events 才答得完整。
name: waitevent.instance_time
description: 窗口内 DB time 各项的增量（snap_global_instance_time 后减前）
readonly: true
sql: |
  WITH b AS (SELECT snap_stat_name AS stat_name, sum(snap_value) AS v
               FROM snapshot.snap_global_instance_time
              WHERE snapshot_id = {{b}} GROUP BY snap_stat_name),
       e AS (SELECT snap_stat_name AS stat_name, sum(snap_value) AS v
               FROM snapshot.snap_global_instance_time
              WHERE snapshot_id = {{e}} GROUP BY snap_stat_name)
  SELECT e.stat_name AS stat_name,
         (e.v - b.v)  AS delta_us
    FROM e JOIN b USING (stat_name)
   ORDER BY delta_us DESC;
params:
  - key: b
    type: INTEGER
    description: 起始快照 ID
  - key: e
    type: INTEGER
    description: 结束快照 ID
```

- [ ] **Step 4: 写 `events.yaml`**

内容与 `scripts/registry/wdr/waits.yaml` 同源，但 **`GROUP BY` 到 `event` 而不是只到 `wait_class`**，用于下钻。**必须保留 `WHERE upper(e.wait_class) NOT IN ('STATUS','NONE')`**——注释里写明理由（`STATUS/wait cmd` 是等客户端的空闲时间，实测累计 681262104468 us，不排除会得出「99.9% 花在 STATUS」这种无用且误导的结论）。

- [ ] **Step 5: 跑测试 + 真库验证**

```bash
ssh sqlrush@192.168.128.1 'cd ~/gh_skill/opencode_skill-main-v2-0729 && python3 -m pytest tests/test_waitevent_registry_units.py -q && GSDB_HOME=~/.gdaa python3 -c "
import sys; sys.path.insert(0, \".\")
from common import access
r = access.for_conn(\"og\")
snaps = r.run(\"wdr.snapshots\", {\"limit\": 6})
ids = sorted(int(s[\"snapshot_id\"]) for s in snaps)[-2:]
print(\"窗口\", ids)
for row in r.run(\"waitevent.instance_time\", {\"b\": ids[0], \"e\": ids[1]}):
    print(\" \", row[\"stat_name\"], row[\"delta_us\"])
"'
```

预期：打印出十项的增量，`DB_TIME` 应为最大值之一。**若某项为负**，说明快照间发生过实例重启（计数器归零）——Task 12 要处理这种情况：负值一律报「该窗口跨越了实例重启，数据不可用」，不能当成 0。

- [ ] **Step 6: 提交**

```bash
ssh sqlrush@192.168.128.1 "cd ~/gh_skill/opencode_skill-main-v2-0729 && git add scripts/registry/waitevent tests/test_waitevent_registry_units.py && git commit -q -m 'feat(waitevent): 注册脚本 —— 时间模型增量 + 事件级下钻（STATUS 必须排除）'"
```

---

## Task 12: DB time 分解与判定（纯函数）

**Files:**
- Create: `skills/gaussdb-waitevent/scripts/dbtime.py`
- Test: `tests/test_waitevent_dbtime_units.py`

**Interfaces:**
- Consumes: Task 10 的结论、Task 1 `common.finding`
- Produces:
  - `Breakdown` 冻结 dataclass：`db_time_us: int, items: list[tuple[str, int, float]], restarted: bool`
  - `breakdown(rows: list[dict]) -> Breakdown`
  - `judge_dbtime(bd: Breakdown, waits: list[dict]) -> list[Finding]`

判定规则（`dimension="DB Time"`）：

| code | 条件 | severity |
|---|---|---|
| `DBTIME_RESTART` | 任一 delta 为负 | NOTICE（数据不可用，不是问题） |
| `DBTIME_IO_HEAVY` | `DATA_IO_TIME / DB_TIME >= 0.30` | WARN |
| `DBTIME_CPU_HEAVY` | `CPU_TIME / DB_TIME >= 0.70` | NOTICE |
| `DBTIME_NET_HEAVY` | `NET_SEND_TIME / DB_TIME >= 0.30` | WARN |
| `WAIT_LOCK_HEAVY` | `LOCK_EVENT` 耗时 / DB_TIME >= 0.10 | WARN |
| `WAIT_LWLOCK_HEAVY` | `LWLOCK_EVENT` 耗时 / DB_TIME >= 0.10 | WARN |

- [ ] **Step 1: 写失败的测试**

创建 `tests/test_waitevent_dbtime_units.py`，至少覆盖：
- 正常窗口的占比计算（给定 delta，断言百分比）
- **`DB_TIME` 为 0 时不能除零**，返回空 items 且不抛
- **任一 delta 为负 → `restarted=True`，且不产生 IO/CPU 类 finding**（跨重启的数据算出来的比例是假的，报出去比不报更糟）
- `DATA_IO_TIME` 占 35% → `DBTIME_IO_HEAVY` 且 WARN
- `LOCK_EVENT` 占 15% → `WAIT_LOCK_HEAVY`
- 全部正常 → 空 findings 列表（不是 None）

- [ ] **Step 2: 跑测试确认失败**

```bash
ssh sqlrush@192.168.128.1 "cd ~/gh_skill/opencode_skill-main-v2-0729 && python3 -m pytest tests/test_waitevent_dbtime_units.py -q"
```

- [ ] **Step 3: 写实现**

`breakdown()` 要点：
- 用 `as_int` 还原（结果值全是字符串）
- 任一 `delta_us < 0` → `restarted=True`
- `DB_TIME` 为 0 或缺失 → `items=[]`，不除零
- 按 Task 10 的结论决定 `items` 是层级结构还是平铺；**结论若是「验不出来」，平铺并在 `Breakdown` 里带一个说明串**

`judge_dbtime()` 要点：
- `restarted=True` 时**只产出 `DBTIME_RESTART`**，其余一律不判 —— 跨重启算出的比例是假的

- [ ] **Step 4: 跑测试确认通过**

```bash
ssh sqlrush@192.168.128.1 "cd ~/gh_skill/opencode_skill-main-v2-0729 && python3 -m pytest tests/test_waitevent_dbtime_units.py -q"
```

- [ ] **Step 5: 提交**

```bash
ssh sqlrush@192.168.128.1 "cd ~/gh_skill/opencode_skill-main-v2-0729 && git add skills/gaussdb-waitevent/scripts/dbtime.py tests/test_waitevent_dbtime_units.py && git commit -q -m 'feat(waitevent): DB time 分解与判定 —— 跨实例重启的窗口只报不可用，不算比例'"
```

---

## Task 13: `waitevent.py` 主体

**Files:**
- Create: `skills/gaussdb-waitevent/scripts/waitevent.py`
- Create: `skills/gaussdb-waitevent/scripts/render.py`（复制自 topsql）
- Test: `tests/test_waitevent_entry_units.py`

**Interfaces:**
- Consumes: Task 11 注册脚本、Task 12 `dbtime`
- Produces: `collect(runner, snapshots: int) -> WaitReport`、`render_markdown`、`main(argv=None) -> int`

CLI：`waitevent.py -c <conn> [--snapshots 6] [--begin ID --end ID] [--format json] [--timeout N]`

- [ ] **Step 1: 写失败的测试**

覆盖：
- 假 runner 下正常出报告，rc=0
- `--snapshots 6` 时按快照 id 排序取最近 6 个，形成 5 个窗口
- `--begin/--end` 指定时不再自动选
- 快照不足 2 个时**明确报错**（rc=2，说明「至少需要 2 个快照才能算窗口」），不是空报告
- `--format json` 形状为 Finding 契约、`skill == "gaussdb-waitevent"`
- 取数失败 → rc=2 且无 Traceback

- [ ] **Step 2–5**：同 Task 7 的节奏（跑失败 → 复制 render.py → 写实现 → 跑通过 → 提交）

提交信息：`feat(waitevent): skill 主体 —— 多窗口 DB time 分解 + 等待事件下钻`

---

## Task 14: waitevent 的 SKILL.md 与双模式矩阵

**Files:**
- Create: `skills/gaussdb-waitevent/SKILL.md`
- Create: `tools/matrix_waitevent.py`

- [ ] **Step 1: 写 SKILL.md**（7 条结构约束同 Task 8）

内容要点必须包含：
- **DB time 各项不是互斥的加和**，以及本实例上的验证结论（来自 Task 10）
- **STATUS/wait cmd 已被排除**及理由
- 时间模型里没有锁，锁的耗时来自等待事件
- 快照跨实例重启时该窗口不可用

- [ ] **Step 2: 跑结构测试**

```bash
ssh sqlrush@192.168.128.1 "cd ~/gh_skill/opencode_skill-main-v2-0729 && python3 -m pytest tests/test_skill_md_structure_units.py -q"
```

- [ ] **Step 3: 写并跑双模式矩阵**（照 Task 9 Step 3 的表格，用例换成 waitevent 的参数）

```bash
ssh sqlrush@192.168.128.1 "cd ~/gh_skill && bash opencode_skill-main-v2-0729/install-opencode.sh --dest ~/.config/opencode/skills >/dev/null 2>&1 && cd /tmp && python3 ~/gh_skill/opencode_skill-main-v2-0729/tools/matrix_waitevent.py"
```

预期：失败项 0

- [ ] **Step 4: 提交**

```bash
ssh sqlrush@192.168.128.1 "cd ~/gh_skill/opencode_skill-main-v2-0729 && git add skills/gaussdb-waitevent/SKILL.md tools/matrix_waitevent.py && git commit -q -m 'docs(waitevent): SKILL.md + 双模式矩阵'"
```

---

# Phase 3：`gaussdb-vacuum`

## Task 15: vacuum 的注册脚本

**Files:**
- Create: `scripts/registry/vacuum/dead_tuples.yaml`
- Create: `scripts/registry/vacuum/autovac_settings.yaml`
- Create: `scripts/registry/vacuum/autovac_workers.yaml`
- Create: `scripts/registry/vacuum/oldest_xmin.yaml`
- Test: `tests/test_vacuum_registry_units.py`

**Interfaces:**
- Produces：
  - `vacuum.dead_tuples` 列：`schema, table, n_live_tup, n_dead_tup, reltuples, table_bytes, last_autovacuum_age_s, last_vacuum_age_s, vacuum_count, autovacuum_count, autovac_enabled, reloptions`
  - `vacuum.autovac_settings` 列：`name, setting`（`autovacuum%` 与 `vacuum_cost%`）
  - `vacuum.autovac_workers` 列：`pid, sessionid, xact_age_s, query`（正在跑的 autovacuum worker）
  - `vacuum.oldest_xmin` 列：`source, identifier, xmin_age_s, detail`（长事务 / 两阶段事务 / 复制槽）

**`oldest_xmin` 是 R4 的数据来源**——死元组被老事务卡住时，手工 VACUUM 是无效建议。

- [ ] **Step 1–6**：同 Task 4 的节奏。测试至少断言：
- 四条脚本都 `readonly is True`
- `dead_tuples.yaml` 返回 `reltuples`（算触发线要用）和 `table_bytes`（R3 的表大小门槛要用）
- `dead_tuples.yaml` 排除系统 schema（照 `scripts/registry/health/bloat.yaml` 的 `NOT IN ('pg_catalog','information_schema','snapshot','dbe_perf','dbe_pldeveloper','cstore')`）
- `oldest_xmin.yaml` 同时覆盖 `pg_stat_activity`、`pg_prepared_xacts`、`pg_replication_slots` 三个来源

真库验证时确认 `gsbench_e2e_20260801_100g.plan_data` 出现在结果里且 `n_dead_tup` 约 2009 万。

提交信息：`feat(vacuum): 注册脚本 —— 含 oldest_xmin，长事务卡住回收时 VACUUM 是无效建议`

---

## Task 16: 触发线与四条清理规则（纯函数）

**Files:**
- Create: `skills/gaussdb-vacuum/scripts/rules.py`
- Create: `skills/gaussdb-vacuum/scripts/thresholds.py`
- Test: `tests/test_vacuum_rules_units.py`

**Interfaces:**
- Produces:
  - `Thresholds` 冻结 dataclass：`autovac_overdue_s: float = 3600.0, dead_ratio_warn: float = 0.20, dead_ratio_crit: float = 0.40, min_table_bytes: int = 100 * 1024 * 1024`
  - `default_thresholds() -> Thresholds`
  - `trigger_line(reltuples: float, settings: dict, reloptions: str) -> float`
  - `evaluate(table: dict, settings: dict, oldest_xmin: list, th: Thresholds) -> list[str]` —— 返回命中的规则码（`"R1"`/`"R2"`/`"R3"`/`"R4"`）
  - `judge_tables(tables, settings, oldest_xmin, th) -> list[Finding]`

- [ ] **Step 1: 写失败的测试**

至少覆盖：
- `trigger_line(1000, {"autovacuum_vacuum_threshold": "50", "autovacuum_vacuum_scale_factor": "0.2"}, "")` == `250.0`
- 表级 `reloptions` 里的 `autovacuum_vacuum_scale_factor=0.05` **覆盖**全局值
- **R1**：`n_dead_tup` 超过触发线且 `last_autovacuum_age_s` 为 `None` → 命中
- **R1**：超过触发线且 `last_autovacuum_age_s = 7200`（> 3600）→ 命中
- **R1 不命中**：超过触发线但 `last_autovacuum_age_s = 60` → 不命中
- **R2**：`reloptions` 含 `autovacuum_enabled=false` → 命中
- **R3**：死元组比 0.5、表 200 MB → 命中且 severity 是 CRITICAL（≥0.40）
- **R3 不命中**：死元组比 0.5 但表只有 1 MB（< 100 MB 门槛）
- **R4**：`oldest_xmin` 里有一条 3600 秒的长事务 → 命中，且 finding 的 evidence **必须包含「VACUUM 现在做也没用」这层意思**
- **实测案例**：`plan_data`（活 20178297 / 死 20087028 / `autovacuum_count=0` / `last_autovacuum=None`）→ 同时命中 R1 与 R3

- [ ] **Step 2–5**：跑失败 → 写实现 → 跑通过 → 提交

`evaluate()` 的实现要点：**R4 命中时，其余规则照常命中但报告措辞要改**——不是「不报 R1/R3」，而是在建议里明说「先处理阻塞回收的事务，否则 VACUUM 跑了也回收不掉」。

提交信息：`feat(vacuum): 触发线实算 + 四条清理规则 —— 每张表列出命中了哪几条`

---

## Task 17: `vacuum.py` 主体、SKILL.md、双模式矩阵

**Files:**
- Create: `skills/gaussdb-vacuum/scripts/vacuum.py`
- Create: `skills/gaussdb-vacuum/scripts/render.py`（复制自 topsql）
- Create: `skills/gaussdb-vacuum/SKILL.md`
- Create: `tools/matrix_vacuum.py`
- Test: `tests/test_vacuum_entry_units.py`

**Interfaces:**
- Produces：`collect(runner, limit, th) -> VacuumReport`、`render_markdown`、`main(argv=None) -> int`

CLI：`vacuum.py -c <conn> [--limit 20] [--format json] [--timeout N]`

报告三段：风险表（含触发线与命中的规则）→ autovacuum 近期运行情况 → 手工清理评估。

- [ ] **Step 1: 写失败的测试**（照 Task 7 的用例组织）

必须含：
- 无风险表时 → 明确写「未发现死元组风险表」，**不是空白**
- 风险表的每行显示命中了哪几条规则
- R4 命中时报告出现「先处理该事务」的措辞
- **报告里不得出现任何空间回收量的预估**（断言输出不含「可回收」「预计释放」这类字样）
- `--format json` 的 `skill == "gaussdb-vacuum"`

- [ ] **Step 2: 写实现与 SKILL.md**（SKILL.md 的 7 条结构约束同 Task 8）

SKILL.md 安全红线除明文口令那条外，加一条：**本 skill 不执行 VACUUM，只评估**。

- [ ] **Step 3: 跑单测 + 结构测试**

```bash
ssh sqlrush@192.168.128.1 "cd ~/gh_skill/opencode_skill-main-v2-0729 && python3 -m pytest tests/test_vacuum_entry_units.py tests/test_skill_md_structure_units.py -q"
```

- [ ] **Step 4: 在真库上验证实测案例**

```bash
ssh sqlrush@192.168.128.1 'export GSDB_HOME=~/.gdaa; SK=~/.config/opencode/skills
cd ~/gh_skill && bash opencode_skill-main-v2-0729/install-opencode.sh --dest $SK >/dev/null 2>&1
python3 $SK/gaussdb-vacuum/scripts/vacuum.py -c og | grep -A2 plan_data'
```

预期：`plan_data` 出现在风险表里，命中 R1 与 R3。**不要对它执行 VACUUM。**

- [ ] **Step 5: 写并跑双模式矩阵，然后提交**

```bash
ssh sqlrush@192.168.128.1 "cd /tmp && python3 ~/gh_skill/opencode_skill-main-v2-0729/tools/matrix_vacuum.py"
ssh sqlrush@192.168.128.1 "cd ~/gh_skill/opencode_skill-main-v2-0729 && git add skills/gaussdb-vacuum tools/matrix_vacuum.py tests/test_vacuum_entry_units.py && git commit -q -m 'feat(vacuum): skill 主体 + SKILL.md + 双模式矩阵'"
```

---

# Phase 4：health 汇总层

## Task 18: 子进程汇总模块

**Files:**
- Create: `skills/gaussdb-health/scripts/aggregate.py`
- Test: `tests/test_health_aggregate_units.py`

**Interfaces:**
- Consumes: Task 1 `common.finding.findings_from_json`
- Produces:
  - `SubSkillResult` 冻结 dataclass：`skill: str, ok: bool, findings: list, error: str`
  - `SUB_SKILLS: tuple[str, ...]` == `("gaussdb-lockwait", "gaussdb-waitevent", "gaussdb-vacuum")`
  - `NEEDS_TARGET: tuple[str, ...]` == `("gaussdb-explain", "gaussdb-sqltune", "gaussdb-sqlreview", "gaussdb-sqlfetch", "gaussdb-proctune")`
  - `script_path(skill: str) -> pathlib.Path`
  - `run_sub_skill(skill: str, conn: str, timeout: int, runner=subprocess.run) -> SubSkillResult`
  - `collect_all(conn: str, timeout: int) -> list[SubSkillResult]`

- [ ] **Step 1: 写失败的测试**

创建 `tests/test_health_aggregate_units.py`，用假的 `runner` 注入，覆盖：

```python
def test_script_path_is_a_sibling_layout():
    """仓库里与安装后是同一结构：skills/gaussdb-X/scripts/X.py"""
    p = aggregate.script_path("gaussdb-lockwait")
    assert p.parts[-3:] == ("gaussdb-lockwait", "scripts", "lockwait.py")


def test_success_parses_findings():
    ...  # 假 runner 返回 returncode=0 与合法 findings json


def test_nonzero_exit_is_recorded_not_raised():
    """子 skill 失败不能掀翻 health，但必须留下原因。"""
    r = aggregate.run_sub_skill(..., runner=_fake(rc=2, err="error: 连不上"))
    assert r.ok is False
    assert "连不上" in r.error
    assert r.findings == []


def test_timeout_is_recorded_as_a_failure():
    """超时也是失败，不是「没风险」。"""
    ...


def test_unparseable_stdout_is_a_failure_not_an_empty_list():
    """**json 解析不出来 ≠ 没风险。** 返回空列表会让 health 报「这块没问题」。"""
    r = aggregate.run_sub_skill(..., runner=_fake(rc=0, out="not json"))
    assert r.ok is False
    assert "解析" in r.error


def test_conn_and_format_are_passed_through():
    """子进程必须用同一个连接，否则查的是别的库。"""
    ...  # 断言 argv 里含 -c <conn> 与 --format json


def test_environment_is_inherited_for_the_token():
    """中间件令牌在环境变量里 —— 不继承的话子进程一律鉴权失败。"""
    ...  # 断言没有传 env= 或传的是 os.environ 的副本
```

- [ ] **Step 2: 跑测试确认失败**

```bash
ssh sqlrush@192.168.128.1 "cd ~/gh_skill/opencode_skill-main-v2-0729 && python3 -m pytest tests/test_health_aggregate_units.py -q"
```

- [ ] **Step 3: 写实现**

创建 `skills/gaussdb-health/scripts/aggregate.py`。要点：

```python
"""向其他 skill 索取风险 —— **子进程，不是 import**。

同进程 import 走不通：render.py 在 14 个 skill 里出现 13 次，model.py /
collectors.py / report.py / thresholds.py / util.py 也各有 3–4 份。
每个 skill 都 sys.path.insert(0, 自己的目录) 然后 import render ——
同进程加载两个 skill，import render 会解析到最后插入的那个目录，
**拿到别的 skill 的模块，且不报错**。

子进程还带来两个好处：某个 skill 崩了只影响那一格；health 里跑的
与用户单独跑的是同一条代码路径，不会出现「health 说有问题、单独跑看不到」。
"""
```

- `script_path()` 用 `pathlib.Path(__file__).resolve().parents[2] / skill / "scripts" / skill.replace("gaussdb-", "") + ".py"`
- `run_sub_skill()` 用 `subprocess.run([sys.executable, str(path), "-c", conn, "--format", "json", "--timeout", str(timeout)], capture_output=True, text=True, timeout=timeout + 15)`
- **不传 `env=`**，让 `os.environ`（含中间件令牌）自然继承
- 捕获 `subprocess.TimeoutExpired` → `ok=False, error="超时（%ds）"`
- `returncode != 0` → `ok=False, error=stderr 的首行`
- `findings_from_json` 抛 `ValueError` → `ok=False, error="解析子 skill 输出失败：..."`
- **任何情况下都不 raise**

- [ ] **Step 4–5: 跑测试通过并提交**

```bash
ssh sqlrush@192.168.128.1 "cd ~/gh_skill/opencode_skill-main-v2-0729 && python3 -m pytest tests/test_health_aggregate_units.py -q && git add skills/gaussdb-health/scripts/aggregate.py tests/test_health_aggregate_units.py && git commit -q -m 'feat(health): 子进程汇总模块 —— 同进程 import 会撞 render.py 且不报错'"
```

---

## Task 19: health 交出四个维度并接上汇总

**Files:**
- Modify: `skills/gaussdb-health/scripts/collectors.py`（移除 4 个 collector）
- Modify: `skills/gaussdb-health/scripts/health.py`
- Modify: `skills/gaussdb-health/scripts/report.py`
- Delete: `scripts/registry/health/lock_chain.yaml`、`waits.yaml`、`lwlock.yaml`、`bloat.yaml`
- Test: `tests/test_health_handover_units.py`

**Interfaces:**
- Consumes: Task 18 `aggregate`
- Produces: `run_health(...)` 多一个参数 `sub_results: list[SubSkillResult]`；`main()` 的退出码新增 3

- [ ] **Step 1: 写失败的测试**

创建 `tests/test_health_handover_units.py`：

```python
def test_the_four_dimensions_are_no_longer_collected_locally():
    """交出去了就不能还留一份 —— 两份采集会给出不一致的数字，且不一致是静默的。"""
    src = (_ROOT / "skills" / "gaussdb-health" / "scripts"
           / "collectors.py").read_text(encoding="utf-8")
    for gone in ("health.lock_chain", "health.waits", "health.lwlock", "health.bloat"):
        assert gone not in src, "collectors.py 还在自己查 %s" % gone


def test_the_retired_registry_scripts_are_gone():
    for rel in ("lock_chain.yaml", "waits.yaml", "lwlock.yaml", "bloat.yaml"):
        assert not (_REG / "health" / rel).exists(), "%s 该删了" % rel


def test_failed_sub_skill_is_named_in_the_report():
    """**不静默跳过。** 少一节的报告和干净的报告不能长得一样。"""
    ...  # 构造一个 ok=False 的 SubSkillResult，断言渲染结果里有它的名字和原因


def test_skills_needing_a_target_are_listed_as_not_covered():
    ...  # 断言报告里点名 explain/sqltune/sqlreview/sqlfetch/proctune 未纳入


def test_exit_code_3_when_a_dimension_failed():
    """一份缺了锁和等待的体检报告若退出 0，脚本里与干净报告无法区分。"""
    ...  # 断言 main() 返回 3 且报告仍然打印出来


def test_exit_code_0_when_everything_collected():
    ...
```

- [ ] **Step 2: 跑测试确认失败**

```bash
ssh sqlrush@192.168.128.1 "cd ~/gh_skill/opencode_skill-main-v2-0729 && python3 -m pytest tests/test_health_handover_units.py -q"
```

- [ ] **Step 3: 改 collectors.py**

从 `registry()` 返回的列表里删掉 `waits` / `bloat` / `lwlock` / `locks` 四项及其函数，并在文件头加注释说明它们去了哪个 skill。**其余 8 个维度一行不动。**

- [ ] **Step 4: 改 health.py**

- `run_health()` 增加 `sub_results` 参数，把各 `SubSkillResult` 的 findings 并入 `ev.findings`
- `main()` 里调 `aggregate.collect_all(args.conn, timeout)`
- 有任一 `ok=False` → 返回 3（**报告照常打印**）
- `--include/--exclude` 的维度名保留 `locks/waits/lwlock/bloat`，映射到对应子 skill（用户的用法不变）

- [ ] **Step 5: 改 report.py**

报告顶部新增两段：
- 「本次未采集到的维度」：列出 `ok=False` 的 skill 与原因
- 「未纳入汇总的能力」：列出 `NEEDS_TARGET` 里的 5 个 skill，说明它们需要指定 SQL/对象

每条 finding 后面带上来源：`（详见 gaussdb-lockwait）`。

- [ ] **Step 6: 删掉退役的注册脚本**

```bash
ssh sqlrush@192.168.128.1 "cd ~/gh_skill/opencode_skill-main-v2-0729 && git rm -q scripts/registry/health/lock_chain.yaml scripts/registry/health/waits.yaml scripts/registry/health/lwlock.yaml scripts/registry/health/bloat.yaml"
```

- [ ] **Step 7: 跑全量测试**

```bash
ssh sqlrush@192.168.128.1 "cd ~/gh_skill/opencode_skill-main-v2-0729 && python3 -m pytest tests/ -q 2>&1 | tail -5"
```

预期：0 failed。**若 `tests/test_scenario_matrix_units.py` 或既有 health 测试变红**，检查是不是把不该动的 8 个维度也改了。

- [ ] **Step 8: 提交**

```bash
ssh sqlrush@192.168.128.1 "cd ~/gh_skill/opencode_skill-main-v2-0729 && git add -A && git commit -q -m 'refactor(health): 交出锁/等待/轻量锁/死元组四个维度，改为汇总子 skill 的风险'"
```

---

## Task 20: health 的 SKILL.md 与端到端验收

**Files:**
- Modify: `skills/gaussdb-health/SKILL.md`
- Create: `tools/matrix_health_aggregate.py`

- [ ] **Step 1: 改 SKILL.md**

把定位改写清楚：health 是**汇总层**，重点是各 skill 报回来的风险，全面信息去对应 skill 看。加一段说明退出码 3 的含义。结构约束仍是那 7 条（改完跑 `tests/test_skill_md_structure_units.py`）。

- [ ] **Step 2: 写端到端验收工具**

创建 `tools/matrix_health_aggregate.py`，在 api / gsql 两模式下各验：

| 用例 | 期望 |
|---|---|
| 正常汇总 | rc=0，报告里出现三个子 skill 的 findings，且带「详见 gaussdb-xxx」 |
| 某个子 skill 不可用（把它的脚本临时改名） | rc=**3**，报告仍打印，顶部点名该 skill 与原因 |
| `--include locks` | 只跑 lockwait，rc=0 |
| 需要指定对象的 5 个 skill | 报告里被点名「未纳入」 |
| 全程 | 无 Traceback |

- [ ] **Step 3: 部署并跑**

```bash
ssh sqlrush@192.168.128.1 "cd ~/gh_skill && bash opencode_skill-main-v2-0729/install-opencode.sh --dest ~/.config/opencode/skills >/dev/null 2>&1 && cd /tmp && python3 ~/gh_skill/opencode_skill-main-v2-0729/tools/matrix_health_aggregate.py"
```

预期：失败项 0

- [ ] **Step 4: 全量回归 + 三个新 skill 的矩阵一起跑**

```bash
ssh sqlrush@192.168.128.1 "cd ~/gh_skill/opencode_skill-main-v2-0729 && python3 -m pytest tests/ -q 2>&1 | tail -3
cd /tmp && for m in lockwait waitevent vacuum health_aggregate; do
  echo \"--- \$m ---\"; python3 ~/gh_skill/opencode_skill-main-v2-0729/tools/matrix_\$m.py 2>&1 | tail -2
done"
```

预期：单测 0 failed，四个矩阵失败项均为 0。

- [ ] **Step 5: 提交并推送**

```bash
ssh sqlrush@192.168.128.1 "cd ~/gh_skill/opencode_skill-main-v2-0729 && git add -A && git commit -q -m 'docs(health): SKILL.md 改为汇总层定位 + 端到端验收工具' && git push origin main"
```

- [ ] **Step 6: 重新部署，确认版本戳对得上**

```bash
ssh sqlrush@192.168.128.1 "cd ~/gh_skill && bash opencode_skill-main-v2-0729/install-opencode.sh --dest ~/.config/opencode/skills >/dev/null 2>&1 && head -6 ~/.config/opencode/skills/.installed-version"
```

预期：`commit` 与 `git log -1` 一致，`skills:` 列表里出现三个新 skill。

---

## 自查记录

**规格覆盖**：设计文档的每一节都有对应任务——`common/finding.py`（T1–T2）、lockwait 四段输出（T3–T9）、8 级矩阵实测（T3）、kill 三条规矩（T6）、能力边界写进 SKILL.md（T8）、waitevent 的 DB time 口径与 STATUS 排除（T10–T14）、vacuum 四条规则含 R4（T15–T17）、health 子进程汇总与退出码 3（T18–T20）、双模式矩阵（T9/T14/T17/T20）。

**两处「需实测确认」都落在了具体任务里**：`pg_cancel_backend` / `pg_terminate_backend` 的可用性在 T6 Step 1；DB time 包含关系在 T10，且明确写了「验不出来就不画树」的分支。

**命名一致性**：`Finding` / `Severity` / `worst` / `findings_to_json` / `findings_from_json`（T1）在 T2、T7、T12、T18 中沿用同名；`conflicts` / `conflict_reason` / `typical_statements`（T3）在 T7 沿用；`roots` / `cycles` / `depth` / `blocked_by_root`（T5）在 T7 沿用；`kill_for` / `render_kills`（T6）在 T7 沿用。
