# 万桥 × 研伴 · 全面优化报告与迭代规划（2026-09-12）

> 输入：Codex 问题分析（6 大问题）+ 智能体互联国标体系讲解（4 图）+ 生态调研（GitHub/官方文档/论文）
> 核实方式：全部问题逐条对照本地代码实证；调研来源见文末链接
> 结论先行：**Codex 六条全部属实**。本报告给出逐条方案、国标缺口映射、生态对标与四夜迭代路线（v4 任务书已备）。

---

## 一、Codex 六大问题核实结论（逐条对码）

| # | 问题 | 核实 | 证据位置 | 严重度 | 状态 |
|---|---|---|---|---|---|
| ① | 梧桐 Discovery 自动发现是假的：Leader 用 partners.toml 写死 6 个端口，无任何调用 Discovery Server 的代码 | ✅ 属实 | `yanban/leader.py:22`（CONFIG=partners.toml）、`orchestrator.py`（静态端点表） | ★★★ 比赛档次问题 | **v4-W2** |
| ② | 断线自动重连未实现：撤架✅，但 `_owner()` 异常后结束，上游恢复需重启网关 | ✅ 属实 | `gateway/registry.py::_owner`（修复前无循环） | ★★★ 现场演示翻车点 | **✅ 已修** `f0f1224`（指数退避重连循环 + 2 例回归，178 passed） |
| ③ | 论文全流程 SOP 实为并行扇出：asyncio.gather 并发全部 stage，下游拿不到上游产出，Review 审写死结论 | ✅ 属实 | `yanban/orchestrator.py`（gather 模式） | ★★★ 叙事硬伤 | **v4-W3** |
| ④ | "10/10=100%"水分：成功定义=Leader completed+≥1 子任务 completed，失败子任务也算成功 | ✅ 属实 | `yanban/bench.py` 判定口径 | ★★★ 答辩风险 | **v4-W4** |
| ⑤ | 质量闭环只有形没有验：MAX_ROUNDS=1，改后不再审，无法证明修改有效 | ✅ 属实 | `yanban/orchestrator.py` | ★★ 论文级创新点缺失 | **v4-W5** |
| ⑥ | mTLS+AIC 可信身份是待接入态：identity_binding_enabled=False 遍地，AIC 为占位码 | ✅ 属实 | `gateway/partner.py:162`、各 partner | ★★（外部依赖梧桐审核） | **v4-W6** |

**Codex 额外两条口径建议**（采纳）：宣称从"首个 MCP↔AIP 适配网关"改为"**面向 GB/Z 185—2026 智能体互联标准的 MCP 工具生态自动适配网关**"；项目定位升级为"**研伴 Mesh——基于 AIP 国标的异构智能体动态发现与 MCP 工具生态可信协同平台**"（科研=验证场景）。

---

## 二、国标体系对照：万桥缺什么（对照 4 张体系图）

智能体互联国标体系 = 身份码节点管理平台 → 自治域互联平台（注册/身份凭证/发现三模块）→ CA 根证书平台 → 根信息查询平台；设计原则 = **可信、可靠、中立、简洁**；典型场景中每个智能体的内部结构 = **身份维护 / 描述维护 / 互联鉴权 / 智能体交互 / 工具访问** 五层。

万桥/研伴对国标五层的覆盖现状：

| 国标智能体内部层 | 万桥现状 | 缺口 | 对应问题 |
|---|---|---|---|
| 工具访问 | ✅ MCP stdio/http + A2A 上游，三传输统一治理 | — | — |
| 智能体交互 | ✅ AIP /rpc 面 + A2A /面（双面同进程），tasks/get/cancel | streaming SSE（message/stream、tasks/resubscribe）未做 | 新增 W6 |
| 描述维护 | ✅ ACS 动态聚合 + AgentCard 动态生成（双路径） | extended card / 安全 scheme 字段未进 Card | W6 |
| 互联鉴权 | ⚠️ mTLS 只在 ACS securitySchemes 里声明；Bearer 已支持（a2a 上游）；identity_binding 全关 | AIC 身份校验流程未接（依赖梧桐审核/EAB 证书） | ⑥/W7 |
| 身份维护 | ❌ 占位 AIC 码 | 同上 | ⑥/W7 |
| （国标新增）发现 | ❌ 无发现消费端 | **DiscoveryRequest/ADP 协议客户端未实现**——这是把"工具目录"升级为"生态发现"的关键 | ①/W2 |

**关键洞察**：国标的"发现信息查询平台"与 Codex 问题①是同一件事——**发现不是功能，是国标五大平台的核心枢纽**。而本地 `ACPs-community/discovery-server` 已经给出官方实现：`POST /acps-adp-v2/discover`（DiscoveryRequest：type=explicit/exploratory/trending/filtered + query + filter + 转发链字段；语义匹配用 BGE-M3 embeddings——正是 RAG-MCP 模式的官方版）。**Leader 接上它，问题①⑤（工具多选错）一并解决。**

---

## 三、行业痛点 × 生态对标（为什么这些优化方向是对的）

国标宣讲材料点名的行业痛点，与头部项目的解法一一对应：

| 行业痛点（国标材料） | 头部项目解法 | 万桥采纳 |
|---|---|---|
| MCP 工具多时性能下降、错误选择 | **RAG-MCP**：工具描述向量化，语义检索 top-k（AWS/阿里云 AI Gateway 语义检索均已产品化）；**FastMCP Tool Search transform**：用搜索接口替代全量 tools/list | W2：Leader 先走梧桐 Discovery（官方语义匹配），网关侧再补 /tools/search 兜底（W6） |
| MCP 无身份验证、提示注入 | 网关层统一认证 + 治理（**IBM ContextForge**：MCP+A2A+REST 统一控制面、认证/RBAC/限流/审计；**agentgateway** CNCF 方向同型） | 万桥=轻量版 ContextForge：已有命名空间隔离/撤架/AMP 审计/探针；补 AIC 身份（W7） |
| A2A 无可信机制、点对点不支持大规模 | A2A 官方路线：**AgentCard capabilities 声明 + SSE 流式（message/stream）+ tasks/resubscribe 断线重挂 + pushNotificationConfig webhook**；国标：自治域+CA 信任链 | W6：流式+resubscribe；W7：梧桐 CA/EAB |
| 断线恢复 | 所有生产级网关的标配是**重连循环+退避**（我们已补齐 f0f1224） | ✅ |

生态参照系（对标定位用，非依赖）：IBM ContextForge（Apache-2.0，功能最全的统一网关）、PrefectHQ FastMCP（Tool Search）、ansible/mcp-gateway（多 server 聚合）、agentgateway（CNCF）、a2a-python（官方 SDK，streaming/resubscribe 参考实现）、本地 acps discovery-server（ADP 协议官方实现）。万桥的差异化 = **AIP 国标原生 + 确定性翻译（不编排）+ 三传输上游统一治理 + 弱契约如实声明**——这四点 ContextForge 都没有。

---

## 四、优化方案明细（每项含验收标准）

### W1 ✅ 已完成：上游断线自动重连（问题②）
owner 循环化 + 指数退避（2s→30s），撤架语义不变；回归测试：失败→撤架→重连→自动上架、持续失败不崩溃 owner 不死。commit `f0f1224`，178 passed。

### W2 Leader 真发现（问题①，最高优先）
1. `yanban/discovery.py`：DiscoveryClient（httpx），`discover(query, limit, filter)` → `POST {base}/acps-adp-v2/discover`，解析候选 Agent+ACS 端点；离线测试用 MockTransport。
2. `leader.py`：partners 解析器改三层——`partners.toml`（静态兜底）+ `DISCOVERY_URL` 环境变量开启动态发现 + **能力匹配器**（任务分解出的子任务→Discovery query→候选→ACS 能力核对→动态端点）。保留静态模式（DISCOVERY_URL 为空=现状，不破坏比赛演示）。
3. 验收：一条查询走"Leader 理解任务→生成能力需求→Discovery 搜索→候选 ACS 匹配→动态调用未知 Partner"全链（对假 Discovery Server 的离线测试 + 对本地 discovery-server 的联测脚本）；partners.toml 删掉一个 Partner 后系统仍能从 Discovery 找到它。

### W3 SOP 改 DAG（问题③）
`orchestrator.py` 重构：stage 显式声明 `inputs: [上游 stage 产物键]`，拓扑排序执行（无依赖的仍并行），产物经 `stage_outputs[key]` 注入下游 prompt；审稿 verdict（accept/minor/major/reject）驱动条件边（major/reject→修订→再审，MAX_ROUNDS=3）。验收：论文流水线 demo 中 Writing 的输入包含 Literature 产物文本（测试断言）；审稿意见引用的是上一 stage 真实输出。

### W4 基准四指标重做（问题④）
`bench.py` 判定口径改：TaskCompletionRate（全部必要子任务 completed）/ PartialCompletionRate（≥1 有效产物）/ RPCSuccessRate（成功 RPC/总 RPC，AMP 日志统计）/ CapabilityCoverage（成功能力数/请求能力数）。输出四行指标+子任务明细表，**不再输出单一"成功率 100%"**。验收：旧口径下"literature=Failed 也 Success"的用例，新口径下 TaskCompletion=8/10=80% 这类诚实数字。

### W5 质量闭环实验（问题⑤）
MAX_ROUNDS=3；每轮 Review 产出质量分；跑三组对照（单 Agent / 多 Agent 无闭环 / 完整闭环）×N 篇，产出对比表（平均质量分/幻觉率/完整度）。验收：实验数据表进 docs/，数字可复现（固定 seed+模型）。

### W6 网关流式+工具检索（国标"交互"层补全）
1. A2A 面 `message/stream`（SSE，TaskStatusUpdateEvent 帧）+ `tasks/resubscribe`；AgentCard capabilities.streaming=true。参考 a2a-python 官方实现。
2. 网关 `/tools/search`：聚合目录的关键词+可选向量检索 top-k（先 BM25/关键词，向量留接口），缓解"工具多选错"。
3. 验收：探针新增流式检查项；openai 客户端 stream=true 可消费。

### W7 AIC 可信身份接入（问题⑥，外部依赖）
梧桐审核通过 → EAB 证书 → mTLS；identity_binding 分环境开启（demo 关/比赛开）。被动依赖外部审核进度，代码侧预备 config 开关+证书加载路径即可。

---

## 五、迭代路线（v4 夜班任务书已按此撰写）

- **第 1 夜（v4）**：W2 真发现（核心）+ W3 DAG + W4 四指标——全部落在研伴仓，直接回应 Codex ①③④
- **第 2 夜（v5）**：W5 质量闭环实验 + W6 流式与工具检索——落在万桥仓+研伴仓
- **第 3 夜（v6）**：W7 可信身份（视梧桐审核进度）+ 演示视频物料 + 压测
- 持续：每夜 bundle 备份、PROGRESS/晨报、诚实账本

---

## 六、调研来源

- A2A 官方：Streaming & Asynchronous Operations（message/stream、tasks/resubscribe、pushNotificationConfig、capabilities 标志）https://a2a-protocol.org/latest/topics/streaming-and-async/
- IBM ContextForge：https://ibm.github.io/mcp-context-forge/ ｜ https://github.com/IBM/mcp-context-forge
- FastMCP Tool Search：https://gofastmcp.com/servers/transforms/tool-search
- RAG-MCP（工具描述向量库+语义 top-k，准确率 ~+200%）：https://aws.amazon.com/cn/blogs/china/combining-rag-and-mcp-in-amazon-bedrock-to-effectively-alleviate-prompt-bloom/ ｜ arXiv《A Vector-Based Approach to MCP Tool Selection》
- 阿里云 AI Gateway MCP 语义检索：https://help.aliyun.com/zh/api-gateway/ai-gateway/user-guide/semantic-retrieval
- agentgateway（CNCF，MCP/REST/A2A 统一网关）：https://blog.mygraphql.com/zh/posts/ai/ai-devops/agent-gateway/agentgateway-impl/
- MCP 网关横评（2026）：https://www.truefoundry.com/blog/best-mcp-gateways ｜ https://qveris.ai/zh-cn/guides/mcp-gateway
- 本地官方实现：ACPs-community/discovery-server（ADP 协议 /acps-adp-v2/discover + BGE-M3 语义匹配）、registry-server、ca-server
