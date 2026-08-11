# 三个诊断 skill 与 health 汇总层改造 · 设计

日期：2026-08-11
状态：已与需求方逐节确认，待进入实现计划

## 目标

新增三个 skill，并把现有 `gaussdb-health` 从"自己什么都查"改造成"汇总其他 skill 的风险"：

| skill | 要回答的问题 |
|---|---|
| `gaussdb-lockwait` | 谁挡了谁、挡在什么锁上、挡了多久、根源是谁、怎么快速恢复 |
| `gaussdb-waitevent` | 最近几个采样窗口里，DB time 花到哪儿去了 |
| `gaussdb-vacuum` | 哪些表死元组过多、autovacuum 有没有跟上、哪些需要手工清理 |
| `gaussdb-health`（改造） | 上面这些以及其余维度**报回来的风险**，详情去各 skill 看 |

## 硬约束

**中间件（白名单）模式下只能执行预先注册的脚本。** 所以"全面分析"必须在写代码前就把要查的视图和 SQL 定死，不能像 `gaussdb-explain` 那样临时拼 SQL。某个分析维度如果只能靠动态 SQL 拿到，那它在客户环境就是不可用的——明确报"当前无此能力"，不静默降级。

## 已确认的四个决策

1. **health 交出四个维度**（`DIM_LOCKS` / `DIM_WAITS` / `DIM_LWLOCK` / `DIM_BLOAT`），由新 skill 接管采集，health 改为调用它们。
   *理由：* health 的定位是"重点介绍其他 skill 反馈的风险，全面信息去各 skill 里看"。它若再自己维护一份锁的采集逻辑，"去各 skill 里看"看到的可能和 health 说的不是一回事，而这种不一致是静默的。

2. **`gaussdb-waitevent` 复用 `gaussdb-wdr` 的注册脚本**（`wdr.waits` / `wdr.window` / `wdr.snapshots`），只新增时间模型部分。
   *理由：* 注册表本来就是共享命名空间。wdr 是"一个窗口的整体报告"，waitevent 是"专挖 DB time 去哪了"，职责不重复，但等待数据必须同一个口径——各查一遍迟早在"STATUS 排不排除""窗口边界怎么取"上分叉。

3. **health 用子进程调各 skill 的 `--format json`**，Finding 提到 `common/finding.py` 统一。
   *理由见下节实测：* 同进程 import 走不通。

4. **四件事一批做完，一个设计文档。**

## 实测事实（本设计的依据，全部在 og5 / openGauss-lite 5.0.3 上取得）

### 视图可用性

```
snapshot.snapshot                    192 行   id 1305–1497   2026-08-01 → 2026-08-11
snapshot.snap_global_wait_events   79680 行
snapshot.snap_global_instance_time  1920 行   ← DB time 模型的快照，wdr 目前没用它
snapshot.snap_summary_statement    418759 行
gs_instance_time / dbe_perf.global_instance_time    10 行（实时）
dbe_perf.wait_events / global_wait_events          415 行
pg_thread_wait_status                              826 行
pg_locks / pg_stat_activity / pg_stat_user_tables  可用

gs_wait_events        取不到（lite 上没有）
pgxc_instance_time    取不到（分布式模式才有）
```

### 时间模型的十项

```
DB_TIME 10247106 | EXECUTION_TIME 7667264 | CPU_TIME 5578611 | PL_EXECUTION_TIME 2728969
NET_SEND_TIME 1407287 | PLAN_TIME 1050700 | PARSE_TIME 178036 | DATA_IO_TIME 160278
PL_COMPILATION_TIME 149590 | REWRITE_TIME 56462
```

**时间模型里没有"锁"这一项。** 锁与轻量锁的耗时只能从等待事件来，所以"DB time 花在哪"必须合并两个来源才答得完整。

### 等待事件的分类与一个必须避开的坑

```
STATUS        76 个事件   累计 681262104468 us
IO_EVENT      73 个事件   累计    802422831 us
LWLOCK_EVENT 206 个事件   累计      2803043 us
LOCK_EVENT    15 个事件   累计        48569 us
DMS_EVENT     45 个事件   累计            0 us（lite 上恒为 0）
```

`STATUS / wait cmd` 单项就占 680804855434 us —— 那是**等客户端发命令的空闲时间**，不是干活。按 type 直接求和会得出"99.9% 的时间花在 STATUS"，无用且误导。现有 `wdr.waits` 已排除 `STATUS/NONE`，新 skill 沿用同一口径。

### 锁：真实堵塞下的实测

造了一次真实堵塞（A 持 `ACCESS EXCLUSIVE`，B 请求 `ACCESS SHARE`），四项要素全部取得到：

```
锁对象与类型   locktype=relation  locktag=3985:b2123:0:0:0:0  rel=zz_lock_probe
互斥关系       waiter AccessShareLock ← holder AccessExclusiveLock   （pg_locks 自连接一条 SQL 配好）
等待时长       query_age_s = 4.0     （让它等了 4 秒，数对得上）
阻塞链         pg_thread_wait_status.block_sessionid = 2259 → 可逐级上溯
```

两个视图在 `locktag` 上能对上，可互为交叉校验。`sessionid` 是稳定标识；`pid` 在 openGauss 里是线程号（形如 281452581017248）。

### autovacuum 参数与一个现成案例

```
autovacuum=on  naptime=30s  max_workers=3  mode=mix
autovacuum_vacuum_threshold=50   autovacuum_vacuum_scale_factor=0.2
autovacuum_freeze_max_age=4000000000

gsbench_e2e_20260801_100g.plan_data   活 20178297 / 死 20087028（约 50%）
                                      autovacuum_count=0   last_autovacuum=never
```

这张表是现成的验证素材，开发期只读不清。

### 同进程 import 走不通

```
render.py       在 14 个 skill 里出现 13 次
model.py        health memanalyze sqlreview wdr
collectors.py   health memanalyze wdr
report.py       health memanalyze sqlreview wdr
thresholds.py   health memanalyze wdr
util.py         health memanalyze wdr
```

每个 skill 的脚本都 `sys.path.insert(0, 自己的目录)` 然后 `import render`。同进程加载两个 skill，`import render` 会解析到最后插入的那个目录——拿到别的 skill 的模块，**且不报错**。这是决策 3 选子进程的直接原因。

### Finding 形状已有共识

`health` / `wdr` / `memanalyze` 三家的 `Finding` 是同一份 dataclass 抄了三遍：
`dimension, code, severity, metric, value, threshold, evidence`（wdr 多一个 `sql_id`）。
`sqlreview`（规则型）与 `explain`（计划型）形状不同，**不参与汇总，不动**。

## 共用地基：`common/finding.py`

```python
class Severity(IntEnum):   OK=0  NOTICE=1  WARN=2  CRITICAL=3
@dataclass(frozen=True)
class Finding:
    dimension: str; code: str; severity: Severity
    metric: str; value: str; threshold: str; evidence: str
    sql_id: str = ""      # wdr 已在用
    skill: str = ""       # 汇总时标明来源
    def to_dict(self) -> dict          # 各 skill --format json 的统一形状
```

三个新 skill 直接用；`health` / `wdr` / `memanalyze` 改为 import 它，**行为不变**，只是不再各存一份。

## `gaussdb-lockwait`

注册脚本组 `scripts/registry/lockwait/`。取数：`pg_locks`（holder/waiter 模式对）+ `pg_thread_wait_status`（阻塞链）+ `pg_stat_activity`（时长、SQL、用户、应用）。

**输出四段**：概览（几条链 / 多深 / 涉及几个会话）→ 逐对明细 → 阻塞链树与根 → kill 语句。

逐对明细每行：锁类型与对象、`waiter 模式 ← holder 模式`、该对属于 8 级矩阵的哪种互斥、等待时长、双方 sessionid / 用户 / 应用 / SQL。

### 8 级锁矩阵是测出来的，不是写出来的

用 64 对锁模式实际去撞一遍（一条会话持 A、另一条请求 B，看是否被挡），把矩阵测出来写进代码，测法留作测试。换到商用 GaussDB 上重跑一遍即可知道有无差异。

### kill 语句的三条规矩

1. **只对根 holder 生成。** 杀链条中间节点不解堵，是常见误操作。
2. **按 holder 状态选函数**：`state='active'` 用 `pg_cancel_backend`（取消当前语句、保住会话）；`idle in transaction`（没在跑却占着锁）用 `pg_terminate_backend`，cancel 对它无效。两个函数在 openGauss 上的可用性与入参需实测确认。
3. **每条语句旁注明它会杀掉谁**：用户、应用、已运行多久、正在执行什么。让人能自己判断代价，而不是照抄。

生成的语句放在单独代码块里，SKILL.md 的安全红线写死**不得执行**。

### 已知能力边界

`lockwait` **只能在堵塞发生时抓**。openGauss 的 `statement_history` 只记 `lock_wait_time` 总量，不记当时谁在阻塞——事后追查"某条 SQL 被谁挡了"在这个内核上做不到。项目内已有实测记录：sqlid 870461000 等锁 35.4 秒，事后无法定位阻塞者。这条写进 SKILL.md，不留给用户去撞。

## `gaussdb-waitevent`

窗口：默认最近 6 个快照（5 个窗口），`--snapshots N` 或 `--begin/--end` 指定。快照值是累计量，窗口值 = 后减前。

复用 `wdr.waits` / `wdr.window` / `wdr.snapshots`；新增 `waitevent.instance_time`（查 `snapshot.snap_global_instance_time`）。

### DB time 分解的口径

**这些项不是互斥的加和**——`EXECUTION_TIME` 本身包含 CPU 与 IO，直接列各项占 DB_TIME 的比例会加起来超过 100%。报告分两层呈现：

```
DB_TIME                       ← 分母
├ 解析阶段  PARSE / REWRITE / PLAN_TIME
└ 执行阶段  EXECUTION_TIME
   ├ CPU_TIME
   ├ DATA_IO_TIME
   └ NET_SEND_TIME
PL_EXECUTION / PL_COMPILATION ← 存储过程，与上面部分重叠
```

层级关系要在 og5 上用实际数字验证（CPU+IO+NET 是否 ≤ EXECUTION）。**验不出来的包含关系不画进树里**，改为平铺列出并注明口径未经确认——宁可少说，不画一棵假的树。

锁的耗时从等待事件补：`LOCK_EVENT` / `LWLOCK_EVENT` / `IO_EVENT` 三类的窗口增量，以及每类下钻到具体事件。`STATUS` 一律排除。

N 个窗口逐个列出，以便区分持续问题与时段尖峰。

## `gaussdb-vacuum`

注册脚本组 `scripts/registry/vacuum/`。取数：`pg_stat_user_tables` + `pg_class` + `pg_settings` + `pg_stat_activity`（正在跑的 autovacuum worker）。

**风险表**：死元组数、死元组比例、表大小、`last_autovacuum`、`autovacuum_count`、表级 `autovacuum_enabled`。

**autovacuum 触发线实算**：`threshold + scale_factor × reltuples`，参数从 `pg_settings` 实读，表级 `reloptions` 有覆盖的用表级。于是报告能直接说"死元组 2009 万，触发线 403 万，早该被清了"。

### 手工清理评估：四条写死的规则，每张表列出命中了哪几条

- **R1 autovacuum 没跟上** —— 死元组数已超过触发线，且满足其一：`last_autovacuum` 为空，或距 `last_autovacuum` 已超过 `AUTOVAC_OVERDUE_S`（初值 3600 秒 = 120 × naptime）
- **R2 表级关了 autovacuum** —— `reloptions` 含 `autovacuum_enabled=false`
- **R3 死元组占比高且表够大** —— 死元组比例 ≥ `DEAD_RATIO_WARN`（初值 0.20）且表大小 ≥ `MIN_TABLE_BYTES`（初值 100 MB）；比例 ≥ `DEAD_RATIO_CRIT`（初值 0.40）升为 CRITICAL
- **R4 有长事务卡住回收** —— 死元组只要还被某个老事务的快照需要就删不掉。此时建议手工 VACUUM 是**无效建议**。需查最老的 xmin（长事务 / 两阶段事务 / 复制槽），命中即明说"先处理这个事务，VACUUM 现在做也没用"

**不给"清完能回收多少空间"这种数。** 那要真跑才知道，推测出来的数字会被当成承诺。

## `gaussdb-health` 的改造

交出四个维度的采集，改为起子进程：

```
health → subprocess(python3, {baseDir}/../gaussdb-lockwait/scripts/lockwait.py,
                    -c <同一连接>, --format json, --timeout N)
       → 解析 stdout 的 findings → 合并排序 → 报告
```

子 skill 路径按**相对布局**定位（`skills/gaussdb-X/scripts/X.py`），仓库与安装后同构；`gaussdb-explain` 的 SKILL.md 已在用 `{baseDir}/../gaussdb-login/...` 这个写法。中间件令牌随 `os.environ` 传给子进程。

health 只收 findings 汇总排序，**详情不复述**，给出"详见 `gaussdb-lockwait`"这类指引。

**报告必须写明两件事**：
- 哪些 skill 调用失败了及原因（不静默跳过）
- 哪些维度因需要用户指定对象而未纳入（`explain` / `sqltune` / `sqlreview` / `sqlfetch` / `proctune`）

## 数据流

```
CLI → access.for_conn() → runner.run("<组>.<脚本>", {...}) → 归一化行字典
    → 判定层（纯函数、无 I/O）→ Finding 列表
    → 渲染 markdown ／ --format json
```

判定层为纯函数是有意的：8 级矩阵、阻塞链上溯、DB time 占比、autovacuum 触发线、四条清理规则，这些不连库就能测。中间件与直连的差异全部止步于 `runner`。

## 错误处理

**空结果和查不到是两回事。** 没有锁堵塞是**正常状态**，报告必须显式写"当前无锁等待"，不能留空白——`runner.py` 的 `if not cols` 那个教训在此同样适用，一段空白会被读成"这项没查"。

**health 汇总的失败不隐瞒。** 子 skill 崩了、超时了、或在白名单模型下不可用，都在报告顶部单列"本次未采集到的维度及原因"。

### 退出码

| 码 | 含义 |
|---|---|
| 0 | 报告完整 |
| 1 | 参数 / 语句形态被拒 |
| 2 | 失败，没出报告 |
| 3 | 报告出了，但有维度采集失败 |

加第 3 档的理由：一份缺了锁和等待两个维度的体检报告若退出 0，在脚本里与一份干净报告完全无法区分——那正是本项目一路在防的静默形态。

## 测试

**1. 单测（纯函数层）**
- 8 级锁矩阵表
- 阻塞链上溯：**含环必须能终止**。死锁就是链上有环（A 等 B、B 等 A），朴素逐级上溯会死循环
- DB time 占比、autovacuum 触发线、四条规则命中、Finding 合并排序
- kill 语句选 cancel 还是 terminate

**2. og5 实测**
- **8×8 锁模式实撞，把矩阵测出来**，与代码里的表逐格比对；不一致以实测为准
- 造三层堵塞链，验证根 holder 找得对
- 造死锁，验证不死循环
- 快照窗口取数、DB time 各项的包含关系
- `gsbench_e2e_20260801_100g.plan_data`（死元组 50%）上验 R1 / R3

**3. 双模式矩阵**（照 `gaussdb-explain` 那套）
每个新 skill 在 api / gsql 两种模式下跑同一批用例，输出必须一致；**任何 Traceback 算 bug**。

**4. health 汇总端到端**
子 skill 失败时报告仍产出且标注清楚，退出码为 3。

## 明确不做的事

- **不给"清完能回收多少空间"的预估**——要真跑才知道，推测值会被当成承诺
- **不执行任何 kill 语句**——只生成
- **不动 `sqlreview` / `explain` 的 Finding 形状**——它们不参与汇总
- **不为任意 SQL 注册直通脚本**——白名单是客户的安全策略，不由交付方替客户放开
- **不画未经验证的 DB time 包含关系树**
