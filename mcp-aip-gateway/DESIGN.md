# MCP↔AIP 适配网关 技术设计与验证报告

> 项目：2026 AIC 智能体互联 · 研伴科研协作智能体团队
> 版本：v2.0-alpha（2026-09-12，万桥三件套：翻译引擎 + 一致性套件 + 互操作基准；A2A 双向已通）
> 状态：核心循环 ✅ 多上游聚合 ✅ 真实 MCP 生态 ✅ A2A 上游接入注册表 ✅ A2A 服务面+任务接口 ✅
>       双面同进程+AMP 审计 ✅ 级联回环基准 v0.2（n=5 全绿）✅ 生态探针 v0 ✅

---

## 0. 一句话定位

把任意 MCP Server 的工具**零改动**翻译成 AIP 国标 Partner（自动生成 ACS、自动注册、自动代理调用），
使梧桐平台上的智能体可以直接发现并调用全球 MCP 工具生态——「面向 AIP 国标的 ContextForge」。

学术依据：受 MCP×A2A 框架（arXiv:2506.01804）启发；工程参照 IBM ContextForge（3.4k★）、
mcp-gateway（命名空间隔离）、a2a-samples（A2A-MCP 集成）；与 ACPs 社区 Issue #3 的"适配器"方向同频。

---

## 1. 研究结论（全部实测验证，非文档转述）

### 1.1 MCP 侧关键事实（官方 Python SDK 2.1.1）

| 项 | 实测结论 |
|---|---|
| 客户端 API | `ClientSession.list_tools()` / `call_tool(name, arguments)` |
| Tool 字段 | `name, title, description, inputSchema(v1)/input_schema(v2), annotations` |
| **v2 破坏性变更** | `FastMCP`→`MCPServer`（`mcp.server.mcpserver`）；字段 camelCase→snake_case。**翻译器必须双兼容** |
| 传输 | stdio（本地）/ streamable-http（远程）。stdio 的 anyio 上下文**必须由单一任务持有**（cancel scope 限制） |
| inputSchema | 标准 JSON Schema（properties/required/type），是自动化翻译的核心信息源 |

### 1.2 AIP/ACPs 侧关键事实（v2.2.0 + 梧桐平台实测）

| 项 | 实测结论 |
|---|---|
| ACS 必填字段 | `aic, active, lastModifiedTime, protocolVersion, name, description, version, provider, securitySchemes, endPoints, capabilities, defaultInputModes, defaultOutputModes, skills`（来源：registry-server/app/agent/acsSchema.json，已用 jsonschema 本地校验） |
| AgentSkill 必填 | `id, name, description, version, tags`；`examples/inputModes/outputModes` 可选但强烈建议（Discovery 语义检索靠 description+examples 命中） |
| 注册流程 | aic 新智能体先放**合法格式占位 AIC**（10 段 OID），平台审核通过后分配正式 AIC（CRC 盐值在平台侧）→ `agent check` 同步 → EAB → 证书 |
| 任务状态机 | start→(working)→awaiting-completion→complete→completed；网关将 MCP 调用失败映射为 `failed`、未知工具映射为 `rejected` |
| 身份绑定 | `identity_binding_enabled` 默认 True，本地 HTTP 需显式关闭；部署态开 mTLS + expected_partner_aic |

### 1.3 业界参照（ContextForge 架构要点）

三层网关：**Tools Gateway**（上游 MCP/REST → 统一 MCP 暴露 + 联邦工具目录 + 虚拟服务器捆绑）
→ **Agent Gateway**（A2A/OpenAI/Anthropic 智能体路由）→ **API Gateway**（鉴权/限流/重试/guardrails）。
注册表模式：注册上游 server → 枚举工具入库 → 按需捆绑暴露。我们将其"统一端点"一侧从 MCP 换成 **AIP**。

---

## 2. 核心映射表（MCP → AIP）

| MCP 概念 | AIP 概念 | 翻译规则（已实现） |
|---|---|---|
| Server name | 命名空间 `ns` | skill.id = `{ns}__{tool.name}`（防多 server 工具名冲突，参照 mcp-gateway） |
| Tool.name / title | skill.id 尾段 / skill.name | title 缺省用 name |
| Tool.description | skill.description 首段 | 原文保留（中英均可，Discovery 语义检索兼容） |
| Tool.inputSchema | skill.description【输入要求】段 | properties+required → 中文必填/可选参数表 + JSON 参数示例说明 |
| Tool.inputSchema | skill.examples | 按参数类型生成自然语言示例调用（提升 Discovery 召回） |
| Tool.annotations.title | skill.tags | 附加 ns、"MCP工具" 标签 |
| call_tool(arguments) | TaskCommand(start).dataItems | `{"tool": name, "arguments": {...}}` JSON 文本 |
| CallToolResult.content[] | Product.dataItems | 文本内容合并为 TextDataItem（结构化留 v1.1） |
| 工具不存在 | TaskState.Rejected | 返回可用工具清单（帮助 Leader 自纠错） |
| 调用异常 | TaskState.Failed | 异常信息入 dataItems |

**架构定位原则（重要）**：网关是**确定性协议翻译层**——不做意图识别、不做 LLM 编排。
自然语言→工具选择的智能归 Leader（这是"协同调度"创新点的职责边界，避免两个模块互相侵蚀）。

---

## 3. 网关架构（三层，对齐 ContextForge 骨架）

```text
                    梧桐平台 / 任意 AIP Leader
                          │ AIP over HTTPS+mTLS（AIC 身份 + identity binding）
        ┌─────────────────▼──────────────────┐
        │  AIP Partner 层（协议适配网关面）      │   ← gateway/partner.py
        │  /rpc 状态机 + /health + /acs + AMP   │
        │  (start→call→awaiting-completion)    │
        ├─────────────────────────────────────┤
        │  翻译层（Tools Gateway）               │   ← gateway/translator.py
        │  Tool→Skill 映射 / 参数摘要 / 示例生成  │
        │  命名空间隔离 / 多 server 聚合(v1.1)    │
        ├─────────────────────────────────────┤
        │  MCP 客户端层（连接治理）               │   ← MCP SDK 2.1.1
        │  stdio / streamable-http             │
        │  连接池 / 健康检查 / 断线重连(v1.1)      │
        └─────────┬───────────┬───────────────┘
                  │           │
            MCP Server A   MCP Server B   ……（零改动）
```

可靠性设计（对齐 a2a-mesh / agntcy 思路，v1.1 实现）：
- 上游失联检测：心跳探活 + 失联自动从 ACS 撤下（呼应平台"智能体发现 Agent"的召回痛点）
- 调用超时→failed；重试策略可配
- AMP Access 全链路审计已接入（每次 /rpc 记录调用方 AIC/工具/耗时/结果码）

---

## 4. PoC 验证结果（2026-09-04，全部通过）

| # | 验证项 | 结果 |
|---|---|---|
| 1 | MCP server（MCPServer v2 API）stdio 握手 | ✅ |
| 2 | list_tools → 自动生成 ACS（2 个工具→2 个 skill） | ✅ |
| 3 | 生成 ACS 通过官方 acsSchema.json jsonschema 校验 | ✅（可直接提交梧桐） |
| 4 | AIP start→MCP literature_search→Product→complete | ✅ 返回真实检索结果 |
| 5 | AIP start→MCP summarize_paper→Product→complete | ✅ |
| 6 | 未知工具 → Rejected + 可用清单 | ✅（Leader 可自纠错） |
| 7 | AMP 心跳 + 访问审计 NDJSON | ✅ |

代码：`D:\code\mcp-aip-gateway\`（demo_mcp_server.py / gateway/translator.py / gateway/partner.py / test_gateway_e2e.py / gen-acs.json）

**过程中趟平的坑（写报告的"踩坑与解决"素材）：**
1. MCP SDK 2.x `FastMCP`→`MCPServer`、camelCase→snake_case（网上资料多为 v1，已双兼容）
2. stdio_client 的 anyio cancel scope 必须单一任务持有（网关用"owner task"模式管理连接生命周期）
3. 梧桐 ACS schema 必填字段比教程示例多（aic/lastModifiedTime 等），本地 jsonschema 校验先行可避免提交被拒

---

### v2.0.0-alpha 夜班新增坑位（2026-09-07，万桥冲刺）

- **多上游启动竞态**：`registry.start()` 的全局 `asyncio.Event` 被首个就绪上游提前 set，
  其余上游初始化期即被 /health 判为撤架——已改逐上游 ready Event（commit bb87f00，
  由互操作基准首轮假失败取证发现）
- **MCP SDK 2.x 移除 `CallToolResult.isError`**：空 content 工具结果曾致 AIP 面 500——
  统一 `call_result_is_error` 双键名 helper（commit 533820f）
- **`cmd /c npx` 作 stdio 命令会挂死**（cmd→npx→node 三层进程树管道收不干净）：
  正确姿势是 npx 预热后直连 `node <npx缓存>/.../dist/index.js`（docs/eco_matrix.md §二）
- **PyPI 同名异物**：`mcp-server-memory` 与官方 TS 版 server-memory 无关，勿用

## 5. 路线图（按比赛时间倒排）

| 阶段 | 内容 | 对应评分 | 状态 |
|---|---|---|---|
| v1.0（第 2 周） | 单上游翻译 + AIP 代理 + ACS 生成 + schema 校验 | 创新性 | ✅ 完成 |
| v1.1（第 3 周前） | 多 MCP server 聚合（config.toml 注册表）；探活+失联自动撤架；命名空间全局目录；**接真实 MCP 生态（官方 mcp-server-fetch，uvx 安装零改动）** | 创新性（生态打通规模） | ✅ 完成（http 传输待补） |
| v1.2（第 3 周） | 更多真实生态（filesystem/arXiv 等）；LLM 增强描述质量 | 创新性+应用成效 | ⬜ |
| v1.3（第 4 周） | 注册梧桐（复用文献 Partner 的注册流程）；mTLS + identity binding；AMP 接平台 Monitor | AI 技术应用 | ⬜ |
| 评测（第 5 周） | 任务成功率/时延/调用步数量化（参照 MCP-Universe） | 应用成效 | ⬜ |

### v1.1 实测记录（2026-09-04）

- 双上游同时在线：yanban（2 工具）+ fetch（1 工具，官方真实生态），聚合 ACS 3 skills 过 schema 校验
- 经 AIP 协议调用 `fetch__fetch` 抓取 wt.ioa.pub 成功（真实 MCP 生态 ↔ AIP 国标全链路）
- 撤架机制：上游连接异常时自动从聚合 ACS 移除（owner 任务退出即撤架），/health 可观测

### v1.1 新增坑位记录

- MCP `session.initialize()` 握手不可省略（缺省则服务端拒绝 list_tools："Invalid request parameters"）
- Windows 子进程 PATH 无 python/uvx：stdio 上游 command="python" 一律解析为 sys.executable；uvx 首装需注入 UV_DEFAULT_INDEX 镜像
- anyio ExceptionGroup 需逐层解包才能看到根因（已实现）

---

## 6. 与评分表的对应关系

- **创新性 25**：首个面向 AIP 国标的 MCP 适配实现（学术：MCP×A2A 启发；社区：Issue #3 同频；生态：ContextForge 骨架国产标化）
- **AI 技术应用 20**：网关本身是 AIP Partner（mTLS+AIC 身份+AMP 可观测），Leader 经 Discovery 发现它即可用全部 MCP 工具
- **总结与展望 10**：网关数据（工具数/调用量/成功率）+ 对"标准融合"的前瞻思考
