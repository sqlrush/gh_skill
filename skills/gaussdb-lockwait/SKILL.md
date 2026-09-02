---
name: gaussdb-lockwait
version: 1.0.0
description: "通过内置脚本对 OpenGauss/GaussDB 做锁等待与阻塞链诊断。用户觉得某条 SQL 卡住了、在等锁、堵着不动，或想知道谁把谁挡住了、挡在哪把锁上、挡了多久、阻塞链的根是谁时使用，包括“卡住了”“在等锁”“这条 SQL 不动了”“谁把谁堵住了”“是不是有锁等待”“帮我看看现在有没有阻塞”等请求。触发后运行 scripts/lockwait.py，输出真实的持有者/等待者明细、阻塞链与根，以及供人工核对的 kill 语句；不要只解释锁等待的概念。本 skill 只能捕捉正在发生的堵塞，堵塞结束后无法回溯是被谁挡住的，遇到事后追查请求要如实说明这条边界。"
allowed-tools: ["exec", "read"]
compatibility: opencode
metadata:
  runtime: python3
  emoji: "🔒"
  family: diagnostics
---

# Lock Wait（OpenGauss/GaussDB 锁等待与阻塞链）

命中以下请求时，必须使用本 skill 并实际执行脚本，不要只做概念解释：

- 用户说某条 SQL 卡住了、不动了、跑不完
- 用户问是不是在等锁、有没有阻塞
- 用户想知道谁把谁堵住了、阻塞链的根源是哪个会话
- 用户要一份可供人工核对的恢复（kill）语句

典型触发语句：

- 卡住了
- 在等锁
- 这条 SQL 不动了
- 谁把谁堵住了
- 是不是有锁等待
- 帮我看看现在有没有阻塞
- 这个阻塞链的根是哪个会话

## 能力边界（先看这条，别等用户撞上）

`lockwait` **只能在堵塞正在发生时抓取**。它读的是 `pg_locks` / `pg_thread_wait_status` / `pg_stat_activity` 这几个反映数据库当前状态的视图——堵塞一旦解除，这些行也就不存在了。openGauss 的 `statement_history` 只记录 `lock_wait_time` 这一个总量，不记录当时是被哪个会话挡住的，所以"这条 SQL 一小时前被谁堵住了"这类事后追查，在这个内核上做不到。

实测记录：sql_id `870461000` 曾等锁 35.4 秒，占其 DB time 的 100%，但等待结束之后已经无法定位当时的阻塞者。遇到类似的事后追查请求，如实告知这条边界，不要拿 `statement_history` 里的耗时去猜测或编造阻塞对象。

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
4. 如果用户只是问"有没有阻塞"，没有指定条数或格式：
   默认执行 `scripts/lockwait.py -c <conn>`，用脚本默认的 `--limit`。
5. 如果用户要更多/更少的等待对，或要结构化结果接给别的工具：
   相应加 `--limit N`、`--format json`。
6. 如果用户想追查一条已经结束、当前查不到堵塞记录的 SQL：
   直接说明"能力边界"一节里的限制，不要用 `statement_history` 的 `lock_wait_time` 去反推阻塞对象。

## 标准工作流

```bash
python3 {baseDir}/scripts/lockwait.py -c <连接名> [--limit 20] [--format json] [--timeout 30]
```

省略 `-c` 时使用 `gaussdb-login` 已登录选定的那条连接；`--limit` 控制返回的等待对上限；`--format json` 输出结构化结果，供 `gaussdb-health` 等其他 skill 汇总；`--timeout` 控制单次采集的超时秒数。

## 输出结构

报告固定分四段：

1. **概览** —— 当前有几条阻塞链、最深的链有几层、一共涉及多少个会话。没有等待时明确写"当前无锁等待"，不要留空白，空白会被读成"这项没查"。
2. **逐对明细** —— 每一对 waiter/holder 一行：锁类型与被锁对象、`waiter 模式 ← holder 模式`、这一对命中 8 级锁矩阵（`common/lockmodes.py`，全部 64 格已在真库上实测确认）里的哪一种互斥关系、等待时长，以及双方的 sessionid、用户、应用名、正在执行的 SQL。
3. **阻塞链树与根** —— 把逐对明细串成树，标出每条链的根 holder；含环（死锁）时明确标出环上的会话，不当成普通链处理。
4. **kill 语句** —— 见下节，仅对根 holder 生成，供人工核对。

## kill 语句

- **只对根 holder 生成。** 杀链条中间节点不解堵：3 等 2、2 等 1 时杀掉 2，3 会立刻改成等 1，现场没有任何变化，而操作者会误以为已经处理过。
- **按 holder 当前状态选函数**：`state = 'active'` 用 `pg_cancel_session(pid, sessionid)`（只取消当前语句、保住会话）；`idle in transaction`（没在跑却占着锁）用 `pg_terminate_session(pid, sessionid)`，因为 cancel 对它无效。两个都用两参数的会话感知形式，不是单参数的 `pg_cancel_backend`/`pg_terminate_backend`——线程池开启时 pid 是会被复用的线程号，两参数版本要求 pid 与 sessionid 同时对上才动手，对不上就返回 false、什么也不做，能防住诊断到执行之间 pid 被复用、杀错会话的场景。
- **每条语句旁必须注明会杀掉谁**：用户、应用、已运行/已空闲多久、正在执行什么，让人能自己判断这次代价是否可接受。

## 安全红线

- **生成的 kill 语句只给人看，你不得执行它。** 本 skill 的职责到"把 `pg_cancel_session` / `pg_terminate_session` 语句连同上下文生成出来"为止；`{baseDir}/scripts/lockwait.py` 本身也只读、绝不执行任何变更。这是刻意的边界：`pg_terminate_session` 杀错会话会砍掉一个活着的事务，这笔代价是否可接受，取决于操作者对业务当下在做什么的判断，不该由工具替他做决定。你自己不要执行这些语句、不要建议"让我来执行"，也不要通过其他方式（psql/gsql/别的脚本）代为执行。

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
