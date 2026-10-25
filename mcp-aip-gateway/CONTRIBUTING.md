# 贡献指南

1. Fork → 分支（feat/xxx 或 fix/xxx）→ 提交 → PR
2. 提交信息用中文或英文均可，一行主题 + 必要时正文说明动机
3. Bug 报告请附：OS、Python 版本、acps-sdk 版本、MCP SDK 版本、最小复现步骤、完整报错
4. 新上游适配请先在 gateway/config.toml 以本地配置验证，勿提交含真实地址/凭据的配置
5. 提交前：`uv run python -m compileall gateway -q` 无输出、`uv run pytest -q` 全绿
6. 设计级改动请先开 Issue 讨论（本项目有明确的"确定性翻译层"定位边界，见 README）
