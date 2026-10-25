# 万桥 WanBridge · A2A↔AIP 桥接设计（v0.1，2026-09-06）

> 规范依据（R9 核实记录，均于 2026-09-06 抓取）：
> - AgentCard 结构与 well-known 路径：[agent2agent.info AgentCard](https://agent2agent.info/docs/concepts/agentcard/)（建议 `https://<base>/.well-known/agent-card.json`；早期版本为 `agent.json`，实现需两者兼容）
> - Task/TaskStatus/TaskState（7 态：submitted/working/input-required/completed/canceled/failed/unknown）与 TaskSendParams：[agent2agent.info Task](https://agent2agent.info/docs/concepts/task/)
> - Message（role: user|agent, parts[]）与 Part（TextPart/FilePart/JsonPart，注：新版 JsonPart 取代旧 DataPart）：[agent2agent.info Message](https://agent2agent.info/docs/concepts/message/)、[Part](https://agent2agent.info/docs/concepts/part/)
> - JSON-RPC 方法名存在版本差异：v0.2/v0.3 社区资料为 `tasks/send`、`tasks/get`；最新规范出现 `message/send` 与 `supportedInterfaces.protocolBinding`（[official spec](https://a2a-protocol.org/latest/specification/)）。**万桥服务端同时支持 `tasks/send` 与 `message/send` 两个方法名；客户端默认 `tasks/send`，可配置。**
> - 仍不确定项（实现时以兼容写法处理）：Artifact 逐字段（按 name/description/parts[] 通用写法）、`protocolVersion` 字段有无（ Card 里带一个 `protocolVersion` 扩展字段不影响旧客户端）。

## 0. 定位边界（继承 DESIGN.md）

万桥仍是**确定性协议翻译层**：不做意图识别、不做 LLM 编排。A2A 的"技能"与 MCP 的"工具"同视为上游能力，统一翻译成 AIP 技能；AIP Leader 依旧负责选择与编排。

## 1. 方向一：A2A Agent 作为上游（A2A → AIP）

### 1.1 发现与枚举

```
GET {base_url}/.well-known/agent-card.json   （404 时回退 agent.json）
→ AgentCard
```

| AgentCard 字段 | 网关内表示 |
|---|---|
| `name` | 上游命名空间 `ns`（slug 化：非 `[a-z0-9-]` 字符折叠为 `-`） |
| `description` | 上游描述（进 /health 与聚合 ACS 的元数据） |
| `url` | JSON-RPC 端点 base（tasks/send 发往这里） |
| `version` / `provider.organization` | 上游元数据 |
| `capabilities.streaming` | false→同步等待；true→后续版本 SSE（v0 不启用） |
| `authentication.schemes` | 非空且含 Bearer 时读 config 的 token（不落盘）；本版仅支持 none/Bearer |
| `defaultInputModes/OutputModes` | 记录但不影响 AIP 侧固定 text/json 模式 |

### 1.2 技能翻译（AgentSkill → AIP skill）

| A2A AgentSkill | AIP skill | 规则 |
|---|---|---|
| `id` | `id` 尾段 | `skill.id = {ns}__{a2a_skill.id}`（与 MCP 命名空间同构） |
| `name` | `name` | 原样 |
| `description` | `description` 首段 | 追加【输入要求】："该技能接受自由文本任务描述与可选 JSON 参数（JSON Part），由上游 A2A Agent 自行解释"——A2A 技能没有 inputSchema，**如实声明弱契约**，不编造参数表（与 MCP 路线的关键差异） |
| `examples` | `skill.examples` | 原样（已是自然语言，Discovery 友好） |
| `tags` | `tags` | `[ns, "A2A技能", *skill.tags]` |
| `inputModes/outputModes` | 忽略 | AIP 侧统一 text/json（沿用网关约定） |

### 1.3 调用映射（tasks/send → AIP 任务状态机）

请求（网关作为 A2A 客户端）：

```json
POST {url}
{"jsonrpc":"2.0","id":"<uuid>","method":"tasks/send",
 "params":{"id":"<uuid>","sessionId":"<uuid>",
   "message":{"role":"user","parts":[
     {"type":"text","text":"<args JSON 文本，约定 {\"tool\":\"<skill尾段>\",\"arguments\":{...}}>"}
   ]}}}
```

响应 → AIP 状态映射：

| A2A TaskState | AIP TaskState | dataItems |
|---|---|---|
| `completed` | `completed` | artifacts[].parts[] 的 text/json 依序合并为 TextDataItem（结构化留 v2） |
| `input-required` | `awaiting-completion` | status.message.parts 文本透传（Leader/用户可 continue） |
| `working` / `submitted` | `working` | — |
| `failed` / `unknown` | `failed` | 错误文本 |
| `canceled` | `failed` | "上游取消" |

超时→`failed`；HTTP 404/连接失败→探活判负、自动撤架（复用 MCP 路线的撤架机制）。

### 1.4 上游接入注册表（v2.0.0-alpha，2026-09-12 新增）

config.toml 新增 `transport = "a2a"` + `url`（AgentCard 基地址）。registry 集成语义：

- **探活**：owner task 周期 `refetch_card()`（双路径 well-known）；成功→`skills_to_tool_shells`
  把 Card.skills 包成 **Tool 同构壳对象**（name=尾段id / title / description=弱契约文案 /
  inputSchema=None / annotations=None）进 `ServerState.tools`——all_tools/resolve/build_acs 零改动；
  失败→撤架（清 tools、置 error、ACS 重建），与 MCP 路线对称。
- **调用**：`registry.call` 对 a2a 上游分发到 `A2aClient.call_skill`，返回 **CallToolResult 同构适配壳**
  `NS(content=[NS(text=result.text)], isError=…)`——partner.py / a2a_server.py 取值路径零改动。
- **状态语义定案**（壳 isError 标志）：`completed`→False（正常 awaiting-completion 流）；
  `input-required`→**False**（透传产物文本，Leader 可 continue，属正常流非错误）；
  `failed`/`canceled`/`unknown`→True；`working`/`submitted`（同步模型等不到终态）→按 failed 处理
  （文本注明"上游仍在处理"）。技能集漂移（Card skills 变化）→ 探活时刷新工具表并触发 ACS 重建。
- **连接工厂可注入**：`_connect`（MCP）/ `_a2a_client_factory`（A2A）均为实例可覆写方法——
  离线单测经此注入假会话/假客户端驱动 owner 循环；多上游启动竞态回归测试（bb87f00 还债）即走此注入。

## 2. 方向二：网关自身暴露为 A2A Agent（A2A ← AIP）

### 2.1 Agent Card（动态生成）

`GET /.well-known/agent-card.json` 与 `/.well-known/agent.json`（兼容）：

```json
{
  "name": "wanbridge-gateway",
  "description": "MCP↔AIP↔A2A 全协议互操作网关（万桥）",
  "url": "http://<self>/",
  "version": "2.0.0a1",
  "protocolVersion": "0.3.0",
  "provider": {"organization": "WanBridge contributors", "url": "<repo>"},
  "capabilities": {"streaming": false, "pushNotifications": false, "stateTransitionHistory": false},
  "defaultInputModes": ["text/plain", "application/json"],
  "defaultOutputModes": ["text/plain", "application/json"],
  "authentication": {"schemes": []},
  "skills": [ /* 聚合 ACS 的每个 skill → AgentSkill（id 去命名空间尾段 / name / description 首段 / tags 去内部标签 / examples 原样） */ ],
  "metadata": {"aic": "<网关 AIC>", "upstreams": 5, "tools": 19}
}
```

### 2.2 调用入口（双方法名兼容）

`POST /`（或 `/rpc`）：`tasks/send` 与 `message/send` 同义处理。

- 入参 message.parts：接受 TextPart（text=工具约定 JSON，与 AIP dataItems 同契约）或 JsonPart（`{"tool": "...", "arguments": {...}}` 直接给）——**JsonPart 是 A2A 侧的一等公民用法**，TextPart 保底给只会发文本的客户端
- 响应：`Task{id, status:{state: completed|failed|input-required|working}, artifacts:[{name:"result", parts:[TextPart(输出合并文本)]}], sessionId}`
- 未知工具：state=failed + message 说明（A2A 无 Rejected 态；消息体里保留可用工具清单供自纠错）
- 非法 JSON-RPC：标准 error 对象（-32601/-32602）

### 2.3 任务接口完备：tasks/get 与 tasks/cancel（v2.0.0-alpha，2026-09-12 新增）

> **R12 规范核实记录**（本机直连 spec 站仅得 SPA 导航页，降级走 PyPI 阿里镜像取官方
> a2a-sdk 1.1.2 源码为规范依据；sha256 eb9a698f…9399f9c9，抓取日期 2026-09-12）：
> - `a2a/compat/v0_3/types.py`：`GetTaskRequest.method = 'tasks/get'`（:1212）、
>   `CancelTaskRequest.method = 'tasks/cancel'`（:1125）、`TaskQueryParams{id: str 必填,
>   history_length?: int, metadata?: dict}`（:947）、`TaskIdParams{id: str 必填, metadata?: dict}`（:877）、
>   `TaskNotFoundError.code = -32001 'Task not found'`、`TaskNotCancelableError.code = -32002
>   'Task cannot be canceled'`
> - `a2a/server/request_handlers/default_request_handler.py`：`TERMINAL_TASK_STATES =
>   {completed, canceled, failed, rejected}`（:71-76，v0.3 较 v0.2 新增 rejected）；取消守卫
>   对终态任务抛 -32002（:194-198），非终态（submitted/working/input-required）可取消

**服务端语义**（万桥为同步翻译层，send 即达终态；任务表内存态、进程生命周期）：
- `tasks/send` / `message/send`：执行结果（含 failed）按 task_id 记入内存任务表
- `tasks/get`：按 id 返回任务表中的 Task；未知 id → JSON-RPC error -32001
- `tasks/cancel`：未知 id → -32001；终态（completed/failed/canceled，含 v0.3 rejected）→ -32002
  带当前状态说明；非终态 → 置 canceled 并返回 Task（为异步上游 v2 预留）
- params 缺 id / 非法 → -32602；方法名严格用 `tasks/get`、`tasks/cancel`（v0.2-3 与 v0.3 无差异，
  不做别名兼容——双方法名差异仅存在于 send，见 §2.2）

**客户端语义**（A2aClient）：`get_task(id)` / `cancel_task(id)`，只传必填 id；
协议级 error（-32001/-32002 等）以 `A2aRpcError(code, message, data)` 抛出（A2aError 子类，
与 call_skill 的"错误翻译为 failed Task"语义区分——查询/取消没有 Task 语境可翻译）。

## 2.4 流式：message/stream 与 tasks/resubscribe（v2.1，2026-09-12 新增）

> **R12 规范核实记录**（官方 a2a-sdk 1.1.2 源码，sha256 eb9a698f…，2026-09-12 阿里镜像抓取）：
> `SendStreamingMessageRequest.method = 'message/stream'`（types.py:1571-1584，v0.2 社区别名
> `tasks/sendSubscribe` 兼容接受）；事件两类：`TaskStatusUpdateEvent{context_id, final: bool,
> kind='status-update', status: TaskStatus, task_id}`（:1660-1680）、
> `TaskArtifactUpdateEvent{artifact, context_id, kind='artifact-update', task_id, append?}`（:1603-1622）；
> `TaskResubscriptionRequest.method = 'tasks/resubscribe'`（:979）。传输：SSE（`data: <json>\n\n` 帧）。

**万桥语义**（确定性翻译层，同步上游 → 流式形态 = "working 帧 → 终态帧"两段式）：
- `message/stream` / `tasks/sendSubscribe`：先发 working（final=false），上游返回后发
  artifact-update + 终态 status-update（completed/failed，final=true）；任务照常落内存任务表
- `tasks/resubscribe`：按 id 从任务表重放（status 帧 + artifact 帧 + final 帧）；
  未知 id → -32001（与 tasks/get 同语义）
- AgentCard `capabilities.streaming = true`（W5 起如实声明）
- 边界：万桥不做增量 token 流（上游为同步模型），流式粒度=任务状态级

## 3. 与一致性测试套件的关系（M4）

本设计中的"规范依据"逐条派生为可执行测试：Card 必填字段存在性、well-known 双路径、7 态状态机语义、双方法名兼容、Part 三类型解析。AIP 侧同理（ACS schema 必填字段全集/状态机非法迁移拒绝/AIC 格式）。见 `tests/conformance/`。

## 4. 版本与风险

- 万桥实现锚定：A2A v0.2-v0.3 主流形态（tasks/send + well-known card），前向兼容 message/send
- 风险：最新 official spec 引入 supportedInterfaces/protocolBinding（WS/gRPC）——v0 不实现，白皮书 roadmap 列为 v2；AgentCard 字段在不同资料间有出入——实现按"字段尽量全给、解析按可选处理"的宽容原则
