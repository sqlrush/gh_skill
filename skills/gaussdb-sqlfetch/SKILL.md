---
name: gaussdb-sqlfetch
version: 2.0.0
description: "通过内置脚本把 OpenGauss/GaussDB 的 unique_sql_id 解析成完整 SQL 文本。用户要查看 SQL_ID 背后的原始 SQL、从 Top SQL/慢 SQL/WDR/SQL 审查/调优结果里取出语句、查看某条 sql_id 对应的完整文本，或展开归一化 SQL 时使用，包括“根据 sql_id 查 SQL”“把这条 SQL_ID 对应的原文取出来”“看完整 SQL”等请求。触发后运行 scripts/sqlfetch.py，输出真实 SQL，不要猜测或编造语句。"
allowed-tools: ["exec", "read"]
compatibility: opencode
metadata:
  runtime: python3
  emoji: "🔎"
  family: sql-optimization
---

# SQL Fetch（OpenGauss/GaussDB）

命中以下请求时，必须使用本 skill 并实际执行脚本，不要只做概念解释：

- 用户要根据 sql_id 取完整 SQL 文本
- 用户要从 Top SQL、慢 SQL、WDR、调优结果里还原原始语句
- 用户要查看归一化 SQL 背后的真实文本

典型触发语句：

- 根据 sql_id 查 SQL
- 把这条 SQL_ID 对应的原文取出来
- 看完整 SQL
- 这条 SQL 是什么原文
- 把归一化 SQL 还原出来

## 工作流

1. **选择连接。** 连接名沿用 `$GSDB_HOME/config.yaml`（默认 `~/.gdaa/config.yaml`） 的 `name` 字段。若不确定有哪些连接，看 name 列表；只在有多个时才问用哪一个。该文件**只含连接元数据，无密码**——口令在 `{baseDir}/../common/credentials/*.enc`，由脚本解密，**你不要去读/解密它**。
2. 运行：

   ```bash
   python3 {baseDir}/scripts/sqlfetch.py -c <conn> <unique_sql_id>
   ```

3. 若输出提示存在占位符（Normalized），说明这是归一化 SQL，向用户索要真实值并展示替换后的 SQL。
4. **若输出带 🛑「SQL 被 openGauss 截断」**：说明这条 SQL 太长、超过 `track_activity_query_size`，库里留存的就是半截文本（数据库侧 `track_activity_query_size` 的留存限制）。**不要**拿它去 explain/调优——向用户索要完整 SQL，后续 explain/sqltune 都用 `--sql-stdin` 传完整文本。
5. 下一步建议：走 explain 工作流快速看计划，或走 sqltune 工作流做深度调优。

## 安全红线

- **只通过本技能脚本取数**：`{baseDir}/scripts/sqlfetch.py` 走只读会话、自动解密 `{baseDir}/../common/credentials/` 凭据，**你自己不要**直接写 Python/psql/gsql 连库、不要读取或解密 `{baseDir}/../common/credentials/`。脚本未覆盖的能力，如实说明「当前无此能力」并停止。

