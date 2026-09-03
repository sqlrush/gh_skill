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
- 2026-09-03 · **kimi/k3 · `scripts/kb/model-demo.sh` 全 8 步跑通**(user 换了新 key):
  - 01 导入前:小节「未接入(无索引)」;INDEX_UNUSED → 排除反例后变更窗口 `DROP INDEX`;CACHE_LOW → 先治理顺扫再评估扩缓冲池;两条均标「通用经验」。
  - 02 导入规范:模型读完 `运维规范摘录.md` 后**先给 11 条条款的确认清单**(ID/一句话/出处/去向/动作,并指出 3 条可作金丝雀),等确认;
    03 确认后写入 9 个 rules/*.yaml(`rm -rf inbox && index && validate` 被 OpenCode 非交互权限拒——用户配置里 rm 是 ask;后续步骤补跑 index,只留 inbox 残留 warn)。
  - 04 导入工单:ingest 8 单(列映射、脱敏)后**逐题问 5 个策略问题**;05 答完后模型填候选、跑 review,把 **61 项编号选择列表(16 条边无默认)** 原样呈现;
    06「全部接受,边也接受」→ apply/validate/index → 状态行「条款 11 · 案例 8 · 图 47 条已确认边」,validate 0 error 1 warn。
  - 07 导入后:INDEX_UNUSED → 「台账登记、观察满 30 天覆盖月末批量、再提变更单 23:00–06:00 窗口 DROP、双人复核〔依据:案例 S3-20250210-CBST,已确认〕」;
    CACHE_LOW → 「不得因单次命中率调 shared_buffers(NUMA 绑核固定),报表 SQL 迁只读实例〔依据:GS-GUC-001 §4.2 + 案例 S2-20250405-CBST〕」。
    注:这一步 GRMP mock 返回 503,本地维度没采到发现,模型如实说明"没查到不是健康",处置建议仍从知识库取——优先按知识库成立。
  - 08 复位后:小节「未接入(存储无索引)」,建议回到 `DROP INDEX CONCURRENTLY` / 先调优顺扫再评估 shared_buffers,标「通用经验」。
  - 原始输出在 Mac `~/kb-demo-out/01…08.txt`(含测试环境 IP,不入库)。ID 命名与示例库不同(模型自己分配 GS-GUC-001 等),属正常。
- 2026-09-03 · **kimi/k3 · 三条出处闸门加固后的对照(feat/kb-hardening → main)**。为不碰正在给客户演示的环境,在 Mac 隔离环境跑:
  `XDG_CONFIG_HOME=~/kb-verify/xdg`(独立 skills + kb),向量库 openGauss 7 DataVec(og7:5439),图库另起 `neo4j:5-community`(7475),
  Ollama bge-m3;提示词与 `model-demo.sh` 八步逐字相同(脚本 `~/kb-verify/run.sh`,输出 `~/kb-verify/out/`)。
  | 指标 | 基线(v8) | 加固后 |
  |---|---|---|
  | 06 状态行 | 条款 11 · 案例 8 · 47 条已确认边 | **条款 12 · 案例 8 · 52 条已确认边**,向量覆盖 100%(datavec) |
  | 05 选择列表 | 61 项 / 16 条边无默认 | 67 项 / 16 条边无默认,`[error]` 0——新的「现场摘录必填 / 已确认须有根因摘录」没拒掉 K3 任何一单 |
  | 04 闸门 | 首次 propose 同时吐出无策略约束的工作单 | propose 打印「策略未确认,不出工作单」退出 2,模型逐题问 8 题,答完重跑才有工作单 |
  | 07 引用 | S3-20250210-CBST / GS-GUC-001 | GS-IDX-001 + S3-20250210-CBST-未使用索引…、GS-GUC-001 + S2-20250405-CBST-报表…;`cite-check` 4/4 在库,处置按案例(台账观察 30 天 / 不调 shared_buffers 迁只读) |
  | 01 / 08 | 通用经验,无 ID | 通用经验,无 ID(08 的 health 小节「未接入」) |
  两个与代码无关的坑:① 第一轮 03 步模型把 `rm -rf inbox && index` 拼成一条被 OpenCode 非交互权限拒后**回复为空**,Kimi 随后对整段
  会话报 `assistant must not be empty`,05/06 全挂——隔离副本里把 `rm *` 改成 allow 后第二轮全过(生产配置不动);② openGauss 每个用户
  有同名 schema,用 omm 不带前缀 `DROP TABLE kb_docs` 会静默跳过,复位后 08 步仍看到旧数据——改成 `gaussdb.kb_docs` 后重跑 08 才干净。
