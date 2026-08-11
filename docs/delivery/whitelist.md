# GRMP 白名单脚本清单

由 `grmp_middleware/fixtures/script_config.db` 导出（那是 SQLite，
GitHub 上点不开）。**这份清单就是 agent 在客户环境能执行的全部 SQL**
—— 白名单之外的一条都递不进去。

重新生成：

```bash
python3 -m grmp_middleware.dump_whitelist
```

| 项 | 值 |
|---|---|
| 脚本总数 | 94 |
| id 范围 | 1 ~ 94 |

> `id` 是**环境相关数据，不是契约**。skill 从不持有它 —— 运行时调
> 接口一按 `cmd_name` 现查。客户环境重新发布后 id 会不同，属正常。

## 按命名空间

| 命名空间 | 条数 | 脚本 |
|---|---|---|
| **explain** | 2 | `plan_text`, `plan_text_analyze` |
| **health** | 19 | `archive_mode`, `bgwriter`, `bloat`, `conn_concentration`, `conn_states`, `db_concurrency`, `db_info`, `invalid_index`, `lock_chain`, `long_xact`, `lwlock`, `overview`, `prepared_xacts`, `replication`, `slow_sql`, `stale_stats`, `stats_window`, `unused_index`, `waits` |
| **lockwait** | 2 | `chain`, `pairs` |
| **memanalyze** | 11 | `activity`, `cols_bare`, `cols_qualified`, `context`, `gucs`, `instance`, `session`, `wlm_operator`, `wlm_operator_hist`, `wlm_sql`, `wlm_sql_hist` |
| **perf** | 9 | `bgwriter`, `db_stat`, `instance_time`, `locks`, `memory`, `sessions`, `table_stat`, `wait_events`, `wait_status` |
| **procinfo** | 2 | `key_gucs`, `proc_def` |
| **proctune** | 10 | `column_stats`, `db_version`, `indexes`, `key_gucs`, `plan_text`, `plan_text_analyze`, `proc_def`, `sql_from_history`, `sql_from_statement`, `tables` |
| **session** | 3 | `active_only`, `by_user`, `top_by` |
| **slowsql** | 1 | `slow_sql` |
| **sqlfetch** | 2 | `from_history`, `from_statement` |
| **sqlreview** | 5 | `from_history`, `from_statement`, `indexes`, `tables`, `top_sql` |
| **sqltune** | 11 | `column_stats`, `from_history`, `from_statement`, `indexes`, `key_gucs`, `plan_json`, `plan_text`, `plan_text_analyze`, `stats_freshness`, `tables`, `version` |
| **topproc** | 1 | `top_procs` |
| **topsql** | 1 | `top_sql` |
| **waitevent** | 2 | `events`, `instance_time` |
| **wdr** | 13 | `cache`, `checkpoint`, `db_stat`, `db_summary`, `file_io`, `load_profile`, `native_report`, `node_name`, `snapshots`, `top_sql`, `waits`, `wdr_enabled`, `window` |

---

## 全部脚本

### `explain.plan_text`

- id `1` · 类型 `SQL` · 会话 **只读** · is_valid `1` · 异步 `0`

| 参数 | 类型 |
|---|---|
| `sql` | STRING |

```sql
EXPLAIN (ANALYZE false, BUFFERS false, FORMAT TEXT) {{sql}}
```

### `explain.plan_text_analyze`

- id `2` · 类型 `SQL` · 会话 **只读** · is_valid `1` · 异步 `0`

| 参数 | 类型 |
|---|---|
| `sql` | STRING |

```sql
EXPLAIN (ANALYZE true, BUFFERS true, FORMAT TEXT) {{sql}}
```

### `health.archive_mode`

- id `3` · 类型 `SQL` · 会话 **只读** · is_valid `1` · 异步 `0`

无参数

```sql
SELECT setting FROM pg_settings WHERE name='archive_mode';
```

### `health.bgwriter`

- id `4` · 类型 `SQL` · 会话 **只读** · is_valid `1` · 异步 `0`

无参数

```sql
SELECT checkpoints_timed, checkpoints_req FROM pg_stat_bgwriter;
```

### `health.bloat`

- id `5` · 类型 `SQL` · 会话 **只读** · is_valid `1` · 异步 `0`

| 参数 | 类型 |
|---|---|
| `limit` | INTEGER |

```sql
SELECT t.schemaname, t.relname, t.n_live_tup, t.n_dead_tup,
       EXTRACT(EPOCH FROM (now()-t.last_autovacuum)) AS last_autovacuum_age_s,
       CASE WHEN 'autovacuum_enabled=false' = ANY(c.reloptions) THEN false ELSE true END AS autovac_enabled
FROM pg_stat_user_tables t
JOIN pg_class c ON c.oid = t.relid
WHERE t.n_dead_tup > 0
  AND t.schemaname NOT IN ('pg_catalog','information_schema','snapshot','dbe_perf','dbe_pldeveloper','cstore')
ORDER BY t.n_dead_tup::numeric/GREATEST(t.n_live_tup+t.n_dead_tup,1) DESC
LIMIT {{limit}};
```

### `health.conn_concentration`

- id `6` · 类型 `SQL` · 会话 **只读** · is_valid `1` · 异步 `0`

无参数

```sql
SELECT COALESCE(query,'') q, count(*) c, sum(count(*)) OVER () AS total
FROM pg_stat_activity
WHERE state='active' AND COALESCE(query,'')<>'' AND COALESCE(connection_info,'')<>''
GROUP BY query ORDER BY c DESC LIMIT 1;
```

### `health.conn_states`

- id `7` · 类型 `SQL` · 会话 **只读** · is_valid `1` · 异步 `0`

无参数

```sql
SELECT COALESCE(state,'<null>') AS state, count(*) AS cnt
FROM pg_stat_activity
GROUP BY state
ORDER BY cnt DESC;
```

### `health.db_concurrency`

- id `8` · 类型 `SQL` · 会话 **只读** · is_valid `1` · 异步 `0`

无参数

```sql
SELECT deadlocks, xact_commit, xact_rollback
FROM pg_stat_database WHERE datname=current_database();
```

### `health.db_info`

- id `9` · 类型 `SQL` · 会话 **只读** · is_valid `1` · 异步 `0`

无参数

```sql
select pg_encoding_to_char(encoding) as encoding_name, *
from pg_database
where datname not in ('template1','postgres','template0');
```

### `health.invalid_index`

- id `10` · 类型 `SQL` · 会话 **只读** · is_valid `1` · 异步 `0`

无参数

```sql
SELECT count(*) AS cnt FROM pg_index WHERE NOT indisvalid;
```

### `health.lock_chain`

- id `11` · 类型 `SQL` · 会话 **只读** · is_valid `1` · 异步 `0`

| 参数 | 类型 |
|---|---|
| `limit` | INTEGER |

```sql
SELECT w.sessionid AS waiter_session,
       w.tid AS waiter_tid,
       COALESCE(w.wait_status, '') AS wait_status,
       COALESCE(w.wait_event, '') AS wait_event,
       COALESCE(w.lockmode, '') AS lockmode,
       COALESCE(w.locktag, '') AS locktag,
       w.block_sessionid AS blocker_session,
       COALESCE(b.state, '') AS blocker_state,
       COALESCE(b.usename, '') AS blocker_user,
       COALESCE(b.application_name, '') AS blocker_app,
       COALESCE(EXTRACT(EPOCH FROM (now() - b.xact_start)), 0) AS blocker_xact_age_s,
       COALESCE(EXTRACT(EPOCH FROM (now() - b.state_change)), 0) AS blocker_state_age_s,
       COALESCE(substr(b.query, 1, 200), '') AS blocker_query,
       COALESCE(substr(a.query, 1, 200), '') AS waiter_query
FROM pg_thread_wait_status w
LEFT JOIN pg_stat_activity b ON b.sessionid = w.block_sessionid
LEFT JOIN pg_stat_activity a ON a.sessionid = w.sessionid
WHERE w.block_sessionid IS NOT NULL
  AND w.block_sessionid <> 0
  AND w.block_sessionid <> w.sessionid
ORDER BY blocker_xact_age_s DESC
LIMIT {{limit}};
```

### `health.long_xact`

- id `12` · 类型 `SQL` · 会话 **只读** · is_valid `1` · 异步 `0`

| 参数 | 类型 |
|---|---|
| `limit` | INTEGER |

```sql
SELECT pid,
       COALESCE(usename,'') AS usename,
       state,
       EXTRACT(EPOCH FROM (now()-xact_start))   AS xact_age_s,
       EXTRACT(EPOCH FROM (now()-state_change)) AS state_age_s,
       COALESCE(query,'') AS query
FROM pg_stat_activity
WHERE state IN ('active','idle in transaction') AND xact_start IS NOT NULL
  AND COALESCE(connection_info,'') <> ''
ORDER BY xact_start
LIMIT {{limit}};
```

### `health.lwlock`

- id `13` · 类型 `SQL` · 会话 **只读** · is_valid `1` · 异步 `0`

| 参数 | 类型 |
|---|---|
| `limit` | INTEGER |

```sql
SELECT COALESCE(wait_event,'<lwlock>') AS evt, count(*) AS cnt
FROM pg_thread_wait_status
WHERE lower(wait_status) LIKE '%lwlock%'
GROUP BY wait_event
ORDER BY cnt DESC
LIMIT {{limit}};
```

### `health.overview`

- id `14` · 类型 `SQL` · 会话 **只读** · is_valid `1` · 异步 `0`

无参数

```sql
SELECT
  CASE WHEN sum(blks_hit)+sum(blks_read)=0 THEN 100
       ELSE round(100.0*sum(blks_hit)/(sum(blks_hit)+sum(blks_read)),2) END AS cache_hit_pct,
  sum(numbackends)::bigint AS numbackends,
  (SELECT setting::bigint FROM pg_settings WHERE name='max_connections') AS max_conn,
  pg_is_in_recovery() AS in_recovery,
  (SELECT COALESCE(EXTRACT(EPOCH FROM now()-min(xact_start)),0)::bigint
   FROM pg_stat_activity
   WHERE state IN ('active','idle in transaction') AND xact_start IS NOT NULL
     AND COALESCE(connection_info,'')<>'') AS oldest_xact_s
FROM pg_stat_database;
```

### `health.prepared_xacts`

- id `15` · 类型 `SQL` · 会话 **只读** · is_valid `1` · 异步 `0`

无参数

```sql
SELECT count(*) AS cnt FROM pg_prepared_xacts;
```

### `health.replication`

- id `16` · 类型 `SQL` · 会话 **只读** · is_valid `1` · 异步 `0`

无参数

```sql
SELECT application_name,
       COALESCE(client_addr::text,'') AS client_addr,
       state, sync_state,
       pg_xlog_location_diff(sender_sent_location, receiver_replay_location)::bigint AS lag_bytes
FROM pg_stat_replication;
```

### `health.slow_sql`

- id `17` · 类型 `SQL` · 会话 **只读** · is_valid `1` · 异步 `0`

| 参数 | 类型 |
|---|---|
| `threshold_ms` | INTEGER |
| `limit` | INTEGER |

```sql
SELECT
  unique_sql_id::text,
  LEFT(REGEXP_REPLACE(query, '\s+', ' ', 'g'), 180) AS query,
  n_calls AS calls,
  ROUND((total_elapse_time/NULLIF(n_calls,0))/1000::numeric, 2) AS avg_ms,
  ROUND(total_elapse_time/1000000::numeric, 2) AS total_sec,
  ROUND(cpu_time/1000000::numeric, 2) AS cpu_sec,
  n_returned_rows AS rows
FROM dbe_perf.statement
WHERE (total_elapse_time/NULLIF(n_calls,0))/1000 > {{threshold_ms}}
  AND n_calls > 0
ORDER BY total_elapse_time/NULLIF(n_calls,0) DESC
LIMIT {{limit}};
```

### `health.stale_stats`

- id `18` · 类型 `SQL` · 会话 **只读** · is_valid `1` · 异步 `0`

| 参数 | 类型 |
|---|---|
| `min_rows` | INTEGER |
| `schema_filter` | STRING |
| `limit` | INTEGER |

```sql
SELECT n.nspname || '.' || c.relname AS tbl_name,
       c.relpages AS frozen_pages,
       pg_relation_size(c.oid) / current_setting('block_size')::bigint AS cur_pages,
       c.reltuples::bigint AS frozen_tuples,
       COALESCE(t.n_live_tup, 0) AS live_tuples,
       (SELECT count(*) FROM pg_stats s
         WHERE s.schemaname = n.nspname AND s.tablename = c.relname) AS stat_columns,
       COALESCE(to_char(t.last_analyze, 'YYYY-MM-DD HH24:MI:SS'), 'never') AS last_analyze,
       COALESCE(to_char(t.last_autoanalyze, 'YYYY-MM-DD HH24:MI:SS'), 'never') AS last_autoanalyze
FROM pg_class c
JOIN pg_namespace n ON n.oid = c.relnamespace
LEFT JOIN pg_stat_user_tables t ON t.relid = c.oid
WHERE c.relkind = 'r' AND n.nspname NOT IN {{schema_filter}}
  AND c.reltuples > {{min_rows}}
ORDER BY c.relpages DESC LIMIT {{limit}};
```

### `health.stats_window`

- id `19` · 类型 `SQL` · 会话 **只读** · is_valid `1` · 异步 `0`

无参数

```sql
SELECT COALESCE(to_char(stats_reset, 'YYYY-MM-DD HH24:MI:SS'), 'never') AS stats_reset,
       COALESCE(EXTRACT(EPOCH FROM (now() - stats_reset)), -1) AS window_seconds
FROM pg_stat_database
WHERE datname = current_database();
```

### `health.unused_index`

- id `20` · 类型 `SQL` · 会话 **只读** · is_valid `1` · 异步 `0`

| 参数 | 类型 |
|---|---|
| `min_bytes` | INTEGER |
| `schema_filter` | STRING |
| `limit` | INTEGER |

```sql
SELECT s.schemaname||'.'||s.indexrelname AS idx_name,
       s.relname AS table_name,
       s.idx_scan,
       s.idx_tup_read,
       s.idx_tup_fetch,
       pg_relation_size(s.indexrelid) AS idx_bytes,
       i.indisprimary,
       i.indisunique,
       COALESCE(ts.seq_scan, 0) AS table_seq_scan,
       COALESCE(ts.idx_scan, 0) AS table_idx_scan
FROM pg_stat_user_indexes s
JOIN pg_index i ON i.indexrelid = s.indexrelid
LEFT JOIN pg_stat_user_tables ts ON ts.relid = s.relid
WHERE pg_relation_size(s.indexrelid) > {{min_bytes}}
  AND s.schemaname NOT IN {{schema_filter}}
ORDER BY s.idx_scan ASC, pg_relation_size(s.indexrelid) DESC
LIMIT {{limit}};
```

### `health.waits`

- id `21` · 类型 `SQL` · 会话 **只读** · is_valid `1` · 异步 `0`

无参数

```sql
SELECT wait_status, count(*) AS cnt
FROM pg_thread_wait_status
WHERE wait_status IS NOT NULL AND wait_status NOT IN ('none','wait cmd')
GROUP BY wait_status
ORDER BY cnt DESC;
```

### `lockwait.chain`

- id `22` · 类型 `SQL` · 会话 **只读** · is_valid `1` · 异步 `0`

无参数

```sql
SELECT w.sessionid       AS sessionid,
       w.block_sessionid AS block_sessionid
  FROM pg_thread_wait_status w
 WHERE w.block_sessionid IS NOT NULL
   AND w.block_sessionid <> 0
   AND w.block_sessionid <> w.sessionid;
```

### `lockwait.pairs`

- id `23` · 类型 `SQL` · 会话 **只读** · is_valid `1` · 异步 `0`

| 参数 | 类型 |
|---|---|
| `limit` | INTEGER |

```sql
SELECT w.pid                         AS waiter_pid,
       COALESCE(w.sessionid, 0)      AS waiter_sessionid,
       w.mode                        AS waiter_mode,
       h.pid                         AS holder_pid,
       COALESCE(h.sessionid, 0)      AS holder_sessionid,
       h.mode                        AS holder_mode,
       w.locktype                    AS locktype,
       COALESCE(n.nspname || '.' || c.relname, '')  AS lock_object,
       COALESCE(w.locktag, '')       AS locktag,
       round(EXTRACT(EPOCH FROM (now() - wa.query_start))::numeric, 1) AS waiter_wait_s,
       COALESCE(wa.usename, '')      AS waiter_user,
       COALESCE(wa.application_name, '') AS waiter_app,
       COALESCE(substr(wa.query, 1, 300), '')       AS waiter_query,
       COALESCE(ha.state, '')        AS holder_state,
       COALESCE(ha.usename, '')      AS holder_user,
       COALESCE(ha.application_name, '') AS holder_app,
       round(EXTRACT(EPOCH FROM (now() - ha.xact_start))::numeric, 1) AS holder_xact_age_s,
       COALESCE(substr(ha.query, 1, 300), '')       AS holder_query
  FROM pg_locks w
  JOIN pg_locks h
    ON h.locktag = w.locktag AND h.granted AND h.pid <> w.pid
  LEFT JOIN pg_class c     ON c.oid = w.relation
  LEFT JOIN pg_namespace n ON n.oid = c.relnamespace
  LEFT JOIN pg_stat_activity wa ON wa.pid = w.pid
  LEFT JOIN pg_stat_activity ha ON ha.pid = h.pid
 WHERE w.granted = false
 ORDER BY waiter_wait_s DESC NULLS FIRST -- 未知时长（见上）排最前，不许沉底
 LIMIT {{limit}};
```

### `memanalyze.activity`

- id `24` · 类型 `SQL` · 会话 **只读** · is_valid `1` · 异步 `0`

无参数

```sql
SELECT sessionid, pid, usename, application_name, state, query FROM pg_stat_activity;
```

### `memanalyze.cols_bare`

- id `25` · 类型 `SQL` · 会话 **只读** · is_valid `1` · 异步 `0`

| 参数 | 类型 |
|---|---|
| `relname` | STRING |
| `schemas` | STRING |

```sql
SELECT a.attname::text AS attname
FROM pg_class c
JOIN pg_namespace n ON n.oid = c.relnamespace
JOIN pg_attribute a ON a.attrelid = c.oid
WHERE c.relname = '{{relname}}' AND n.nspname IN ({{schemas}})
  AND a.attnum > 0 AND NOT a.attisdropped
ORDER BY a.attnum;
```

### `memanalyze.cols_qualified`

- id `26` · 类型 `SQL` · 会话 **只读** · is_valid `1` · 异步 `0`

| 参数 | 类型 |
|---|---|
| `schema` | STRING |
| `relname` | STRING |

```sql
SELECT a.attname::text AS attname
FROM pg_class c
JOIN pg_namespace n ON n.oid = c.relnamespace
JOIN pg_attribute a ON a.attrelid = c.oid
WHERE n.nspname = '{{schema}}' AND c.relname = '{{relname}}'
  AND a.attnum > 0 AND NOT a.attisdropped
ORDER BY a.attnum;
```

### `memanalyze.context`

- id `27` · 类型 `SQL` · 会话 **只读** · is_valid `1` · 异步 `0`

| 参数 | 类型 |
|---|---|
| `limit` | INTEGER |

```sql
SELECT contextname, sum(totalsize) AS totalsize,
       sum(freesize) AS freesize, sum(usedsize) AS usedsize
FROM (SELECT contextname, totalsize, freesize, usedsize FROM gs_session_memory_detail) t
GROUP BY contextname ORDER BY 4 DESC NULLS LAST
LIMIT {{limit}};
```

### `memanalyze.gucs`

- id `28` · 类型 `SQL` · 会话 **只读** · is_valid `1` · 异步 `0`

无参数

```sql
SELECT name, setting
FROM pg_settings
WHERE name IN (
  'max_process_memory', 'shared_buffers', 'work_mem', 'maintenance_work_mem',
  'max_connections', 'enable_memory_limit', 'memory_tracking_mode',
  'use_workload_manager', 'enable_resource_track', 'resource_track_level',
  'resource_track_cost', 'resource_track_duration',
  'enable_dynamic_workload', 'query_max_mem', 'query_mem')
ORDER BY name;
```

### `memanalyze.instance`

- id `29` · 类型 `SQL` · 会话 **只读** · is_valid `1` · 异步 `0`

无参数

```sql
SELECT memorytype, memorymbytes FROM gs_total_memory_detail;
```

### `memanalyze.session`

- id `30` · 类型 `SQL` · 会话 **只读** · is_valid `1` · 异步 `0`

| 参数 | 类型 |
|---|---|
| `limit` | INTEGER |

```sql
SELECT sessid, init_mem, used_mem, peak_mem FROM dbe_perf.session_memory
ORDER BY peak_mem DESC NULLS LAST LIMIT {{limit}};
```

### `memanalyze.wlm_operator`

- id `31` · 类型 `SQL` · 会话 **只读** · is_valid `1` · 异步 `0`

| 参数 | 类型 |
|---|---|
| `limit` | INTEGER |

```sql
SELECT queryid, plan_node_id, plan_node_name, duration, NULL AS estimate_memory, NULL AS memory_used, max_peak_memory, average_peak_memory, NULL AS spill_size, warning FROM gs_wlm_operator_statistics
ORDER BY max_peak_memory DESC NULLS LAST LIMIT {{limit}};
```

### `memanalyze.wlm_operator_hist`

- id `32` · 类型 `SQL` · 会话 **只读** · is_valid `1` · 异步 `0`

| 参数 | 类型 |
|---|---|
| `limit` | INTEGER |

```sql
SELECT queryid, plan_node_id, plan_node_name, duration, NULL AS estimate_memory, NULL AS memory_used, max_peak_memory, average_peak_memory, NULL AS spill_size, warning FROM gs_wlm_operator_history
ORDER BY max_peak_memory DESC NULLS LAST LIMIT {{limit}};
```

### `memanalyze.wlm_sql`

- id `33` · 类型 `SQL` · 会话 **只读** · is_valid `1` · 异步 `0`

| 参数 | 类型 |
|---|---|
| `limit` | INTEGER |

```sql
SELECT queryid, query, start_time, duration, estimate_memory, NULL AS used_memory, max_peak_memory, average_peak_memory, spill_info FROM gs_wlm_session_statistics
ORDER BY max_peak_memory DESC NULLS LAST LIMIT {{limit}};
```

### `memanalyze.wlm_sql_hist`

- id `34` · 类型 `SQL` · 会话 **只读** · is_valid `1` · 异步 `0`

| 参数 | 类型 |
|---|---|
| `limit` | INTEGER |

```sql
SELECT queryid, query, start_time, duration, estimate_memory, NULL AS used_memory, max_peak_memory, average_peak_memory, spill_info FROM gs_wlm_session_history
ORDER BY max_peak_memory DESC NULLS LAST LIMIT {{limit}};
```

### `perf.bgwriter`

- id `35` · 类型 `SQL` · 会话 **只读** · is_valid `1` · 异步 `0`

无参数

```sql
select checkpoints_timed, checkpoints_req,
       checkpoint_write_time, checkpoint_sync_time,
       buffers_checkpoint, buffers_clean, maxwritten_clean,
       buffers_backend, buffers_backend_fsync, buffers_alloc,
       to_char(stats_reset,'YYYY-MM-DD HH24:MI:SS') as stats_reset
from pg_stat_bgwriter;
```

### `perf.db_stat`

- id `36` · 类型 `SQL` · 会话 **只读** · is_valid `1` · 异步 `0`

无参数

```sql
select datname, numbackends, xact_commit, xact_rollback,
       blks_read, blks_hit,
       round(blks_hit*100.0/nullif(blks_hit+blks_read,0), 2) as hit_ratio,
       tup_returned, tup_fetched, deadlocks, conflicts
from pg_stat_database
order by xact_commit desc;
```

### `perf.instance_time`

- id `37` · 类型 `SQL` · 会话 **只读** · is_valid `1` · 异步 `0`

无参数

```sql
select stat_name, value
from dbe_perf.global_instance_time
order by value desc;
```

### `perf.locks`

- id `38` · 类型 `SQL` · 会话 **只读** · is_valid `1` · 异步 `0`

| 参数 | 类型 |
|---|---|
| `limit` | INTEGER |

```sql
select l.locktype, l.mode, l.granted,
       l.pid::text, l.sessionid::text,
       coalesce(c.relname, '-') as relname,
       coalesce(a.usename, '-') as usename
from pg_locks l
left join pg_class c on c.oid = l.relation
left join pg_stat_activity a on a.sessionid = l.sessionid
order by l.granted, l.locktype
limit {{limit}};
```

### `perf.memory`

- id `39` · 类型 `SQL` · 会话 **只读** · is_valid `1` · 异步 `0`

| 参数 | 类型 |
|---|---|
| `limit` | INTEGER |

```sql
select nodename, memorytype, memorymbytes
from dbe_perf.memory_node_detail
order by memorymbytes desc
limit {{limit}};
```

### `perf.sessions`

- id `40` · 类型 `SQL` · 会话 **只读** · is_valid `1` · 异步 `0`

| 参数 | 类型 |
|---|---|
| `limit` | INTEGER |

```sql
select sessionid::text, usename, datname, application_name, state,
       to_char(backend_start,'YYYY-MM-DD HH24:MI:SS') as backend_start,
       to_char(query_start,'YYYY-MM-DD HH24:MI:SS') as query_start,
       waiting,
       left(regexp_replace(query, '\s+', ' ', 'g'), 120) as query
from pg_stat_activity
where state <> 'idle'
order by query_start nulls last
limit {{limit}};
```

### `perf.table_stat`

- id `41` · 类型 `SQL` · 会话 **只读** · is_valid `1` · 异步 `0`

| 参数 | 类型 |
|---|---|
| `limit` | INTEGER |

```sql
select schemaname||'.'||relname as tbl, seq_scan, idx_scan,
       n_live_tup, n_dead_tup,
       round(n_dead_tup*100.0/nullif(n_live_tup+n_dead_tup,0), 2) as dead_pct,
       to_char(last_autovacuum,'YYYY-MM-DD HH24:MI:SS') as last_autovacuum
from pg_stat_user_tables
where n_live_tup > 0
order by n_dead_tup desc
limit {{limit}};
```

### `perf.wait_events`

- id `42` · 类型 `SQL` · 会话 **只读** · is_valid `1` · 异步 `0`

| 参数 | 类型 |
|---|---|
| `limit` | INTEGER |

```sql
select type, event, wait, total_wait_time, avg_wait_time, max_wait_time
from dbe_perf.wait_events
where wait > 0
order by total_wait_time desc
limit {{limit}};
```

### `perf.wait_status`

- id `43` · 类型 `SQL` · 会话 **只读** · is_valid `1` · 异步 `0`

| 参数 | 类型 |
|---|---|
| `limit` | INTEGER |

```sql
select thread_name, wait_status, wait_event, db_name,
       sessionid::text, block_sessionid::text
from pg_thread_wait_status
where wait_status <> 'none'
limit {{limit}};
```

### `procinfo.key_gucs`

- id `44` · 类型 `SQL` · 会话 **只读** · is_valid `1` · 异步 `0`

无参数

```sql
SELECT name, setting, COALESCE(unit, '') AS unit
FROM pg_settings
WHERE name IN (
  'work_mem', 'maintenance_work_mem', 'shared_buffers',
  'effective_cache_size', 'random_page_cost', 'seq_page_cost',
  'cpu_tuple_cost', 'cpu_index_tuple_cost', 'cpu_operator_cost', 'block_size',
  'query_dop',
  'max_parallel_workers_per_gather', 'from_collapse_limit',
  'join_collapse_limit', 'geqo_threshold', 'default_statistics_target')
ORDER BY name;
```

### `procinfo.proc_def`

- id `45` · 类型 `SQL` · 会话 **只读** · is_valid `1` · 异步 `0`

| 参数 | 类型 |
|---|---|
| `name` | STRING |
| `schema` | STRING |

```sql
SELECT n.nspname, p.proname, l.lanname, p.prosrc,
       pg_catalog.pg_get_function_arguments(p.oid) AS args
FROM pg_proc p
JOIN pg_namespace n ON n.oid = p.pronamespace
JOIN pg_language l ON l.oid = p.prolang
WHERE p.proname = '{{name}}' AND ('{{schema}}' = '' OR n.nspname = '{{schema}}')
ORDER BY (n.nspname = 'public') DESC, n.nspname
LIMIT 1;
```

### `proctune.column_stats`

- id `46` · 类型 `SQL` · 会话 **只读** · is_valid `1` · 异步 `0`

| 参数 | 类型 |
|---|---|
| `names` | STRING |

```sql
SELECT tablename, attname, n_distinct, null_frac, avg_width, correlation,
       COALESCE(most_common_vals::text, '') AS most_common_vals,
       COALESCE(most_common_freqs::text, '') AS most_common_freqs,
       COALESCE(histogram_bounds::text, '') AS histogram_bounds
FROM pg_stats
WHERE tablename IN ({{names}});
```

### `proctune.db_version`

- id `47` · 类型 `SQL` · 会话 **只读** · is_valid `1` · 异步 `0`

无参数

```sql
SELECT version() AS version;
```

### `proctune.indexes`

- id `48` · 类型 `SQL` · 会话 **只读** · is_valid `1` · 异步 `0`

| 参数 | 类型 |
|---|---|
| `names` | STRING |

```sql
SELECT t.relname AS table_name, i.relname AS index_name,
       ix.indisunique, ix.indisprimary,
       pg_get_indexdef(ix.indexrelid) AS index_def
FROM pg_class t
JOIN pg_index ix ON t.oid = ix.indrelid
JOIN pg_class i ON i.oid = ix.indexrelid
WHERE t.relname IN ({{names}});
```

### `proctune.key_gucs`

- id `49` · 类型 `SQL` · 会话 **只读** · is_valid `1` · 异步 `0`

无参数

```sql
SELECT name, setting, COALESCE(unit, '') AS unit
FROM pg_settings
WHERE name IN (
  'work_mem', 'maintenance_work_mem', 'shared_buffers',
  'effective_cache_size', 'random_page_cost', 'seq_page_cost',
  'cpu_tuple_cost', 'cpu_index_tuple_cost', 'cpu_operator_cost', 'block_size',
  'query_dop',
  'max_parallel_workers_per_gather', 'from_collapse_limit',
  'join_collapse_limit', 'geqo_threshold', 'default_statistics_target')
ORDER BY name;
```

### `proctune.plan_text`

- id `50` · 类型 `SQL` · 会话 **只读** · is_valid `1` · 异步 `0`

| 参数 | 类型 |
|---|---|
| `sql` | STRING |

```sql
EXPLAIN (ANALYZE false, BUFFERS false, FORMAT TEXT) {{sql}}
```

### `proctune.plan_text_analyze`

- id `51` · 类型 `SQL` · 会话 **只读** · is_valid `1` · 异步 `0`

| 参数 | 类型 |
|---|---|
| `sql` | STRING |

```sql
EXPLAIN (ANALYZE true, BUFFERS true, FORMAT TEXT) {{sql}}
```

### `proctune.proc_def`

- id `52` · 类型 `SQL` · 会话 **只读** · is_valid `1` · 异步 `0`

| 参数 | 类型 |
|---|---|
| `name` | STRING |
| `schema` | STRING |

```sql
SELECT n.nspname, p.proname, l.lanname, p.prosrc,
       pg_catalog.pg_get_function_arguments(p.oid) AS args
FROM pg_proc p
JOIN pg_namespace n ON n.oid = p.pronamespace
JOIN pg_language l ON l.oid = p.prolang
WHERE p.proname = '{{name}}' AND ('{{schema}}' = '' OR n.nspname = '{{schema}}')
ORDER BY (n.nspname = 'public') DESC, n.nspname
LIMIT 1;
```

### `proctune.sql_from_history`

- id `53` · 类型 `SQL` · 会话 **只读** · is_valid `1` · 异步 `0`

| 参数 | 类型 |
|---|---|
| `sid` | INTEGER |

```sql
SELECT schema_name, query
FROM dbe_perf.statement_history
WHERE unique_query_id = {{sid}}
  AND query NOT LIKE '/* missing SQL statement%'
ORDER BY start_time DESC
LIMIT 1;
```

### `proctune.sql_from_statement`

- id `54` · 类型 `SQL` · 会话 **只读** · is_valid `1` · 异步 `0`

| 参数 | 类型 |
|---|---|
| `sid` | INTEGER |

```sql
SELECT query FROM dbe_perf.statement
WHERE unique_sql_id = {{sid}}
  AND query IS NOT NULL
  AND LENGTH(TRIM(query)) > 0
LIMIT 1;
```

### `proctune.tables`

- id `55` · 类型 `SQL` · 会话 **只读** · is_valid `1` · 异步 `0`

| 参数 | 类型 |
|---|---|
| `names` | STRING |

```sql
SELECT n.nspname, c.relname, c.relpages,
       c.reltuples::bigint AS reltuples,
       pg_relation_size(c.oid) / current_setting('block_size')::bigint AS curpages,
       c.relkind,
       pg_total_relation_size(c.oid) / 1024.0 / 1024.0 AS size_mb
FROM pg_class c
LEFT JOIN pg_namespace n ON c.relnamespace = n.oid
WHERE c.relname IN ({{names}}) AND c.relkind IN ('r','v','p','m');
```

### `session.active_only`

- id `56` · 类型 `SQL` · 会话 **只读** · is_valid `1` · 异步 `0`

| 参数 | 类型 |
|---|---|
| `active_only` | BOOLEAN |
| `limit` | INTEGER |

```sql
select pid, usename, state, application_name
from pg_stat_activity
where ({{active_only}} = false or state = 'active')
limit {{limit}};
```

### `session.by_user`

- id `57` · 类型 `SQL` · 会话 **只读** · is_valid `1` · 异步 `0`

| 参数 | 类型 |
|---|---|
| `username` | STRING |

```sql
select pid, usename, state, application_name
from pg_stat_activity
where usename = '{{username}}';
```

### `session.top_by`

- id `58` · 类型 `SQL` · 会话 **只读** · is_valid `1` · 异步 `0`

| 参数 | 类型 |
|---|---|
| `sort_col` | STRING |
| `limit` | INTEGER |

```sql
select datname, usename, state, backend_start
from pg_stat_activity
order by {{sort_col}} desc
limit {{limit}};
```

### `slowsql.slow_sql`

- id `59` · 类型 `SQL` · 会话 **只读** · is_valid `1` · 异步 `0`

| 参数 | 类型 |
|---|---|
| `threshold_ms` | INTEGER |
| `begin_time` | DATETIME |
| `limit` | INTEGER |

```sql
SELECT
  unique_sql_id::text,
  LEFT(REGEXP_REPLACE(query, '\s+', ' ', 'g'), 180) AS query,
  n_calls AS calls,
  ROUND((total_elapse_time/NULLIF(n_calls,0))/1000::numeric, 2) AS avg_ms,
  ROUND(total_elapse_time/1000000::numeric, 2) AS total_sec,
  ROUND(cpu_time/1000000::numeric, 2) AS cpu_sec,
  n_returned_rows AS rows
FROM dbe_perf.statement
WHERE (total_elapse_time/NULLIF(n_calls,0))/1000 > {{threshold_ms}}
  AND n_calls > 0 AND last_updated >= CAST('{{begin_time}}' AS TIMESTAMP)
ORDER BY total_elapse_time/NULLIF(n_calls,0) DESC
LIMIT {{limit}};
```

### `sqlfetch.from_history`

- id `60` · 类型 `SQL` · 会话 **只读** · is_valid `1` · 异步 `0`

| 参数 | 类型 |
|---|---|
| `sid` | INTEGER |

```sql
SELECT schema_name, query
FROM dbe_perf.statement_history
WHERE unique_query_id = {{sid}}
  AND query NOT LIKE '/* missing SQL statement%'
ORDER BY start_time DESC
LIMIT 1;
```

### `sqlfetch.from_statement`

- id `61` · 类型 `SQL` · 会话 **只读** · is_valid `1` · 异步 `0`

| 参数 | 类型 |
|---|---|
| `sid` | INTEGER |

```sql
SELECT query FROM dbe_perf.statement
WHERE unique_sql_id = {{sid}}
  AND query IS NOT NULL
  AND LENGTH(TRIM(query)) > 0
LIMIT 1;
```

### `sqlreview.from_history`

- id `62` · 类型 `SQL` · 会话 **只读** · is_valid `1` · 异步 `0`

| 参数 | 类型 |
|---|---|
| `sid` | INTEGER |

```sql
SELECT schema_name, query
FROM dbe_perf.statement_history
WHERE unique_query_id = {{sid}}
  AND query NOT LIKE '/* missing SQL statement%'
ORDER BY start_time DESC
LIMIT 1;
```

### `sqlreview.from_statement`

- id `63` · 类型 `SQL` · 会话 **只读** · is_valid `1` · 异步 `0`

| 参数 | 类型 |
|---|---|
| `sid` | INTEGER |

```sql
SELECT query FROM dbe_perf.statement
WHERE unique_sql_id = {{sid}}
  AND query IS NOT NULL
  AND LENGTH(TRIM(query)) > 0
LIMIT 1;
```

### `sqlreview.indexes`

- id `64` · 类型 `SQL` · 会话 **只读** · is_valid `1` · 异步 `0`

| 参数 | 类型 |
|---|---|
| `schema` | STRING |

```sql
SELECT
  n.nspname::text                                              AS schema,
  t.relname::text                                              AS table,
  i.relname::text                                              AS name,
  array_to_string(
    COALESCE((SELECT array_agg(a.attname::text ORDER BY k.ord)
              FROM (SELECT s AS ord,
                           (string_to_array(ix.indkey::text, ' '))[s]::smallint AS attnum
                    FROM generate_series(
                           1,
                           array_length(string_to_array(ix.indkey::text, ' '), 1)) s) k
              JOIN pg_attribute a
                ON a.attrelid = t.oid AND a.attnum = k.attnum),
             ARRAY[]::text[]), ',')                            AS columns,
  ix.indisunique                                               AS is_unique,
  ix.indisprimary                                              AS is_primary,
  COALESCE(s.idx_scan, 0)                                      AS scans
FROM pg_index ix
JOIN pg_class i     ON i.oid = ix.indexrelid
JOIN pg_class t     ON t.oid = ix.indrelid
JOIN pg_namespace n ON n.oid = t.relnamespace
LEFT JOIN pg_stat_user_indexes s ON s.indexrelid = ix.indexrelid
WHERE n.nspname = '{{schema}}'
ORDER BY t.relname, i.relname;
```

### `sqlreview.tables`

- id `65` · 类型 `SQL` · 会话 **只读** · is_valid `1` · 异步 `0`

| 参数 | 类型 |
|---|---|
| `schema` | STRING |

```sql
SELECT
  n.nspname::text                                              AS schema,
  c.relname::text                                              AS table,
  EXISTS (SELECT 1 FROM pg_constraint pk
          WHERE pk.conrelid = c.oid AND pk.contype = 'p')      AS has_pk,
  array_to_string(
    COALESCE((SELECT array_agg(fk.conname::text)
              FROM pg_constraint fk
              WHERE fk.conrelid = c.oid AND fk.contype = 'f'),
             ARRAY[]::text[]), ',')                            AS fks,
  array_to_string(
    COALESCE((SELECT array_agg(a.attname::text ORDER BY a.attnum)
              FROM pg_attribute a
              WHERE a.attrelid = c.oid AND a.attnum > 0 AND NOT a.attisdropped),
             ARRAY[]::text[]), ',')                            AS columns
FROM pg_class c
JOIN pg_namespace n ON n.oid = c.relnamespace
WHERE c.relkind = 'r' AND n.nspname = '{{schema}}'
ORDER BY c.relname;
```

### `sqlreview.top_sql`

- id `66` · 类型 `SQL` · 会话 **只读** · is_valid `1` · 异步 `0`

| 参数 | 类型 |
|---|---|
| `limit` | INTEGER |

```sql
SELECT unique_sql_id::text, query
FROM dbe_perf.statement
WHERE n_calls > 0 AND query IS NOT NULL AND query <> ''
ORDER BY total_elapse_time DESC
LIMIT {{limit}};
```

### `sqltune.column_stats`

- id `67` · 类型 `SQL` · 会话 **只读** · is_valid `1` · 异步 `0`

| 参数 | 类型 |
|---|---|
| `names` | STRING |

```sql
SELECT tablename, attname, n_distinct, null_frac, avg_width, correlation,
       COALESCE(most_common_vals::text, '') AS most_common_vals,
       COALESCE(most_common_freqs::text, '') AS most_common_freqs,
       COALESCE(histogram_bounds::text, '') AS histogram_bounds
FROM pg_stats
WHERE tablename IN ({{names}});
```

### `sqltune.from_history`

- id `68` · 类型 `SQL` · 会话 **只读** · is_valid `1` · 异步 `0`

| 参数 | 类型 |
|---|---|
| `sid` | INTEGER |

```sql
SELECT schema_name, query
FROM dbe_perf.statement_history
WHERE unique_query_id = {{sid}}
  AND query NOT LIKE '/* missing SQL statement%'
ORDER BY start_time DESC
LIMIT 1;
```

### `sqltune.from_statement`

- id `69` · 类型 `SQL` · 会话 **只读** · is_valid `1` · 异步 `0`

| 参数 | 类型 |
|---|---|
| `sid` | INTEGER |

```sql
SELECT query FROM dbe_perf.statement
WHERE unique_sql_id = {{sid}}
  AND query IS NOT NULL
  AND LENGTH(TRIM(query)) > 0
LIMIT 1;
```

### `sqltune.indexes`

- id `70` · 类型 `SQL` · 会话 **只读** · is_valid `1` · 异步 `0`

| 参数 | 类型 |
|---|---|
| `names` | STRING |

```sql
SELECT t.relname AS table_name,
       i.relname AS index_name,
       ix.indisunique,
       ix.indisprimary,
       i.relpages AS index_relpages,
       i.reltuples::bigint AS index_reltuples,
       pg_get_indexdef(ix.indexrelid) AS index_def
FROM pg_class t
JOIN pg_index ix ON t.oid = ix.indrelid
JOIN pg_class i ON i.oid = ix.indexrelid
WHERE t.relname IN ({{names}});
```

### `sqltune.key_gucs`

- id `71` · 类型 `SQL` · 会话 **只读** · is_valid `1` · 异步 `0`

无参数

```sql
SELECT name, setting, COALESCE(unit, '') AS unit
FROM pg_settings
WHERE name IN (
  'work_mem', 'maintenance_work_mem', 'shared_buffers',
  'effective_cache_size', 'random_page_cost', 'seq_page_cost',
  'cpu_tuple_cost', 'cpu_index_tuple_cost', 'cpu_operator_cost', 'block_size',
  'query_dop',
  'max_parallel_workers_per_gather', 'from_collapse_limit',
  'join_collapse_limit', 'geqo_threshold', 'default_statistics_target')
ORDER BY name;
```

### `sqltune.plan_json`

- id `72` · 类型 `SQL` · 会话 **只读** · is_valid `1` · 异步 `0`

| 参数 | 类型 |
|---|---|
| `sql` | STRING |

```sql
EXPLAIN (ANALYZE false, BUFFERS false, FORMAT JSON) {{sql}}
```

### `sqltune.plan_text`

- id `73` · 类型 `SQL` · 会话 **只读** · is_valid `1` · 异步 `0`

| 参数 | 类型 |
|---|---|
| `sql` | STRING |

```sql
EXPLAIN (ANALYZE false, BUFFERS false, FORMAT TEXT) {{sql}}
```

### `sqltune.plan_text_analyze`

- id `74` · 类型 `SQL` · 会话 **只读** · is_valid `1` · 异步 `0`

| 参数 | 类型 |
|---|---|
| `sql` | STRING |

```sql
EXPLAIN (ANALYZE true, BUFFERS true, FORMAT TEXT) {{sql}}
```

### `sqltune.stats_freshness`

- id `75` · 类型 `SQL` · 会话 **只读** · is_valid `1` · 异步 `0`

| 参数 | 类型 |
|---|---|
| `names` | STRING |

```sql
SELECT schemaname, relname,
       n_live_tup, n_dead_tup,
       COALESCE(to_char(last_analyze, 'YYYY-MM-DD HH24:MI:SS'), 'never') AS last_analyze,
       COALESCE(to_char(last_autoanalyze, 'YYYY-MM-DD HH24:MI:SS'), 'never') AS last_autoanalyze,
       analyze_count, autoanalyze_count
FROM pg_stat_user_tables
WHERE relname IN ({{names}});
```

### `sqltune.tables`

- id `76` · 类型 `SQL` · 会话 **只读** · is_valid `1` · 异步 `0`

| 参数 | 类型 |
|---|---|
| `names` | STRING |

```sql
SELECT n.nspname, c.relname, c.relpages,
       c.reltuples::bigint AS reltuples,
       pg_relation_size(c.oid) / current_setting('block_size')::bigint AS curpages,
       c.relkind,
       pg_total_relation_size(c.oid) / 1024.0 / 1024.0 AS size_mb
FROM pg_class c
LEFT JOIN pg_namespace n ON c.relnamespace = n.oid
WHERE c.relname IN ({{names}}) AND c.relkind IN ('r','v','p','m');
```

### `sqltune.version`

- id `77` · 类型 `SQL` · 会话 **只读** · is_valid `1` · 异步 `0`

无参数

```sql
SELECT version() AS version;
```

### `topproc.top_procs`

- id `78` · 类型 `SQL` · 会话 **只读** · is_valid `1` · 异步 `0`

| 参数 | 类型 |
|---|---|
| `order` | STRING |
| `limit` | INTEGER |

```sql
SELECT n.nspname, p.proname, s.calls,
       ROUND(s.total_time::numeric, 2) AS total_ms,
       ROUND(s.self_time::numeric, 2) AS self_ms
FROM pg_stat_user_functions s
JOIN pg_proc p ON p.oid = s.funcid
JOIN pg_namespace n ON n.oid = p.pronamespace
ORDER BY {{order}} NULLS LAST
LIMIT {{limit}};
```

### `topsql.top_sql`

- id `79` · 类型 `SQL` · 会话 **只读** · is_valid `1` · 异步 `0`

| 参数 | 类型 |
|---|---|
| `order` | STRING |
| `limit` | INTEGER |

```sql
SELECT
  unique_sql_id::text,
  LEFT(REGEXP_REPLACE(query, '\s+', ' ', 'g'), 80) AS query,
  n_calls AS calls,
  ROUND(total_elapse_time/1000000::numeric, 2) AS total_sec,
  ROUND((total_elapse_time/NULLIF(n_calls,0))/1000::numeric, 2) AS avg_ms,
  n_returned_rows AS rows
FROM dbe_perf.statement
WHERE n_calls > 0
ORDER BY {{order}}
LIMIT {{limit}};
```

### `waitevent.events`

- id `80` · 类型 `SQL` · 会话 **只读** · is_valid `1` · 异步 `0`

| 参数 | 类型 |
|---|---|
| `b` | INTEGER |
| `e` | INTEGER |
| `top` | INTEGER |

```sql
WITH b AS (SELECT snap_type AS wait_class, snap_event AS event, sum(snap_wait) AS waits, sum(snap_total_wait_time) AS wt
             FROM snapshot.snap_global_wait_events WHERE snapshot_id={{b}} GROUP BY snap_type, snap_event),
     e AS (SELECT snap_type AS wait_class, snap_event AS event, sum(snap_wait) AS waits, sum(snap_total_wait_time) AS wt
             FROM snapshot.snap_global_wait_events WHERE snapshot_id={{e}} GROUP BY snap_type, snap_event)
SELECT e.wait_class,
       e.event,
       SUM(e.waits-b.waits) AS waits,
       SUM(e.wt-b.wt)       AS wait_us
FROM e JOIN b USING (wait_class, event)
WHERE upper(e.wait_class) NOT IN ('STATUS','NONE')
GROUP BY e.wait_class, e.event
HAVING SUM(e.wt-b.wt) > 0
ORDER BY wait_us DESC LIMIT {{top}};
```

### `waitevent.instance_time`

- id `81` · 类型 `SQL` · 会话 **只读** · is_valid `1` · 异步 `0`

| 参数 | 类型 |
|---|---|
| `b` | INTEGER |
| `e` | INTEGER |

```sql
WITH b AS (SELECT snap_stat_name AS stat_name, sum(snap_value) AS v
             FROM snapshot.snap_global_instance_time
            WHERE snapshot_id = {{b}} GROUP BY snap_stat_name),
     e AS (SELECT snap_stat_name AS stat_name, sum(snap_value) AS v
             FROM snapshot.snap_global_instance_time
            WHERE snapshot_id = {{e}} GROUP BY snap_stat_name)
SELECT e.stat_name AS stat_name,
       (e.v - b.v)  AS delta_us
  FROM e JOIN b USING (stat_name)
 ORDER BY delta_us DESC;
```

### `wdr.cache`

- id `82` · 类型 `SQL` · 会话 **只读** · is_valid `1` · 异步 `0`

| 参数 | 类型 |
|---|---|
| `b` | INTEGER |
| `e` | INTEGER |
| `top` | INTEGER |

```sql
WITH b AS (SELECT db_name, snap_schemaname, snap_relname,
                  (COALESCE(snap_heap_blks_read,0)+COALESCE(snap_idx_blks_read,0)) AS phys,
                  (COALESCE(snap_heap_blks_hit,0)+COALESCE(snap_idx_blks_hit,0))   AS logi
             FROM snapshot.snap_summary_statio_all_tables WHERE snapshot_id={{b}}),
     e AS (SELECT db_name, snap_schemaname, snap_relname,
                  (COALESCE(snap_heap_blks_read,0)+COALESCE(snap_idx_blks_read,0)) AS phys,
                  (COALESCE(snap_heap_blks_hit,0)+COALESCE(snap_idx_blks_hit,0))   AS logi
             FROM snapshot.snap_summary_statio_all_tables WHERE snapshot_id={{e}})
SELECT e.snap_relname, (e.phys-b.phys) AS phys_read, (e.logi-b.logi) AS logical_read
FROM e JOIN b USING (db_name, snap_schemaname, snap_relname)
WHERE (e.phys-b.phys) > 0
ORDER BY phys_read DESC LIMIT {{top}};
```

### `wdr.checkpoint`

- id `83` · 类型 `SQL` · 会话 **只读** · is_valid `1` · 异步 `0`

| 参数 | 类型 |
|---|---|
| `b` | INTEGER |
| `e` | INTEGER |

```sql
WITH b AS (SELECT snap_node_name, snap_checkpoints_timed AS timed, snap_checkpoints_req AS req
             FROM snapshot.snap_global_bgwriter_stat WHERE snapshot_id={{b}}),
     e AS (SELECT snap_node_name, snap_checkpoints_timed AS timed, snap_checkpoints_req AS req
             FROM snapshot.snap_global_bgwriter_stat WHERE snapshot_id={{e}})
SELECT COALESCE(SUM(e.timed-b.timed),0) AS checkpoints_timed,
       COALESCE(SUM(e.req-b.req),0)     AS checkpoints_req
FROM e JOIN b USING (snap_node_name);
```

### `wdr.db_stat`

- id `84` · 类型 `SQL` · 会话 **只读** · is_valid `1` · 异步 `0`

| 参数 | 类型 |
|---|---|
| `b` | INTEGER |
| `e` | INTEGER |

```sql
WITH b AS (SELECT snap_datname, snap_xact_commit, snap_xact_rollback, snap_deadlocks,
                  snap_temp_bytes, snap_blks_hit, snap_blks_read
             FROM snapshot.snap_summary_stat_database WHERE snapshot_id={{b}}),
     e AS (SELECT snap_datname, snap_xact_commit, snap_xact_rollback, snap_deadlocks,
                  snap_temp_bytes, snap_blks_hit, snap_blks_read
             FROM snapshot.snap_summary_stat_database WHERE snapshot_id={{e}})
SELECT COALESCE(SUM(e.snap_xact_commit-b.snap_xact_commit),0)     AS xact_commit,
       COALESCE(SUM(e.snap_xact_rollback-b.snap_xact_rollback),0) AS xact_rollback,
       COALESCE(SUM(e.snap_deadlocks-b.snap_deadlocks),0)         AS deadlocks,
       COALESCE(SUM(e.snap_temp_bytes-b.snap_temp_bytes),0)       AS temp_bytes,
       COALESCE(SUM(e.snap_blks_hit-b.snap_blks_hit),0)           AS blks_hit,
       COALESCE(SUM(e.snap_blks_read-b.snap_blks_read),0)         AS blks_read
FROM e JOIN b USING (snap_datname);
```

### `wdr.db_summary`

- id `85` · 类型 `SQL` · 会话 **只读** · is_valid `1` · 异步 `0`

| 参数 | 类型 |
|---|---|
| `b` | INTEGER |
| `e` | INTEGER |

```sql
WITH b AS (SELECT snap_datname, snap_xact_commit, snap_blks_read, snap_blks_hit
             FROM snapshot.snap_summary_stat_database WHERE snapshot_id={{b}}),
     e AS (SELECT snap_datname, snap_xact_commit, snap_blks_read, snap_blks_hit
             FROM snapshot.snap_summary_stat_database WHERE snapshot_id={{e}})
SELECT COALESCE(SUM(e.snap_xact_commit-b.snap_xact_commit),0) AS xact_commit,
       COALESCE(SUM(e.snap_blks_read-b.snap_blks_read),0)     AS blks_read,
       COALESCE(SUM(e.snap_blks_hit-b.snap_blks_hit),0)       AS blks_hit
FROM e JOIN b USING (snap_datname);
```

### `wdr.file_io`

- id `86` · 类型 `SQL` · 会话 **只读** · is_valid `1` · 异步 `0`

| 参数 | 类型 |
|---|---|
| `b` | INTEGER |
| `e` | INTEGER |
| `top` | INTEGER |

```sql
WITH b AS (SELECT snap_filenum, snap_dbid, snap_spcid, snap_phyrds AS reads, snap_phywrts AS writes
             FROM snapshot.snap_summary_file_iostat WHERE snapshot_id={{b}}),
     e AS (SELECT snap_filenum, snap_dbid, snap_spcid, snap_phyrds AS reads, snap_phywrts AS writes
             FROM snapshot.snap_summary_file_iostat WHERE snapshot_id={{e}})
SELECT ('db'||e.snap_dbid||'/spc'||e.snap_spcid||'/f'||e.snap_filenum) AS filename,
       (e.reads-b.reads)   AS reads,
       (e.writes-b.writes) AS writes
FROM e JOIN b USING (snap_filenum, snap_dbid, snap_spcid)
WHERE (e.reads-b.reads) > 0 OR (e.writes-b.writes) > 0
ORDER BY reads DESC LIMIT {{top}};
```

### `wdr.load_profile`

- id `87` · 类型 `SQL` · 会话 **只读** · is_valid `1` · 异步 `0`

| 参数 | 类型 |
|---|---|
| `b` | INTEGER |
| `e` | INTEGER |

```sql
WITH b AS (SELECT snap_unique_sql_id AS sid, sum(snap_total_elapse_time) AS t, sum(snap_cpu_time) AS c
             FROM snapshot.snap_summary_statement WHERE snapshot_id={{b}} GROUP BY snap_unique_sql_id),
     e AS (SELECT snap_unique_sql_id AS sid, sum(snap_total_elapse_time) AS t, sum(snap_cpu_time) AS c
             FROM snapshot.snap_summary_statement WHERE snapshot_id={{e}} GROUP BY snap_unique_sql_id)
SELECT COALESCE(SUM(e.t-b.t),0)  AS db_time_us,
       COALESCE(SUM(e.c-b.c),0)  AS cpu_time_us
FROM e JOIN b USING (sid);
```

### `wdr.native_report`

- id `88` · 类型 `SQL` · 会话 **只读** · is_valid `1` · 异步 `0`

| 参数 | 类型 |
|---|---|
| `begin` | INTEGER |
| `end` | INTEGER |
| `scope` | STRING |
| `node` | STRING |

```sql
SELECT generate_wdr_report({{begin}}, {{end}}, 'all', '{{scope}}', '{{node}}') AS report_line;
```

### `wdr.node_name`

- id `89` · 类型 `SQL` · 会话 **只读** · is_valid `1` · 异步 `0`

无参数

```sql
SHOW pgxc_node_name;
```

### `wdr.snapshots`

- id `90` · 类型 `SQL` · 会话 **只读** · is_valid `1` · 异步 `0`

| 参数 | 类型 |
|---|---|
| `limit` | INTEGER |

```sql
SELECT snapshot_id,
       to_char(start_ts,'YYYY-MM-DD HH24:MI') AS start_ts,
       to_char(end_ts,'YYYY-MM-DD HH24:MI')   AS end_ts,
       round(EXTRACT(EPOCH FROM (end_ts-start_ts))/60)::bigint AS dur_min
FROM snapshot.snapshot ORDER BY snapshot_id DESC LIMIT {{limit}};
```

### `wdr.top_sql`

- id `91` · 类型 `SQL` · 会话 **只读** · is_valid `1` · 异步 `0`

| 参数 | 类型 |
|---|---|
| `b` | INTEGER |
| `e` | INTEGER |
| `top` | INTEGER |

```sql
WITH b AS (SELECT snap_unique_sql_id AS sid, max(snap_query) AS query,
                  sum(snap_n_calls) AS calls, sum(snap_total_elapse_time) AS elapsed, sum(snap_cpu_time) AS cpu,
                  sum(COALESCE(snap_sort_spill_size,0)+COALESCE(snap_hash_spill_size,0)) AS spill,
                  sum(snap_n_blocks_fetched-snap_n_blocks_hit) AS phys
             FROM snapshot.snap_summary_statement WHERE snapshot_id={{b}} GROUP BY snap_unique_sql_id),
     e AS (SELECT snap_unique_sql_id AS sid, max(snap_query) AS query,
                  sum(snap_n_calls) AS calls, sum(snap_total_elapse_time) AS elapsed, sum(snap_cpu_time) AS cpu,
                  sum(COALESCE(snap_sort_spill_size,0)+COALESCE(snap_hash_spill_size,0)) AS spill,
                  sum(snap_n_blocks_fetched-snap_n_blocks_hit) AS phys
             FROM snapshot.snap_summary_statement WHERE snapshot_id={{e}} GROUP BY snap_unique_sql_id)
SELECT e.sid, e.query,
       (e.calls-b.calls)       AS calls,
       (e.elapsed-b.elapsed)   AS elapsed_us,
       (e.cpu-b.cpu)           AS cpu_us,
       (e.spill-b.spill)       AS spill_kb,
       (e.phys-b.phys)         AS phys_blocks
FROM e JOIN b USING (sid)
WHERE (e.elapsed-b.elapsed) > 0
ORDER BY elapsed_us DESC LIMIT {{top}};
```

### `wdr.waits`

- id `92` · 类型 `SQL` · 会话 **只读** · is_valid `1` · 异步 `0`

| 参数 | 类型 |
|---|---|
| `b` | INTEGER |
| `e` | INTEGER |
| `top` | INTEGER |

```sql
WITH b AS (SELECT snap_type AS wait_class, snap_event AS event, sum(snap_wait) AS waits, sum(snap_total_wait_time) AS wt
             FROM snapshot.snap_global_wait_events WHERE snapshot_id={{b}} GROUP BY snap_type, snap_event),
     e AS (SELECT snap_type AS wait_class, snap_event AS event, sum(snap_wait) AS waits, sum(snap_total_wait_time) AS wt
             FROM snapshot.snap_global_wait_events WHERE snapshot_id={{e}} GROUP BY snap_type, snap_event)
SELECT e.wait_class,
       SUM(e.waits-b.waits) AS waits,
       SUM(e.wt-b.wt)       AS wait_us
FROM e JOIN b USING (wait_class, event)
WHERE upper(e.wait_class) NOT IN ('STATUS','NONE')
GROUP BY e.wait_class
HAVING SUM(e.wt-b.wt) > 0
ORDER BY wait_us DESC LIMIT {{top}};
```

### `wdr.wdr_enabled`

- id `93` · 类型 `SQL` · 会话 **只读** · is_valid `1` · 异步 `0`

无参数

```sql
SHOW enable_wdr_snapshot;
```

### `wdr.window`

- id `94` · 类型 `SQL` · 会话 **只读** · is_valid `1` · 异步 `0`

| 参数 | 类型 |
|---|---|
| `begin` | INTEGER |
| `end` | INTEGER |

```sql
SELECT to_char(b.start_ts,'YYYY-MM-DD HH24:MI') AS b_start,
       to_char(e.start_ts,'YYYY-MM-DD HH24:MI') AS e_start,
       round(EXTRACT(EPOCH FROM (e.start_ts-b.start_ts))/60)::bigint AS dur
FROM (SELECT start_ts FROM snapshot.snapshot WHERE snapshot_id={{begin}}) b,
     (SELECT start_ts FROM snapshot.snapshot WHERE snapshot_id={{end}}) e;
```

