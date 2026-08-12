"""向其他 skill 索取风险 —— **子进程，不是 import**。

同进程 import 走不通：render.py 在 14 个 skill 里出现 13 次，model.py /
collectors.py / report.py / thresholds.py / util.py 也各有 3–4 份。
每个 skill 都 `sys.path.insert(0, 自己的目录)` 然后 `import render` ——
同进程加载两个 skill，`import render` 会解析到最后插入的那个目录，
**拿到别的 skill 的模块，且不报错**。

子进程还带来两个好处：某个 skill 崩了只影响那一格；health 里跑的
与用户单独跑的是同一条代码路径，不会出现「health 说有问题、单独跑看不到」。

**这个模块里最重要的规则**：子 skill 失败必须被记成失败，不能记成
「没查出风险」。两者的区别就是这个模块存在的意义——如果子进程崩了、
超时了、或者吐出来的东西解析不了，而我们返回空 findings 列表，health
会打印一份干净的报告，读者会以为那块没问题。这比 health 直接报错更糟。
"""
from __future__ import annotations

import pathlib
import subprocess
import sys
from dataclasses import dataclass, field
from typing import Any, Callable

_HERE = pathlib.Path(__file__).resolve()
sys.path.insert(0, str(_HERE.parent))          # sibling modules（如果以后有的话）
for _anc in _HERE.parents:                      # locate common/（仓库根或安装目录都适用）
    if (_anc / "common" / "__init__.py").exists():
        sys.path.insert(0, str(_anc))
        break

from common.finding import findings_from_json  # noqa: E402

# script_path() 用来定位子 skill 脚本的那一级目录：
# aggregate.py 在 skills/gaussdb-health/scripts/ 下，parents[2] 就是 skills/。
# 仓库里和装好之后是同一层级结构，gaussdb-explain/SKILL.md 引用
# gaussdb-login/scripts/login.py 用的就是这个约定。
_SKILLS_DIR = _HERE.parents[2]

# 能在没有额外输入（sql_id / 目标 SQL / 目标存储过程）的情况下自己查完的子 skill。
# health 汇总时把它们各跑一遍。
SUB_SKILLS: tuple[str, ...] = ("gaussdb-lockwait", "gaussdb-waitevent", "gaussdb-vacuum")

# 需要用户指名 SQL / sql_id / 存储过程才能跑的 skill —— health 没有这类输入，
# 跑不了。列出来是为了让 health 的报告明说「这几项没覆盖」，而不是悄悄漏掉。
NEEDS_TARGET: tuple[str, ...] = (
    "gaussdb-explain", "gaussdb-sqltune", "gaussdb-sqlreview",
    "gaussdb-sqlfetch", "gaussdb-proctune",
)


@dataclass(frozen=True)
class SubSkillResult:
    """一次子 skill 子进程调用的结果。

    ok=False 时 findings 必须是空列表、error 必须是非空的、人能看懂的原因——
    "解析不出来"和"没有风险"是两件不同的事，绝不能用同一个空列表表示。
    """
    skill: str
    ok: bool
    findings: list = field(default_factory=list)
    error: str = ""


def script_path(skill: str) -> pathlib.Path:
    """把 skill 名解析成脚本路径：skills/<skill>/scripts/<去掉前缀的名字>.py。

    仓库里和装好之后是同一层结构，所以这条路径在两边都成立。
    """
    name = skill.replace("gaussdb-", "", 1) + ".py"
    return _SKILLS_DIR / skill / "scripts" / name


def run_sub_skill(skill: str, conn: str, timeout: int,
                   runner: Callable[..., Any] = subprocess.run) -> SubSkillResult:
    """跑一个子 skill 的脚本，`--format json` 拿结果。

    **绝不 raise**——子 skill 挂了、超时了、吐出来的东西解析不了，都记录成
    ok=False + 一句人能看懂的 error，而不是让异常掀翻整个 health 汇总。

    不传 `env=`，让子进程自然继承 `os.environ`（含中间件令牌）；
    否则子进程一律鉴权失败，看起来像是一堆连不上的假警报。
    """
    path = script_path(skill)
    argv = [sys.executable, str(path), "-c", conn,
             "--format", "json", "--timeout", str(timeout)]

    try:
        proc = runner(argv, capture_output=True, text=True, timeout=timeout + 15)
    except subprocess.TimeoutExpired:
        return SubSkillResult(skill=skill, ok=False, findings=[],
                               error="超时（%ds）" % timeout)
    except Exception as exc:  # 子进程起不来也不能掀翻 health（脚本路径错/权限问题……）
        return SubSkillResult(skill=skill, ok=False, findings=[],
                               error="子进程启动失败：%s" % exc)

    if proc.returncode != 0:
        stderr = (proc.stderr or "").strip()
        if stderr:
            reason = stderr.splitlines()[0]
        else:
            reason = "退出码 %d，无 stderr 输出" % proc.returncode
        return SubSkillResult(skill=skill, ok=False, findings=[], error=reason)

    try:
        findings = findings_from_json(proc.stdout)
    except ValueError as exc:
        return SubSkillResult(skill=skill, ok=False, findings=[],
                               error="解析子 skill 输出失败：%s" % exc)

    return SubSkillResult(skill=skill, ok=True, findings=findings, error="")


def collect_all(conn: str, timeout: int) -> list:
    """依次跑完 SUB_SKILLS 里的每一个，一个失败不影响其它的继续跑。"""
    return [run_sub_skill(skill, conn, timeout) for skill in SUB_SKILLS]
