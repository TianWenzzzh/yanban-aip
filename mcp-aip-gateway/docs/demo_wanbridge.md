# 万桥 WanBridge · 60 秒演示脚本（比赛/路演用）

> 全部命令在本仓根目录执行；端口 ≥8060 避开在役系统。先 `uv sync`。

## 0. 起双面演示实例（后台）

```bash
uv run python bench/run_httpdemo.py 8062 &          # MCP httpdemo 上游
sleep 2
A2A_CONFIG=bench/bench_config_upstream.toml AMP_LOG_DIR=/tmp/wb-amp \
  uv run uvicorn --factory gateway.app:create_app --host 127.0.0.1 --port 8060 &
sleep 3   # 等上游全部就绪
curl -s http://127.0.0.1:8060/health | head -c 200
```

## 1. 同一能力，两种协议，一个结果（30 秒）

```bash
# A2A 面发现：动态 AgentCard（双路径同卡）
curl -s http://127.0.0.1:8060/.well-known/agent-card.json | head -c 300

# A2A 面调用（tasks/send，JsonPart 一等公民）
curl -s -X POST http://127.0.0.1:8060/ -H "content-type: application/json" -d '{
  "jsonrpc":"2.0","id":"1","method":"tasks/send",
  "params":{"id":"t1","message":{"parts":[{"type":"data",
    "data":{"tool":"httpdemo__literature_search","arguments":{"topic":"大模型多智能体协作","limit":2}}}]}}}' | head -c 300

# AIP 面调用同一工具（/rpc，国标 Partner）——产物内容与 A2A 面等价
curl -s -X POST http://127.0.0.1:8060/rpc -H "content-type: application/json" -d '{
  "jsonrpc":"2.0","id":"2","method":"rpc",
  "params":{"command":{"type":"task-command","id":"c1","taskId":"demo-t2",
    "sentAt":"2026-09-12T02:00:00Z","senderRole":"leader","senderId":"demo-leader",
    "command":"start",
    "dataItems":[{"id":"d1","type":"text","text":"{\"tool\":\"httpdemo__literature_search\",\"arguments\":{\"topic\":\"大模型多智能体协作\",\"limit\":2}}"}]}}}' | head -c 300
```

## 2. 生态探针（15 秒）

```bash
uv run python tests/conformance/probe.py --target http://127.0.0.1:8060 --profile all
# 11 项协议形状检查，现场看 11/11 PASS
```

## 3. 级联回环（15 秒，展示"万桥级联万桥"）

```bash
# 第二实例扮上游 A2A Agent（8070），再跑基准 E 组：AIP→万桥→万桥→MCP
uv run python bench/bench_interop.py 2>&1 | grep -E "^E[12]|轮次分布" -A6
# 看点：E1/E2 全绿，级联一跳开销 +2~3ms（协议翻译层不主导时延）
```

## 收尾

```bash
kill %1 %2   # 只回收本脚本拉起的进程（R6）
```

演示素材对应比赛材料：`docs/bench_interop_results.md`（量化数据）、`docs/probe_self_report.md`（探针报告）、`docs/whitepaper_v0.md`（技术主张）。
