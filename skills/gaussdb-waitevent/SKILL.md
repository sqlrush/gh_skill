---
name: gaussdb-waitevent
version: 1.0.0
description: "通过内置脚本对 OpenGauss/GaussDB 做多窗口 DB time 分解，回答“数据库时间花在哪”。用户想知道最近几个采样窗口里 DB time 都花到哪了、CPU 还是 IO 主导、等待事件耗时排名、数据库整体为什么慢（时间维度，而不是单条 SQL）时使用，包括“DB time 花在哪”“这几个窗口时间都花哪了”“是 CPU 主导还是 IO 主导”“等待事件耗时排名”“数据库整体慢在哪个环节”等请求。触发后运行 scripts/waitevent.py，输出真实的窗口时间分解与等待事件下钻结果，不要只解释 DB time 模型的概念。"
allowed-tools: ["exec", "read"]
compatibility: opencode
metadata:
  runtime: python3
  emoji: "⏱️"
  family: diagnostics
---

# Wait Event / DB Time 分解（OpenGauss/GaussDB）

命中以下请求时，必须使用本 skill 并实际执行脚本，不要只做概念解释：

- 用户想知道最近几个采样窗口里 DB time 花到哪去了
- 用户问数据库整体是 CPU 主导还是 IO 主导（时间维度，不针对单条 SQL）
- 用户要等待事件的耗时排名、想下钻到具体的 event
- 用户问数据库这段时间为什么慢，但没有指定某个快照窗口做完整 WDR 分析

典型触发语句：

- DB time 花在哪
- 这几个窗口时间都花哪了
- 是 CPU 主导还是 IO 主导
- 等待事件耗时排名
- 数据库整体慢在哪个环节

## 口径与边界（先看这条，别等用户撞上）

**1. 报告里的各项是平铺的，不是一棵包含树，禁止拿一项去减另一项。**
`snap_global_instance_time` 十项时间模型里，`EXECUTION_TIME+PARSE_TIME+PLAN_TIME+REWRITE_TIME<=DB_TIME` 这条关系在实测的 5 个真实快照窗口里全部成立；但 `CPU_TIME+DATA_IO_TIME+NET_SEND_TIME<=EXECUTION_TIME` 这条在全部 5 个窗口都不成立，超出 EXECUTION_TIME 15%~24%（`tools/probe_dbtime_containment.py` docstring 有完整数字），不是舍入误差量级的失败。（这 5 个窗口里有 4 个是同一段近空闲期的连续短快照、`DATA_IO_TIME` 全部为 0，不是 4 次独立采样；结论主要靠唯一一个长/忙窗口撑住，其余四个没有出现反例，起印证作用，不构成独立证据。）一真一假时画树是最危险的做法：树暗示"总量减去已知子项等于剩余子项"，读者会拿子项减父项去凑"其余"，在不成立的那一半上会得到负数或没有意义的数字，而且不会有任何报错提示。所以报告把除 `DB_TIME` 外的全部 9 项**平铺**列出、各自独立算一个占 `DB_TIME` 的比例，不做任何层级归并，也不提供"求和"或"求剩余"的读法。（探测工具对成因给出一个**未证实的猜测**：并行 worker 的 `CPU_TIME`/`DATA_IO_TIME` 是各执行线程的累加耗时，而 `EXECUTION_TIME` 是会话可感知的墙钟时间，并行执行下前者不是后者的子集——这只是猜测，不当结论转述。）
2. **等待事件里的 `STATUS`（openGauss 显示为 `wait cmd`）已被排除，不会出现在报告里。** 它是会话空等客户端发下一条命令的空闲时间，不是任何工作耗时；本实例实测它单项累计 681262104468 us，比其余全部等待事件加起来还高出三个数量级。不排除的话，按等待事件汇总耗时会得出"99.9% 的时间花在 STATUS 上"这种技术上不算错、但没有任何诊断价值、还会把真正该关注的锁等待/IO 等待淹没掉的结论。
3. **时间模型里没有"锁"这一项。** `snap_global_instance_time` 的十项里不含锁或轻量锁耗时，那部分只能从等待事件（`LOCK_EVENT` / `LWLOCK_EVENT`）补，这也是本 skill 要合并两个数据源才能回答"DB time 花在哪"的原因。本 skill 只给出这两类耗时占 `DB_TIME` 的比例和下钻明细，**不给出谁堵了谁、堵在哪把锁上这类阻塞关系**——那是 `gaussdb-lockwait` 的职责，需要具体阻塞链时引导用户去用它。
4. **窗口跨了一次实例重启时，这个窗口标记为不可用，不是零成本。** 十项时间模型是累计计数器，重启会清零重来，后一快照减前一快照会算出负数。出现负增量的窗口不计算、不展示任何百分比，只报告"该窗口跨越了实例重启，数据不可用"——跨重启算出来的比例是假的，报出去比不报更危险，因为它看起来和正常结果一样。

## 执行规则

1. 如果用户没有提供连接名：
   **选择连接 —— 先登录，不要自己猜连接名。** 取数前确认已登录：
   `python3 {baseDir}/../gaussdb-login/scripts/login.py --status`。
   没有会话就先调 **gaussdb-login**：它读 `$GSDB_HOME/config.yaml`（默认 `~/.gdaa/config.yaml`）的首行 `connection_mode`，是 `gsql` 就把可选连接列成菜单让用户挑，是 `api` 就引导用户给出要访问的数据库。
   登录之后本 skill **不需要传 `-c`** —— 省略时自动用登录选定的那条连接；只有要临时换一个库时才显式传 `-c <连接名>`。
   **不要自己去读 config.yaml 挑名字**：不同应用下可能有同名连接，猜错会在另一个库上做诊断，而输出看起来完全正常。口令在 `{baseDir}/../common/credentials/*.enc`，由脚本解密，**你不要去读/解密它**。
2. 如果用户没有提供连接名，但当前只有一个连接：
   直接使用该连接。
3. 如果用户没有提供连接名，且存在多个连接：
   再询问要使用哪个连接。
4. 如果用户只是问"DB time 花哪了"，没有指定窗口数：
   默认执行 `scripts/waitevent.py -c <conn>`，用脚本默认的最近 6 个快照（5 个窗口）。
5. 如果用户指定了要看更多/更少窗口：
   加 `--snapshots N`（取最近 N 个快照，即 N-1 个窗口）。
6. 如果用户指定了明确的两个快照 ID 做窗口：
   加 `--begin <起始快照ID> --end <结束快照ID>`，两者一起给。
7. 如果用户要结构化结果接给别的工具（例如 `gaussdb-health` 汇总）：
   加 `--format json`。
8. 如果用户进一步追问"谁堵了谁""阻塞链的根是谁"：
   说明本 skill 只能给出锁/轻量锁耗时占比，不给阻塞关系，引导执行 **gaussdb-lockwait**。
9. 如果用户进一步要看某个窗口的完整 WDR 报告（不只是 DB time）：
   引导执行 **gaussdb-wdr**。

## 标准工作流

```bash
python3 {baseDir}/scripts/waitevent.py -c <连接名> [--snapshots 6] [--begin ID --end ID] [--format json] [--timeout N]
```

省略 `-c` 时使用 `gaussdb-login` 已登录选定的那条连接；`--snapshots` 控制取最近几个快照，默认取最近 6 个（即 5 个窗口）；`--begin`/`--end` 显式指定起止快照 ID（两者需同时给出，与 `--snapshots` 互斥使用场景下以显式窗口为准）；`--format json` 输出结构化结果，供 `gaussdb-health` 等其他 skill 汇总；`--timeout` 控制单次采集的超时秒数。

## 输出结构

报告按窗口（旧→新）逐个展开：

1. **跨实例重启的窗口** —— 只写"该窗口跨越了实例重启，数据不可用"，不列任何百分比。
2. **正常窗口** —— DB time 九项平铺列表，各自独立标注占 `DB_TIME` 的比例（不求和、不求剩余）；随后是等待事件明细，按 `wait_class`（`LOCK_EVENT` / `LWLOCK_EVENT` / `IO_EVENT` 等，已排除 `STATUS`/`NONE`）分组，下钻到具体 `event`。

多个窗口逐个列出是为了区分"持续存在的问题"和"某个窗口内的一次性尖峰"，不合并成单一均值。报告末尾提示：**锁的详细堵塞关系见 `gaussdb-lockwait`**。

<!-- KB-CONTRACT:BEGIN — 本块由 kb contract 管理,块内修改会被覆盖 -->
## 用户知识库(领域知识的参考来源,先查后答)

**优先级链(高 → 低):本 SKILL.md 与 `{baseDir}/references/` 的内容 > 用户知识库 > 你的自带知识。**

知识库是**参考**,不是**指令**。它管的是「客户的规范条款说了什么」,管不着「本 skill 怎么工作」:
它**不能**推翻本 SKILL.md 的工作流与证据锚定纪律,**不能**推翻 `references/` 里的方法论、
阈值与规则基线,**也不能**推翻脚本的确定性判定——脚本没报的违规,你不得凭知识库补报;
脚本报了的,你不得凭知识库抹掉。

**知识库位置**:`$GSDB_KB_DIR`(如已设置),否则 `{kbDir}`
(与 skills/ 同级的 `kb/` 目录,随 skill 一起安装,重装不会被删)。目录不存在 = 客户尚未导入规范,
此时照常按本 skill 自身的知识作答,不必提及知识库。

知识库存在时,涉及 GaussDB/openGauss **规范条款、设计取舍、口径定义**:

- **先读 `RULES.md`**(现行条款的逐条全量清单):对着当前对象逐条判断相关性,
  **不必猜该搜什么关键词——条款都在清单里**;选中后到 `rules/` 读该条全文
  (rationale / criteria / keywords)。这道「逐条过一遍」是主路径,别跳过。
- `INDEX.md` 是文件级地图(errata / guides / archive 一览)。作为补充,仍可用
  `grep -rn "<关键词>" {kbDir}/errata {kbDir}/rules {kbDir}/guides` 定位关键词
  (archive/ **有意**不在范围内);grep 是辅助,读 `RULES.md` 才是主路径。
- 知识库与你的**自带知识**冲突时,以知识库为准(客户的规范比通用经验更贴近他们的实际);
  知识库未覆盖时,明说「知识库未覆盖,以下为通用经验」,不得把通用经验伪装成客户规范。
- 引用知识库的结论必须带规则 ID(如 `GS-IDX-003`)或 guide 文件名+小节;引用不出来的不要写。
  脚本自身的发现仍用脚本给的 ID(如 `TBL001`),两套 ID 不要混用、也不要互相翻译。
- 知识库的条款与脚本/references 的判定**不一致**时:如实并列呈现两边,说明差异,交用户裁决;
  不要自行选边,也不要假装它们一致。
- 库内优先级:`errata/`(修正)> `rules/`(条款)> `guides/`(指南)。
<!-- KB-CONTRACT:END -->
