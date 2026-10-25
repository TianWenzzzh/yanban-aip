# 万桥生态验证矩阵（M5）

> 目的：证明万桥翻译层对**真实 MCP 生态**的普适性——官方参考实现、不同语言（Python/TypeScript）、
> 不同分发渠道（PyPI-uvx / npm-npx / 本地脚本）、不同传输（stdio / streamable-http）逐个过闸。
> 验证工具：`bench/smoke_mcp_server.py`（握手→list_tools→可选工具调用，输出单行 JSON，硬超时防挂死）。
>
> - 第一批（2026-09-04，DESIGN.md v1.1/v1.2 记录）：fetch/time/git/httpdemo/yanban 首验通过
> - 第二批（2026-09-06 深夜，本表）：上述 5 上游**全部复验** + 新增 4 候选**实测** + 1 已知不兼容**坐实根因**
> - 环境口径：PyPI 走阿里镜像（`UV_DEFAULT_INDEX`）；npm 直连（当日 Clash 代理未开，实测直连 PONG 394ms）

## 一、已验证上游（网关 config.toml 在列，2026-09-06 复验 ✅）

| # | server | 版本 | 语言/分发 | 传输 | 工具数 | 结果 | 坑/备注 |
|---|---|---|---|---|---|---|---|
| 1 | yanban（demo_mcp_server.py） | 本地 v1.2 | Python/本地脚本 | stdio | 2 | ✅ 握手+列目 2001ms | 文献检索/摘要两工具；同脚本 `--http` 即 httpdemo |
| 2 | mcp-server-fetch | 2026.8.18 | Python/PyPI-uvx | stdio | 1 | ✅ 握手+列目 5885ms | 抓外网需代理；本次列目未触发外网 |
| 3 | mcp-server-time | 2026.8.18 | Python/PyPI-uvx | stdio | 2 | ✅ 握手+**实调** get_current_time 2ms | 返回 Asia/Shanghai 真实时间；注意 SDK 2.x 的 `CallToolResult` 已无 `isError` 字段（探针已双键名兼容） |
| 4 | mcp-server-git | 2026.8.18 | Python/PyPI-uvx | stdio | 12 | ✅ 握手+列目 41668ms | 冷启动慢（uvx 解析+安装）；限定 yanban 仓只读使用 |
| 5 | httpdemo（demo_mcp_server.py --http） | 本地 v1.2 | Python/本地脚本 | streamable-http | 2 | ✅ 握手+列目 753ms | 8022 开发端口；启动器 `bench/run_httpdemo.py` 会落盘精确 PID（R6 清理红线） |

## 二、新增候选（2026-09-06 实测 ✅，全部官方 TypeScript 实现）

| # | server | 版本 | 语言/分发 | 传输 | 工具数 | 结果 | 坑/备注 |
|---|---|---|---|---|---|---|---|
| 6 | server-everything | 2026.8.31 | TS/npm | stdio | 13 | ✅ 握手+**实调** echo 2ms（回显"万桥 WanBridge"） | 官方能力最全的测试场（资源/订阅/长任务/结构化内容） |
| 7 | server-memory | 2026.8.31 | TS/npm | stdio | 9 | ✅ 握手+列目 918ms | 知识图谱；**PyPI 同名包 `mcp-server-memory` 是另一物**（安装成功但模块结构不符，勿用） |
| 8 | server-filesystem | 2026.8.31 | TS/npm | stdio | 14+ | ✅ 握手+列目 | 必须给允许目录参数；本次限定本仓目录（最小授权） |
| 9 | server-sequential-thinking | 2026.8.31 | TS/npm | stdio | 1 | ✅ 握手+列目 915ms | 动态反思推理工具，纯文本契约，万桥弱契约翻译的天然样本 |

**npm 系接入手感（重要坑）**：
- `cmd /c npx -y <pkg>` 作 stdio 命令会**挂死**（cmd→npx→node 三层进程树，stdio 管道收不干净）——已弃用；
- 正确姿势：`npx -y <pkg>` 预热一次后，取 `$(npm config get cache)/_npx/<hash>/node_modules/.../dist/index.js`，
  直接 `node <entry.js>` 启动（单进程树，退出干净）。探针已加 `--timeout` 硬超时兜底。

## 三、已知不兼容（如实记录，供白皮书"边界"节引用）

| server | 现象 | 根因（2026-09-06 取证） | 结论 |
|---|---|---|---|
| mcp-server-sqlite（PyPI，官方已归档） | uvx 可装可执行，但 MCP 握手即崩（Connection closed） | `AttributeError: 'Server' object has no attribute 'list_resources'`——归档版用 MCP SDK **1.x 低层 Server 装饰器**，uvx 恒定解析最新 **SDK 2.x**（破坏性变更，DESIGN.md 坑位 #1 同源） | 非万桥之过，是上游停更×SDK 大版本的兼容债；等官方 TS 版 server-sqlite 或社区 fork |
| sequential-thinking / filesystem 的 Python 版 | PyPI 无官方包（No solution found when resolving） | 官方仅 npm 分发 | 走 npm 路线即可，万桥翻译层不区分上游语言 |

## 四、跑分计划（>100MB 或长时任务只写计划不执行，R6）

- `@modelcontextprotocol/server-benchmark-*` 类压测包：暂缓（体积/时长未知，先计划）。
- 真实外网重载体（arXiv 全文抓取、GitHub 全量):留给 M6 基准的小循环口径（单次调用、限时）。
- 矩阵复跑口令（一键复现）：
  ```bash
  export UV_DEFAULT_INDEX=https://mirrors.aliyun.com/pypi/simple/
  uv run python bench/smoke_mcp_server.py --ns yanban -- python demo_mcp_server.py
  uv run python bench/smoke_mcp_server.py --ns fetch -- uvx mcp-server-fetch
  uv run python bench/smoke_mcp_server.py --ns time --call get_current_time --args '{"timezone":"Asia/Shanghai"}' -- uvx mcp-server-time --local-timezone Asia/Shanghai
  uv run python bench/smoke_mcp_server.py --ns git -- uvx mcp-server-git --repository D:/code/yanban
  uv run python bench/run_httpdemo.py 8022 & sleep 6; uv run python bench/smoke_mcp_server.py --ns httpdemo --http http://127.0.0.1:8022/mcp; kill -9 -W $(cat logs/httpdemo.pid)
  # npm 系见第二节"正确姿势"
  ```
