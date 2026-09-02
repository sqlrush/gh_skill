"""common.kb —— 客户知识库的共享层,所有 skill 都从这里查库。

真相在 <kb>/ 下的文件;高斯/PG(向量 + 词法)与 Neo4j(图)是可重建的派生索引。
本包只依赖标准库 + pg8000(已在 requirements.txt 白名单内),Neo4j 走 HTTP。

模块分工:
    config      kb.yaml / OpenCode provider 端点 / 凭据名(密钥只在内存,repr 不泄露)
    text        中文二元组 + 标识符分词,tsvector/tsquery 字面量(绕开引擎解析器)
    store_pg    高斯 DataVec / PG pgvector 同一套 SQL
    store_graph Neo4j HTTP Query API
    embed       OpenAI 兼容 /v1/embeddings 客户端
    query       混合检索编排
    render      「客户知识库参照」小节
"""
