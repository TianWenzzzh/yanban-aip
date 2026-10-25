"""万桥互操作基准 v0.2（M6+N5）。

跨协议任务矩阵（每轮 12 项 × n=5 轮出分布）：
- A 组 AIP→MCP：官方 AipRpcClient → 网关 AIP 面（/rpc）→ MCP 上游
- B 组 A2A→MCP 经万桥：bridge_a2a.A2aClient → 网关 A2A 面（AgentCard+tasks/send）→ 同一批 MCP 上游
- C 组 错误路径：未知工具在两种协议面的语义（AIP=Rejected / A2A=failed+可用清单）——预期失败也算跑通
- D 组 发现与全回环：well-known 双路径发现时延；A2aClient→万桥 A2A 面→MCP→A2A Task→桥状态映射，
  并与 A 组同工具结果做内容等价性对照
- E 组 级联回环（v0.2 新增，N5）：8070 第二实例扮上游 A2A Agent（挂自己的 MCP）——
  E1 AIP→A2A 上游全链路：AIP start "remote__literature_search" → registry a2a 分发 → 8070 → MCP；
  E2 万桥级联：A2aClient → 8060 A2A 面 → a2a 上游转发 → 8070 A2A 面 → 8070 的 MCP → 原路回包

评分口径：成功率 / 时延（端到端，monotonic 实测）/ 往返次数（HTTP RPC 计数）。
往返计数规则：AIP 任务 = start(1)+complete(1)；A2A 任务 = tasks/send(1)（卡发现组内均摊）；
E1 = start+complete+桥 send = 3；E2 = 客户端 send + 桥 send = 2。

基准栈（全部开发端口 ≥8060，避开在役 8021-8041；进程由本脚本 Popen 句柄精确管理，R6 合规）：
  8062 httpdemo MCP 上游（streamable-http）
  8070 上游 A2A Agent（gateway.a2a_server:create_app_from_env + bench_config_upstream.toml）
  8061 网关 AIP Partner 面（gateway.partner:app + bench_config.toml）
  8060 网关 A2A 服务面（gateway.a2a_server:create_app_from_env + bench_config.toml）

用法：uv run python bench/bench_interop.py
输出：控制台表格 + docs/bench_interop_results.md + bench/bench_results.json
"""

from __future__ import annotations

import asyncio
import json
import os
import socket
import subprocess
import sys
import time
from dataclasses import dataclass, field, asdict
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

PORT_HTTPDEMO, PORT_AIP, PORT_A2A, PORT_UP_A2A = 8062, 8061, 8060, 8070
ROUNDS = 5
LEADER_AIC = "1.2.156.3088.1.1.LDR001.LDR001.1.0002"

LIT_ARGS = {"topic": "大模型多智能体协作", "limit": 3}
SUM_ARGS = {
    "title": "Agent Lab",
    "abstract": "多智能体系统通过任务分解与协同执行提升科研效率，本文提出一种结构化契约流水线方法。",
    "max_words": 50,
}


@dataclass
class TaskRecord:
    task_id: str
    group: str
    path: str
    description: str
    ok: bool
    latency_ms: int
    round_trips: int
    detail: str = ""
    extra: dict = field(default_factory=dict)


def port_free(port: int) -> bool:
    with socket.socket() as s:
        return s.connect_ex(("127.0.0.1", port)) != 0


async def wait_http(url: str, timeout_s: float = 40.0) -> bool:
    t0 = time.monotonic()
    async with httpx.AsyncClient() as client:
        while time.monotonic() - t0 < timeout_s:
            try:
                r = await client.get(url)
                if r.status_code < 500:
                    return True
            except httpx.HTTPError:
                pass
            await asyncio.sleep(0.5)
    return False


async def boot_stack() -> list[subprocess.Popen]:
    """起基准栈并等健康；任一组件失联即终止已启动部分并抛错。

    启动顺序（v0.2）：8062 httpdemo → 8070 上游 A2A Agent → 8060/8061（其 registry
    的 a2a 上游 ready Event 要等 8070 的 Card 可达，首刷才放行 start()）。
    """
    assert port_free(PORT_HTTPDEMO) and port_free(PORT_AIP) and port_free(PORT_A2A) \
        and port_free(PORT_UP_A2A), \
        "8060-8062/8070 端口被占：先清理再跑（本脚本只按 Popen 句柄管理自己的进程）"
    procs: list[subprocess.Popen] = []
    env = {**os.environ, "UV_DEFAULT_INDEX": "https://mirrors.aliyun.com/pypi/simple/"}
    logs = ROOT / "bench" / "logs"
    logs.mkdir(exist_ok=True)

    def spawn(name: str, args: list[str], extra_env: dict[str, str]) -> subprocess.Popen:
        e = {**env, **extra_env}
        f = open(logs / f"{name}.log", "w", encoding="utf-8")
        p = subprocess.Popen(args, cwd=ROOT, env=e, stdout=f, stderr=f)  # noqa: S603
        procs.append(p)
        return p

    try:
        spawn("bench-httpdemo-8062", [sys.executable, "bench/run_httpdemo.py", str(PORT_HTTPDEMO)], {})
        spawn("bench-upstream-a2a-8070", [sys.executable, "-m", "uvicorn", "gateway.a2a_server:create_app_from_env",
                                          "--factory", "--host", "127.0.0.1", "--port", str(PORT_UP_A2A)],
              {"A2A_CONFIG": str(ROOT / "bench" / "bench_config_upstream.toml"), "A2A_PORT": str(PORT_UP_A2A)})
        ok = await asyncio.gather(
            wait_http(f"http://127.0.0.1:{PORT_HTTPDEMO}/mcp"),
            wait_http(f"http://127.0.0.1:{PORT_UP_A2A}/health"),
        )
        if not all(ok):
            raise RuntimeError(f"基准栈（上游侧）健康等待失败：{ok}（详见 bench/logs/*.log）")
        spawn("bench-a2a-8060", [sys.executable, "-m", "uvicorn", "gateway.a2a_server:create_app_from_env",
                                 "--factory", "--host", "127.0.0.1", "--port", str(PORT_A2A)],
              {"A2A_CONFIG": str(ROOT / "bench" / "bench_config.toml"), "A2A_PORT": str(PORT_A2A)})
        spawn("bench-aip-8061", [sys.executable, "-m", "uvicorn", "gateway.partner:app",
                                 "--host", "127.0.0.1", "--port", str(PORT_AIP)],
              {"GATEWAY_CONFIG": str(ROOT / "bench" / "bench_config.toml"), "GATEWAY_PORT": str(PORT_AIP),
               "AMP_LOG_DIR": str(logs)})
        ok = await asyncio.gather(
            wait_http(f"http://127.0.0.1:{PORT_A2A}/health"),
            wait_http(f"http://127.0.0.1:{PORT_AIP}/health"),
        )
        if not all(ok):
            raise RuntimeError(f"基准栈（网关侧）健康等待失败：{ok}（详见 bench/logs/*.log）")
        return procs
    except BaseException:
        teardown(procs)
        raise


def teardown(procs: list[subprocess.Popen]) -> None:
    """按 Popen 句柄精确回收（R6：只动自己拉起的进程）。"""
    for p in procs:
        if p.poll() is None:
            p.terminate()
    for p in procs:
        try:
            p.wait(timeout=10)
        except subprocess.TimeoutExpired:
            p.kill()


# ── 任务执行体 ────────────────────────────────────────────────────────────


async def run_aip_task(task_id: str, group: str, tool: str, arguments: dict, expect: str, *,
                       path: str = "AIP→MCP") -> TaskRecord:
    """A/C/E 组：AIP 官方客户端 start→(products)→complete。"""
    from acps_sdk.aip import AipRpcClient, TaskState, TextDataItem

    t0 = time.monotonic()
    rts = 0
    client = AipRpcClient(partner_url=f"http://localhost:{PORT_AIP}/rpc", leader_id=LEADER_AIC,
                          identity_binding_enabled=False)
    try:
        task = await client.start_task(session_id=f"s-{task_id}", task_id=f"t-{task_id}",
                                       user_input=json.dumps({"tool": tool, "arguments": arguments}, ensure_ascii=False))
        rts += 1
        state = str(task.status.state.value if hasattr(task.status.state, "value") else task.status.state)
        texts: list[str] = []
        if task.status.state == TaskState.AwaitingCompletion:
            for p in task.products or []:
                texts += [i.text for i in (p.dataItems or []) if isinstance(i, TextDataItem)]
            task = await client.complete_task(task_id=f"t-{task_id}", session_id=f"s-{task_id}")
            rts += 1
            state = str(task.status.state.value if hasattr(task.status.state, "value") else task.status.state)
        elif task.status.state in (TaskState.Failed, TaskState.Rejected):
            texts = [i.text for i in (task.status.dataItems or []) if isinstance(i, TextDataItem)]
        latency = int((time.monotonic() - t0) * 1000)
        ok = state == expect
        return TaskRecord(task_id, group, "AIP→MCP", f"AIP start {tool}", ok, latency, rts,
                          detail=f"state={state} text={texts[0][:60] if texts else ''!r}",
                          extra={"raw_text": texts[0] if texts else ""})
    finally:
        await client.close()


async def run_a2a_task(task_id: str, skill_id: str, arguments: dict, expect: str,
                       client) -> TaskRecord:
    """B/C 组：A2aClient tasks/send → 状态映射（卡发现往返由调用方另计）。"""
    from gateway.bridge_a2a import A2aError

    t0 = time.monotonic()
    try:
        result = await client.call_skill(skill_id, arguments)
    except A2aError as exc:
        return TaskRecord(task_id, "B", "A2A→MCP 经万桥", f"A2A tasks/send {skill_id}", False,
                          int((time.monotonic() - t0) * 1000), 1, detail=f"桥错误：{exc}")
    latency = int((time.monotonic() - t0) * 1000)
    ok = result.aip_state == expect
    return TaskRecord(task_id, "B", "A2A→MCP 经万桥", f"A2A tasks/send {skill_id}", ok, latency, 1,
                      detail=f"a2a={result.a2a_state}→aip={result.aip_state} text={result.text[:80]!r}",
                      extra={"a2a_state": result.a2a_state, "aip_state": result.aip_state})


async def run_cascade_task(task_id: str, skill_id: str, arguments: dict, expect: str, client) -> TaskRecord:
    """E2 组：万桥级联 A2aClient → 8060 A2A 面 → a2a 上游转发 → 8070 A2A 面 → MCP → 原路回包。"""
    from gateway.bridge_a2a import A2aError

    t0 = time.monotonic()
    try:
        result = await client.call_skill(skill_id, arguments)
    except A2aError as exc:
        return TaskRecord(task_id, "E", "万桥级联", f"A2A 级联 {skill_id}", False,
                          int((time.monotonic() - t0) * 1000), 2, detail=f"桥错误：{exc}")
    latency = int((time.monotonic() - t0) * 1000)
    ok = result.aip_state == expect
    return TaskRecord(task_id, "E", "万桥级联", f"A2A 级联 {skill_id}", ok, latency, 2,
                      detail=f"a2a={result.a2a_state}→aip={result.aip_state} text={result.text[:60]!r}",
                      extra={"a2a_state": result.a2a_state, "aip_state": result.aip_state})


async def run_round(round_no: int, records: list[TaskRecord], card) -> bool:
    """跑一轮 12 项任务；返回本轮是否全绿。"""
    from acps_sdk.aip import AipRpcClient  # noqa: F401 — 复用 run_aip_task 内部导入
    from gateway.bridge_a2a import A2aClient

    r = f"r{round_no}"
    a1 = await run_aip_task(f"A1{r}", "A", "yanban__literature_search", LIT_ARGS, "completed")
    records.append(a1)
    records.append(await run_aip_task(f"A2{r}", "A", "yanban__summarize_paper", SUM_ARGS, "completed"))
    a3 = await run_aip_task(f"A3{r}", "A", "httpdemo__literature_search", LIT_ARGS, "completed")
    records.append(a3)
    a1_text = a1.extra.get("raw_text", "")

    async with A2aClient(card) as a2a:
        records.append(await run_a2a_task(f"B1{r}", "yanban__literature_search", LIT_ARGS, "completed", a2a))
        records.append(await run_a2a_task(f"B2{r}", "yanban__summarize_paper", SUM_ARGS, "completed", a2a))
        records.append(await run_a2a_task(f"B3{r}", "httpdemo__literature_search", LIT_ARGS, "completed", a2a))
        records.append(await run_a2a_task(f"C1{r}", "no_such_tool", {}, "failed", a2a))

        # ── E 组：级联回环（经 8060 的 a2a 上游 → 8070 → 8070 的 MCP） ──
        records.append(await run_aip_task(f"E1{r}", "E", "remote__literature_search", LIT_ARGS, "completed",
                                  path="AIP→A2A上游→MCP（级联）"))
        records.append(await run_cascade_task(f"E2{r}", "remote__literature_search", LIT_ARGS, "completed", a2a))
    records.append(await run_aip_task(f"C2{r}", "C", "not_exist_tool", {}, "rejected"))

    # ── D 组：发现与全回环 ──
    t0 = time.monotonic()
    async with httpx.AsyncClient() as http:
        r1 = await http.get(f"http://127.0.0.1:{PORT_A2A}/.well-known/agent-card.json")
        r2 = await http.get(f"http://127.0.0.1:{PORT_A2A}/.well-known/agent.json")
    d1_ms = int((time.monotonic() - t0) * 1000)
    records.append(TaskRecord(f"D1{r}", "D", "发现", "well-known 双路径发现", r1.status_code == r2.status_code == 200
                              and r1.json() == r2.json(), d1_ms, 2, detail="双路径同卡"))

    t0 = time.monotonic()
    async with A2aClient(card) as a2a:
        d2 = await a2a.call_skill("yanban__literature_search", LIT_ARGS)
    d2_ms = int((time.monotonic() - t0) * 1000)
    state_legal = d2.aip_state in {"accepted", "working", "awaiting-input", "awaiting-completion",
                                   "completed", "canceled", "failed", "rejected"}
    same_content = d2.text.strip() == a1_text.strip() if a1_text else False
    records.append(TaskRecord(f"D2{r}", "D", "全回环", "A2A客户端→万桥A2A面→MCP→桥映射",
                              d2.aip_state == "completed" and state_legal, d2_ms, 1,
                              detail=f"aip_state={d2.aip_state} legal={state_legal} 内容与A1等价={same_content}",
                              extra={"equivalent_to_A1": same_content}))

    round_ok = all(x.ok for x in records if x.task_id.endswith(r))
    print(f"[round {round_no}] {'全绿 ✅' if round_ok else '存在失败 ❌'}"
          f"（累计 {len(records)} 条记录）")
    return round_ok


async def main() -> int:
    from gateway.bridge_a2a import A2aClient, fetch_agent_card

    records: list[TaskRecord] = []
    procs: list[subprocess.Popen] = []
    started = time.strftime("%Y-%m-%d %H:%M:%S")
    try:
        procs = await boot_stack()
        async with httpx.AsyncClient() as http:
            for name, port in (("上游A2A", PORT_UP_A2A), ("A2A", PORT_A2A), ("AIP", PORT_AIP)):
                t0 = time.monotonic()
                ups: dict = {}
                while time.monotonic() - t0 < 30:
                    h = (await http.get(f"http://127.0.0.1:{port}/health")).json()
                    ups = h.get("upstreams", {})
                    if ups and all(u.get("online") for u in ups.values()):
                        break
                    await asyncio.sleep(0.5)
                else:
                    raise RuntimeError(f"{name} 面（{port}）上游 30s 未全部在线：{ups}")
                print(f"[boot] {name}({port}) 全部上游在线（{int((time.monotonic()-t0)*1000)}ms）："
                      f"{ {ns: len(u.get('tools') or {}) if isinstance(u.get('tools'), dict) else u.get('tools') for ns, u in ups.items()} }")

        card = await fetch_agent_card(f"http://127.0.0.1:{PORT_A2A}")
        all_green = True
        for round_no in range(1, ROUNDS + 1):
            all_green &= await run_round(round_no, records, card)

    finally:
        teardown(procs)

    # ── 汇总与落盘 ──
    groups: dict[str, list[TaskRecord]] = {}
    for r in records:
        groups.setdefault(r.group, []).append(r)

    def agg(rs: list[TaskRecord]) -> dict:
        n = len(rs)
        ok = sum(1 for r in rs if r.ok)
        lat = sorted(r.latency_ms for r in rs)
        return {"tasks": n, "success_rate": ok / n if n else 0.0,
                "latency_avg_ms": sum(lat) // n if n else 0,
                "latency_p50_ms": lat[len(lat) // 2] if lat else 0,
                "latency_max_ms": max(lat) if lat else 0,
                "round_trips_avg": round(sum(r.round_trips for r in rs) / n, 2) if n else 0}

    summary = {g: agg(rs) for g, rs in groups.items()}
    total = agg(records)
    # 轮次分布：每轮成功率 + E 组逐轮时延（级联开销分布观察用）
    rounds_out = []
    for round_no in range(1, ROUNDS + 1):
        rs = [x for x in records if x.task_id.endswith(f"r{round_no}")]
        rounds_out.append({"round": round_no, "tasks": len(rs),
                           "success_rate": sum(1 for x in rs if x.ok) / len(rs) if rs else 0.0,
                           "e1_ms": next((x.latency_ms for x in rs if x.task_id.startswith("E1")), None),
                           "e2_ms": next((x.latency_ms for x in rs if x.task_id.startswith("E2")), None),
                           "a3_ms": next((x.latency_ms for x in rs if x.task_id.startswith("A3")), None)})
    out_json = {"started_at": started, "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                "version": "0.2", "rounds_planned": ROUNDS,
                "tasks": [asdict(r) for r in records], "groups": summary, "total": total,
                "rounds": rounds_out,
                "note": "v0.2：12 项 × 5 轮；E1=AIP→a2a上游→8070→MCP（3RT），E2=A2A客户端→8060→8070→MCP（2RT）；"
                        "B/C 组卡发现组内均摊，D1 单独计时"}
    (ROOT / "bench" / "bench_results.json").write_text(
        json.dumps(out_json, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"\n万桥互操作基准 v0.2（{started} 启动，{ROUNDS} 轮 × 12 项）")
    print(f"{'任务':<8} {'路径':<14} {'结果':<4} {'时延ms':>7} {'往返':>4}  说明")
    for r in records:
        print(f"{r.task_id:<8} {r.path:<14} {'✅' if r.ok else '❌':<4} {r.latency_ms:>7} {r.round_trips:>4}  {r.detail[:60]}")
    print(f"\n总计：{total['tasks']} 任务，成功率 {total['success_rate']:.0%}，"
          f"平均时延 {total['latency_avg_ms']}ms，平均往返 {total['round_trips_avg']}")
    for g, s in summary.items():
        print(f"  [{g}] 成功率 {s['success_rate']:.0%} | avg {s['latency_avg_ms']}ms | p50 {s['latency_p50_ms']}ms | 往返 {s['round_trips_avg']}")
    print("\n轮次分布（级联开销观察：E1/E2 vs A3 同轮直连）：")
    for rd in rounds_out:
        print(f"  round {rd['round']}: 成功率 {rd['success_rate']:.0%} | A3={rd['a3_ms']}ms | E1={rd['e1_ms']}ms | E2={rd['e2_ms']}ms")
    ok = total["success_rate"] == 1.0
    print(f"\n基准 v0.2（n={ROUNDS}）：{'全绿 ✅' if ok else '存在失败项 ❌（明细见上与 bench/bench_results.json）'}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
