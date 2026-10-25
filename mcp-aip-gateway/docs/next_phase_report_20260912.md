# 下阶段研究报告与计划书（v5/v6）——梧桐审核通过后的全自主任务图谱

> 触发：梧桐审核 APPROVED（2026-09-12 用户确认）｜ 比赛截止 2026-10-15（剩余 33 天）
> 侦察依据：yanban-registry 本地状态、acps-cli CA 命令面源码、acps-infra 基础设施、acps-sdk 身份绑定实现
> 结论先行：审核通过把 Codex 问题⑥（可信身份）从"待外部"解锁为"可自主完成"，且它是答辩差异化最强的一张牌——**可信闭环（AIC 身份 + mTLS 证书 + AMP 审计）是国标"可信"第一设计原则的完整落地，竞品普遍缺失**。

---

## 一、形势变化分析：审核通过解锁了什么

### 1.1 解锁链
```
审核 APPROVED
  ├─ acps-cli agent check → 读回正式 AIC（替换占位身份码 YNBAN1.ANA001…）
  ├─ acps-cli cert eab fetch → EAB 凭证（keyId/macKey/aic）
  ├─ acps-cli cert issue（ACME）→ Agent 证书（mTLS 材料）
  ├─ identity_binding_enabled=True 的技术前提全部就绪
  └─ 演示叙事升级："可信接入全链路现场演示"（杀进程恢复 + 伪造身份被拒）
```

### 1.2 侦察事实（2026-09-12 实测）
- `yanban-registry/`：acps-cli.toml + acs-yanban-literature.json + receipt（**仍是 09-04 的 PENDING 快照**，需重新 check 读回正式 AIC）
- `acps-cli/acps_cli/ca/commands.py`：完整 ACME 客户端实现；e2e 测试（test_atr_eab_workflow.py）给出标准流程：`agent check → cert eab fetch --out eab.json（keyId/macKey/aic）→ cert issue --eab-file …`（ACME 申请 Agent 证书）
- acps-sdk 身份绑定：`assert_sender_matches_peer`（partner.py 已有调用点，identity_binding_enabled=False 是唯一闸门）
- `acps-infra/dev-infra/`：官方开发基础设施（discovery-server 所需的 Postgres 等，运行不改）
- GLM key 有效（`~/code/yanban/.env`，embedding API 可用——/tools/search 向量版零大下载）

### 1.3 风险与边界
- **EAB macKey 与证书私钥是新的密钥类资产**：必须落 `yanban-registry/secrets/`（gitignore），升级红线 R19
- 平台侧网络连通性未知（审核通过≠CLI 可达）：身份链路所有平台交互步骤设计"每 60 分钟重试+其余任务并行"的降级结构
- 容器重建是在役变更：走"先 build 后 swap，健康验证失败即回滚"（R20）
- identity_binding 必须分模式（R21）：DEMO 模式关闭（外部 Leader 未持证书会全被拒），TRUSTED 模式开启——不是全局开关

---

## 二、全自主任务图谱（按依赖与价值排序）

| # | 任务 | 回应 | 依赖 | 价值 | 夜次 |
|---|---|---|---|---|---|
| T0 | **可信身份闭环**：check 读回正式 AIC → 更新 ACS/代码身份 → EAB → ACME 签发证书 → mTLS 配置 → identity_binding TRUSTED 模式 + 伪造身份拒绝测试 | Codex ⑥ | 平台 CLI 可达 | ★★★（答辩差异化） | v5 |
| T1 | **W4 实验修正复跑**：stage 深度强制、门控修订三策略 A/B（flash 重写 / 意见注入不重写 / plus 重写）、固定 5 主题 × N=5、完整度双盲评分 | Codex ⑤ | GLM 配额 | ★★★（答辩数据） | v5 |
| T2 | **Discovery 真实联测**：acps-infra 起 Postgres+pgvector → discovery-server 本地运行（运行不改）→ 种子 Agent → Leader DISCOVERY_URL 全链 | Codex ① | Docker/磁盘 | ★★☆ | v5 |
| T3 | 在役容器重建：v2.1 网关 + v4 研伴代码进容器，健康 7/7 + GLM 端到端，失败回滚 | 技术债 | T0 身份文件就位更佳 | ★★☆ | v5 |
| T4 | live e2e 加固：arXiv/GLM 超时重试，`pytest -m live` 可信化 | 质量债 | — | ★★ | v5 |
| T5 | /tools/search 向量版：GLM embedding-3 编码聚合目录（零大下载），关键词兜底 | 工具膨胀 | GLM | ★★ | v5 |
| T6 | **比赛材料草稿**：技术报告七段全稿、PPT 大纲（15 页）、5 分钟视频分镜脚本 | 比赛 | T1 数据 | ★★★ | v5/v6 |
| T7 | AMP 后台数据报告：amp_report 跑在役日志出"应用成效"图表素材 | 比赛 | 在役日志 | ★★ | v6 |
| T8 | 压测：Leader 并发 10/20 任务、网关 5 上游并发调用、SSE 长连接稳定性 | 应用成效 | T3 | ★★ | v6 |
| T9 | 发布整备：v2.1.0 Release Notes、CHANGELOG、ACS/AgentCard 版本号统一 | 开源形象 | — | ★☆ | v6 |

## 三、时间线（倒排至 10-15）

```
09/12（今晚）   v5 夜班：T0 身份闭环 + T1 实验复跑 + T2 联测 + T3 容器 + T4 加固
09/13-09/15    v6 夜班：T6 材料全稿 + T7 AMP 报告 + T8 压测 + T9 发布
09/16-09/25    用户审阅材料，AI 迭代修改（白天 10 分钟级多轮）
09/26-10/08    视频录制（用户出镜，AI 备分镜/字幕/口播稿）；查漏补缺
10/09-10/14    最终提交物打磨 + 提交（报告/PPT/视频），预留 48h buffer
10/15          截止
```

## 四、v5 之后的状态预测

v5 完成后，Codex 六问题状态：①真发现（含真实联测）✅ ②重连 ✅ ③真 DAG ✅ ④四指标 ✅ ⑤闭环实验（修正版数据）✅ ⑥可信身份 ✅——**六个问题全部闭环，且每条都有测试或数据证据**。剩余工作全部转入比赛材料与打磨，工程冲刺阶段结束。
