#!/bin/bash
# 演示复位到"导入前":清空 <安装根>/kb(只留指向容器的 kb.yaml),清两库的表——health 应显示「知识库未接入(存储里还没有索引)」。
# 用法:bash scripts/kb/demo-reset.sh [KB 目录,默认 ~/.config/opencode/kb]
set -u
export PATH=/opt/homebrew/bin:/usr/local/bin:$PATH
KB="${1:-$HOME/.config/opencode/kb}"
source "$HOME/.kb-test.env"
rm -rf "$KB"; mkdir -p "$KB"
cat > "$KB/kb.yaml" <<'YAML'
store:
  pg: {host: 127.0.0.1, port: 5440, database: kb, user: kb, credential: kb-pg}
  graph: {url: http://127.0.0.1:7474, user: neo4j, credential: kb-graph}
embeddings: {source: none}
YAML
docker exec kbpg psql -U kb -d kb -q -c "DROP TABLE IF EXISTS kb_chunks, kb_node_vectors, kb_docs, kb_meta CASCADE" >/dev/null 2>&1 \
  && echo "kbpg 表已清" || echo "kbpg 清表失败(容器没起?)"
docker exec kbneo4j cypher-shell -u neo4j -p "$KB_NEO4J_PW" "MATCH (n) DETACH DELETE n" >/dev/null 2>&1 \
  && echo "kbneo4j 已清" || echo "kbneo4j 清图失败(容器没起?)"
echo "复位完成:$KB 只剩 kb.yaml。现在 health 应为「未接入(存储里还没有索引)」。"
