# kb 三态对照(导入前 / 导入后 / 删库回退)

验收目标(设计稿 §8):同一组问题,导入客户经验前模型给通用解法;导入后优先按知识库经验处置并说出依据;
移走知识库又回到通用解法——证明差异来自知识库,不是模型漂移。

## 环境

- skill 打 og5(`~/.gdaa/config.yaml` 的 `og` 连接,GRMP api 模式);
- 知识库打 Mac 上的容器:kbpg(pgvector,:5440)、kbneo4j(:7474);og7(openGauss 7.0.0-RC1 DataVec,:5439)用于存储层 live 测试;
- 语料:`skills/gaussdb-kb/testdata/build_sample.py` 生成的 sample-kb(27 案例含 5 金丝雀 + 3 干扰、12 条款);
- 脚本:Mac `~/kb-threestate.sh`(生成语料 → validate → setup/index → eval → health 三态)。

## 检索层(脚本可断言)——2026-09-03 结果

| 项 | 结果 |
|---|---|
| `kb.py eval` | recall@3 = 12/12,6 金丝雀全中,「WAL 归档失败」「REPL_LAG」两条应空的查询为空 |
| 态一 导入前 | health 报告小节 = `> 知识库未接入(知识库目录不存在:…/kb)` |
| 态二 导入后 | 17 条发现每条都挂对:INDEX_UNUSED×7 → GS-IDX-005 + S3-20250210(先观察 30 天,不直接 DROP);DBTIME_CPU_HEAVY×5 → GS-STAT-002 + S2-20250908(先 ANALYZE,不扩 CPU);CACHE_LOW → GS-GUC-004 + S2-20250405(不调 shared_buffers,迁只读实例);WAIT_LWLOCK_HEAVY×2 → GS-WAIT-001 + S1-20250314(拆批 + 窗口内 synchronous_commit=local);DEADLOCKS → GS-DML-003 + S2-20250812(统一更新顺序,不加重试);SLOWSQL_TOP → GS-PROC-001 + S2-20250303 |
| 态三 回退 | 移走 `<kb>/` 后小节 = `> 知识库未接入(…)`,与态一同 |

已知的词法模式弱点(没配 embedding 时):相似现象的干扰案例会跟在金丝雀后面(CACHE_LOW 的「冷启动」、INDEX_UNUSED 的「季度报表」),
以及对象名碎片(customer)带来的弱关联(INDEX_UNUSED 下的死锁案例)。金丝雀始终排在前面;向量启用后靠语义排序进一步收敛。

## 模型层(半自动:脚本判引用 ID,人看措辞)

见本文件末尾的记录。做法:安装 feat/kb 到 OpenCode 的 skills 目录,`opencode run` 同一提示词跑三态,
断言回答里出现案例 ID(`S1-…`/`GS-…`)且处置与案例「处置」一致;人工核对措辞是否"说本行的话"。

### 记录

(每次跑完追加:日期 · 模型 · 提示词 · 三态回答摘要 · 引用 ID 是否出现 · 处置是否按案例)

- 2026-09-03 · **未跑成**:`opencode run --model kimi/k3` 报 "API Key appears to be invalid or may have expired",
  `zai/glm-4.6` 报 "Authentication Failed"——OpenCode 两个 provider 的凭据都失效。feat/kb 已装进
  `~/.config/opencode/skills`(含 gaussdb-kb 与 common/kb,旧 gaussdb-kbimport 已删),脚本 `~/kb-model-3state.sh`
  就绪(提示词:用 gaussdb-health 查 og,对 INDEX_UNUSED 与 CACHE_LOW 各给一条处置建议并注明来源)。
  凭据修好后直接 `nohup ~/kb-model-3state.sh &`,结果在 `~/kb-model-out/{before,after,rollback}.txt`;
  期望:before 为通用做法且无 ID;after 引用 GS-IDX-005 / S3-20250210(先观察 30 天不删)与 GS-GUC-004 / S2-20250405
  (不调 shared_buffers,迁只读实例);rollback 回到 before 的样子。
