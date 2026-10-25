# WanBridge v2.0 Release Notes（草稿 · 待发布物料 R14）

> 发布时机由项目所有者决定；本文件为 GitHub Release 直接粘贴版草稿。

## WanBridge v2.0.0-alpha.2 — 闭环与探针（2026-09-12）

万桥（WanBridge）把 MCP↔AIP 适配网关升级为**国标 × MCP × A2A 全协议互操作基础设施**。
v2.0 两夜冲刺把"各自成立的 MVP 三件套"推进为"互相咬合的闭环 + 可对外的探针"。

### 亮点

1. **A2A 上游真正接入注册表**：`config.toml` 一行 `transport = "a2a"` 即把远端 A2A Agent
   变成上游——AgentCard 周期探活、失联自动撤架、技能弱契约聚合；AIP 与 A2A 两个协议面
   共享同一能力目录：**同一能力，两种协议，一个结果**。
2. **级联回环基准 v0.2（n=5）**：双万桥实例级联（A2A 客户端 → 万桥 → 万桥 → MCP），
   60/60 全绿；**级联一跳协议开销仅 ≈2-3ms**——翻译层不主导时延。
3. **生态探针**：`probe.py` 一条命令探测任意 A2A/AIP 端点的协议形状一致性
   （11 项检查），生态伙伴接入前可自查。

### 其它

- tasks/get、tasks/cancel（语义核实自官方 a2a-sdk 1.1.2，记录见设计文档 §2.3）
- 双面同进程（`gateway/app.py`）：AIP /rpc 与 A2A / 共 app 共 registry；A2A 面 AMP 审计
- 离线单测 137→171；多上游启动竞态回归测试（bb87f00 还债）
- httpx 升主依赖；版本 2.0.0a2

### 诚实账本

- streaming SSE / gRPC 绑定 / supportedInterfaces：v2 路线图（设计文档 §4 已声明）
- 外网型上游未入基准（本机无代理网络）
- 基准 n=5（脚本可重复，欢迎在更好网络环境复跑）

### 验证

```bash
uv run pytest tests/ -q                             # 171 passed
uv run python tests/conformance/run_conformance.py  # 62 passed
uv run python bench/bench_interop.py                # 12 项 × 5 轮 全绿
uv run python tests/conformance/probe.py --target <url> --profile all
```
