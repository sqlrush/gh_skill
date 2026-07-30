# 在 OpenCode 上装载这些 skill —— 操作手册

> **更详细的从零安装部署版**(含系统前置、Docker 起测试库、逐字段配置、全 skill 参数速查)见
> [docs/delivery/01-installation.md](delivery/01-installation.md)。本手册是精简操作版。

本手册说明如何把 `opencode_skill` 的 10 个技能（gaussdb-slowsql / gaussdb-topsql / gaussdb-sqlfetch /
gaussdb-explain / gaussdb-sqltune / gaussdb-proctune / gaussdb-procinfo / gaussdb-topproc / gaussdb-health / gaussdb-wdr）装到
[OpenCode](https://opencode.ai) 并跑通。

OpenCode 原生支持 Agent Skills：它会从下列目录发现 `SKILL.md`——

- **全局**：`~/.config/opencode/skills/<name>/SKILL.md`
- **项目级**：`<repo>/.opencode/skills/<name>/SKILL.md`（也认 `.claude/skills/`、`.agents/skills/`）

> 注意：OpenCode 没有 `{baseDir}` 占位符。本仓库的 `SKILL.md` 用 `{baseDir}` 指代自身目录，
> 安装脚本会在拷贝时把它替换成真实绝对路径，所以**必须用安装脚本（或手动替换）**，不能直接软链。

---

## 0. 前置条件

| 依赖 | 说明 |
|---|---|
| Python ≥ 3.9 | 运行各 skill 的脚本 |
| `pg8000` / `cryptography` / `PyYAML` | 纯 Python，无需编译 |
| OpenGauss/GaussDB 连接配置目录 | 位置由环境变量 `GSDB_HOME` 指定；存连接元数据 + 凭据 |

安装 Python 依赖：

```bash
python3 -m pip install -r requirements.txt
# 或： python3 -m pip install pg8000 cryptography PyYAML
```

---

## 1. 准备数据库连接（目录由 `GSDB_HOME` 指定）

所有 skill 通过 `-c <name>` 选连接。连接配置放在一个**本地目录**里，目录位置由
环境变量 **`GSDB_HOME`** 指定——名字/路径随你定。目录内容：

- `$GSDB_HOME/config.yaml` —— 连接元数据（host/port/db/user/type，**无密码**）
- 口令二选一：
  - **环境变量**（简单，免加密库）：`export GSDB_PASSWORD='...'`（旧 `GDAA_PASSWORD` 仍兼容），适合本机 / CI；
  - **加密落盘**：`$GSDB_HOME/credentials/<name>.enc`（AES-256-GCM，本机密钥 `$GSDB_HOME/key`）。

**情况 A：已有连接目录**（如旧的 `$GSDB_HOME`）—— 直接复用。不在默认位置就先指过去：

```bash
export GSDB_HOME=~/.my-db-conns     # 你的目录
cat "$GSDB_HOME/config.yaml"        # 看 name 列表（不含密码）
```

**情况 B：手工新建**

```bash
export GSDB_HOME=~/.my-db-conns     # 任意名字/路径
mkdir -p "$GSDB_HOME" && chmod 700 "$GSDB_HOME"
cat >> "$GSDB_HOME/config.yaml" <<'YAML'
connections:
  - name: og-prod
    type: opengauss        # opengauss | gaussdb
    host: 10.0.0.1
    port: 5432
    database: appdb
    user: tuner
    driver: gsql           # gsql(默认) | pg8000；可省，见 connection-drivers.md
YAML
chmod 600 "$GSDB_HOME/config.yaml"

# 口令方式一（推荐，免加密库）：环境变量直存
export GSDB_PASSWORD='your-password'

# 口令方式二：加密落盘（写 $GSDB_HOME/credentials/og-prod.enc）
python3 -c "import sys; sys.path.insert(0,'.'); from common import save_secret; save_secret('og-prod', input('password: '))"
```

> 旧的 `GDAA_HOME` / `GDAA_PASSWORD` 仍被兼容：代码先读 `GSDB_*`，读不到再读 `GDAA_*`，存量部署无需改动。

验证连接可用：

```bash
python3 skills/gaussdb-sqltune/scripts/sqltune.py -c og-prod --sql-stdin <<'SQL'
SELECT 1
SQL
```

---

## 2. 安装 skill 到 OpenCode

### 方式一：安装脚本（推荐）

```bash
# 全局安装到 ~/.config/opencode/skills/
./install-opencode.sh

# 只装部分
./install-opencode.sh gaussdb-sqltune gaussdb-slowsql

# 装到某个项目（<repo>/.opencode/skills/）
./install-opencode.sh --project /path/to/your/repo

# 预演，不写盘
./install-opencode.sh --dry-run
```

脚本做三件事：① 检查 Python 依赖；② 把每个 skill 目录拷到目标；③ 连同共享层
`common/` 一起拷过去，并把 `SKILL.md` 里的 `{baseDir}` 替换成真实绝对路径。

安装后目录结构（全局示例）：

```
~/.config/opencode/skills/
├── common/                  # 共享连接层（无 SKILL.md，OpenCode 自动忽略）
├── gaussdb-sqltune/  SKILL.md + scripts/ + references/
├── gaussdb-proctune/ SKILL.md + scripts/ + references/
├── gaussdb-slowsql/  gaussdb-topsql/  gaussdb-sqlfetch/  gaussdb-explain/  ...
```

### 方式二：手动安装

```bash
DEST=~/.config/opencode/skills
mkdir -p "$DEST"
cp -R common "$DEST/common"
cp -R skills/gaussdb-sqltune "$DEST/gaussdb-sqltune"          # 对每个要装的 skill 重复
# 把 SKILL.md 里的 {baseDir} 换成该 skill 的绝对路径：
python3 - "$DEST/gaussdb-sqltune" <<'PY'
import pathlib,sys; b=pathlib.Path(sys.argv[1]); f=b/"SKILL.md"; f.write_text(f.read_text().replace("{baseDir}",str(b)))
PY
```

---

## 3. 验证 OpenCode 已发现 skill

启动 OpenCode，技能会通过原生 `skill` 工具暴露。模型可调用
`skill({ name: "gaussdb-sqltune" })` 等。让 agent「列出可用 skill」即可确认 6 个都在。

命令行快速自检（不经 OpenCode，直接验证脚本可独立运行）：

```bash
python3 ~/.config/opencode/skills/gaussdb-sqltune/scripts/sqltune.py -h
python3 ~/.config/opencode/skills/gaussdb-slowsql/scripts/slowsql.py -c og-prod --threshold 1000
```

---

## 4. 各 skill 用途与触发

| skill               | 何时用 | 入口脚本 |
|---------------------|---|---|
| **gaussdb-slowsql** | 「哪些 SQL 慢」 | `slowsql.py -c <conn> --threshold <ms>` |
| **gaussdb-topsql**    | 「最耗资源的 SQL 排名」 | `topsql.py -c <conn> --by time\|avg\|calls\|reads\|rows` |
| **gaussdb-sqlfetch**  | 「把 sql_id 还原成 SQL 文本」 | `sqlfetch.py -c <conn> <unique_sql_id>` |
| **gaussdb-explain**   | 「看这条 SQL 的执行计划+风险」 | `explain.py -c <conn> --sql-stdin` |
| **gaussdb-sqltune**   | 「深度调优这条 SQL」（hypopg 验证索引、verify 验改写） | `sqltune.py -c <conn> <id\|--sql-stdin>`、`verify.py ...` |
| **gaussdb-proctune**  | 「调优存储过程」（结构分析 + 游标 SELECT 调优） | `proctune.py collect\|tune-cursor -c <conn> <schema.proc>` |

用户用自然语言提问（「库里哪些 SQL 慢」「帮我调这条 SQL」「看下这个存储过程」），
OpenCode 会按 `SKILL.md` 的 description 选中对应 skill 并按其工作流执行。

---

## 5. 安全模型

- 脚本以**只读会话**连库（写/DDL 被会话级 `READ ONLY` 拦截；存储过程不被执行）。
- 凭据由脚本就地解密，**不会**在对话中回显；`SKILL.md` 明确禁止 agent 自行连库或读取
  `$GSDB_HOME/credentials/`——取数只走这些脚本。
- `gaussdb-sqltune`/`gaussdb-proctune` 的索引与改写建议都经 hypopg + cost（+ 等价性）硬验证后才呈现。

---

## 6. 故障排查

| 现象 | 处理 |
|---|---|
| `ModuleNotFoundError: No module named 'pg8000'` | `python3 -m pip install -r requirements.txt` |
| `ModuleNotFoundError: No module named 'common'` | 没用安装脚本（漏拷 `common/`）。重跑 `install-opencode.sh` |
| SKILL.md 里出现字面量 `{baseDir}` | 没做替换。用安装脚本，或手动跑第 2 步的替换片段 |
| `no connection named 'xxx'` | `$GSDB_HOME/config.yaml` 里没有该 name；见第 1 步（确认 `GSDB_HOME` 指对目录） |
| `decrypt credential ...` 失败 | `$GSDB_HOME/key` 与 `.enc` 不匹配（换机要一起带过来），或改用 `GSDB_PASSWORD` |
| `connect ... Connection reset` | 目标可能是只读备库，连主库；或检查 host/port/防火墙 |
| 慢SQL/gaussdb-topsql 为空 | 实例未开 `enable_stmt_track`，或降低 `--threshold` |

---

## 7. 升级 / 卸载

```bash
# 升级：仓库 git pull 后重跑安装脚本（覆盖式）
./install-opencode.sh

# 卸载：删掉对应目录
rm -rf ~/.config/opencode/skills/{gaussdb-sqltune,gaussdb-proctune,gaussdb-slowsql,gaussdb-topsql,gaussdb-sqlfetch,gaussdb-explain,common}
```
