---
name: gaussdb-login
version: 1.0.0
description: "登录并选定本次会话要连的 OpenGauss/GaussDB 数据库。**这是所有数据库操作的第一步**：其余 gaussdb-* skill 不带 -c 时都用这里选定的连接。用户说“连数据库”“登录数据库”“换一个库”“连哪个库”“看有哪些数据库可以连”“切到 app2 的库”，或在尚未登录的情况下要求做慢 SQL/健康检查/调优/WDR 等任何取数操作时使用。触发后运行 scripts/login.py：配置首行 connection_mode 是 gsql 就把可选连接列成菜单让用户挑，是 api 就引导用户提供要访问的数据库名。不要凭空假设连接名。"
allowed-tools: ["exec", "read"]
compatibility: opencode
metadata:
  runtime: python3
  emoji: "🔑"
  family: connection
---

# 数据库登录（OpenGauss/GaussDB）

**所有数据库操作的第一步。** 其余 13 个 skill 不带 `-c` 时，用的都是这里选定
的连接。

命中以下请求时必须使用本 skill 并实际执行脚本：

- 用户要连数据库 / 登录数据库 / 换一个库 / 切到别的应用
- 用户问「有哪些库可以连」「现在连的是哪个」
- 用户要做任何取数操作（慢 SQL、健康检查、调优、WDR…）而**尚未登录**

## 怎么做

### 第一步：看配置是哪种模式

```bash
python3 {baseDir}/scripts/login.py --list
```

配置文件首行 `connection_mode` 决定后面怎么走，**你不要替用户猜**：

| 模式 | 含义 | 接下来 |
|---|---|---|
| `gsql` | 直连数据库 | 把 `--list` 列出的连接**原样展示给用户**，让他选 |
| `api` | 走 GRMP 中间件 | 问用户要访问哪个数据库 |

### 第二步：gsql 模式 —— 让用户挑

`--list` 会输出一张表（应用 / 连接名 / 类型 / 目标 / 用户 / 驱动）。
**把这张表展示给用户**，等他选，然后：

```bash
python3 {baseDir}/scripts/login.py --app <应用> --conn <连接名>
```

多个应用下可能有同名连接，所以 `--app` 和 `--conn` 要一起给。

### 第三步：api 模式 —— 问用户要连哪个库

api 模式下没有预置的连接清单：目标库由用户指定，中间件按它路由到实例。

```bash
python3 {baseDir}/scripts/login.py --database <数据库名>
```

用户没说是哪个库时**要问**，不要拿一个见过的名字去试。

### 其他

```bash
python3 {baseDir}/scripts/login.py --status    # 当前连的是哪个
python3 {baseDir}/scripts/login.py --logout    # 清除会话
```

## 登录成功之后

告诉用户现在连的是哪个库（应用 / 连接名 / 目标地址 / 模式），然后正常继续
他原本要做的事。**其余 skill 不需要再传 `-c`**。

用户中途要换库，再跑一次本 skill 即可，会话会被覆盖。

## 规则

- **不要猜连接名。** 配置里有什么就展示什么；用户没选就问，不要挑「看起来
  像生产库」的那个。连错库做诊断，输出看起来完全正常。
- **登录失败时不要绕过。** 脚本验证不通过就**不会**建立会话，此时不要改用
  `-c` 硬连别的库，把失败原因如实告诉用户。
- **`--no-verify` 不要主动用。** 它跳过连通性验证，失败会推迟到下一个 skill
  取数时才出现，那时错误看起来像是那个 skill 坏了。
- 已经登录过就不必重复登录；不确定时先 `--status`。
- **绝不回显口令或令牌。** 会话文件里也没有它们（口令走加密凭据，令牌走
  环境变量）。

## 安全红线

- **配置文件里绝不允许出现明文口令。** `config.yaml` 只放连接元数据 —— 它会被 cat、会进备份、会被贴进工单和聊天窗口，而没人会想到里面藏着生产库口令。口令一律加密存放在 `$GSDB_HOME/credentials/*.enc`（AES-256-GCM，AAD 绑定连接名），由脚本自动解密，**你不要去读取或解密它**。
  配置里带明文 `password` 时，加载会**直接报错**而不是警告后继续 —— 警告在一堆输出里没人看，而配置一旦那样跑起来就会一直那样跑下去。
  发现用户配置里有明文口令时，提示他改用：`python3 -m common.credential_cli set <连接名>`，然后删掉配置里的 password/encrypted 两行。

- 只通过本 skill 的脚本读配置，**不要**自己去 cat / 解密 `config.yaml`、
  `credentials/`、`key`。
- 本 skill 只读配置 + 写一个不含凭据的会话文件，不改配置、不存口令、不建库。
