---
name: gaussdb-vacuum
version: 1.0.0
description: "通过内置脚本对 OpenGauss/GaussDB 做死元组（dead tuple）与 autovacuum 健康度评估。用户想知道哪些表堆积了太多死元组、表膨胀（bloat）是不是严重、autovacuum 有没有追上、某张表是不是需要手工 VACUUM 时使用，包括“死元组多不多”“表膨胀严重吗”“autovacuum 追上了吗”“这张表要不要手工 vacuum”“死元组比例”“autovacuum 有没有卡住”等请求。触发后运行 scripts/vacuum.py，输出真实的风险表、命中的规则与证据、autovacuum 近期运行情况；不要只解释 vacuum/dead tuple 的概念。本 skill 只评估，不执行任何 VACUUM/ANALYZE。"
allowed-tools: ["exec", "read"]
compatibility: opencode
metadata:
  runtime: python3
  emoji: "🧹"
  family: diagnostics
---

# Vacuum / 死元组清理评估（OpenGauss/GaussDB）

命中以下请求时，必须使用本 skill 并实际执行脚本，不要只做概念解释：

- 用户想知道哪些表死元组（dead tuple）太多、表是不是膨胀了
- 用户问 autovacuum 有没有追上、是不是卡住了
- 用户想知道某张表要不要手工 `VACUUM`
- 用户要一份带触发线、带证据的死元组风险清单，而不是一句"建议 VACUUM"

典型触发语句：

- 死元组多不多
- 表膨胀严重吗
- autovacuum 追上了吗
- 这张表要不要手工 vacuum
- 死元组比例是多少
- autovacuum 是不是卡住了

## 命中的规则（先看这条，别只报"建议 VACUUM"）

本 skill 不输出一句空洞的"建议 VACUUM"——每张上榜的表都要写清**命中了哪几条规则、各自的证据是什么**，让人能自己核对这个结论。规则一共四条：

- **R1 autovacuum 没追上**：死元组数已经过了触发线，而且这张表要么从没被 autovacuum 服务过（`last_autovacuum` 为 NULL），要么距上次服务已经过去很久。
- **R2 autovacuum 在这张表上被关掉了**：`reloptions` 里带 `autovacuum_enabled=false`——不管死元组堆多少，autovacuum 永远不会碰它，这条单独判、不看触发线。
- **R3 死元组比例高，且表本身大到值得管**：只看比例会把一堆几十行的小表也标红，没有意义；只有比例高**并且**表大小过了门槛才算数。
- **R4 有更老的事务挡着回收**——见下一节，命中时优先读这条，其他规则的结论都要让位于它。

## R4：卡住回收的事务，这是唯一会让"建议 VACUUM"变成错误建议的情形

死元组能不能被回收，取决于是否还有更老事务的快照可能用得到它们——只要这样的事务存在，`VACUUM` 就无法把这些行清理掉，哪怕死元组数字再高、哪怕触发线越过再多。这时候如果照常建议"运行 VACUUM"，DBA 跑完发现空间根本没回收，得到的结论要么是"这工具有问题"，要么是转头去别处找原因——两个结论都是错的，真正的问题一直没被处理。所以 **R4 命中时，报告要先说清楚该处理哪个事务，并明说现在跑 VACUUM 不会有效果**，而不是把它和其他规则并列成一条待办。

挡住回收的事务，从三个互相独立的来源分别检查，缺一个就会漏掉一整类根因：

1. **正在跑的长事务**——从 `pg_stat_activity` 里活跃/事务中的会话取事务开始时间。
2. **两阶段（prepared）事务**——从 `pg_prepared_xacts` 取。这类事务只要执行了 `PREPARE` 就持有 xmin，**此时可能没有任何活着的 backend**，只查 `pg_stat_activity` 会完全看不到它。
3. **复制槽（replication slot）**——从 `pg_replication_slots` 取，槽用 `xmin`/`catalog_xmin` 钉住回收下限。

**已实测的限制：复制槽这一路的 `xmin_age_s` 恒为空。** 这张视图在本内核上没有任何时间戳列（逐列核对过 `information_schema`），算不出"挡了多久"。报告仍然会把这个复制槽列为阻塞源，只是缺一个年龄数字——**看到 `xmin_age_s` 是空的，不能理解成"这不是问题"**，它照样在挡回收，只是不知道挡了多久。

## 触发线怎么算

触发线不是写死的常数，而是从实例当前的真实设置现算的：

```
触发线 = autovacuum_vacuum_threshold + autovacuum_vacuum_scale_factor × reltuples
```

两个参数从 `pg_settings` 实时读取，如果某张表在 `reloptions` 里单独覆盖了这两个值，按该表的覆盖值算，不用全局值。本实例实测的 GUC 现值：`autovacuum_vacuum_threshold = 50`、`autovacuum_vacuum_scale_factor = 0.2`、`autovacuum_naptime = 30`、`autovacuum_max_workers = 3`。

所以报告能给出类似"死元组 20.09M，触发线 4.03M——早就该被 vacuum 了"这样有依据的判断，而不是甩一句"超过阈值了"却不说阈值是多少、怎么算出来的。

## 本 skill 不给什么：空间回收量

本 skill 不会预估"清理后能回收多少空间"。这个数字只有真的跑一次 `VACUUM`/`VACUUM FULL` 之后才知道，此前给出的任何数字都是猜测；而一个猜测数字摆在一堆真实测量值旁边，会被当成承诺来读。所以报告只给能测量到的（死元组数、比例、触发线、autovacuum 服务历史），到此为止，不追加"预计可回收 X GB"这类内容。

## 统计口径说明

- 死元组数（`n_dead_tup`）来自统计收集器，是估算值，不是精确扫描结果；执行过 `pg_stat_reset()` 之后这些计数会被清零重新累计。
- `last_autovacuum` 为 `NULL` 表示**从未被 autovacuum 服务过**，不是"0 秒前刚跑过"——这是两个相反的事实，本 skill 严格区分，不会把 NULL 折算成 0。

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
4. 如果用户只是问"死元组多不多/要不要 vacuum"，没有指定条数或格式：
   默认执行 `scripts/vacuum.py -c <conn>`，用脚本默认的 `--limit`。
5. 如果用户要更多/更少的表，或要结构化结果接给别的工具：
   相应加 `--limit N`、`--format json`。
6. 如果用户问"清理后能回收多少空间"：
   如实说明本 skill 不提供这个数字（见上"本 skill 不给什么"一节），不要临时估算一个。
7. 如果用户要求直接执行 VACUUM/ANALYZE：
   参见下方"安全红线"，本 skill 不执行，只评估。

## 标准工作流

```bash
python3 {baseDir}/scripts/vacuum.py -c <连接名> [--limit 20] [--format json] [--timeout N]
```

省略 `-c` 时使用 `gaussdb-login` 已登录选定的那条连接；`--limit` 控制风险表返回的条数上限；`--format json` 输出结构化结果，供 `gaussdb-health` 等其他 skill 汇总；`--timeout` 控制单次采集的超时秒数。

## 输出结构

报告固定分三段：

1. **风险表** —— 每行给 schema.table、活/死元组数、死元组比例、表大小、**触发线**、`last_autovacuum`，以及**这张表命中了哪几条规则（R1~R4）**。没有风险表时明确写"未发现死元组风险表"，不是空白——空白会被读成"这项没查"。
2. **autovacuum 近期运行情况** —— 关键 GUC（`autovacuum`/`naptime`/`max_workers`/`mode`/`threshold`/`scale_factor`）与当前正在跑的 autovacuum worker；一个 worker 都没有时明说"当前没有正在运行的 autovacuum 线程"。
3. **手工清理评估** —— 逐表列出命中了哪几条规则、各自代表什么；某张表命中 R4 时，**先给出"先处理该事务，现在跑 VACUUM 不会有效果"这句提示，再列其余规则**。

## 安全红线

- **本 skill 只评估，绝不执行 `VACUUM`/`VACUUM FULL`/`ANALYZE`，你也不得代它执行。** `VACUUM FULL` 会对表加 `ACCESS EXCLUSIVE` 锁并整表重写——大表上这就是一次停服，什么时候能做、要不要做，取决于维护窗口，这个判断权在懂维护窗口的人手里，不在工具手里。普通 `VACUUM` 虽然轻得多，但在繁忙实例上仍然会跟正常业务抢 IO，同样不该由工具自作主张跑。发现死元组风险后，本 skill 只给出评估结果与证据，不生成、不建议自己执行任何清理命令。
