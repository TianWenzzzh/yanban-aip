# mcp-aip-gateway

**MCP ↔ AIP 适配网关 · 把全球 MCP 工具生态零改动接入 AIP 智能体互联国标生态**

English: A protocol gateway that exposes any MCP server's tools as an AIP (Agent Interconnection Protocol, China's national-standard agent interconnection system) partner — zero changes to upstream MCP servers. Think "ContextForge, but the unified endpoint speaks AIP".

> 版本 v1.2 ｜ 状态：核心循环/多上游聚合/真实 MCP 生态接入均已实测验证 ｜ Python ≥3.14 ｜ 依赖 acps-sdk v2.2.0
> 详细设计依据与实测记录见 [DESIGN.md](DESIGN.md)（所有结论均为实测验证，非文档转述）。

---

## 一、这是什么（Why）

[AIP](https://aip.openatom.tech/)（智能体互联协议）是我国智能体互联标准体系的协议族，梧桐（wt.ioa.pub）等平台基于其运行；[MCP](https://modelcontextprotocol.io) 是事实上的工具接入标准，生态里有数千个现成 server。**两边目前互不相通。**

本网关做的事只有一件：**确定性地把 MCP 翻译成 AIP**——

- 上游任意 MCP server（stdio / streamable-http）**零改动**接入
- 工具 → AIP Skill 自动翻译（含 JSON Schema → 参数摘要、调用示例自动生成，喂给 Discovery 语义检索）
- ACS 自动生成并通过官方 jsonschema 校验；自动注册、心跳探活、失联自动撤架
- 调用结果 → AIP Task 状态机映射（成功 `completed`；未知工具 `rejected` 并回带可用工具清单；异常 `failed`）
- 多上游聚合 + 命名空间隔离（`{ns}__{tool}`），AMP 全链路审计

**定位边界**：网关是确定性协议翻译层，不做意图识别、不做 LLM 编排——自然语言→工具选择的智能归 Leader 负责。

## 二、架构（三层，对齐 IBM ContextForge 骨架）

```text
                梧桐平台 / 任意 AIP Leader
                      │ AIP over HTTP(S)/mTLS（AIC 身份 + identity binding）
    ┌─────────────────▼──────────────────┐
    │  AIP Partner 层        partner.py   │  /rpc 状态机 + /health + /acs + AMP 审计
    ├─────────────────────────────────────┤
    │  翻译层（Tools Gateway）translator.py │  Tool→Skill 映射 / 参数摘要 / 示例生成
    │                                      │  命名空间隔离 / 多 server 聚合
    ├─────────────────────────────────────┤
    │  MCP 客户端层（连接治理） registry.py  │  stdio / streamable-http
    │                                      │  连接池 / 心跳探活 / 失联撤架
    └─────────┬───────────┬───────────────┘
         MCP Server A   MCP Server B  ……（零改动）
```

核心映射速览（完整表见 DESIGN.md §2）：

| MCP | AIP | 规则 |
|---|---|---|
| Server name | 命名空间 `ns` | `skill.id = {ns}__{tool.name}` |
| `Tool.inputSchema` | skill.description【输入要求】段 | properties+required → 参数表 + JSON 示例 |
| `Tool.inputSchema` | skill.examples | 按参数类型生成自然语言示例（提升 Discovery 召回） |
| `call_tool(args)` | `TaskCommand(start).dataItems` | `{"tool": name, "arguments": {...}}` |
| 工具不存在 / 异常 | `rejected` / `failed` | rejected 回带可用工具清单，供 Leader 自纠错 |

## 二·五、万桥扩展：A2A 桥（实验性，v2.0.0-alpha）

> **English (TL;DR)**: WanBridge upgrades the gateway into a tri-protocol interoperability
> hub — **GB-AIP × MCP × A2A**. It acts as a deterministic translation layer in both directions:
> remote A2A agents can be registered as upstreams (`transport = "a2a"` in `config.toml`, with
> Card-probe health & auto-demotion), and the gateway itself exposes an A2A server face
> (dynamic AgentCard, `tasks/send`/`message/send`, `tasks/get`, `tasks/cancel`).
> Both protocol faces share one registry — same capabilities, two protocols, one result.
> Cascade benchmark (v0.2, n=5): 60/60 green, one extra A2A hop costs ≈ 2-3 ms.
> See `docs/design_wanbridge_a2a.md` (design & spec provenance) and `docs/whitepaper_v0.md`.

在 MCP↔AIP 之上，万桥把网关升级为**国标×MCP×A2A**三协议互操作基础设施（白皮书：[docs/whitepaper_v0.md](docs/whitepaper_v0.md)）：

```text
   A2A 客户端                      A2A Agent（上游）
      │  AgentCard + tasks/send         ▲ AgentCard（well-known 双路径）
      ▼                                 │ tasks/send（万桥作为 A2A 客户端）
┌─────────────────────────────────────────────────┐
│  万桥网关                                         │
│  a2a_server.py（A2A 服务面：动态 AgentCard +      │
│    tasks/send/message/send 双方法名）             │
│  bridge_a2a.py（A2A 上游适配：7 态映射/弱契约声明）│
│  partner.py / translator.py / registry.py（同前） │
└─────────────────────────────────────────────────┘
      │ AIP（/rpc 状态机 + ACS）         │ MCP（stdio / streamable-http）
      ▼                                 ▼
  梧桐平台 Leader                 MCP Server 生态（零改动）
```

| 新模块 | 作用 | 设计依据 |
|---|---|---|
| `gateway/bridge_a2a.py` | A2A Agent 视作上游：AgentCard 发现、AgentSkill→AIP 弱契约如实声明、tasks/send 七态映射 | docs/design_wanbridge_a2a.md §1 |
| `gateway/a2a_server.py` | 网关自身暴露为 A2A Agent：聚合 ACS→动态 AgentCard，双方法名 JSON-RPC 入口，独立运行端口 ≥8060 | 同上 §2 |
| `tests/conformance/` | 一致性套件（规约即数据）：AIP schema/AIC/状态机 + A2A Card/七态/Part，可独立出报告 | 同上 §3 |
| `bench/bench_interop.py` | 互操作基准：跨协议任务矩阵（首轮 10/10=100%，见 docs/bench_interop_results.md） | — |
| `docs/eco_matrix.md` | 生态验证矩阵：9 上游实测（8 兼容 + sqlite 不兼容根因坐实） | — |

> ⚠️ 实验性：A2A 面锚定 v0.2-v0.3 主流形态（tasks/send + well-known），前向兼容 message/send；
> streaming/gRPC 绑定留 v2。API 可能变动，暂不建议生产依赖。

## 三、快速开始

```bash
# 依赖：Python 3.14+ / uv；acps-sdk 以本地路径依赖引入（见 pyproject）
git clone <本仓库> && cd mcp-aip-gateway
# 确保 ../ACPs-community/acps-sdk 存在（acps-sdk v2.2.0 源码）
uv sync

# 配置上游：编辑 gateway/config.toml（每上游一个 [upstreams.*] 段，支持 stdio 与 http）
uv run uvicorn gateway.partner:app --host 127.0.0.1 --port 8021

# 验证
curl http://127.0.0.1:8021/health
# {"status":"ok","upstreams":{"yanban":{"online":true,"tools":2}, ...}}
```

容器模式：`gateway/config.container.toml` + `GATEWAY_CONFIG` 环境变量切换（容器内不装 uvx，用精简上游集）。

实测规模：单实例聚合 **4 上游 17 工具**（本地 AIP partner、官方 fetch/time/git MCP server、streamable-http demo），ACS 一次通过官方 schema 校验。

### A2A 服务面（实验性）快速体验

```bash
# 独立起 A2A 面（端口 ≥8060，避开在役系统）
A2A_CONFIG=gateway/config.toml A2A_PORT=8060 uv run uvicorn gateway.a2a_server:create_app_from_env --factory --host 127.0.0.1 --port 8060

# 发现：双路径同一张动态 AgentCard
curl http://127.0.0.1:8060/.well-known/agent-card.json
curl http://127.0.0.1:8060/.well-known/agent.json

# 调用：tasks/send 或 message/send 双方法名同义
curl -X POST http://127.0.0.1:8060/ -H "content-type: application/json" -d '{
  "jsonrpc": "2.0", "id": "1", "method": "tasks/send",
  "params": {"message": {"parts": [{"type": "data",
    "data": {"tool": "<ns>__<tool>", "arguments": {}}}]}}}'
```

客户端桩：`gateway/bridge_a2a.py` 的 `fetch_agent_card` + `A2aClient.call_skill`（离线单测见 tests/）。

### 双面同进程（一个 app 同时说 AIP 与 A2A）

```bash
# 同一 app：POST /rpc 说 AIP，POST / 与 /.well-known/agent-card.json 说 A2A（能力同源）
uv run uvicorn --factory gateway.app:create_app --host 127.0.0.1 --port 8060

# A2A 面：发现 + 调用 + 任务检索（v2.0.0-alpha 新增 tasks/get、tasks/cancel）
curl http://127.0.0.1:8060/.well-known/agent-card.json
curl -X POST http://127.0.0.1:8060/ -H "content-type: application/json" -d '{
  "jsonrpc": "2.0", "id": "1", "method": "tasks/send",
  "params": {"id": "t1", "message": {"parts": [{"type": "data",
    "data": {"tool": "<ns>__<tool>", "arguments": {}}}]}}}'
curl -X POST http://127.0.0.1:8060/ -H "content-type: application/json" -d '{
  "jsonrpc": "2.0", "id": "2", "method": "tasks/get", "params": {"id": "t1"}}'
```

A2A 面审计写 `AMP_LOG_DIR/amp_access_a2a-<port>.jsonl`（AMP Access NDJSON）。
任务接口语义与错误码依据见 `docs/design_wanbridge_a2a.md` §2.3（R12 核实记录）。

## 四、质量与验证

- 单元测试 `tests/test_translator.py`（15 用例：翻译映射/双 SDK 兼容/参数摘要/示例生成/ACS 组装，离线可跑）
- 端到端脚本 `test_gateway_e2e.py`（需网关在线：未知工具拒绝、真实调用、ACS 导出）
- 量化基准（研伴团队 2026-09-05 夜间跑）：10/10 任务 100% 通过，跨智能体 RPC 48 次/轮
- 兼容性要点（踩坑实录，详见 DESIGN.md §1）：
  - MCP SDK 2.x 破坏性变更（`FastMCP`→`MCPServer`、字段 snake_case）→ 翻译器双兼容
  - stdio 的 anyio cancel scope 必须单任务持有 → 连接治理单独封装
  - streamable-http 客户端名为 `streamable_http_client` 且返回 2 元组
  - LLM 常把工具输入拼成非法 JSON → 网关宽容解析层

## 五、Roadmap

- [ ] REST 上游适配（ContextForge 的 REST→MCP 段）
- [ ] 结构化结果（ImageDataItem/StructuredDataItem）映射
- [ ] 工具级鉴权与限流（API Gateway 层）
- [ ] 多网关联邦目录

## 六、社区

欢迎 Issue/PR。提交前请先搜索是否已有重复；Bug 请附复现环境（OS/Python/acps-sdk 版本）与最小复现步骤。
项目诞生的社区反馈（对 ACPs-community 的 4 份实测 Issue 草稿）见 `docs/`（待发布）。

## License

MIT © 2026 mcp-aip-gateway contributors
