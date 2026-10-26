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
| 脚本总数 | 139 |
| id 范围 | 1 ~ 139 |

> `id` 是**环境相关数据，不是契约**。skill 从不持有它 —— 运行时调
> 接口一按 `cmd_name` 现查。客户环境重新发布后 id 会不同，属正常。

## 按命名空间

| 命名空间 | 条数 | 脚本 |
|---|---|---|
| **config** | 1 | `settings` |
| **explain** | 14 | `active_pid`, `from_history`, `from_statement`, `kernel_funcs`, `multi_stmt_probe`, `plan_from_history`, `plan_text`, `plan_text_analyze`, `plan_text_analyze_schema`, `plan_text_schema`, `relation_schemas`, `runtime_plan`, `runtime_plan_int4`, `session_by_pid` |
| **health** | 24 | `archive_mode`, `bgwriter`, `conn_concentration`, `conn_states`, `db_concurrency`, `db_info`, `invalid_index`, `live_counters`, `live_waits`, `long_xact`, `overview`, `partitions`, `prepared_xacts`, `replication`, `slow_sql`, `snap_counters`, `snap_waits`, `stale_stats`, `stats_window`, `storage`, `sync_commit`, `thread_pool`, `unused_index`, `wal_slots` |
| **lockwait** | 5 | `asp_pairs`, `asp_sessions`, `asp_status`, `chain`, `pairs` |
| **memanalyze** | 11 | `activity`, `cols_bare`, `cols_qualified`, `context`, `gucs`, `instance`, `session`, `wlm_operator`, `wlm_operator_hist`, `wlm_sql`, `wlm_sql_hist` |
| **perf** | 9 | `bgwriter`, `db_stat`, `instance_time`, `locks`, `memory`, `sessions`, `table_stat`, `wait_events`, `wait_status` |
| **procinfo** | 5 | `key_gucs`, `locate_context`, `locate_schema`, `locate_search`, `proc_def` |
| **proctune** | 15 | `column_stats`, `db_version`, `indexes`, `key_gucs`, `locate_context`, `locate_schema`, `locate_search`, `plan_text`, `plan_text_analyze`, `plan_text_analyze_schema`, `plan_text_schema`, `proc_def`, `sql_from_history`, `sql_from_statement`, `tables` |
| **session** | 3 | `active_only`, `by_user`, `top_by` |
| **slowsql** | 1 | `slow_sql` |
| **sqlfetch** | 2 | `from_history`, `from_statement` |
| **sqlreview** | 5 | `from_history`, `from_statement`, `indexes`, `tables`, `top_sql` |
| **sqltune** | 18 | `column_stats`, `column_types`, `exec_history`, `from_history`, `from_statement`, `indexes`, `key_gucs`, `plan_json`, `plan_json_schema`, `plan_text`, `plan_text_analyze`, `plan_text_analyze_schema`, `plan_text_schema`, `stat_now`, `stat_trend`, `stats_freshness`, `tables`, `version` |
| **topproc** | 1 | `top_procs` |
| **topsql** | 2 | `snap_statement`, `top_sql` |
| **vacuum** | 7 | `autovac_settings`, `autovac_workers`, `bloat_estimate`, `dead_tuples`, `freeze_age`, `kernel_info`, `oldest_xmin` |
| **waitevent** | 2 | `events`, `instance_time` |
| **wdr** | 14 | `cache`, `checkpoint`, `db_stat`, `db_summary`, `file_io`, `load_profile`, `native_report`, `node_name`, `plan_in_window`, `snapshots`, `top_sql`, `waits`, `wdr_enabled`, `window` |

---

## 全部脚本

### `config.settings`

- id `1` · 类型 `SQL` · 会话 **只读** · is_valid `1` · 异步 `0`

无参数

```sql
SELECT name,
       COALESCE(setting,'')    AS setting,
       COALESCE(unit,'')       AS unit,
       COALESCE(vartype,'')    AS vartype,
       COALESCE(boot_val,'')   AS boot_val,
       COALESCE(reset_val,'')  AS reset_val,
       COALESCE(context,'')    AS context,
       COALESCE(source,'')     AS source,
       COALESCE(min_val,'')    AS min_val,
       COALESCE(max_val,'')    AS max_val,
       COALESCE(enumvals::text,'') AS enumvals,
       COALESCE(category,'')   AS category,
       COALESCE(short_desc,'') AS short_desc
  FROM pg_settings
 ORDER BY name;
```

### `explain.active_pid`

- id `2` · 类型 `SQL` · 会话 **只读** · is_valid `1` · 异步 `0`

| 参数 | 类型 |
|---|---|
| `sql_id` | INTEGER |

```sql
SELECT pid, unique_sql_id, query, query_start
  FROM pg_stat_activity
 WHERE unique_sql_id = {{sql_id}}
   AND state = 'active'
   AND pid <> pg_backend_pid()
 ORDER BY query_start
 LIMIT 1;
```

### `explain.from_history`

- id `3` · 类型 `SQL` · 会话 **只读** · is_valid `1` · 异步 `0`

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

### `explain.from_statement`

- id `4` · 类型 `SQL` · 会话 **只读** · is_valid `1` · 异步 `0`

| 参数 | 类型 |
|---|---|
| `sid` | INTEGER |

```sql
SELECT query, user_name FROM dbe_perf.statement
WHERE unique_sql_id = {{sid}}
  AND query IS NOT NULL
  AND LENGTH(TRIM(query)) > 0
LIMIT 1;
```

### `explain.kernel_funcs`

- id `5` · 类型 `SQL` · 会话 **只读** · is_valid `1` · 异步 `0`

无参数

```sql
SELECT 'version' AS item, version() AS detail
UNION ALL
SELECT 'func:' || p.proname AS item, pg_get_function_arguments(p.oid) AS detail
  FROM pg_proc p
 WHERE p.proname IN ('gs_get_explain', 'gs_get_kernel_info');
```

### `explain.multi_stmt_probe`

- id `6` · 类型 `SQL` · 会话 **只读** · is_valid `1` · 异步 `0`

无参数

```sql
SET search_path TO public; SELECT 1 AS ok;
```

### `explain.plan_from_history`

- id `7` · 类型 `SQL` · 会话 **只读** · is_valid `1` · 异步 `0`

| 参数 | 类型 |
|---|---|
| `sid` | INTEGER |

```sql
SELECT schema_name,
       to_char(start_time, 'YYYY-MM-DD HH24:MI:SS') AS start_time,
       round(db_time / 1000.0, 1) AS db_time_ms,
       n_returned_rows,
       query_plan
  FROM dbe_perf.statement_history
 WHERE unique_query_id = {{sid}}
   AND query_plan IS NOT NULL AND query_plan <> ''
 ORDER BY start_time DESC
 LIMIT 1;
```

### `explain.plan_text`

- id `8` · 类型 `SQL` · 会话 **只读** · is_valid `1` · 异步 `0`

| 参数 | 类型 |
|---|---|
| `sql` | STRING |

```sql
EXPLAIN (ANALYZE false, BUFFERS false, FORMAT TEXT) {{sql}}
```

### `explain.plan_text_analyze`

- id `9` · 类型 `SQL` · 会话 **只读** · is_valid `1` · 异步 `0`

| 参数 | 类型 |
|---|---|
| `sql` | STRING |

```sql
EXPLAIN (ANALYZE false, BUFFERS false, FORMAT TEXT) {{sql}}
```

### `explain.plan_text_analyze_schema`

- id `10` · 类型 `SQL` · 会话 **只读** · is_valid `1` · 异步 `0`

| 参数 | 类型 |
|---|---|
| `schema` | STRING |
| `sql` | STRING |

```sql
SET search_path TO "{{schema}}", public; EXPLAIN (ANALYZE false, BUFFERS false, FORMAT TEXT) {{sql}}
```

### `explain.plan_text_schema`

- id `11` · 类型 `SQL` · 会话 **只读** · is_valid `1` · 异步 `0`

| 参数 | 类型 |
|---|---|
| `schema` | STRING |
| `sql` | STRING |

```sql
SET search_path TO "{{schema}}", public; EXPLAIN (ANALYZE false, BUFFERS false, FORMAT TEXT) {{sql}}
```

### `explain.relation_schemas`

- id `12` · 类型 `SQL` · 会话 **只读** · is_valid `1` · 异步 `0`

| 参数 | 类型 |
|---|---|
| `names` | STRING |

```sql
SELECT n.nspname, c.relname
FROM pg_class c
LEFT JOIN pg_namespace n ON c.relnamespace = n.oid
WHERE c.relname IN ({{names}}) AND c.relkind IN ('r','v','p','m','f');
```

### `explain.runtime_plan`

- id `13` · 类型 `SQL` · 会话 **只读** · is_valid `1` · 异步 `0`

| 参数 | 类型 |
|---|---|
| `pid` | INTEGER |

```sql
SELECT gs_get_explain({{pid}}::bigint) AS plan;
```

### `explain.runtime_plan_int4`

- id `14` · 类型 `SQL` · 会话 **只读** · is_valid `1` · 异步 `0`

| 参数 | 类型 |
|---|---|
| `pid` | INTEGER |

```sql
SELECT gs_get_explain({{pid}}::integer) AS plan;
```

### `explain.session_by_pid`

- id `15` · 类型 `SQL` · 会话 **只读** · is_valid `1` · 异步 `0`

| 参数 | 类型 |
|---|---|
| `pid` | INTEGER |

```sql
SELECT pid, unique_sql_id, query, query_start, state
  FROM pg_stat_activity
 WHERE pid = {{pid}}
 LIMIT 1;
```

### `health.archive_mode`

- id `16` · 类型 `SQL` · 会话 **只读** · is_valid `1` · 异步 `0`

无参数

```sql
SELECT setting FROM pg_settings WHERE name='archive_mode';
```

### `health.bgwriter`

- id `17` · 类型 `SQL` · 会话 **只读** · is_valid `1` · 异步 `0`

无参数

```sql
SELECT checkpoints_timed, checkpoints_req FROM pg_stat_bgwriter;
```

### `health.conn_concentration`

- id `18` · 类型 `SQL` · 会话 **只读** · is_valid `1` · 异步 `0`

无参数

```sql
SELECT COALESCE(query,'') q, count(*) c, sum(count(*)) OVER () AS total
FROM pg_stat_activity
WHERE state='active' AND COALESCE(query,'')<>'' AND client_port IS NOT NULL
GROUP BY query ORDER BY c DESC LIMIT 1;
```

### `health.conn_states`

- id `19` · 类型 `SQL` · 会话 **只读** · is_valid `1` · 异步 `0`

无参数

```sql
SELECT COALESCE(state,'<null>') AS state, count(*) AS cnt
FROM pg_stat_activity
WHERE sessionid <> 0
GROUP BY state
ORDER BY cnt DESC;
```

### `health.db_concurrency`

- id `20` · 类型 `SQL` · 会话 **只读** · is_valid `1` · 异步 `0`

无参数

```sql
SELECT deadlocks, xact_commit, xact_rollback
FROM pg_stat_database WHERE datname=current_database();
```

### `health.db_info`

- id `21` · 类型 `SQL` · 会话 **只读** · is_valid `1` · 异步 `0`

无参数

```sql
select pg_encoding_to_char(encoding) as encoding_name, *
from pg_database
where datname not in ('template1','postgres','template0');
```

### `health.invalid_index`

- id `22` · 类型 `SQL` · 会话 **只读** · is_valid `1` · 异步 `0`

无参数

```sql
SELECT count(*) AS cnt FROM pg_index WHERE NOT indisvalid;
```

### `health.live_counters`

- id `23` · 类型 `SQL` · 会话 **只读** · is_valid `1` · 异步 `0`

无参数

```sql
SELECT 'now' AS item, to_char(now(), 'YYYY-MM-DD HH24:MI:SS') AS value
UNION ALL
SELECT 'postmaster_start', to_char(pg_postmaster_start_time(), 'YYYY-MM-DD HH24:MI:SS')
UNION ALL
SELECT 'stats_reset', COALESCE(to_char(max(stats_reset), 'YYYY-MM-DD HH24:MI:SS'), '')
  FROM pg_stat_database
UNION ALL
SELECT 'db_time_us', COALESCE(sum(value), 0)::text
  FROM dbe_perf.global_instance_time WHERE stat_name = 'DB_TIME'
UNION ALL
SELECT 'cpu_time_us', COALESCE(sum(value), 0)::text
  FROM dbe_perf.global_instance_time WHERE stat_name = 'CPU_TIME'
UNION ALL
SELECT 'xact_commit', COALESCE(sum(xact_commit), 0)::text FROM pg_stat_database
UNION ALL
SELECT 'xact_rollback', COALESCE(sum(xact_rollback), 0)::text FROM pg_stat_database
UNION ALL
SELECT 'deadlocks', COALESCE(sum(deadlocks), 0)::text FROM pg_stat_database
UNION ALL
SELECT 'temp_bytes', COALESCE(sum(temp_bytes), 0)::text FROM pg_stat_database
UNION ALL
SELECT 'blks_read', COALESCE(sum(blks_read), 0)::text FROM pg_stat_database
UNION ALL
SELECT 'blks_hit', COALESCE(sum(blks_hit), 0)::text FROM pg_stat_database
UNION ALL
SELECT 'ckpt_timed', COALESCE(sum(checkpoints_timed), 0)::text FROM pg_stat_bgwriter
UNION ALL
SELECT 'ckpt_req', COALESCE(sum(checkpoints_req), 0)::text FROM pg_stat_bgwriter;
```

### `health.live_waits`

- id `24` · 类型 `SQL` · 会话 **只读** · is_valid `1` · 异步 `0`

无参数

```sql
SELECT type  AS wait_class,
       event AS event,
       sum(wait)            AS waits,
       sum(total_wait_time) AS wait_us
  FROM dbe_perf.global_wait_events
 WHERE upper(type) NOT IN ('STATUS','NONE')
 GROUP BY type, event
HAVING sum(wait) > 0;
```

### `health.long_xact`

- id `25` · 类型 `SQL` · 会话 **只读** · is_valid `1` · 异步 `0`

| 参数 | 类型 |
|---|---|
| `limit` | INTEGER |

```sql
SELECT pid,
       COALESCE(usename,'') AS usename,
       state,
       waiting,
       EXTRACT(EPOCH FROM (now()-xact_start))   AS xact_age_s,
       EXTRACT(EPOCH FROM (now()-state_change)) AS state_age_s,
       COALESCE(query,'') AS query
FROM pg_stat_activity
WHERE state IN ('active','idle in transaction') AND xact_start IS NOT NULL
  AND client_port IS NOT NULL
  AND pid <> pg_backend_pid()
ORDER BY xact_start
LIMIT {{limit}};
```

### `health.overview`

- id `26` · 类型 `SQL` · 会话 **只读** · is_valid `1` · 异步 `0`

无参数

```sql
SELECT
  CASE WHEN sum(blks_hit)+sum(blks_read)=0 THEN 100
       ELSE round(100.0*sum(blks_hit)/(sum(blks_hit)+sum(blks_read)),2) END AS cache_hit_pct,
  (SELECT count(*) FROM pg_stat_activity WHERE sessionid <> 0)::bigint AS numbackends,
  (SELECT setting::bigint FROM pg_settings WHERE name='max_connections') AS max_conn,
  pg_is_in_recovery() AS in_recovery,
  (SELECT COALESCE(EXTRACT(EPOCH FROM now()-min(xact_start)),0)::bigint
   FROM pg_stat_activity
   WHERE state IN ('active','idle in transaction') AND xact_start IS NOT NULL
     AND client_port IS NOT NULL) AS oldest_xact_s
FROM pg_stat_database;
```

### `health.partitions`

- id `27` · 类型 `SQL` · 会话 **只读** · is_valid `1` · 异步 `0`

无参数

```sql
SELECT n.nspname      AS schema,
       c.relname      AS "table",
       count(*)       AS parts,
       COALESCE(max(p.partstrategy), '') AS strategy
  FROM pg_partition p
  JOIN pg_class c     ON c.oid = p.parentid
  JOIN pg_namespace n ON n.oid = c.relnamespace
 WHERE p.parttype = 'p'
   AND n.nspname NOT IN ('pg_catalog','information_schema','snapshot','dbe_perf',
                         'dbe_pldeveloper','cstore','pg_toast')
 GROUP BY n.nspname, c.relname
 ORDER BY parts DESC;
```

### `health.prepared_xacts`

- id `28` · 类型 `SQL` · 会话 **只读** · is_valid `1` · 异步 `0`

无参数

```sql
SELECT count(*) AS cnt FROM pg_prepared_xacts;
```

### `health.replication`

- id `29` · 类型 `SQL` · 会话 **只读** · is_valid `1` · 异步 `0`

无参数

```sql
SELECT application_name,
       COALESCE(client_addr::text,'') AS client_addr,
       state, sync_state,
       pg_xlog_location_diff(sender_sent_location, receiver_replay_location)::bigint AS lag_bytes
FROM pg_stat_replication;
```

### `health.slow_sql`

- id `30` · 类型 `SQL` · 会话 **只读** · is_valid `1` · 异步 `0`

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

### `health.snap_counters`

- id `31` · 类型 `SQL` · 会话 **只读** · is_valid `1` · 异步 `0`

| 参数 | 类型 |
|---|---|
| `snap` | INTEGER |

```sql
SELECT 'snap_at' AS item,
       to_char((SELECT start_ts FROM snapshot.snapshot WHERE snapshot_id = {{snap}}),
               'YYYY-MM-DD HH24:MI:SS') AS value
UNION ALL
SELECT 'db_time_us', COALESCE(sum(snap_value), 0)::text
  FROM snapshot.snap_global_instance_time WHERE snapshot_id = {{snap}} AND snap_stat_name = 'DB_TIME'
UNION ALL
SELECT 'cpu_time_us', COALESCE(sum(snap_value), 0)::text
  FROM snapshot.snap_global_instance_time WHERE snapshot_id = {{snap}} AND snap_stat_name = 'CPU_TIME'
UNION ALL
SELECT 'xact_commit', COALESCE(sum(snap_xact_commit), 0)::text
  FROM snapshot.snap_summary_stat_database WHERE snapshot_id = {{snap}}
UNION ALL
SELECT 'xact_rollback', COALESCE(sum(snap_xact_rollback), 0)::text
  FROM snapshot.snap_summary_stat_database WHERE snapshot_id = {{snap}}
UNION ALL
SELECT 'deadlocks', COALESCE(sum(snap_deadlocks), 0)::text
  FROM snapshot.snap_summary_stat_database WHERE snapshot_id = {{snap}}
UNION ALL
SELECT 'temp_bytes', COALESCE(sum(snap_temp_bytes), 0)::text
  FROM snapshot.snap_summary_stat_database WHERE snapshot_id = {{snap}}
UNION ALL
SELECT 'blks_read', COALESCE(sum(snap_blks_read), 0)::text
  FROM snapshot.snap_summary_stat_database WHERE snapshot_id = {{snap}}
UNION ALL
SELECT 'blks_hit', COALESCE(sum(snap_blks_hit), 0)::text
  FROM snapshot.snap_summary_stat_database WHERE snapshot_id = {{snap}}
UNION ALL
SELECT 'ckpt_timed', COALESCE(sum(snap_checkpoints_timed), 0)::text
  FROM snapshot.snap_global_bgwriter_stat WHERE snapshot_id = {{snap}}
UNION ALL
SELECT 'ckpt_req', COALESCE(sum(snap_checkpoints_req), 0)::text
  FROM snapshot.snap_global_bgwriter_stat WHERE snapshot_id = {{snap}};
```

### `health.snap_waits`

- id `32` · 类型 `SQL` · 会话 **只读** · is_valid `1` · 异步 `0`

| 参数 | 类型 |
|---|---|
| `snap` | INTEGER |

```sql
SELECT snap_type  AS wait_class,
       snap_event AS event,
       sum(snap_wait)            AS waits,
       sum(snap_total_wait_time) AS wait_us
  FROM snapshot.snap_global_wait_events
 WHERE snapshot_id = {{snap}}
   AND upper(snap_type) NOT IN ('STATUS','NONE')
 GROUP BY snap_type, snap_event
HAVING sum(snap_wait) > 0;
```

### `health.stale_stats`

- id `33` · 类型 `SQL` · 会话 **只读** · is_valid `1` · 异步 `0`

| 参数 | 类型 |
|---|---|
| `min_rows` | INTEGER |
| `min_pages` | INTEGER |
| `schema_filter` | STRING |
| `limit` | INTEGER |

```sql
SELECT tbl_name, frozen_pages, cur_pages, frozen_tuples, live_tuples,
       stat_columns, last_analyze, last_autoanalyze
FROM (
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
    AND (c.reltuples > {{min_rows}}
         OR pg_relation_size(c.oid) / current_setting('block_size')::bigint > {{min_pages}})
) x
ORDER BY (stat_columns = 0 OR frozen_pages = 0) DESC,
         CASE WHEN frozen_pages > 0
              THEN CAST(abs(cur_pages - frozen_pages) AS float8) / frozen_pages
              ELSE 0 END DESC,
         cur_pages DESC
LIMIT {{limit}};
```

### `health.stats_window`

- id `34` · 类型 `SQL` · 会话 **只读** · is_valid `1` · 异步 `0`

无参数

```sql
SELECT COALESCE(to_char(stats_reset, 'YYYY-MM-DD HH24:MI:SS'), 'never') AS stats_reset,
       COALESCE(EXTRACT(EPOCH FROM (now() - stats_reset)), -1) AS window_seconds
FROM pg_stat_database
WHERE datname = current_database();
```

### `health.storage`

- id `35` · 类型 `SQL` · 会话 **只读** · is_valid `1` · 异步 `0`

无参数

```sql
SELECT 'database' AS kind,
       d.datname  AS name,
       pg_database_size(d.oid)::bigint AS bytes
  FROM pg_database d
 WHERE d.datallowconn
UNION ALL
SELECT 'tablespace' AS kind,
       t.spcname    AS name,
       pg_tablespace_size(t.oid)::bigint AS bytes
  FROM pg_tablespace t
 ORDER BY bytes DESC;
```

### `health.sync_commit`

- id `36` · 类型 `SQL` · 会话 **只读** · is_valid `1` · 异步 `0`

无参数

```sql
SELECT 'peers_configured' AS item,
       (SELECT count(*)::text FROM pg_settings
         WHERE name LIKE 'replconninfo%' AND COALESCE(setting,'') <> '') AS value
UNION ALL
SELECT 'in_recovery' AS item, pg_is_in_recovery()::text AS value
UNION ALL
SELECT 'sync_commit' AS item,
       COALESCE((SELECT setting FROM pg_settings WHERE name='synchronous_commit'),'') AS value
UNION ALL
SELECT 'standby_names' AS item,
       COALESCE((SELECT setting FROM pg_settings WHERE name='synchronous_standby_names'),'') AS value;
```

### `health.thread_pool`

- id `37` · 类型 `SQL` · 会话 **只读** · is_valid `1` · 异步 `0`

无参数

```sql
SELECT 'enable_thread_pool' AS item,
       COALESCE((SELECT setting FROM pg_settings WHERE name='enable_thread_pool'),'') AS value
UNION ALL
SELECT 'thread_pool_attr' AS item,
       COALESCE((SELECT setting FROM pg_settings WHERE name='thread_pool_attr'),'') AS value
UNION ALL
SELECT 'max_connections' AS item,
       COALESCE((SELECT setting FROM pg_settings WHERE name='max_connections'),'') AS value
UNION ALL
SELECT 'real_sessions' AS item,
       (SELECT count(*)::text FROM pg_stat_activity WHERE sessionid <> 0) AS value;
```

### `health.unused_index`

- id `38` · 类型 `SQL` · 会话 **只读** · is_valid `1` · 异步 `0`

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

### `health.wal_slots`

- id `39` · 类型 `SQL` · 会话 **只读** · is_valid `1` · 异步 `0`

无参数

```sql
SELECT slot_name,
       COALESCE(slot_type,'')  AS slot_type,
       COALESCE(database,'')   AS database,
       active::text            AS active,
       pg_xlog_location_diff(pg_current_xlog_location(), restart_lsn)::bigint AS lag_bytes
  FROM pg_replication_slots
 WHERE restart_lsn IS NOT NULL
 ORDER BY lag_bytes DESC;
```

### `lockwait.asp_pairs`

- id `40` · 类型 `SQL` · 会话 **只读** · is_valid `1` · 异步 `0`

| 参数 | 类型 |
|---|---|
| `begin_time` | DATETIME |
| `end_time` | DATETIME |
| `limit` | INTEGER |

```sql
WITH t AS (
  SELECT a.sessionid, a.block_sessionid, a.event, a.lockmode, a.locktag,
         a.unique_query_id, a.unique_query, a.sample_time
    FROM gs_asp a
   WHERE a.sample_time >= CAST('{{begin_time}}' AS TIMESTAMP)
     AND a.sample_time <= CAST('{{end_time}}' AS TIMESTAMP)
), s AS (
  SELECT * FROM t
   WHERE t.block_sessionid IS NOT NULL AND t.block_sessionid <> 0
  UNION ALL
  SELECT m.sessionid, m.block_sessionid, m.event, m.lockmode, m.locktag,
         m.unique_query_id, m.unique_query, m.sample_time
    FROM dbe_perf.local_active_session m
   WHERE m.need_flush_sample
     AND m.sample_time >= CAST('{{begin_time}}' AS TIMESTAMP)
     AND m.sample_time <= CAST('{{end_time}}' AS TIMESTAMP)
     AND m.sample_time > (SELECT COALESCE(max(t.sample_time), CAST('1970-01-01' AS TIMESTAMP)) FROM t)
     AND m.block_sessionid IS NOT NULL AND m.block_sessionid <> 0
), g AS (
  SELECT sessionid, block_sessionid,
         COALESCE(event, '') AS event, COALESCE(lockmode, '') AS lockmode,
         COALESCE(locktag, '') AS locktag, unique_query_id,
         count(*) AS samples, min(sample_time) AS first_seen, max(sample_time) AS last_seen,
         max(substr(COALESCE(unique_query, ''), 1, 300)) AS query
    FROM s
   GROUP BY sessionid, block_sessionid, COALESCE(event, ''), COALESCE(lockmode, ''),
            COALESCE(locktag, ''), unique_query_id
), d AS (
  SELECT g.*, CASE WHEN g.locktag <> '' THEN COALESCE(locktag_decode(g.locktag), '') ELSE '' END AS decoded
    FROM g
)
SELECT d.sessionid AS waiter_sessionid,
       d.block_sessionid AS holder_sessionid,
       d.event AS wait_event,
       d.lockmode AS waiter_mode,
       d.locktag AS locktag,
       d.decoded AS lock_decoded,
       COALESCE((SELECT n.nspname || '.' || c.relname
                   FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
                  WHERE c.oid = CAST(NULLIF(substring(d.decoded from 'relation:([0-9]+)'), '') AS oid)
                    AND substring(d.decoded from 'database:([0-9]+)')
                        = (SELECT CAST(oid AS text) FROM pg_database WHERE datname = current_database())), '') AS lock_object,
       d.unique_query_id AS waiter_sql_id,
       d.query AS waiter_query,
       d.samples AS samples,
       to_char(d.first_seen, 'YYYY-MM-DD HH24:MI:SS') AS first_seen,
       to_char(d.last_seen, 'YYYY-MM-DD HH24:MI:SS') AS last_seen
  FROM d
 ORDER BY d.samples DESC, d.sessionid
 LIMIT {{limit}};
```

### `lockwait.asp_sessions`

- id `41` · 类型 `SQL` · 会话 **只读** · is_valid `1` · 异步 `0`

| 参数 | 类型 |
|---|---|
| `begin_time` | DATETIME |
| `end_time` | DATETIME |
| `limit` | INTEGER |

```sql
WITH t AS (
  SELECT a.sessionid, a.block_sessionid, a.state, a.event, a.unique_query_id, a.unique_query,
         a.application_name, a.client_addr, a.xact_start_time, a.sample_time
    FROM gs_asp a
   WHERE a.sample_time >= CAST('{{begin_time}}' AS TIMESTAMP)
     AND a.sample_time <= CAST('{{end_time}}' AS TIMESTAMP)
), a AS (
  SELECT * FROM t
  UNION ALL
  SELECT m.sessionid, m.block_sessionid, m.state, m.event, m.unique_query_id, m.unique_query,
         m.application_name, m.client_addr, m.xact_start_time, m.sample_time
    FROM dbe_perf.local_active_session m
   WHERE m.need_flush_sample
     AND m.sample_time >= CAST('{{begin_time}}' AS TIMESTAMP)
     AND m.sample_time <= CAST('{{end_time}}' AS TIMESTAMP)
     AND m.sample_time > (SELECT COALESCE(max(t.sample_time), CAST('1970-01-01' AS TIMESTAMP)) FROM t)
), w AS (
  SELECT sessionid, block_sessionid FROM a
   WHERE block_sessionid IS NOT NULL AND block_sessionid <> 0
), ids AS (
  SELECT sessionid AS sid FROM w UNION SELECT block_sessionid FROM w
), r AS (
  SELECT a.sessionid, a.state, a.event, a.unique_query_id, a.unique_query,
         a.application_name, a.client_addr, a.xact_start_time, a.sample_time,
         row_number() OVER (PARTITION BY a.sessionid ORDER BY a.sample_time DESC) AS rn,
         count(*) OVER (PARTITION BY a.sessionid) AS samples
    FROM a
   WHERE a.sessionid IN (SELECT sid FROM ids)
)
SELECT r.sessionid AS sessionid,
       COALESCE(r.state, '') AS state,
       COALESCE(r.event, '') AS last_event,
       r.unique_query_id AS sql_id,
       substr(COALESCE(r.unique_query, ''), 1, 300) AS query,
       COALESCE(r.application_name, '') AS application_name,
       COALESCE(host(r.client_addr), '') AS client_addr,
       COALESCE(to_char(r.xact_start_time, 'YYYY-MM-DD HH24:MI:SS'), '') AS xact_start,
       r.samples AS samples
  FROM r
 WHERE r.rn = 1
 ORDER BY r.sessionid
 LIMIT {{limit}};
```

### `lockwait.asp_status`

- id `42` · 类型 `SQL` · 会话 **只读** · is_valid `1` · 异步 `0`

| 参数 | 类型 |
|---|---|
| `begin_time` | DATETIME |
| `end_time` | DATETIME |

```sql
SELECT name AS item, setting AS value
  FROM pg_settings
 WHERE name IN ('enable_asp', 'asp_sample_interval', 'asp_flush_rate', 'asp_retention_days',
                'asp_flush_mode', 'asp_sample_num')
UNION ALL
SELECT 'samples_in_window', CAST(count(*) AS text)
  FROM gs_asp
 WHERE sample_time >= CAST('{{begin_time}}' AS TIMESTAMP)
   AND sample_time <= CAST('{{end_time}}' AS TIMESTAMP)
UNION ALL
SELECT 'memory_samples_in_window', CAST(count(*) AS text)
  FROM dbe_perf.local_active_session m
 WHERE m.need_flush_sample
   AND m.sample_time >= CAST('{{begin_time}}' AS TIMESTAMP)
   AND m.sample_time <= CAST('{{end_time}}' AS TIMESTAMP)
   AND m.sample_time > (SELECT COALESCE(max(t.sample_time), CAST('1970-01-01' AS TIMESTAMP))
                          FROM gs_asp t
                         WHERE t.sample_time >= CAST('{{begin_time}}' AS TIMESTAMP)
                           AND t.sample_time <= CAST('{{end_time}}' AS TIMESTAMP))
UNION ALL
SELECT 'memory_first', COALESCE(to_char(min(sample_time), 'YYYY-MM-DD HH24:MI:SS'), '')
  FROM dbe_perf.local_active_session
UNION ALL
SELECT 'memory_last', COALESCE(to_char(max(sample_time), 'YYYY-MM-DD HH24:MI:SS'), '')
  FROM dbe_perf.local_active_session
UNION ALL
SELECT 'table_last', COALESCE(to_char(max(sample_time), 'YYYY-MM-DD HH24:MI:SS'), '')
  FROM gs_asp
UNION ALL
SELECT 'now', to_char(now(), 'YYYY-MM-DD HH24:MI:SS');
```

### `lockwait.chain`

- id `43` · 类型 `SQL` · 会话 **只读** · is_valid `1` · 异步 `0`

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

- id `44` · 类型 `SQL` · 会话 **只读** · is_valid `1` · 异步 `0`

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

- id `45` · 类型 `SQL` · 会话 **只读** · is_valid `1` · 异步 `0`

无参数

```sql
SELECT sessionid, pid, usename, application_name, state, query FROM pg_stat_activity;
```

### `memanalyze.cols_bare`

- id `46` · 类型 `SQL` · 会话 **只读** · is_valid `1` · 异步 `0`

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

- id `47` · 类型 `SQL` · 会话 **只读** · is_valid `1` · 异步 `0`

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

- id `48` · 类型 `SQL` · 会话 **只读** · is_valid `1` · 异步 `0`

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

- id `49` · 类型 `SQL` · 会话 **只读** · is_valid `1` · 异步 `0`

无参数

```sql
SELECT name, setting
FROM pg_settings
WHERE name IN (
  'max_process_memory', 'shared_buffers', 'work_mem', 'maintenance_work_mem',
  'max_connections', 'enable_memory_limit', 'memory_tracking_mode',
  'use_workload_manager', 'enable_resource_track', 'resource_track_level',
  'resource_track_cost', 'resource_track_duration', 'enable_resource_record',
  'enable_dynamic_workload', 'query_max_mem', 'query_mem')
ORDER BY name;
```

### `memanalyze.instance`

- id `50` · 类型 `SQL` · 会话 **只读** · is_valid `1` · 异步 `0`

无参数

```sql
SELECT memorytype, memorymbytes FROM gs_total_memory_detail;
```

### `memanalyze.session`

- id `51` · 类型 `SQL` · 会话 **只读** · is_valid `1` · 异步 `0`

| 参数 | 类型 |
|---|---|
| `limit` | INTEGER |

```sql
SELECT sessid, init_mem, used_mem, peak_mem FROM dbe_perf.session_memory
ORDER BY peak_mem DESC NULLS LAST LIMIT {{limit}};
```

### `memanalyze.wlm_operator`

- id `52` · 类型 `SQL` · 会话 **只读** · is_valid `1` · 异步 `0`

| 参数 | 类型 |
|---|---|
| `limit` | INTEGER |

```sql
SELECT queryid, plan_node_id, plan_node_name, duration, NULL AS estimate_memory, NULL AS memory_used, max_peak_memory, average_peak_memory, NULL AS spill_size, warning FROM gs_wlm_operator_statistics
ORDER BY max_peak_memory DESC NULLS LAST LIMIT {{limit}};
```

### `memanalyze.wlm_operator_hist`

- id `53` · 类型 `SQL` · 会话 **只读** · is_valid `1` · 异步 `0`

| 参数 | 类型 |
|---|---|
| `limit` | INTEGER |

```sql
SELECT queryid, plan_node_id, plan_node_name, duration, NULL AS estimate_memory, NULL AS memory_used, max_peak_memory, average_peak_memory, NULL AS spill_size, warning FROM gs_wlm_operator_history
ORDER BY max_peak_memory DESC NULLS LAST LIMIT {{limit}};
```

### `memanalyze.wlm_sql`

- id `54` · 类型 `SQL` · 会话 **只读** · is_valid `1` · 异步 `0`

| 参数 | 类型 |
|---|---|
| `limit` | INTEGER |

```sql
SELECT queryid, query, start_time, duration, estimate_memory, NULL AS used_memory, max_peak_memory, average_peak_memory, spill_info FROM gs_wlm_session_statistics
ORDER BY max_peak_memory DESC NULLS LAST LIMIT {{limit}};
```

### `memanalyze.wlm_sql_hist`

- id `55` · 类型 `SQL` · 会话 **只读** · is_valid `1` · 异步 `0`

| 参数 | 类型 |
|---|---|
| `limit` | INTEGER |

```sql
SELECT queryid, query, start_time, duration, estimate_memory, NULL AS used_memory, max_peak_memory, average_peak_memory, spill_info FROM gs_wlm_session_history
ORDER BY max_peak_memory DESC NULLS LAST LIMIT {{limit}};
```

### `perf.bgwriter`

- id `56` · 类型 `SQL` · 会话 **只读** · is_valid `1` · 异步 `0`

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

- id `57` · 类型 `SQL` · 会话 **只读** · is_valid `1` · 异步 `0`

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

- id `58` · 类型 `SQL` · 会话 **只读** · is_valid `1` · 异步 `0`

无参数

```sql
select stat_name, value
from dbe_perf.global_instance_time
order by value desc;
```

### `perf.locks`

- id `59` · 类型 `SQL` · 会话 **只读** · is_valid `1` · 异步 `0`

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

- id `60` · 类型 `SQL` · 会话 **只读** · is_valid `1` · 异步 `0`

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

- id `61` · 类型 `SQL` · 会话 **只读** · is_valid `1` · 异步 `0`

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

- id `62` · 类型 `SQL` · 会话 **只读** · is_valid `1` · 异步 `0`

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

- id `63` · 类型 `SQL` · 会话 **只读** · is_valid `1` · 异步 `0`

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

- id `64` · 类型 `SQL` · 会话 **只读** · is_valid `1` · 异步 `0`

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

- id `65` · 类型 `SQL` · 会话 **只读** · is_valid `1` · 异步 `0`

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

### `procinfo.locate_context`

- id `66` · 类型 `SQL` · 会话 **只读** · is_valid `1` · 异步 `0`

无参数

```sql
SELECT current_database() AS db, current_user AS usr,
       (SELECT string_agg(datname, ', ' ORDER BY datname) FROM pg_database WHERE NOT datistemplate) AS databases;
```

### `procinfo.locate_schema`

- id `67` · 类型 `SQL` · 会话 **只读** · is_valid `1` · 异步 `0`

| 参数 | 类型 |
|---|---|
| `schema` | STRING |

```sql
SELECT n.nspname, pg_catalog.has_schema_privilege(n.oid, 'USAGE') AS usage
FROM pg_namespace n
WHERE n.nspname = '{{schema}}';
```

### `procinfo.locate_search`

- id `68` · 类型 `SQL` · 会话 **只读** · is_valid `1` · 异步 `0`

| 参数 | 类型 |
|---|---|
| `name` | STRING |
| `package` | STRING |

```sql
SELECT 'proc' AS kind, n.nspname, COALESCE(k.pkgname, '') AS pkgname, p.proname
FROM pg_proc p
LEFT JOIN pg_namespace n ON n.oid = p.pronamespace
LEFT JOIN gs_package k ON k.oid = p.propackageid
WHERE p.proname ILIKE '%{{name}}%'
  AND (n.nspname IS NULL OR (n.nspname NOT LIKE 'pg_%' AND n.nspname NOT LIKE 'dbe_%'
       AND n.nspname NOT IN ('information_schema', 'pkg_service', 'pkg_util', 'sqladvisor', 'db4ai',
                             'snapshot', 'blockchain', 'cstore', 'pmk', 'coverage')))
UNION ALL
SELECT 'package' AS kind, n.nspname, k.pkgname, '' AS proname
FROM gs_package k
LEFT JOIN pg_namespace n ON n.oid = k.pkgnamespace
WHERE COALESCE(length('{{package}}'), 0) > 0 AND k.pkgname ILIKE '%{{package}}%'
ORDER BY 1, 2, 3, 4
LIMIT 40;
```

### `procinfo.proc_def`

- id `69` · 类型 `SQL` · 会话 **只读** · is_valid `1` · 异步 `0`

| 参数 | 类型 |
|---|---|
| `name` | STRING |
| `schema` | STRING |
| `package` | STRING |

```sql
SELECT n.nspname, p.proname, l.lanname, p.prosrc,
       pg_catalog.pg_get_function_arguments(p.oid) AS args,
       COALESCE(k.pkgname, '') AS package
FROM pg_proc p
JOIN pg_namespace n ON n.oid = p.pronamespace
JOIN pg_language l ON l.oid = p.prolang
LEFT JOIN gs_package k ON k.oid = p.propackageid
WHERE p.proname = '{{name}}'
  AND ('{{schema}}' IS NULL OR '{{schema}}' = '' OR n.nspname = '{{schema}}')
  AND ('{{package}}' IS NULL OR '{{package}}' = '' OR k.pkgname = '{{package}}')
ORDER BY (n.nspname = 'public') DESC, n.nspname, k.pkgname
LIMIT 5;
```

### `proctune.column_stats`

- id `70` · 类型 `SQL` · 会话 **只读** · is_valid `1` · 异步 `0`

| 参数 | 类型 |
|---|---|
| `names` | STRING |

```sql
SELECT schemaname, tablename, attname, n_distinct, null_frac, avg_width, correlation,
       COALESCE(most_common_vals::text, '') AS most_common_vals,
       COALESCE(most_common_freqs::text, '') AS most_common_freqs,
       COALESCE(histogram_bounds::text, '') AS histogram_bounds
FROM pg_stats
WHERE tablename IN ({{names}});
```

### `proctune.db_version`

- id `71` · 类型 `SQL` · 会话 **只读** · is_valid `1` · 异步 `0`

无参数

```sql
SELECT version() AS version;
```

### `proctune.indexes`

- id `72` · 类型 `SQL` · 会话 **只读** · is_valid `1` · 异步 `0`

| 参数 | 类型 |
|---|---|
| `names` | STRING |

```sql
SELECT n.nspname AS schema_name, t.relname AS table_name, i.relname AS index_name,
       ix.indisunique, ix.indisprimary,
       pg_get_indexdef(ix.indexrelid) AS index_def
FROM pg_class t
JOIN pg_index ix ON t.oid = ix.indrelid
JOIN pg_class i ON i.oid = ix.indexrelid
JOIN pg_namespace n ON n.oid = t.relnamespace
WHERE t.relname IN ({{names}});
```

### `proctune.key_gucs`

- id `73` · 类型 `SQL` · 会话 **只读** · is_valid `1` · 异步 `0`

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

### `proctune.locate_context`

- id `74` · 类型 `SQL` · 会话 **只读** · is_valid `1` · 异步 `0`

无参数

```sql
SELECT current_database() AS db, current_user AS usr,
       (SELECT string_agg(datname, ', ' ORDER BY datname) FROM pg_database WHERE NOT datistemplate) AS databases;
```

### `proctune.locate_schema`

- id `75` · 类型 `SQL` · 会话 **只读** · is_valid `1` · 异步 `0`

| 参数 | 类型 |
|---|---|
| `schema` | STRING |

```sql
SELECT n.nspname, pg_catalog.has_schema_privilege(n.oid, 'USAGE') AS usage
FROM pg_namespace n
WHERE n.nspname = '{{schema}}';
```

### `proctune.locate_search`

- id `76` · 类型 `SQL` · 会话 **只读** · is_valid `1` · 异步 `0`

| 参数 | 类型 |
|---|---|
| `name` | STRING |
| `package` | STRING |

```sql
SELECT 'proc' AS kind, n.nspname, COALESCE(k.pkgname, '') AS pkgname, p.proname
FROM pg_proc p
LEFT JOIN pg_namespace n ON n.oid = p.pronamespace
LEFT JOIN gs_package k ON k.oid = p.propackageid
WHERE p.proname ILIKE '%{{name}}%'
  AND (n.nspname IS NULL OR (n.nspname NOT LIKE 'pg_%' AND n.nspname NOT LIKE 'dbe_%'
       AND n.nspname NOT IN ('information_schema', 'pkg_service', 'pkg_util', 'sqladvisor', 'db4ai',
                             'snapshot', 'blockchain', 'cstore', 'pmk', 'coverage')))
UNION ALL
SELECT 'package' AS kind, n.nspname, k.pkgname, '' AS proname
FROM gs_package k
LEFT JOIN pg_namespace n ON n.oid = k.pkgnamespace
WHERE COALESCE(length('{{package}}'), 0) > 0 AND k.pkgname ILIKE '%{{package}}%'
ORDER BY 1, 2, 3, 4
LIMIT 40;
```

### `proctune.plan_text`

- id `77` · 类型 `SQL` · 会话 **只读** · is_valid `1` · 异步 `0`

| 参数 | 类型 |
|---|---|
| `sql` | STRING |

```sql
EXPLAIN (ANALYZE false, BUFFERS false, FORMAT TEXT) {{sql}}
```

### `proctune.plan_text_analyze`

- id `78` · 类型 `SQL` · 会话 **只读** · is_valid `1` · 异步 `0`

| 参数 | 类型 |
|---|---|
| `sql` | STRING |

```sql
EXPLAIN (ANALYZE false, BUFFERS false, FORMAT TEXT) {{sql}}
```

### `proctune.plan_text_analyze_schema`

- id `79` · 类型 `SQL` · 会话 **只读** · is_valid `1` · 异步 `0`

| 参数 | 类型 |
|---|---|
| `schema` | STRING |
| `sql` | STRING |

```sql
SET search_path TO "{{schema}}", public; EXPLAIN (ANALYZE false, BUFFERS false, FORMAT TEXT) {{sql}}
```

### `proctune.plan_text_schema`

- id `80` · 类型 `SQL` · 会话 **只读** · is_valid `1` · 异步 `0`

| 参数 | 类型 |
|---|---|
| `schema` | STRING |
| `sql` | STRING |

```sql
SET search_path TO "{{schema}}", public; EXPLAIN (ANALYZE false, BUFFERS false, FORMAT TEXT) {{sql}}
```

### `proctune.proc_def`

- id `81` · 类型 `SQL` · 会话 **只读** · is_valid `1` · 异步 `0`

| 参数 | 类型 |
|---|---|
| `name` | STRING |
| `schema` | STRING |
| `package` | STRING |

```sql
SELECT n.nspname, p.proname, l.lanname, p.prosrc,
       pg_catalog.pg_get_function_arguments(p.oid) AS args,
       COALESCE(k.pkgname, '') AS package
FROM pg_proc p
JOIN pg_namespace n ON n.oid = p.pronamespace
JOIN pg_language l ON l.oid = p.prolang
LEFT JOIN gs_package k ON k.oid = p.propackageid
WHERE p.proname = '{{name}}'
  AND ('{{schema}}' IS NULL OR '{{schema}}' = '' OR n.nspname = '{{schema}}')
  AND ('{{package}}' IS NULL OR '{{package}}' = '' OR k.pkgname = '{{package}}')
ORDER BY (n.nspname = 'public') DESC, n.nspname, k.pkgname
LIMIT 5;
```

### `proctune.sql_from_history`

- id `82` · 类型 `SQL` · 会话 **只读** · is_valid `1` · 异步 `0`

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

- id `83` · 类型 `SQL` · 会话 **只读** · is_valid `1` · 异步 `0`

| 参数 | 类型 |
|---|---|
| `sid` | INTEGER |

```sql
SELECT query, user_name FROM dbe_perf.statement
WHERE unique_sql_id = {{sid}}
  AND query IS NOT NULL
  AND LENGTH(TRIM(query)) > 0
LIMIT 1;
```

### `proctune.tables`

- id `84` · 类型 `SQL` · 会话 **只读** · is_valid `1` · 异步 `0`

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

- id `85` · 类型 `SQL` · 会话 **只读** · is_valid `1` · 异步 `0`

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

- id `86` · 类型 `SQL` · 会话 **只读** · is_valid `1` · 异步 `0`

| 参数 | 类型 |
|---|---|
| `username` | STRING |

```sql
select pid, usename, state, application_name
from pg_stat_activity
where usename = '{{username}}';
```

### `session.top_by`

- id `87` · 类型 `SQL` · 会话 **只读** · is_valid `1` · 异步 `0`

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

- id `88` · 类型 `SQL` · 会话 **只读** · is_valid `1` · 异步 `0`

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

- id `89` · 类型 `SQL` · 会话 **只读** · is_valid `1` · 异步 `0`

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

- id `90` · 类型 `SQL` · 会话 **只读** · is_valid `1` · 异步 `0`

| 参数 | 类型 |
|---|---|
| `sid` | INTEGER |

```sql
SELECT query, user_name FROM dbe_perf.statement
WHERE unique_sql_id = {{sid}}
  AND query IS NOT NULL
  AND LENGTH(TRIM(query)) > 0
LIMIT 1;
```

### `sqlreview.from_history`

- id `91` · 类型 `SQL` · 会话 **只读** · is_valid `1` · 异步 `0`

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

- id `92` · 类型 `SQL` · 会话 **只读** · is_valid `1` · 异步 `0`

| 参数 | 类型 |
|---|---|
| `sid` | INTEGER |

```sql
SELECT query, user_name FROM dbe_perf.statement
WHERE unique_sql_id = {{sid}}
  AND query IS NOT NULL
  AND LENGTH(TRIM(query)) > 0
LIMIT 1;
```

### `sqlreview.indexes`

- id `93` · 类型 `SQL` · 会话 **只读** · is_valid `1` · 异步 `0`

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

- id `94` · 类型 `SQL` · 会话 **只读** · is_valid `1` · 异步 `0`

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

- id `95` · 类型 `SQL` · 会话 **只读** · is_valid `1` · 异步 `0`

| 参数 | 类型 |
|---|---|
| `limit` | INTEGER |

```sql
SELECT unique_sql_id::text, query
FROM dbe_perf.statement
WHERE n_calls > 0 AND query IS NOT NULL AND LENGTH(TRIM(query)) > 0
ORDER BY total_elapse_time DESC
LIMIT {{limit}};
```

### `sqltune.column_stats`

- id `96` · 类型 `SQL` · 会话 **只读** · is_valid `1` · 异步 `0`

| 参数 | 类型 |
|---|---|
| `names` | STRING |

```sql
SELECT schemaname, tablename, attname, n_distinct, null_frac, avg_width, correlation,
       COALESCE(most_common_vals::text, '') AS most_common_vals,
       COALESCE(most_common_freqs::text, '') AS most_common_freqs,
       COALESCE(histogram_bounds::text, '') AS histogram_bounds
FROM pg_stats
WHERE tablename IN ({{names}});
```

### `sqltune.column_types`

- id `97` · 类型 `SQL` · 会话 **只读** · is_valid `1` · 异步 `0`

| 参数 | 类型 |
|---|---|
| `tables` | STRING |
| `columns` | STRING |

```sql
SELECT n.nspname AS schema_name, c.relname AS table_name,
       a.attname, format_type(a.atttypid, NULL) AS type_name
FROM pg_attribute a
JOIN pg_class c ON c.oid = a.attrelid
JOIN pg_namespace n ON n.oid = c.relnamespace
WHERE c.relname IN ({{tables}})
  AND a.attname IN ({{columns}})
  AND a.attnum > 0 AND NOT a.attisdropped
  AND c.relkind IN ('r','v','p','m')
  AND n.nspname NOT IN ('pg_catalog','information_schema');
```

### `sqltune.exec_history`

- id `98` · 类型 `SQL` · 会话 **只读** · is_valid `1` · 异步 `0`

| 参数 | 类型 |
|---|---|
| `sid` | INTEGER |
| `days` | INTEGER |

```sql
SELECT hour, runs, avg_ms, max_ms, plan_sig,
       CASE WHEN prev_sig IS NULL OR prev_sig <> plan_sig THEN query_plan END AS query_plan
  FROM (SELECT hour, runs, avg_ms, max_ms, plan_sig, query_plan,
               lag(plan_sig) OVER (ORDER BY hour) AS prev_sig
          FROM (SELECT to_char(hour, 'YYYY-MM-DD HH24:MI') AS hour, runs,
                       round(avg_us / 1000.0, 1) AS avg_ms,
                       round(max_us / 1000.0, 1) AS max_ms,
                       CASE WHEN coalesce(query_plan, '') = '' THEN NULL
                            ELSE md5(regexp_replace(regexp_replace(query_plan, '\((cost|actual)[^)]*\)', '', 'g'),
                                                    '[0-9]', '', 'g')) END AS plan_sig,
                       query_plan
                  FROM (SELECT date_trunc('hour', start_time) AS hour, query_plan,
                               row_number() OVER (PARTITION BY date_trunc('hour', start_time)
                                                  ORDER BY (coalesce(query_plan, '') = ''), start_time DESC) AS rn,
                               count(*) OVER (PARTITION BY date_trunc('hour', start_time)) AS runs,
                               avg(db_time) OVER (PARTITION BY date_trunc('hour', start_time)) AS avg_us,
                               max(db_time) OVER (PARTITION BY date_trunc('hour', start_time)) AS max_us
                          FROM dbe_perf.statement_history
                         WHERE unique_query_id = {{sid}}
                           AND start_time >= now() - {{days}} * INTERVAL '1 day') r
                 WHERE rn = 1) a) b
 ORDER BY hour;
```

### `sqltune.from_history`

- id `99` · 类型 `SQL` · 会话 **只读** · is_valid `1` · 异步 `0`

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

- id `100` · 类型 `SQL` · 会话 **只读** · is_valid `1` · 异步 `0`

| 参数 | 类型 |
|---|---|
| `sid` | INTEGER |

```sql
SELECT query, user_name FROM dbe_perf.statement
WHERE unique_sql_id = {{sid}}
  AND query IS NOT NULL
  AND LENGTH(TRIM(query)) > 0
LIMIT 1;
```

### `sqltune.indexes`

- id `101` · 类型 `SQL` · 会话 **只读** · is_valid `1` · 异步 `0`

| 参数 | 类型 |
|---|---|
| `names` | STRING |

```sql
SELECT n.nspname AS schema_name, t.relname AS table_name,
       i.relname AS index_name,
       ix.indisunique,
       ix.indisprimary,
       i.relpages AS index_relpages,
       i.reltuples::bigint AS index_reltuples,
       pg_get_indexdef(ix.indexrelid) AS index_def
FROM pg_class t
JOIN pg_index ix ON t.oid = ix.indrelid
JOIN pg_class i ON i.oid = ix.indexrelid
JOIN pg_namespace n ON n.oid = t.relnamespace
WHERE t.relname IN ({{names}});
```

### `sqltune.key_gucs`

- id `102` · 类型 `SQL` · 会话 **只读** · is_valid `1` · 异步 `0`

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

- id `103` · 类型 `SQL` · 会话 **只读** · is_valid `1` · 异步 `0`

| 参数 | 类型 |
|---|---|
| `sql` | STRING |

```sql
EXPLAIN (ANALYZE false, BUFFERS false, FORMAT JSON) {{sql}}
```

### `sqltune.plan_json_schema`

- id `104` · 类型 `SQL` · 会话 **只读** · is_valid `1` · 异步 `0`

| 参数 | 类型 |
|---|---|
| `schema` | STRING |
| `sql` | STRING |

```sql
SET search_path TO "{{schema}}", public; EXPLAIN (ANALYZE false, BUFFERS false, FORMAT JSON) {{sql}}
```

### `sqltune.plan_text`

- id `105` · 类型 `SQL` · 会话 **只读** · is_valid `1` · 异步 `0`

| 参数 | 类型 |
|---|---|
| `sql` | STRING |

```sql
EXPLAIN (ANALYZE false, BUFFERS false, FORMAT TEXT) {{sql}}
```

### `sqltune.plan_text_analyze`

- id `106` · 类型 `SQL` · 会话 **只读** · is_valid `1` · 异步 `0`

| 参数 | 类型 |
|---|---|
| `sql` | STRING |

```sql
EXPLAIN (ANALYZE false, BUFFERS false, FORMAT TEXT) {{sql}}
```

### `sqltune.plan_text_analyze_schema`

- id `107` · 类型 `SQL` · 会话 **只读** · is_valid `1` · 异步 `0`

| 参数 | 类型 |
|---|---|
| `schema` | STRING |
| `sql` | STRING |

```sql
SET search_path TO "{{schema}}", public; EXPLAIN (ANALYZE false, BUFFERS false, FORMAT TEXT) {{sql}}
```

### `sqltune.plan_text_schema`

- id `108` · 类型 `SQL` · 会话 **只读** · is_valid `1` · 异步 `0`

| 参数 | 类型 |
|---|---|
| `schema` | STRING |
| `sql` | STRING |

```sql
SET search_path TO "{{schema}}", public; EXPLAIN (ANALYZE false, BUFFERS false, FORMAT TEXT) {{sql}}
```

### `sqltune.stat_now`

- id `109` · 类型 `SQL` · 会话 **只读** · is_valid `1` · 异步 `0`

| 参数 | 类型 |
|---|---|
| `sid` | INTEGER |

```sql
SELECT sum(n_calls) AS calls,
       round(sum(total_elapse_time) / 1000.0, 1) AS total_ms,
       round(sum(total_elapse_time) / 1000.0 / nullif(sum(n_calls), 0), 1) AS avg_ms,
       round(max(max_elapse_time) / 1000.0, 1) AS max_ms,
       round(min(nullif(min_elapse_time, 0)) / 1000.0, 1) AS min_ms,
       round(sum(cpu_time) / 1000.0, 1) AS cpu_ms,
       round(sum(data_io_time) / 1000.0, 1) AS io_ms,
       sum(n_returned_rows) AS returned_rows
  FROM dbe_perf.statement
 WHERE unique_sql_id = {{sid}};
```

### `sqltune.stat_trend`

- id `110` · 类型 `SQL` · 会话 **只读** · is_valid `1` · 异步 `0`

| 参数 | 类型 |
|---|---|
| `sid` | INTEGER |
| `days` | INTEGER |

```sql
SELECT s.snapshot_id,
       to_char(s.start_ts, 'YYYY-MM-DD HH24:MI:SS') AS snap_ts,
       sum(t.snap_n_calls) AS calls,
       sum(t.snap_total_elapse_time) AS total_us
  FROM snapshot.snap_summary_statement t
  JOIN snapshot.snapshot s ON s.snapshot_id = t.snapshot_id
 WHERE t.snap_unique_sql_id = {{sid}}
   AND s.start_ts >= now() - {{days}} * INTERVAL '1 day'
 GROUP BY s.snapshot_id, s.start_ts
 ORDER BY s.snapshot_id;
```

### `sqltune.stats_freshness`

- id `111` · 类型 `SQL` · 会话 **只读** · is_valid `1` · 异步 `0`

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

- id `112` · 类型 `SQL` · 会话 **只读** · is_valid `1` · 异步 `0`

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

- id `113` · 类型 `SQL` · 会话 **只读** · is_valid `1` · 异步 `0`

无参数

```sql
SELECT version() AS version;
```

### `topproc.top_procs`

- id `114` · 类型 `SQL` · 会话 **只读** · is_valid `1` · 异步 `0`

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

### `topsql.snap_statement`

- id `115` · 类型 `SQL` · 会话 **只读** · is_valid `1` · 异步 `0`

| 参数 | 类型 |
|---|---|
| `snap` | INTEGER |
| `limit` | INTEGER |

```sql
SELECT
  snap_unique_sql_id::text AS unique_sql_id,
  LEFT(REGEXP_REPLACE(COALESCE(NULLIF(REGEXP_REPLACE(max(snap_query), '^(\s*(--[^\n]*(\n|$)|/\*([^*]|\*+[^*/])*\*+/))+\s*', ''), ''), max(snap_query)),
                     '\s+', ' ', 'g'), 80) AS query,
  sum(snap_n_calls) AS calls,
  ROUND(sum(snap_total_elapse_time)/1000000::numeric, 2) AS total_sec,
  ROUND((sum(snap_total_elapse_time)/NULLIF(sum(snap_n_calls),0))/1000::numeric, 2) AS avg_ms,
  sum(snap_n_returned_rows) AS rows,
  ROUND(sum(snap_cpu_time)/1000000::numeric, 2) AS cpu_sec,
  ROUND(sum(snap_data_io_time)/1000000::numeric, 2) AS io_sec
FROM snapshot.snap_summary_statement
WHERE snapshot_id = {{snap}}
GROUP BY snap_unique_sql_id
HAVING sum(snap_n_calls) > 0
ORDER BY sum(snap_total_elapse_time) DESC
LIMIT {{limit}};
```

### `topsql.top_sql`

- id `116` · 类型 `SQL` · 会话 **只读** · is_valid `1` · 异步 `0`

| 参数 | 类型 |
|---|---|
| `order` | STRING |
| `limit` | INTEGER |

```sql
SELECT
  unique_sql_id::text,
  LEFT(REGEXP_REPLACE(COALESCE(NULLIF(REGEXP_REPLACE(query, '^(\s*(--[^\n]*(\n|$)|/\*([^*]|\*+[^*/])*\*+/))+\s*', ''), ''), query),
                     '\s+', ' ', 'g'), 80) AS query,
  n_calls AS calls,
  ROUND(total_elapse_time/1000000::numeric, 2) AS total_sec,
  ROUND((total_elapse_time/NULLIF(n_calls,0))/1000::numeric, 2) AS avg_ms,
  n_returned_rows AS rows,
  ROUND(cpu_time/1000000::numeric, 2) AS cpu_sec,
  ROUND(data_io_time/1000000::numeric, 2) AS io_sec
FROM (SELECT unique_sql_id,
             max(query)             AS query,
             sum(n_calls)           AS n_calls,
             sum(total_elapse_time) AS total_elapse_time,
             sum(n_returned_rows)   AS n_returned_rows,
             sum(n_blocks_hit)      AS n_blocks_hit,
             sum(n_blocks_fetched)  AS n_blocks_fetched,
             sum(cpu_time)          AS cpu_time,
             sum(data_io_time)      AS data_io_time
        FROM dbe_perf.statement
       GROUP BY unique_sql_id) s
WHERE n_calls > 0
ORDER BY {{order}}
LIMIT {{limit}};
```

### `vacuum.autovac_settings`

- id `117` · 类型 `SQL` · 会话 **只读** · is_valid `1` · 异步 `0`

无参数

```sql
SELECT name, setting
  FROM pg_settings
 WHERE name LIKE 'autovacuum%' OR name LIKE 'vacuum_cost%'
 ORDER BY name;
```

### `vacuum.autovac_workers`

- id `118` · 类型 `SQL` · 会话 **只读** · is_valid `1` · 异步 `0`

无参数

```sql
SELECT pid,
       COALESCE(sessionid,0) AS sessionid,
       EXTRACT(EPOCH FROM (now()-xact_start)) AS xact_age_s,
       COALESCE(query,'') AS query
  FROM pg_stat_activity
 WHERE query LIKE 'autovacuum:%';
```

### `vacuum.bloat_estimate`

- id `119` · 类型 `SQL` · 会话 **只读** · is_valid `1` · 异步 `0`

| 参数 | 类型 |
|---|---|
| `limit` | INTEGER |

```sql
SELECT n.nspname AS schema,
       c.relname AS "table",
       c.relpages::bigint                       AS relpages,
       (pg_relation_size(c.oid) / 8192)::bigint AS real_pages,
       c.reltuples::bigint                      AS reltuples,
       pg_relation_size(c.oid)::bigint          AS real_bytes,
       (SELECT sum(s.avg_width)
          FROM pg_stats s
         WHERE s.schemaname = n.nspname AND s.tablename = c.relname) AS avg_row_width
  FROM pg_class c
  JOIN pg_namespace n ON n.oid = c.relnamespace
 WHERE c.relkind = 'r'
   AND n.nspname NOT IN ('pg_catalog','information_schema','snapshot','dbe_perf',
                         'dbe_pldeveloper','cstore','pg_toast')
   AND pg_relation_size(c.oid) > 8192 * 128
 ORDER BY pg_relation_size(c.oid) DESC
 LIMIT {{limit}};
```

### `vacuum.dead_tuples`

- id `120` · 类型 `SQL` · 会话 **只读** · is_valid `1` · 异步 `0`

| 参数 | 类型 |
|---|---|
| `limit` | INTEGER |

```sql
SELECT n.nspname AS schema,
       t.relname AS "table",
       t.n_live_tup AS n_live_tup,
       t.n_dead_tup AS n_dead_tup,
       c.reltuples AS reltuples,
       pg_total_relation_size(c.oid) AS table_bytes,
       EXTRACT(EPOCH FROM (now()-t.last_autovacuum)) AS last_autovacuum_age_s,
       EXTRACT(EPOCH FROM (now()-t.last_vacuum)) AS last_vacuum_age_s,
       t.vacuum_count AS vacuum_count,
       t.autovacuum_count AS autovacuum_count,
       CASE WHEN 'autovacuum_enabled=false' = ANY(c.reloptions) THEN false ELSE true END AS autovac_enabled,
       COALESCE(array_to_string(c.reloptions, ','), '') AS reloptions
  FROM pg_stat_user_tables t
  JOIN pg_class c ON c.oid = t.relid
  JOIN pg_namespace n ON n.oid = c.relnamespace
 WHERE n.nspname NOT IN ('pg_catalog','information_schema','snapshot','dbe_perf','dbe_pldeveloper','cstore')
 ORDER BY t.n_dead_tup DESC
 LIMIT {{limit}};
```

### `vacuum.freeze_age`

- id `121` · 类型 `SQL` · 会话 **只读** · is_valid `1` · 异步 `0`

无参数

```sql
SELECT d.datname,
       (txid_snapshot_xmin(txid_current_snapshot())
        - d.datfrozenxid64::text::numeric)::bigint      AS freeze_age,
       (SELECT setting::numeric FROM pg_settings
         WHERE name = 'autovacuum_freeze_max_age')::bigint AS max_age,
       (SELECT setting FROM pg_settings
         WHERE name = 'vacuum_freeze_table_age')         AS freeze_table_age
  FROM pg_database d
 WHERE d.datallowconn
 ORDER BY freeze_age DESC;
```

### `vacuum.kernel_info`

- id `122` · 类型 `SQL` · 会话 **只读** · is_valid `1` · 异步 `0`

无参数

```sql
SELECT node_name, module, name, value
  FROM gs_get_kernel_info();
```

### `vacuum.oldest_xmin`

- id `123` · 类型 `SQL` · 会话 **只读** · is_valid `1` · 异步 `0`

无参数

```sql
SELECT 'long_xact' AS source,
       CAST(pid AS text) AS identifier,
       EXTRACT(EPOCH FROM (now()-xact_start)) AS xmin_age_s,
       'usename=' || COALESCE(usename,'') || ' state=' || COALESCE(state,'') ||
         ' query=' || COALESCE(substr(query,1,200),'') AS detail
  FROM pg_stat_activity
 WHERE xact_start IS NOT NULL
   AND state IN ('active','idle in transaction')
   AND client_port IS NOT NULL
   AND pid <> pg_backend_pid()
UNION ALL
SELECT 'prepared_xact' AS source,
       gid AS identifier,
       EXTRACT(EPOCH FROM (now()-prepared)) AS xmin_age_s,
       'owner=' || COALESCE(owner,'') || ' database=' || COALESCE(database,'') AS detail
  FROM pg_prepared_xacts
UNION ALL
SELECT 'replication_slot' AS source,
       slot_name AS identifier,
       CAST(NULL AS double precision) AS xmin_age_s,
       'xmin=' || COALESCE(CAST(xmin AS text),'') ||
         ' catalog_xmin=' || COALESCE(CAST(catalog_xmin AS text),'') ||
         ' active=' || CAST(active AS text) AS detail
  FROM pg_replication_slots
 WHERE xmin IS NOT NULL OR catalog_xmin IS NOT NULL
ORDER BY xmin_age_s DESC NULLS FIRST;
```

### `waitevent.events`

- id `124` · 类型 `SQL` · 会话 **只读** · is_valid `1` · 异步 `0`

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

- id `125` · 类型 `SQL` · 会话 **只读** · is_valid `1` · 异步 `0`

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

- id `126` · 类型 `SQL` · 会话 **只读** · is_valid `1` · 异步 `0`

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
SELECT e.snap_relname,
       (e.phys - COALESCE(b.phys, 0)) AS phys_read,
       (e.logi - COALESCE(b.logi, 0)) AS logical_read
FROM e LEFT JOIN b USING (db_name, snap_schemaname, snap_relname)
WHERE (e.phys - COALESCE(b.phys, 0)) > 0
ORDER BY phys_read DESC LIMIT {{top}};
```

### `wdr.checkpoint`

- id `127` · 类型 `SQL` · 会话 **只读** · is_valid `1` · 异步 `0`

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

- id `128` · 类型 `SQL` · 会话 **只读** · is_valid `1` · 异步 `0`

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
SELECT COALESCE(SUM(e.snap_xact_commit - COALESCE(b.snap_xact_commit, 0)),0)     AS xact_commit,
       COALESCE(SUM(e.snap_xact_rollback - COALESCE(b.snap_xact_rollback, 0)),0) AS xact_rollback,
       COALESCE(SUM(e.snap_deadlocks - COALESCE(b.snap_deadlocks, 0)),0)         AS deadlocks,
       COALESCE(SUM(e.snap_temp_bytes - COALESCE(b.snap_temp_bytes, 0)),0)       AS temp_bytes,
       COALESCE(SUM(e.snap_blks_hit - COALESCE(b.snap_blks_hit, 0)),0)           AS blks_hit,
       COALESCE(SUM(e.snap_blks_read - COALESCE(b.snap_blks_read, 0)),0)         AS blks_read
FROM e LEFT JOIN b USING (snap_datname);
```

### `wdr.db_summary`

- id `129` · 类型 `SQL` · 会话 **只读** · is_valid `1` · 异步 `0`

| 参数 | 类型 |
|---|---|
| `b` | INTEGER |
| `e` | INTEGER |

```sql
WITH b AS (SELECT snap_datname, snap_xact_commit, snap_blks_read, snap_blks_hit
             FROM snapshot.snap_summary_stat_database WHERE snapshot_id={{b}}),
     e AS (SELECT snap_datname, snap_xact_commit, snap_blks_read, snap_blks_hit
             FROM snapshot.snap_summary_stat_database WHERE snapshot_id={{e}})
SELECT COALESCE(SUM(e.snap_xact_commit - COALESCE(b.snap_xact_commit, 0)),0) AS xact_commit,
       COALESCE(SUM(e.snap_blks_read - COALESCE(b.snap_blks_read, 0)),0)     AS blks_read,
       COALESCE(SUM(e.snap_blks_hit - COALESCE(b.snap_blks_hit, 0)),0)       AS blks_hit
FROM e LEFT JOIN b USING (snap_datname);
```

### `wdr.file_io`

- id `130` · 类型 `SQL` · 会话 **只读** · is_valid `1` · 异步 `0`

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
       (e.reads  - COALESCE(b.reads,  0)) AS reads,
       (e.writes - COALESCE(b.writes, 0)) AS writes
FROM e LEFT JOIN b USING (snap_filenum, snap_dbid, snap_spcid)
WHERE (e.reads  - COALESCE(b.reads,  0)) > 0
   OR (e.writes - COALESCE(b.writes, 0)) > 0
ORDER BY reads DESC LIMIT {{top}};
```

### `wdr.load_profile`

- id `131` · 类型 `SQL` · 会话 **只读** · is_valid `1` · 异步 `0`

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

- id `132` · 类型 `SQL` · 会话 **只读** · is_valid `1` · 异步 `0`

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

- id `133` · 类型 `SQL` · 会话 **只读** · is_valid `1` · 异步 `0`

无参数

```sql
SHOW pgxc_node_name;
```

### `wdr.plan_in_window`

- id `134` · 类型 `SQL` · 会话 **只读** · is_valid `1` · 异步 `0`

| 参数 | 类型 |
|---|---|
| `sid` | INTEGER |
| `begin_time` | DATETIME |
| `end_time` | DATETIME |

```sql
SELECT to_char(start_time, 'YYYY-MM-DD HH24:MI:SS') AS start_time,
       round(db_time / 1000.0, 1) AS db_time_ms,
       n_returned_rows,
       query_plan
  FROM dbe_perf.statement_history
 WHERE unique_query_id = {{sid}}
   AND start_time >= CAST('{{begin_time}}' AS TIMESTAMP)
   AND start_time <= CAST('{{end_time}}' AS TIMESTAMP)
   AND query_plan IS NOT NULL AND query_plan <> ''
 ORDER BY start_time DESC
 LIMIT 1;
```

### `wdr.snapshots`

- id `135` · 类型 `SQL` · 会话 **只读** · is_valid `1` · 异步 `0`

| 参数 | 类型 |
|---|---|
| `limit` | INTEGER |

```sql
SELECT snapshot_id,
       to_char(start_ts,'YYYY-MM-DD HH24:MI:SS') AS start_ts,
       to_char(end_ts,'YYYY-MM-DD HH24:MI:SS')   AS end_ts,
       round(EXTRACT(EPOCH FROM (end_ts-start_ts))/60)::bigint AS dur_min
FROM snapshot.snapshot ORDER BY snapshot_id DESC LIMIT {{limit}};
```

### `wdr.top_sql`

- id `136` · 类型 `SQL` · 会话 **只读** · is_valid `1` · 异步 `0`

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
       (e.calls   - COALESCE(b.calls,   0)) AS calls,
       (e.elapsed - COALESCE(b.elapsed, 0)) AS elapsed_us,
       (e.cpu     - COALESCE(b.cpu,     0)) AS cpu_us,
       (e.spill   - COALESCE(b.spill,   0)) AS spill_kb,
       (e.phys    - COALESCE(b.phys,    0)) AS phys_blocks
FROM e LEFT JOIN b USING (sid)
WHERE (e.elapsed - COALESCE(b.elapsed, 0)) > 0
ORDER BY elapsed_us DESC LIMIT {{top}};
```

### `wdr.waits`

- id `137` · 类型 `SQL` · 会话 **只读** · is_valid `1` · 异步 `0`

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

- id `138` · 类型 `SQL` · 会话 **只读** · is_valid `1` · 异步 `0`

无参数

```sql
SHOW enable_wdr_snapshot;
```

### `wdr.window`

- id `139` · 类型 `SQL` · 会话 **只读** · is_valid `1` · 异步 `0`

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

