# 研伴 Yanban · AIP 智能体协作系统 + MCP↔AIP 网关

基于 AIP 智能体互联国标（GB/Z 185 / ACPs v2.2.0）的科研协作多智能体系统，以及配套的协议适配网关「万桥 WanBridge」。

2026 第八届全球校园人工智能算法精英大赛「智能体互联」算法主题赛参赛作品的公开源码。

## 仓库结构

| 目录 | 内容 |
|---|---|
| [`yanban/`](yanban/README.md) | 科研协作系统主体：Leader 编排器 + 5 个科研 Partner（文献 / 实验 / 数据 / 写作 / 审稿）+ 质量闭环 + Web 工作台 |
| [`mcp-aip-gateway/`](mcp-aip-gateway/README.md) | 万桥网关：把任意 MCP Server 的工具零改动翻译为 AIP Partner，同时暴露 AIP 与 A2A 两个协议面 |

系统组成、端口分配与启动方式见 [`yanban/README.md`](yanban/README.md)；网关设计、翻译规则与实测记录见 [`mcp-aip-gateway/DESIGN.md`](mcp-aip-gateway/DESIGN.md)。

## 与上游 AIP 开源社区的关系

- 协议基座为 [AIP-PUB/ACPs-community](https://github.com/AIP-PUB/ACPs-community) **v2.2.0**（MulanPSL-2.0）。
- `acps-sdk` 作为依赖安装使用，**未修改上游源码**，本仓库不重复分发上游代码。
- 本仓库的改动集中在上游未覆盖的三层：协议翻译层（MCP↔AIP）、科研任务编排层、质量闭环与评估层。

## 快速开始

```bash
# 1) 配置模型 Key（智谱 GLM，OpenAI 兼容接口）
cd yanban && cp .env.example .env   # 填入 GLM_API_KEY

# 2) 一键拉起 7 个服务（5 Partner + Leader + 网关）并做健康探活
bash deploy.sh

# 3) 工作台
#    Leader / Web UI : http://localhost:8041/
#    网关 A2A 面     : http://localhost:8021/
```

手工启动、容器化方式与工作台开发模式见 [`yanban/README.md`](yanban/README.md)。

## 测试

```bash
cd yanban && uv run pytest
cd ../mcp-aip-gateway && uv run pytest
```

## 平台发布

「研伴·文献检索智能体」已在梧桐智能体互联平台（wt.ioa.pub）发布并通过审核，AIC：

```
1.2.156.3088.1.BUPT.BNN2UD.A0G0KD.YYTVI4.1DHO
```

可通过平台公开发现接口按 AIC 查询：

```bash
curl -sS -X POST "https://wt.ioa.pub/discovery/acps-adp-v2/discover" \
  -H "Content-Type: application/json" \
  -d '{"type":"filtered","limit":5,"filter":{"conditions":[{"field":"aic","op":"eq","value":"1.2.156.3088.1.BUPT.BNN2UD.A0G0KD.YYTVI4.1DHO"}]}}'
```

## 许可证

- 根目录及各子系统：木兰宽松许可证第 2 版（MulanPSL-2.0），见 [LICENSE](LICENSE)。
- [`mcp-aip-gateway/`](mcp-aip-gateway/LICENSE)：MIT。

## 边界说明

本仓库只包含源码、测试与对外文档。竞赛内部材料、迭代日志、平台探测记录、用户数据与运行数据库均不在本仓库内；示例配置中的密钥为占位符，实际运行需自备。
