# Changelog

## v2.0.0-alpha.2 (2026-09-12) — 闭环与探针

- **A2A 上游接入注册表（方向一转正）**：config 支持 `transport = "a2a"` + Card 基地址；周期 fetch Card 探活、失败撤架；Card.skills 包成 Tool 同构壳进 tools 表（all_tools/resolve/build_acs 零改动）；`registry.call` 返回 CallToolResult 同构适配壳（isError 语义：failed→true，input-required 透传→false）——AIP 面/A2A 面取值路径零改动
- **A2A 任务接口完备**：tasks/get、tasks/cancel（语义依据官方 a2a-sdk 1.1.2 核实，R12 记录见 design_wanbridge_a2a.md §2.3）；内存任务表；未知 id -32001、终态取消 -32002；客户端 `A2aClient.get_task/cancel_task` + `A2aRpcError`
- **双面同进程**：`gateway/app.py`——AIP /rpc 与 A2A /+well-known 共 app 共 registry；A2A 面 AMP 审计（amp_access_a2a-<port>.jsonl）
- **互操作基准 v0.2**：级联回环 E 组（8070 上游 A2A Agent 双实例栈）；12 项 × 5 轮 = 60/60 全绿；级联开销 E1−A3 ≈ +2~3ms（docs/bench_interop_results.md）
- **生态探针 v0**：`tests/conformance/probe.py`（--target/--profile/--out，只测协议形状）；自家双面实例首轮自测 11/11 PASS（docs/probe_self_report.md）
- **质量**：新增离线单测 34 例（壳对象/生命周期/适配壳/竞态回归 bb87f00/任务接口/双面）；测试总数 137→171；stop() 显式 cancel 修复探活循环拖尾；httpx 升主依赖
- 版本号 2.0.0a2

## v2.0.0-alpha (2026-09-07) — 万桥 WanBridge

- **A2A 上游适配（方向一，实验性）**：`gateway/bridge_a2a.py`——AgentCard 双路径发现（agent-card.json 回退 agent.json）、AgentSkill→AIP 技能（弱契约如实声明，不编造参数表）、tasks/send 七态→AIP 状态映射、超时/连接错误差异化语义（保活 vs 撤架）
- **A2A 服务端暴露（方向二，实验性）**：`gateway/a2a_server.py`——动态 AgentCard（聚合 ACS→AgentSkill）、tasks/send 与 message/send 双方法名兼容、JsonPart/TextPart 双通道入参、独立运行形态（端口 ≥8060）
- **一致性测试套件**：`tests/conformance/` 62 用例（官方 acsSchema 派生校验/官方 AIC 校验器驱动/规范状态迁移表/awaiting-input 语义固化/A2A Card·七态·Part），独立运行器产出 docs/conformance_report.md
- **互操作基准 v0**：`bench/bench_interop.py` 跨协议 10 任务（AIP→MCP / A2A→MCP 经桥 / 错误路径 / 全回环），首轮 100% 全绿（docs/bench_interop_results.md）
- **生态验证矩阵**：`docs/eco_matrix.md` 9 上游实测（含官方 TS 系 4 件套与 sqlite 不兼容根因）；通用冒烟探针 `bench/smoke_mcp_server.py`
- **白皮书 v0**：`docs/whitepaper_v0.md`《智能体互联网的路由器》
- **修复 v1.2.1**：多上游启动竞态——首个就绪上游提前放行 start()，其余上游初始化期被误判撤架；改为逐上游 ready Event
- 版本号 2.0.0a1；新增 `bridge` extra（httpx，A2A 桥传输层）

## v1.2.0 (2026-09-05)

- 新增 streamable-http 传输上游（mcp SDK 2.x `streamable_http_client`）
- 新增官方 mcp-server-git 上游接入（12 工具，限定仓库模式）
- 新增官方 mcp-server-time 上游（2 工具）；聚合规模达 4 上游 17 工具
- 宽容解析：LLM 拼接式非法工具输入自动纠正（Rejected→Completed）
- 上游失联自动撤架机制（ACS 实时收缩，防 Discovery 召回到死节点）
- 容器化配置支持（GATEWAY_CONFIG / config.container.toml）
- AMP 全链路审计（调用方 AIC/工具/耗时/结果码）

## v1.1.0 (2026-09-04)

- 多上游聚合与命名空间隔离（`{ns}__{tool}`）
- ACS 自动生成并通过官方 jsonschema 校验
- PoC 全链路验证：注册→发现→调用→状态机映射

## v1.0.0 (2026-09-03)

- MCP→AIP 协议翻译核心：Tool→Skill 映射、参数摘要与调用示例生成
- AIP Partner /rpc 状态机（start→working→awaiting-completion→completed/failed/rejected）
