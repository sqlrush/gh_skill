---
name: gaussdb-topproc
version: 2.0.0
description: "通过内置脚本对 OpenGauss/GaussDB 存储过程或函数做耗时排名。用户要查找、列出、排序、查看或分析总耗时、自身耗时、调用次数最高的过程/函数时使用，包括“最慢存储过程”“top proc”“哪个过程最耗时”“按调用次数排一下函数”“哪些过程最拖慢系统”等请求。触发后运行 scripts/topproc.py，输出真实排名，不要只解释过程统计的概念。"
allowed-tools: ["exec", "read"]
compatibility: opencode
metadata:
  runtime: python3
  emoji: "🏭"
  family: stored-procedure
---

# Top Procedures（OpenGauss/GaussDB 慢存储过程发现）

按资源消耗找出最慢/最重的存储过程或函数。**只通过本技能脚本取数。**

命中以下请求时，必须使用本 skill 并实际执行脚本，不要只做概念解释：

- 用户要查最慢的存储过程或函数
- 用户要按总耗时、自身耗时、调用次数给过程做排名
- 用户要先找出最重的过程，再继续做 procinfo 或 proctune

典型触发语句：

- 最慢存储过程
- top proc
- 哪个过程最耗时
- 按调用次数排一下函数
- 哪些过程最拖慢系统

## 工作流

1. **选择连接 —— 先登录，不要自己猜连接名。** 取数前确认已登录：
   `python3 {baseDir}/../gaussdb-login/scripts/login.py --status`。
   没有会话就先调 **gaussdb-login**：它读 `$GSDB_HOME/config.yaml`（默认 `~/.gdaa/config.yaml`）的首行 `connection_mode`，是 `gsql` 就把可选连接列成菜单让用户挑，是 `api` 就引导用户给出要访问的数据库。
   登录之后本 skill **不需要传 `-c`** —— 省略时自动用登录选定的那条连接；只有要临时换一个库时才显式传 `-c <连接名>`。
   **不要自己去读 config.yaml 挑名字**：不同应用下可能有同名连接，猜错会在另一个库上做诊断，而输出看起来完全正常。口令在 `{baseDir}/../common/credentials/*.enc`，由脚本解密，**你不要去读/解密它**。
2. **排名。**

   ```bash
   python3 {baseDir}/scripts/topproc.py -c <conn> --by time --limit 20
   ```

   数据源是 `pg_stat_user_functions`（函数级累计统计）。`--by` 可选：`time`（总耗时）、`self`（自身耗时，剔除被调用子函数）、`calls`（调用次数）。

3. **统计为空时如实说明（不要旁路）。** 若输出是「无函数级统计」提示，说明该实例 `track_functions=none`，函数级统计关闭。**不要自己直连数据库或解密凭据去查**——按提示告诉用户两条正路：
   - 让 DBA `SET track_functions='pl'`（或 `'all'`）后，调用一次目标过程，再重跑本脚本；
   - 或用 `/gaussdb-topsql` 看顶层调用语句，以及（`track_stmt_stat_level` 捕获到的）过程内部慢语句。

4. **呈现与转交。** 给出排名表（过程、calls、total_ms、self_ms），引用真实数字。对最耗资源的过程，引导下一步：
   - 只读诊断 → `/gaussdb-procinfo <schema.proc>`（结构热点，不改写）；
   - 经验证优化 → `/gaussdb-proctune <schema.proc>`（采证据 + hypopg 验证 + 出报告）。

## 规则

- 每个结论引用脚本输出里的真实数字，不编造。
- 统计不可用时如实说明并停止，绝不用其它手段绕过取数。
- 绝不在对话中回显密码或 DSN。

## 安全红线

- **只通过本技能脚本取数**：`{baseDir}/scripts/topproc.py` 走只读会话、自动解密 `{baseDir}/../common/credentials/` 凭据，**你自己不要**直接写 Python/psql/gsql 连库、不要读取或解密 `{baseDir}/../common/credentials/`。脚本未覆盖的能力，如实说明「当前无此能力」并停止。

