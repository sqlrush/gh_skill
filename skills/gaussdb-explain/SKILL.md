---
name: gaussdb-explain
version: 2.0.0
description: "通过内置脚本执行 OpenGauss/GaussDB SQL 执行计划分析。用户要查看、运行、对比或分析一条或多条 SQL 的执行计划时使用，包括“给我SQL的执行计划”“给我几个SQL的执行计划”“跑 explain”“看执行计划”“explain analyze”“比较两条 SQL 的 plan”等请求。触发后运行 scripts/explain.py，输出真实 plan，不要只解释 EXPLAIN 会做什么。"
allowed-tools: ["exec", "read"]
compatibility: opencode
metadata:
  runtime: python3
  emoji: "📖"
  family: sql-optimization
---

# Explain（OpenGauss/GaussDB）

命中以下请求时，必须使用本 skill 并实际执行脚本，不要只做概念解释：

- 用户要求查看一条或多条 SQL 的执行计划
- 用户要求运行 `EXPLAIN` 或 `EXPLAIN ANALYZE`
- 用户要求比较多个 SQL 的 plan
- 用户明确提到执行计划、plan、cost、Seq Scan、Sort、Nested Loop、Hash Join 等执行计划节点

典型触发语句：

- 给我一个 SQL 的执行计划
- 给我几个 SQL 的执行计划
- 跑 explain
- 看执行计划
- explain analyze 一下这条 SQL
- 比较这两条 SQL 的执行计划

执行规则：

1. 如果用户已经提供 SQL：
   直接运行 `scripts/explain.py` 获取真实执行计划，不要只解释概念。
2. 如果用户一次给出多条 SQL：
   对每条 SQL 分别执行，不要只挑一条。
3. 如果用户没有提供 SQL 文本：
   只补问一件事：“请提供要查看执行计划的 SQL。”
4. 如果用户没有提供连接名：
   **选择连接。** 连接名沿用 `{baseDir}/../common/config.yaml` 的 `name` 字段。若不确定有哪些连接，看 name 列表；只在有多个时才问用哪一个。该文件**只含连接元数据，无密码**——口令在 `{baseDir}/../common/credentials/*.enc`，由脚本解密，**你不要去读/解密它**。
5. 如果用户没有提供连接名，但当前只有一个连接：
   直接使用该连接。
6. 如果用户没有提供连接名，且存在多个连接：
   再询问要使用哪个连接。
7. 除非用户明确要求 `EXPLAIN ANALYZE`，或明确同意执行 analyze，否则默认只跑普通 `EXPLAIN`。
8. 输出时优先返回真实 plan 结果，不要仅返回“这条 SQL 大概会怎样执行”的推测。

标准工作流：

1. 如果用户提供了一条 SQL，执行：

   ```bash
   python3 {baseDir}/scripts/explain.py -c <conn> --sql-stdin <<'SQL'
   <the SQL>
   SQL


## 安全红线

- **只通过本技能脚本取数**：`{baseDir}/scripts/explain.py` 走只读会话（默认不执行 SQL）、自动解密 `{baseDir}/../common/credentials/` 凭据，**你自己不要**直接写 Python/psql/gsql 连库、不要读取或解密 `{baseDir}/../common/credentials/`。脚本未覆盖的能力，如实说明「当前无此能力」并停止。
