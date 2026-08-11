---
name: gaussdb-topsql
version: 2.0.0
description: "通过内置脚本对 OpenGauss/GaussDB 的 Top SQL 做多维排名。用户要按总耗时、平均耗时、调用次数、逻辑读、返回行数等维度查看资源消耗榜、热点 SQL 排行榜或系统负载主导语句时使用，包括“给我最耗时的 SQL”“查 top sql”“找最耗资源的 SQL”“看 SQL 排行榜”“按平均耗时排行”“按调用次数排行”“按逻辑读排行”“哪些 SQL 最拖慢系统”等请求。触发后运行 scripts/topsql.py，输出真实的多维排名结果，不要只解释 Top SQL 的概念。"
allowed-tools: ["exec", "read"]
compatibility: opencode
metadata:
  runtime: python3
  emoji: "🔥"
  family: sql-optimization
---

# Top SQL（OpenGauss/GaussDB）

命中以下请求时，必须使用本 skill 并实际执行脚本，不要只做概念解释：

- 用户要求查看当前最耗时、最耗资源、最热点的 SQL
- 用户要求查看 Top SQL、SQL 排行榜、资源消耗榜
- 用户要求按总耗时、平均耗时、调用次数、逻辑读、返回行数对 SQL 排序
- 用户询问“哪些 SQL 最拖慢系统”“当前数据库最重的 SQL 是什么”这类需要真实排名结果的问题

典型触发语句：

- 给我最耗时的 SQL
- 查一下 top sql
- 看最耗资源的 SQL 排行榜
- 按平均耗时排一下前 10 条 SQL
- 按调用次数最多的 SQL 排行
- 哪些 SQL 最拖慢系统

执行规则：

1. 如果用户只说“top sql / 最耗时 SQL / 最耗资源 SQL”，但没有指定排序维度：
   默认执行 `scripts/topsql.py -c <conn> --by time --limit 10`。
2. 如果用户明确指定排序维度：
   - 总耗时 / 最耗时 -> `--by time`
   - 平均耗时 -> `--by avg`
   - 调用次数 -> `--by calls`
   - 逻辑读 -> `--by reads`
   - 返回行数 -> `--by rows`
3. 如果用户明确指定要看前 N 条：
   把 N 映射到 `--limit N`；未指定时默认 `--limit 10`。
4. 如果用户没有提供连接名：
   **选择连接 —— 先登录，不要自己猜连接名。** 取数前确认已登录：
   `python3 {baseDir}/../gaussdb-login/scripts/login.py --status`。
   没有会话就先调 **gaussdb-login**：它读 `$GSDB_HOME/config.yaml`的首行 `connection_mode`，是 `gsql` 就把可选连接列成菜单让用户挑，是 `api` 就引导用户给出要访问的数据库。
   登录之后本 skill **不需要传 `-c`** —— 省略时自动用登录选定的那条连接；只有要临时换一个库时才显式传 `-c <连接名>`。
   **不要自己去读 config.yaml 挑名字**：不同应用下可能有同名连接，猜错会在另一个库上做诊断，而输出看起来完全正常。口令在 `{baseDir}/../common/credentials/*.enc`，由脚本解密，**你不要去读/解密它**。
5. 如果用户没有提供连接名，但当前只有一个连接：
   直接使用该连接。
6. 如果用户没有提供连接名，且存在多个连接：
   再询问要使用哪个连接。
7. 输出时优先返回真实 Top SQL 排名结果，并说明哪些 SQL 在主导整体负载、原因是什么；不要只解释“Top SQL 一般是干什么的”。
8. 如果用户进一步要求查看某条 SQL 的完整文本：
   优先基于结果里的 `SQL_ID` 引导执行 `gaussdb-sqlfetch`；不要凭空编造完整 SQL 文本。
9. 如果用户进一步要求对某条 SQL 做执行计划或调优：
   引导进入 `gaussdb-explain` 或 `gaussdb-sqltune`，但前提是先把真实 Top SQL 结果查出来。

标准工作流：

1. 如果用户未指定排序维度，执行：

   ```bash
   python3 {baseDir}/scripts/topsql.py -c <conn> --by time --limit 10
   ```

2. 如果用户指定了排序维度和条数，按需执行，例如：

   ```bash
   python3 {baseDir}/scripts/topsql.py -c <conn> --by avg --limit 20
   ```

## 安全红线

- **配置文件里绝不允许出现明文口令。** `config.yaml` 只放连接元数据 —— 它会被 cat、会进备份、会被贴进工单和聊天窗口，而没人会想到里面藏着生产库口令。口令一律加密存放在 `$GSDB_HOME/credentials/*.enc`（AES-256-GCM，AAD 绑定连接名），由脚本自动解密，**你不要去读取或解密它**。
  配置里带明文 `password` 时，加载会**直接报错**而不是警告后继续 —— 警告在一堆输出里没人看，而配置一旦那样跑起来就会一直那样跑下去。
  发现用户配置里有明文口令时，提示他改用：`python3 -m common.credential_cli set <连接名>`，然后删掉配置里的 password/encrypted 两行。

- **只通过本技能脚本取数**：`{baseDir}/scripts/topsql.py` 走只读会话、自动解密 `{baseDir}/../common/credentials/` 凭据，**你自己不要**直接写 Python/psql/gsql 连库、不要读取或解密 `{baseDir}/../common/credentials/`。脚本未覆盖的能力，如实说明「当前无此能力」并停止。
