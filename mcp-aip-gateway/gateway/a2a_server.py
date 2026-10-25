"""网关自身暴露为 A2A Agent（万桥方向二：A2A ← 网关聚合能力）。

设计依据：docs/design_wanbridge_a2a.md §2（规范核实记录见该文档头部，R9 合规）。
- 动态 AgentCard：聚合 ACS → AgentSkill（id 去命名空间尾段 / description 首段 /
  tags 去内部标签 / examples 原样），well-known 双路径同卡（agent-card.json 新版 + agent.json 兼容）
- POST / 与 /rpc：tasks/send 与 message/send 双方法名同义处理（§2.2，v0.2-3 与最新规范差异兼容）
- 入参 Parts：JsonPart/DataPart 一等公民（{"tool","arguments"} 直接给）；
  TextPart 保底给只会发文本的客户端（与 AIP dataItems 同契约 + 宽容解析）
- 出参：Task{id, status, artifacts:[TextPart(输出合并文本)], sessionId}
- 未知工具 → failed + 可用工具清单（A2A 无 Rejected 态，消息体里保留清单供自纠错）
- 非法 JSON-RPC → 标准错误对象（-32700/-32600/-32601/-32602）

纯函数与 IO 分离：build_agent_card / extract_invocation / handle_jsonrpc 不碰网络，
可完全离线测试；create_a2a_app 负责与 McpRegistry 接线。
"""

from __future__ import annotations

import contextlib
import json
import os
import re
import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Awaitable, Callable

from acps_sdk.amp import AccessBody, AccessEmitter, AccessParticipant, AccessRequest, AccessResponse, ErrorInfo
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response

from gateway.registry import McpRegistry, call_result_is_error

WANBRIDGE_VERSION = "2.0.0a1"
A2A_PROTOCOL_VERSION = "0.3.0"
A2A_SEND_METHODS = ("tasks/send", "message/send")
A2A_STREAM_METHODS = ("message/stream", "tasks/sendSubscribe")  # v0.3 正名 + v0.2 别名（§2.4）
A2A_METHODS = A2A_SEND_METHODS + ("tasks/get", "tasks/cancel", "tasks/resubscribe")
INTERNAL_TAGS = {"MCP工具", "A2A技能"}
DEFAULT_PORT = 8060  # 开发/演示端口红线：≥8060（8021-8041 属在役系统）

# §2.3（R12 核实：a2a-sdk 1.1.2 default_request_handler.py:71-76）：
# 终态任务不可取消（-32002）。rejected 为 v0.3 新增态，万桥 v0.2-3 形态不产出但取消语义兼容。
A2A_TERMINAL_STATES = {"completed", "failed", "canceled", "rejected"}
A2A_ERR_TASK_NOT_FOUND = -32001
A2A_ERR_TASK_NOT_CANCELABLE = -32002


class TaskNotFoundError(LookupError):
    """tasks/get / tasks/cancel：任务表中无此 id（→ JSON-RPC -32001）。"""


class TaskNotCancelableError(RuntimeError):
    """tasks/cancel：任务已处终态（→ JSON-RPC -32002，消息带当前状态）。"""


def _first_line(text: str) -> str:
    for line in str(text or "").splitlines():
        if line.strip():
            return line.strip()
    return ""


def acs_skill_to_agent_skill(skill: dict[str, Any]) -> dict[str, Any]:
    """聚合 ACS 的一个 skill → A2A AgentSkill（§2.1 映射：尾段 id/首段描述/去内部标签）。"""
    skill_id = str(skill.get("id") or "")
    ns, _, tail = skill_id.partition("__")
    tags = [str(t) for t in (skill.get("tags") or []) if t not in INTERNAL_TAGS and t != ns]
    return {
        "id": tail or skill_id,
        "name": str(skill.get("name") or (tail or skill_id)),
        "description": _first_line(skill.get("description") or ""),
        "tags": tags,
        "examples": list(skill.get("examples") or []),
    }


def build_agent_card(acs: dict[str, Any], *, self_url: str) -> dict[str, Any]:
    """聚合 ACS → 动态 AgentCard（§2.1；字段尽量全给，客户端解析按可选处理）。"""
    skills = [acs_skill_to_agent_skill(s) for s in (acs.get("skills") or [])]
    upstreams = {str(s.get("id") or "").partition("__")[0] for s in (acs.get("skills") or [])}
    upstreams.discard("")
    return {
        "name": "wanbridge-gateway",
        "description": "MCP↔AIP↔A2A 全协议互操作网关（万桥）",
        "url": self_url,
        "version": WANBRIDGE_VERSION,
        "protocolVersion": A2A_PROTOCOL_VERSION,
        "provider": {"organization": "WanBridge contributors", "url": ""},
        "capabilities": {"streaming": True, "pushNotifications": False, "stateTransitionHistory": False},
        "defaultInputModes": ["text/plain", "application/json"],
        "defaultOutputModes": ["text/plain", "application/json"],
        "authentication": {"schemes": []},
        "skills": skills,
        "metadata": {"aic": str(acs.get("aic") or ""), "upstreams": len(upstreams), "tools": len(skills)},
    }


def _parse_invocation_text(raw: str) -> tuple[str, dict[str, Any]] | None:
    """TextPart 文本 → 调用对：约定 JSON 优先，宽容解析 LLM 拼接风格（同 partner.py）。"""
    try:
        req = json.loads(raw)
    except (json.JSONDecodeError, ValueError):
        req = None
    if isinstance(req, dict) and "tool" in req:
        args = req.get("arguments")
        return str(req["tool"]), dict(args) if isinstance(args, dict) else {}
    m = re.match(r"^([\w:__.-]+)\s+(\{.*\})\s*$", raw.strip(), re.S)
    if m:
        try:
            args = json.loads(m.group(2))
        except json.JSONDecodeError:
            return None
        return m.group(1), args if isinstance(args, dict) else {}
    return None


def extract_invocation(parts: list[dict[str, Any]] | None) -> tuple[str, dict[str, Any]] | None:
    """从 message.parts 提取 {tool, arguments}：JsonPart/DataPart 一等公民，TextPart 保底。"""
    for part in parts or []:
        kind = part.get("type") or part.get("kind") or ""
        if kind in ("data", "json"):
            payload = part["data"] if "data" in part else part.get("json")
            if isinstance(payload, dict) and "tool" in payload:
                args = payload.get("arguments")
                return str(payload["tool"]), dict(args) if isinstance(args, dict) else {}
        elif kind == "text":
            hit = _parse_invocation_text(str(part.get("text") or ""))
            if hit:
                return hit
    return None


async def handle_tasks_send(
    params: dict[str, Any],
    *,
    resolve: Callable[[str], Any],
    call: Callable[..., Awaitable[Any]],
    available_ids: Callable[[], list[str]],
    store: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """tasks/send / message/send 共同执行体：定位工具 → 上游调用 → Task 结果（§2.2）。

    store（内存任务表，§2.3）：传入时每次执行结果（含 failed）按 task_id 记录，
    供 tasks/get 检索与 tasks/cancel 判态；不传则用一次性局部表（纯调用无副作用语义不变）。
    """
    task_id = str(params.get("id") or uuid.uuid4())
    session_id = str(params.get("sessionId") or uuid.uuid4())
    store = store if store is not None else {}

    def task(state: str, message_text: str | None = None, artifact_text: str | None = None) -> dict[str, Any]:
        out: dict[str, Any] = {"id": task_id, "sessionId": session_id, "status": {"state": state}}
        if message_text:
            out["status"]["message"] = {"role": "agent", "parts": [{"type": "text", "text": message_text}]}
        if artifact_text is not None:
            out["artifacts"] = [{"name": "result", "parts": [{"type": "text", "text": artifact_text}]}]
        return out

    invocation = extract_invocation((params.get("message") or {}).get("parts"))
    if invocation is None:
        out = task(
            "failed",
            '入参需提供工具调用：JsonPart {"tool": "ns__name", "arguments": {...}}'
            " 或 TextPart 同契约 JSON 文本。",
        )
        store[task_id] = out
        return out
    skill_id, arguments = invocation
    hit = resolve(skill_id)
    if hit is None:
        out = task("failed", f"未知或已下架工具 {skill_id!r}。当前可用：{sorted(available_ids())}")
        store[task_id] = out
        return out
    upstream_state, tool = hit
    ns, tool_name = upstream_state.spec.ns, tool.name
    try:
        result = await call(ns, tool_name, arguments)
    except Exception as exc:  # noqa: BLE001 — 上游失败翻译为 A2A failed（§2.2）
        out = task("failed", f"上游调用失败（{ns}/{tool_name}）：{exc}")
        store[task_id] = out
        return out
    texts = [c.text for c in (result.content or []) if hasattr(c, "text")]
    payload = "\n".join(texts) or json.dumps({"isError": call_result_is_error(result)}, ensure_ascii=False)
    out = task("completed", artifact_text=payload)
    store[task_id] = out
    return out


def search_tools(catalog: dict[str, tuple[Any, Any]], query: str, *, limit: int = 5) -> list[dict[str, Any]]:
    """/tools/search（W5）：聚合目录关键词检索 top-k——缓解"工具多选错"（国标发现模块的网关侧兜底）。

    评分 = 查询词（2-gram/英数词）在 skill id/name/description 中的命中数；零依赖（向量检索留接口）。
    """
    import re as _re

    def lexicon(text: str) -> set[str]:
        """英数词（含字符 2-gram）+ 中文 2-gram——与 discovery.py 词法对齐；
        纯 [a-z0-9_]+ 会把中文全滤掉（T3 容器实测：中文查询永空）。"""
        text = (text or "").lower()
        words = set(_re.findall(r"[a-z0-9_]+", text))
        grams = {w[i:i + 2] for w in words if len(w) > 1 for i in range(len(w) - 1)}
        for seg in _re.findall(r"[\u4e00-\u9fff]+", text):
            if len(seg) == 1:
                words.add(seg)
            for i in range(len(seg) - 1):
                grams.add(seg[i:i + 2])
        return words | grams

    tokens = lexicon(query)
    hits = []
    for full_id, (state, tool) in catalog.items():
        desc = str(getattr(tool, "description", "") or "")
        hay = lexicon(f"{full_id} {getattr(tool, 'name', '')} {desc}")
        score = len(tokens & hay)
        if score > 0:
            hits.append({"id": full_id, "name": getattr(tool, "name", ""),
                         "description": desc[:160], "score": score})
    hits.sort(key=lambda x: x["score"], reverse=True)
    return hits[:limit]


def _rpc_error(rpc_id: Any, code: int, message: str) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": rpc_id, "error": {"code": code, "message": message}}


def handle_tasks_get(params: dict[str, Any], *, store: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """tasks/get（§2.3）：按 id 返回任务表中的 Task；未知 id → TaskNotFoundError（-32001）。"""
    task = store.get(str(params.get("id") or ""))
    if task is None:
        raise TaskNotFoundError(str(params.get("id")))
    return task


def handle_tasks_cancel(params: dict[str, Any], *, store: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """tasks/cancel（§2.3）：未知 id → -32001；终态 → -32002（带当前状态）；
    非终态 → 置 canceled 并返回 Task（同步翻译层现不会产生非终态，为异步上游 v2 预留）。"""
    task_id = str(params.get("id") or "")
    task = store.get(task_id)
    if task is None:
        raise TaskNotFoundError(task_id)
    state = str((task.get("status") or {}).get("state") or "unknown")
    if state in A2A_TERMINAL_STATES:
        raise TaskNotCancelableError(f"Task cannot be canceled - current state: {state}")
    task["status"] = {"state": "canceled"}
    return task


async def handle_tasks_stream(
    params: dict[str, Any],
    *,
    resolve: Callable[[str], Any],
    call: Callable[..., Awaitable[Any]],
    available_ids: Callable[[], list[str]],
    store: dict[str, dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """message/stream / tasks/sendSubscribe（§2.4）。

    确定性翻译层的流式形态：working 帧 → artifact 帧（有产物时）→ 终态帧。
    事件形状按 a2a-sdk 1.1.2：{context_id, final, kind, status, task_id}（status-update）、
    {artifact, context_id, kind, task_id}（artifact-update）。任务照常落内存任务表。
    """
    task_id = str(params.get("id") or uuid.uuid4())
    session_id = str(params.get("sessionId") or uuid.uuid4())
    store = store if store is not None else {}

    def status_event(state: str, final: bool) -> dict[str, Any]:
        return {"kind": "status-update", "task_id": task_id, "context_id": session_id,
                "status": {"state": state}, "final": final}

    frames = [status_event("working", final=False)]
    result = await handle_tasks_send({**params, "id": task_id, "sessionId": session_id},
                                     resolve=resolve, call=call,
                                     available_ids=available_ids, store=store)
    state = str((result.get("status") or {}).get("state") or "failed")
    for artifact in result.get("artifacts") or []:
        frames.append({"kind": "artifact-update", "task_id": task_id, "context_id": session_id,
                       "artifact": artifact, "append": False})
    frames.append(status_event(state, final=True))
    return frames


def handle_tasks_resubscribe(params: dict[str, Any], *,
                             store: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    """tasks/resubscribe（§2.4）：按 id 从任务表重放事件流；未知 id → -32001（同 tasks/get）。"""
    task_id = str((params or {}).get("id") or "")
    task = store.get(task_id)
    if task is None:
        raise TaskNotFoundError(task_id)
    session_id = str(task.get("sessionId") or "")
    state = str((task.get("status") or {}).get("state") or "unknown")
    frames = [{"kind": "status-update", "task_id": task_id, "context_id": session_id,
               "status": {"state": state}, "final": False}]
    text = "\n".join(
        p.get("text", "") for a in (task.get("artifacts") or []) for p in (a.get("parts") or [])
        if isinstance(p, dict))
    if text:
        frames.append({"kind": "artifact-update", "task_id": task_id, "context_id": session_id,
                       "artifact": {"name": "result", "parts": [{"type": "text", "text": text}]},
                       "append": False})
    frames.append({"kind": "status-update", "task_id": task_id, "context_id": session_id,
                   "status": {"state": state}, "final": True})
    return frames


def _require_task_id(params: Any) -> str:
    """tasks/get、tasks/cancel 的 params 校验：id 为必填字符串（其余字段宽容忽略）。"""
    if not isinstance(params, dict) or not isinstance(params.get("id"), str) or not params["id"]:
        raise ValueError("params.id（string）为必填")
    return params["id"]


async def handle_jsonrpc(
    payload: Any,
    *,
    resolve: Callable[[str], Any],
    call: Callable[..., Awaitable[Any]],
    available_ids: Callable[[], list[str]],
    store: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """JSON-RPC 2.0 分发：send 双方法名同义 + tasks/get、tasks/cancel（§2.2/§2.3）；
    协议级错误走标准 error 对象。"""
    rpc_id = payload.get("id") if isinstance(payload, dict) else None
    if not isinstance(payload, dict) or payload.get("jsonrpc") != "2.0" or not isinstance(payload.get("method"), str):
        return _rpc_error(rpc_id, -32600, "Invalid Request")
    method = payload["method"]
    if method not in A2A_METHODS:
        return _rpc_error(rpc_id, -32601, f"Method not found: {method}")
    params = payload.get("params")
    if method in A2A_SEND_METHODS:
        if not isinstance(params, dict) or not isinstance(params.get("message"), dict):
            return _rpc_error(rpc_id, -32602, "params.message（object）为必填")
        result = await handle_tasks_send(params, resolve=resolve, call=call,
                                         available_ids=available_ids, store=store)
        return {"jsonrpc": "2.0", "id": rpc_id, "result": result}
    store = store if store is not None else {}
    try:
        task_id = _require_task_id(params)
        if method == "tasks/get":
            result = handle_tasks_get({"id": task_id}, store=store)
        else:
            result = handle_tasks_cancel({"id": task_id}, store=store)
    except ValueError as exc:
        return _rpc_error(rpc_id, -32602, str(exc))
    except TaskNotFoundError:
        return _rpc_error(rpc_id, A2A_ERR_TASK_NOT_FOUND, "Task not found")
    except TaskNotCancelableError as exc:
        return _rpc_error(rpc_id, A2A_ERR_TASK_NOT_CANCELABLE, str(exc))
    return {"jsonrpc": "2.0", "id": rpc_id, "result": result}


DepsFactory = Callable[[], dict[str, Any]]


def rrf_fuse(rank_lists: list[list[dict[str, Any]]], *, k: int = 60,
             limit: int = 5) -> list[dict[str, Any]]:
    """Reciprocal Rank Fusion（v6 升级3）：多路检索 rank 融合。

    RRF(d) = Σ 1/(k + rank_i(d))，k=60（Cormack et al. 2009 常规值）。
    只用 rank 不用原始分——关键词计数与余弦相似分布不可比，rank 融合免归一化。
    """
    fused: dict[str, float] = {}
    meta: dict[str, dict[str, Any]] = {}
    for results in rank_lists:
        for rank, item in enumerate(results, 1):
            fid = item["id"]
            fused[fid] = fused.get(fid, 0.0) + 1.0 / (k + rank)
            meta[fid] = item
    ordered = sorted(fused.items(), key=lambda x: x[1], reverse=True)[:limit]
    return [{**meta[fid], "rrf": round(score, 5)} for fid, score in ordered]


def _emit_audit(access_emitter, gateway_aic: str, req_bytes: int, resp_bytes: int,
                error: Any, t0: float) -> None:
    """统一审计发射（W5：JSON 与 SSE 双路径共用；审计失败不影响主响应）。"""
    if access_emitter is None:
        return
    error_info = None
    if isinstance(error, dict):
        error_info = ErrorInfo(code=int(error.get("code") or -32603),
                               message=str(error.get("message") or ""))
    with contextlib.suppress(Exception):
        access_emitter.emit_sync(AccessBody(
            request=AccessRequest(method="POST", url="/", route="/", bodySizeBytes=req_bytes),
            response=AccessResponse(statusCode=200, bodySizeBytes=resp_bytes),
            caller=AccessParticipant(aic="a2a-client", serviceName="a2a-client"),
            callee=AccessParticipant(aic=gateway_aic, serviceName="wanbridge-a2a"),
            error=error_info,
            durationMs=int((time.monotonic() - t0) * 1000),
        ))


async def _resubscribe_jsonrpc(payload: dict, *, store: dict | None = None, **_: Any) -> dict:
    """tasks/resubscribe 的 JSON-RPC 包装（SSE 事件帧以 frames 数组返回）。"""
    rpc_id = payload.get("id")
    params = payload.get("params")
    try:
        task_id = _require_task_id(params)
        frames = handle_tasks_resubscribe({"id": task_id}, store=store or {})
    except ValueError as exc:
        return _rpc_error(rpc_id, -32602, str(exc))
    except TaskNotFoundError:
        return _rpc_error(rpc_id, A2A_ERR_TASK_NOT_FOUND, "Task not found")
    return {"jsonrpc": "2.0", "id": rpc_id, "result": {"frames": frames}}


def _wire_routes(
    app: FastAPI,
    deps: DepsFactory,
    card_builder: Callable[[], dict[str, Any]],
    *,
    access_emitter: Any | None = None,
    gateway_aic: str = "wanbridge-gateway",
    include_rpc_route: bool = True,
) -> None:
    """挂 A2A 服务面路由（§2.1/§2.2）。

    access_emitter：传入 acps_sdk AccessEmitter 时，每次 JSON-RPC 往返写一条 AMP 审计
    （N4；文件名由调用方定，惯例 amp_access_a2a-<port>.jsonl；TestClient 注入 tmp_path）。
    include_rpc_route：双面同进程（gateway/app.py）时 AIP 面已占用 POST /rpc，
    A2A 面只挂 POST /（Card.url 指向 /），置 False 避免同路径双注册。
    """

    @app.get("/.well-known/agent-card.json")
    @app.get("/.well-known/agent.json")
    async def agent_card() -> dict[str, Any]:
        return card_builder()

    async def serve_rpc(request: Request):
        raw = await request.body()
        t0 = time.monotonic()
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            return JSONResponse(_rpc_error(None, -32700, "Parse error"))
        d = deps()
        d.pop("catalog", None)  # 目录仅供 /tools/search 使用，其余分发器不接收
        method = payload.get("method") if isinstance(payload, dict) else None
        if method in A2A_STREAM_METHODS:  # §2.4：SSE 流式（message/stream + v0.2 别名）
            params = payload.get("params")
            if not isinstance(params, dict) or not isinstance(params.get("message"), dict):
                return JSONResponse(_rpc_error(payload.get("id"), -32602, "params.message（object）为必填"))
            frames = await handle_tasks_stream(params, resolve=d["resolve"], call=d["call"],
                                               available_ids=d["available_ids"], store=d.get("store") or {})
            body = "".join(f"data: {json.dumps(f, ensure_ascii=False)}\n\n" for f in frames)
            _emit_audit(access_emitter, gateway_aic, len(raw), len(body), None, t0)
            return Response(content=body, media_type="text/event-stream")
        if method == "tasks/resubscribe":
            out = await _resubscribe_jsonrpc(payload, **d)
        else:
            out = await handle_jsonrpc(payload, **d)
        error = out.get("error") if isinstance(out, dict) else None
        _emit_audit(access_emitter, gateway_aic, len(raw), len(json.dumps(out, ensure_ascii=False)),
                    error, t0)
        return JSONResponse(out)

    async def tools_search(request: Request):
        d = deps()
        query = request.query_params.get("q", "")
        limit = int(request.query_params.get("limit", "5"))
        mode = request.query_params.get("mode", "keyword")
        out: dict[str, Any] = {"query": query, "mode": mode, "results": []}
        if mode == "vector":  # T5：语义检索；任何失败降级关键词并如实标记
            try:
                from gateway.vector_search import VectorIndex
                idx = getattr(request.app.state, "_wanbridge_vector_index", None)
                if idx is None:
                    idx = VectorIndex()
                    request.app.state._wanbridge_vector_index = idx
                out["results"] = await idx.query(d["catalog"](), query, limit=limit)
                out["mode"] = "vector"
                return JSONResponse(out)
            except Exception as exc:  # noqa: BLE001 — 降级关键词（语义不变）
                out["mode"] = "keyword"
                out["fallback_reason"] = str(exc)[:120]
        elif mode == "hybrid":  # v6 升级3：关键词+向量 rank 融合（RRF k=60）
            try:
                from gateway.vector_search import VectorIndex
                idx = getattr(request.app.state, "_wanbridge_vector_index", None)
                if idx is None:
                    idx = VectorIndex()
                    request.app.state._wanbridge_vector_index = idx
                catalog = d["catalog"]()
                kw_results = search_tools(catalog, query, limit=limit * 2)
                vec_results = await idx.query(catalog, query, limit=limit * 2)
                out["results"] = rrf_fuse([kw_results, vec_results], limit=limit)
                out["mode"] = "hybrid"
                return JSONResponse(out)
            except Exception as exc:  # noqa: BLE001 — 降级关键词
                out["mode"] = "keyword"
                out["fallback_reason"] = str(exc)[:120]
        out["results"] = search_tools(d["catalog"](), query, limit=limit)
        return JSONResponse(out)

    app.get("/tools/search")(tools_search)
    app.post("/")(serve_rpc)
    if include_rpc_route:
        app.post("/rpc")(serve_rpc)


def create_a2a_app(registry: Any, *, self_url: str, access_emitter: AccessEmitter | None = None,
                   gateway_aic: str = "wanbridge-gateway", include_rpc_route: bool = True) -> FastAPI:
    """把 A2A 服务端路由挂到 app 上，能力面来自 registry（McpRegistry 同构对象）。"""
    app = FastAPI(title="WanBridge A2A Server")
    store: dict[str, dict[str, Any]] = {}  # 内存任务表（§2.3），app 生命周期内共享
    deps: DepsFactory = lambda: {
        "resolve": registry.resolve,
        "call": registry.call,
        "available_ids": lambda: list(registry.all_tools()),
        "store": store,
        "catalog": lambda: registry.all_tools(),
    }
    _wire_routes(app, deps, lambda: build_agent_card(registry.build_acs(), self_url=self_url),
                 access_emitter=access_emitter, gateway_aic=gateway_aic,
                 include_rpc_route=include_rpc_route)
    return app


def create_standalone_app(
    *,
    config_path: Path | None = None,
    host: str = "localhost",
    port: int = DEFAULT_PORT,
    gateway_aic: str = "1.2.156.3088.1.1.MCPGW1.GATE01.1.0001",
) -> FastAPI:
    """独立运行形态（开发/演示/基准，端口 ≥8060）：自带 McpRegistry 生命周期。"""
    config_path = config_path or Path(__file__).parent / "config.toml"
    registry = McpRegistry(
        config_path=config_path,
        endpoint_url=f"http://{host}:{port}/",
        gateway_aic=gateway_aic,
    )

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        await registry.start()
        yield
        await registry.stop()

    app = FastAPI(title="WanBridge A2A Server", lifespan=lifespan)

    @app.get("/health")
    async def health() -> dict[str, Any]:
        return {
            "status": "ok",
            "mode": "a2a-standalone",
            "upstreams": {
                ns: {"online": s.online, "tools": len(s.tools), "error": s.error}
                for ns, s in registry.states.items()
            },
        }

    deps: DepsFactory = lambda: {
        "resolve": registry.resolve,
        "call": registry.call,
        "available_ids": lambda: list(registry.all_tools()),
        "store": _task_store,
        "catalog": lambda: registry.all_tools(),
    }
    _task_store: dict[str, dict[str, Any]] = {}
    _amp_log_dir = Path(os.environ.get("AMP_LOG_DIR", "./logs"))
    _amp_log_dir.mkdir(parents=True, exist_ok=True)
    _emitter = AccessEmitter(_amp_log_dir / f"amp_access_a2a-{port}.jsonl", aic=gateway_aic)
    _wire_routes(app, deps, lambda: build_agent_card(registry.build_acs(), self_url=f"http://{host}:{port}/"),
                 access_emitter=_emitter, gateway_aic=gateway_aic)
    return app


def create_app_from_env() -> FastAPI:
    """uvicorn --factory 入口：A2A_CONFIG / A2A_PORT 环境变量注入（基准与演示用）。"""
    import os

    return create_standalone_app(
        config_path=Path(os.environ.get("A2A_CONFIG", Path(__file__).parent / "config.toml")),
        port=int(os.environ.get("A2A_PORT", str(DEFAULT_PORT))),
    )


if __name__ == "__main__":  # pragma: no cover — 本地演示入口
    import os

    import uvicorn

    uvicorn.run(create_standalone_app(port=int(os.environ.get("A2A_PORT", str(DEFAULT_PORT)))),
                host="0.0.0.0", port=int(os.environ.get("A2A_PORT", str(DEFAULT_PORT))))
