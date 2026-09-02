---
name: gaussdb-slowsql
version: 2.0.0
description: "通过内置脚本发现 OpenGauss/GaussDB 中超过指定耗时阈值的慢 SQL。用户要按阈值筛出慢语句、找出超过某个耗时门槛的 SQL、查看高平均耗时且3已超阈值的语句，或先拿一批可调优的 SQL 候选时使用，包括“查超过 1 秒的 SQL”“找出超阈值慢 SQL”“哪些 SQL 平均耗时超过 500ms”“给我超 1 秒的 SQL 列表”等请求。触发后运行 scripts/slowsql.py，输出真实的超阈值慢 SQL 结果，不要只解释慢 SQL 的概念。"
allowed-tools: ["exec", "read"]
compatibility: opencode
metadata:
  runtime: python3
  emoji: "🐢"
  family: sql-optimization
---

# 慢 SQL（OpenGauss/GaussDB）

命中以下请求时，必须使用本 skill 并实际执行脚本，不要只做概念解释：

- 用户要按耗时阈值查慢 SQL 或慢 SQL 列表
- 用户要找超过某个耗时阈值的 SQL
- 用户要先拿一批候选 SQL 再继续 explain 或 tune

典型触发语句：

- 查超过 1 秒的慢 SQL
- 哪些 SQL 很慢
- 找超过 1 秒的 SQL
- 给我超阈值慢 SQL 列表
- 先看一下最慢的 SQL

## 工作流

1. **选择连接 —— 先登录，不要自己猜连接名。** 取数前确认已登录：
   `python3 {baseDir}/../gaussdb-login/scripts/login.py --status`。
   没有会话就先调 **gaussdb-login**：它读 `$GSDB_HOME/config.yaml`的首行 `connection_mode`，是 `gsql` 就把可选连接列成菜单让用户挑，是 `api` 就引导用户给出要访问的数据库。
   登录之后本 skill **不需要传 `-c`** —— 省略时自动用登录选定的那条连接；只有要临时换一个库时才显式传 `-c <连接名>`。
   **不要自己去读 config.yaml 挑名字**：不同应用下可能有同名连接，猜错会在另一个库上做诊断，而输出看起来完全正常。口令在 `{baseDir}/../common/credentials/*.enc`，由脚本解密，**你不要去读/解密它**。

2. 运行（按需调 `--threshold` 平均 ms），提醒用户慢sql的查找时间范围为近一周：

   ```bash
   python3 {baseDir}/scripts/slowsql.py -c <conn> --threshold 1000 --limit 20
   ```

3. 查找慢sql的结果如果超过设置的阈值`SLOWSQL_MAX_ROWS`，询问用户是否导出。或者输入`export=true`, 两者触发文件下载功能
   
4. 总结最严重的语句（调用次数 × 平均耗时 = 影响）。可建议：
   
   - `python3 {baseDir}/../gaussdb-sqlfetch/scripts/sqlfetch.py -c <conn> <SQL_ID>` 取完整 SQL 文本；
   - 对头部语句走 gaussdb-sqltune 工作流。
   `--format json` 输出含 `cpu_sec`：平均慢但 CPU≈0 往往是锁/等待（contention），不要盲目加索引。
   
5. 结果为空 → 检查 `enable_stmt_track`，或降低阈值。

## 文件下载功能

   支持将服务端生成慢查询结果文件下载至您本地PC客户端，方便离线分析或归档。

- **触发条件**: 结果行数如果超过设置的阈值`SLOWSQL_MAX_ROWS`，系统自动生成 CSV 格式的慢查询结果文件
- **下载流程（需您明确同意）**: 统生成报告后，您的客户端将弹出一个授权对话框。
- **对话框中会清晰展示**: 文件名称、文件大小、默认保存位置：您的桌面（可在对话框中修改）
- **您点击“确认下载”后，** 文件将通过加密通道传输至您的本地电脑
- **下载完成后，** 服务端临时文件将自动清除。

**安全承诺**：整个传输过程使用 TLS 加密，服务器不保留您的本地路径信息。若您在 2 分钟内未确认，系统将自动取消本次下载，您可随时重新发起。

#### 如何操作

- **触发下载**：在对话框中执行 `/gauss-slowsql export=true`，或点击界面“导出”按钮。
- **取消下载**：直接关闭授权弹窗即可，不会产生任何文件残留
