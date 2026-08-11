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
`snap_global_instance_time` 十项时间模型里，`EXECUTION_TIME+PARSE_TIME+PLAN_TIME+REWRITE_TIME<=DB_TIME` 这条关系在实测的 5 个真实快照窗口里全部成立；但 `CPU_TIME+DATA_IO_TIME+NET_SEND_TIME<=EXECUTION_TIME` 这条在全部 5 个窗口都不成立，超出 EXECUTION_TIME 15%~24%（`tools/probe_dbtime_containment.py` docstring 有完整数字），不是舍入误差量级的失败。一真一假时画树是最危险的做法：树暗示"总量减去已知子项等于剩余子项"，读者会拿子项减父项去凑"其余"，在不成立的那一半上会得到负数或没有意义的数字，而且不会有任何报错提示。所以报告把除 `DB_TIME` 外的全部 9 项**平铺**列出、各自独立算一个占 `DB_TIME` 的比例，不做任何层级归并，也不提供"求和"或"求剩余"的读法。（探测工具对成因给出一个**未证实的猜测**：并行 worker 的 `CPU_TIME`/`DATA_IO_TIME` 是各执行线程的累加耗时，而 `EXECUTION_TIME` 是会话可感知的墙钟时间，并行执行下前者不是后者的子集——这只是猜测，不当结论转述。）
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

## 安全红线

- **配置文件里绝不允许出现明文口令。** `config.yaml` 只放连接元数据 —— 它会被 cat、会进备份、会被贴进工单和聊天窗口，而没人会想到里面藏着生产库口令。口令一律加密存放在 `$GSDB_HOME/credentials/*.enc`（AES-256-GCM，AAD 绑定连接名），由脚本自动解密，**你不要去读取或解密它**。
  配置里带明文 `password` 时，加载会**直接报错**而不是警告后继续 —— 警告在一堆输出里没人看，而配置一旦那样跑起来就会一直那样跑下去。
  发现用户配置里有明文口令时，提示他改用：`python3 -m common.credential_cli set <连接名>`，然后删掉配置里的 password/encrypted 两行。

- **只通过本技能脚本取数**：`{baseDir}/scripts/waitevent.py` 走只读会话、自动解密 `{baseDir}/../common/credentials/` 凭据，**你自己不要**直接写 Python/psql/gsql 连库、不要读取或解密 `{baseDir}/../common/credentials/`。本 skill 只读、不生成任何供执行的语句，脚本未覆盖的能力，如实说明「当前无此能力」并停止。
