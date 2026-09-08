---
name: gaussdb-login
version: 1.2.0
description: "登录并选定本次会话要连的 OpenGauss/GaussDB 数据库。**这是所有数据库操作的第一步**：其余 gaussdb-* skill 不带 -c 时都用这里选定的连接。用户说“连数据库”“登录数据库”“换一个库”“连哪个库”“看有哪些数据库可以连”“切到 app2 的库”，或在尚未登录的情况下要求做慢 SQL/健康检查/调优/WDR 等任何取数操作时使用。触发后运行 scripts/login.py：配置首行 connection_mode 是 gsql 就把可选连接列成菜单让用户挑，不要凭空假设连接名, 是 api 就引导用户提供要访问的数据库名。"
allowed-tools: ["exec", "read"]
compatibility: opencode
metadata:
  runtime: python3
  emoji: "🔑"
  family: connection
---

# 数据库登录（OpenGauss/GaussDB）

**所有数据库操作的第一步。** 登录成功会得到一个**会话句柄**（5 位小写字母数字），其余 skill 用
`--session <句柄>` 指名要用这条连接；沙箱里只有一个会话时也可以不带。

**为什么有句柄**：客户现场多个用户共用一个沙箱。原先只有一个会话文件，谁最后登录所有人就连谁的库，
退出码 0、不报错——静默串库。现在每次登录各存一份，靠句柄区分；分不清时脚本拒绝执行，不猜。

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
python3 {baseDir}/scripts/login.py --ip <实例IP> --database <数据库名>
```

用户没说是哪个库时**要问**，不要拿一个见过的名字去试。数据面仅做标识之用。

### 其他

```bash
python3 {baseDir}/scripts/login.py --status                    # 列出沙箱里的全部会话，标出不带句柄时会用哪条
python3 {baseDir}/scripts/login.py --logout --session <句柄>   # 退出某一个会话
python3 {baseDir}/scripts/login.py --logout                    # 只有一个会话时可以不带句柄
python3 {baseDir}/scripts/login.py --logout --all              # 清掉沙箱里全部会话（会影响其他用户，用户明确要求才做）
```

## 登录成功之后

登录输出里有一行「主备」（登录时顺手跑一次已注册的 `health.overview` 看 `pg_is_in_recovery()`）。显示**备机**时要告诉用户：
`dbe_perf.statement_history` 是 unlogged 表备机读不到，sqlfetch / sqltune / sqlreview / proctune 取 SQL 文本会退到归一化文本，
要真实参数值需用主库 IP 重新登录。显示「未探测」只是没判断出来（脚本没注册或版本没这列），不是失败，照常用。

告诉用户现在连的是哪个库（应用 / 实例 IP / 数据库名称 / 模式），然后正常继续
他原本要做的事。**记住登录输出里的会话句柄，本次对话里后续每条 skill 命令都带 `--session <句柄>`**；
句柄不需要念给用户，只在命令里用。登录输出里若列出「沙箱里还有 N 个其他会话」，那是别人的登录，不要动、不要用。

用户中途要换库，再跑一次本 skill 拿一个新句柄即可，旧会话不会被覆盖；不再需要的那条用 `--logout --session <旧句柄>` 退掉。

**没有句柄时怎么办**：某条命令被拒绝并列出「沙箱里有 N 个会话」，说明本次对话里没有登录过、或句柄丢了。
把清单里的目标库转给用户确认要用哪个，或让用户重新登录；**不要自己从清单里挑一个**，挑错就是在别人的库上做诊断。

## 登出
用户要求登出数据库，或更换其他数据库时，先退掉本次对话的会话再跑一次本 skill
```bash
python3 {baseDir}/scripts/login.py --status                    # 列出全部会话
python3 {baseDir}/scripts/login.py --logout --session <句柄>   # 只退自己这一条
```
`--logout --all` 会把其他用户的会话一起清掉，只在用户明确要求时用。


## 规则

- **不要猜连接名。** 1、API模式：不显示连接名。2、gsql模式： 配置里有什么就展示什么；用户没选就问，不要挑「看起来
  像生产库」的那个。连错库做诊断，输出看起来完全正常。
- **登录失败时不要绕过。** 脚本验证不通过就**不会**建立会话，此时不要改用
  `-c` 硬连别的库，把失败原因如实告诉用户。
- **`--no-verify` 不要主动用。** 它跳过连通性验证，失败会推迟到下一个 skill
  取数时才出现，那时错误看起来像是那个 skill 坏了。
- 已经登录过就不必重复登录；不确定时先 `--status`。
- **绝不回显口令或令牌。** 会话文件里也没有它们（口令走加密凭据，令牌走
  环境变量）。

