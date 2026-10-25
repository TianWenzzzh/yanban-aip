"""研伴 Partner 通用基座：AIP RPC 骨架 + AMP + FastAPI 工厂。

各 Partner 只需实现 skill 函数（text, state) -> output）和可选的缺参判定。
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import sys
import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Awaitable, Callable

from acps_sdk.aip import Product, TaskCommand, TaskResult, TaskState, TextDataItem
from acps_sdk.aip.aip_rpc_server import (
    CommandHandlers,
    DefaultHandlers,
    TaskManager,
    add_aip_rpc_router,
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

from acps_sdk.aip.access_status import status_from_rpc_response
from acps_sdk.aip.aip_rpc_model import RpcResponse, JSONRPCError

# ── 智能体卡（ACS）对外发放：登记入口经 caddy 落到 Partner 应用本身（轮 299 主机实证
#    `47.114.38.125.sslip.io` → `127.0.0.1:18031` = yanban-literature-1:8031），
#    所以发卡必须挂在 Partner 应用上，挂 Leader 不解决平台取卡（那一条 404 已实测）。
#    卡内容以**平台已登记的那张**为源（`acs/acs-<slug>.json`），此处只做两件事：
#    ① endPoints 按代码常量重算（防文件里留下未插值占位符或过期入口）；
#    ② 运行时 aic 与卡内 aic 不一致 ⇒ 503 拒发，🔴 绝不发一张身份错位的卡冒充别的智能体。
WELL_KNOWN_PATHS = ("/.well-known/agent-card.json", "/.well-known/agent.json")
# 🔴 轮 307 换成新机那条**实测可达**的地址。旧值 `https://47.114.38.125.sslip.io/rpc` 现在是一条死路：
# 老机已是待下线副本，而那条域名在中国内地节点上按 ICP 拦 80/443（未决 67，实测拿到 403 拦截页）。
# 为什么这条要紧：卡是发给**别人**照着调的——广告一个打不通的地址，等于给每个照卡调的人指一条死路，
# 而"被调用次数"恰恰要从他们那一次点击来。哨兵＝`输出/scripts/probe_r307_card_reach.py`（现读卡片再逐条从公网打）。
PUBLIC_RPC_URL = "http://47.95.252.83:8031/rpc"
# 轮 308：平台注册 API 实测要求端点声明 mTLS（`endPoint must use mutualTLS security scheme`，未决 74(a)），
# 而这条入口由 `yanban-aip-tls` 容器做 TLS 终结再转 8031，证书 CN 就是在册那颗 AIC。
# 端口是 8443 不是 443：中国内地节点按域名拦 80/443（未决 67），非 80/443 实测可达。
# 🔴 轮 309 把域名形式换成 **IP 形式**（原值 `https://47.95.252.83.sslip.io:8443/rpc`）。这不是洁癖，
# 是基线探针 T1 三臂实测的结论（`输出/scripts/probe_r309_handoff_baseline.py`）：带
# `47.95.252.83.sslip.io` 这个 SNI 的 ClientHello 被链路 **RST**（连 8443 这种非 80/443 端口也拦，
# 因为拦的是域名不是端口），而**无 SNI**与 **SNI=IP** 两臂都握手成功；同一台机上机内自打同 SNI 成功
# ⇒ 服务本身没问题。平台侧 1.5.1 那张卡（`输出/证据/轮309/discovery_1DHO_原卡.json`）注册的也正是
# 这条 IP 形式地址 ⇒ 在册卡与这里必须逐字相等，否则照卡来调的人第一步就撞在链路上。
# 主机名那一维仍然**不许**验：AIC 证书的 SAN 只有 `URI:acps://<AIC>`、没有 DNS/IP（未决 74(c)），
# 所以客户端必须是「Agent CA 验链 + 关掉 check_hostname」那条臂——`probe_r307_card_reach.py` 的
# TLS 臂与 `yanban-selfcall` 走的都是这条，公网实测 `TLS_PUBLIC_RC=0`（轮 308c）。
# 消费方：`输出/scripts/register_r308.py`（登记给平台的那张 ACS 用它，不在脚本里另抄一遍字面量）。
PUBLIC_AIP_TLS_URL = "https://47.95.252.83:8443/rpc"


def partner_card(name: str, aic: str, *, acs_dir: Path | None = None,
                 inbox_aic: str | None = None) -> dict | None:
    """读该 Partner 的卡材料并校正入口；缺文件或身份不符返回 None（由路由转 503）。

    `inbox_aic`：AMQP 收件箱用哪颗 aic 拼（默认＝`aic`）。轮 308c 起 literature 的 HTTP 身份
    迁到了新授权的那颗 …1022，而**平台侧两张卡的 AMQP 收件箱都冻结为 inbox_05BB**
    （已通过的记录客户端改不了）⇒ MQ 身份必须跟着冻结值走，不能跟着 HTTP 身份走。
    """
    f = (acs_dir or Path(__file__).resolve().parent / "acs") / ("acs-%s.json" % name.replace("yanban-", "", 1))
    if not f.exists():
        return None
    card = json.loads(f.read_text(encoding="utf-8"))
    if card.get("aic") != aic:
        return None
    card["endPoints"] = [
        # 🔴 明文 http 的入口不许声明 mtls：那等于告诉照着卡来的调用方"请用 TLS 客户端证书"，
        # 而这条地址上根本没有 TLS。这条留给「不带证书、裸 HTTP 打进来」的调用方（对外指引里那条路）。
        {"url": PUBLIC_RPC_URL, "transport": "JSONRPC", "security": []},
        # 轮 309 起把 mTLS 那条也广告上（形状与平台 1.5.1 那张卡逐字一致，见 PUBLIC_AIP_TLS_URL 上方）：
        # SDK 默认档的调用方要的就是这一条，而它的可达性有实测背书（`probe_r307_card_reach.py`
        # 的 Agent CA + 不验主机名那一臂，轮 308c 公网 `TLS_PUBLIC_RC=0`）。
        # 🔴 这条的**安全性声明必须是 mtls**：URL 从域名换成 IP 不改这一维，别顺手"为了让别人好调"
        # 把 mtls 摘掉——那才是真把验证关掉（交接文档明令禁止的处置）。
        {"url": PUBLIC_AIP_TLS_URL, "transport": "JSONRPC", "security": [{"mtls": []}]},
        {"url": "amqps://wt.ioa.pub:5671/acps?inbox=inbox_%s" % (inbox_aic or aic),
         "transport": "AMQP", "security": [{"mtls": []}]},
    ]
    card["active"] = True
    return card


def add_partner_card_routes(app: FastAPI, *, name: str, aic: str,
                           acs_dir: Path | None = None,
                           inbox_aic: str | None = None) -> None:
    from fastapi.responses import JSONResponse

    async def _card():
        card = partner_card(name, aic, acs_dir=acs_dir, inbox_aic=inbox_aic)
        if card is None:
            return JSONResponse(status_code=503, content={"error": "card_unavailable",
                                                          "reason": "卡材料缺失或 aic 与运行时不一致"})
        return JSONResponse(content=card)

    for p in WELL_KNOWN_PATHS:  # 两条同函数＝逐字同卡，且不会只挂一条
        app.add_api_route(p, _card, methods=["GET"])

from acps_sdk.aip.aip_rpc_server import handle_rpc_request

AMP_LOG_DIR = Path(__file__).parent / "logs"
AMP_LOG_DIR.mkdir(parents=True, exist_ok=True)

# ── 计数面（轮 306）：一条入站通道要么"装了就会落账"，要么被显式声明"为什么不算被调用"。
# 谁装了计数由安装器自己登记在这里（不靠注释、不靠人记得），闸门
# `tests2/test_counting_surface.py` 拿它与每个 app 真实的 `app.routes` 对账 ⇒
# 以后加功能新开一条入站路而没人决定它算不算"被调用"，那道闸门当场红。
# 这条对账存在的原因不是洁癖：轮 306 现读线上账本 route 值只有 `/rpc` 与 `/`，
# 而 `/group/rpc`（别人把我拉进群组）与群组 MQ 里下发的任务指令**一条都没落过账**
# ⇒ 平台真走群组那条路调用我们时，"被调用次数"数出来是 0。
COUNTED_ROUTES: dict[str, set[str]] = {}


def mark_counted(service: str, route: str) -> None:
    COUNTED_ROUTES.setdefault(service, set()).add(route)


async def note_access(emitter: AccessEmitter, *, callee_aic: str, callee_name: str, route: str,
                      caller: str, code: int, raw_len: int, resp_len: int = 0,
                      duration_ms: int = 0, method: str = "rpc",
                      error: ErrorInfo | None = None,
                      correlation_id: str | None = None) -> None:
    """把"被人调用了一次"写成一条 AMP access 记录——`/rpc` 之外的通道都走这一个函数。

    🔴 计数与业务不能互相拖：落账坏了绝不许把这次调用本身也弄失败（所以不 re-raise），
    但也绝不许静默——它会在 stderr 印 `AMP-EMIT-FAIL`，因为这条通道就是分数来源，
    哑掉必须能被看见（轮 181c 那次是整条路由没 emit，静默了两周）。

    🔴 轮 308c：`correlation_id` 是平台按 taskId 双侧配对计"调用/被调用"的那把钥匙
    （docs.html「日志上报要求」；官方 demo 两侧都填 `sessionId`）——缺了它，这条记录
    就算上报到平台也配不上对。取不到时照旧留空写一条，不让它把一次调用弄失败。
    """
    try:
        await emitter.emit(AccessBody(
            request=AccessRequest(method=method, url=route, route=route, bodySizeBytes=raw_len),
            response=AccessResponse(statusCode=code, bodySizeBytes=resp_len),
            caller=AccessParticipant(aic=caller or "unknown", serviceName=caller or "unknown"),
            callee=AccessParticipant(aic=callee_aic, serviceName=callee_name),
            error=error,
            durationMs=duration_ms,
        ), correlation_id=correlation_id)
    except Exception as exc:  # noqa: BLE001 — 记账失败不许多烧掉一次调用
        print("AMP-EMIT-FAIL %s %s: %s" % (route, type(exc).__name__, str(exc)[:160]),
              file=sys.stderr)


def _task_id_tolerant(handlers: CommandHandlers) -> CommandHandlers:
    """`start` 不带 taskId 是手搓调用方的常见形状（SDK 允许不带，我们的 `on_start` 却要拿它建任务）。

    在这里补一颗，比在每颗 `on_start` 里各补一次少一处会漏的地方：轮 306 外部实测，缺它时
    SDK 的兜底会拿 `task_id=None` 去 `update_task_status` 再抛一次 ⇒ 端点回 HTTP 500 崩栈。
    """
    inner = handlers

    async def on_start(command: TaskCommand, task: TaskResult | None) -> TaskResult:
        if not command.taskId:
            command.taskId = "task-" + str(uuid.uuid4())
        return await inner.on_start(command, task)

    return CommandHandlers(on_start=on_start, on_get=inner.on_get, on_cancel=inner.on_cancel,
                           on_complete=inner.on_complete, on_continue=inner.on_continue,
                           on_message=inner.on_message)


def add_audited_rpc_router(app: FastAPI, endpoint: str, handlers: CommandHandlers, *,
                           aic: str, name: str, emitter: AccessEmitter,
                           identity_binding_enabled: bool = True) -> None:
    """带 AMP access 审计的 /rpc —— 唯一一条"被调用能数得出来"的路。

    🔴 为什么非要有这个函数：SDK 的 `add_aip_rpc_router` 函数体里没有任何 AccessEmitter/emit
    （读过源码，26 行），走它的智能体被调用一百次也留不下记录。2026-09-30 实测：注册在梧桐上的
    「研伴·文献检索智能体」正是走裸路由，容器内打一次 /rpc，`amp_access_*.jsonl` 增量 0，
    而平台侧那句"非平台上注册的智能体无法形成可对接的调用记录"意味着**没有记录就没有分数**。
    调用方身份取 `TaskCommand.senderId`（写死成本队 leader 的话，日志答不出"被谁调用"）。
    """

    mark_counted(name, endpoint)
    handlers = _task_id_tolerant(handlers)

    @app.post(endpoint, response_model=RpcResponse)
    async def rpc_endpoint(request: Request) -> RpcResponse:
        raw = await request.body()
        resp: RpcResponse | None = None
        error_info = None
        # 先给一个兜底码：`handle_rpc_request` 抛异常时下面那行赋值不会执行，
        # 而 finally 里要读它——原来没初始化 ⇒ UnboundLocalError 被 suppress 咽掉 ⇒ **失败的调用一条都不留**。
        code = 500
        t0 = time.monotonic()
        try:
            resp = await handle_rpc_request(
                request, handlers, local_aic=aic, identity_binding_enabled=identity_binding_enabled,
            )
            code = status_from_rpc_response(resp)
        except Exception as exc:  # noqa: BLE001
            # 🔴 不许多穿到 FastAPI：轮 306 外部实测，SDK 的兜底在"任务还不存在"时自己再抛一次
            # （`update_task_status(None, …)`）⇒ 调用方收到 HTTP 500 崩栈、拿不到任何可解析的回答。
            # 计数不受它影响（账照落），但别人第一次手搓调用撞上的就是这个 ⇒ 换成 JSON-RPC 错误体。
            error_info = ErrorInfo(code=500, message=str(exc))
            resp = RpcResponse(id=None, error=JSONRPCError(
                code=-32603, message="Internal error", data=str(exc)[:300]))
            code = status_from_rpc_response(resp)
        finally:
            caller = _caller_of(raw)
            with contextlib.suppress(Exception):
                await emitter.emit(
                    AccessBody(
                        request=AccessRequest(method="rpc", url=endpoint, route=endpoint, bodySizeBytes=len(raw)),
                        response=AccessResponse(statusCode=code, bodySizeBytes=len(resp.model_dump_json()) if resp else 0),
                        caller=AccessParticipant(aic=caller, serviceName=caller),
                        callee=AccessParticipant(aic=aic, serviceName=name),
                        error=error_info,
                        durationMs=int((time.monotonic() - t0) * 1000),
                    ),
                    correlation_id=_corr_of(raw),
                )
        return resp


def _caller_of(raw: bytes) -> str:
    """从请求体里取调用方标识；取不到就明写 unknown，不冒充成本队。"""
    try:
        sid = (json.loads(raw).get("params", {}).get("command", {}) or {}).get("senderId") or ""
    except Exception:  # noqa: BLE001 — 半截 JSON 也要留下一条记录
        return "unknown"
    return str(sid)[:120] or "unknown"


def _corr_of(raw: bytes) -> str | None:
    """从请求体取这次调用的 correlation（`sessionId`，缺了退 `taskId`）。

    🔴 口径来自官方参考件（不是猜的）：demo-partner 的 `/rpc` 落账时
    `correlation_id=command.sessionId`，SDK 客户端 `AipRpcClient` 发射 caller span 也是
    `correlation_id=command.sessionId` ⇒ 平台按这个值把两侧的记录配成"1 次调用"。
    docs.html 的「日志上报要求」把这个字段写作 taskId，取不到 `sessionId` 时退到
    `taskId` 兜底；两个都取不到就留空（照旧写记录，只是这发配不上对）。
    """
    try:
        cmd = (json.loads(raw).get("params", {}).get("command", {}) or {})
    except Exception:  # noqa: BLE001 — 半截 JSON 也算一次被调用
        return None
    return str(cmd.get("sessionId") or cmd.get("taskId") or "")[:120] or None


def create_partner_app(
    *,
    name: str,
    aic: str,
    port: int,
    run_skill: Callable[[str], Awaitable[str]],
    needs_input: Callable[[str], bool] | None = None,
    input_hint: str = "请补充必要信息。",
    identity_binding: bool | None = None,  # None=按 YANBAN_IDENTITY_MODE（R21）；显式传值可覆盖
) -> FastAPI:
    if identity_binding is None:
        from identity_mode import identity_binding_enabled
        identity_binding = identity_binding_enabled()
    heartbeat = HeartbeatEmitter(AMP_LOG_DIR / f"amp_heartbeat_{name}.jsonl", aic=aic)
    access_emitter = AccessEmitter(AMP_LOG_DIR / f"amp_access_{name}.jsonl", aic=aic)
    hb_task: asyncio.Task | None = None

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        nonlocal hb_task
        hb_task = asyncio.create_task(heartbeat.run_periodic(15), name=f"hb-{name}")
        yield
        hb_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await hb_task

    app = FastAPI(title=f"研伴·{name}", lifespan=lifespan)
    _original_inputs: dict[str, str] = {}

    def _sender(task: TaskResult) -> TaskResult:
        task.senderId = aic
        return task

    def _text_of(command: TaskCommand) -> str:
        for item in command.dataItems or []:
            if isinstance(item, TextDataItem):
                return item.text
        return ""

    async def on_start(command: TaskCommand, task: TaskResult | None) -> TaskResult:
        if task:
            return _sender(task)
        raw = _text_of(command)
        _original_inputs[command.taskId] = raw
        if needs_input and needs_input(raw):
            return _sender(TaskManager.create_task(
                command, initial_state=TaskState.AwaitingInput,
                data_items=[TextDataItem(text=input_hint)],
            ))
        task = TaskManager.create_task(command, initial_state=TaskState.Working)
        try:
            content = await run_skill(raw)
        except Exception as exc:  # noqa: BLE001
            return _sender(TaskManager.update_task_status(
                task.taskId, TaskState.Failed, data_items=[TextDataItem(text=f"能力执行失败：{exc}")],
            ))
        TaskManager.set_products(
            task.taskId,
            [Product(id=f"{name}-{task.taskId[:8]}", name=name, dataItems=[TextDataItem(text=content)])],
        )
        return _sender(TaskManager.update_task_status(task.taskId, TaskState.AwaitingCompletion))

    async def on_continue(command: TaskCommand, task: TaskResult) -> TaskResult:
        """awaiting-input → 补充信息与原始输入拼接后重新执行能力。"""
        TaskManager.add_command_to_history(task.taskId, command)
        combined = _original_inputs.get(command.taskId, "") + "\n" + _text_of(command)
        _original_inputs[command.taskId] = combined
        if needs_input and needs_input(combined):
            return _sender(TaskManager.update_task_status(
                task.taskId, TaskState.AwaitingInput,
                data_items=[TextDataItem(text=input_hint)],
            ))
        try:
            content = await run_skill(combined)
        except Exception as exc:  # noqa: BLE001
            return _sender(TaskManager.update_task_status(
                task.taskId, TaskState.Failed, data_items=[TextDataItem(text=f"能力执行失败：{exc}")],
            ))
        TaskManager.set_products(
            task.taskId,
            [Product(id=f"{name}-{task.taskId[:8]}", name=name, dataItems=[TextDataItem(text=content)])],
        )
        return _sender(TaskManager.update_task_status(task.taskId, TaskState.AwaitingCompletion))

    handlers = CommandHandlers(
        on_start=on_start,
        on_continue=on_continue,
        on_get=DefaultHandlers.get,
        on_cancel=DefaultHandlers.cancel,
        on_complete=DefaultHandlers.complete,
    )
    # 注意：不调用 add_aip_rpc_router（它会先注册 /rpc 抢路由），审计由下方自定义端点统一包装
    add_audited_rpc_router(app, "/rpc", handlers, aic=aic, name=name,
                           emitter=access_emitter, identity_binding_enabled=identity_binding)

    @app.get("/health")
    async def health() -> dict:
        return {"status": "ok", "agent": name, "aic": aic}

    return app
