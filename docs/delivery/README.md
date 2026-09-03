# 交付文档（delivery）

面向**交付与新人上手**的成套文档。五份可按下表顺序阅读:

| # | 文档 | 读者 | 内容 |
|---|---|---|---|
| 01 | [安装部署](01-installation.md) | 部署/运维、首次使用者 | 从空系统到跑通:系统前置、装依赖、(可选)Docker 起测试库、配连接(`GSDB_HOME`/`config.yaml` 逐字段/口令两法/sslmode/driver)、装 skill 到 OpenCode、验证、**全部 skill 命令行参数速查**、排障、升级卸载 |
| 02 | [代码结构详解](02-architecture.md) | 想读懂代码的开发者 | 分层与数据流、`common/` 逐模块(config/credential/db 门面/backends 双后端)、json_agg 类型保真与按类型参数注入、只读/回滚/兜底/hypopg 守卫、skill 解剖、按族介绍原有 10 个 skill、关键设计取舍 |
| 03 | [编码规范](03-coding-standards.md) | 往项目加代码的人 | 不可变数据、多小文件、命名与函数、错误处理、输入校验、安全(密码走 env、只读会话、参数化)、依赖约束、测试(TDD/pytest/`-m "not live"`)、Backend 接口契约、SKILL.md 约定、Git 提交规范、提交前检查清单 |
| 04 | [参与开发指南](04-contributing.md) | 从 0 参与项目的贡献者 | 搭开发环境、搭本地测试库、项目速览;**演示①新增 skill**(完整模板)、**演示②给 skill 加函数**(TDD)、**演示③加 references 文档**;提交评审流程、常见坑 |
| 05 | [新增三个 skill](05-new-skills.md) | 使用者 + 开发者 | **gaussdb-sqlreview**(SQL 规范审查:三输入源、规则模型、自研 lexer)、**gaussdb-memanalyze**(内存冲高六层下钻:运行时视图探测、盲区自陈、前置 GUC)、**gaussdb-kb**(知识库导入:五子命令、目录结构、条款 schema);**知识库落点与优先级链**、测试 |

## 快速导航

- **我要把它装起来用** → 01
- **我要看懂它怎么实现的** → 02
- **我要往里面加代码** → 先 04(搭环境 + 演示),规则查 03,原理查 02
- **我要用/改 gaussdb-sqlreview、gaussdb-memanalyze、gaussdb-kb** → 05
- **我要导入客户的规范文档** → 05 第 4 节(gaussdb-kb)+ 第 5 节(知识库落点与优先级链)
- **我要把知识库(向量库 + 图库)装起来** → 10(安装手册),原理与日常使用 → 09
- **连接/驱动细节**(gsql/pg8000、`GSDB_HOME`、已知差异) → 见 [../connection-drivers.md](../connection-drivers.md)

> 说明:连接配置目录由环境变量 `GSDB_HOME` 指定(任意名/路径,默认 `$GSDB_HOME`,;双后端 gsql(默认)/ pg8000,连接级自动兜底。gsql 是 openGauss 的 Linux 客户端,macOS 上会自动回退 pg8000。gsql/pg8000 的类型 parity 差异标注为"待验证"(未在 Linux+gsql 真库实证)。

补充：

| 文档 | 内容 |
|---|---|
| [06-白名单清单](whitelist.md) | 89 条脚本的 id/参数/SQL 全文 |
| [07-调用链路](07-调用链路.md) | 逻辑名 → id → 执行的全过程，附真实报文 |
| [09-客户知识库](09-客户知识库.md) | **gaussdb-kb**(取代 kbimport):规范 + 工单 → 高斯/PG 向量库 + Neo4j 图库,各诊断 skill 按发现查库、优先引用客户先例;选择列表闸门、三态验收、降级表 |
| [10-知识库安装手册](10-知识库安装手册.md) | **从零装起 gaussdb-kb 及两个后台库**:skill 安装与 `<kb>` 目录、openGauss 7 DataVec / PG+pgvector 二选一(用户、认证、远程访问、vector 探测)、Neo4j 5 社区版(≥5.19 Query API v2)、embedding 端点、凭据与 `kb.yaml` 逐字段、setup/index/health 验证、降级表、排障表、备份重建卸载、本机测试容器、安装检查单 |
