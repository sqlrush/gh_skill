# OpenGauss/GaussDB 内核速查表

## 本工具用到的 dbe_perf 视图

| 视图 | 内容 | 备注 |
|---|---|---|
| dbe_perf.statement | 归一化语句聚合(unique_sql_id、n_calls、total_elapse_time µs、n_returned_rows、n_blocks_hit/fetched) | gaussdb-slowsql/gaussdb-topsql 的数据源 |
| dbe_perf.statement_history | 单次执行历史,含字面 SQL + schema_name | 需 `enable_stmt_track=on`;字面量需 `track_stmt_parameter=on` |

`total_elapse_time` 单位是微秒;脚本会转成 ms/s。

## 执行计划节点速查

- Seq Scan / Index Scan / Index Only Scan / Bitmap Heap Scan —— 访问路径。
- Nested Loop / Hash Join / Merge Join —— 连接策略。
- "Sort Method: external merge"(ANALYZE 输出)—— work_mem 溢出。
- 行存 vs 列存表(orientation=column)的最优访问模式不同;计划看着反常时查表 DDL。

## OG 与原生 PostgreSQL 的关键差异

- 认证:OG 用 sha256,GaussDB 用 SCRAM-SHA256(10) —— 原生 psql/libpq 客户端通常连不上;本工具用纯 Python 的 pg8000 驱动。
- GaussDB 要求 DSN 带 `database=` 键,且走简单查询协议(xid64)。
- 部分 `enable_*` 优化器 GUC 是 OG 专有;从 `## Key Parameters (GUC)` 证据小节读真实值,别套 PG 默认值。
- WDR(工作负载诊断报告)在 OG 上存在;本工具已由 `wdr` 技能覆盖(snaps/collect/render)。
