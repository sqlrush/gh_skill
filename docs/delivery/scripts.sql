
INSERT INTO grmp.script_config (id, script_type, script_name, database_type, refered_appbusiness, kernel_version, region, deployment_form, execute_node_type, cluster_deployment_mode, script_content, parameter_config, scene, is_valid, create_user, create_time, last_modify_user, last_modify_time, is_asyn, "extend", compliance_mode, uuid) VALUES (grmp.script_config_seq.nextval, 'SQL', 'explain.plan_text', 'appbusiness', 1, 'ALL', NULL, NULL, NULL, 'centralization', 'EXPLAIN (ANALYZE false, BUFFERS false, FORMAT TEXT) {{sql}}
', '[{"key":"sql","value":"","type":"STRING","autoAcquire":false}]', 'AGENT', 1, '999999999', now(), '999999999', NULL, 0, NULL, 'ALL', uuid());
INSERT INTO grmp.script_config (id, script_type, script_name, database_type, refered_appbusiness, kernel_version, region, deployment_form, execute_node_type, cluster_deployment_mode, script_content, parameter_config, scene, is_valid, create_user, create_time, last_modify_user, last_modify_time, is_asyn, "extend", compliance_mode, uuid) VALUES (grmp.script_config_seq.nextval, 'SQL', 'explain.plan_text_analyze', 'appbusiness', 1, 'ALL', NULL, NULL, NULL, 'centralization', 'EXPLAIN (ANALYZE false, BUFFERS false, FORMAT TEXT) {{sql}}
', '[{"key":"sql","value":"","type":"STRING","autoAcquire":false}]', 'AGENT', 1, '999999999', now(), '999999999', NULL, 0, NULL, 'ALL', uuid());
INSERT INTO grmp.script_config (id, script_type, script_name, database_type, refered_appbusiness, kernel_version, region, deployment_form, execute_node_type, cluster_deployment_mode, script_content, parameter_config, scene, is_valid, create_user, create_time, last_modify_user, last_modify_time, is_asyn, "extend", compliance_mode, uuid) VALUES (grmp.script_config_seq.nextval, 'SQL', 'health.archive_mode', 'postgres', 1, 'ALL', NULL, NULL, NULL, 'centralization', 'SELECT setting FROM pg_settings WHERE name=''archive_mode'';
', '[]', 'AGENT', 1, '999999999', now(), '999999999', NULL, 0, NULL, 'ALL', uuid());
INSERT INTO grmp.script_config (id, script_type, script_name, database_type, refered_appbusiness, kernel_version, region, deployment_form, execute_node_type, cluster_deployment_mode, script_content, parameter_config, scene, is_valid, create_user, create_time, last_modify_user, last_modify_time, is_asyn, "extend", compliance_mode, uuid) VALUES (grmp.script_config_seq.nextval, 'SQL', 'health.bgwriter', 'postgres', 1, 'ALL', NULL, NULL, NULL, 'centralization', 'SELECT checkpoints_timed, checkpoints_req FROM pg_stat_bgwriter;
', '[]', 'AGENT', 1, '999999999', now(), '999999999', NULL, 0, NULL, 'ALL', uuid());
INSERT INTO grmp.script_config (id, script_type, script_name, database_type, refered_appbusiness, kernel_version, region, deployment_form, execute_node_type, cluster_deployment_mode, script_content, parameter_config, scene, is_valid, create_user, create_time, last_modify_user, last_modify_time, is_asyn, "extend", compliance_mode, uuid) VALUES (grmp.script_config_seq.nextval, 'SQL', 'health.conn_concentration', 'postgres', 1, 'ALL', NULL, NULL, NULL, 'centralization', 'SELECT COALESCE(query,'''') q, count(*) c, sum(count(*)) OVER () AS total
FROM pg_stat_activity
WHERE state=''active'' AND COALESCE(query,'''')<>'''' AND COALESCE(connection_info,'''')<>''''
GROUP BY query ORDER BY c DESC LIMIT 1;
', '[]', 'AGENT', 1, '999999999', now(), '999999999', NULL, 0, NULL, 'ALL', uuid());
INSERT INTO grmp.script_config (id, script_type, script_name, database_type, refered_appbusiness, kernel_version, region, deployment_form, execute_node_type, cluster_deployment_mode, script_content, parameter_config, scene, is_valid, create_user, create_time, last_modify_user, last_modify_time, is_asyn, "extend", compliance_mode, uuid) VALUES (grmp.script_config_seq.nextval, 'SQL', 'health.conn_states', 'postgres', 1, 'ALL', NULL, NULL, NULL, 'centralization', 'SELECT COALESCE(state,''<null>'') AS state, count(*) AS cnt
FROM pg_stat_activity
GROUP BY state
ORDER BY cnt DESC;
', '[]', 'AGENT', 1, '999999999', now(), '999999999', NULL, 0, NULL, 'ALL', uuid());
INSERT INTO grmp.script_config (id, script_type, script_name, database_type, refered_appbusiness, kernel_version, region, deployment_form, execute_node_type, cluster_deployment_mode, script_content, parameter_config, scene, is_valid, create_user, create_time, last_modify_user, last_modify_time, is_asyn, "extend", compliance_mode, uuid) VALUES (grmp.script_config_seq.nextval, 'SQL', 'health.db_concurrency', 'postgres', 1, 'ALL', NULL, NULL, NULL, 'centralization', 'SELECT deadlocks, xact_commit, xact_rollback
FROM pg_stat_database WHERE datname=current_database();
', '[]', 'AGENT', 1, '999999999', now(), '999999999', NULL, 0, NULL, 'ALL', uuid());
INSERT INTO grmp.script_config (id, script_type, script_name, database_type, refered_appbusiness, kernel_version, region, deployment_form, execute_node_type, cluster_deployment_mode, script_content, parameter_config, scene, is_valid, create_user, create_time, last_modify_user, last_modify_time, is_asyn, "extend", compliance_mode, uuid) VALUES (grmp.script_config_seq.nextval, 'SQL', 'health.db_info', 'postgres', 1, 'ALL', NULL, NULL, NULL, 'centralization', 'select pg_encoding_to_char(encoding) as encoding_name, *
from pg_database
where datname not in (''template1'',''postgres'',''template0'');
', '[]', 'AGENT', 1, '999999999', now(), '999999999', NULL, 0, NULL, 'ALL', uuid());
INSERT INTO grmp.script_config (id, script_type, script_name, database_type, refered_appbusiness, kernel_version, region, deployment_form, execute_node_type, cluster_deployment_mode, script_content, parameter_config, scene, is_valid, create_user, create_time, last_modify_user, last_modify_time, is_asyn, "extend", compliance_mode, uuid) VALUES (grmp.script_config_seq.nextval, 'SQL', 'health.invalid_index', 'postgres', 1, 'ALL', NULL, NULL, NULL, 'centralization', 'SELECT count(*) AS cnt FROM pg_index WHERE NOT indisvalid;
', '[]', 'AGENT', 1, '999999999', now(), '999999999', NULL, 0, NULL, 'ALL', uuid());
INSERT INTO grmp.script_config (id, script_type, script_name, database_type, refered_appbusiness, kernel_version, region, deployment_form, execute_node_type, cluster_deployment_mode, script_content, parameter_config, scene, is_valid, create_user, create_time, last_modify_user, last_modify_time, is_asyn, "extend", compliance_mode, uuid) VALUES (grmp.script_config_seq.nextval, 'SQL', 'health.long_xact', 'postgres', 1, 'ALL', NULL, NULL, NULL, 'centralization', 'SELECT pid,
       COALESCE(usename,'''') AS usename,
       state,
       EXTRACT(EPOCH FROM (now()-xact_start))   AS xact_age_s,
       EXTRACT(EPOCH FROM (now()-state_change)) AS state_age_s,
       COALESCE(query,'''') AS query
FROM pg_stat_activity
WHERE state IN (''active'',''idle in transaction'') AND xact_start IS NOT NULL
  AND COALESCE(connection_info,'''') <> ''''
ORDER BY xact_start
LIMIT {{limit}};
', '[{"key":"limit","value":"","type":"INTEGER","autoAcquire":false}]', 'AGENT', 1, '999999999', now(), '999999999', NULL, 0, NULL, 'ALL', uuid());
INSERT INTO grmp.script_config (id, script_type, script_name, database_type, refered_appbusiness, kernel_version, region, deployment_form, execute_node_type, cluster_deployment_mode, script_content, parameter_config, scene, is_valid, create_user, create_time, last_modify_user, last_modify_time, is_asyn, "extend", compliance_mode, uuid) VALUES (grmp.script_config_seq.nextval, 'SQL', 'health.overview', 'postgres', 1, 'ALL', NULL, NULL, NULL, 'centralization', 'SELECT
  CASE WHEN sum(blks_hit)+sum(blks_read)=0 THEN 100
       ELSE round(100.0*sum(blks_hit)/(sum(blks_hit)+sum(blks_read)),2) END AS cache_hit_pct,
  sum(numbackends)::bigint AS numbackends,
  (SELECT setting::bigint FROM pg_settings WHERE name=''max_connections'') AS max_conn,
  pg_is_in_recovery() AS in_recovery,
  (SELECT COALESCE(EXTRACT(EPOCH FROM now()-min(xact_start)),0)::bigint
   FROM pg_stat_activity
   WHERE state IN (''active'',''idle in transaction'') AND xact_start IS NOT NULL
     AND COALESCE(connection_info,'''')<>'''') AS oldest_xact_s
FROM pg_stat_database;
', '[]', 'AGENT', 1, '999999999', now(), '999999999', NULL, 0, NULL, 'ALL', uuid());
INSERT INTO grmp.script_config (id, script_type, script_name, database_type, refered_appbusiness, kernel_version, region, deployment_form, execute_node_type, cluster_deployment_mode, script_content, parameter_config, scene, is_valid, create_user, create_time, last_modify_user, last_modify_time, is_asyn, "extend", compliance_mode, uuid) VALUES (grmp.script_config_seq.nextval, 'SQL', 'health.prepared_xacts', 'postgres', 1, 'ALL', NULL, NULL, NULL, 'centralization', 'SELECT count(*) AS cnt FROM pg_prepared_xacts;
', '[]', 'AGENT', 1, '999999999', now(), '999999999', NULL, 0, NULL, 'ALL', uuid());
INSERT INTO grmp.script_config (id, script_type, script_name, database_type, refered_appbusiness, kernel_version, region, deployment_form, execute_node_type, cluster_deployment_mode, script_content, parameter_config, scene, is_valid, create_user, create_time, last_modify_user, last_modify_time, is_asyn, "extend", compliance_mode, uuid) VALUES (grmp.script_config_seq.nextval, 'SQL', 'health.replication', 'postgres', 1, 'ALL', NULL, NULL, NULL, 'centralization', 'SELECT application_name,
       COALESCE(client_addr::text,'''') AS client_addr,
       state, sync_state,
       pg_xlog_location_diff(sender_sent_location, receiver_replay_location)::bigint AS lag_bytes
FROM pg_stat_replication;
', '[]', 'AGENT', 1, '999999999', now(), '999999999', NULL, 0, NULL, 'ALL', uuid());
INSERT INTO grmp.script_config (id, script_type, script_name, database_type, refered_appbusiness, kernel_version, region, deployment_form, execute_node_type, cluster_deployment_mode, script_content, parameter_config, scene, is_valid, create_user, create_time, last_modify_user, last_modify_time, is_asyn, "extend", compliance_mode, uuid) VALUES (grmp.script_config_seq.nextval, 'SQL', 'health.slow_sql', 'postgres', 1, 'ALL', NULL, NULL, NULL, 'centralization', 'SELECT
  unique_sql_id::text,
  LEFT(REGEXP_REPLACE(query, ''\s+'', '' '', ''g''), 180) AS query,
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
', '[{"key":"threshold_ms","value":"","type":"INTEGER","autoAcquire":false},{"key":"limit","value":"","type":"INTEGER","autoAcquire":false}]', 'AGENT', 1, '999999999', now(), '999999999', NULL, 0, NULL, 'ALL', uuid());
INSERT INTO grmp.script_config (id, script_type, script_name, database_type, refered_appbusiness, kernel_version, region, deployment_form, execute_node_type, cluster_deployment_mode, script_content, parameter_config, scene, is_valid, create_user, create_time, last_modify_user, last_modify_time, is_asyn, "extend", compliance_mode, uuid) VALUES (grmp.script_config_seq.nextval, 'SQL', 'health.stale_stats', 'postgres', 1, 'ALL', NULL, NULL, NULL, 'centralization', 'SELECT n.nspname || ''.'' || c.relname AS tbl_name,
       c.relpages AS frozen_pages,
       pg_relation_size(c.oid) / current_setting(''block_size'')::bigint AS cur_pages,
       c.reltuples::bigint AS frozen_tuples,
       COALESCE(t.n_live_tup, 0) AS live_tuples,
       (SELECT count(*) FROM pg_stats s
         WHERE s.schemaname = n.nspname AND s.tablename = c.relname) AS stat_columns,
       COALESCE(to_char(t.last_analyze, ''YYYY-MM-DD HH24:MI:SS''), ''never'') AS last_analyze,
       COALESCE(to_char(t.last_autoanalyze, ''YYYY-MM-DD HH24:MI:SS''), ''never'') AS last_autoanalyze
FROM pg_class c
JOIN pg_namespace n ON n.oid = c.relnamespace
LEFT JOIN pg_stat_user_tables t ON t.relid = c.oid
WHERE c.relkind = ''r'' AND n.nspname NOT IN {{schema_filter}}
  AND c.reltuples > {{min_rows}}
ORDER BY c.relpages DESC LIMIT {{limit}};
', '[{"key":"min_rows","value":"","type":"INTEGER","autoAcquire":false},{"key":"schema_filter","value":"","type":"STRING","autoAcquire":false},{"key":"limit","value":"","type":"INTEGER","autoAcquire":false}]', 'AGENT', 1, '999999999', now(), '999999999', NULL, 0, NULL, 'ALL', uuid());
INSERT INTO grmp.script_config (id, script_type, script_name, database_type, refered_appbusiness, kernel_version, region, deployment_form, execute_node_type, cluster_deployment_mode, script_content, parameter_config, scene, is_valid, create_user, create_time, last_modify_user, last_modify_time, is_asyn, "extend", compliance_mode, uuid) VALUES (grmp.script_config_seq.nextval, 'SQL', 'health.stats_window', 'postgres', 1, 'ALL', NULL, NULL, NULL, 'centralization', 'SELECT COALESCE(to_char(stats_reset, ''YYYY-MM-DD HH24:MI:SS''), ''never'') AS stats_reset,
       COALESCE(EXTRACT(EPOCH FROM (now() - stats_reset)), -1) AS window_seconds
FROM pg_stat_database
WHERE datname = current_database();
', '[]', 'AGENT', 1, '999999999', now(), '999999999', NULL, 0, NULL, 'ALL', uuid());
INSERT INTO grmp.script_config (id, script_type, script_name, database_type, refered_appbusiness, kernel_version, region, deployment_form, execute_node_type, cluster_deployment_mode, script_content, parameter_config, scene, is_valid, create_user, create_time, last_modify_user, last_modify_time, is_asyn, "extend", compliance_mode, uuid) VALUES (grmp.script_config_seq.nextval, 'SQL', 'health.unused_index', 'postgres', 1, 'ALL', NULL, NULL, NULL, 'centralization', 'SELECT s.schemaname||''.''||s.indexrelname AS idx_name,
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
', '[{"key":"min_bytes","value":"","type":"INTEGER","autoAcquire":false},{"key":"schema_filter","value":"","type":"STRING","autoAcquire":false},{"key":"limit","value":"","type":"INTEGER","autoAcquire":false}]', 'AGENT', 1, '999999999', now(), '999999999', NULL, 0, NULL, 'ALL', uuid());
INSERT INTO grmp.script_config (id, script_type, script_name, database_type, refered_appbusiness, kernel_version, region, deployment_form, execute_node_type, cluster_deployment_mode, script_content, parameter_config, scene, is_valid, create_user, create_time, last_modify_user, last_modify_time, is_asyn, "extend", compliance_mode, uuid) VALUES (grmp.script_config_seq.nextval, 'SQL', 'lockwait.chain', 'postgres', 1, 'ALL', NULL, NULL, NULL, 'centralization', 'SELECT w.sessionid       AS sessionid,
       w.block_sessionid AS block_sessionid
  FROM pg_thread_wait_status w
 WHERE w.block_sessionid IS NOT NULL
   AND w.block_sessionid <> 0
   AND w.block_sessionid <> w.sessionid;
', '[]', 'AGENT', 1, '999999999', now(), '999999999', NULL, 0, NULL, 'ALL', uuid());
INSERT INTO grmp.script_config (id, script_type, script_name, database_type, refered_appbusiness, kernel_version, region, deployment_form, execute_node_type, cluster_deployment_mode, script_content, parameter_config, scene, is_valid, create_user, create_time, last_modify_user, last_modify_time, is_asyn, "extend", compliance_mode, uuid) VALUES (grmp.script_config_seq.nextval, 'SQL', 'lockwait.pairs', 'postgres', 1, 'ALL', NULL, NULL, NULL, 'centralization', 'SELECT w.pid                         AS waiter_pid,
       COALESCE(w.sessionid, 0)      AS waiter_sessionid,
       w.mode                        AS waiter_mode,
       h.pid                         AS holder_pid,
       COALESCE(h.sessionid, 0)      AS holder_sessionid,
       h.mode                        AS holder_mode,
       w.locktype                    AS locktype,
       COALESCE(n.nspname || ''.'' || c.relname, '''')  AS lock_object,
       COALESCE(w.locktag, '''')       AS locktag,
       round(EXTRACT(EPOCH FROM (now() - wa.query_start))::numeric, 1) AS waiter_wait_s,
       COALESCE(wa.usename, '''')      AS waiter_user,
       COALESCE(wa.application_name, '''') AS waiter_app,
       COALESCE(substr(wa.query, 1, 300), '''')       AS waiter_query,
       COALESCE(ha.state, '''')        AS holder_state,
       COALESCE(ha.usename, '''')      AS holder_user,
       COALESCE(ha.application_name, '''') AS holder_app,
       round(EXTRACT(EPOCH FROM (now() - ha.xact_start))::numeric, 1) AS holder_xact_age_s,
       COALESCE(substr(ha.query, 1, 300), '''')       AS holder_query
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
', '[{"key":"limit","value":"","type":"INTEGER","autoAcquire":false}]', 'AGENT', 1, '999999999', now(), '999999999', NULL, 0, NULL, 'ALL', uuid());
INSERT INTO grmp.script_config (id, script_type, script_name, database_type, refered_appbusiness, kernel_version, region, deployment_form, execute_node_type, cluster_deployment_mode, script_content, parameter_config, scene, is_valid, create_user, create_time, last_modify_user, last_modify_time, is_asyn, "extend", compliance_mode, uuid) VALUES (grmp.script_config_seq.nextval, 'SQL', 'memanalyze.activity', 'postgres', 1, 'ALL', NULL, NULL, NULL, 'centralization', 'SELECT sessionid, pid, usename, application_name, state, query FROM pg_stat_activity;
', '[]', 'AGENT', 1, '999999999', now(), '999999999', NULL, 0, NULL, 'ALL', uuid());
INSERT INTO grmp.script_config (id, script_type, script_name, database_type, refered_appbusiness, kernel_version, region, deployment_form, execute_node_type, cluster_deployment_mode, script_content, parameter_config, scene, is_valid, create_user, create_time, last_modify_user, last_modify_time, is_asyn, "extend", compliance_mode, uuid) VALUES (grmp.script_config_seq.nextval, 'SQL', 'memanalyze.cols_bare', 'postgres', 1, 'ALL', NULL, NULL, NULL, 'centralization', 'SELECT a.attname::text AS attname
FROM pg_class c
JOIN pg_namespace n ON n.oid = c.relnamespace
JOIN pg_attribute a ON a.attrelid = c.oid
WHERE c.relname = ''{{relname}}'' AND n.nspname IN ({{schemas}})
  AND a.attnum > 0 AND NOT a.attisdropped
ORDER BY a.attnum;
', '[{"key":"relname","value":"","type":"STRING","autoAcquire":false},{"key":"schemas","value":"","type":"STRING","autoAcquire":false}]', 'AGENT', 1, '999999999', now(), '999999999', NULL, 0, NULL, 'ALL', uuid());
INSERT INTO grmp.script_config (id, script_type, script_name, database_type, refered_appbusiness, kernel_version, region, deployment_form, execute_node_type, cluster_deployment_mode, script_content, parameter_config, scene, is_valid, create_user, create_time, last_modify_user, last_modify_time, is_asyn, "extend", compliance_mode, uuid) VALUES (grmp.script_config_seq.nextval, 'SQL', 'memanalyze.cols_qualified', 'postgres', 1, 'ALL', NULL, NULL, NULL, 'centralization', 'SELECT a.attname::text AS attname
FROM pg_class c
JOIN pg_namespace n ON n.oid = c.relnamespace
JOIN pg_attribute a ON a.attrelid = c.oid
WHERE n.nspname = ''{{schema}}'' AND c.relname = ''{{relname}}''
  AND a.attnum > 0 AND NOT a.attisdropped
ORDER BY a.attnum;
', '[{"key":"schema","value":"","type":"STRING","autoAcquire":false},{"key":"relname","value":"","type":"STRING","autoAcquire":false}]', 'AGENT', 1, '999999999', now(), '999999999', NULL, 0, NULL, 'ALL', uuid());
INSERT INTO grmp.script_config (id, script_type, script_name, database_type, refered_appbusiness, kernel_version, region, deployment_form, execute_node_type, cluster_deployment_mode, script_content, parameter_config, scene, is_valid, create_user, create_time, last_modify_user, last_modify_time, is_asyn, "extend", compliance_mode, uuid) VALUES (grmp.script_config_seq.nextval, 'SQL', 'memanalyze.context', 'postgres', 1, 'ALL', NULL, NULL, NULL, 'centralization', 'SELECT contextname, sum(totalsize) AS totalsize,
       sum(freesize) AS freesize, sum(usedsize) AS usedsize
FROM (SELECT contextname, totalsize, freesize, usedsize FROM gs_session_memory_detail) t
GROUP BY contextname ORDER BY 4 DESC NULLS LAST
LIMIT {{limit}};
', '[{"key":"limit","value":"","type":"INTEGER","autoAcquire":false}]', 'AGENT', 1, '999999999', now(), '999999999', NULL, 0, NULL, 'ALL', uuid());
INSERT INTO grmp.script_config (id, script_type, script_name, database_type, refered_appbusiness, kernel_version, region, deployment_form, execute_node_type, cluster_deployment_mode, script_content, parameter_config, scene, is_valid, create_user, create_time, last_modify_user, last_modify_time, is_asyn, "extend", compliance_mode, uuid) VALUES (grmp.script_config_seq.nextval, 'SQL', 'memanalyze.gucs', 'postgres', 1, 'ALL', NULL, NULL, NULL, 'centralization', 'SELECT name, setting
FROM pg_settings
WHERE name IN (
  ''max_process_memory'', ''shared_buffers'', ''work_mem'', ''maintenance_work_mem'',
  ''max_connections'', ''enable_memory_limit'', ''memory_tracking_mode'',
  ''use_workload_manager'', ''enable_resource_track'', ''resource_track_level'',
  ''resource_track_cost'', ''resource_track_duration'',
  ''enable_dynamic_workload'', ''query_max_mem'', ''query_mem'')
ORDER BY name;
', '[]', 'AGENT', 1, '999999999', now(), '999999999', NULL, 0, NULL, 'ALL', uuid());
INSERT INTO grmp.script_config (id, script_type, script_name, database_type, refered_appbusiness, kernel_version, region, deployment_form, execute_node_type, cluster_deployment_mode, script_content, parameter_config, scene, is_valid, create_user, create_time, last_modify_user, last_modify_time, is_asyn, "extend", compliance_mode, uuid) VALUES (grmp.script_config_seq.nextval, 'SQL', 'memanalyze.instance', 'postgres', 1, 'ALL', NULL, NULL, NULL, 'centralization', 'SELECT memorytype, memorymbytes FROM gs_total_memory_detail;
', '[]', 'AGENT', 1, '999999999', now(), '999999999', NULL, 0, NULL, 'ALL', uuid());
INSERT INTO grmp.script_config (id, script_type, script_name, database_type, refered_appbusiness, kernel_version, region, deployment_form, execute_node_type, cluster_deployment_mode, script_content, parameter_config, scene, is_valid, create_user, create_time, last_modify_user, last_modify_time, is_asyn, "extend", compliance_mode, uuid) VALUES (grmp.script_config_seq.nextval, 'SQL', 'memanalyze.session', 'postgres', 1, 'ALL', NULL, NULL, NULL, 'centralization', 'SELECT sessid, init_mem, used_mem, peak_mem FROM dbe_perf.session_memory
ORDER BY peak_mem DESC NULLS LAST LIMIT {{limit}};
', '[{"key":"limit","value":"","type":"INTEGER","autoAcquire":false}]', 'AGENT', 1, '999999999', now(), '999999999', NULL, 0, NULL, 'ALL', uuid());
INSERT INTO grmp.script_config (id, script_type, script_name, database_type, refered_appbusiness, kernel_version, region, deployment_form, execute_node_type, cluster_deployment_mode, script_content, parameter_config, scene, is_valid, create_user, create_time, last_modify_user, last_modify_time, is_asyn, "extend", compliance_mode, uuid) VALUES (grmp.script_config_seq.nextval, 'SQL', 'memanalyze.wlm_operator', 'postgres', 1, 'ALL', NULL, NULL, NULL, 'centralization', 'SELECT queryid, plan_node_id, plan_node_name, duration, NULL AS estimate_memory, NULL AS memory_used, max_peak_memory, average_peak_memory, NULL AS spill_size, warning FROM gs_wlm_operator_statistics
ORDER BY max_peak_memory DESC NULLS LAST LIMIT {{limit}};
', '[{"key":"limit","value":"","type":"INTEGER","autoAcquire":false}]', 'AGENT', 1, '999999999', now(), '999999999', NULL, 0, NULL, 'ALL', uuid());
INSERT INTO grmp.script_config (id, script_type, script_name, database_type, refered_appbusiness, kernel_version, region, deployment_form, execute_node_type, cluster_deployment_mode, script_content, parameter_config, scene, is_valid, create_user, create_time, last_modify_user, last_modify_time, is_asyn, "extend", compliance_mode, uuid) VALUES (grmp.script_config_seq.nextval, 'SQL', 'memanalyze.wlm_operator_hist', 'postgres', 1, 'ALL', NULL, NULL, NULL, 'centralization', 'SELECT queryid, plan_node_id, plan_node_name, duration, NULL AS estimate_memory, NULL AS memory_used, max_peak_memory, average_peak_memory, NULL AS spill_size, warning FROM gs_wlm_operator_history
ORDER BY max_peak_memory DESC NULLS LAST LIMIT {{limit}};
', '[{"key":"limit","value":"","type":"INTEGER","autoAcquire":false}]', 'AGENT', 1, '999999999', now(), '999999999', NULL, 0, NULL, 'ALL', uuid());
INSERT INTO grmp.script_config (id, script_type, script_name, database_type, refered_appbusiness, kernel_version, region, deployment_form, execute_node_type, cluster_deployment_mode, script_content, parameter_config, scene, is_valid, create_user, create_time, last_modify_user, last_modify_time, is_asyn, "extend", compliance_mode, uuid) VALUES (grmp.script_config_seq.nextval, 'SQL', 'memanalyze.wlm_sql', 'postgres', 1, 'ALL', NULL, NULL, NULL, 'centralization', 'SELECT queryid, query, start_time, duration, estimate_memory, NULL AS used_memory, max_peak_memory, average_peak_memory, spill_info FROM gs_wlm_session_statistics
ORDER BY max_peak_memory DESC NULLS LAST LIMIT {{limit}};
', '[{"key":"limit","value":"","type":"INTEGER","autoAcquire":false}]', 'AGENT', 1, '999999999', now(), '999999999', NULL, 0, NULL, 'ALL', uuid());
INSERT INTO grmp.script_config (id, script_type, script_name, database_type, refered_appbusiness, kernel_version, region, deployment_form, execute_node_type, cluster_deployment_mode, script_content, parameter_config, scene, is_valid, create_user, create_time, last_modify_user, last_modify_time, is_asyn, "extend", compliance_mode, uuid) VALUES (grmp.script_config_seq.nextval, 'SQL', 'memanalyze.wlm_sql_hist', 'postgres', 1, 'ALL', NULL, NULL, NULL, 'centralization', 'SELECT queryid, query, start_time, duration, estimate_memory, NULL AS used_memory, max_peak_memory, average_peak_memory, spill_info FROM gs_wlm_session_history
ORDER BY max_peak_memory DESC NULLS LAST LIMIT {{limit}};
', '[{"key":"limit","value":"","type":"INTEGER","autoAcquire":false}]', 'AGENT', 1, '999999999', now(), '999999999', NULL, 0, NULL, 'ALL', uuid());

INSERT INTO grmp.script_config (id, script_type, script_name, database_type, refered_appbusiness, kernel_version, region, deployment_form, execute_node_type, cluster_deployment_mode, script_content, parameter_config, scene, is_valid, create_user, create_time, last_modify_user, last_modify_time, is_asyn, "extend", compliance_mode, uuid) VALUES (grmp.script_config_seq.nextval, 'SQL', 'procinfo.key_gucs', 'postgres', 1, 'ALL', NULL, NULL, NULL, 'centralization', 'SELECT name, setting, COALESCE(unit, '''') AS unit
FROM pg_settings
WHERE name IN (
  ''work_mem'', ''maintenance_work_mem'', ''shared_buffers'',
  ''effective_cache_size'', ''random_page_cost'', ''seq_page_cost'',
  ''cpu_tuple_cost'', ''cpu_index_tuple_cost'', ''cpu_operator_cost'', ''block_size'',
  ''query_dop'',
  ''max_parallel_workers_per_gather'', ''from_collapse_limit'',
  ''join_collapse_limit'', ''geqo_threshold'', ''default_statistics_target'')
ORDER BY name;
', '[]', 'AGENT', 1, '999999999', now(), '999999999', NULL, 0, NULL, 'ALL', uuid());
INSERT INTO grmp.script_config (id, script_type, script_name, database_type, refered_appbusiness, kernel_version, region, deployment_form, execute_node_type, cluster_deployment_mode, script_content, parameter_config, scene, is_valid, create_user, create_time, last_modify_user, last_modify_time, is_asyn, "extend", compliance_mode, uuid) VALUES (grmp.script_config_seq.nextval, 'SQL', 'procinfo.proc_def', 'postgres', 1, 'ALL', NULL, NULL, NULL, 'centralization', 'SELECT n.nspname, p.proname, l.lanname, p.prosrc,
       pg_catalog.pg_get_function_arguments(p.oid) AS args
FROM pg_proc p
JOIN pg_namespace n ON n.oid = p.pronamespace
JOIN pg_language l ON l.oid = p.prolang
WHERE p.proname = ''{{name}}'' AND (''{{schema}}'' = '''' OR n.nspname = ''{{schema}}'')
ORDER BY (n.nspname = ''public'') DESC, n.nspname
LIMIT 1;
', '[{"key":"name","value":"","type":"STRING","autoAcquire":false},{"key":"schema","value":"","type":"STRING","autoAcquire":false}]', 'AGENT', 1, '999999999', now(), '999999999', NULL, 0, NULL, 'ALL', uuid());
INSERT INTO grmp.script_config (id, script_type, script_name, database_type, refered_appbusiness, kernel_version, region, deployment_form, execute_node_type, cluster_deployment_mode, script_content, parameter_config, scene, is_valid, create_user, create_time, last_modify_user, last_modify_time, is_asyn, "extend", compliance_mode, uuid) VALUES (grmp.script_config_seq.nextval, 'SQL', 'proctune.column_stats', 'postgres', 1, 'ALL', NULL, NULL, NULL, 'centralization', 'SELECT tablename, attname, n_distinct, null_frac, avg_width, correlation,
       COALESCE(most_common_vals::text, '''') AS most_common_vals,
       COALESCE(most_common_freqs::text, '''') AS most_common_freqs,
       COALESCE(histogram_bounds::text, '''') AS histogram_bounds
FROM pg_stats
WHERE tablename IN ({{names}});
', '[{"key":"names","value":"","type":"STRING","autoAcquire":false}]', 'AGENT', 1, '999999999', now(), '999999999', NULL, 0, NULL, 'ALL', uuid());
INSERT INTO grmp.script_config (id, script_type, script_name, database_type, refered_appbusiness, kernel_version, region, deployment_form, execute_node_type, cluster_deployment_mode, script_content, parameter_config, scene, is_valid, create_user, create_time, last_modify_user, last_modify_time, is_asyn, "extend", compliance_mode, uuid) VALUES (grmp.script_config_seq.nextval, 'SQL', 'proctune.db_version', 'postgres', 1, 'ALL', NULL, NULL, NULL, 'centralization', 'SELECT version() AS version;
', '[]', 'AGENT', 1, '999999999', now(), '999999999', NULL, 0, NULL, 'ALL', uuid());
INSERT INTO grmp.script_config (id, script_type, script_name, database_type, refered_appbusiness, kernel_version, region, deployment_form, execute_node_type, cluster_deployment_mode, script_content, parameter_config, scene, is_valid, create_user, create_time, last_modify_user, last_modify_time, is_asyn, "extend", compliance_mode, uuid) VALUES (grmp.script_config_seq.nextval, 'SQL', 'proctune.indexes', 'postgres', 1, 'ALL', NULL, NULL, NULL, 'centralization', 'SELECT t.relname AS table_name, i.relname AS index_name,
       ix.indisunique, ix.indisprimary,
       pg_get_indexdef(ix.indexrelid) AS index_def
FROM pg_class tt
JOIN pg_index ix ON t.oid = ix.indrelid
JOIN pg_class i ON i.oid = ix.indexrelid
WHERE t.relname IN ({{names}});
', '[{"key":"names","value":"","type":"STRING","autoAcquire":false}]', 'AGENT', 1, '999999999', now(), '999999999', NULL, 0, NULL, 'ALL', uuid());
INSERT INTO grmp.script_config (id, script_type, script_name, database_type, refered_appbusiness, kernel_version, region, deployment_form, execute_node_type, cluster_deployment_mode, script_content, parameter_config, scene, is_valid, create_user, create_time, last_modify_user, last_modify_time, is_asyn, "extend", compliance_mode, uuid) VALUES (grmp.script_config_seq.nextval, 'SQL', 'proctune.key_gucs', 'postgres', 1, 'ALL', NULL, NULL, NULL, 'centralization', 'SELECT name, setting, COALESCE(unit, '''') AS unit
FROM pg_settings
WHERE name IN (
  ''work_mem'', ''maintenance_work_mem'', ''shared_buffers'',
  ''effective_cache_size'', ''random_page_cost'', ''seq_page_cost'',
  ''cpu_tuple_cost'', ''cpu_index_tuple_cost'', ''cpu_operator_cost'', ''block_size'',
  ''query_dop'',
  ''max_parallel_workers_per_gather'', ''from_collapse_limit'',
  ''join_collapse_limit'', ''geqo_threshold'', ''default_statistics_target'')
ORDER BY name;
', '[]', 'AGENT', 1, '999999999', now(), '999999999', NULL, 0, NULL, 'ALL', uuid());
INSERT INTO grmp.script_config (id, script_type, script_name, database_type, refered_appbusiness, kernel_version, region, deployment_form, execute_node_type, cluster_deployment_mode, script_content, parameter_config, scene, is_valid, create_user, create_time, last_modify_user, last_modify_time, is_asyn, "extend", compliance_mode, uuid) VALUES (grmp.script_config_seq.nextval, 'SQL', 'proctune.plan_text', 'appbusiness', 1, 'ALL', NULL, NULL, NULL, 'centralization', 'EXPLAIN (ANALYZE false, BUFFERS false, FORMAT TEXT) {{sql}}
', '[{"key":"sql","value":"","type":"STRING","autoAcquire":false}]', 'AGENT', 1, '999999999', now(), '999999999', NULL, 0, NULL, 'ALL', uuid());
INSERT INTO grmp.script_config (id, script_type, script_name, database_type, refered_appbusiness, kernel_version, region, deployment_form, execute_node_type, cluster_deployment_mode, script_content, parameter_config, scene, is_valid, create_user, create_time, last_modify_user, last_modify_time, is_asyn, "extend", compliance_mode, uuid) VALUES (grmp.script_config_seq.nextval, 'SQL', 'proctune.plan_text_analyze', 'appbusiness', 1, 'ALL', NULL, NULL, NULL, 'centralization', 'EXPLAIN (ANALYZE false, BUFFERS fasle, FORMAT TEXT) {{sql}}
', '[{"key":"sql","value":"","type":"STRING","autoAcquire":false}]', 'AGENT', 1, '999999999', now(), '999999999', NULL, 0, NULL, 'ALL', uuid());
INSERT INTO grmp.script_config (id, script_type, script_name, database_type, refered_appbusiness, kernel_version, region, deployment_form, execute_node_type, cluster_deployment_mode, script_content, parameter_config, scene, is_valid, create_user, create_time, last_modify_user, last_modify_time, is_asyn, "extend", compliance_mode, uuid) VALUES (grmp.script_config_seq.nextval, 'SQL', 'proctune.proc_def', 'postgres', 1, 'ALL', NULL, NULL, NULL, 'centralization', 'SELECT n.nspname, p.proname, l.lanname, p.prosrc,
       pg_catalog.pg_get_function_arguments(p.oid) AS args
FROM pg_proc p
JOIN pg_namespace n ON n.oid = p.pronamespace
JOIN pg_language l ON l.oid = p.prolang
WHERE p.proname = ''{{name}}'' AND (''{{schema}}'' = '''' OR n.nspname = ''{{schema}}'')
ORDER BY (n.nspname = ''public'') DESC, n.nspname
LIMIT 1;
', '[{"key":"name","value":"","type":"STRING","autoAcquire":false},{"key":"schema","value":"","type":"STRING","autoAcquire":false}]', 'AGENT', 1, '999999999', now(), '999999999', NULL, 0, NULL, 'ALL', uuid());
INSERT INTO grmp.script_config (id, script_type, script_name, database_type, refered_appbusiness, kernel_version, region, deployment_form, execute_node_type, cluster_deployment_mode, script_content, parameter_config, scene, is_valid, create_user, create_time, last_modify_user, last_modify_time, is_asyn, "extend", compliance_mode, uuid) VALUES (grmp.script_config_seq.nextval, 'SQL', 'proctune.sql_from_history', 'postgres', 1, 'ALL', NULL, NULL, NULL, 'centralization', 'SELECT schema_name, query
FROM dbe_perf.statement_history
WHERE unique_query_id = {{sid}}
  AND query NOT LIKE ''/* missing SQL statement%''
ORDER BY start_time DESC
LIMIT 1;
', '[{"key":"sid","value":"","type":"INTEGER","autoAcquire":false}]', 'AGENT', 1, '999999999', now(), '999999999', NULL, 0, NULL, 'ALL', uuid());
INSERT INTO grmp.script_config (id, script_type, script_name, database_type, refered_appbusiness, kernel_version, region, deployment_form, execute_node_type, cluster_deployment_mode, script_content, parameter_config, scene, is_valid, create_user, create_time, last_modify_user, last_modify_time, is_asyn, "extend", compliance_mode, uuid) VALUES (grmp.script_config_seq.nextval, 'SQL', 'proctune.sql_from_statement', 'postgres', 1, 'ALL', NULL, NULL, NULL, 'centralization', 'SELECT query FROM dbe_perf.statement
WHERE unique_sql_id = {{sid}}
  AND query IS NOT NULL
  AND LENGTH(TRIM(query)) > 0
LIMIT 1;
', '[{"key":"sid","value":"","type":"INTEGER","autoAcquire":false}]', 'AGENT', 1, '999999999', now(), '999999999', NULL, 0, NULL, 'ALL', uuid());
INSERT INTO grmp.script_config (id, script_type, script_name, database_type, refered_appbusiness, kernel_version, region, deployment_form, execute_node_type, cluster_deployment_mode, script_content, parameter_config, scene, is_valid, create_user, create_time, last_modify_user, last_modify_time, is_asyn, "extend", compliance_mode, uuid) VALUES (grmp.script_config_seq.nextval, 'SQL', 'proctune.tables', 'postgres', 1, 'ALL', NULL, NULL, NULL, 'centralization', 'SELECT n.nspname, c.relname, c.relpages,
       c.reltuples::bigint AS reltuples,
       pg_relation_size(c.oid) / current_setting(''block_size'')::bigint AS curpages,
       c.relkind,
       pg_total_relation_size(c.oid) / 1024.0 / 1024.0 AS size_mb
FROM pg_class c
LEFT JOIN pg_namespace n ON c.relnamespace = n.oid
WHERE c.relname IN ({{names}}) AND c.relkind IN (''r'',''v'',''p'',''m'');
', '[{"key":"names","value":"","type":"STRING","autoAcquire":false}]', 'AGENT', 1, '999999999', now(), '999999999', NULL, 0, NULL, 'ALL', uuid());

INSERT INTO grmp.script_config (id, script_type, script_name, database_type, refered_appbusiness, kernel_version, region, deployment_form, execute_node_type, cluster_deployment_mode, script_content, parameter_config, scene, is_valid, create_user, create_time, last_modify_user, last_modify_time, is_asyn, "extend", compliance_mode, uuid) VALUES (grmp.script_config_seq.nextval, 'SQL', 'slowsql.slow_sql', 'postgres', 1, 'ALL', NULL, NULL, NULL, 'centralization', 'SELECT
  unique_sql_id::text,
  LEFT(REGEXP_REPLACE(query, ''\s+'', '' '', ''g''), 180) AS query,
  n_calls AS calls,
  ROUND((total_elapse_time/NULLIF(n_calls,0))/1000::numeric, 2) AS avg_ms,
  ROUND(total_elapse_time/1000000::numeric, 2) AS total_sec,
  ROUND(cpu_time/1000000::numeric, 2) AS cpu_sec,
  n_returned_rows AS rows
FROM dbe_perf.statement
WHERE (total_elapse_time/NULLIF(n_calls,0))/1000 > {{threshold_ms}}
  AND n_calls > 0 AND last_updated >= CAST(''{{begin_time}}'' AS TIMESTAMP)
ORDER BY total_elapse_time/NULLIF(n_calls,0) DESC
LIMIT {{limit}};
', '[{"key":"threshold_ms","value":"","type":"INTEGER","autoAcquire":false},{"key":"begin_time","value":"","type":"DATETIME","autoAcquire":false},{"key":"limit","value":"","type":"INTEGER","autoAcquire":false}]', 'AGENT', 1, '999999999', now(), '999999999', NULL, 0, NULL, 'ALL', uuid());
INSERT INTO grmp.script_config (id, script_type, script_name, database_type, refered_appbusiness, kernel_version, region, deployment_form, execute_node_type, cluster_deployment_mode, script_content, parameter_config, scene, is_valid, create_user, create_time, last_modify_user, last_modify_time, is_asyn, "extend", compliance_mode, uuid) VALUES (grmp.script_config_seq.nextval, 'SQL', 'sqlfetch.from_history', 'postgres', 1, 'ALL', NULL, NULL, NULL, 'centralization', 'SELECT schema_name, query
FROM dbe_perf.statement_history
WHERE unique_query_id = {{sid}}
  AND query NOT LIKE ''/* missing SQL statement%''
ORDER BY start_time DESC
LIMIT 1;
', '[{"key":"sid","value":"","type":"INTEGER","autoAcquire":false}]', 'AGENT', 1, '999999999', now(), '999999999', NULL, 0, NULL, 'ALL', uuid());
INSERT INTO grmp.script_config (id, script_type, script_name, database_type, refered_appbusiness, kernel_version, region, deployment_form, execute_node_type, cluster_deployment_mode, script_content, parameter_config, scene, is_valid, create_user, create_time, last_modify_user, last_modify_time, is_asyn, "extend", compliance_mode, uuid) VALUES (grmp.script_config_seq.nextval, 'SQL', 'sqlfetch.from_statement', 'postgres', 1, 'ALL', NULL, NULL, NULL, 'centralization', 'SELECT query FROM dbe_perf.statement
WHERE unique_sql_id = {{sid}}
  AND query IS NOT NULL
  AND LENGTH(TRIM(query)) > 0
LIMIT 1;
', '[{"key":"sid","value":"","type":"INTEGER","autoAcquire":false}]', 'AGENT', 1, '999999999', now(), '999999999', NULL, 0, NULL, 'ALL', uuid());
INSERT INTO grmp.script_config (id, script_type, script_name, database_type, refered_appbusiness, kernel_version, region, deployment_form, execute_node_type, cluster_deployment_mode, script_content, parameter_config, scene, is_valid, create_user, create_time, last_modify_user, last_modify_time, is_asyn, "extend", compliance_mode, uuid) VALUES (grmp.script_config_seq.nextval, 'SQL', 'sqlreview.from_history', 'postgres', 1, 'ALL', NULL, NULL, NULL, 'centralization', 'SELECT schema_name, query
FROM dbe_perf.statement_history
WHERE unique_query_id = {{sid}}
  AND query NOT LIKE ''/* missing SQL statement%''
ORDER BY start_time DESC
LIMIT 1;
', '[{"key":"sid","value":"","type":"INTEGER","autoAcquire":false}]', 'AGENT', 1, '999999999', now(), '999999999', NULL, 0, NULL, 'ALL', uuid());
INSERT INTO grmp.script_config (id, script_type, script_name, database_type, refered_appbusiness, kernel_version, region, deployment_form, execute_node_type, cluster_deployment_mode, script_content, parameter_config, scene, is_valid, create_user, create_time, last_modify_user, last_modify_time, is_asyn, "extend", compliance_mode, uuid) VALUES (grmp.script_config_seq.nextval, 'SQL', 'sqlreview.from_statement', 'postgres', 1, 'ALL', NULL, NULL, NULL, 'centralization', 'SELECT query FROM dbe_perf.statement
WHERE unique_sql_id = {{sid}}
  AND query IS NOT NULL
  AND LENGTH(TRIM(query)) > 0
LIMIT 1;
', '[{"key":"sid","value":"","type":"INTEGER","autoAcquire":false}]', 'AGENT', 1, '999999999', now(), '999999999', NULL, 0, NULL, 'ALL', uuid());
INSERT INTO grmp.script_config (id, script_type, script_name, database_type, refered_appbusiness, kernel_version, region, deployment_form, execute_node_type, cluster_deployment_mode, script_content, parameter_config, scene, is_valid, create_user, create_time, last_modify_user, last_modify_time, is_asyn, "extend", compliance_mode, uuid) VALUES (grmp.script_config_seq.nextval, 'SQL', 'sqlreview.indexes', 'postgres', 1, 'ALL', NULL, NULL, NULL, 'centralization', 'SELECT
  n.nspname::text                                              AS schema,
  t.relname::text                                              AS table,
  i.relname::text                                              AS name,
  array_to_string(
    COALESCE((SELECT array_agg(a.attname::text ORDER BY k.ord)
              FROM (SELECT s AS ord,
                           (string_to_array(ix.indkey::text, '' ''))[s]::smallint AS attnum
                    FROM generate_series(
                           1,
                           array_length(string_to_array(ix.indkey::text, '' ''), 1)) s) k
              JOIN pg_attribute a
                ON a.attrelid = t.oid AND a.attnum = k.attnum),
             ARRAY[]::text[]), '','')                            AS columns,
  ix.indisunique                                               AS is_unique,
  ix.indisprimary                                              AS is_primary,
  COALESCE(s.idx_scan, 0)                                      AS scans
FROM pg_index ix
JOIN pg_class i     ON i.oid = ix.indexrelid
JOIN pg_class t     ON t.oid = ix.indrelid
JOIN pg_namespace n ON n.oid = t.relnamespace
LEFT JOIN pg_stat_user_indexes s ON s.indexrelid = ix.indexrelid
WHERE n.nspname = ''{{schema}}''
ORDER BY t.relname, i.relname;
', '[{"key":"schema","value":"","type":"STRING","autoAcquire":false}]', 'AGENT', 1, '999999999', now(), '999999999', NULL, 0, NULL, 'ALL', uuid());
INSERT INTO grmp.script_config (id, script_type, script_name, database_type, refered_appbusiness, kernel_version, region, deployment_form, execute_node_type, cluster_deployment_mode, script_content, parameter_config, scene, is_valid, create_user, create_time, last_modify_user, last_modify_time, is_asyn, "extend", compliance_mode, uuid) VALUES (grmp.script_config_seq.nextval, 'SQL', 'sqlreview.tables', 'postgres', 1, 'ALL', NULL, NULL, NULL, 'centralization', 'SELECT
  n.nspname::text                                              AS schema,
  c.relname::text                                              AS table,
  EXISTS (SELECT 1 FROM pg_constraint pk
          WHERE pk.conrelid = c.oid AND pk.contype = ''p'')      AS has_pk,
  array_to_string(
    COALESCE((SELECT array_agg(fk.conname::text)
              FROM pg_constraint fk
              WHERE fk.conrelid = c.oid AND fk.contype = ''f''),
             ARRAY[]::text[]), '','')                            AS fks,
  array_to_string(
    COALESCE((SELECT array_agg(a.attname::text ORDER BY a.attnum)
              FROM pg_attribute a
              WHERE a.attrelid = c.oid AND a.attnum > 0 AND NOT a.attisdropped),
             ARRAY[]::text[]), '','')                            AS columns
FROM pg_class c
JOIN pg_namespace n ON n.oid = c.relnamespace
WHERE c.relkind = ''r'' AND n.nspname = ''{{schema}}''
ORDER BY c.relname;
', '[{"key":"schema","value":"","type":"STRING","autoAcquire":false}]', 'AGENT', 1, '999999999', now(), '999999999', NULL, 0, NULL, 'ALL', uuid());
INSERT INTO grmp.script_config (id, script_type, script_name, database_type, refered_appbusiness, kernel_version, region, deployment_form, execute_node_type, cluster_deployment_mode, script_content, parameter_config, scene, is_valid, create_user, create_time, last_modify_user, last_modify_time, is_asyn, "extend", compliance_mode, uuid) VALUES (grmp.script_config_seq.nextval, 'SQL', 'sqlreview.top_sql', 'postgres', 1, 'ALL', NULL, NULL, NULL, 'centralization', 'SELECT unique_sql_id::text, query
FROM dbe_perf.statement
WHERE n_calls > 0 AND query IS NOT NULL AND LENGTH(TRIM(query)) > 0
ORDER BY total_elapse_time DESC
LIMIT {{limit}};
', '[{"key":"limit","value":"","type":"INTEGER","autoAcquire":false}]', 'AGENT', 1, '999999999', now(), '999999999', NULL, 0, NULL, 'ALL', uuid());
INSERT INTO grmp.script_config (id, script_type, script_name, database_type, refered_appbusiness, kernel_version, region, deployment_form, execute_node_type, cluster_deployment_mode, script_content, parameter_config, scene, is_valid, create_user, create_time, last_modify_user, last_modify_time, is_asyn, "extend", compliance_mode, uuid) VALUES (grmp.script_config_seq.nextval, 'SQL', 'sqltune.column_stats', 'postgres', 1, 'ALL', NULL, NULL, NULL, 'centralization', 'SELECT tablename, attname, n_distinct, null_frac, avg_width, correlation,
       COALESCE(most_common_vals::text, '''') AS most_common_vals,
       COALESCE(most_common_freqs::text, '''') AS most_common_freqs,
       COALESCE(histogram_bounds::text, '''') AS histogram_bounds
FROM pg_stats
WHERE tablename IN ({{names}});
', '[{"key":"names","value":"","type":"STRING","autoAcquire":false}]', 'AGENT', 1, '999999999', now(), '999999999', NULL, 0, NULL, 'ALL', uuid());
INSERT INTO grmp.script_config (id, script_type, script_name, database_type, refered_appbusiness, kernel_version, region, deployment_form, execute_node_type, cluster_deployment_mode, script_content, parameter_config, scene, is_valid, create_user, create_time, last_modify_user, last_modify_time, is_asyn, "extend", compliance_mode, uuid) VALUES (grmp.script_config_seq.nextval, 'SQL', 'sqltune.column_types', 'postgres', 1, 'ALL', NULL, NULL, NULL, 'centralization', 'SELECT a.attname, format_type(a.atttypid, NULL) AS type_name
FROM pg_attribute a
JOIN pg_class c ON c.oid = a.attrelid
JOIN pg_namespace n ON n.oid = c.relnamespace
WHERE c.relname IN ({{tables}})
  AND a.attname IN ({{columns}})
  AND a.attnum > 0 AND NOT a.attisdropped
  AND c.relkind IN (''r'',''v'',''p'',''m'')
  AND n.nspname NOT IN (''pg_catalog'',''information_schema'');
', '[{"key":"tables","value":"","type":"STRING","autoAcquire":false},{"key":"columns","value":"","type":"STRING","autoAcquire":false}]', 'AGENT', 1, '999999999', now(), '999999999', NULL, 0, NULL, 'ALL', uuid());
INSERT INTO grmp.script_config (id, script_type, script_name, database_type, refered_appbusiness, kernel_version, region, deployment_form, execute_node_type, cluster_deployment_mode, script_content, parameter_config, scene, is_valid, create_user, create_time, last_modify_user, last_modify_time, is_asyn, "extend", compliance_mode, uuid) VALUES (grmp.script_config_seq.nextval, 'SQL', 'sqltune.from_history', 'postgres', 1, 'ALL', NULL, NULL, NULL, 'centralization', 'SELECT schema_name, query
FROM dbe_perf.statement_history
WHERE unique_query_id = {{sid}}
  AND query NOT LIKE ''/* missing SQL statement%''
ORDER BY start_time DESC
LIMIT 1;
', '[{"key":"sid","value":"","type":"INTEGER","autoAcquire":false}]', 'AGENT', 1, '999999999', now(), '999999999', NULL, 0, NULL, 'ALL', uuid());
INSERT INTO grmp.script_config (id, script_type, script_name, database_type, refered_appbusiness, kernel_version, region, deployment_form, execute_node_type, cluster_deployment_mode, script_content, parameter_config, scene, is_valid, create_user, create_time, last_modify_user, last_modify_time, is_asyn, "extend", compliance_mode, uuid) VALUES (grmp.script_config_seq.nextval, 'SQL', 'sqltune.from_statement', 'postgres', 1, 'ALL', NULL, NULL, NULL, 'centralization', 'SELECT query FROM dbe_perf.statement
WHERE unique_sql_id = {{sid}}
  AND query IS NOT NULL
  AND LENGTH(TRIM(query)) > 0
LIMIT 1;
', '[{"key":"sid","value":"","type":"INTEGER","autoAcquire":false}]', 'AGENT', 1, '999999999', now(), '999999999', NULL, 0, NULL, 'ALL', uuid());
INSERT INTO grmp.script_config (id, script_type, script_name, database_type, refered_appbusiness, kernel_version, region, deployment_form, execute_node_type, cluster_deployment_mode, script_content, parameter_config, scene, is_valid, create_user, create_time, last_modify_user, last_modify_time, is_asyn, "extend", compliance_mode, uuid) VALUES (grmp.script_config_seq.nextval, 'SQL', 'sqltune.indexes', 'postgres', 1, 'ALL', NULL, NULL, NULL, 'centralization', 'SELECT t.relname AS table_name,
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
', '[{"key":"names","value":"","type":"STRING","autoAcquire":false}]', 'AGENT', 1, '999999999', now(), '999999999', NULL, 0, NULL, 'ALL', uuid());
INSERT INTO grmp.script_config (id, script_type, script_name, database_type, refered_appbusiness, kernel_version, region, deployment_form, execute_node_type, cluster_deployment_mode, script_content, parameter_config, scene, is_valid, create_user, create_time, last_modify_user, last_modify_time, is_asyn, "extend", compliance_mode, uuid) VALUES (grmp.script_config_seq.nextval, 'SQL', 'sqltune.key_gucs', 'postgres', 1, 'ALL', NULL, NULL, NULL, 'centralization', 'SELECT name, setting, COALESCE(unit, '''') AS unit
FROM pg_settings
WHERE name IN (
  ''work_mem'', ''maintenance_work_mem'', ''shared_buffers'',
  ''effective_cache_size'', ''random_page_cost'', ''seq_page_cost'',
  ''cpu_tuple_cost'', ''cpu_index_tuple_cost'', ''cpu_operator_cost'', ''block_size'',
  ''query_dop'',
  ''max_parallel_workers_per_gather'', ''from_collapse_limit'',
  ''join_collapse_limit'', ''geqo_threshold'', ''default_statistics_target'')
ORDER BY name;
', '[]', 'AGENT', 1, '999999999', now(), '999999999', NULL, 0, NULL, 'ALL', uuid());
INSERT INTO grmp.script_config (id, script_type, script_name, database_type, refered_appbusiness, kernel_version, region, deployment_form, execute_node_type, cluster_deployment_mode, script_content, parameter_config, scene, is_valid, create_user, create_time, last_modify_user, last_modify_time, is_asyn, "extend", compliance_mode, uuid) VALUES (grmp.script_config_seq.nextval, 'SQL', 'sqltune.plan_json', 'appbusiness', 1, 'ALL', NULL, NULL, NULL, 'centralization', 'EXPLAIN (ANALYZE false, BUFFERS false, FORMAT JSON) {{sql}}
', '[{"key":"sql","value":"","type":"STRING","autoAcquire":false}]', 'AGENT', 1, '999999999', now(), '999999999', NULL, 0, NULL, 'ALL', uuid());
INSERT INTO grmp.script_config (id, script_type, script_name, database_type, refered_appbusiness, kernel_version, region, deployment_form, execute_node_type, cluster_deployment_mode, script_content, parameter_config, scene, is_valid, create_user, create_time, last_modify_user, last_modify_time, is_asyn, "extend", compliance_mode, uuid) VALUES (grmp.script_config_seq.nextval, 'SQL', 'sqltune.plan_text', 'appbusiness', 1, 'ALL', NULL, NULL, NULL, 'centralization', 'EXPLAIN (ANALYZE false, BUFFERS false, FORMAT TEXT) {{sql}}
', '[{"key":"sql","value":"","type":"STRING","autoAcquire":false}]', 'AGENT', 1, '999999999', now(), '999999999', NULL, 0, NULL, 'ALL', uuid());
INSERT INTO grmp.script_config (id, script_type, script_name, database_type, refered_appbusiness, kernel_version, region, deployment_form, execute_node_type, cluster_deployment_mode, script_content, parameter_config, scene, is_valid, create_user, create_time, last_modify_user, last_modify_time, is_asyn, "extend", compliance_mode, uuid) VALUES (grmp.script_config_seq.nextval, 'SQL', 'sqltune.plan_text_analyze', 'appbusiness', 1, 'ALL', NULL, NULL, NULL, 'centralization', 'EXPLAIN (ANALYZE false, BUFFERS fasle, FORMAT TEXT) {{sql}}
', '[{"key":"sql","value":"","type":"STRING","autoAcquire":false}]', 'AGENT', 1, '999999999', now(), '999999999', NULL, 0, NULL, 'ALL', uuid());
INSERT INTO grmp.script_config (id, script_type, script_name, database_type, refered_appbusiness, kernel_version, region, deployment_form, execute_node_type, cluster_deployment_mode, script_content, parameter_config, scene, is_valid, create_user, create_time, last_modify_user, last_modify_time, is_asyn, "extend", compliance_mode, uuid) VALUES (grmp.script_config_seq.nextval, 'SQL', 'sqltune.stats_freshness', 'postgres', 1, 'ALL', NULL, NULL, NULL, 'centralization', 'SELECT schemaname, relname,
       n_live_tup, n_dead_tup,
       COALESCE(to_char(last_analyze, ''YYYY-MM-DD HH24:MI:SS''), ''never'') AS last_analyze,
       COALESCE(to_char(last_autoanalyze, ''YYYY-MM-DD HH24:MI:SS''), ''never'') AS last_autoanalyze,
       analyze_count, autoanalyze_count
FROM pg_stat_user_tables
WHERE relname IN ({{names}});
', '[{"key":"names","value":"","type":"STRING","autoAcquire":false}]', 'AGENT', 1, '999999999', now(), '999999999', NULL, 0, NULL, 'ALL', uuid());
INSERT INTO grmp.script_config (id, script_type, script_name, database_type, refered_appbusiness, kernel_version, region, deployment_form, execute_node_type, cluster_deployment_mode, script_content, parameter_config, scene, is_valid, create_user, create_time, last_modify_user, last_modify_time, is_asyn, "extend", compliance_mode, uuid) VALUES (grmp.script_config_seq.nextval, 'SQL', 'sqltune.tables', 'postgres', 1, 'ALL', NULL, NULL, NULL, 'centralization', 'SELECT n.nspname, c.relname, c.relpages,
       c.reltuples::bigint AS reltuples,
       pg_relation_size(c.oid) / current_setting(''block_size'')::bigint AS curpages,
       c.relkind,
       pg_total_relation_size(c.oid) / 1024.0 / 1024.0 AS size_mb
FROM pg_class c
LEFT JOIN pg_namespace n ON c.relnamespace = n.oid
WHERE c.relname IN ({{names}}) AND c.relkind IN (''r'',''v'',''p'',''m'');
', '[{"key":"names","value":"","type":"STRING","autoAcquire":false}]', 'AGENT', 1, '999999999', now(), '999999999', NULL, 0, NULL, 'ALL', uuid());
INSERT INTO grmp.script_config (id, script_type, script_name, database_type, refered_appbusiness, kernel_version, region, deployment_form, execute_node_type, cluster_deployment_mode, script_content, parameter_config, scene, is_valid, create_user, create_time, last_modify_user, last_modify_time, is_asyn, "extend", compliance_mode, uuid) VALUES (grmp.script_config_seq.nextval, 'SQL', 'sqltune.version', 'postgres', 1, 'ALL', NULL, NULL, NULL, 'centralization', 'SELECT version() AS version;
', '[]', 'AGENT', 1, '999999999', now(), '999999999', NULL, 0, NULL, 'ALL', uuid());
INSERT INTO grmp.script_config (id, script_type, script_name, database_type, refered_appbusiness, kernel_version, region, deployment_form, execute_node_type, cluster_deployment_mode, script_content, parameter_config, scene, is_valid, create_user, create_time, last_modify_user, last_modify_time, is_asyn, "extend", compliance_mode, uuid) VALUES (grmp.script_config_seq.nextval, 'SQL', 'topproc.top_procs', 'postgres', 1, 'ALL', NULL, NULL, NULL, 'centralization', 'SELECT n.nspname, p.proname, s.calls,
       ROUND(s.total_time::numeric, 2) AS total_ms,
       ROUND(s.self_time::numeric, 2) AS self_ms
FROM pg_stat_user_functions s
JOIN pg_proc p ON p.oid = s.funcid
JOIN pg_namespace n ON n.oid = p.pronamespace
ORDER BY {{order}} NULLS LAST
LIMIT {{limit}};
', '[{"key":"order","value":"","type":"STRING","autoAcquire":false},{"key":"limit","value":"","type":"INTEGER","autoAcquire":false}]', 'AGENT', 1, '999999999', now(), '999999999', NULL, 0, NULL, 'ALL', uuid());
INSERT INTO grmp.script_config (id, script_type, script_name, database_type, refered_appbusiness, kernel_version, region, deployment_form, execute_node_type, cluster_deployment_mode, script_content, parameter_config, scene, is_valid, create_user, create_time, last_modify_user, last_modify_time, is_asyn, "extend", compliance_mode, uuid) VALUES (grmp.script_config_seq.nextval, 'SQL', 'topsql.top_sql', 'postgres', 1, 'ALL', NULL, NULL, NULL, 'centralization', 'SELECT
  unique_sql_id::text,
  LEFT(REGEXP_REPLACE(query, ''\s+'', '' '', ''g''), 80) AS query,
  n_calls AS calls,
  ROUND(total_elapse_time/1000000::numeric, 2) AS total_sec,
  ROUND((total_elapse_time/NULLIF(n_calls,0))/1000::numeric, 2) AS avg_ms,
  n_returned_rows AS rows
FROM dbe_perf.statement
WHERE n_calls > 0
ORDER BY {{order}}
LIMIT {{limit}};
', '[{"key":"order","value":"","type":"STRING","autoAcquire":false},{"key":"limit","value":"","type":"INTEGER","autoAcquire":false}]', 'AGENT', 1, '999999999', now(), '999999999', NULL, 0, NULL, 'ALL', uuid());
INSERT INTO grmp.script_config (id, script_type, script_name, database_type, refered_appbusiness, kernel_version, region, deployment_form, execute_node_type, cluster_deployment_mode, script_content, parameter_config, scene, is_valid, create_user, create_time, last_modify_user, last_modify_time, is_asyn, "extend", compliance_mode, uuid) VALUES (grmp.script_config_seq.nextval, 'SQL', 'vacuum.autovac_settings', 'postgres', 1, 'ALL', NULL, NULL, NULL, 'centralization', 'SELECT name, setting
  FROM pg_settings
 WHERE name LIKE ''autovacuum%'' OR name LIKE ''vacuum_cost%''
 ORDER BY name;
', '[]', 'AGENT', 1, '999999999', now(), '999999999', NULL, 0, NULL, 'ALL', uuid());
INSERT INTO grmp.script_config (id, script_type, script_name, database_type, refered_appbusiness, kernel_version, region, deployment_form, execute_node_type, cluster_deployment_mode, script_content, parameter_config, scene, is_valid, create_user, create_time, last_modify_user, last_modify_time, is_asyn, "extend", compliance_mode, uuid) VALUES (grmp.script_config_seq.nextval, 'SQL', 'vacuum.autovac_workers', 'postgres', 1, 'ALL', NULL, NULL, NULL, 'centralization', 'SELECT pid,
       COALESCE(sessionid,0) AS sessionid,
       EXTRACT(EPOCH FROM (now()-xact_start)) AS xact_age_s,
       COALESCE(query,'''') AS query
  FROM pg_stat_activity
 WHERE query LIKE ''autovacuum:%'';
', '[]', 'AGENT', 1, '999999999', now(), '999999999', NULL, 0, NULL, 'ALL', uuid());
INSERT INTO grmp.script_config (id, script_type, script_name, database_type, refered_appbusiness, kernel_version, region, deployment_form, execute_node_type, cluster_deployment_mode, script_content, parameter_config, scene, is_valid, create_user, create_time, last_modify_user, last_modify_time, is_asyn, "extend", compliance_mode, uuid) VALUES (grmp.script_config_seq.nextval, 'SQL', 'vacuum.dead_tuples', 'postgres', 1, 'ALL', NULL, NULL, NULL, 'centralization', 'SELECT n.nspname AS schema,
       t.relname AS "table",
       t.n_live_tup AS n_live_tup,
       t.n_dead_tup AS n_dead_tup,
       c.reltuples AS reltuples,
       pg_total_relation_size(c.oid) AS table_bytes,
       EXTRACT(EPOCH FROM (now()-t.last_autovacuum)) AS last_autovacuum_age_s,
       EXTRACT(EPOCH FROM (now()-t.last_vacuum)) AS last_vacuum_age_s,
       t.vacuum_count AS vacuum_count,
       t.autovacuum_count AS autovacuum_count,
       CASE WHEN ''autovacuum_enabled=false'' = ANY(c.reloptions) THEN false ELSE true END AS autovac_enabled,
       COALESCE(array_to_string(c.reloptions, '',''), '''') AS reloptions
  FROM pg_stat_user_tables t
  JOIN pg_class c ON c.oid = t.relid
  JOIN pg_namespace n ON n.oid = c.relnamespace
 WHERE n.nspname NOT IN (''pg_catalog'',''information_schema'',''snapshot'',''dbe_perf'',''dbe_pldeveloper'',''cstore'')
 ORDER BY t.n_dead_tup DESC
 LIMIT {{limit}};
', '[{"key":"limit","value":"","type":"INTEGER","autoAcquire":false}]', 'AGENT', 1, '999999999', now(), '999999999', NULL, 0, NULL, 'ALL', uuid());
INSERT INTO grmp.script_config (id, script_type, script_name, database_type, refered_appbusiness, kernel_version, region, deployment_form, execute_node_type, cluster_deployment_mode, script_content, parameter_config, scene, is_valid, create_user, create_time, last_modify_user, last_modify_time, is_asyn, "extend", compliance_mode, uuid) VALUES (grmp.script_config_seq.nextval, 'SQL', 'vacuum.oldest_xmin', 'postgres', 1, 'ALL', NULL, NULL, NULL, 'centralization', 'SELECT ''long_xact'' AS source,
       CAST(pid AS text) AS identifier,
       EXTRACT(EPOCH FROM (now()-xact_start)) AS xmin_age_s,
       ''usename='' || COALESCE(usename,'''') || '' state='' || COALESCE(state,'''') ||
         '' query='' || COALESCE(substr(query,1,200),'''') AS detail
  FROM pg_stat_activity
 WHERE xact_start IS NOT NULL
   AND state IN (''active'',''idle in transaction'')
   AND COALESCE(connection_info,'''') <> ''''
   AND pid <> pg_backend_pid()
UNION ALL
SELECT ''prepared_xact'' AS source,
       gid AS identifier,
       EXTRACT(EPOCH FROM (now()-prepared)) AS xmin_age_s,
       ''owner='' || COALESCE(owner,'''') || '' database='' || COALESCE(database,'''') AS detail
  FROM pg_prepared_xacts
UNION ALL
SELECT ''replication_slot'' AS source,
       slot_name AS identifier,
       CAST(NULL AS double precision) AS xmin_age_s,
       ''xmin='' || COALESCE(CAST(xmin AS text),'''') ||
         '' catalog_xmin='' || COALESCE(CAST(catalog_xmin AS text),'''') ||
         '' active='' || CAST(active AS text) AS detail
  FROM pg_replication_slots
 WHERE xmin IS NOT NULL OR catalog_xmin IS NOT NULL
ORDER BY xmin_age_s DESC NULLS FIRST;
', '[]', 'AGENT', 1, '999999999', now(), '999999999', NULL, 0, NULL, 'ALL', uuid());
INSERT INTO grmp.script_config (id, script_type, script_name, database_type, refered_appbusiness, kernel_version, region, deployment_form, execute_node_type, cluster_deployment_mode, script_content, parameter_config, scene, is_valid, create_user, create_time, last_modify_user, last_modify_time, is_asyn, "extend", compliance_mode, uuid) VALUES (grmp.script_config_seq.nextval, 'SQL', 'waitevent.events', 'postgres', 1, 'ALL', NULL, NULL, NULL, 'centralization', 'WITH b AS (SELECT snap_type AS wait_class, snap_event AS event, sum(snap_wait) AS waits, sum(snap_total_wait_time) AS wt
             FROM snapshot.snap_global_wait_events WHERE snapshot_id={{b}} GROUP BY snap_type, snap_event),
     e AS (SELECT snap_type AS wait_class, snap_event AS event, sum(snap_wait) AS waits, sum(snap_total_wait_time) AS wt
             FROM snapshot.snap_global_wait_events WHERE snapshot_id={{e}} GROUP BY snap_type, snap_event)
SELECT e.wait_class,
       e.event,
       SUM(e.waits-b.waits) AS waits,
       SUM(e.wt-b.wt)       AS wait_us
FROM e JOIN b USING (wait_class, event)
WHERE upper(e.wait_class) NOT IN (''STATUS'',''NONE'')
GROUP BY e.wait_class, e.event
HAVING SUM(e.wt-b.wt) > 0
ORDER BY wait_us DESC LIMIT {{top}};
', '[{"key":"b","value":"","type":"INTEGER","autoAcquire":false},{"key":"e","value":"","type":"INTEGER","autoAcquire":false},{"key":"top","value":"","type":"INTEGER","autoAcquire":false}]', 'AGENT', 1, '999999999', now(), '999999999', NULL, 0, NULL, 'ALL', uuid());
INSERT INTO grmp.script_config (id, script_type, script_name, database_type, refered_appbusiness, kernel_version, region, deployment_form, execute_node_type, cluster_deployment_mode, script_content, parameter_config, scene, is_valid, create_user, create_time, last_modify_user, last_modify_time, is_asyn, "extend", compliance_mode, uuid) VALUES (grmp.script_config_seq.nextval, 'SQL', 'waitevent.instance_time', 'postgres', 1, 'ALL', NULL, NULL, NULL, 'centralization', 'WITH b AS (SELECT snap_stat_name AS stat_name, sum(snap_value) AS v
             FROM snapshot.snap_global_instance_time
            WHERE snapshot_id = {{b}} GROUP BY snap_stat_name),
     e AS (SELECT snap_stat_name AS stat_name, sum(snap_value) AS v
             FROM snapshot.snap_global_instance_time
            WHERE snapshot_id = {{e}} GROUP BY snap_stat_name)
SELECT e.stat_name AS stat_name,
       (e.v - b.v)  AS delta_us
  FROM e JOIN b USING (stat_name)
 ORDER BY delta_us DESC;
', '[{"key":"b","value":"","type":"INTEGER","autoAcquire":false},{"key":"e","value":"","type":"INTEGER","autoAcquire":false}]', 'AGENT', 1, '999999999', now(), '999999999', NULL, 0, NULL, 'ALL', uuid());
INSERT INTO grmp.script_config (id, script_type, script_name, database_type, refered_appbusiness, kernel_version, region, deployment_form, execute_node_type, cluster_deployment_mode, script_content, parameter_config, scene, is_valid, create_user, create_time, last_modify_user, last_modify_time, is_asyn, "extend", compliance_mode, uuid) VALUES (grmp.script_config_seq.nextval, 'SQL', 'wdr.cache', 'postgres', 1, 'ALL', NULL, NULL, NULL, 'centralization', 'WITH b AS (SELECT db_name, snap_schemaname, snap_relname,
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
', '[{"key":"b","value":"","type":"INTEGER","autoAcquire":false},{"key":"e","value":"","type":"INTEGER","autoAcquire":false},{"key":"top","value":"","type":"INTEGER","autoAcquire":false}]', 'AGENT', 1, '999999999', now(), '999999999', NULL, 0, NULL, 'ALL', uuid());
INSERT INTO grmp.script_config (id, script_type, script_name, database_type, refered_appbusiness, kernel_version, region, deployment_form, execute_node_type, cluster_deployment_mode, script_content, parameter_config, scene, is_valid, create_user, create_time, last_modify_user, last_modify_time, is_asyn, "extend", compliance_mode, uuid) VALUES (grmp.script_config_seq.nextval, 'SQL', 'wdr.checkpoint', 'postgres', 1, 'ALL', NULL, NULL, NULL, 'centralization', 'WITH b AS (SELECT snap_node_name, snap_checkpoints_timed AS timed, snap_checkpoints_req AS req
             FROM snapshot.snap_global_bgwriter_stat WHERE snapshot_id={{b}}),
     e AS (SELECT snap_node_name, snap_checkpoints_timed AS timed, snap_checkpoints_req AS req
             FROM snapshot.snap_global_bgwriter_stat WHERE snapshot_id={{e}})
SELECT COALESCE(SUM(e.timed-b.timed),0) AS checkpoints_timed,
       COALESCE(SUM(e.req-b.req),0)     AS checkpoints_req
FROM e JOIN b USING (snap_node_name);
', '[{"key":"b","value":"","type":"INTEGER","autoAcquire":false},{"key":"e","value":"","type":"INTEGER","autoAcquire":false}]', 'AGENT', 1, '999999999', now(), '999999999', NULL, 0, NULL, 'ALL', uuid());
INSERT INTO grmp.script_config (id, script_type, script_name, database_type, refered_appbusiness, kernel_version, region, deployment_form, execute_node_type, cluster_deployment_mode, script_content, parameter_config, scene, is_valid, create_user, create_time, last_modify_user, last_modify_time, is_asyn, "extend", compliance_mode, uuid) VALUES (grmp.script_config_seq.nextval, 'SQL', 'wdr.db_stat', 'postgres', 1, 'ALL', NULL, NULL, NULL, 'centralization', 'WITH b AS (SELECT snap_datname, snap_xact_commit, snap_xact_rollback, snap_deadlocks,
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
', '[{"key":"b","value":"","type":"INTEGER","autoAcquire":false},{"key":"e","value":"","type":"INTEGER","autoAcquire":false}]', 'AGENT', 1, '999999999', now(), '999999999', NULL, 0, NULL, 'ALL', uuid());
INSERT INTO grmp.script_config (id, script_type, script_name, database_type, refered_appbusiness, kernel_version, region, deployment_form, execute_node_type, cluster_deployment_mode, script_content, parameter_config, scene, is_valid, create_user, create_time, last_modify_user, last_modify_time, is_asyn, "extend", compliance_mode, uuid) VALUES (grmp.script_config_seq.nextval, 'SQL', 'wdr.db_summary', 'postgres', 1, 'ALL', NULL, NULL, NULL, 'centralization', 'WITH b AS (SELECT snap_datname, snap_xact_commit, snap_blks_read, snap_blks_hit
             FROM snapshot.snap_summary_stat_database WHERE snapshot_id={{b}}),
     e AS (SELECT snap_datname, snap_xact_commit, snap_blks_read, snap_blks_hit
             FROM snapshot.snap_summary_stat_database WHERE snapshot_id={{e}})
SELECT COALESCE(SUM(e.snap_xact_commit-b.snap_xact_commit),0) AS xact_commit,
       COALESCE(SUM(e.snap_blks_read-b.snap_blks_read),0)     AS blks_read,
       COALESCE(SUM(e.snap_blks_hit-b.snap_blks_hit),0)       AS blks_hit
FROM e JOIN b USING (snap_datname);
', '[{"key":"b","value":"","type":"INTEGER","autoAcquire":false},{"key":"e","value":"","type":"INTEGER","autoAcquire":false}]', 'AGENT', 1, '999999999', now(), '999999999', NULL, 0, NULL, 'ALL', uuid());
INSERT INTO grmp.script_config (id, script_type, script_name, database_type, refered_appbusiness, kernel_version, region, deployment_form, execute_node_type, cluster_deployment_mode, script_content, parameter_config, scene, is_valid, create_user, create_time, last_modify_user, last_modify_time, is_asyn, "extend", compliance_mode, uuid) VALUES (grmp.script_config_seq.nextval, 'SQL', 'wdr.file_io', 'postgres', 1, 'ALL', NULL, NULL, NULL, 'centralization', 'WITH b AS (SELECT snap_filenum, snap_dbid, snap_spcid, snap_phyrds AS reads, snap_phywrts AS writes
             FROM snapshot.snap_summary_file_iostat WHERE snapshot_id={{b}}),
     e AS (SELECT snap_filenum, snap_dbid, snap_spcid, snap_phyrds AS reads, snap_phywrts AS writes
             FROM snapshot.snap_summary_file_iostat WHERE snapshot_id={{e}})
SELECT (''db''||e.snap_dbid||''/spc''||e.snap_spcid||''/f''||e.snap_filenum) AS filename,
       (e.reads-b.reads)   AS reads,
       (e.writes-b.writes) AS writes
FROM e JOIN b USING (snap_filenum, snap_dbid, snap_spcid)
WHERE (e.reads-b.reads) > 0 OR (e.writes-b.writes) > 0
ORDER BY reads DESC LIMIT {{top}};
', '[{"key":"b","value":"","type":"INTEGER","autoAcquire":false},{"key":"e","value":"","type":"INTEGER","autoAcquire":false},{"key":"top","value":"","type":"INTEGER","autoAcquire":false}]', 'AGENT', 1, '999999999', now(), '999999999', NULL, 0, NULL, 'ALL', uuid());
INSERT INTO grmp.script_config (id, script_type, script_name, database_type, refered_appbusiness, kernel_version, region, deployment_form, execute_node_type, cluster_deployment_mode, script_content, parameter_config, scene, is_valid, create_user, create_time, last_modify_user, last_modify_time, is_asyn, "extend", compliance_mode, uuid) VALUES (grmp.script_config_seq.nextval, 'SQL', 'wdr.load_profile', 'postgres', 1, 'ALL', NULL, NULL, NULL, 'centralization', 'WITH b AS (SELECT snap_unique_sql_id AS sid, sum(snap_total_elapse_time) AS t, sum(snap_cpu_time) AS c
             FROM snapshot.snap_summary_statement WHERE snapshot_id={{b}} GROUP BY snap_unique_sql_id),
     e AS (SELECT snap_unique_sql_id AS sid, sum(snap_total_elapse_time) AS t, sum(snap_cpu_time) AS c
             FROM snapshot.snap_summary_statement WHERE snapshot_id={{e}} GROUP BY snap_unique_sql_id)
SELECT COALESCE(SUM(e.t-b.t),0)  AS db_time_us,
       COALESCE(SUM(e.c-b.c),0)  AS cpu_time_us
FROM e JOIN b USING (sid);
', '[{"key":"b","value":"","type":"INTEGER","autoAcquire":false},{"key":"e","value":"","type":"INTEGER","autoAcquire":false}]', 'AGENT', 1, '999999999', now(), '999999999', NULL, 0, NULL, 'ALL', uuid());
INSERT INTO grmp.script_config (id, script_type, script_name, database_type, refered_appbusiness, kernel_version, region, deployment_form, execute_node_type, cluster_deployment_mode, script_content, parameter_config, scene, is_valid, create_user, create_time, last_modify_user, last_modify_time, is_asyn, "extend", compliance_mode, uuid) VALUES (grmp.script_config_seq.nextval, 'SQL', 'wdr.native_report', 'postgres', 1, 'ALL', NULL, NULL, NULL, 'centralization', 'SELECT generate_wdr_report({{begin}}, {{end}}, ''all'', ''{{scope}}'', ''{{node}}'') AS report_line;
', '[{"key":"begin","value":"","type":"INTEGER","autoAcquire":false},{"key":"end","value":"","type":"INTEGER","autoAcquire":false},{"key":"scope","value":"","type":"STRING","autoAcquire":false},{"key":"node","value":"","type":"STRING","autoAcquire":false}]', 'AGENT', 1, '999999999', now(), '999999999', NULL, 0, NULL, 'ALL', uuid());
INSERT INTO grmp.script_config (id, script_type, script_name, database_type, refered_appbusiness, kernel_version, region, deployment_form, execute_node_type, cluster_deployment_mode, script_content, parameter_config, scene, is_valid, create_user, create_time, last_modify_user, last_modify_time, is_asyn, "extend", compliance_mode, uuid) VALUES (grmp.script_config_seq.nextval, 'SQL', 'wdr.node_name', 'postgres', 1, 'ALL', NULL, NULL, NULL, 'centralization', 'SHOW pgxc_node_name;
', '[]', 'AGENT', 1, '999999999', now(), '999999999', NULL, 0, NULL, 'ALL', uuid());
INSERT INTO grmp.script_config (id, script_type, script_name, database_type, refered_appbusiness, kernel_version, region, deployment_form, execute_node_type, cluster_deployment_mode, script_content, parameter_config, scene, is_valid, create_user, create_time, last_modify_user, last_modify_time, is_asyn, "extend", compliance_mode, uuid) VALUES (grmp.script_config_seq.nextval, 'SQL', 'wdr.snapshots', 'postgres', 1, 'ALL', NULL, NULL, NULL, 'centralization', 'SELECT snapshot_id,
       to_char(start_ts,''YYYY-MM-DD HH24:MI'') AS start_ts,
       to_char(end_ts,''YYYY-MM-DD HH24:MI'')   AS end_ts,
       round(EXTRACT(EPOCH FROM (end_ts-start_ts))/60)::bigint AS dur_min
FROM snapshot.snapshot ORDER BY snapshot_id DESC LIMIT {{limit}};
', '[{"key":"limit","value":"","type":"INTEGER","autoAcquire":false}]', 'AGENT', 1, '999999999', now(), '999999999', NULL, 0, NULL, 'ALL', uuid());
INSERT INTO grmp.script_config (id, script_type, script_name, database_type, refered_appbusiness, kernel_version, region, deployment_form, execute_node_type, cluster_deployment_mode, script_content, parameter_config, scene, is_valid, create_user, create_time, last_modify_user, last_modify_time, is_asyn, "extend", compliance_mode, uuid) VALUES (grmp.script_config_seq.nextval, 'SQL', 'wdr.top_sql', 'postgres', 1, 'ALL', NULL, NULL, NULL, 'centralization', 'WITH b AS (SELECT snap_unique_sql_id AS sid, max(snap_query) AS query,
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
', '[{"key":"b","value":"","type":"INTEGER","autoAcquire":false},{"key":"e","value":"","type":"INTEGER","autoAcquire":false},{"key":"top","value":"","type":"INTEGER","autoAcquire":false}]', 'AGENT', 1, '999999999', now(), '999999999', NULL, 0, NULL, 'ALL', uuid());
INSERT INTO grmp.script_config (id, script_type, script_name, database_type, refered_appbusiness, kernel_version, region, deployment_form, execute_node_type, cluster_deployment_mode, script_content, parameter_config, scene, is_valid, create_user, create_time, last_modify_user, last_modify_time, is_asyn, "extend", compliance_mode, uuid) VALUES (grmp.script_config_seq.nextval, 'SQL', 'wdr.waits', 'postgres', 1, 'ALL', NULL, NULL, NULL, 'centralization', 'WITH b AS (SELECT snap_type AS wait_class, snap_event AS event, sum(snap_wait) AS waits, sum(snap_total_wait_time) AS wt
             FROM snapshot.snap_global_wait_events WHERE snapshot_id={{b}} GROUP BY snap_type, snap_event),
     e AS (SELECT snap_type AS wait_class, snap_event AS event, sum(snap_wait) AS waits, sum(snap_total_wait_time) AS wt
             FROM snapshot.snap_global_wait_events WHERE snapshot_id={{e}} GROUP BY snap_type, snap_event)
SELECT e.wait_class,
       SUM(e.waits-b.waits) AS waits,
       SUM(e.wt-b.wt)       AS wait_us
FROM e JOIN b USING (wait_class, event)
WHERE upper(e.wait_class) NOT IN (''STATUS'',''NONE'')
GROUP BY e.wait_class
HAVING SUM(e.wt-b.wt) > 0
ORDER BY wait_us DESC LIMIT {{top}};
', '[{"key":"b","value":"","type":"INTEGER","autoAcquire":false},{"key":"e","value":"","type":"INTEGER","autoAcquire":false},{"key":"top","value":"","type":"INTEGER","autoAcquire":false}]', 'AGENT', 1, '999999999', now(), '999999999', NULL, 0, NULL, 'ALL', uuid());
INSERT INTO grmp.script_config (id, script_type, script_name, database_type, refered_appbusiness, kernel_version, region, deployment_form, execute_node_type, cluster_deployment_mode, script_content, parameter_config, scene, is_valid, create_user, create_time, last_modify_user, last_modify_time, is_asyn, "extend", compliance_mode, uuid) VALUES (grmp.script_config_seq.nextval, 'SQL', 'wdr.wdr_enabled', 'postgres', 1, 'ALL', NULL, NULL, NULL, 'centralization', 'SHOW enable_wdr_snapshot;
', '[]', 'AGENT', 1, '999999999', now(), '999999999', NULL, 0, NULL, 'ALL', uuid());
INSERT INTO grmp.script_config (id, script_type, script_name, database_type, refered_appbusiness, kernel_version, region, deployment_form, execute_node_type, cluster_deployment_mode, script_content, parameter_config, scene, is_valid, create_user, create_time, last_modify_user, last_modify_time, is_asyn, "extend", compliance_mode, uuid) VALUES (grmp.script_config_seq.nextval, 'SQL', 'wdr.window', 'postgres', 1, 'ALL', NULL, NULL, NULL, 'centralization', 'SELECT to_char(b.start_ts,''YYYY-MM-DD HH24:MI'') AS b_start,
       to_char(e.start_ts,''YYYY-MM-DD HH24:MI'') AS e_start,
       round(EXTRACT(EPOCH FROM (e.start_ts-b.start_ts))/60)::bigint AS dur
FROM (SELECT start_ts FROM snapshot.snapshot WHERE snapshot_id={{begin}}) b,
     (SELECT start_ts FROM snapshot.snapshot WHERE snapshot_id={{end}}) e;
', '[{"key":"begin","value":"","type":"INTEGER","autoAcquire":false},{"key":"end","value":"","type":"INTEGER","autoAcquire":false}]', 'AGENT', 1, '999999999', now(), '999999999', NULL, 0, NULL, 'ALL', uuid());