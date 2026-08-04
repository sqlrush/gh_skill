# GRMP 兼容中间件 —— 架构图与接口规范图

**配套文档**：[2026-07-31-grmp-middleware.md](./2026-07-31-grmp-middleware.md)
**日期**：2026-07-31

本文用图说明：本地 openGauss 的访问是如何被封装成客户中间件接口的。

---

## 图 1：客户环境 ↔ 本机模拟的逐层对照

整个方案的立足点——每一层都有对应物，协议不变，只换实现。

```
          客户环境（不可见）                      本机模拟（我们要建的）
   ═══════════════════════════════        ═══════════════════════════════

   ┌─────────────────────────┐            ┌─────────────────────────┐
   │  agent（客户的调用方）    │            │  skill（~/gh_skill）     │
   └───────────┬─────────────┘            └───────────┬─────────────┘
               │                                      │
               │  POST + auth 头                      │  同一套报文
               │  application/json                    │  同一套报文
               ▼                                      ▼
   ┌─────────────────────────┐            ┌─────────────────────────┐
   │  GRMP 平台               │            │  grmp-mock              │
   │  <客户接入地址>           │◄─ 协议一致 ─►│  127.0.0.1:18080        │
   │  /icbc/paas/aiops/grmp   │            │  /icbc/paas/aiops/grmp  │
   └───────────┬─────────────┘            └───────────┬─────────────┘
               │                                      │
       ┌───────┴────────┐                     ┌───────┴────────┐
       │                │                     │                │
       ▼                ▼                     ▼                ▼
  script_config    实例注册表             script_config    instances.yaml
  （客户库表）      dataIp→连接串          （SQLite）        dataIp→连接名
       │                │                     │                │
       └───────┬────────┘                     └───────┬────────┘
               │                                      │
               ▼                                      ▼
   ┌─────────────────────────┐            ┌─────────────────────────┐
   │  客户 GaussDB 实例        │            │  og5 容器               │
   │  <客户数据 IP>            │            │  127.0.0.1:5433         │
   └─────────────────────────┘            │  openGauss-lite 5.0.3   │
                                          └─────────────────────────┘

   ─────────────────────────────────────────────────────────────────
   关键：dataIp 沿用客户环境的取值，本机映射到 og5。
        这样同一份 skill 配置在两边都能跑，不需要改任何调用参数。
```

---

## 图 2：本机分层结构

```
┌────────────────────────────────────────────────────────────────┐
│  skill 层    slowsql / health / topsql / ...  （13 个）          │
│              runner.run("slowsql.slow_sql", {...})              │
└──────────────────────────┬─────────────────────────────────────┘
                           │  统一入口，skill 不感知走哪条路
                           ▼
┌────────────────────────────────────────────────────────────────┐
│  common/access.py        按 config.yaml 的 driver 选路          │
└──────────┬──────────────────────────────────┬──────────────────┘
           │ driver: grmp                     │ driver: pg8000 / gsql
           ▼                                  ▼
┌──────────────────────┐            ┌──────────────────────┐
│ common/grmp_client.py│            │common/script_runner.py│
│  · 查列表→缓存        │            │  · 读同一份 YAML      │
│  · cmd_name→id 解析  │            │  · 本地渲染           │
│  · HTTP 调用         │            │  · 直接 db.query()    │
└──────────┬───────────┘            └──────────┬───────────┘
           │                                   │
           │ HTTP                              │
           ▼                                   │
┌────────────────────────────────────┐         │
│  grmp_middleware/grmp_mock/  （HTTP 服务）    │         │
│  ┌──────────────────────────────┐  │         │
│  │ server.py    路由分发         │  │         │
│  │ auth.py      auth 头校验      │  │         │
│  │ instances.py dataIp→连接名    │  │         │
│  │ registry.py  查 script_config │  │         │
│  │ placeholder.py 校验+渲染 ◄────┼──┼─────────┘  两条路径
│  │ executor.py  执行             │  │            共用渲染器
│  │ serialize.py 全字符串化       │  │
│  │ pagination.py PageInfo 18 字段│  │
│  │ envelope.py  两套信封         │  │
│  └──────────────────────────────┘  │
└──────────────────┬─────────────────┘
                   │
                   ▼
┌────────────────────────────────────────────────────────────────┐
│  common/db.py  →  pg8000  →  og5 (127.0.0.1:5433)              │
└────────────────────────────────────────────────────────────────┘
```

`placeholder.py` 被两条路径共用是刻意设计。否则双路径一致性测试比对的是两条不同的 SQL，
而不是两条不同的执行链路，测试就失去意义。

---

## 图 3：一次带参调用的完整时序

```
skill          grmp_client        grmp-mock       script_config      og5
 │                  │                  │                │            │
 │ run("slowsql     │                  │                │            │
 │  .slow_sql",     │                  │                │            │
 │  {threshold:200, │                  │                │            │
 │   limit:20})     │                  │                │            │
 ├─────────────────►│                  │                │            │
 │                  │                  │                │            │
 │      ┌───────────┤ 首次调用才走      │                │            │
 │      │           │                  │                │            │
 │      │  ① POST /common-operations   │                │            │
 │      │  {"dataIp":"<客户数据IP>",    │                │            │
 │      │   "offset":1,"limit":1000}   │                │            │
 │      │           ├─────────────────►│                │            │
 │      │           │                  ├───查全部脚本──►│            │
 │      │           │                  │◄───────────────┤            │
 │      │           │◄─────────────────┤                │            │
 │      │           │ {"code":"0","msg":"success",      │            │
 │      │           │  "result":{"total":13,"list":[...],│           │
 │      │           │   ...PageInfo 16 字段}}           │            │
 │      │           │                  │                │            │
 │      │  ② 本地解析 cmd_name→id      │                │            │
 │      │     "slowsql.slow_sql" → "527"                │            │
 │      └───────────┤ 结果进程内缓存    │                │            │
 │                  │                  │                │            │
 │                  │  ③ POST /common-operations/invoke │            │
 │                  │  {"dataIp":"<客户数据IP>",         │            │
 │                  │   "id":"527",                     │            │
 │                  │   "param":[                       │            │
 │                  │     {"param_name":"threshold_ms", │            │
 │                  │      "param_value":"200"},        │            │
 │                  │     {"param_name":"limit",        │            │
 │                  │      "param_value":"20"}]}        │            │
 │                  ├─────────────────►│                │            │
 │                  │                  │                │            │
 │                  │            ④ auth 头校验          │            │
 │                  │            ⑤ dataIp→连接名 "og"   │            │
 │                  │                  ├──取模板────────►│           │
 │                  │                  │◄───────────────┤            │
 │                  │            ⑥ 类型校验             │            │
 │                  │               "200" 匹配 ^-?\d+$  │            │
 │                  │            ⑦ 文本替换渲染         │            │
 │                  │                  │                │            │
 │                  │                  ├──真实 SQL─────────────────►│
 │                  │                  │◄──原生行（含类型）─────────┤
 │                  │            ⑧ 全字符串化序列化     │            │
 │                  │◄─────────────────┤                │            │
 │                  │ {"result":{"type":"array","data":[...]},       │
 │                  │  "task_id":"grmp-<uuid>",         │            │
 │                  │  "call_type":"sync",              │            │
 │                  │  "status":"finished"}             │            │
 │◄─────────────────┤                  │                │            │
 │  rows（全是字符串）│                 │                │            │
```

第 ② 步用 `cmd_name` 解析 ID 而不是硬编码——脚本 ID 是环境相关数据。客户接口文档里
`id=56` 是「查看数据库信息」，客户调用示例里同一个 `id=56` 却传了慢 SQL 的参数。

---

## 图 4：两个接口的报文结构

```
接口一  POST /diagnostic/agent/common-operations
════════════════════════════════════════════════════════════════

请求                              响应
┌──────────────────┐             ┌─ code : String  "0"成功 "1"错误
│ dataIp  必选 单IP │             ├─ msg  : String  中文，仅供展示
│ offset  可选 页码 │  ← 是页码    └─ result
│ limit   可选 ≤1000│    不是偏移量    ├─ total : Integer
└──────────────────┘                  ├─ list[]
                                      │   ├─ id          String
                                      │   ├─ cmd_name    String
                                      │   ├─ cmd_type    SQL|PYTHON
                                      │   ├─ cmd         String  SQL明文
                                      │   ├─ description String  ← 文档未定义
                                      │   └─ param[]              无参时 []
                                      │       ├─ param_name  String
                                      │       ├─ data_type   5种枚举
                                      │       ├─ required    Boolean 真布尔
                                      │       └─ description String
                                      └─ PageInfo 16 字段
                                          pageNum pageSize size
                                          startRow endRow pages
                                          prePage nextPage      ← 边界为 0 非 null
                                          isFirstPage isLastPage
                                          hasPreviousPage hasNextPage
                                          navigatePages(=8)
                                          navigatepageNums[]    ← 第二个 p 小写
                                          navigateFirstPage navigateLastPage


接口二  POST /diagnostic/agent/common-operations/invoke
════════════════════════════════════════════════════════════════
        ↑ 路径里没有 /dataip/{dataip}（接口文档示例是错的）

请求                              响应   ← 没有 code / msg
┌──────────────────┐             ┌─ result
│ dataIp  必选      │             │   ├─ type : "array" | "Text"
│         ← 文档漏列 │             │   └─ data : 数组 | 字符串
│ id      必选      │             ├─ task_id   : "grmp-"+UUID
│ param[] 可选      │             ├─ call_type : "sync"  ← 文档未定义
│  ├─ param_name    │             └─ status    : "finished" ← 枚举未知
│  └─ param_value   │
│     ↑ 只有这两个键 │             ⚠ 判成功必须同时校验
│       值一律字符串 │                status=="finished" 且 result 存在
└──────────────────┘                不能靠 data 为空判断
```

---

## 图 5：一条 SQL 在各层的形态变化

最直接地说明：本地 og 的访问是怎么被「包」成中间件接口的。

```
┌─ ⓪ 脚本仓库（仓库内 YAML，单一事实源）
│   scripts/registry/slowsql/slow_sql.yaml
│   ─────────────────────────────────────────────────
│   name: slowsql.slow_sql
│   sql: |
│     SELECT unique_sql_id::text, ... FROM dbe_perf.statement
│     WHERE (total_elapse_time/NULLIF(n_calls,0))/1000 > {{threshold_ms}}
│     ORDER BY ... LIMIT {{limit}}
│   params:
│     - {key: threshold_ms, type: INTEGER, required: true}
│     - {key: limit,        type: INTEGER, required: true}
│
▼  注册工具  grmp_register.py（硬拦截 + 风险标注 + 生成客户格式 DML）
│
┌─ ① script_config 一行记录（21 列，与客户表结构一致）
│   id='527'  script_name='slowsql.slow_sql'  script_type='SQL'
│   script_content='SELECT ... > {{threshold_ms}} ... LIMIT {{limit}}'
│   parameter_config='[{"key":"threshold_ms","value":"",
│                       "type":"INTEGER","autoAcquire":false}, ...]'
│   scene='AGENT'  is_valid=1  is_asyn=0
│
▼
┌─ ② skill 的调用（不含任何 SQL）
│   runner.run("slowsql.slow_sql", {"threshold_ms": 200, "limit": 20})
│
▼
┌─ ③ HTTP 报文（这一层完全是客户协议）
│   POST http://127.0.0.1:18080/icbc/paas/aiops/grmp
│        /diagnostic/agent/common-operations/invoke
│   auth: <令牌，从 GRMP_AUTH_TOKEN 环境变量读取>
│   Content-Type: application/json
│
│   {"dataIp":"<客户数据IP>","id":"527",
│    "param":[{"param_name":"threshold_ms","param_value":"200"},
│             {"param_name":"limit","param_value":"20"}]}
│                                    ↑ 整型也写成字符串
▼
┌─ ④ 中间件内：类型校验
│   "200" 匹配 INTEGER 的 ^-?\d+$   ✓
│   "20"  匹配 INTEGER 的 ^-?\d+$   ✓
│   （不匹配则直接 code:"1" 拒绝，不执行）
│
▼
┌─ ⑤ 文本替换渲染（与客户中间件同款，非绑定变量）
│   SELECT unique_sql_id::text, ... FROM dbe_perf.statement
│   WHERE (total_elapse_time/NULLIF(n_calls,0))/1000 > 200
│   ORDER BY ... LIMIT 20
│                        ↑ 到这里才是一条普通 SQL
▼
┌─ ⑥ pg8000 标准 PostgreSQL wire 协议 → og5:5433
│   （从这一层往下，和直连路径完全一样）
│
▼
┌─ ⑦ og5 返回原生行，带真实类型
│   [('3389211...', 'select * from t1 ...', 12, 45.2, 0.54), ...]
│      str            str                    int  float  float
▼
┌─ ⑧ 序列化：全部转成字符串（客户协议如此）
│   {"result":{
│      "type":"array",
│      "data":[{"unique_sql_id":"3389211...",
│               "query":"select * from t1 ...",
│               "calls":"12",        ← 原来是 int 12
│               "avg_ms":"45.2",     ← 原来是 float
│               "cpu_sec":"0.54"}]},
│    "task_id":"grmp-<uuid>","call_type":"sync","status":"finished"}
▼
└─ ⑨ skill 收到的：全字符串的字典列表，需自行转类型
```

---

## 「封装」到底封了什么

中间件在 og 前面加了三样东西，其余原封不动：

| 加的东西 | 在哪一步 | 对 skill 的影响 |
|---|---|---|
| **白名单** | ①② | 不能发任意 SQL，只能引用注册过的脚本 |
| **HTTP 一层** | ③ | 多一跳网络，多一套错误语义（HTTP 200 + `code`） |
| **类型擦除** | ③⑧ | 参数和结果全变字符串，类型信息丢失 |

第 ⑥ 步往下和直连路径**完全一样**——同一个 pg8000、同一个 og5、同一条 SQL。
所以双路径一致性测试比对的是 ①~⑤ 和 ⑧ 这几层，og 本身不是变量。

第三样「类型擦除」是 skill 改造时最容易踩的坑：直连路径拿到的 `calls` 是 `int 12`，
中间件路径拿到的是 `"12"`。skill 里凡是直接拿去做算术或排序的地方都要加转换，
否则字符串排序会得出 `"12" < "9"` 这种结果——**而且不报错**。

此项在实现计划 P5 阶段单列检查项。
