# GRMP 兼容中间件 —— 开发方案

**日期**：2026-07-31
**状态**：待评审
**目标仓库**：`~/gh_skill/opencode_skill-main-v2-0729`
**协议依据**：`~/gh_skill/api_photo/GRMP-agent白屏化诊断接口规范说明.md`

---

## 1. 目标与非目标

### 目标

在本机搭一个**协议级兼容 GRMP** 的中间件，使 skill 能以「先注册脚本、再按 ID 调用」的方式访问 openGauss/GaussDB，从而在本机复现客户环境的真实约束与调用链路。

改造后 `~/gh_skill` 对数据库的访问分两条路径：

| 路径 | 说明 | 用途 |
|---|---|---|
| **中间件路径** | skill → HTTP → grmp-mock → pg8000 → 数据库 | 模拟客户环境，交付前验证 |
| **直连路径** | skill → `common.Database` → gsql/pg8000 → 数据库 | 本地开发调试，能力不受白名单限制 |

两条路径**共用同一份 SQL 定义**（脚本仓库），避免维护两套。

### 非目标

- 不复刻客户中间件的内部实现（鉴权算法、实例注册表、脚本作用域过滤的真实规则均不可知）
- 不实现异步执行与 Python 命令（客户文档本身就没有规范）
- 不追求性能

### 「兼容」的边界

能做到：**按客户接口文档写的客户端，指向我们的服务能正常工作**。
做不到：按真实中间件未文档化行为写的客户端也能工作——那些行为不可知。

---

## 2. 环境事实（已实测）

### 2.1 机器与运行时

| 项 | 情况 |
|---|---|
| 开发/调试机 | Mac，`ssh sqlrush@192.168.128.1` |
| 文件系统 | Mac 与 Linux 容器**共享** `/Users/sqlrush/`，可在任一侧编辑、Mac 侧运行 |
| Python | Mac 系统 python3 **3.9.6**（无 venv） |
| 已装依赖 | `pg8000 1.31.5`、`PyYAML`、`cryptography` —— 均已就绪 |
| **无** gsql / psql | 直连路径在 Mac 上实际走 pg8000 自动兜底 |
| 无 node / go / docker CLI | 中间件必须是**纯 Python + 标准库**，不引入新依赖 |

**Python 3.9 约束**：不能用 `match`、不能用运行时 `X | Y` 注解。仓库已普遍 `from __future__ import annotations`，沿用即可。

### 2.2 数据库

| 实例 | 状态 | 实际地址 |
|---|---|---|
| `og5` 容器 | **运行中** | `127.0.0.1:5433`（容器内 5432） |
| `og-pri` 容器 | 已退出（2 周前） | — |
| `og-std` 容器 | 已退出（2 周前） | — |
| `gauss-amm-lab` 机器 | 运行中 | `127.0.0.1:15432`（源码编译 GaussDB，AMM 实验用） |

服务端版本：**openGauss-lite 5.0.3**。

### 2.3 待修的环境问题（P0 前置）

1. `~/.gdaa/config.yaml` 中 `og`/`og-pri`/`og-std` 的 `host:port` 全部过期（写的是 `*.orb.local:5432`，该域名不解析）。至少需把 `og` 改为 `127.0.0.1:5433`。
2. 客户改造版 `common/config.py:76` 把配置目录硬编码为 `/workspace/.opencode/skills/common`。本机运行必须设 `GSDB_HOME=$HOME/.gdaa`。
3. `common/config.py` 的 `_VALID_DRIVERS` 只接受 `gsql`/`pg8000`，新增访问路径需扩展。

### 2.4 参数替换方式：采用文本替换，不用绑定变量

**结论先行：`{{}}` 走文本替换 + 严格类型校验。** 这不是安全上的妥协，而是本项目的目标决定的——中间件的职责是**复现客户行为**，做得比客户更安全，本地就测不出客户环境的真实问题。

三方面证据一致指向文本替换：

1. **协议设计**：`{{name}}` 是命名文本占位符，不是 `$1`/`?`/`:name`；`param_value` 一律以字符串下发。占位符可出现在任意文本位置，这是文本替换才有的自由度。
2. **客户样例**：`where runtime > {{threshold_seconds}}`，INTEGER 类型、占位符裸露无引号。
3. **仓库现状**：现有 skill 本来就是文本内联——`slowsql.py:53` 的 `> {int(threshold_ms)}` 与 `LIMIT {int(limit)}` 是 f-string 拼接，先 `int()` 强转再拼。

在 og5 上实测了绑定变量的边界，结果恰好说明**为什么不能用绑定变量**：

| 占位符位置 | 文本替换（= 客户行为） | 绑定变量 |
|---|---|---|
| `WHERE col > {{x}}` | 正常 | 可绑，正常 |
| `LIMIT {{x}}` / `OFFSET {{x}}` | 正常 | 可绑，正常 |
| `IN ({{x}})` 单值 / 字符串等值 | 正常 | 可绑，正常 |
| `FROM {{table}}`（表名） | 正常 | **语法错误** SQLSTATE 42601 |
| `SELECT {{col}}`（列名） | 正常 | ⚠️ **静默错误**：返回字符串常量，不是列值 |
| `ORDER BY {{col}}` | 正常 | ⚠️ **静默错误**：按常量排序 = 不排序 |

若采用绑定变量，一条在客户环境跑得好好的脚本，在本地中间件上会**静默失效**——方向与我们的目标正相反。

**注入面如何处理**：不靠改变替换方式，靠三层：

1. **严格类型校验**（客户中间件声明了 `parameter_config.type`，大概率也做这一步）：INTEGER 必须匹配 `^-?\d+$`，Boolean 必须是 `true`/`false`，DateTime/Timestamp 按格式校验，不合法即拒绝执行
2. **只监听 `127.0.0.1`**，且只执行预注册脚本
3. **注册期风险报告**：占位符落在标识符位/ORDER BY 位时**不拦截**（拦了就偏离客户行为），而是在注册报告里标出「此脚本在客户环境同样存在注入面」——这份清单本身对客户是有价值的输出

`PREPARE` 仍可用于注册期语法校验，但校验对象改为**用样例值渲染后的完整 SQL**，而不是把占位符换成 `$n`。

---

## 3. 总体架构

### 3.1 单一脚本源，两条执行路径

```
              scripts/registry/<skill>/<name>.yaml
              （SQL 模板 + 参数定义，仓库内单一事实源）
                              │
        ┌─────────────────────┴─────────────────────┐
        │                                           │
   register.py 注册工具                        直连路径
   （静态校验 + PREPARE 校验                   本地渲染模板
     + 生成 GRMP 格式 DML）                    绑定参数后直发
        │                                           │
        ▼                                           │
   script_config 表（SQLite）                       │
        │                                           │
   grmp-mock HTTP 服务                              │
   ├─ POST /diagnostic/agent/common-operations      │
   └─ POST /.../common-operations/invoke            │
        │                                           │
        └──────────────► common.Database ◄──────────┘
                              │
                         og5 (127.0.0.1:5433)
```

### 3.2 skill 侧的统一入口

skill 不直接感知走哪条路径。新增门面 `common/access.py`：

```python
runner = access.for_conn("og")                    # 按 config.yaml 的 driver 选路
rows = runner.run("slowsql.slow_sql",             # 逻辑脚本名，不是数字 ID
                  {"threshold_ms": 200, "limit": 20})
```

- `driver: pg8000` / `gsql` → 直连路径：本地渲染模板 + 绑定参数
- `driver: grmp` → 中间件路径：查列表拿 ID → invoke

**逻辑名而非数字 ID**，这一点是硬要求：接口文档里 `id=56` 是「查看数据库信息」，客户调用示例里同一个 `id=56` 却被传了慢 SQL 的参数——**脚本 ID 是环境相关数据**。硬编码 ID 的失败方式极其隐蔽：换环境后 ID 依然存在，指向另一条脚本，执行成功、结果无关、不报错。

中间件路径的 ID 解析流程：调接口一 → 按 `cmd_name` 匹配逻辑名 → 取 `id`（进程内缓存）→ 调接口二。这和客户环境里 agent 必须做的事完全一致。

---

## 4. 组件设计

按仓库既有风格：小文件、高内聚，单文件 200–400 行，不超过 800。

### 4.1 脚本仓库 `scripts/registry/`

每条脚本一个 YAML，是 SQL 的唯一来源：

```yaml
# scripts/registry/slowsql/slow_sql.yaml
name: slowsql.slow_sql          # 逻辑名，全局唯一，对应 cmd_name
description: 查询平均耗时超阈值的 SQL
script_type: SQL
database_type: postgres
scene: AGENT
is_asyn: 0
compliance_mode: ALL
kernel_version: ALL
cluster_deployment_mode: centralization
sql: |
  SELECT unique_sql_id::text,
         LEFT(REGEXP_REPLACE(query, E'\\s+', ' ', 'g'), 180) AS query,
         n_calls AS calls,
         ...
  FROM dbe_perf.statement
  WHERE (total_elapse_time/NULLIF(n_calls,0))/1000 > {{threshold_ms}}
    AND n_calls > 0
  ORDER BY total_elapse_time/NULLIF(n_calls,0) DESC
  LIMIT {{limit}}
params:
  - key: threshold_ms
    type: INTEGER
    required: true
    description: 平均耗时阈值（毫秒）
  - key: limit
    type: INTEGER
    required: true
    description: 返回条数上限
```

字段刻意与 `script_config` 的 21 列对齐，注册工具做的是机械映射而非再设计。

### 4.2 注册工具 `tools/grmp_register.py`

职责：把 YAML 变成 `script_config` 记录，**并在入库前把所有能静态发现的问题拦下来**。

**校验分两类：硬拦截**（不过即拒绝入库）与**风险标注**（不拦截，只记录）。区分标准很明确：客户环境也会失败的，硬拦；客户环境能跑通的，不能拦——拦了就偏离了模拟目标。

硬拦截：

| # | 校验 | 拦截的问题 |
|---|---|---|
| 1 | 占位符与 `params` 声明双向一致 | SQL 里有未声明的 `{{x}}`，或声明了没用到的参数 |
| 2 | 用样例值渲染后 `PREPARE` 通过 | 模板本身写错，渲染出来就是语法错误 |
| 3 | 引号责任符合约定（见决策表第 4 项） | 与中间件的加引号策略冲突，渲染后语法错误 |
| 4 | 逻辑名唯一 | 重名覆盖 |
| 5 | 非只读语句需在 YAML 显式声明 | 误注册写操作 |
| 6 | 每个参数须有 `type`，且属五种合法类型 | 运行期无法做类型校验 |

风险标注（写入注册报告，不影响入库）：

| 标注 | 触发条件 | 意义 |
|---|---|---|
| `IDENT_POSITION` | 占位符落在表名/列名/`ORDER BY`/`GROUP BY` 位 | 该脚本在客户环境同样存在注入面；类型校验对标识符位无效 |
| `MULTI_VALUE` | 占位符位于 `IN (...)` 内 | 多值展开语义未定义，客户中间件行为未知 |
| `NON_READONLY` | 语句非 SELECT | 需单独审批 |

静态位置判断的实现：把占位符替换成唯一哨兵串，词法扫描其是否紧跟 `FROM`/`JOIN`/`INTO`/`UPDATE` 之后、位于 `SELECT`…`FROM` 之间的裸标识符位、或落在 `ORDER BY`/`GROUP BY` 列表内。

**这份风险清单是有价值的交付物**：它精确回答了「客户现有脚本库里，哪些脚本的参数可能被用来注入」。

工具同时产出**客户格式的 INSERT DML**（`docs/delivery/` 下），列顺序、引号风格、`parameter_config` 的 JSON 键名（`key`/`value`/`type`/`autoAcquire`）与客户样例逐字对齐——这份 DML 是交付给客户走版本发布的成品。

### 4.3 script_config 存储

**用 SQLite，不建在 og 上。** 理由：

- og 上有 200 万行 demo 数据，不希望被测试元数据污染
- 中间件的元数据属于中间件自己，客户那边 GRMP 也有独立的库
- 标准库自带 `sqlite3`，零依赖

表结构保留客户的 **21 列全集**（含 `region`、`deployment_form`、`refered_appbusiness` 等我们用不到的列），使导出的 DML 可直接用于客户环境。

### 4.4 grmp-mock 服务 `tools/grmp_mock/`

标准库 `http.server`，单进程，只监听 `127.0.0.1`。

| 文件 | 职责 | 预估行数 |
|---|---|---|
| `__main__.py` | 入口、参数解析、启动 | ~80 |
| `server.py` | 路由分发、请求体解析、异常兜底 | ~180 |
| `envelope.py` | 两套响应信封 + 错误码 | ~90 |
| `pagination.py` | PageHelper `PageInfo` 全 18 字段计算 | ~110 |
| `registry.py` | 读 script_config、作用域过滤、按名/ID 查 | ~160 |
| `placeholder.py` | `{{}}` 解析、类型校验、文本替换渲染 | ~150 |
| `executor.py` | 调 `common.Database` 执行、组装 result | ~140 |
| `serialize.py` | 全字符串化、布尔/NULL 渲染 | ~90 |
| `instances.py` | `dataIp` → 连接名映射 | ~70 |
| `settings.py` | 兼容性开关集中定义 | ~80 |
| `auth.py` | `auth` 头校验 | ~50 |

`dataIp` 映射沿用客户示例中的 IP，使同一份配置在本机与客户环境行为一致：

```yaml
# tools/grmp_mock/instances.yaml
<CUSTOMER_TEST_IP>: og        # 客户示例中的 IP，本机映射到 og5
```

### 4.5 中间件客户端 `common/grmp_client.py`

skill 侧的调用方，纯标准库 `urllib.request`。

- 令牌从 `GRMP_AUTH_TOKEN` 环境变量读取，**不落盘、不进代码**
- 启动时校验令牌存在，缺失即报错（fail fast）
- 列表结果进程内缓存，避免每次调用都翻页
- 按 `cmd_name` 解析逻辑名 → ID；解析不到时报错信息列出可用脚本名

### 4.6 直连路径 `common/script_runner.py`

从同一份 YAML 渲染：类型校验 → 文本替换 → `db.query(rendered_sql)`。

**两条路径必须共用同一个渲染器**（`placeholder.py`），否则同一模板在两侧生成的 SQL 不同，双路径一致性测试就失去意义——它比对的将是两条不同的 SQL，而不是两条不同的执行链路。

直连路径同样走文本替换而非绑定变量，理由有二：一是保证与中间件路径逐字一致；二是仓库现有 skill 本来就是文本内联（`slowsql.py:53`），改成绑定变量反而是行为变更。

---

## 5. 协议实现细则

严格对齐 `GRMP-agent白屏化诊断接口规范说明.md`。以下为实现时必须逐条落实的点。

### 5.1 路由与传输

```
POST {base}/icbc/paas/aiops/grmp/diagnostic/agent/common-operations
POST {base}/icbc/paas/aiops/grmp/diagnostic/agent/common-operations/invoke
```

- 路径中**不含** `/dataip/{dataip}`（接口文档示例是错的，以客户实际调用为准）
- 为兼容起见，`/dataip/{dataip}/...` 一并注册并接受，路径变量与 body 冲突时以 body 为准并记 WARN
- `auth` 头，非 `Authorization`，无前缀
- `Content-Type: application/json`

### 5.2 两套响应信封

接口一：
```json
{ "code": "0", "msg": "success", "result": { ...PageInfo... } }
```

接口二：
```json
{ "result": {"type": "...", "data": ...}, "task_id": "grmp-<uuid4>",
  "call_type": "sync", "status": "finished" }
```

- `code` 是**字符串**
- 接口二**不带** `code`/`msg`
- 错误：HTTP 200 + `{"code":"1","msg":"<中文>"}`。已知样例必须逐字复现：`dataIp` 查不到实例时返回 `"通过dataIp查询不到对应高斯实例信息"`

### 5.3 分页

- 请求的 `offset` 是**页码**（1-based），不是行偏移量
- 响应含 `PageInfo` 全 18 字段，`navigatePages` 固定 `8`
- `prePage`/`nextPage` 在边界处为 `0`，不是 `null`
- 字段名 `navigatepageNums` 第二个 p 小写
- 不支持服务端按 `cmd_type` 过滤

### 5.4 结果序列化

- **所有值渲染成 JSON 字符串**，数值型也不例外（`"datdba":"10"`）
- 布尔与 NULL 的渲染风格做成配置项（见第 6 节）
- `type` 取 `array`（有结果集）或 `Text`（无结果集/标量输出）

### 5.5 显式不支持的能力

以下情况返回明确错误，**绝不静默降级**：

| 情况 | 行为 |
|---|---|
| `cmd_type: PYTHON` | `code:"1"` + "本实现不支持 Python 命令" |
| `is_asyn = 1` | `code:"1"` + "本实现不支持异步执行" |
| 脚本 ID 不存在 | `code:"1"` + 明确说明 |
| 必填参数缺失 | `code:"1"` + 点名缺哪个 |
| 参数类型不匹配 | `code:"1"` + 说明期望类型 |

不做「异步脚本偷偷同步执行后返回 `call_type:"sync"`」——那会让调用方误以为拿到了异步语义。

---

## 6. 兼容性决策表

文档未定死、必须我们自己定的项。**全部做成配置项**，默认值如下，联调时对着客户真实响应校准。

| # | 决策项 | 默认值 | 依据 | 猜错的后果 |
|---|---|---|---|---|
| 1 | 布尔渲染 | `t` / `f` | 文档 3.2（较新的那段） | ⚠️ 布尔判断反向，无异常 |
| 2 | NULL 渲染 | `""` 空字符串 | 3.2 中 `datacl` 的旁证 | 无法区分 NULL 与空串 |
| 3 | Timestamp 单位 | 按长度自适应（≥13 位为毫秒） | 文档两种都给了 | ⚠️ 时间范围整体偏移 |
| 4 | 占位符替换方式 | **文本替换 + 严格类型校验**，不用绑定变量 | 见 2.4：协议设计、客户样例、仓库现状三方一致 | 用绑定变量则标识符位占位符静默失效 |
| 5 | 占位符引号责任 | **作者写引号**（`= '{{name}}'`），中间件只做纯文本替换 | 最简实现（`String.replace`）即如此；INTEGER 样例裸露无引号与之相容 | 脚本跨环境语法错误（会报错，不静默） |
| 6 | 类型校验强度 | INTEGER `^-?\d+$`、Boolean 仅 `true`/`false`、时间按格式；不合法即拒绝执行 | 客户声明了 `parameter_config.type`，大概率也校验 | 放松则注入面扩大；收紧则可能拒绝客户能跑通的脚本 |
| 7 | 可选参数未传 | 报错，不做「删掉条件」的猜测 | 文档未定义 | 静默改变语义 |
| 8 | `code` 错误码 | 统一 `"1"` | 唯一已知样例 | 客户端无法细分错误 |
| 9 | 结果集行数上限 | 10000，超限报错不截断 | 文档未定义 | 截断 = 静默丢数据 |
| 10 | 兼容旧参数形状 | 默认**拒绝** `{data_type,description,...}` | 接口文档示例是错的 | 静默取错值 |

第 1、3 项标 ⚠️ 者属于「猜错也不报错、只出错值」，**必须**向客户确认。

第 5、6 项猜错会报错（语法错误 / 参数被拒），不会静默出错值，但仍会导致本机与客户环境行为不一致——同样要确认，优先级次之。确认方式很轻：向客户要**一条带 String 参数的真实脚本**即可同时定案这两项。

---

## 7. 分期与验收

每期结束必须**在 Mac 上真跑一遍**，单测绿不算完成。

### P0 — 环境就绪（0.5 天）

- 修正 `~/.gdaa/config.yaml`：`og` → `127.0.0.1:5433`
- 建 venv 或确认系统 python3 依赖可用
- 固化运行方式：`GSDB_HOME=$HOME/.gdaa`
- **验收**：`Database.connect("og")` 在 Mac 上成功，能查出 `version()`

### P1 — 协议骨架 + 契约测试（1 天）

- `envelope.py` / `pagination.py` / `serialize.py`
- 用规范说明里的**真实报文做 golden case**，不连库
- **验收**：接口文档中的两个响应示例能被逐字节复现（除 task_id）

### P2 — 脚本仓库与注册工具（1.5 天）

- YAML schema + `grmp_register.py` 六条校验链
- SQLite `script_config`（21 列）
- 先注册 2 条脚本：`slowsql.slow_sql`、`health.db_info`
- **验收**：
  - 硬拦截生效：构造「占位符未声明」「渲染后语法错误」「引号责任不符」三类坏脚本，注册工具全部拒绝并给出可操作的错误信息
  - 风险标注生效：构造占位符落在表名位/`ORDER BY` 位的脚本，注册工具**放行**（客户环境能跑）但在报告里标出 `IDENT_POSITION`
  - 类型校验生效：`threshold_ms` 传 `"1 OR 1=1"` 被拒绝

### P3 — 接口一（1 天）

- `registry.py` / `instances.py` / `auth.py` / 接口一路由
- **验收**：`curl` 用客户示例的报文格式，能查到 P2 注册的两条脚本；用 `dataIp:"1.2.3.4"` 能拿到逐字一致的 `code:"1"` 错误

### P4 — 接口二同步 SQL（1.5 天）

- `placeholder.py` / `executor.py` / 接口二路由
- **验收**：`curl` 带参调用 `slowsql.slow_sql`，在 og5 上真查出数据；响应结构与客户示例同形

### P5 — skill 接入（2 天）

- `common/access.py` / `grmp_client.py` / `script_runner.py`
- `config.py` 扩展 `_VALID_DRIVERS`
- 改造 `slowsql` 一个 skill 走新入口
- **验收**：同一个 skill，`driver: pg8000` 与 `driver: grmp` 两条路径跑出的结果**逐行一致**

### P6 — 全量迁移（按 skill 数量估）

- 其余 12 个 skill 的 SQL 逐条抽取、注册、接入
- **验收**：全部 skill 双路径一致性通过；无法迁移的 SQL 单独成表，说明原因（这份清单本身就是交付物——它精确回答了「哪些 skill 在客户环境跑不了」）

---

## 8. 测试策略

沿用仓库既有约定（`tests/test_*_units.py` 单测、`test_*_live.py` 连库）。

| 层次 | 文件 | 内容 |
|---|---|---|
| 契约 | `test_grmp_protocol_units.py` | 用客户真实报文做 golden，校验信封/分页/序列化 |
| 注册校验 | `test_grmp_register_units.py` | 六条校验链，重点覆盖两个静默陷阱 |
| 占位符 | `test_grmp_placeholder_units.py` | 类型校验、文本渲染、注入样本被拒、两条路径渲染结果逐字相同 |
| 客户端 | `test_grmp_client_units.py` | 逻辑名解析、缓存、错误传播 |
| 连库 | `test_grmp_live.py` | 真起服务、真连 og5、端到端 |
| **双路径一致性** | `test_dual_path_live.py` | 同脚本两条路径结果比对 |

**双路径一致性测试是本方案价值最高的一项**：任何差异要么是中间件 bug，要么是协议的真实限制，两者都必须被看见。

---

## 9. 安全

| 项 | 做法 |
|---|---|
| 认证令牌 | `GRMP_AUTH_TOKEN` 环境变量，启动时校验存在；不写进代码/配置/版本库 |
| 数据库凭据 | 复用现有 `common/credential.py` 加密凭据，中间件不新增凭据存储 |
| SQL 注入 | 见下方专项说明——本组件**刻意**采用文本替换 |
| 只读 | 执行器沿用 `read_only=True`，非只读脚本需在 YAML 显式声明并走单独审批 |
| 监听范围 | 只绑 `127.0.0.1`，不对外暴露 |
| 错误信息 | 数据库错误经脱敏后回传，不泄露连接串与主机名 |
| 客户测试环境值 | `<CUSTOMER_TEST_IP>` 等仅作本机映射键使用，不出现在对外材料 |

### 关于「不用绑定变量」的专项说明

仓库通用安全规范要求「SQL 注入防护使用参数化查询」。本组件**有意偏离**该条，理由与边界如下，需在评审时明确接受：

**为什么偏离**：grmp-mock 是**测试替身**，唯一职责是复现客户中间件的行为。客户中间件用 `{{}}` 文本占位符，若我们改用绑定变量，标识符位的占位符会静默失效（见 2.4 实测），导致「客户能跑的脚本本地跑不了」——本地测试因此失去意义。做得比被模拟对象更安全，等于测不出被模拟对象的问题。

**边界与补偿措施**：

- 仅限**本机开发调试**使用，只监听 `127.0.0.1`，**不得部署到任何共享或生产环境**
- 启动时打印醒目提示，声明本进程使用文本替换
- 严格类型校验作为第一道防线（决策表第 6 项）
- 只执行预注册脚本，不接受任意 SQL
- 只读会话（`read_only=True`）
- 注册期对存在注入面的脚本出具风险清单

**这一偏离仅限 `tools/grmp_mock/` 与共享渲染器**。仓库其余部分不受影响；`common/` 下面向真实数据库的既有代码保持原有约定。

---

## 10. 风险与未决项

| 风险 | 影响 | 应对 |
|---|---|---|
| 部分 skill 的 SQL 无法模板化（动态拼表名/列名） | 该 skill 在客户环境跑不了 | P6 逐条记录，形成能力缺口清单交付 |
| 布尔/NULL/时间戳三项决策猜错 | 结果值错误且不报错 | 全部配置化；向客户索取一次真实响应即可锁定 |
| 客户中间件错误信封与我们不同 | 错误分支行为不一致 | 成功报文严格对齐；错误分支单独标注为「本实现约定」 |
| `og-pri`/`og-std` 未运行 | 多实例路由无法验证 | P0 阶段决定是否拉起；不影响单实例主链路 |
| openGauss-lite 能力边界 | 部分系统视图缺失 | 沿用现有 skill 已知的 lite 限制处理 |

### 需向客户确认（阻塞交付，不阻塞开发）

1. 结果集布尔渲染 `t/f` 还是 `true/false`？NULL 渲染成什么？
2. `Timestamp` 参数按秒还是毫秒解析？
3. 脚本中 String 占位符的引号由脚本作者写还是中间件补？**索取一条带 String 参数的真实脚本即可定案**（同时定案决策表第 5、6 项）
4. 接口二（执行）失败时的响应结构；`status` 的完整枚举？
5. `{{}}` 是文本替换还是绑定变量？（第 3 项拿到脚本后即可反推：若脚本写作 `= '{{name}}'`，则确认为文本替换）

---

## 11. 本方案的选型说明

采用「**先注册脚本、再按 ID 调用**」，而非「中间件后端透明代理任意 SQL」。

代价是每条 SQL 都要进脚本仓库、过校验、生成 DML——工作量实在。但这个负担是客户环境的真实负担（脚本只能走版本 DML 发布），提前吃下来，交付时才不会发现半数 skill 根本跑不了。

透明代理方案的问题在于：本机全绿、客户环境全挂，而这个落差要到联调才暴露。对以「静默失效比报错危险」为准绳的交付场景，这个方案不可取。

### 贯穿全案的一条原则：保真优先于改良

凡是「我们能做得比客户中间件更好」的地方，一律**不做**：不用绑定变量、不拦截标识符位占位符、不给异步脚本兜底、不静默截断超大结果集。

理由是同一个：本地中间件的价值全部来自「它的行为等于客户的行为」。任何单方面的改良都会让本地测试放过一类客户环境必然出现的问题，而这类问题恰恰要到交付现场才暴露——那时的排查成本比现在高一个数量级。

改良的位置在别处：注册期的风险清单、显式的错误信息、双路径一致性测试。这些不改变运行时行为，只是把本来看不见的差异**变得可见**。
