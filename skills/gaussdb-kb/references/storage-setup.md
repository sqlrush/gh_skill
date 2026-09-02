# 存储接入(`<kb>/kb.yaml` + 凭据)

知识库需要两个服务:**高斯/PG**(向量 + 词法;openGauss ≥ 7.0 的 DataVec,或 PostgreSQL + pgvector)和 **Neo4j**(图,社区版即可)。
它们是知识库**专用**实例,不是被管的业务库(现场被管库经 GRMP 白名单,每条 SQL 都是发布 DML,不能放知识库)。
两个库都是可重建的派生索引,真相在 `<kb>/` 的文件里——库坏了 `kb.py index --rebuild` 就回来。

## kb.yaml(只有连接元数据与凭据名,**绝不写口令**)

```yaml
store:
  pg:    {host: 10.0.0.9, port: 5432, database: kb, user: kb, credential: kb-pg, sslmode: disable}
  graph: {url: http://10.0.0.9:7474, user: neo4j, credential: kb-graph, database: neo4j}
embeddings:
  source: opencode            # opencode = 复用 OpenCode 配置的 provider 端点;url = 显式;none = 不开向量
  model: bge-m3               # 该端点上挂的嵌入模型名
  dims: 1024
  # base_url: http://127.0.0.1:11434/v1   # source: url 时
  # api_key_env: EMBED_API_KEY            # source: url 时,可选
thresholds: {}                # 分类型分数阈值,默认即可
```

`kb.yaml` 里出现 `password` / `apikey` / `token` 直接报错。口令用加密凭据:

```bash
python3 -m common.credential_cli set kb-pg       # 交互输入,AES-256-GCM 落 $GSDB_HOME/credentials/kb-pg.enc
python3 -m common.credential_cli set kb-graph
```

## 步骤

```bash
python3 {baseDir}/scripts/kb.py setup      # 探测引擎能力、建表、建约束;打印向量有无 / hnsw 有无 / Neo4j 版本
python3 {baseDir}/scripts/kb.py index      # 文件 → 两库
python3 {baseDir}/scripts/kb.py health     # 状态行 + 覆盖率 + 待处理 + 缺口清单
```

## 降级即发现

- 引擎没有 `vector` 类型:表里不建向量列,`kb_meta.vector_engine=none`,状态行写「向量:未启用(存储引擎无 vector 类型)」;
- 配了 embedding 但覆盖率不满:`index` 退出码 2,`index --fill-missing` 补;状态行写覆盖百分比;
- 没配 embedding:只告警,不算失败,状态行写「未启用(kb.yaml 未配 embeddings)」;
- Neo4j 不可达:index 只写高斯/PG 并告警,检索时状态行写「图:不可用」,路径小节为空;
- 高斯/PG 不可达:各 skill 的小节只剩「知识库未接入(原因)」,skill 本身照常。

## embedding 端点

`source: opencode` 读 `~/.config/opencode/opencode.jsonc` 里当前 `model` 所属 provider 的 `options.baseURL` 与 key,
向 `<baseURL>/embeddings` 请求 `embeddings.model`。前提是这个 provider 上确实挂了嵌入模型(Ollama / vLLM / Xinference
都能顺道挂);纯聊天 API 没有,就用 `source: url` 指一个单独的嵌入服务,或 `source: none`。
索引侧超时 30 s/批、查询侧 1.5 s,超了本次不用向量,状态行注明。
