"""MCP↔AIP 网关 Partner（v1.1：多上游聚合 + 探活撤架）。

对外：一个 AIP Partner，聚合 config.toml 里全部上游 MCP Server 的工具。
AIP start 输入：{"tool": "ns__name" 或裸 name, "arguments": {...}}
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import time
from contextlib import asynccontextmanager
from pathlib import Path

from acps_sdk.aip import (
    Product,
    TaskCommand,
    TaskResult,
    TaskState,
    TextDataItem,
)
from acps_sdk.aip.access_status import status_from_rpc_response
from acps_sdk.aip.aip_rpc_model import RpcResponse
from acps_sdk.aip.aip_rpc_server import (
    CommandHandlers,
    DefaultHandlers,
    TaskManager,
    handle_rpc_request,
)
from acps_sdk.amp import (
    AccessBody,
    AccessEmitter,
    AccessParticipant,
    AccessRequest,
    AccessResponse,
    ErrorInfo,
    HeartbeatEmitter,
)
from fastapi import FastAPI, Request

from gateway.registry import McpRegistry, call_result_is_error

GATEWAY_AIC = "1.2.156.3088.1.1.MCPGW1.GATE01.1.0001"
GATEWAY_PORT = int(os.environ.get("GATEWAY_PORT", "8021"))
AMP_LOG_DIR = Path(os.environ.get("AMP_LOG_DIR", "./logs"))
AMP_LOG_DIR.mkdir(parents=True, exist_ok=True)

heartbeat = HeartbeatEmitter(AMP_LOG_DIR / "amp_heartbeat_mcp-gateway.jsonl", aic=GATEWAY_AIC)
access_emitter = AccessEmitter(AMP_LOG_DIR / "amp_access_mcp-gateway.jsonl", aic=GATEWAY_AIC)

registry = McpRegistry(
    config_path=Path(os.environ.get("GATEWAY_CONFIG", Path(__file__).parent / "config.toml")),
    endpoint_url=f"http://localhost:{GATEWAY_PORT}/rpc",
    gateway_aic=GATEWAY_AIC,
)
hb_task: asyncio.Task | None = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global hb_task
    await registry.start()
    hb_task = asyncio.create_task(heartbeat.run_periodic(15), name="amp-heartbeat")
    yield
    hb_task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await hb_task
    await registry.stop()


app = FastAPI(title="MCP-AIP Gateway Partner", lifespan=lifespan)


def _with_sender(task: TaskResult) -> TaskResult:
    task.senderId = GATEWAY_AIC
    return task


async def on_start(command: TaskCommand, task: TaskResult | None) -> TaskResult:
    if task:
        return _with_sender(task)

    raw = ""
    for item in command.dataItems or []:
        if isinstance(item, TextDataItem):
            raw = item.text
            break

    try:
        req = json.loads(raw)
        skill_id = str(req.get("tool", ""))
        arguments = req.get("arguments") or {}
    except (json.JSONDecodeError, AttributeError):
        # 宽容解析：LLM 常把输入写成 "tool__name {\"k\": v}" 拼接文本
        import re

        m = re.match(r"^([\w:__.-]+)\s+(\{.*\})\s*$", raw.strip(), re.S)
        if m:
            skill_id = m.group(1)
            try:
                arguments = json.loads(m.group(2))
            except json.JSONDecodeError:
                return _with_sender(TaskManager.create_task(
                    command, initial_state=TaskState.Rejected,
                    data_items=[TextDataItem(text=f"arguments 不是合法 JSON：{raw[:120]}")],
                ))
        else:
            return _with_sender(TaskManager.create_task(
                command,
                initial_state=TaskState.Rejected,
                data_items=[TextDataItem(text=f'输入必须是 JSON：{{"tool": "ns__name", "arguments": {{...}}}}，收到：{raw[:120]}')],
            ))

    hit = registry.resolve(skill_id)
    if hit is None:
        available = sorted(registry.all_tools())
        return _with_sender(TaskManager.create_task(
            command,
            initial_state=TaskState.Rejected,
            data_items=[TextDataItem(text=f"未知或已下架工具 {skill_id!r}。当前可用：{available}")],
        ))

    state, tool = hit
    ns, tool_name = state.spec.ns, tool.name
    task = TaskManager.create_task(command, initial_state=TaskState.Working)
    try:
        result = await registry.call(ns, tool_name, arguments)
    except Exception as exc:  # noqa: BLE001
        return _with_sender(TaskManager.update_task_status(
            task.taskId,
            TaskState.Failed,
            data_items=[TextDataItem(text=f"MCP 调用失败（上游 {ns}/{tool_name}）：{exc}")],
        ))

    texts = [c.text for c in (result.content or []) if hasattr(c, "text")]
    payload = "\n".join(texts) or json.dumps({"isError": call_result_is_error(result)}, ensure_ascii=False)
    TaskManager.set_products(
        task.taskId,
        [Product(id=f"mcp-{ns}-{tool_name}", name=tool_name, dataItems=[TextDataItem(text=payload)])],
    )
    updated = TaskManager.update_task_status(task.taskId, TaskState.AwaitingCompletion)
    return _with_sender(updated)


handlers = CommandHandlers(
    on_start=on_start,
    on_get=DefaultHandlers.get,
    on_cancel=DefaultHandlers.cancel,
    on_complete=DefaultHandlers.complete,
)


@app.post("/rpc", response_model=RpcResponse)
async def rpc_endpoint(request: Request) -> RpcResponse:
    raw = await request.body()
    resp: RpcResponse | None = None
    error_info = None
    t0 = time.monotonic()
    try:
        resp = await handle_rpc_request(request, handlers, local_aic=GATEWAY_AIC, identity_binding_enabled=False)
        code = status_from_rpc_response(resp)
    except Exception as exc:  # noqa: BLE001
        error_info = ErrorInfo(code=500, message=str(exc))
        raise
    finally:
        try:
            await access_emitter.emit(
                AccessBody(
                    request=AccessRequest(method="rpc", url="/rpc", route="/rpc", bodySizeBytes=len(raw)),
                    response=AccessResponse(statusCode=code, bodySizeBytes=len(resp.model_dump_json()) if resp else 0),
                    caller=AccessParticipant(aic="leader", serviceName="leader"),
                    callee=AccessParticipant(aic=GATEWAY_AIC, serviceName="mcp-aip-gateway"),
                    error=error_info,
                    durationMs=int((time.monotonic() - t0) * 1000),
                )
            )
        except Exception:  # noqa: BLE001
            pass
    return resp


@app.get("/health")
async def health() -> dict:
    return {
        "status": "ok",
        "aic": GATEWAY_AIC,
        "upstreams": {
            s.spec.ns: {"online": s.online, "tools": len(s.tools), "error": s.error}
            for s in registry.states.values()
        },
    }


@app.get("/acs")
async def acs() -> dict:
    """聚合 ACS（随上游上下架动态变化，可直接提交梧桐）。"""
    return registry.build_acs()
