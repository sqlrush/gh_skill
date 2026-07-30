# opencode_skill 安装部署文档

本文档面向熟悉 Python 和 git、但未接触过本项目或 openGauss 的新人，覆盖从零到跑通的全流程。

---

## 1. 概述与前置条件

### 1.1 项目简介

opencode_skill 是一套可在 [OpenCode](https://opencode.ai) 中使用的数据库诊断与调优技能包，面向 OpenGauss/GaussDB。共 10 个技能，分两类：

**SQL 优化族**

| 技能 | 用途 |
|---|---|
| `gaussdb-slowsql` | 按平均耗时阈值发现慢 SQL，暴露 CPU≈0 的锁/等待类慢查询 |
| `gaussdb-topsql` | 无阈值、按总耗时/平均/调用数/逻辑读/行数排名最耗资源的 SQL |
| `gaussdb-sqlfetch` | 把 `unique_sql_id` 还原为完整 SQL 文本，标注截断与占位符 |
| `gaussdb-explain` | 获取真实数据库的执行计划并给出确定性风险标注 |
| `gaussdb-sqltune` | 深度调优：一条命令出完整证据包，自动用 hypopg 验证索引候选，支持改写验证 |

**存储过程族 + 诊断族**

| 技能 | 用途 |
|---|---|
| `gaussdb-topproc` | 按总耗时/自身耗时/调用数排名最慢的存储过程/函数 |
| `gaussdb-procinfo` | 只读诊断存储过程结构：循环内 SQL、逐行 DML、动态 SQL 等反模式 |
| `gaussdb-proctune` | 存储过程深度调优：对只读游标 SELECT 做 hypopg 验证的索引/改写优化 |
| `gaussdb-health` | 12 维数据库健康检查（等待事件/慢SQL/长事务/死元组/锁/连接/复制等），按阈值产确定性发现 |
| `gaussdb-wdr` | WDR 报告解读：列快照、计算多维 delta、产确定性发现并驱动 gaussdb-sqltune 实证 |

所有技能共享同一套连接层（`common/`），与已有的 $GSDB_HOME CLI 连接配置字节兼容。

---

### 1.2 Python 要求

需要 Python **3.9 或以上版本**。

**macOS（推荐用 Homebrew）**

```bash
brew install python@3.12
python3 --version  # 确认 ≥ 3.9
```

**Linux（Debian/Ubuntu）**

```bash
sudo apt-get update && sudo apt-get install -y python3 python3-pip
python3 --version
```

**Linux（RHEL/CentOS/Rocky）**

```bash
sudo dnf install python3 python3-pip   # 或 yum
python3 --version
```

---

### 1.3 OpenCode 安装

请参考 [OpenCode 官方文档](https://opencode.ai) 完成安装。OpenCode 安装后，技能通过原生 `skill` 工具暴露，无需额外插件。

---

### 1.4 可选：Docker

如果本机没有 OpenCode 或只想快速试用，可用 Docker 单独运行某个技能脚本：

```bash
docker run --rm -it \
  -v "$GSDB_HOME:/root/.my-gsdb-home" \
  -e GSDB_PASSWORD="$GSDB_PASSWORD" \
  python:3.12-slim bash
# 容器内：pip install pg8000 cryptography PyYAML && python3 skills/gaussdb-slowsql/scripts/slowsql.py -h
```

---

## 2. 取代码 + 装依赖

```bash
git clone https://github.com/your-org/opencode_skill.git
cd opencode_skill
```

安装 Python 依赖（纯 Python，无需编译）：

```bash
python3 -m pip install -r requirements.txt
```

`requirements.txt` 包含：

| 包 | 版本要求 | 用途 |
|---|---|---|
| `pg8000` | ≥ 1.30 | PostgreSQL 协议驱动，用于连接 openGauss/GaussDB |
| `cryptography` | ≥ 41 | AES-256-GCM 凭据解密 |
| `PyYAML` | ≥ 6 | 读取 `$GSDB_HOME/config.yaml` |

**离线 / 无网络环境**

在有网络的机器上先下载 wheel 包，再拷到目标机安装：

```bash
# 有网络的机器上（架构需一致）：
pip download -r requirements.txt -d ./wheels/

# 目标机（无网络）：
python3 -m pip install --no-index --find-links ./wheels/ -r requirements.txt
```

---

## 3. （可选）用 Docker 起一个 openGauss 测试库

如果没有现成的 openGauss 实例，可以用官方 Docker 镜像快速启动一个用于测试：

```bash
docker run -d \
  --name og-test \
  -e GS_PASSWORD='Test@1234' \
  -p 5432:5432 \
  enmotech/opengauss:latest
```

等待约 30 秒数据库初始化完成，然后验证连通性：

```bash
# 用 pg8000 测试（macOS 上无 gsql 二进制可用此法）
python3 - <<'PY'
import pg8000.native
conn = pg8000.native.Connection(
    user="gaussdb", password="Test@1234",
    host="127.0.0.1", port=5432, database="postgres")
print(conn.run("SELECT version()"))
conn.close()
PY
```

测试库信息：

| 项目 | 值 |
|---|---|
| 主机 | `127.0.0.1` |
| 端口 | `5432` |
| 用户 | `gaussdb` |
| 密码 | `Test@1234`（示例，请自行设置强密码） |
| 数据库 | `postgres` |

---

## 4. 配置数据库连接

### 4.1 连接配置目录（`$GSDB_HOME`）

所有技能通过 `-c <name>` 参数选择连接。连接配置存放在一个本地目录中，路径由环境变量 **`GSDB_HOME`** 指定（任意名/任意路径均可）：

- 未设置时默认使用 `$GSDB_HOME`
- 旧的 `GDAA_HOME` 环境变量仍被兼容（优先级：`GSDB_HOME` > `GDAA_HOME` ）

```bash
# 方式一：使用默认目录 ~/.my-gsdb-home
mkdir -p ~/.my-gsdb-home && chmod 700 ~/.my-gsdb-home

# 方式二：指定自定义目录（永久生效需加入 shell rc 文件）
export GSDB_HOME=~/.my-db-conns
mkdir -p "$GSDB_HOME" && chmod 700 "$GSDB_HOME"
```

目录内容结构：

```
$GSDB_HOME/
├── config.yaml          # 连接元数据（无密码）
├── key                  # AES-256 加密密钥（首次写入时自动生成，权限 0600）
└── credentials/
    └── <name>.enc       # 每个连接的加密密码（权限 0600）
```

---

### 4.2 config.yaml 逐字段详解

在 `$GSDB_HOME/config.yaml` 中写入连接定义：

```bash
cat > "$GSDB_HOME/config.yaml" <<'YAML'
connections:
  - name: og-prod
    type: opengauss
    host: 10.0.0.1
    port: 5432
    database: appdb
    user: tuner
    sslmode: prefer
    driver: gsql
YAML
chmod 600 "$GSDB_HOME/config.yaml"
```

各字段说明：

| 字段 | 必填 | 说明 |
|---|---|---|
| `name` | 是 | 连接名，在脚本 `-c` 参数中使用；只能包含小写字母、数字、`_`、`-`，且以字母或数字开头 |
| `type` | 是 | 数据库类型，取值：`opengauss` 或 `gaussdb` |
| `host` | 是 | 数据库主机名或 IP 地址 |
| `port` | 是 | 端口号（1~65535） |
| `database` | 是 | 数据库名称 |
| `user` | 是 | 连接用户名 |
| `sslmode` | 否 | SSL 模式，留空则由驱动默认处理；见下文 |
| `driver` | 否 | 连接驱动，默认 `gsql`；见下文 |

**sslmode 取值范围**

| 值 | 含义 |
|---|---|
| `disable` | 不使用 SSL |
| `allow` | 优先非 SSL，服务端要求时才用 SSL |
| `prefer` | 优先 SSL，失败后降为非 SSL |
| `require` | 强制 SSL，不验证证书 |
| `verify-ca` | 强制 SSL，验证 CA 证书 |
| `verify-full` | 强制 SSL，验证 CA 证书及主机名 |

也可通过环境变量 `PGSSLMODE` 覆盖（config.yaml 中的 `sslmode` 优先级更高）。

**driver 字段**

| 值 | 说明 | 适用场景 |
|---|---|---|
| `gsql`（默认） | 调用本机 `gsql` 命令行客户端，每条查询起一个子进程 | Linux 生产主机（已安装 openGauss 客户端） |
| `pg8000` | 纯 Python TCP 驱动，无需安装任何二进制 | macOS 开发机、CI/容器、或需要 hypopg 验证 |

> 注意：`gsql` 是 openGauss 的 Linux 客户端，**macOS 上没有原生版本**。在 macOS 上配置 `driver: gsql` 时，连接层会检测到 gsql 二进制不存在，自动回退到 pg8000（透明，调用方无感知）。如想明确指定跳过 gsql 尝试，直接设 `driver: pg8000`。
>
> 额外注意：`gaussdb-sqltune` 和 `gaussdb-proctune` 的 hypopg 索引验证依赖持久 TCP 连接（会话级虚拟索引），**gsql 后端无持久会话，验证步骤会明确报错**并提示改用 pg8000。建议需要 hypopg 验证的连接显式设置 `driver: pg8000`。

可通过环境变量 `GDAA_GSQL` 覆盖 gsql 二进制路径，例如：`export GDAA_GSQL=/usr/local/bin/gsql`。

---

### 4.3 配置密码

#### 方式一：环境变量（推荐用于开发机 / CI）

```bash
export GSDB_PASSWORD='your-db-password'
```

优点：无需加密库，简单直接。旧变量 `GDAA_PASSWORD` 仍被兼容。每次执行脚本前需确保该变量已设置（或写入 shell rc 文件）。

#### 方式二：加密落盘（推荐用于生产 / 多连接场景）

密码以 AES-256-GCM 加密存储在 `$GSDB_HOME/credentials/<name>.enc`，加密密钥存放于 `$GSDB_HOME/key`（首次写入时自动生成，权限 0600）。

```bash
cd /path/to/opencode_skill   # 确保 common/ 在路径上
python3 -c "
import sys
sys.path.insert(0, '.')
from common.credential import save_secret
save_secret('og-prod', input('password: '))
"
```

输入密码后，加密文件会写入 `$GSDB_HOME/credentials/og-prod.enc`。

**安全权衡对比**

| | 环境变量 | 加密落盘 |
|---|---|---|
| 适合场景 | CI、本机单连接、快速测试 | 生产机、多连接管理 |
| 密码出现在 | 进程环境变量（`ps` 或 `/proc` 可见） | 加密文件，不出现在内存/日志 |
| 依赖 | 无 | `cryptography` 包 + `$GSDB_HOME/key` |
| 跨机迁移 | 直接重设 | 需同时迁移 `key` 文件（否则无法解密） |

两种方式优先级：**`GSDB_PASSWORD` 环境变量 > 加密落盘文件**。设置了环境变量后，加密落盘的凭据不会被读取。

---

### 4.4 验证连接配置

```bash
python3 skills/gaussdb-sqlfetch/scripts/sqlfetch.py -c og-prod 1
# 若报 "sql id 1 not found in ..." 则说明连接成功（只是该 id 不存在）
# 若报 "no connection named 'og-prod'" 则配置文件未正确读取
```

---

## 5. 安装 skill 到 OpenCode

### 5.1 安装脚本（推荐）

```bash
# 全局安装到 ~/.config/opencode/skills/（所有项目可用）
./install-opencode.sh

# 只装部分技能
./install-opencode.sh gaussdb-sqltune gaussdb-slowsql

# 装到某个项目目录（<repo>/.opencode/skills/）
./install-opencode.sh --project /path/to/your/repo

# 安装到自定义目录
./install-opencode.sh --dest /custom/path/skills

# 预演，不写盘（确认会发生什么）
./install-opencode.sh --dry-run

# 组合用法：预演特定技能装到项目
./install-opencode.sh --project /path/to/repo --dry-run gaussdb-sqltune
```

安装脚本做三件事：
1. 检查 Python 及必要模块（`pg8000`、`cryptography`、`yaml`）
2. 把 `common/` 共享连接层拷到目标目录
3. 把每个技能目录拷到目标，并把 `SKILL.md` 中的 `{baseDir}` 占位符替换为该技能的真实绝对路径

---

### 5.2 手动安装

如果不想使用安装脚本，可以手动操作：

```bash
DEST=~/.config/opencode/skills
mkdir -p "$DEST"

# 1. 拷贝共享连接层
cp -R common "$DEST/common"

# 2. 拷贝技能目录（以 sqltune 为例，对每个需要的技能重复）
cp -R skills/gaussdb-sqltune "$DEST/gaussdb-sqltune"

# 3. 替换 SKILL.md 中的 {baseDir} 占位符
python3 - "$DEST/gaussdb-sqltune" <<'PY'
import pathlib, sys
b = pathlib.Path(sys.argv[1])
f = b / "SKILL.md"
f.write_text(f.read_text().replace("{baseDir}", str(b)))
PY
```

---

### 5.3 `{baseDir}` 替换机制说明

各 `SKILL.md` 中用 `{baseDir}` 指代技能自身的安装目录（例如 `~/.config/opencode/skills/gaussdb-sqltune`）。OpenCode 原生不支持该占位符，因此安装时必须将其替换为实际的绝对路径。

这也意味着**不能用软链接代替安装**——软链接目标路径会变，SKILL.md 中的路径就会失效。安装脚本或手动替换都会修改目标位置的副本，源码树保持不变。

---

### 5.4 安装后目录结构

全局安装后（`~/.config/opencode/skills/`）：

```
~/.config/opencode/skills/
├── common/                   # 共享连接层（无 SKILL.md，OpenCode 自动忽略为技能）
│   ├── __init__.py
│   ├── config.py
│   ├── credential.py
│   └── ...
├── gaussdb-slowsql/
│   ├── SKILL.md              # {baseDir} 已替换为本目录绝对路径
│   └── scripts/
│       ├── slowsql.py
│       └── render.py
├── gaussdb-topsql/
│   ├── SKILL.md
│   └── scripts/
├── gaussdb-sqlfetch/  gaussdb-explain/  gaussdb-sqltune/  gaussdb-proctune/  gaussdb-procinfo/  gaussdb-topproc/  gaussdb-health/  gaussdb-wdr/
│   └── ...（同上结构）
```

---

## 6. 验证

### 6.1 命令行自检（不经 OpenCode）

安装完成后，直接用 Python 调用脚本验证可独立运行：

```bash
SKILLS=~/.config/opencode/skills

python3 $SKILLS/gaussdb-slowsql/scripts/slowsql.py -h
python3 $SKILLS/gaussdb-topsql/scripts/topsql.py -h
python3 $SKILLS/gaussdb-sqlfetch/scripts/sqlfetch.py -h
python3 $SKILLS/gaussdb-explain/scripts/explain.py -h
python3 $SKILLS/gaussdb-sqltune/scripts/sqltune.py -h
python3 $SKILLS/gaussdb-proctune/scripts/proctune.py -h
python3 $SKILLS/gaussdb-procinfo/scripts/procinfo.py -h
python3 $SKILLS/gaussdb-topproc/scripts/topproc.py -h
python3 $SKILLS/gaussdb-health/scripts/health.py -h
python3 $SKILLS/gaussdb-wdr/scripts/wdr.py -h
```

每个命令应输出帮助文本，不应报 `ModuleNotFoundError`。

快速连通测试（需要已配置连接 `og-prod`）：

```bash
python3 $SKILLS/gaussdb-slowsql/scripts/slowsql.py -c og-prod --threshold 1000 --limit 5
```

### 6.2 OpenCode 内验证

启动 OpenCode，技能会通过原生 `skill` 工具自动暴露。可在对话中让 agent 列出可用技能：

```
列出当前可用的所有 skill
```

OpenCode 会调用 `skill({ name: "gaussdb-health" })` 等方式使用技能。

---

## 7. 各 skill 完整命令行参数速查

以下参数直接来自各脚本的 `argparse` 定义，与实际代码保持一致。

---

### 7.1 gaussdb-slowsql

**脚本：** `scripts/gaussdb-slowsql.py`

**功能：** 列出平均耗时超过阈值的 SQL，数据源为 `dbe_perf.statement`。

| 参数 | 类型 | 默认值 | 说明 |
|---|---|---|---|
| `-c` / `--conn` | str（必填） | — | 连接名（对应 config.yaml 中的 `name`） |
| `--threshold` | int | `1000` | 平均耗时阈值（毫秒），超过此值才列出 |
| `--limit` | int | `20` | 最多返回的行数 |
| `--format` | `markdown`\|`json` | `markdown` | 输出格式 |
| `--timeout` | int | `30` | 语句超时（秒） |

**示例：**

```bash
# 查找平均耗时超过 2 秒的慢 SQL，最多返回 10 条
python3 $SKILLS/gaussdb-slowsql/scripts/slowsql.py -c og-prod --threshold 2000 --limit 10

# JSON 格式输出（含 cpu_sec 字段，便于判断是否是锁/等待类慢查询）
python3 $SKILLS/gaussdb-slowsql/scripts/slowsql.py -c og-prod --threshold 500 --format json
```

---

### 7.2 gaussdb-topsql

**脚本：** `scripts/topsql.py`

**功能：** 无阈值，按指定维度排名最耗资源的 SQL。

| 参数 | 类型 | 默认值 | 说明 |
|---|---|---|---|
| `-c` / `--conn` | str（必填） | — | 连接名 |
| `--by` | `time`\|`avg`\|`calls`\|`reads`\|`rows` | `time` | 排序键：`time`=总耗时、`avg`=平均耗时、`calls`=调用次数、`reads`=逻辑读、`rows`=返回行数 |
| `--limit` | int | `10` | 最多返回的行数 |
| `--format` | `markdown`\|`json` | `markdown` | 输出格式 |
| `--timeout` | int | `30` | 语句超时（秒） |

**示例：**

```bash
# 按总耗时排 Top 10
python3 $SKILLS/gaussdb-topsql/scripts/topsql.py -c og-prod --by time --limit 10

# 按调用次数排 Top 20
python3 $SKILLS/gaussdb-topsql/scripts/topsql.py -c og-prod --by calls --limit 20

# 按逻辑读排，JSON 输出
python3 $SKILLS/gaussdb-topsql/scripts/topsql.py -c og-prod --by reads --format json
```

---

### 7.3 gaussdb-sqlfetch

**脚本：** `scripts/sqlfetch.py`

**功能：** 通过 `unique_sql_id` 还原完整 SQL 文本，先查 `statement_history`（含真实参数值），再 fallback 到 `statement`（归一化文本）。

| 参数 | 类型 | 默认值 | 说明 |
|---|---|---|---|
| `sql_id`（位置参数，必填） | int（可为负） | — | `unique_sql_id`，通过 gaussdb-slowsql/gaussdb-topsql 取得 |
| `-c` / `--conn` | str（必填） | — | 连接名 |
| `--format` | `markdown`\|`json` | `markdown` | 输出格式 |
| `--timeout` | int | `30` | 语句超时（秒） |

**示例：**

```bash
python3 $SKILLS/gaussdb-sqlfetch/scripts/sqlfetch.py -c og-prod 1234567890
python3 $SKILLS/gaussdb-sqlfetch/scripts/sqlfetch.py -c og-prod -- -9876543210
```

---

### 7.4 gaussdb-explain

**脚本：** `scripts/explain.py`

**功能：** 从真实数据库获取执行计划，标注 Seq Scan、Sort、Nested Loop+Seq Scan 等风险。SQL 始终通过 stdin 传入。

| 参数 | 类型 | 默认值 | 说明 |
|---|---|---|---|
| `-c` / `--conn` | str（必填） | — | 连接名 |
| `--sql-stdin` | flag（必填） | — | 从 stdin 读取 SQL 文本（必须指定此参数） |
| `--analyze` | flag | 关闭 | 使用 `EXPLAIN ANALYZE`（会真实执行 SQL；DML 自动包在回滚事务中）|
| `--format` | `markdown`\|`json` | `markdown` | 输出格式 |
| `--timeout` | int | `30` | 语句超时（秒） |

**示例：**

```bash
# 基本用法（不执行 SQL，只看计划）
python3 $SKILLS/gaussdb-explain/scripts/explain.py -c og-prod --sql-stdin <<'SQL'
SELECT u.name, COUNT(o.id)
FROM users u
LEFT JOIN orders o ON u.id = o.user_id
WHERE u.status = 'active'
GROUP BY u.name
SQL

# EXPLAIN ANALYZE（执行 SQL，DML 自动回滚）
python3 $SKILLS/gaussdb-explain/scripts/explain.py -c og-prod --sql-stdin --analyze <<'SQL'
SELECT * FROM orders WHERE order_date > '2024-01-01'
SQL
```

---

### 7.5 gaussdb-sqltune

**脚本：** `scripts/sqltune.py`（主流程）和 `scripts/verify.py`（改写验证）

#### sqltune.py

**功能：** 一次性出完整证据包（执行计划、表/索引/列统计、GUC），并自动用 hypopg 对索引候选做假设验证。

| 参数 | 类型 | 默认值 | 说明 |
|---|---|---|---|
| `sql_id`（位置参数，可选） | int（可为负） | — | `unique_sql_id`，与 `--sql-stdin` 二选一 |
| `-c` / `--conn` | str（必填） | — | 连接名 |
| `--sql-stdin` | flag | 关闭 | 从 stdin 读取 SQL 文本，与 `sql_id` 二选一 |
| `--bind` | str（可重复） | `[]` | 按占位符顺序提供真实绑定值，例如 `--bind 42 --bind foo` |
| `--analyze` | flag | 关闭 | 使用 `EXPLAIN ANALYZE`（会真实执行 SQL；DML 自动包在回滚事务中） |
| `--format` | `markdown`\|`json` | `markdown` | 输出格式 |
| `--timeout` | int | `30` | 语句超时（秒） |

**示例：**

```bash
# 按 sql_id 调优
python3 $SKILLS/gaussdb-sqltune/scripts/sqltune.py -c og-prod 1234567890

# 按 sql_id + 提供真实绑定值（提高索引验证精度）
python3 $SKILLS/gaussdb-sqltune/scripts/sqltune.py -c og-prod 1234567890 --bind 100 --bind active

# 直接提供 SQL 文本
python3 $SKILLS/gaussdb-sqltune/scripts/sqltune.py -c og-prod --sql-stdin <<'SQL'
SELECT * FROM orders WHERE user_id = $1 AND status = $2
SQL

# JSON 格式输出（程序化使用）
python3 $SKILLS/gaussdb-sqltune/scripts/sqltune.py -c og-prod 1234567890 --format json
```

#### verify.py（改写验证）

**功能：** 对比原始 SQL 与改写 SQL 的 planner cost，可选做结果集等价性校验（md5 采样），支持组合假设索引验证。

| 参数 | 类型 | 默认值 | 说明 |
|---|---|---|---|
| `-c` / `--conn` | str（必填） | — | 连接名 |
| `--original` | str（必填） | — | 原始 SQL 文本（不含占位符，已替换为真实值） |
| `--rewrite` | str（必填） | — | 改写后的 SQL 文本（不含占位符） |
| `--no-equiv` | flag | 关闭 | 跳过结果集等价性校验（md5 采样） |
| `--auto-index` | flag | 关闭 | 通过 `gs_index_advise` 自动发现并组合假设索引 |
| `--index` | str（可重复） | `[]` | 显式指定 `CREATE INDEX DDL` 加入组合验证（可多次使用） |
| `--format` | `markdown`\|`json` | `markdown` | 输出格式 |
| `--timeout` | int | `30` | 语句超时（秒） |

**示例：**

```bash
# 基本改写验证
python3 $SKILLS/gaussdb-sqltune/scripts/verify.py -c og-prod \
  --original 'SELECT * FROM orders WHERE TO_CHAR(order_date, '"'"'YYYY-MM'"'"') = '"'"'2024-01'"'"'' \
  --rewrite  'SELECT * FROM orders WHERE order_date >= '"'"'2024-01-01'"'"' AND order_date < '"'"'2024-02-01'"'"''

# 改写 + 自动发现索引组合验证
python3 $SKILLS/gaussdb-sqltune/scripts/verify.py -c og-prod \
  --original 'SELECT ...' \
  --rewrite  'SELECT ...' \
  --auto-index \
  --index 'CREATE INDEX ON myschema.orders(order_date)'
```

> 注意：`gaussdb-sqltune` 和 `gaussdb-proctune` 目录下各有一份相同内容的 `verify.py`，两者功能一致。
gaussdb-
---

### 7.6 gaussdb-proctune

**脚本：** `scripts/proctune.py`（两个子命令）和 `scripts/verify.py`（改写验证，同 sqltune/verify.py）

#### proctune.py collect

**功能：** 采集存储过程的结构证据——源码、结构热点（循环内 SQL、逐行 DML、动态 SQL、循环内异常块）、嵌入语句列表、运行时归因说明、关键 GUC。

| 参数 | 类型 | 默认值 | 说明 |
|---|---|---|---|
| `proc`（位置参数，必填） | str | — | 存储过程全限定名，格式 `schema.proc` |
| `-c` / `--conn` | str（必填） | — | 连接名 |
| `--format` | `markdown`\|`json` | `markdown` | 输出格式 |
| `--timeout` | int | `30` | 语句超时（秒） |

**示例：**

```bash
python3 $SKILLS/gaussdb-proctune/scripts/proctune.py collect -c og-prod myschema.settlement
```

#### proctune.py tune-cursor

**功能：** 对每个只读游标的 SELECT 采集证据并用 hypopg 验证索引候选；跳过 FOR UPDATE、动态游标等不符合条件的游标。

| 参数 | 类型 | 默认值 | 说明 |
|---|---|---|---|
| `proc`（位置参数，必填） | str | — | 存储过程全限定名，格式 `schema.proc` |
| `-c` / `--conn` | str（必填） | — | 连接名 |
| `--cursor` | str（可重复） | `[]` | 只处理指定名称的游标（默认处理全部符合条件的游标） |
| `--bind` | str（可重复） | `[]` | 覆盖游标变量值，格式 `var=value`（可多次使用） |
| `--format` | `markdown`\|`json` | `markdown` | 输出格式 |
| `--timeout` | int | `30` | 语句超时（秒） |

**示例：**

```bash
# 对全部游标做证据采集+索引验证
python3 $SKILLS/gaussdb-proctune/scripts/proctune.py tune-cursor -c og-prod myschema.settlement

# 只处理特定游标
python3 $SKILLS/gaussdb-proctune/scripts/proctune.py tune-cursor -c og-prod myschema.settlement \
  --cursor cur_orders

# 提供真实变量值（提高索引验证精度）
python3 $SKILLS/gaussdb-proctune/scripts/proctune.py tune-cursor -c og-prod myschema.settlement \
  --bind p_date=2024-01-01 --bind p_status=active
```

---

### 7.7 gaussdb-procinfo

**脚本：** `scripts/procinfo.py`

**功能：** 只读静态诊断存储过程——取源码、扫描结构热点、列出嵌入语句、快照关键 GUC。不改写、不验证、不执行过程。

| 参数 | 类型 | 默认值 | 说明 |
|---|---|---|---|
| `proc`（位置参数，必填） | str | — | 存储过程全限定名，格式 `schema.proc` |
| `-c` / `--conn` | str（必填） | — | 连接名 |
| `--format` | `markdown`\|`json` | `markdown` | 输出格式 |
| `--timeout` | int | `30` | 语句超时（秒） |

**示例：**

```bash
python3 $SKILLS/gaussdb-procinfo/scripts/procinfo.py -c og-prod myschema.settlement
python3 $SKILLS/gaussdb-procinfo/scripts/procinfo.py -c og-prod myschema.settlement --format json
```

---

### 7.8 gaussdb-topproc

**脚本：** `scripts/topproc.py`

**功能：** 按资源消耗排名最耗时的存储过程/函数，数据源为 `pg_stat_user_functions`。若结果为空，提示用户开启 `track_functions`。

| 参数 | 类型 | 默认值 | 说明 |
|---|---|---|---|
| `-c` / `--conn` | str（必填） | — | 连接名 |
| `--by` | `time`\|`self`\|`calls` | `time` | 排序键：`time`=总耗时、`self`=自身耗时（剔除子函数）、`calls`=调用次数 |
| `--limit` | int | `20` | 最多返回的行数 |
| `--format` | `markdown`\|`json` | `markdown` | 输出格式 |
| `--timeout` | int | `30` | 语句超时（秒） |

**示例：**

```bash
# 按总耗时排 Top 20
python3 $SKILLS/gaussdb-topproc/scripts/topproc.py -c og-prod --by time --limit 20

# 按自身耗时排（剔除被调用子函数的时间）
python3 $SKILLS/gaussdb-topproc/scripts/topproc.py -c og-prod --by self

# 按调用次数排
python3 $SKILLS/gaussdb-topproc/scripts/topproc.py -c og-prod --by calls --limit 10
```

---

### 7.9 health

**脚本：** `scripts/health.py`

**功能：** 一次性只读采集 12 个维度的健康证据，并按阈值产生确定性发现（严重度：健康/关注/告警/严重）。12 个维度包括：`overview`（总览）、`waits`（等待事件）、`slowsql`（慢 SQL）、`xact`（长事务/空闲事务）、`bloat`（死元组膨胀）、`lwlock`（轻量锁）、`locks`（事务锁/阻塞链）、`conn`（连接/活跃会话）、`logs`（Checkpoint/WAL/归档）、`repl`（主备复制）、`schema`（对象/索引）、`concurrency`（事务并发）。

| 参数 | 类型 | 默认值 | 说明 |
|---|---|---|---|
| `-c` / `--conn` | str（必填） | — | 连接名 |
| `--include` | str（逗号分隔） | `""`（全部） | 只采集指定维度，例如 `waits,slowsql,bloat` |
| `--exclude` | str（逗号分隔） | `""`（不排除） | 排除指定维度 |
| `--top` | int | `10` | 各 Top 列表（如 Top 慢 SQL、Top 死元组表）的条数 |
| `--format` | `markdown`\|`json` | `markdown` | 输出格式 |
| `--timeout` | int | `30` | 语句超时（秒） |

**示例：**

```bash
# 全量 12 维健康检查
python3 $SKILLS/health/scripts/health.py -c og-prod

# 只看等待事件和锁相关维度
python3 $SKILLS/health/scripts/health.py -c og-prod --include waits,lwlock,locks

# 排除主备复制维度（单机实例）
python3 $SKILLS/health/scripts/health.py -c og-prod --exclude repl

# 各 Top 列表显示 20 条
python3 $SKILLS/health/scripts/health.py -c og-prod --top 20

# JSON 输出（程序化使用）
python3 $SKILLS/health/scripts/health.py -c og-prod --format json
```

---

### 7.10 wdr

**脚本：** `scripts/wdr.py`（三个子命令）

**功能：** WDR 快照间负载分析。三步工作流：列快照 → 采集 delta 证据 → 渲染报告（渲染步骤不连接数据库）。

#### wdr.py snaps（列快照）

| 参数 | 类型 | 默认值 | 说明 |
|---|---|---|---|
| `-c` / `--conn` | str（必填） | — | 连接名 |
| `--limit` | int | `20` | 列出最近 N 个快照 |
| `--timeout` | int | `30` | 语句超时（秒） |

```bash
python3 $SKILLS/wdr/scripts/wdr.py snaps -c og-prod
python3 $SKILLS/wdr/scripts/wdr.py snaps -c og-prod --limit 50
```

#### wdr.py collect（采集 delta 证据）

| 参数 | 类型 | 默认值 | 说明 |
|---|---|---|---|
| `-c` / `--conn` | str（必填） | — | 连接名 |
| `--begin` | int（必填） | `0` | 起始快照 ID（从 snaps 输出取得） |
| `--end` | int（必填） | `0` | 结束快照 ID（必须大于 begin） |
| `--scope` | `node`\|`cluster` | `node` | 采集范围 |
| `--node` | str | `""` | 节点名（空则自动检测） |
| `--top` | int | `10` | 各 Top 列表条数 |
| `--save-html` | str | `""` | 将原生 WDR HTML 报告落盘到指定路径（审计用） |
| `--format` | `markdown`\|`json` | `markdown` | 输出格式 |
| `--timeout` | int | `30` | 语句超时（秒） |

```bash
# 采集快照 5 到 8 之间的 delta，输出到 JSON 文件（供 render 步骤使用）
python3 $SKILLS/gaussdb-wdr/scripts/wdr.py collect -c og-prod \
  --begin 5 --end 8 \
  --format json > /tmp/wdr_evidence.json

# 同时留底原生 WDR HTML 报告
python3 $SKILLS/wdr/scripts/wdr.py collect -c og-prod \
  --begin 5 --end 8 \
  --save-html /tmp/wdr_native.html \
  --format json > /tmp/wdr_evidence.json
```

#### wdr.py render（渲染最终报告，不连数据库）

| 参数 | 类型 | 默认值 | 说明 |
|---|---|---|---|
| `--evidence` | str（必填） | `""` | `collect --format json` 产出的证据 JSON 路径 |
| `--interp` | str（必填） | `""` | LLM 产出的 `interp.json` 路径（判断与发现） |
| `--format` | `md`\|`ansi` | `md` | 输出格式：`md`=Markdown，`ansi`=带颜色的终端文本 |
| `--no-color` | flag | 关闭 | `ansi` 模式下去掉颜色 |
| `--out` | str | `""` | 输出落盘到指定文件（默认打印到 stdout） |

```bash
# 渲染报告并输出到 stdout
python3 $SKILLS/wdr/scripts/wdr.py render \
  --evidence /tmp/wdr_evidence.json \
  --interp /tmp/wdr_interp.json

# 渲染为带颜色的终端格式
python3 $SKILLS/wdr/scripts/wdr.py render \
  --evidence /tmp/wdr_evidence.json \
  --interp /tmp/wdr_interp.json \
  --format ansi

# 落盘到文件
python3 $SKILLS/wdr/scripts/wdr.py render \
  --evidence /tmp/wdr_evidence.json \
  --interp /tmp/wdr_interp.json \
  --out /tmp/wdr_report.md
```

---

## 8. 排障表

| 现象 | 原因 | 解决方法 |
|---|---|---|
| `ModuleNotFoundError: No module named 'pg8000'`（或 `cryptography`、`yaml`） | Python 依赖未安装 | `python3 -m pip install -r requirements.txt` |
| `ModuleNotFoundError: No module named 'common'` | 安装时漏拷 `common/` 目录，或直接软链了源码树 | 重跑 `install-opencode.sh`（会自动拷 `common/`） |
| `SKILL.md` 中出现字面量 `{baseDir}` | 跳过了安装脚本，或手动安装时忘记替换 | 重跑 `install-opencode.sh`，或手动执行替换（见 5.2 节的替换命令） |
| `no connection named 'og-prod'` | `$GSDB_HOME/config.yaml` 中没有该名称的连接定义 | 检查 `$GSDB_HOME` 是否指向正确目录（`echo $GSDB_HOME`），以及 config.yaml 中的 `name` 字段是否与 `-c` 参数一致 |
| `decrypt credential ...` 失败（解密失败） | `$GSDB_HOME/key` 与 `.enc` 文件不匹配（例如跨机迁移时只拷了 `.enc` 没拷 `key`） | 迁移时需同时迁移 `key` 文件；或改用 `export GSDB_PASSWORD=...` 临时覆盖 |
| `Session is read-only` / 写操作被拦截 | 所有技能脚本默认使用只读会话，写/DDL 语句被会话级 `SET SESSION READ ONLY` 拦截 | 这是预期行为，技能不支持写操作；需要写操作请用独立的数据库连接工具 |
| `DBError: gsql binary not found` | `gsql` 不在 PATH 中（macOS 上无原生版本） | 设置 `driver: pg8000`（在 config.yaml 中），或 `export GDAA_GSQL=/path/to/gsql`（Linux 上安装 openGauss 客户端后设置） |
| `慢SQL/gaussdb-topsql 结果为空` | 实例未开启 `enable_stmt_track`，或阈值过高 | 联系 DBA 确认 `SHOW enable_stmt_track`；或降低 `--threshold`（slowsql），如改为 `--threshold 100` |
| `gaussdb-topproc 结果为空（无函数级统计）` | `track_functions=none`，函数级统计关闭 | 联系 DBA 执行 `SET track_functions='pl'`（或 `'all'`），然后调用一次目标存储过程，再重跑 |
| `sqltune verify 报"索引验证不可用"或需要 pg8000` | `driver: gsql` 生效（gsql 每次请求起独立子进程，无法保持 hypopg 会话级虚拟索引） | 在 config.yaml 中将该连接的 `driver` 改为 `pg8000` |
| WDR `"WDR 未开启"` 或 `"快照不足"` | 实例 `enable_wdr_snapshot=off`，或快照数量不足 2 个 | 联系 DBA 执行 `ALTER SYSTEM SET enable_wdr_snapshot=on` 并 reload/重启；或手动 `SELECT create_wdr_snapshot()` 创建快照，但**本技能脚本不代为执行** |
| `python3: command not found` | Python 未安装，或未加入 PATH | 参考 1.2 节安装 Python |

---

## 9. 升级 / 卸载

### 升级

更新源码后重跑安装脚本（覆盖式安装）：

```bash
cd /path/to/opencode_skill
git pull
./install-opencode.sh
```

若只升级部分技能：

```bash
./install-opencode.sh gaussdb-sqltune gaussdb-health gaussdb-wdr
```

### 卸载

删除安装目录即可：

```bash
# 卸载全局安装
rm -rf ~/.config/opencode/skills/{common,gaussdb-slowsql,gaussdb-topsql,gaussdb-sqlfetch,gaussdb-explain,gaussdb-sqltune,gaussdb-proctune,gaussdb-procinfo,gaussdb-topproc,gaussdb-health,gaussdb-wdr}

# 卸载项目级安装
rm -rf /path/to/your/repo/.opencode/skills/{common,gaussdb-slowsql,gaussdb-topsql,gaussdb-sqlfetch,gaussdb-explain,gaussdb-sqltune,gaussdb-proctune,gaussdb-procinfo,gaussdb-topproc,gaussdb-health,gaussdb-wdr}
```

连接配置和凭据（`$GSDB_HOME`）不会被自动删除，需要时手动清理：

```bash
# 谨慎：会删除所有连接配置和凭据
rm -rf "$GSDB_HOME"
```
