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

## 对 AIP 开源社区的贡献

本项目没有直接改动上游仓库代码，而是把在实战中遇到的问题整理成可复现的用例，向 [AIP-PUB/ACPs-community](https://github.com/AIP-PUB/ACPs-community) 提交问题反馈与补丁 PR，由社区评审决定是否合入。以下链接均可公开核验：

| 类型 | 内容 | 链接 |
|---|---|---|
| 文档 PR | 让 `agent-development.md` 的最小 Leader / Partner 示例在 SDK v2.2.0 下可直接运行（identity binding 开关、AIC 10 段格式） | [PR #29](https://github.com/AIP-PUB/ACPs-community/pull/29) |
| 测试 PR | AMP `MetricsEmitter` 写入失败用例改用平台无关的路径构造，修复 Windows 上断言失败 | [PR #30](https://github.com/AIP-PUB/ACPs-community/pull/30) |
| 脚本 PR | 修复 Git Bash 下 `dev-cert.sh` 的 MSYS 参数改写问题，并让 openssl 失败不再静默 | [PR #31](https://github.com/AIP-PUB/ACPs-community/pull/31) |
| Issue | [Docs] 最小示例与 SDK v2.2.0 不兼容（identity binding / AIC 格式） | [#24](https://github.com/AIP-PUB/ACPs-community/issues/24) |
| Issue | [Bug] `dev-cert.sh` 在 Git Bash 下 openssl `-subj` 被 MSYS 路径转换破坏 | [#25](https://github.com/AIP-PUB/ACPs-community/issues/25) |
| Issue | [Bug] AMP 测试在 Windows 上失败（`/readonly_nonexistent_dir` 不是有效故障路径） | [#26](https://github.com/AIP-PUB/ACPs-community/issues/26) |

三个补丁均先在 Windows 11 / Python 3.14.6 / acps-sdk v2.2.0 上复现原始失败，再验证修复后的通过结果，复现命令与前后对比数据见各 PR 描述。其中 #24 一条还附了对我们原报告表述的自我更正（AIC 10 段校验只在开启身份绑定时生效）。

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
