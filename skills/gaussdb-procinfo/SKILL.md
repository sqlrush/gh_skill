---
name: gaussdb-procinfo
version: 2.0.0
description: "通过内置脚本对 OpenGauss/GaussDB 存储过程做只读诊断。用户要查看过程/函数源码、找循环内 SQL、逐行 DML、动态 SQL、循环异常、嵌入语句等热点，或想知道过程为什么慢但不改写时使用，包括“看下这个存储过程”“查过程源码”“这个过程为什么慢”“有没有循环里执行 SQL”等请求。触发后运行 scripts/procinfo.py，采集真实过程证据，不要只给抽象建议。"
allowed-tools: ["exec", "read"]
compatibility: opencode
metadata:
  runtime: python3
  emoji: "🩺"
  family: stored-procedure
---

# Proc Info（OpenGauss/GaussDB 存储过程只读诊断）

轻量只读诊断。**本技能只采集并解读证据，不改写、不验证、不执行过程。** 要对游标 SELECT 做经验证的索引/改写优化，改用 `/gaussdb-proctune` 对同一过程做深度调优。

本技能用 Python 脚本（`{baseDir}/scripts/`）取数：`procinfo.py` 出源码 + 结构发现 + 嵌入语句 + 运行时归因 + GUC。连接元数据读取 `$GSDB_HOME/config.yaml`（默认 `~/.gdaa/config.yaml`），凭据由脚本从 `{baseDir}/../common/credentials/` 自动解密。

命中以下请求时，必须使用本 skill 并实际执行脚本，不要只做概念解释：

- 用户要查看存储过程或函数源码
- 用户要找循环内 SQL、逐行 DML、动态 SQL、循环异常等热点
- 用户要知道某个过程为什么慢，但暂时不做改写验证

典型触发语句：

- 看下这个存储过程
- 查过程源码
- 这个过程为什么慢
- 有没有循环里执行 SQL
- 这个函数里有没有逐行 DML 或动态 SQL

## 工作流

1. **选择连接。** 连接名沿用 `$GSDB_HOME/config.yaml`（默认 `~/.gdaa/config.yaml`） 的 `name` 字段。若不确定有哪些连接，看 name 列表；只在有多个时才问用哪一个。该文件**只含连接元数据，无密码**——口令在 `{baseDir}/../common/credentials/*.enc`，由脚本解密，**你不要去读/解密它**。
2. **采集证据——一条命令。**

   ```bash
   python3 {baseDir}/scripts/procinfo.py -c <conn> <schema.proc>
   ```

   产 `## Procedure Source`、`## Structural Findings`、`## Embedded Statements`、`## Runtime Attribution`、`## Key Parameters (GUC)`。

3. **解读与呈现。**
   - **结构热点图** —— 把过程源码原样放进一个普通 ``` 代码块，在每个反模式节点行末尾追加内联标记 `[H1]`、`[H2]`…（按 `## Structural Findings` 里的顺序编号）。
   - **热点走查表** —— 第一列 `[Hn]`，交叉引用行号、反模式类型（`loop_sql` 循环内查询、`per_row_dml` 逐行 DML、`dynamic_sql` 动态 SQL、`exception_in_loop` 循环内异常块）、以及（若有）运行时归因。
   - **方向性观察** —— 对每个热点，依据反模式类型给出改法方向（如「循环内逐行 DML → 可考虑集合化」「循环内查询 → 可考虑外提或 JOIN」），引用证据里的具体数字。

4. **收尾提示。** 明确说明：以上是**未验证的诊断与方向**；要拿到经验证（cost + 等价 + hypopg 索引）的可落地优化，对同一过程运行 `/gaussdb-proctune`。

## 规则

- 只诊断，不改写、不验证、不执行过程或任何 DML。
- 每个观察都引用脚本输出里的某个数字，不编造。
- 运行时归因不可用时（实例未开 `track_stmt_stat_level`），如实说明并降级为纯静态结构分析。
- 绝不在对话中回显密码或 DSN。

## 安全红线

- **只通过本技能脚本取数**：`{baseDir}/scripts/procinfo.py` 走只读会话、自动解密 `{baseDir}/../common/credentials/` 凭据，**你自己不要**直接写 Python/psql/gsql 连库、不要读取或解密 `{baseDir}/../common/credentials/`。脚本未覆盖的能力，如实说明「当前无此能力」并停止。

