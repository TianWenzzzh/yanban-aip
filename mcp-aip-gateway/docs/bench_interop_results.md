# 万桥互操作基准报告 v0.2（N5：级联回环 n=5）

> v0 报告（2026-09-07 首轮）见下文【归档】节。本节为 v0.2 最新结果。

- 启动：2026-09-12 01:5x（`time.strftime` 实测）｜ 环境：Linux（新机）/ Python 3.14.7（uv）/ mcp SDK 2.x / acps-sdk 2.2.0（editable）
- 基准栈（全部开发端口 ≥8060，Popen 句柄精确管理）：
  - `8062` httpdemo MCP 上游（streamable-http）
  - `8070` **上游 A2A Agent**（第二万桥实例，`bench_config_upstream.toml`，扮远端智能体）
  - `8061` 网关 AIP 面（`bench_config.toml`：yanban stdio + httpdemo + **a2a 上游 → 8070**）
  - `8060` 网关 A2A 面（同一份 config，能力与 AIP 面同源）
- 规模：**12 项 × 5 轮 = 60 任务，成功率 100%**（v0 的 10 项单轮 → v0.2 扩 E 组并跑 5 轮出分布）
- 原始数据：`bench/bench_results.json`（含逐轮 rounds 字段）｜ 复跑：`uv run python bench/bench_interop.py`

## 一·v0.2 E 组：级联回环（本版核心主张）

| 任务 | 路径 | 往返 | 稳态时延（r2-r5） | 说明 |
|---|---|---|---|---|
| E1 | AIP→**A2A 上游**→MCP（级联） | 3（start+complete+桥send） | 13-16ms，首轮 44ms（冷启动） | AIP start `remote__literature_search` → registry a2a 分发 → 8070 → httpdemo |
| E2 | 万桥级联（A2A 客户端视角） | 2（客户端 send + 桥 send） | 6-9ms | A2aClient → 8060 A2A 面 → a2a 上游转发 → 8070 A2A 面 → 8070 的 MCP → 原路回包 |

**级联时延分解**（同轮 A3 直连 httpdemo ≈11-14ms 为基线）：
- E1 − A3 ≈ **+2~3ms**：一次跨进程 tasks/send + 桥状态映射的纯协议开销
- E2 ≈ 6-9ms，**低于 E1**：A2A 面单次往返语义（send 即终态）省掉 complete 往返
- 结论：万桥级联一跳的协议翻译成本 ≈ 2-3ms 量级——"协议翻译层不主导时延"主张成立

## 二·v0.2 分组汇总（n=5）

| 组 | 成功率 | 平均时延 | p50 | 往返均值 |
|---|---|---|---|---|
| A（AIP→MCP） | 100% | 13ms | 11ms | 2.0 |
| B（A2A→MCP 经万桥） | 100% | 4ms | 4ms | 1.0 |
| C（双协议错误路径） | 100% | 6ms | 6ms | 1.0 |
| D（发现+全回环） | 100% | 8ms | 8ms | 1.5 |
| **E（级联回环）** | **100%** | **13ms** | **13ms** | **2.0** |

逐轮成功率 5/5 全绿（rounds 明细见 bench_results.json）。D2 内容与 A1 跨协议等价 5/5 轮为 True。

---

# 万桥互操作基准报告 v0（M6 首轮）【归档】

- 启动：2026-09-07 00:05:00（`time.strftime` 实测，非估算）
- 环境：Windows 11 / Python 3.14.6（uv venv）/ mcp SDK 2.x / acps-sdk 2.2.0（editable）
- 基准栈（全部开发端口 ≥8060，与在役系统 8021-8041 隔离；进程由脚本 Popen 句柄精确管理）：
  - `8062` httpdemo MCP 上游（streamable-http）
  - `8061` 网关 AIP Partner 面（`gateway.partner:app` + `bench/bench_config.toml`）
  - `8060` 网关 A2A 服务面（`gateway.a2a_server:create_app_from_env` + 同一份 config）
  - 两个上游（yanban stdio 2 工具 + httpdemo 2 工具）在两面**同时在线**（启动快照 8-9ms 内确认）
- 原始数据：`bench/bench_results.json` ｜ 复跑口令：`uv run python bench/bench_interop.py`

## 一、任务矩阵与首轮结果（10/10 全绿，成功率 100%）

| 任务 | 路径 | 结果 | 时延ms | 往返 | 说明 |
|---|---|---|---|---|---|
| A1 | AIP→MCP | ✅ | 352 | 2 | yanban__literature_search，经 AIP 面 start→complete |
| A2 | AIP→MCP | ✅ | 286 | 2 | yanban__summarize_paper |
| A3 | AIP→MCP | ✅ | 287 | 2 | httpdemo__literature_search（streamable-http 上游） |
| B1 | A2A→MCP 经万桥 | ✅ | 337 | 1 | 与 A1 同工具，走 AgentCard+tasks/send |
| B2 | A2A→MCP 经万桥 | ✅ | 8 | 1 | 与 A2 同工具 |
| B3 | A2A→MCP 经万桥 | ✅ | 10 | 1 | 与 A3 同工具 |
| C1 | A2A 错误路径 | ✅ | 8 | 1 | 未知工具→failed+可用清单（A2A 无 Rejected 态的翻译决策验证） |
| C2 | AIP 错误路径 | ✅ | 274 | 1 | 未知工具→rejected（返回可用清单供 Leader 自纠错） |
| D1 | 发现 | ✅ | 24 | 2 | well-known 双路径各 1 次，两路径返回同一张卡 |
| D2 | 全回环 | ✅ | 274 | 1 | A2aClient→万桥 A2A 面→MCP→桥状态映射；**结果与 A1 跨协议等价=True** |

分组汇总：

| 组 | 成功率 | 平均时延 | p50 | 往返均值 |
|---|---|---|---|---|
| A（AIP→MCP） | 100% | 308ms | 287ms | 2.0 |
| B（A2A→MCP 经桥） | 100% | 90ms | 10ms | 1.0 |
| C（错误路径） | 100% | 274ms | 274ms | 1.0 |
| D（发现/回环） | 100% | 149ms | 274ms | 1.5 |
| **总计** | **100%** | **186ms** | — | **1.4** |

## 二、首轮分析

1. **跨协议等价性成立**：D2 全回环与 A1（AIP 面）同工具同参数，产物文本完全一致（demo 数据确定性），
   且桥映射后的 AIP 状态落在官方 8 态内——"同一能力，两种协议面，一个结果"是万桥的核心主张，首轮即验证。
2. **A2A 面天然更省往返**：B 组 1 往返（tasks/send 单跳同步完成）对 A 组 2 往返（start+complete 交付确认）。
   时延差异主要来自协议语义而非实现：AIP 的 awaiting-completion→complete 交付确认多一跳（B2 8ms vs A2 286ms
   中约 270ms 是 start 内同步等 MCP 调用+complete 往返）。这解释了为什么网关对两种协议都能保持亚秒级。
3. **错误语义两种协议各自合规**：AIP 面未知工具→`rejected`（可自纠错清单）；A2A 面无 Rejected 态→
   `failed`+消息体携带清单——与 M1 设计 §2.2 决策一致，Leader/客户端在两种协议下都能自纠错。

## 三、口径与限制（诚实声明）

- 往返计数：A 组 = start(1)+complete(1)；B/C/D2 = tasks/send(1)；B 组的 AgentCard 发现由 D1 单独计时，
  组内不重复计；D1 = 双路径 GET 各 1。
- demo 上游为确定性数据，时延不构成对外承诺，只用于同机跨协议对照。
- 稳定性（00:15 补充）：连跑 3 轮全部 10/10 全绿（exit=0），总平均时延 186/185/186ms（波动 <1%），
  原始数据 `bench/bench_results_run{1,2,3}.json` + 控制台记录 `bench/logs/bench_run{1,2,3}.txt`。
  n≥20 分布与外网上游窗口均值仍留 v2。
- 外网型上游（fetch 等）未入基准：时延受外网波动支配，跨协议对照意义弱；矩阵已验其兼容性（M5）。

## 四、基准附赠：修复 v1.1 多上游启动竞态

首轮曾打出 A1/A2"未知工具"假失败，取证（`/health` 快照 + uvicorn 生命周期日志时序）定位为
`registry.start()` 的全局 `asyncio.Event` 竞态：**首个就绪上游即放行整个网关启动**，其余上游还在
初始化就被当作已撤架。已修复为逐上游 `ready` Event，全部首刷（或失败）后才就绪——基准脚本同时加了
"全部期望上游在线"兜底闸。该修复让"多上游聚合"在冷启动窗口期的行为与文档承诺一致。
