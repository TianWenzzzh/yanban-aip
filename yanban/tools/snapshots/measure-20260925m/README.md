轮 149 的普查量具：外部提问池逐条走产品自己的判定链，量「首问能不能进检索轮」与「槽位填到哪」（零模型调用：LLM_MOCK 必须为真 + httpx 装雷，植入场红在 Z0b）。

读数：放行 0/206、`ready_for_search` 0 条、199 条一个槽位都没填上；正对照 g98 那句中文放行=True（confidence 0.615），负对照放行=False。confidence 上限 0.231 那条来自 g145 的 K6，不在本仪器里。

| 文件 | sha256 |
| --- | --- |
| `probe_pool_readiness_local.py` | `b8150f15f066b688951ec0b130b68644005cfd1f09c41b7a76c875baf7b9d32d` |
