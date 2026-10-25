# 智能体互联网的路由器

## ——万桥 WanBridge：国标 × MCP × A2A 全协议互操作基础设施（白皮书 v0）

> 版本：v0（2026-09-07 深夜轮次产出）
> 代码仓：`D:\code\mcp-aip-gateway`（本地 commits 92c7672…ea35a9a；未发布，R1 红线）
> 读者假设：下一个接手的学生。所有结论可复现——命令与证据见各节引用。

---

## 0. 一句话主张

**智能体互联网缺的不是又一个协议，而是路由器。**
万桥是确定性协议翻译层：让 MCP 工具生态、A2A 智能体生态与 AIP 国标智能体在零改动的前提下互相发现、
互相调用、互相审计——并用一致性测试套件与公开互操作基准把"兼容"从口号变成可执行的规约。

---

## 1. 问题：四协议格局与"巴别塔"

智能体基础设施今天同时存在四个协议面，各自解决不同层次的问题：

| 协议 | 层次 | 解决什么 | 治理 | 万桥的态度 |
|---|---|---|---|---|
| **MCP** | 工具层 | 智能体↔工具/数据源（inputSchema 强契约） | Anthropic，事实标准 | 上游能力源：零改动接入 |
| **A2A** | 智能体层 | 智能体↔智能体（AgentCard 自描述 + 任务委托，弱契约） | Linux 基金会，150+ 组织 | 双向：既消费 A2A Agent，也被 A2A 客户端消费 |
| **AIP** | 国标智能体互联层 | 国内标准的主/被调度任务协议（ACS 发现 + AIC 身份 + 状态机交付确认） | 国标（ACPs 族） | 网关的本体形态：一个 AIP Partner |
| **AMP** | 可观测层 | 心跳 + 访问审计（谁在何时调了什么，耗时/结果码） | 国标（ACPs 族） | 全链路透传：每次调用落 NDJSON 审计 |

痛点在于：**一个机构的智能体资产往往被协议绑定**。会用 MCP 工具的团队接不进国标调度；
做了 A2A Agent 的能力进不了梧桐平台的 Discovery；反之，国标 Partner 的能力对外部 A2A 生态不可见。
现有方案（IBM ContextForge 等）证明了"网关聚合"的价值，但其统一面是 MCP；**面向 AIP 国标的统一面尚无实现**——
这就是万桥的位置（学术启发：MCP×A2A 框架 arXiv:2506.01804；工程参照：ContextForge、mcp-gateway、a2a-samples，
详见 DESIGN.md §1.3）。

---

## 2. 方法论：翻译，而不是编排

万桥的全部价值建立在一条纪律上（DESIGN.md §2 架构定位原则）：

> **网关是确定性协议翻译层——不做意图识别、不做 LLM 编排。**
> 自然语言→工具选择的智能归 Leader。翻译层一旦"聪明"，可解释性与责任边界就没了。

由此推出三条翻译方法论，全部在代码中可查：

1. **弱契约如实声明**。MCP 工具有 inputSchema，翻译成中文参数表（translator.py【输入要求】段）；
   A2A 技能没有 inputSchema——万桥**不编造参数表**，而是如实声明"接受自由文本任务描述与可选 JSON 参数，
   由上游自行解释"（bridge_a2a.py `_WEAK_CONTRACT_NOTE`）。让 Discovery 的召回差异诚实可见，
   而不是用幻觉的 schema 把它抹平。
2. **语义保留映射，状态对齐有决策记录**。A2A Task 7 态 → AIP 8 态的映射表逐态给出
   （bridge_a2a.py `A2A_TO_AIP_STATE`），其中 `input-required → awaiting-completion` 而非语义上更近的
   `awaiting-input`，理由（沿用梧桐平台已验证流程，继续信息经 message 文本透传）写在设计文档 §1.3
   并被一致性测试固化（tests/conformance/test_aip_conformance.py::TestAwaitingInputSemantics）——
   **决策可辩论，但必须留痕**。
3. **失败语义对齐 + 宽容解析**。A2A 无 Rejected 态→未知工具翻译为 failed+可用清单（消息体自纠错）；
   超时→failed（保活），连接失败→抛错走探活撤架；Part 的 type/kind、data/json 双键名全部宽容解析
   （版本差异风险，design_wanbridge_a2a.md §4）。翻译层是协议间的减震器，不是规范的法官。

**双向映射表的完整性**是翻译质量的下限：A2A↔AIP 的 AgentCard/AgentSkill/tasks-send/TaskState/Part
逐字段映射表见 `docs/design_wanbridge_a2a.md`（每行有规范出处 URL+日期），MCP→AIP 映射表见 DESIGN.md §2。

---

## 3. 万桥三件套

### 3.1 翻译引擎（双向桥 + 一个网关本体）

- **MCP→AIP**（v1.0-v1.2 已验证）：`translator.py` + `registry.py` + `partner.py`——多上游聚合、
  命名空间隔离（`{ns}__{tool}`）、探活撤架、LLM 风格宽容解析。
- **A2A→AIP**（方向一，M2）：`bridge_a2a.py`——AgentCard 双路径发现（`agent-card.json` 回退 `agent.json`）、
  AgentSkill→AIP 技能（弱契约）、tasks/send 客户端与 7 态映射。
- **A2A←网关**（方向二，M3）：`a2a_server.py`——动态 AgentCard（聚合 ACS→AgentSkill：尾段 id/首段描述/
  去内部标签）、tasks/send 与 message/send 双方法名兼容（v0.2-3 与最新规范差异）、JsonPart 一等公民入参。

证据：135 个离线单测全绿（`uv run pytest tests/ -q`，0.65s，mock 传输层，不依赖任何在役服务）。

### 3.2 一致性测试套件（规约即数据）

`tests/conformance/`（M4，62 用例）把规约从文档变成可执行数据（`spec_tables.py`）：
官方 acsSchema.json 快照动态派生必填字段、直接驱动官方 `validate_aic_format` 做非法 AIC 拒绝、
规范状态迁移表当裁判、万桥映射决策 pin 死留痕。独立运行器一键产出报告
（`uv run python tests/conformance/run_conformance.py` → `docs/conformance_report.md`，首轮 62/0/0）。
设计主张：**schema 变更即测试报警，交人工复核**——一致性套件是协议漂移的烟雾报警器。

### 3.3 互操作基准（首轮 100%）

`bench/bench_interop.py`（M6）：自举一套三进程基准栈（8060/8061/8062，全开发端口），10 个跨协议任务（连跑 3 轮全绿，时延波动 <1%）：
AIP→MCP、A2A→MCP 经万桥、双协议错误路径、双路径发现、全回环。首轮 **100% 成功**：
AIP 面 308ms/2 往返，A2A 面 90ms/1 往返，全回环产物与 AIP 面逐字节等价
（`docs/bench_interop_results.md`）。"同一能力，两种协议面，一个结果"——万桥的核心主张首轮即验证。

**基准的附赠收获**是抓出并修复了 v1.2 的多上游启动竞态（commit bb87f00）：
`registry.start()` 的全局 Event 被首个就绪上游提前放行，冷启动窗口期其余上游被误判撤架。
基准不只测性能，还测承诺——文档说"多上游聚合"，基准就要求聚合在启动第 0 秒就成立。

### 3.4 生态实证（第四件：不是套件的一部分，而是套件的前提）

9 个真实 MCP 上游实测（`docs/eco_matrix.md`）：官方 Python 系（fetch/time/git，2026.8.18）、
官方 TypeScript 系（everything/memory/filesystem/sequential-thinking，2026.8.31，经 npx 缓存直连 node 的
干净进程树方案）、本地 stdio 与 streamable-http 双传输。已知不兼容如实归档：
mcp-server-sqlite（官方归档版）死于 MCP SDK 1.x→2.x 破坏性变更（`Server.list_resources` 移除，
AttributeError 实证）——**翻译层的宽容原则救不了停更的上游，但矩阵能让这件事在接入前就被看见**。

---

## 4. 定位边界（引 DESIGN.md，评审自查 P2 已做）

万桥**不做**：意图识别、LLM 编排、工具选择、结果改写、长期记忆。
万桥**只做**：协议翻译、目录聚合、生命周期治理（探活/撤架/恢复）、审计透传。
这意味着：Leader 的智能与网关的确定性各自可测；网关永远可以替换，因为它不持有任何业务语义。
自查结论：M2/M3 实现未越界（A2A 技能的"解释权"完全留给上游 Agent；网关对入参只做契约解析不做语义猜测）。

---

## 5. 工程实录（v0 阶段的诚实账本）

| 项 | 数据 | 证据 |
|---|---|---|
| 离线单测 | 135 全绿（含 62 一致性） | `uv run pytest tests/ -q` |
| 设计文档 | 双向映射表逐字段+规范出处+反例 | docs/design_wanbridge_a2a.md（M1） |
| 生态矩阵 | 9 上游（8 兼容 + 1 不兼容坐实根因） | docs/eco_matrix.md（M5） |
| 互操作基准 | 10/10=100%，n=3 复跑全绿（时延波动 <1%） | docs/bench_interop_results.md（M6） |
| 附赠修复 | v1.2.1 多上游启动竞态 | commit bb87f00 |
| 本地 commits | M1→M6 共 6 笔（50ce6b3/69add42/aaa5874/721d7e6/7395013/bb87f00+ea35a9a） | `git log --oneline` |
| 未做 | 外网重载基准、streaming SSE、gRPC/WS 绑定、多轮基准分布 | 见路线图 |

### 5.1 v0.2 更新（2026-09-12，新机首夜）

- **A2A 上游接入注册表转正**：`transport = "a2a"` 进入 config；Card 探活/撤架与 MCP 路线对称；技能弱契约壳与 MCP Tool 同构，翻译零分叉。
- **级联回环实测**：双实例基准（8060 ↔ 8070），12 项 × 5 轮 60/60 全绿；级联一跳协议开销 **+2~3ms**（E1 13-16ms vs 同轮直连 A3 11-14ms）；A2A 单往返语义（E2 6-9ms）反优于 AIP 双往返。
- **任务接口完备**：tasks/get、tasks/cancel 按 a2a-sdk 1.1.2 核实语义实现（-32001/-32002）。
- **生态探针 v0**：协议形状探测工具（`tests/conformance/probe.py`），自家双面实例首轮 11/11 PASS——"三件套"的最后一块：翻译引擎、一致性套件、互操作基准之外，生态伙伴有了自查工具。
- 版本 2.0.0a2。


## 6. 路线图

- **v2.0-alpha（当前）**：A2A 双向桥（实验性）+ 一致性套件 + 基准 v0——即本三件套。
- **v2.0-beta**：A2A streaming（SSE；`capabilities.streaming` 字段已预留）；`input-required → awaiting-input`
  直映射（梧桐平台流程验证后切换，一致性套件已预留合法迁移断言）；fetch 等外网上游纳入基准（滑动窗口均值）。
- **v2.1**：最新 A2A 规范的 `supportedInterfaces`/`protocolBinding`（WS/gRPC 绑定）；Artifact 结构化产物
  （Product 多 DataItem 而非合并文本）；基准 n≥20 分布报告。
- **v3（愿景）**：把一致性套件反哺 ACPs 社区（对齐 Issue #3 的适配器方向），让"国标×事实标准"的
  翻译规约本身成为社区资产。

## 7. 参考与出处

- A2A：AgentCard/Task/Message/Part 概念页（agent2agent.info）与官方规范（a2a-protocol.org/latest），
  2026-09-06 抓取，逐条记录于 docs/design_wanbridge_a2a.md 头部。
- AIP/ACS：acpsSchema.json（ACPs-community/registry-server，2026-09-06 快照入 tests/conformance/fixtures/）；
  acps-sdk 2.2.0 源码（TaskState 8 态、validate_aic_format、TaskManager）。
- MCP：官方 Python SDK 2.x（实测坑位：FastMCP→MCPServer 改名、camelCase→snake_case、
  CallToolResult.isError 移除、stdio anyio 单任务持有——DESIGN.md §4 与 docs/eco_matrix.md）。
- 学术与工程参照：arXiv:2506.01804（MCP×A2A）、IBM ContextForge、mcp-gateway、a2a-samples（DESIGN.md §1.3）。