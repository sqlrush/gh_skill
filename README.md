# opencode_skill

OpenGauss / GaussDB 数据库 DBA 技能集,由 Go 版 `$GSDB_HOME` 工具用 Python 重写。

目录结构(按既定重构方案):

```
common/            # 唯一共享层:连接 / 凭据 / 只读驱动
skills/<name>/
  SKILL.md         # 面向模型的操作手册(调用本 skill 的 python 脚本)
  references/      # 方法论 + GaussDB 知识库
  scripts/         # 本 skill 自己的逻辑(入口 + vendored 探针)
tests/             # pytest(连接不存在时 live 测试自动跳过)
```

设计原则:`common/` 是唯一共享包(只负责连库 + 解密凭据,复用 GSDB_HOME 的 `$GSDB_HOME` 存储、原样不动)。其余一切——探针、报告渲染、分析——都放在各 skill 的 `scripts/` 里。

## 安装

```bash
python3 -m pip install -r requirements.txt
```

## 装进 OpenCode

```bash
./install-opencode.sh          # → ~/.config/opencode/skills/
```

完整步骤(前置依赖、建连接、验证、排障)见 [docs/INSTALL-opencode.md](docs/INSTALL-opencode.md);**成套交付/上手文档**(安装部署、代码结构、编码规范、参与开发)见 [docs/delivery/](docs/delivery/README.md)。

连接配置放在一个本地目录里,位置由环境变量 `GSDB_HOME` 指定(任意名/路径,默认 `$GSDB_HOME`,`$GSDB_HOME/config.yaml` + `$GSDB_HOME/credentials/`(和 Go 版 `$GSDB_HOME` 共用同一份存储)。`GSDB_PASSWORD`(旧 `GDAA_PASSWORD` 仍兼容)可临时覆盖存储的密码(一次性 / CI 用)。支持 gsql（默认）与 pg8000 双后端，连接级自动兜底；详见 [docs/connection-drivers.md](docs/connection-drivers.md)。

## 范围(当前)

已与 Go 版 `$GSDB_HOME` 技能集**全量对齐**(3 个族、10 个 skill),并在其之上新增 3 个 skill —— 共 **13 个**。
新增的三个(gaussdb-sqlreview / gaussdb-memanalyze / gaussdb-kbimport)有专门的结构与使用文档:[docs/delivery/05-new-skills.md](docs/delivery/05-new-skills.md)。

SQL 优化族:

- `skills/gaussdb-slowsql`  —— 按平均耗时阈值找慢 SQL
- `skills/gaussdb-topsql`   —— 按资源消耗排名最重的 SQL
- `skills/gaussdb-sqlfetch` —— 把 unique_sql_id 还原成完整 SQL 文本
- `skills/gaussdb-explain`  —— 执行计划 + 确定性风险发现
- `skills/gaussdb-sqltune`  —— SQL 深度调优(hypopg + 成本 + 等价性验证)

存储过程族:

- `skills/gaussdb-proctune` —— 存储过程分析 + 只读游标 SELECT 调优
- `skills/gaussdb-procinfo` —— 存储过程只读结构诊断(交棒 proctune)
- `skills/gaussdb-topproc`  —— 按资源消耗排名最重的存储过程(pg_stat_user_functions)

诊断族:

- `skills/gaussdb-health`   —— 12 维只读健康检查 + 确定性发现
- `skills/gaussdb-wdr`      —— WDR 快照 delta 解读(7 维,snaps/collect/render)
- `skills/gaussdb-memanalyze` —— 动态内存冲高六层下钻:L1 实例级(冲的是动态/共享/other 内存)→ L2 内存上下文(泄漏还是真用量)→ L3 会话级(用在哪些会话)→ L4 SQL 级(哪条 SQL、估算偏差、下盘量)→ L5 算子级(哪个算子吃的内存:plan_node_id/算子名/峰值/下盘/倾斜)→ L6 配置面(work_mem×并发是否本身超上限)。视图**运行时探测**,openGauss 与 GaussDB 通用;`resource_track_level` 等 GUC 未开时明确报出 GUC 名与目标值,不静默出空表。三子命令 `snapshot`/`history`/`watch`(watch 由脚本判定泄漏/尖峰/平稳)

规范治理族(Go 版 `$GSDB_HOME` 无对应实现):

- `skills/gaussdb-kbimport` —— 规范知识库导入与治理。把客户的规范文档(txt/md/docx/doc)条款化进 **与 skill 装在一起的知识库**(`<安装根>/kb/`,如全局安装即 `~/.config/opencode/kb/`;errata 修正 + rules 确定性条款 yaml + guides 语义指南 md),自动重建 INDEX、校验规则 ID 与 schema、关键词检索,并把知识库契约段幂等注入**做规范/阈值判断的 skill**(gaussdb-sqlreview/gaussdb-health/gaussdb-wdr/gaussdb-memanalyze/gaussdb-sqltune/gaussdb-proctune;纯取数的 gaussdb-slowsql/gaussdb-topsql/gaussdb-sqlfetch/gaussdb-explain/gaussdb-topproc/gaussdb-procinfo 不注入)。治理边界:**skill 自身 SKILL.md 与脚本的确定性判定 > 知识库 > 模型自带知识** —— 知识库管「规范条款说了什么」,不推翻 skill 的判定逻辑;两边不一致时并列呈现、交用户裁决。
- `skills/gaussdb-sqlreview` —— SQL 规范审查。规范写在 [`skills/gaussdb-sqlreview/references/rules.yaml`](skills/gaussdb-sqlreview/references/rules.yaml),用户可自由编辑:确定性规则(表必须有主键、禁外键、表/索引/列命名、禁物理删除、禁前置模糊匹配、索引列数上限、索引冗余等)由脚本判定;表达不了的语义规则(`check: advisory`)由脚本取证后交模型判断。三个输入源:SQL 文本(`--file`/`--stdin`)、线上 SQL(`--sql-id`/`--top`)、库中存量表与索引(`--schema`)。无 SQL parser 依赖,自研轻量 lexer 保证注释与字符串字面量不误报。

前 10 个 skill 的输出都对照 Go 版 `$GSDB_HOME` 二进制做了交叉验证(gaussdb-sqlreview / gaussdb-memanalyze / gaussdb-kbimport 为本项目新增,无 Go 版对应实现)。gaussdb-health 与 gaussdb-wdr 做了逐字节 diff:维度、表头、阈值串、确定性发现完全一致(wdr 因快照不可变,证据数值完全相同;`wdr render` 除脚注里有意去掉「$GSDB_HOME」一词外完全一致)。gaussdb-slowsql/gaussdb-topsql/gaussdb-sqlfetch 仅在末尾的 "Next:" 提示行不同——指向本地 Python 脚本而非 `$GSDB_HOME`。

驱动:gsql（默认）+ pg8000 双后端，连接级自动兜底（gsql 不可用时自动降为 pg8000）。pg8000 已对 openGauss-lite 5.0.3 的 `opengauss` 与 `gaussdb` 两种连接类型实证；gsql parity 待在 Linux 主机验证，见 [docs/connection-drivers.md](docs/connection-drivers.md)。
