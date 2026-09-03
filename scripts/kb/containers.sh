#!/bin/bash
# 知识库测试容器(幂等):kbpg(pgvector :5440)/ kbneo4j(:7474,7687)/ og7(openGauss 7 DataVec :5439)。
# 口令来自 ~/.kb-test.env(0600),首次运行自动生成,绝不打印。
set -u
export PATH=/opt/homebrew/bin:/usr/local/bin:$PATH
ENV_FILE="$HOME/.kb-test.env"
if [ ! -f "$ENV_FILE" ]; then
  umask 077
  printf 'export KB_OG7_PW=Kb%sA1@\nexport KB_PG_PW=%s\nexport KB_NEO4J_PW=%s\n' \
    "$(openssl rand -hex 12)" "$(openssl rand -hex 12)" "$(openssl rand -hex 12)" > "$ENV_FILE"
  echo "已生成 $ENV_FILE(0600)。之后用 python3 -m common.credential_cli set kb-pg / kb-graph 把同样的口令存进凭据库。"
fi
source "$ENV_FILE"
have_ctr() { docker ps -a --format '{{.Names}}' | grep -qx "$1"; }

start_pg() {
  docker image inspect pgvector/pgvector:pg16 >/dev/null 2>&1 || docker pull -q pgvector/pgvector:pg16 >/dev/null
  have_ctr kbpg || docker run -d --name kbpg -e POSTGRES_PASSWORD="$KB_PG_PW" -e POSTGRES_USER=kb -e POSTGRES_DB=kb -p 5440:5432 pgvector/pgvector:pg16 >/dev/null
  docker start kbpg >/dev/null 2>&1 || true
  for _ in $(seq 1 60); do docker exec kbpg pg_isready -U kb -d kb >/dev/null 2>&1 && { echo "kbpg ready"; return; }; sleep 2; done
  echo "kbpg NOT ready"
}
start_neo4j() {
  docker image inspect neo4j:5-community >/dev/null 2>&1 || docker pull -q neo4j:5-community >/dev/null
  have_ctr kbneo4j || docker run -d --name kbneo4j -e NEO4J_AUTH="neo4j/$KB_NEO4J_PW" -p 7474:7474 -p 7687:7687 neo4j:5-community >/dev/null
  docker start kbneo4j >/dev/null 2>&1 || true
  for _ in $(seq 1 90); do curl -sf http://127.0.0.1:7474/ >/dev/null 2>&1 && { echo "kbneo4j ready"; return; }; sleep 2; done
  echo "kbneo4j NOT ready"
}
start_og7() {
  [ "${KB_WITH_OG7:-0}" = "1" ] || { echo "og7 跳过(KB_WITH_OG7=1 才起,DataVec live 测试用)"; return; }
  IMG=opengauss/opengauss:7.0.0-RC1
  docker image inspect $IMG >/dev/null 2>&1 || docker pull -q $IMG >/dev/null
  have_ctr og7 || docker run -d --name og7 --privileged=true --shm-size=2g -e GS_PASSWORD="$KB_OG7_PW" -p 5439:5432 $IMG >/dev/null
  docker start og7 >/dev/null 2>&1 || true
  for _ in $(seq 1 100); do docker exec og7 su - omm -c "gsql -d postgres -c 'select 1'" >/dev/null 2>&1 && { echo "og7 ready"; return; }; sleep 3; done
  echo "og7 NOT ready"
}
start_pg & start_neo4j & wait
start_og7
docker ps --format '{{.Names}}\t{{.Status}}\t{{.Ports}}' | grep -E "kbpg|kbneo4j|og7"
echo
echo "live 测试环境变量:"
echo '  source ~/.kb-test.env; export KB_TEST_PGVECTOR="127.0.0.1:5440:kb:kb:$KB_PG_PW"; export KB_TEST_NEO4J="http://127.0.0.1:7474|neo4j|$KB_NEO4J_PW"; export KB_TEST_OG7="127.0.0.1:5439:postgres:gaussdb:$KB_OG7_PW"'
