#!/bin/bash
# 演示故障:在 og5 上制造「根阻塞会话 idle in transaction,联机 update 被堵」的锁等待链。
#   start   建 cbst.acct_balance(幂等),起阻塞会话(BEGIN; UPDATE 后不提交,application_name=cbst-batch-adjust)
#           与被堵会话(UPDATE 同一行,application_name=cbst-online);两者在 og5 容器里后台常驻
#   status  看 pg_stat_activity 里这两个会话的状态与等待
#   stop    结束这两个会话并清理
# 只动 og5(测试容器),经 docker exec 以 omm 执行。
set -u
export PATH=/opt/homebrew/bin:/usr/local/bin:$PATH
CTR="${OG_CONTAINER:-og5}"
gsql() { docker exec "$CTR" su - omm -c "gsql -d postgres -At -c \"$1\""; }
show_status() {
  echo "=== pg_stat_activity(演示会话) ==="
  gsql "SELECT pid, application_name, state, waiting, now()-xact_start AS xact_age, left(query,60) FROM pg_stat_activity WHERE application_name IN ('cbst-batch-adjust','cbst-online') ORDER BY application_name"
}

case "${1:-status}" in
  start)
    gsql "CREATE SCHEMA IF NOT EXISTS cbst" >/dev/null
    gsql "CREATE TABLE IF NOT EXISTS cbst.acct_balance(acct_id int PRIMARY KEY, balance numeric(18,2), updated_at timestamptz DEFAULT now())" >/dev/null
    gsql "INSERT INTO cbst.acct_balance SELECT g, 1000, now() FROM generate_series(1,50) g ON DUPLICATE KEY UPDATE NOTHING" >/dev/null 2>&1 \
      || gsql "INSERT INTO cbst.acct_balance SELECT g, 1000, now() FROM generate_series(1,50) g WHERE NOT EXISTS (SELECT 1 FROM cbst.acct_balance)" >/dev/null
    # 阻塞会话:gsql 读 stdin,发完 BEGIN/UPDATE 后 stdin 挂着不结束 → 事务保持 idle in transaction
    docker exec -d "$CTR" bash -c "(echo \"SET application_name='cbst-batch-adjust';\"; echo 'BEGIN;'; echo 'UPDATE cbst.acct_balance SET balance = balance + 1 WHERE acct_id = 1;'; sleep 86400) | su - omm -c 'gsql -d postgres -q' > /tmp/kb-blocker.log 2>&1"
    sleep 3
    # 被堵会话:同一行 update,等锁
    docker exec -d "$CTR" bash -c "su - omm -c \"gsql -d postgres -q -c \\\"SET application_name='cbst-online'; SET statement_timeout='86400s'; UPDATE cbst.acct_balance SET balance = balance - 1 WHERE acct_id = 1;\\\"\" > /tmp/kb-waiter.log 2>&1"
    sleep 3
    show_status
    ;;
  status)
    show_status
    ;;
  stop)
    gsql "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE application_name IN ('cbst-batch-adjust','cbst-online')" >/dev/null
    docker exec "$CTR" bash -c "pkill -f 'sleep 86400' ; true" >/dev/null 2>&1
    echo "演示会话已结束"
    ;;
  *) echo "用法:$0 start|status|stop"; exit 2;;
esac
