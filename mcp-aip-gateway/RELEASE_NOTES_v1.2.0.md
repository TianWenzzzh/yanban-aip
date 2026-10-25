# Release v1.2.0 — 4→5 上游 / 19 工具，streamable-http + 官方 git/time 接入

发布日期：2026-09-06（待用户执行 GitHub Release 时直接粘贴本文件正文）

## Highlights

- **聚合规模**：单实例 5 类上游 19 项技能（本地 AIP partner + 官方 fetch/time/git MCP server + streamable-http demo），ACS 通过官方 jsonschema 校验
- **streamable-http 传输**：远程 MCP server 一等公民（SDK 2.x `streamable_http_client`，含 2 元组返回值兼容）
- **官方生态零改动**：`mcp-server-git`（限定仓库模式，12 工具）、`mcp-server-time`、`mcp-server-fetch` 直接挂载
- **可靠性**：LLM 拼接式非法工具输入宽容解析（Rejected→Completed）；上游失联自动撤架、恢复自动上架；AMP 全链路审计
- **工程**：容器化（GATEWAY_CONFIG 精简上游模式）；15 个离线单测（translator 全覆盖）；`--dev` pytest 就绪

## Upgrade

```bash
git pull && uv sync
# v1.1 → v1.2 无配置破坏性变更；新增上游在 gateway/config.toml 追加 [upstreams.*] 即可
```

## Checksums / 数据

- 冷启动冒烟：/health 5 上游全 online（2026-09-06 记录）
- 基准：研伴体系 10/10 任务、48 RPC/轮（2026-09-05，bench-results-20260905.md）

## Known Issues

- 容器内不装 uvx：容器模式仅挂随镜像上游（GATEWAY_CONFIG=config.container.toml）
- mcp-server-sqlite 为 MCP 1.x 旧 API 编写，与 SDK 2.x 不兼容（网关撤架机制兜底，勿挂）
