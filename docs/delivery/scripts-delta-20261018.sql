-- =====================================================================
-- GRMP 诊断脚本白名单 · 增量导入（2026-10-18）
-- =====================================================================
--
-- 适用前提：已导入过上一版的 105 条脚本。本文件只含相对那一版的**增量**，
-- 不要整份重导（整份见同目录 scripts.sql，共 113 条）。
--
--   新增   8 条 INSERT
--   更新   7 条 UPDATE
--
-- 全部脚本均为**只读查询**，只访问系统视图与系统表，不读业务数据、
-- 不含 DML/DDL、不修改任何对象。
--
-- 导入前请确认两处与贵方规范的对齐：
--   1. `create_user` / `last_modify_user` 当前填 '999999999'，与上一版保持一致；
--      若贵方要求真实工号，请统一替换。
--   2. id 用 `grmp.script_config_seq.nextval`，不硬编码，避免与现有记录冲突。
--
-- =====================================================================
-- 一、新增 8 条
-- =====================================================================

-- [config.settings] 全量运行参数
--   读取：pg_settings
--   用途：参数体检：检查参数自相矛盾、会静默丢数据的危险配置、按实例内存预算算出的超卖
INSERT INTO grmp.script_config (id, script_type, script_name, database_type, refered_appbusiness, kernel_version, region, deployment_form, execute_node_type, cluster_deployment_mode, script_content, parameter_config, scene, is_valid, create_user, create_time, last_modify_user, last_modify_time, is_asyn, "extend", compliance_mode, uuid) VALUES (grmp.script_config_seq.nextval, 'SQL', 'config.settings', 'appbusiness', 1, 'ALL', NULL, NULL, NULL, 'centralization', 'SELECT name,
       COALESCE(setting,'''')    AS setting,
       COALESCE(unit,'''')       AS unit,
       COALESCE(vartype,'''')    AS vartype,
       COALESCE(boot_val,'''')   AS boot_val,
       COALESCE(reset_val,'''')  AS reset_val,
       COALESCE(context,'''')    AS context,
       COALESCE(source,'''')     AS source,
       COALESCE(min_val,'''')    AS min_val,
       COALESCE(max_val,'''')    AS max_val,
       COALESCE(enumvals::text,'''') AS enumvals,
       COALESCE(category,'''')   AS category,
       COALESCE(short_desc,'''') AS short_desc
  FROM pg_settings
 ORDER BY name;
', '[]', 'AGENT', 1, '999999999', now(), '999999999', NULL, 0, NULL, 'ALL', uuid());

-- [health.partitions] 分区表的分区数量与策略
--   读取：pg_partition / pg_class
--   用途：分区数达到四位数时每次查询规划都要遍历分区元数据，规划开销明显上升
INSERT INTO grmp.script_config (id, script_type, script_name, database_type, refered_appbusiness, kernel_version, region, deployment_form, execute_node_type, cluster_deployment_mode, script_content, parameter_config, scene, is_valid, create_user, create_time, last_modify_user, last_modify_time, is_asyn, "extend", compliance_mode, uuid) VALUES (grmp.script_config_seq.nextval, 'SQL', 'health.partitions', 'appbusiness', 1, 'ALL', NULL, NULL, NULL, 'centralization', 'SELECT n.nspname      AS schema,
       c.relname      AS "table",
       count(*)       AS parts,
       COALESCE(max(p.partstrategy), '''') AS strategy
  FROM pg_partition p
  JOIN pg_class c     ON c.oid = p.parentid
  JOIN pg_namespace n ON n.oid = c.relnamespace
 WHERE p.parttype = ''p''
   AND n.nspname NOT IN (''pg_catalog'',''information_schema'',''snapshot'',''dbe_perf'',
                         ''dbe_pldeveloper'',''cstore'',''pg_toast'')
 GROUP BY n.nspname, c.relname
 ORDER BY parts DESC;
', '[]', 'AGENT', 1, '999999999', now(), '999999999', NULL, 0, NULL, 'ALL', uuid());

-- [health.storage] 库与表空间占用量
--   读取：pg_database / pg_tablespace
--   用途：定位哪个库或表空间在增长。注：本内核取不到文件系统剩余空间，只能看占用量
INSERT INTO grmp.script_config (id, script_type, script_name, database_type, refered_appbusiness, kernel_version, region, deployment_form, execute_node_type, cluster_deployment_mode, script_content, parameter_config, scene, is_valid, create_user, create_time, last_modify_user, last_modify_time, is_asyn, "extend", compliance_mode, uuid) VALUES (grmp.script_config_seq.nextval, 'SQL', 'health.storage', 'appbusiness', 1, 'ALL', NULL, NULL, NULL, 'centralization', 'SELECT ''database'' AS kind,
       d.datname  AS name,
       pg_database_size(d.oid)::bigint AS bytes
  FROM pg_database d
 WHERE d.datallowconn
UNION ALL
SELECT ''tablespace'' AS kind,
       t.spcname    AS name,
       pg_tablespace_size(t.oid)::bigint AS bytes
  FROM pg_tablespace t
 ORDER BY bytes DESC;
', '[]', 'AGENT', 1, '999999999', now(), '999999999', NULL, 0, NULL, 'ALL', uuid());

-- [health.sync_commit] 同步提交配置与部署形态
--   读取：pg_settings / pg_is_in_recovery()
--   用途：集群要求同步确认却没有可用同步备时，主库提交会一直等待、写入全部挂住。先判断单机/集群，避免在单机库上误报
INSERT INTO grmp.script_config (id, script_type, script_name, database_type, refered_appbusiness, kernel_version, region, deployment_form, execute_node_type, cluster_deployment_mode, script_content, parameter_config, scene, is_valid, create_user, create_time, last_modify_user, last_modify_time, is_asyn, "extend", compliance_mode, uuid) VALUES (grmp.script_config_seq.nextval, 'SQL', 'health.sync_commit', 'appbusiness', 1, 'ALL', NULL, NULL, NULL, 'centralization', 'SELECT ''peers_configured'' AS item,
       (SELECT count(*)::text FROM pg_settings
         WHERE name LIKE ''replconninfo%'' AND COALESCE(setting,'''') <> '''') AS value
UNION ALL
SELECT ''in_recovery'' AS item, pg_is_in_recovery()::text AS value
UNION ALL
SELECT ''sync_commit'' AS item,
       COALESCE((SELECT setting FROM pg_settings WHERE name=''synchronous_commit''),'''') AS value
UNION ALL
SELECT ''standby_names'' AS item,
       COALESCE((SELECT setting FROM pg_settings WHERE name=''synchronous_standby_names''),'''') AS value;
', '[]', 'AGENT', 1, '999999999', now(), '999999999', NULL, 0, NULL, 'ALL', uuid());

-- [health.thread_pool] 线程池用量
--   读取：pg_settings / pg_stat_activity
--   用途：开启线程池后真正的并发上限是池大小而非 max_connections；池满后新请求排队，表现为「库无响应」而连接数看着仍有余量
INSERT INTO grmp.script_config (id, script_type, script_name, database_type, refered_appbusiness, kernel_version, region, deployment_form, execute_node_type, cluster_deployment_mode, script_content, parameter_config, scene, is_valid, create_user, create_time, last_modify_user, last_modify_time, is_asyn, "extend", compliance_mode, uuid) VALUES (grmp.script_config_seq.nextval, 'SQL', 'health.thread_pool', 'appbusiness', 1, 'ALL', NULL, NULL, NULL, 'centralization', 'SELECT ''enable_thread_pool'' AS item,
       COALESCE((SELECT setting FROM pg_settings WHERE name=''enable_thread_pool''),'''') AS value
UNION ALL
SELECT ''thread_pool_attr'' AS item,
       COALESCE((SELECT setting FROM pg_settings WHERE name=''thread_pool_attr''),'''') AS value
UNION ALL
SELECT ''max_connections'' AS item,
       COALESCE((SELECT setting FROM pg_settings WHERE name=''max_connections''),'''') AS value
UNION ALL
SELECT ''real_sessions'' AS item,
       (SELECT count(*)::text FROM pg_stat_activity WHERE sessionid <> 0) AS value;
', '[]', 'AGENT', 1, '999999999', now(), '999999999', NULL, 0, NULL, 'ALL', uuid());

-- [health.wal_slots] 复制槽落后的 WAL 字节数
--   读取：pg_replication_slots
--   用途：槽不消费时 WAL 只增不减直到填满磁盘；未活跃的槽尤其危险，没有任何机制会自动清理
INSERT INTO grmp.script_config (id, script_type, script_name, database_type, refered_appbusiness, kernel_version, region, deployment_form, execute_node_type, cluster_deployment_mode, script_content, parameter_config, scene, is_valid, create_user, create_time, last_modify_user, last_modify_time, is_asyn, "extend", compliance_mode, uuid) VALUES (grmp.script_config_seq.nextval, 'SQL', 'health.wal_slots', 'appbusiness', 1, 'ALL', NULL, NULL, NULL, 'centralization', 'SELECT slot_name,
       COALESCE(slot_type,'''')  AS slot_type,
       COALESCE(database,'''')   AS database,
       active::text            AS active,
       pg_xlog_location_diff(pg_current_xlog_location(), restart_lsn)::bigint AS lag_bytes
  FROM pg_replication_slots
 WHERE restart_lsn IS NOT NULL
 ORDER BY lag_bytes DESC;
', '[]', 'AGENT', 1, '999999999', now(), '999999999', NULL, 0, NULL, 'ALL', uuid());

-- [vacuum.bloat_estimate] 表膨胀估算的输入
--   读取：pg_class / pg_stats
--   用途：死元组比例回答「有多少可回收」，这条回答「空间比应占多了多少」——后者 VACUUM 不还盘，需要评估重建
INSERT INTO grmp.script_config (id, script_type, script_name, database_type, refered_appbusiness, kernel_version, region, deployment_form, execute_node_type, cluster_deployment_mode, script_content, parameter_config, scene, is_valid, create_user, create_time, last_modify_user, last_modify_time, is_asyn, "extend", compliance_mode, uuid) VALUES (grmp.script_config_seq.nextval, 'SQL', 'vacuum.bloat_estimate', 'appbusiness', 1, 'ALL', NULL, NULL, NULL, 'centralization', 'SELECT n.nspname AS schema,
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
 WHERE c.relkind = ''r''
   AND n.nspname NOT IN (''pg_catalog'',''information_schema'',''snapshot'',''dbe_perf'',
                         ''dbe_pldeveloper'',''cstore'',''pg_toast'')
   AND pg_relation_size(c.oid) > 8192 * 128
 ORDER BY pg_relation_size(c.oid) DESC
 LIMIT {{limit}};
', '[{"key":"limit","value":"","type":"INTEGER","autoAcquire":false}]', 'AGENT', 1, '999999999', now(), '999999999', NULL, 0, NULL, 'ALL', uuid());

-- [vacuum.freeze_age] 事务号冻结水位
--   读取：pg_database.datfrozenxid64 / pg_settings
--   用途：冻结水位接近 autovacuum_freeze_max_age 时，autovacuum 会对大表触发不择时的全表冻结扫描（IO 风暴）；水位不推进则 clog/csnlog 无法截断、持续占盘
INSERT INTO grmp.script_config (id, script_type, script_name, database_type, refered_appbusiness, kernel_version, region, deployment_form, execute_node_type, cluster_deployment_mode, script_content, parameter_config, scene, is_valid, create_user, create_time, last_modify_user, last_modify_time, is_asyn, "extend", compliance_mode, uuid) VALUES (grmp.script_config_seq.nextval, 'SQL', 'vacuum.freeze_age', 'appbusiness', 1, 'ALL', NULL, NULL, NULL, 'centralization', 'SELECT d.datname,
       (txid_snapshot_xmin(txid_current_snapshot())
        - d.datfrozenxid64::text::numeric)::bigint      AS freeze_age,
       (SELECT setting::numeric FROM pg_settings
         WHERE name = ''autovacuum_freeze_max_age'')::bigint AS max_age,
       (SELECT setting FROM pg_settings
         WHERE name = ''vacuum_freeze_table_age'')         AS freeze_table_age
  FROM pg_database d
 WHERE d.datallowconn
 ORDER BY freeze_age DESC;
', '[]', 'AGENT', 1, '999999999', now(), '999999999', NULL, 0, NULL, 'ALL', uuid());

-- =====================================================================
-- 二、更新 7 条已有脚本的 SQL 正文
-- =====================================================================

-- [proctune.plan_text_analyze]
--   **修正拼写错误**：同上（`BUFFERS fasle` → `BUFFERS false`）。
UPDATE grmp.script_config
   SET script_content   = 'EXPLAIN (ANALYZE false, BUFFERS false, FORMAT TEXT) {{sql}}
',
       last_modify_user = '999999999',
       last_modify_time = now()
 WHERE script_name = 'proctune.plan_text_analyze';

-- [sqltune.plan_text_analyze]
--   **修正拼写错误**：`BUFFERS fasle` → `BUFFERS false`。`fasle` 不是合法布尔值，该脚本执行会直接报错。
UPDATE grmp.script_config
   SET script_content   = 'EXPLAIN (ANALYZE false, BUFFERS false, FORMAT TEXT) {{sql}}
',
       last_modify_user = '999999999',
       last_modify_time = now()
 WHERE script_name = 'sqltune.plan_text_analyze';

-- [wdr.cache]
--   **修正取数错误**（同一问题，预防性）：内连接会丢掉窗口内新建的表。 实测窗口里两端对称、未丢行，但结构上与 wdr.file_io 相同，窗口内真新建表时会静默少算。 改为 LEFT JOIN + COALESCE。
UPDATE grmp.script_config
   SET script_content   = 'WITH b AS (SELECT db_name, snap_schemaname, snap_relname,
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
',
       last_modify_user = '999999999',
       last_modify_time = now()
 WHERE script_name = 'wdr.cache';

-- [wdr.db_stat]
--   **修正取数错误**（同一问题，预防性）：内连接会丢掉窗口内新建的库。改为 LEFT JOIN + COALESCE。
UPDATE grmp.script_config
   SET script_content   = 'WITH b AS (SELECT snap_datname, snap_xact_commit, snap_xact_rollback, snap_deadlocks,
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
',
       last_modify_user = '999999999',
       last_modify_time = now()
 WHERE script_name = 'wdr.db_stat';

-- [wdr.db_summary]
--   **修正取数错误**（同一问题，预防性）：内连接会丢掉窗口内新建的库。改为 LEFT JOIN + COALESCE。
UPDATE grmp.script_config
   SET script_content   = 'WITH b AS (SELECT snap_datname, snap_xact_commit, snap_blks_read, snap_blks_hit
             FROM snapshot.snap_summary_stat_database WHERE snapshot_id={{b}}),
     e AS (SELECT snap_datname, snap_xact_commit, snap_blks_read, snap_blks_hit
             FROM snapshot.snap_summary_stat_database WHERE snapshot_id={{e}})
SELECT COALESCE(SUM(e.snap_xact_commit - COALESCE(b.snap_xact_commit, 0)),0) AS xact_commit,
       COALESCE(SUM(e.snap_blks_read - COALESCE(b.snap_blks_read, 0)),0)     AS blks_read,
       COALESCE(SUM(e.snap_blks_hit - COALESCE(b.snap_blks_hit, 0)),0)       AS blks_hit
FROM e LEFT JOIN b USING (snap_datname);
',
       last_modify_user = '999999999',
       last_modify_time = now()
 WHERE script_name = 'wdr.db_summary';

-- [wdr.file_io]
--   **修正取数错误**（与 wdr.top_sql 同一问题）：内连接丢掉窗口内新出现的文件。 实测一个 20 分钟窗口里 1468 个文件只统计了 185 个，物理读少报 62%、物理写少报 58%； 被丢掉的文件里最大的一个物理写 569,700 次，比内连接下整个窗口的写入总量还大 —— 写得最多的文件在报告里完全不存在。改为 LEFT JOIN + COALESCE。
UPDATE grmp.script_config
   SET script_content   = 'WITH b AS (SELECT snap_filenum, snap_dbid, snap_spcid, snap_phyrds AS reads, snap_phywrts AS writes
             FROM snapshot.snap_summary_file_iostat WHERE snapshot_id={{b}}),
     e AS (SELECT snap_filenum, snap_dbid, snap_spcid, snap_phyrds AS reads, snap_phywrts AS writes
             FROM snapshot.snap_summary_file_iostat WHERE snapshot_id={{e}})
SELECT (''db''||e.snap_dbid||''/spc''||e.snap_spcid||''/f''||e.snap_filenum) AS filename,
       (e.reads  - COALESCE(b.reads,  0)) AS reads,
       (e.writes - COALESCE(b.writes, 0)) AS writes
FROM e LEFT JOIN b USING (snap_filenum, snap_dbid, snap_spcid)
WHERE (e.reads  - COALESCE(b.reads,  0)) > 0
   OR (e.writes - COALESCE(b.writes, 0)) > 0
ORDER BY reads DESC LIMIT {{top}};
',
       last_modify_user = '999999999',
       last_modify_time = now()
 WHERE script_name = 'wdr.file_io';

-- [wdr.top_sql]
--   **修正取数错误**：原 SQL 用 `FROM e JOIN b USING (sid)` 内连接，窗口内**新出现**的语句 （起始快照里没有它）会被整批丢掉。实测一个 20 分钟窗口里 147 条语句只保留了 55 条， 导致 DB time 少报约 8800 倍（报 2.67 秒，实际 23518 秒）。改为 LEFT JOIN + COALESCE。
UPDATE grmp.script_config
   SET script_content   = 'WITH b AS (SELECT snap_unique_sql_id AS sid, max(snap_query) AS query,
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
',
       last_modify_user = '999999999',
       last_modify_time = now()
 WHERE script_name = 'wdr.top_sql';

-- =====================================================================
-- 三、导入后自检
-- =====================================================================
-- 应为 113：
--   SELECT count(*) FROM grmp.script_config WHERE scene = 'AGENT';
-- 新增的 8 条应全部在列：
--   SELECT script_name FROM grmp.script_config WHERE script_name IN ('config.settings','health.partitions','health.storage','health.sync_commit','health.thread_pool','health.wal_slots','vacuum.bloat_estimate','vacuum.freeze_age');
-- 更新应已生效（下面两条都应返回 0 行）：
--   SELECT script_name FROM grmp.script_config
--    WHERE script_name LIKE '%plan_text_analyze' AND script_content LIKE '%fasle%';
--   SELECT script_name FROM grmp.script_config
--    WHERE script_name IN ('wdr.top_sql','wdr.file_io','wdr.cache','wdr.db_stat','wdr.db_summary')
--      AND script_content NOT LIKE '%LEFT JOIN%';
