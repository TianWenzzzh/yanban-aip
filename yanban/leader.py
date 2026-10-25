"""研伴 Leader API + CLI 入口（端口 8041）。

POST /task        {"query": "..."} → {"task_id": "..."}
GET  /task/{id}   → 编排状态/计划/子任务/最终报告
GET  /health      → Leader 健康
CLI：uv run python leader.py "研究问题"（同步跑完打印报告）
"""

from __future__ import annotations

import asyncio
import sys
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

from orchestrator import Orchestrator

# 研伴 2.0 认知层（feature/companion-2.0）：/companion/* 全部端点
try:
    from companion.api.router import router as companion_router
    _HAS_COMPANION = True
except Exception as _exc:  # noqa: BLE001 — companion 未就绪时不影响既有功能
    companion_router = None
    _HAS_COMPANION = False
    import logging
    logging.getLogger("yanban.leader").warning("companion 层未加载: %s", _exc)

CONFIG = Path(__file__).parent / "partners.toml"
LEADER_PORT = 8041

orch: Orchestrator | None = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global orch
    orch = Orchestrator(CONFIG)
    yield


app = FastAPI(title="研伴 Leader", lifespan=lifespan)

if _HAS_COMPANION:
    app.include_router(companion_router)
    # 认知层那张命名工具表补上一条**会被审计**的入站入口（为什么必须是这条、以及它只放读类工具，
    # 理由写在 tool_rpc 的模块说明里）。身份绑定与伙伴侧同一个开关，不在这里另立一套。
    import tool_rpc
    from identity_mode import identity_binding_enabled as _identity_binding
    from orchestrator import load_partners as _load_partners

    tool_rpc.install(app, routes=companion_router.routes, aic=_load_partners(CONFIG)[1],
                     name="yanban-leader", log_dir=Path(__file__).parent / "logs",
                     identity_binding_enabled=_identity_binding())


class SubmitBody(BaseModel):
    query: str


@app.post("/task")
async def submit_task(body: SubmitBody) -> dict:
    if not body.query.strip():
        raise HTTPException(400, "query 不能为空")
    task_id = await orch.submit(body.query.strip())
    return {"task_id": task_id}


@app.get("/task/{task_id}")
async def get_task(task_id: str) -> dict:
    r = await orch.get(task_id)
    if r is None:
        raise HTTPException(404, "task 不存在")
    return {
        "task_id": r.task_id,
        "status": r.status,
        "query": r.query,
        "plan": r.plan,
        "subresults": [
            {"partner": s.partner, "state": s.state, "duration_ms": s.duration_ms,
             "rpc_calls": s.rpc_calls, "error": s.error, "source": s.source}
            for s in r.subresults
        ],
        "final_report": r.final_report,
        "quality_gate": r.gate.to_dict(),
        "error": r.error,
        "elapsed_ms": int((r.finished_at or 0) and (r.finished_at - r.started_at) * 1000),
    }


@app.get("/debug")
async def debug() -> dict:
    import orchestrator

    def _has_str(code, needle):
        for c in code.co_consts:
            if isinstance(c, str) and needle in c:
                return True
            if hasattr(c, "co_consts") and _has_str(c, needle):
                return True
        return False

    return {
        "orchestrator_file": orchestrator.__file__,
        "run_marker_in_bytecode": _has_str(orchestrator.Orchestrator._run.__code__, "[run] start"),
        "gate_call_in_bytecode": _has_str(orchestrator.Orchestrator._run.__code__, "run_gate"),
        "quality_gate_file": __import__("quality_gate").__file__,
        "qgate_marker_in_bytecode": _has_str(__import__("quality_gate").run_gate.__code__, "[qgate] called"),
    }


@app.get("/ui", response_class=HTMLResponse)
async def workbench() -> str:
    return (Path(__file__).parent / "ui.html").read_text(encoding="utf-8")


@app.get("/health")
async def health() -> dict:
    return {
        "status": "ok",
        "leader_aic": orch.leader_aic if orch else None,
        "partners": list(orch._endpoints) if orch else [],
    }


# ── 只读增强端点（2026-09-18 工作台夜班，R5：仅解析已有文件 / 转发已有 API，零业务逻辑改动）──

BENCH_MD = Path(__file__).parent / "bench-results-20260917.md"
QUALITY_JSON = Path(__file__).parent / "docs" / "quality_experiment_data_v2.json"
LOG_DIR = Path(__file__).parent / "logs"
GATEWAY_TOOL = "yanban__literature_search"  # 确定性 demo MCP 工具（demo_mcp_server.py，零 LLM 调用）

_METRIC_DEFS = {
    "Task Completion Rate": ("任务完成率", "task_completion"),
    "Partial Completion Rate": ("部分完成率", "partial_completion"),
    "RPC Success Rate": ("RPC 成功率", "rpc_success"),
    "Capability Coverage": ("能力覆盖", "capability_coverage"),
}
_GROUP_NAMES = {
    "G1": "单 Agent",
    "G2": "多 Agent 无闭环",
    "GA": "闭环·flash重写",
    "GB": "闭环·意见注入",
    "GC": "闭环·plus重写",
    "GD": "闭环·Best-of-2选优",
}
_RUBRIC_CN = {"rigor": "严谨性", "coverage": "覆盖度", "citation": "引用规范", "structure": "结构完整", "density": "信息密度"}
_WELL_KNOWN = ("/.well-known/agent-card.json", "/.well-known/agent.json")  # 与 bridge_a2a 双路径一致


async def agent_card() -> dict:
    """Leader 自己发 ACS：平台照登记的公网入口来取卡时不再 404，取到的永远是代码现造的当前版本。

    为什么挂在 Leader 而不是只靠网关：登记 URL（`https://47.114.38.125.sslip.io/rpc`）经 caddy 落到
    leader:18031，而发卡原本是网关 A2A 面的事 ⇒ 实测两条 well-known 都 404（轮 298 只读勘察）。
    """
    import leader_card

    return leader_card.build(aic=(orch.leader_aic if orch else None) or leader_card.LEADER_AIC)


for _p in _WELL_KNOWN:  # 注册与发现共用同一份常量 ⇒ 不可能只挂上一条路径
    app.add_api_route(_p, agent_card, methods=["GET"])


@app.get("/stats/bench")
async def stats_bench() -> dict:
    """解析 bench-results-20260917.md（固定格式行）→ 诚实四指标 + Partner 耗时表。"""
    import re

    text = BENCH_MD.read_text(encoding="utf-8")
    metrics = []
    for line in text.splitlines():
        m = re.match(
            r"\| (Task Completion Rate|Partial Completion Rate|RPC Success Rate|Capability Coverage) \| (.+?) \| (\d+/\d+) = (\d+)% \|",
            line,
        )
        if m:
            cn, key = _METRIC_DEFS[m.group(1)]
            metrics.append({"key": key, "name": m.group(1), "cn": cn, "definition": m.group(2),
                            "fraction": m.group(3), "value": int(m.group(4)) / 100})
    rpc_m = re.search(r"跨智能体 RPC 总次数 \| (\d+)", text)
    per_partner = [
        {"partner": p, "calls": int(c), "avg_ms": int(a), "median_ms": int(med)}
        for p, c, a, med in re.findall(r"- (\w+) 调用 (\d+) 次：平均 (\d+)ms / 中位 (\d+)ms", text)
    ]
    return {
        "bench_date": "2026-09-17",
        "source_file": "bench-results-20260917.md",
        "metrics": metrics,
        "rpc_total": int(rpc_m.group(1)) if rpc_m else 0,
        "per_partner": per_partner,
    }


@app.get("/stats/quality")
async def stats_quality() -> dict:
    """解析 docs/quality_experiment_data_v2.json → 各策略均分/幻觉 + rubric 五维均分。"""
    import json
    import statistics

    raw = json.loads(QUALITY_JSON.read_text(encoding="utf-8"))
    per_group: dict[str, list] = {}
    for groups in raw.get("topics", {}).values():
        for g, samples in groups.items():
            per_group.setdefault(g, []).extend(s for s in samples if isinstance(s, dict) and s.get("ok"))
    strategies = []
    rubric_acc: dict[str, list[float]] = {}
    for g, samples in per_group.items():
        scores = [float(s.get("score") or 0) for s in samples]
        halluc = [len(s.get("hallucinations") or []) for s in samples]
        strategies.append({"group": g, "name": _GROUP_NAMES.get(g, g),
                           "avg": round(statistics.fmean(scores), 2), "min": min(scores),
                           "avg_halluc": round(statistics.fmean(halluc), 1), "n": len(samples)})
        for s in samples:
            for dim, v in ((s.get("rubric") or {}).get("scores") or {}).items():
                rubric_acc.setdefault(dim, []).append(float(v))
    rubric_dims = [{"dim": d, "cn": _RUBRIC_CN.get(d, d), "avg": round(statistics.fmean(vs), 2)}
                   for d, vs in sorted(rubric_acc.items())]
    return {
        "source_files": ["docs/quality_experiment_v2.md", "docs/quality_experiment_data_v2.json"],
        "started_at": raw.get("started_at", ""),
        "n_samples": sum(len(v) for v in per_group.values()),
        "strategies": strategies,
        "rubric_dims": rubric_dims,
    }


@app.get("/amp/latest")
async def amp_latest(n: int = 50) -> dict:
    """logs/ 下 amp_access_*.jsonl 最新 n 行（默认 50），字段口径同 amp_report.py。"""
    import json

    recs: list[dict] = []
    for f in LOG_DIR.glob("amp_access_*.jsonl"):
        for line in f.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                r = json.loads(line)
            except json.JSONDecodeError:
                continue
            body = r.get("body", {})
            req = body.get("request", {})
            recs.append({
                "ts": (r.get("timestamp") or "")[:19].replace("T", " "),
                "caller": (body.get("caller") or {}).get("serviceName") or (body.get("caller") or {}).get("aic", "?"),
                "callee": (body.get("callee") or {}).get("serviceName", ""),
                "action": f"{req.get('method', '?')} {req.get('route', '')}".strip(),
                "duration_ms": body.get("durationMs"),
                "code": (body.get("response") or {}).get("statusCode", 0),
                "file": f.name,
            })
    recs.sort(key=lambda x: x["ts"])
    return {"count": len(recs[-n:]), "records": recs[-n:]}


@app.get("/identity")
async def identity() -> dict:
    """identity mode 当前态 + Partner 注册表（只读）。"""
    import identity_mode

    return {
        "identity_mode": identity_mode.identity_mode(),
        "binding_enabled": identity_mode.identity_binding_enabled(),
        "leader_aic": orch.leader_aic if orch else None,
        # endpoint 报的是**宿主机那份**（load_partners 在容器网络模式下会改写 endpoint 用于路由，
        # 覆盖前留了 host_endpoint）。两列一样 = 这一行抄了被改写后的值，见轮 141 看图的发现。
        "partners": [{"name": p["name"],
                      "endpoint": p.get("host_endpoint") or p["endpoint"],
                      "compose_endpoint": p.get("compose_endpoint")}
                     for p in orch.partners] if orch else [],
        "source": "identity_mode.py + partners.toml（经 Leader 只读端点返回）",
    }


def _gateway_base() -> str:
    """网关 base（去 /rpc 后缀）；容器网络模式下 load_partners 已切 compose_endpoint。"""
    ep = (orch._endpoints.get("mcp_gateway") if orch else None) or "http://localhost:8021/rpc"
    return ep[:-4] if ep.endswith("/rpc") else ep


@app.get("/protocol/aip")
async def protocol_aip(query: str = "大模型多智能体协作") -> dict:
    """演示：网关 AIP 面（POST /rpc）调 literature_search（确定性工具，零 LLM，可重复演示）。"""
    import json
    import time
    import uuid

    from acps_sdk.aip import AipRpcClient, TaskState, TextDataItem

    endpoint = _gateway_base() + "/rpc"
    t0 = time.monotonic()
    rts = 0
    sid, tid = f"s-proto-{uuid.uuid4().hex[:8]}", f"t-proto-{uuid.uuid4().hex[:8]}"
    try:
        client = AipRpcClient(partner_url=endpoint, leader_id=orch.leader_aic if orch else "yanban-leader",
                              identity_binding_enabled=False)
        try:
            task = await client.start_task(
                session_id=sid, task_id=tid,
                user_input=json.dumps({"tool": GATEWAY_TOOL, "arguments": {"topic": query, "limit": 3}}, ensure_ascii=False))
            rts += 1
            texts: list[str] = []
            if task.status.state == TaskState.AwaitingCompletion:
                for p in task.products or []:
                    texts += [i.text for i in (p.dataItems or []) if isinstance(i, TextDataItem)]
                task = await client.complete_task(task_id=tid, session_id=sid)
                rts += 1
            state = str(task.status.state)
            return {"face": "AIP", "round_trips": rts, "elapsed_ms": int((time.monotonic() - t0) * 1000),
                    "result_text": "\n".join(texts), "endpoint": endpoint,
                    "error": "" if state.endswith("Completed") else f"终态 {state}"}
        finally:
            await client.close()
    except Exception as exc:  # noqa: BLE001 — 演示端点：失败也结构化返回
        return {"face": "AIP", "round_trips": rts, "elapsed_ms": int((time.monotonic() - t0) * 1000),
                "result_text": "", "endpoint": endpoint, "error": str(exc)[:200]}


@app.get("/protocol/a2a")
async def protocol_a2a(query: str = "大模型多智能体协作") -> dict:
    """演示：网关 A2A 面（AgentCard 发现 + tasks/send，语义同 bridge_a2a.A2aClient.call_skill）。"""
    import json
    import time
    import uuid

    import httpx

    base = _gateway_base()
    t0 = time.monotonic()
    try:
        async with httpx.AsyncClient(timeout=30) as http:
            card_url = None
            for p in _WELL_KNOWN:
                r = await http.get(base + p)
                if r.status_code == 200:
                    card_url = r.json().get("url") or (base + "/")
                    break
            if card_url is None:
                raise RuntimeError("AgentCard 双路径 404")
            # Card.url 是网关对外自报地址（compose 场景可能指向宿主机 localhost，
            # 容器内 Leader 不可达）：连接失败时回落到本注册端点，保持演示可用
            post_url = card_url

            async def _send(url: str) -> httpx.Response:
                payload = {
                    "jsonrpc": "2.0", "id": str(uuid.uuid4()), "method": "tasks/send",
                    "params": {
                        "id": f"t-proto-{uuid.uuid4().hex[:8]}", "sessionId": f"s-proto-{uuid.uuid4().hex[:8]}",
                        "message": {"role": "user", "parts": [{"type": "text", "text": json.dumps(
                            {"tool": GATEWAY_TOOL, "arguments": {"topic": query, "limit": 3}}, ensure_ascii=False)}]},
                    },
                }
                return await http.post(url, json=payload)

            try:
                r = await _send(card_url)
            except httpx.ConnectError:
                post_url = base + "/"
                r = await _send(post_url)
            if r.status_code >= 400:
                raise RuntimeError(f"tasks/send HTTP {r.status_code}")
            result = (r.json() or {}).get("result") or {}
            state = (result.get("status") or {}).get("state", "")
            texts = []
            for a in result.get("artifacts") or []:
                for part in a.get("parts") or []:
                    if part.get("type") == "text":
                        texts.append(part.get("text") or "")
            return {"face": "A2A", "round_trips": 2, "elapsed_ms": int((time.monotonic() - t0) * 1000),
                    "result_text": "\n".join(texts), "endpoint": post_url,
                    "error": "" if state == "completed" else f"终态 {state}"}
    except Exception as exc:  # noqa: BLE001
        return {"face": "A2A", "round_trips": 2, "elapsed_ms": int((time.monotonic() - t0) * 1000),
                "result_text": "", "endpoint": base, "error": str(exc)[:200]}


# webui/dist 静态托管（P6）：挂载在全部 API 路由之后，/ 仍优先命中上方已注册路由；
# 旧 ui.html 的 /ui 路由保留兜底。
_DIST = Path(__file__).parent / "webui" / "dist"
if _DIST.is_dir():
    from fastapi.staticfiles import StaticFiles

    # Windows 的 mimetypes 走注册表，那里常把 .js 记成 text/plain；StaticFiles 照它发头，
    # 浏览器对 module script 强制 MIME 检查 ⇒ 本机起服务整页白屏（云端 Linux 不受影响，所以从没被发现）。
    import mimetypes

    for _ext, _mt in ((".js", "application/javascript"), (".mjs", "application/javascript"),
                      (".css", "text/css"), (".json", "application/json"), (".svg", "image/svg+xml")):
        mimetypes.add_type(_mt, _ext)
    app.mount("/", StaticFiles(directory=_DIST, html=True), name="webui")


def _cli() -> None:
    if len(sys.argv) < 2:
        print('用法：uv run python leader.py "研究问题"')
        sys.exit(1)

    async def main() -> None:
        o = Orchestrator(CONFIG)
        task_id = await o.submit(sys.argv[1])
        r = await o.get(task_id)
        assert r is not None
        while r.status == "running":
            await asyncio.sleep(2)
        if r.status == "completed":
            print(r.final_report)
            print(f"\n--- 子任务：{[(s.partner, s.state, f'{s.duration_ms}ms') for s in r.subresults]}")
        else:
            print(f"FAILED: {r.error}")
            sys.exit(2)

    asyncio.run(main())


if __name__ == "__main__":
    _cli()
