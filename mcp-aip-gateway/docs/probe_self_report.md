# 万桥生态探针 · 自测报告（首轮）

- 探针：`tests/conformance/probe.py`（N6）｜ 目标：`http://127.0.0.1:8060` ｜ profile：`all`
- 启动：2026-09-12 08:55:12（time.strftime 实测）｜ 结果：**PASS 12 / FAIL 0 / SKIP 0**
- 边界声明：**只测协议形状，不测业务正确性**（不构造真实业务负载，不评价上游返回内容）

| # | 检查项 | 预期 | 实际 | 判定 | 备注 |
|---|---|---|---|---|---|
| A2A-1 | well-known 双路径可达且同卡 | 两路径 200 且 JSON 相同 | agent-card=200 agent.json=200 同卡=True | PASS |  |
| A2A-2 | Card 必填字段存在 | name/url/capabilities/skills 齐备且 skills 为列表 | 缺失=无 skills类型=list | PASS |  |
| A2A-3a | tasks/send 可达（未知工具→failed 任务） | HTTP 200 + result.status.state=failed | HTTP 200 state=failed | PASS |  |
| A2A-3b | message/send 可达（未知工具→failed 任务） | HTTP 200 + result.status.state=failed | HTTP 200 state=failed | PASS |  |
| A2A-3c | message/stream 流式可达 | text/event-stream + final 帧 | cta=text/event-stream; chars final帧=True | PASS |  |
| A2A-4 | 非法 JSON → -32700 | error.code=-32700 | code=-32700 | PASS |  |
| A2A-5 | 未知方法 → -32601 | error.code=-32601 | code=-32601 | PASS |  |
| A2A-6a | tasks/get 未知 id | -32001 | code=-32001 | PASS |  |
| A2A-6b | tasks/get 缺 id | -32602 | code=-32602 | PASS |  |
| AIP-1 | /health 存活 | 200 且 status=ok | HTTP 200 status=ok | PASS |  |
| AIP-2 | /acs 过官方 acsSchema | 200 + jsonschema 校验通过 | skills=2 | PASS |  |
| AIP-3 | /rpc 非法输入 → 协议错误 | error.code=-32700 | code=-32700 | PASS |  |

---
*探针边界：协议形状探测工具，供生态伙伴自查与互查；业务正确性由各上游自己的 E2E 负责。*
