# 研伴现状架构图（ARCHITECTURE_CURRENT）

> 生成：2026-09-19，研伴 2.0 Phase 0 审计（分支 feature/companion-2.0）
> 基线复现：yanban 34 passed / 万桥 192 passed / conformance 62 passed ✅

## 1. 仓库布局

```text
code/
├── yanban/                  # 研伴主系统（Python 3.14 + FastAPI + uv）
│   ├── leader.py            # Leader API(8041)：POST /task、GET /task/{id}、/ui、/health
│   │                        #   + 只读扩展端点：/stats/bench /stats/quality /amp/latest
│   │                        #   /identity /protocol/aip /protocol/a2a（工作台用）
│   │                        #   + webui/dist 静态托管（8041/ 即工作台，/ui 旧版兜底）
│   ├── orchestrator.py      # 编排核心：意图分解(LLM)→SOP/workflows 匹配→DAG 执行→聚合→质量闭环
│   ├── literature_partner.py# 文献 Partner(8031)：多源检索(sources.py) + /group/rpc 群组模式
│   ├── partner_base.py + partner_experiment/analysis/writing/review.py  # 其余 4 Partner
│   ├── sources.py           # multi_source_search：Crossref(含中文期刊·被引数) + arXiv 并行
│   ├── quality_gate.py      # 质量闭环：模拟审稿→按意见修订（≤3 轮）
│   ├── metrics.py / judge.py# 诚实四指标 / rubric 五维评审器
│   ├── llm.py               # GLM 统一封装（chat/chat_json/重试/150s 超时/LLM_MOCK 开关[2.0 新增]）
│   ├── workflows.toml       # SOP 模板：paper-pipeline（literature→experiment→writing→review DAG）
│   ├── partners.toml        # Partner 注册表（endpoint/compose_endpoint/skill_hint）
│   ├── webui/               # 工作台前端：React 18 + Vite 6 + TS strict + Tailwind（Node 24）
│   │   └── src/pages/       # Workbench(三步向导+流水线面板) / TaskHistory / DataOverview
│   │                        # / ProtocolDemo / Settings —— 五页，构建产物 dist 由 Leader 托管
│   ├── demo.sh              # 一键演示（栈拉起+探活+自动提交 SOP 任务）
│   ├── docker-compose.yml   # 7 服务编排（build context=上级目录——相对布局不可变）
│   ├── tests/               # pytest 34 passed（5 deselected）
│   └── data/                # [2.0 新增，gitignore] SQLite + PDF 缓存
├── mcp-aip-gateway/         # 万桥网关（8021）：MCP↔AIP 翻译 + A2A 双面 + /tools/search 混合检索
│   └── tests/               # 192 passed + conformance 62 passed
├── ACPs-community/          # 上游开源（acps-sdk / acps-cli / registry-server）——只读依赖
└── team-repo/               # 私有 GitHub 仓（docs/competition/ 比赛文档与证据镜像）
```

## 2. 运行时拓扑

```text
用户 ⇄ 工作台 webui (8090 dev / 8041 静态)
        ↓ POST /task {query}
Leader(8041) orchestrator.py
  ├─ LLM 分解（llm.py → glm-4-flash）或 workflows.toml SOP 匹配（确定性 DAG）
  ├─ DAG 执行（拓扑序；上游产物注入下游；依赖失败→skipped）
  │    ├─ literature(8031) ← sources.py: Crossref+arXiv 多源并行
  │    ├─ experiment(8032) ├─ analysis(8033) ├─ writing(8034) └─ review(8035)
  │    └─ mcp_gateway(8021)：AIP /rpc 面 + A2A 面同进程，MCP 上游 stdio/http/a2a
  ├─ LLM 聚合 → quality_gate（审稿→修订≤3 轮）
  └─ 状态内存态（self.tasks 字典——重启即失，2.0 引入 SQLite 解决）
```

## 3. 关键机制清单（2.0 必须尊重的既有行为）

| 机制 | 位置 | 2.0 约束 |
|---|---|---|
| AIC 身份 | literature_partner.py:51（现读 `grep -n '^PARTNER_AIC = '`；现役那颗尾号 …1DHO，轮 309 第二次身份迁移后 …SCLPMS 与 …1022 都已随版本作废——本格此前抄的还是旧值，本轮由引用门那条红牵出） | **冻结**：2.0 不改 AIC/端点/证书（改的是指针与抄本，不是身份） |
| 群组模式 /group/rpc | literature_partner.py | 冻结（比赛资格线依赖） |
| TLS 链 | 服务器 stunnel(8031 https)→容器 http(127.0.0.1:18031) | 冻结 |
| 质量闭环 | quality_gate.run_gate（审稿 verdict→修订） | 保留，2.0 只读复用 |
| 诚实指标 | metrics.compute_metrics（四指标口径） | 保留 |
| 工作台五页 | webui/src（Workbench/TaskHistory/DataOverview/ProtocolDemo/Settings） | 保留为 classic；2.0 新增 /companion 路由，VITE_DEFAULT_UI 切换默认页 |
| 静态托管 | leader.py 挂载 webui/dist（html=True） | 2.0 构建后重新 dist 即可 |
| 只读端点模式 | leader.py 尾部追加式端点 | companion API 沿用同模式（挂载新 router） |
| LLM_MOCK | llm.py（2.0 Phase 0 新增） | 测试一律 MOCK；真实调用预算 ≤300 次/全夜 |

## 4. 已知缺口（2.0 要补的）

1. **无持久化**：orchestrator.tasks 内存字典——任务/会话重启即失。
2. **无用户/项目/记忆概念**：无 users/projects/papers_feedback 表。
3. **检索是函数不是引擎**：sources.py 直返列表，无 Provider 抽象/去重/排序/推荐理由/多轮收敛。
4. **对话是单发任务**：/task 一问一答，无反问/状态机/上下文延续。
5. **无 PDF 能力**：无 resolver/下载/解析/阅读。
6. **前端无研究工作台**：五页是任务视角，无对话式主入口/论文卡片/划词交互。

## 5. 风险与注意

- U 盘 FAT32：git 慢（4 分钟级）、无权限位；webui/node_modules 与 .venv 已 gitignore。
- Windows：curl 中文 body 必须 UTF-8 文件；控制台乱码先 `export PYTHONIOENCODING=utf-8`。
- 试用小实例 OOM 前科：云端与本地构建勿并行大任务。
- 梧桐链路（叮当/ACS/stunnel/AIC）冻结：2.0 全部改动不得影响 8031 对外行为与 AIC 声明。
