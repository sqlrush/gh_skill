#=============================health Start==================================================

skill_health_001= """
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
FROM pg_stat_database """


skill_health_002 = """
SELECT wait_status, count(*) AS cnt
FROM pg_thread_wait_status
WHERE wait_status IS NOT NULL AND wait_status NOT IN ('none','wait cmd')
GROUP BY wait_status
ORDER BY cnt DESC """


skill_health_003 = """
SELECT
  unique_sql_id::text,
  LEFT(REGEXP_REPLACE(query, E'\\s+', ' ', 'g'), 180) AS query,
  n_calls AS calls,
  ROUND((total_elapse_time/NULLIF(n_calls,0))/1000::numeric, 2) AS avg_ms,
  ROUND(total_elapse_time/1000000::numeric, 2) AS total_sec,
  ROUND(cpu_time/1000000::numeric, 2) AS cpu_sec,
  n_returned_rows AS rows
FROM dbe_perf.statement
WHERE (total_elapse_time/NULLIF(n_calls,0))/1000 > %s
  AND n_calls > 0
ORDER BY total_elapse_time/NULLIF(n_calls,0) DESC
LIMIT %s """

skill_health_004 = """
SELECT pid, COALESCE(usename,''), state,
       EXTRACT(EPOCH FROM (now()-xact_start)) AS xact_age_s,
       EXTRACT(EPOCH FROM (now()-state_change)) AS state_age_s,
       COALESCE(query,'')
FROM pg_stat_activity
WHERE state IN ('active','idle in transaction') AND xact_start IS NOT NULL
  AND COALESCE(connection_info,'') <> ''
ORDER BY xact_start
LIMIT %s """


skill_health_005 = """
SELECT t.schemaname, t.relname, t.n_live_tup, t.n_dead_tup,
       EXTRACT(EPOCH FROM (now()-t.last_autovacuum)) AS last_autovacuum_age_s,
       CASE WHEN 'autovacuum_enabled=false' = ANY(c.reloptions) THEN false ELSE true END AS autovac_enabled
FROM pg_stat_user_tables t
JOIN pg_class c ON c.oid = t.relid
WHERE t.n_dead_tup > 0+
  AND t.schemaname NOT IN ('pg_catalog','information_schema','snapshot','dbe_perf','dbe_pldeveloper','cstore')
ORDER BY t.n_dead_tup::numeric/GREATEST(t.n_live_tup+t.n_dead_tup,1) DESC
LIMIT %s """


skill_health_006 = """
SELECT COALESCE(wait_event,'<lwlock>') AS evt, count(*) AS cnt
FROM pg_thread_wait_status
WHERE lower(wait_status) LIKE '%lwlock%'
GROUP BY wait_event
ORDER BY cnt DESC
LIMIT %s """


skill_health_007 = """
WITH RECURSIVE waits AS (
  SELECT w.pid AS waiter, h.pid AS holder
  FROM pg_locks w
  JOIN pg_locks h ON h.granted AND NOT w.granted
     AND h.locktype=w.locktype AND h.database IS NOT DISTINCT FROM w.database
     AND h.relation IS NOT DISTINCT FROM w.relation
     AND h.transactionid IS NOT DISTINCT FROM w.transactionid
     AND h.pid<>w.pid),
chain AS (
  SELECT holder AS root, waiter, 1 AS depth FROM waits
  UNION ALL
  SELECT c.root, w.waiter, c.depth+1 FROM chain c JOIN waits w ON w.holder=c.waiter
  WHERE c.depth < 20)
SELECT c.root, max(c.depth) AS depth, count(DISTINCT c.waiter) AS waiters,
       COALESCE(a.state,''),
       EXTRACT(EPOCH FROM (now()-a.xact_start)),
       EXTRACT(EPOCH FROM (now()-a.state_change))
FROM chain c LEFT JOIN pg_stat_activity a ON a.pid=c.root
GROUP BY c.root, a.state, a.xact_start, a.state_change
ORDER BY depth DESC, waiters DESC
LIMIT %s """


skill_health_008 = """
("SELECT COALESCE(state,'<null>') AS state, count(*) "
           "FROM pg_stat_activity GROUP BY state ORDER BY 2 DESC")
           """		   

skill_health_009 = """
SELECT COALESCE(query,'') q, count(*) c, sum(count(*)) OVER () AS total
FROM pg_stat_activity
WHERE state='active' AND COALESCE(query,'')<>'' AND COALESCE(connection_info,'')<>''
GROUP BY query ORDER BY c DESC LIMIT 1 """


skill_health_010 = """
SELECT application_name, COALESCE(client_addr::text,''), state, sync_state,
       pg_xlog_location_diff(sender_sent_location, receiver_replay_location)::bigint AS lag_bytes
FROM pg_stat_replication """



skill_health_011 = """
 SELECT s.schemaname||'.'||s.indexrelname, pg_relation_size(s.indexrelid)
FROM pg_stat_user_indexes s
JOIN pg_index i ON i.indexrelid = s.indexrelid
WHERE s.idx_scan=0 AND pg_relation_size(s.indexrelid) > %s
  AND NOT i.indisprimary AND NOT i.indisunique
  AND s.schemaname NOT IN {schema_filter}
ORDER BY pg_relation_size(s.indexrelid) DESC LIMIT %s """



skill_health_012 = """ 
SELECT schemaname||'.'||relname FROM pg_stat_user_tables
WHERE n_live_tup > %s AND schemaname NOT IN {schema_filter}
  AND (last_analyze IS NULL OR (last_data_changed IS NOT NULL AND last_data_changed > last_analyze))
ORDER BY n_live_tup DESC LIMIT %s """


#=============================health END==================================================







#=============================memanalyze Start==================================================

skill_memanalyze_001 = """
SELECT a.attname::text
FROM pg_class c
JOIN pg_namespace n ON n.oid = c.relnamespace
JOIN pg_attribute a ON a.attrelid = c.oid
WHERE n.nspname = %s AND c.relname = %s
  AND a.attnum > 0 AND NOT a.attisdropped
ORDER BY a.attnum """


skill_memanalyze_002 = """
SELECT a.attname::text
FROM pg_class c
JOIN pg_namespace n ON n.oid = c.relnamespace
JOIN pg_attribute a ON a.attrelid = c.oid
WHERE c.relname = %s AND n.nspname IN ({schemas})
  AND a.attnum > 0 AND NOT a.attisdropped
ORDER BY a.attnum """

#=============================memanalyze END==================================================




#=============================procinfo start==================================================\

skill_procinfo_001 =  """
SELECT n.nspname, p.proname, l.lanname, p.prosrc,
       pg_catalog.pg_get_function_arguments(p.oid)
FROM pg_proc p
JOIN pg_namespace n ON n.oid = p.pronamespace
JOIN pg_language l ON l.oid = p.prolang
WHERE p.proname = %s AND (%s = '' OR n.nspname = %s)
ORDER BY (n.nspname = 'public') DESC, n.nspname
LIMIT 1 """


skill_procinfo_002 =  """
SELECT name, setting, COALESCE(unit, '')
FROM pg_settings
WHERE name IN (
  'work_mem', 'maintenance_work_mem', 'shared_buffers',
  'effective_cache_size', 'random_page_cost', 'seq_page_cost',
  'max_parallel_workers_per_gather', 'from_collapse_limit',
  'join_collapse_limit', 'geqo_threshold', 'default_statistics_target')
ORDER BY name """



#=============================procinfo END==================================================




#=============================proctune Start==================================================
      
skill_proctune_001 = """
SELECT n.nspname, c.relname, c.relpages, c.reltuples::bigint, c.relkind,
       pg_total_relation_size(c.oid) / 1024.0 / 1024.0
FROM pg_class c
LEFT JOIN pg_namespace n ON c.relnamespace = n.oid
WHERE c.relname IN {names} AND c.relkind IN ('r','v','p','m') """


skill_proctune_002 = """
SELECT t.relname, i.relname, ix.indisunique, ix.indisprimary,
       pg_get_indexdef(ix.indexrelid)
FROM pg_class t
JOIN pg_index ix ON t.oid = ix.indrelid
JOIN pg_class i ON i.oid = ix.indexrelid
WHERE t.relname IN {names} """


skill_proctune_003 = """
SELECT tablename, attname, n_distinct, null_frac, avg_width, correlation
FROM pg_stats
WHERE tablename IN {names} """


skill_proctune_004 = """
SELECT name, setting, COALESCE(unit, '')
FROM pg_settings
WHERE name IN (
  'work_mem', 'maintenance_work_mem', 'shared_buffers',
  'effective_cache_size', 'random_page_cost', 'seq_page_cost',
  'max_parallel_workers_per_gather', 'from_collapse_limit',
  'join_collapse_limit', 'geqo_threshold', 'default_statistics_target')
ORDER BY name """


skill_proctune_005 = """
SELECT n.nspname, p.proname, l.lanname, p.prosrc,
       pg_catalog.pg_get_function_arguments(p.oid)
FROM pg_proc p
JOIN pg_namespace n ON n.oid = p.pronamespace
JOIN pg_language l ON l.oid = p.prolang
WHERE p.proname = %s AND (%s = '' OR n.nspname = %s)
ORDER BY (n.nspname = 'public') DESC, n.nspname
LIMIT 1 """


skill_proctune_006 = """
SELECT schema_name, query
FROM dbe_perf.statement_history
WHERE unique_query_id = {sid}
  AND query NOT LIKE '/* missing SQL statement%'
ORDER BY start_time DESC
LIMIT 1 """


skill_proctune_007 = """
SELECT query FROM dbe_perf.statement
WHERE unique_sql_id = {sid}
  AND query IS NOT NULL
  AND query <> ''
LIMIT 1"""

#=============================proctune END==================================================


#=============================slowsql Start==================================================

skill_slowsql_001= """
SELECT
  unique_sql_id::text,
  LEFT(REGEXP_REPLACE(query, E'\\s+', ' ', 'g'), 180) AS query,
  n_calls AS calls,
  ROUND((total_elapse_time/NULLIF(n_calls,0))/1000::numeric, 2) AS avg_ms,
  ROUND(total_elapse_time/1000000::numeric, 2) AS total_sec,
  ROUND(cpu_time/1000000::numeric, 2) AS cpu_sec,
  n_returned_rows AS rows
FROM dbe_perf.statement
WHERE (total_elapse_time/NULLIF(n_calls,0))/1000 > {threshold_ms}
  AND n_calls > 0 AND last_updated >= CAST('{begin_time}' AS TIMESTAMP)
ORDER BY total_elapse_time/NULLIF(n_calls,0) DESC
LIMIT {limit} """


#=============================slowsql END==================================================


#=============================sqlfetch Start==================================================

skill_sqlfetch_001 = """
SELECT schema_name, query
FROM dbe_perf.statement_history
WHERE unique_query_id = {sid}
  AND query NOT LIKE '/* missing SQL statement%'
ORDER BY start_time DESC
LIMIT 1 """


skill_sqlfetch_002 = """
SELECT query FROM dbe_perf.statement
WHERE unique_sql_id = {sid}
  AND query IS NOT NULL
  AND query <> ''
LIMIT 1 """

#=============================sqlfetch END==================================================



#=============================sqlreview Start==================================================

skill_sqlreview_001 = """
SELECT
  n.nspname::text                                              AS schema,
  c.relname::text                                              AS table,
  EXISTS (SELECT 1 FROM pg_constraint pk
          WHERE pk.conrelid = c.oid AND pk.contype = 'p')      AS has_pk,
  COALESCE((SELECT array_agg(fk.conname::text)
            FROM pg_constraint fk
            WHERE fk.conrelid = c.oid AND fk.contype = 'f'),
           ARRAY[]::text[])                                    AS fks,
  COALESCE((SELECT array_agg(a.attname::text ORDER BY a.attnum)
            FROM pg_attribute a
            WHERE a.attrelid = c.oid AND a.attnum > 0 AND NOT a.attisdropped),
           ARRAY[]::text[])                                    AS columns
FROM pg_class c
JOIN pg_namespace n ON n.oid = c.relnamespace
WHERE c.relkind = 'r' AND n.nspname = %s
ORDER BY c.relname"""


skill_sqlreview_002 = """
SELECT
  n.nspname::text                                              AS schema,
  t.relname::text                                              AS table,
  i.relname::text                                              AS name,
  COALESCE((SELECT array_agg(a.attname::text ORDER BY k.ord)
            FROM (SELECT s AS ord,
                         (string_to_array(ix.indkey::text, ' '))[s]::smallint AS attnum
                  FROM generate_series(
                         1,
                         array_length(string_to_array(ix.indkey::text, ' '), 1)) s) k
            JOIN pg_attribute a
              ON a.attrelid = t.oid AND a.attnum = k.attnum),
           ARRAY[]::text[])                                    AS columns,
  ix.indisunique                                               AS is_unique,
  ix.indisprimary                                              AS is_primary,
  COALESCE(s.idx_scan, 0)                                      AS scans
FROM pg_index ix
JOIN pg_class i     ON i.oid = ix.indexrelid
JOIN pg_class t     ON t.oid = ix.indrelid
JOIN pg_namespace n ON n.oid = t.relnamespace
LEFT JOIN pg_stat_user_indexes s ON s.indexrelid = ix.indexrelid
WHERE n.nspname = %s
ORDER BY t.relname, i.relname"""


skill_sqlreview_003 = """
SELECT schema_name, query
FROM dbe_perf.statement_history
WHERE unique_query_id = {sid}
  AND query NOT LIKE '/* missing SQL statement%'
ORDER BY start_time DESC
LIMIT 1 """


skill_sqlreview_004 = """
SELECT query FROM dbe_perf.statement
WHERE unique_sql_id = {sid}
  AND query IS NOT NULL
  AND query <> ''
LIMIT 1 """



skill_sqlreview_005 = """
SELECT unique_sql_id::text, query
FROM dbe_perf.statement
WHERE n_calls > 0 AND query IS NOT NULL AND query <> ''
ORDER BY total_elapse_time DESC
LIMIT {limit} """

#=============================sqlreview END==================================================


#=============================sqltune Start==================================================
skill_sqltune_001 = """
SELECT n.nspname, c.relname, c.relpages, c.reltuples::bigint, c.relkind,
       pg_total_relation_size(c.oid) / 1024.0 / 1024.0
FROM pg_class c
LEFT JOIN pg_namespace n ON c.relnamespace = n.oid
WHERE c.relname IN {names} AND c.relkind IN ('r','v','p','m') """


skill_sqltune_002 = """
SELECT t.relname, i.relname, ix.indisunique, ix.indisprimary,
       pg_get_indexdef(ix.indexrelid)
FROM pg_class t
JOIN pg_index ix ON t.oid = ix.indrelid
JOIN pg_class i ON i.oid = ix.indexrelid
WHERE t.relname IN {names} """


skill_sqltune_003 = """
SELECT tablename, attname, n_distinct, null_frac, avg_width, correlation
FROM pg_stats
WHERE tablename IN {names} """


skill_sqltune_004 = """
SELECT name, setting, COALESCE(unit, '')
FROM pg_settings
WHERE name IN (
  'work_mem', 'maintenance_work_mem', 'shared_buffers',
  'effective_cache_size', 'random_page_cost', 'seq_page_cost',
  'max_parallel_workers_per_gather', 'from_collapse_limit',
  'join_collapse_limit', 'geqo_threshold', 'default_statistics_target')
ORDER BY name """
  
  
skill_sqltune_005 = """
SELECT schema_name, query
FROM dbe_perf.statement_history
WHERE unique_query_id = {sid}
  AND query NOT LIKE '/* missing SQL statement%'
ORDER BY start_time DESC
LIMIT 1 """


        
skill_sqltune_006 = """
SELECT query FROM dbe_perf.statement
WHERE unique_sql_id = {sid}
  AND query IS NOT NULL
  AND query <> ''
LIMIT 1 """
#=============================sqltune END==================================================


#=============================topproc Start---双占位符==================================================

skill_topproc_001 = """
SELECT n.nspname, p.proname, s.calls,
       ROUND(s.total_time::numeric, 2), ROUND(s.self_time::numeric, 2)
FROM pg_stat_user_functions s
JOIN pg_proc p ON p.oid = s.funcid
JOIN pg_namespace n ON n.oid = p.pronamespace
ORDER BY {order} NULLS LAST
LIMIT {limit} """

#=============================topproc END==================================================


#============================topsql Start---双占位符==================================================

skill_topsql_001 = """
SELECT
  unique_sql_id::text,
  LEFT(REGEXP_REPLACE(query, E'\\s+', ' ', 'g'), 80) AS query,
  n_calls AS calls,
  ROUND(total_elapse_time/1000000::numeric, 2) AS total_sec,
  ROUND((total_elapse_time/NULLIF(n_calls,0))/1000::numeric, 2) AS avg_ms,
  n_returned_rows AS rows
FROM dbe_perf.statement
WHERE n_calls > 0
ORDER BY {order}
LIMIT {limit} """


#=============================topsql END==================================================



#=============================wdr Start---双占位符==================================================

skill_wdr_001 = """
WITH b AS (SELECT snap_unique_sql_id AS sid, sum(snap_total_elapse_time) AS t, sum(snap_cpu_time) AS c FROM snapshot.snap_summary_statement WHERE snapshot_id={b} GROUP BY snap_unique_sql_id),
     e AS (SELECT snap_unique_sql_id AS sid, sum(snap_total_elapse_time) AS t, sum(snap_cpu_time) AS c FROM snapshot.snap_summary_statement WHERE snapshot_id={e} GROUP BY snap_unique_sql_id)
SELECT COALESCE(SUM(e.t-b.t),0), COALESCE(SUM(e.c-b.c),0) FROM e JOIN b USING (sid) """


#双占位符

skill_wdr_002 = """
WITH b AS (SELECT snap_datname, snap_xact_commit, snap_blks_read, snap_blks_hit FROM snapshot.snap_summary_stat_database WHERE snapshot_id={b}),
     e AS (SELECT snap_datname, snap_xact_commit, snap_blks_read, snap_blks_hit FROM snapshot.snap_summary_stat_database WHERE snapshot_id={e})
SELECT COALESCE(SUM(e.snap_xact_commit-b.snap_xact_commit),0),
       COALESCE(SUM(e.snap_blks_read-b.snap_blks_read),0),
       COALESCE(SUM(e.snap_blks_hit-b.snap_blks_hit),0)
FROM e JOIN b USING (snap_datname) """


#双占位符

skill_wdr_003 = """
WITH b AS (SELECT snap_datname, snap_xact_commit, snap_xact_rollback, snap_deadlocks, snap_temp_bytes, snap_blks_hit, snap_blks_read
             FROM snapshot.snap_summary_stat_database WHERE snapshot_id={b}),
     e AS (SELECT snap_datname, snap_xact_commit, snap_xact_rollback, snap_deadlocks, snap_temp_bytes, snap_blks_hit, snap_blks_read
             FROM snapshot.snap_summary_stat_database WHERE snapshot_id={e})
SELECT COALESCE(SUM(e.snap_xact_commit-b.snap_xact_commit),0),
       COALESCE(SUM(e.snap_xact_rollback-b.snap_xact_rollback),0),
       COALESCE(SUM(e.snap_deadlocks-b.snap_deadlocks),0),
       COALESCE(SUM(e.snap_temp_bytes-b.snap_temp_bytes),0),
       COALESCE(SUM(e.snap_blks_hit-b.snap_blks_hit),0),
       COALESCE(SUM(e.snap_blks_read-b.snap_blks_read),0)
FROM e JOIN b USING (snap_datname) """


#双占位符

skill_wdr_004 = """
WITH b AS (SELECT snap_unique_sql_id AS sid, max(snap_query) AS query,
                  sum(snap_n_calls) AS calls, sum(snap_total_elapse_time) AS elapsed, sum(snap_cpu_time) AS cpu,
                  sum(COALESCE(snap_sort_spill_size,0)+COALESCE(snap_hash_spill_size,0)) AS spill,
                  sum(snap_n_blocks_fetched-snap_n_blocks_hit) AS phys
             FROM snapshot.snap_summary_statement WHERE snapshot_id={b} GROUP BY snap_unique_sql_id),
     e AS (SELECT snap_unique_sql_id AS sid, max(snap_query) AS query,
                  sum(snap_n_calls) AS calls, sum(snap_total_elapse_time) AS elapsed, sum(snap_cpu_time) AS cpu,
                  sum(COALESCE(snap_sort_spill_size,0)+COALESCE(snap_hash_spill_size,0)) AS spill,
                  sum(snap_n_blocks_fetched-snap_n_blocks_hit) AS phys
             FROM snapshot.snap_summary_statement WHERE snapshot_id={e} GROUP BY snap_unique_sql_id)
SELECT e.sid, e.query,
       (e.calls-b.calls)       AS calls,
       (e.elapsed-b.elapsed)   AS elapsed_us,
       (e.cpu-b.cpu)           AS cpu_us,
       (e.spill-b.spill)       AS spill_kb,
       (e.phys-b.phys)         AS phys_blocks
FROM e JOIN b USING (sid)
WHERE (e.elapsed-b.elapsed) > 0
ORDER BY elapsed_us DESC LIMIT {top} """


#多占位符

skill_wdr_005 = """
WITH b AS (SELECT snap_type AS wait_class, snap_event AS event, sum(snap_wait) AS waits, sum(snap_total_wait_time) AS wt
             FROM snapshot.snap_global_wait_events WHERE snapshot_id={b} GROUP BY snap_type, snap_event),
     e AS (SELECT snap_type AS wait_class, snap_event AS event, sum(snap_wait) AS waits, sum(snap_total_wait_time) AS wt
             FROM snapshot.snap_global_wait_events WHERE snapshot_id={c} GROUP BY snap_type, snap_event)
SELECT e.wait_class,
       SUM(e.waits-b.waits) AS waits,
       SUM(e.wt-b.wt)       AS wait_us
FROM e JOIN b USING (wait_class, event)
WHERE upper(e.wait_class) NOT IN ('STATUS','NONE')
GROUP BY e.wait_class
HAVING SUM(e.wt-b.wt) > 0
ORDER BY wait_us DESC LIMIT {top} """

#多占位符

skill_wdr_006 = """
WITH b AS (SELECT snap_node_name, snap_checkpoints_timed AS timed, snap_checkpoints_req AS req
             FROM snapshot.snap_global_bgwriter_stat WHERE snapshot_id={b}),
     e AS (SELECT snap_node_name, snap_checkpoints_timed AS timed, snap_checkpoints_req AS req
             FROM snapshot.snap_global_bgwriter_stat WHERE snapshot_id={e})
SELECT COALESCE(SUM(e.timed-b.timed),0), COALESCE(SUM(e.req-b.req),0)
FROM e JOIN b USING (snap_node_name) """



#多占位符

skill_wdr_007 = """
WITH b AS (SELECT db_name, snap_schemaname, snap_relname,
                  (COALESCE(snap_heap_blks_read,0)+COALESCE(snap_idx_blks_read,0)) AS phys,
                  (COALESCE(snap_heap_blks_hit,0)+COALESCE(snap_idx_blks_hit,0))   AS logi
             FROM snapshot.snap_summary_statio_all_tables WHERE snapshot_id={b}),
     e AS (SELECT db_name, snap_schemaname, snap_relname,
                  (COALESCE(snap_heap_blks_read,0)+COALESCE(snap_idx_blks_read,0)) AS phys,
                  (COALESCE(snap_heap_blks_hit,0)+COALESCE(snap_idx_blks_hit,0))   AS logi
             FROM snapshot.snap_summary_statio_all_tables WHERE snapshot_id={e})
SELECT e.snap_relname, (e.phys-b.phys) AS phys_read, (e.logi-b.logi) AS logical_read
FROM e JOIN b USING (db_name, snap_schemaname, snap_relname)
WHERE (e.phys-b.phys) > 0
ORDER BY phys_read DESC LIMIT {top} """



#多占位符

skill_wdr_008 = """
WITH b AS (SELECT snap_filenum, snap_dbid, snap_spcid, snap_phyrds AS reads, snap_phywrts AS writes
             FROM snapshot.snap_summary_file_iostat WHERE snapshot_id={b}),
     e AS (SELECT snap_filenum, snap_dbid, snap_spcid, snap_phyrds AS reads, snap_phywrts AS writes
             FROM snapshot.snap_summary_file_iostat WHERE snapshot_id={e})
SELECT ('db'||e.snap_dbid||'/spc'||e.snap_spcid||'/f'||e.snap_filenum) AS filename,
       (e.reads-b.reads)  AS reads,
       (e.writes-b.writes) AS writes
FROM e JOIN b USING (snap_filenum, snap_dbid, snap_spcid)
WHERE (e.reads-b.reads) > 0 OR (e.writes-b.writes) > 0
ORDER BY reads DESC LIMIT {top} """


#多占位符

skill_wdr_009 = """
SELECT to_char(b.start_ts,'YYYY-MM-DD HH24:MI') AS b_start,
       to_char(e.start_ts,'YYYY-MM-DD HH24:MI') AS e_start,
       round(EXTRACT(EPOCH FROM (e.start_ts-b.start_ts))/60)::bigint AS dur
FROM (SELECT start_ts FROM snapshot.snapshot WHERE snapshot_id={begin}) b,
     (SELECT start_ts FROM snapshot.snapshot WHERE snapshot_id={end}) e """
	 
	 

skill_wdr_010 = """ SELECT snapshot_id,
       to_char(start_ts,'YYYY-MM-DD HH24:MI') AS start_ts,
       to_char(end_ts,'YYYY-MM-DD HH24:MI')   AS end_ts,
       round(EXTRACT(EPOCH FROM (end_ts-start_ts))/60)::bigint AS dur_min
FROM snapshot.snapshot ORDER BY snapshot_id DESC LIMIT {limit} """
	 
	 
	 	 
#=============================wdr END==================================================



	 
	 