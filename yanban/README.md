# 研伴 · 科研协作智能体团队

基于 AIP 国标（GB/Z 185 / ACPs v2.2.0）的科研协作多智能体系统 + 首个 MCP↔AIP 协议适配网关。
2026 第八届全球校园人工智能算法精英大赛「智能体互联」算法主题赛参赛作品。

## 系统组成

| 组件 | 端口 | 说明 |
|---|---|---|
| 研伴 Leader | 8041 | 一句话科研任务 → 分解 → 并发编排 → 聚合报告 |
| 文献检索 Partner | 8031 | arXiv 真实检索 + GLM 综述框架 |
| 实验设计 Partner | 8032 | 研究问题 → 假设/变量/对照/指标完整方案 |
| 数据分析 Partner | 8033 | CSV → 纯 Python 真实统计（描述统计+皮尔逊相关）+ GLM 解读 |
| 论文写作 Partner | 8034 | 要点 → 学术语体段落（引用占位） |
| 模拟审稿 Partner | 8035 | 三审稿人意见（方法/写作/贡献）+ Meta 结论 |
| MCP↔AIP 网关 | 8021 | 聚合 4 个上游 MCP Server（17 工具），零改动接入 |

全部 Agent：AIC 国标身份、AMP v2.2.0 可观测（心跳+访问审计 NDJSON）、AIP 任务状态机。

## 快速开始

```bash
# 1) 配置 GLM key（OpenAI 兼容），或让 run_all 自动读取 demo-partner/.env
export GLM_API_KEY=<your-key>

# 2) 启动 5 个 Partner（一键）
uv run python run_all_partners.py

# 3) 启动 MCP↔AIP 网关（另开终端，在 ../mcp-aip-gateway）
uv run uvicorn gateway.partner:app --port 8021

# 4) 启动 Leader（另开终端）
uv run uvicorn leader:app --port 8041

# 5) 提交一个科研任务
curl -X POST http://localhost:8041/task -H "Content-Type: application/json" \
  -d '{"query": "检索 multi-agent 文献 2篇，并写一段相关工作"}'
# 轮询结果
curl http://localhost:8041/task/<task_id>
```

容器化：`GLM_API_KEY=<key> docker compose up -d`（见 docker-compose.yml）。

## 工作台 UI（webui/）

对标产品级工作台的展示层（Vite + React 18 + TypeScript strict + Tailwind，全站中文，零 mock）：

```bash
# 开发模式（:8090，/task 等代理到 8041）
cd webui && npm install && npm run dev
# 构建 + Leader 单端口托管（8041/ 即新版工作台；/ui 为旧版兜底）
cd webui && npm run build && uv run uvicorn leader:app --port 8041
# 一键演示（栈拉起 + 探活 + 自动提交演示任务）
bash demo.sh
```

- **新任务**：Hero 引导 + 三步向导（研究主题 → 范围与约束可跳过 → 确认提交）
- **流水线面板**：plan+subresults 阶段卡（状态徽章/耗时/RPC/依赖）2.5s 轮询，结束后渲染质量闭环轮次时间线
- **任务记录**：localStorage 持久化，点击回看报告
- **数据概览**：四指标卡/质量闭环对照/rubric 五维 donut/对抗样例表，数字全部来自 Leader 只读端点（/stats/bench、/stats/quality 解析 bench-results-20260917.md 与 docs/quality_experiment_data_v2.json）
- **协议演示**：AIP 面 `/rpc` vs A2A 面（AgentCard+tasks/send）左右并排等价演示 + AMP 审计尾巴
- **设置**：identity mode 与 Partner 端点表（只读）

## 质量

- E2E 测试：`GLM_API_KEY=<key> uv run pytest tests/ -v`（5 用例）
- 量化基准：`uv run python bench.py` → 诚实四指标 **Task Completion 90%（9/10）｜Partial 100%｜RPC 90%｜Coverage 92%**（bench-results-20260917.md）
- AMP 统计：`uv run python amp_report.py`

## 目录

- `orchestrator.py` / `leader.py`：Leader 编排
- `partner_base.py` + `partner_*.py` + `literature_partner.py`：Partner 集群
- `gateway_compat.py` + `gen_acs.py` + `acs/`：ACS 生成与官方 schema 校验
- `llm.py`：GLM 统一封装（重试/JSON 模式）
- `DESIGN 参考`：../mcp-aip-gateway/DESIGN.md（网关设计文档）
