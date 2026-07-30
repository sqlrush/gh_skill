# AGENTS.md

处理 OpenGauss/GaussDB 相关请求时，先找 skill，后执行；不要先自己探索。

## 必须遵守

1. 只要用户问题与以下主题相关，先检查 `./skills/` 下是否有匹配的 `gaussdb-*` skill：
   - 慢 SQL
   - Top SQL
   - SQL 原文 / sql_id
   - 执行计划 / explain
   - SQL 调优
   - 存储过程诊断或调优
   - WDR
   - 健康检查
   - SQL 规范审查

2. 找到匹配 skill 后，必须先读取对应 `SKILL.md`，再按 skill 的工作流执行。

3. 不要在读取 skill 前，直接：
   - 使用 `gsql` / `psql`
   - 自己写 Python 连数据库
   - 自己写 shell 命令探测数据库
   - 绕过 skill 脚本直接查库

4. 命中 skill 时，必须优先使用 skill，不要只做概念解释。

## 优先匹配

- “当前数据库有哪些慢 SQL” / “查慢 SQL” / “给我慢 SQL 列表”
  - `gaussdb-slowsql`

- “Top SQL” / “最耗时 SQL” / “哪些 SQL 最拖慢系统”
  - `gaussdb-topsql`

- “根据 sql_id 查 SQL” / “看完整 SQL” / “SQL 原文”
  - `gaussdb-sqlfetch`

- “执行计划” / “跑 explain” / “explain analyze”
  - `gaussdb-explain`

- “优化这条 SQL” / “这个 sql_id 怎么调优”
  - `gaussdb-sqltune`

- “库健康吗” / “为什么卡” / “有没有阻塞” / “有没有长事务”
  - `gaussdb-health`

- “看两个快照之间的 WDR” / “这段时间库为什么慢”
  - `gaussdb-wdr`

- “最慢存储过程” / “哪个过程最耗时”
  - `gaussdb-topproc`

- “看这个存储过程” / “过程为什么慢”
  - `gaussdb-procinfo`

- “优化这个存储过程” / “游标 SQL 怎么优化”
  - `gaussdb-proctune`

- “这段 SQL 合不合规” / “上线前审一下”
  - `gaussdb-sqlreview`
