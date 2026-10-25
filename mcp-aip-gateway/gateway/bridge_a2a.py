"""A2A Agent → AIP 上游适配桥（万桥方向一：A2A → AIP）。

设计依据：docs/design_wanbridge_a2a.md §1（规范核实记录见该文档头部，R9 合规）。
- 发现：GET {base}/.well-known/agent-card.json，404 回退 agent.json（双路径兼容）
- 技能：AgentSkill → ACS skill；A2A 技能无 inputSchema，弱契约如实声明，不编造参数表
- 调用：tasks/send（JSON-RPC 2.0），Task 7 态 → AIP 状态映射（§1.3）

定位边界：确定性协议翻译层——不做意图识别、不做 LLM 编排（DESIGN.md）。

错误语义（§1.3）：超时 → AIP failed（保活，下次探活再判）；
HTTP 非 2xx / 连接失败 → 抛 A2aError，由上层探活判负并自动撤架（复用 MCP 路线机制）。
Part 解析按宽容原则：type/kind 双键名、data/json 双载荷键名（§4 版本差异风险）。
"""

from __future__ import annotations

import json
import re
import uuid
from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Any

import httpx

WELL_KNOWN_PATHS = ("/.well-known/agent-card.json", "/.well-known/agent.json")
DEFAULT_TIMEOUT = 30.0

# §1.3 状态映射表：A2A TaskState → AIP TaskState（kebab-case 字符串，
# 与 acps_sdk TaskState 语义一一对应；canceled AIP 无对应态，归入 failed）
A2A_TO_AIP_STATE: dict[str, str] = {
    "completed": "completed",
    "input-required": "awaiting-completion",
    "working": "working",
    "submitted": "working",
    "failed": "failed",
    "unknown": "failed",
    "canceled": "failed",
}

_WEAK_CONTRACT_NOTE = (
    "【输入要求】该技能接受自由文本任务描述与可选 JSON 参数（JSON Part），"
    "由上游 A2A Agent 自行解释。"
)


class A2aError(RuntimeError):
    """A2A 上游不可达或协议错误（调用方应探活判负、撤架）。"""


class A2aCardNotFound(A2aError):
    """双路径 well-known 均未取到 AgentCard。"""


class A2aRpcError(A2aError):
    """A2A JSON-RPC 协议级错误（tasks/get、tasks/cancel 等查询类方法；§2.3）。

    与 call_skill 的错误语义区分：调用错误翻译为 failed Task（不抛），
    查询/取消没有 Task 语境可翻译，以带 code 的异常抛出（-32001 未找到 / -32002 不可取消）。
    """

    def __init__(self, code: int, message: str, data: Any = None) -> None:
        super().__init__(f"JSON-RPC error {code}: {message}")
        self.code = code
        self.message = message
        self.data = data


def slugify(name: str) -> str:
    """上游命名空间 slug：小写化，非 [a-z0-9-] 字符折叠为 '-'，压缩重复并去首尾。"""
    slug = re.sub(r"[^a-z0-9-]+", "-", name.lower()).strip("-")
    return slug or "a2a-agent"


def aip_state_from_a2a(a2a_state: str) -> str:
    """A2A TaskState → AIP TaskState；未收录的态按 unknown 语义归入 failed（宽容解析）。"""
    return A2A_TO_AIP_STATE.get(a2a_state, "failed")


@dataclass
class AgentCardInfo:
    """AgentCard 的网关内表示（§1.1 映射表；未列字段保留在 raw）。"""

    name: str
    url: str
    description: str = ""
    version: str = ""
    protocol_version: str = ""
    provider_org: str = ""
    streaming: bool = False
    auth_schemes: list[str] = field(default_factory=list)
    skills: list[dict[str, Any]] = field(default_factory=list)
    raw: dict[str, Any] = field(default_factory=dict)

    @property
    def ns(self) -> str:
        return slugify(self.name)


def parse_agent_card(card: dict[str, Any]) -> AgentCardInfo:
    """AgentCard dict → AgentCardInfo。name/url 为必填，其余按可选宽容处理（§4）。"""
    name = card.get("name")
    url = card.get("url")
    if not name or not url:
        raise A2aError(f"AgentCard 缺少必填字段 name/url：{list(card)}")
    capabilities = card.get("capabilities") or {}
    auth = card.get("authentication") or {}
    return AgentCardInfo(
        name=str(name),
        url=str(url),
        description=str(card.get("description") or ""),
        version=str(card.get("version") or ""),
        protocol_version=str(card.get("protocolVersion") or ""),
        provider_org=str((card.get("provider") or {}).get("organization") or ""),
        streaming=bool(capabilities.get("streaming", False)),
        auth_schemes=[str(s) for s in (auth.get("schemes") or [])],
        skills=list(card.get("skills") or []),
        raw=card,
    )


def validate_card_auth(card: AgentCardInfo, token: str | None) -> None:
    """§1.1 鉴权校验：Card 要求 Bearer 而未配置 token → A2aError；其余 scheme → 不支持。

    万桥定位边界：鉴权只在客户端侧被动满足上游要求，不做任何主动协商。
    """
    schemes = {str(x).lower() for x in card.auth_schemes}
    if not schemes:
        return
    if schemes <= {"none", "bearer"}:
        if "bearer" in schemes and not (token or "").strip():
            raise A2aError(f"上游 {card.name!r} 要求 Bearer 鉴权，但 config 未配置 token")
        return
    raise A2aError(f"上游 {card.name!r} 要求不支持的鉴权方式：{sorted(card.auth_schemes)}（本版仅支持 none/Bearer）")


async def fetch_agent_card(
    base_url: str,
    *,
    client: httpx.AsyncClient | None = None,
    timeout: float = DEFAULT_TIMEOUT,
) -> AgentCardInfo:
    """双路径发现：agent-card.json（新版）→ agent.json（旧版回退）。

    404 逐路径回退；双 404 → A2aCardNotFound；网络错误/其他非 2xx → A2aError。
    传入 client 时不接管其生命周期（测试注入 MockTransport 用）。
    """
    owned = client is None
    client = client or httpx.AsyncClient(timeout=timeout)
    try:
        last_not_found = False
        for path in WELL_KNOWN_PATHS:
            url = base_url.rstrip("/") + path
            try:
                resp = await client.get(url)
            except httpx.TimeoutException as exc:
                raise A2aError(f"AgentCard 拉取超时：{url}") from exc
            except httpx.HTTPError as exc:
                raise A2aError(f"AgentCard 拉取失败：{url}：{exc}") from exc
            if resp.status_code == 404:
                last_not_found = True
                continue
            resp.raise_for_status()
            return parse_agent_card(resp.json())
        if last_not_found:
            raise A2aCardNotFound(f"双路径均 404：{base_url}（{', '.join(WELL_KNOWN_PATHS)}）")
        raise A2aError(f"AgentCard 拉取异常：{base_url}")
    finally:
        if owned:
            await client.aclose()


def _skill_description(skill: dict[str, Any], skill_id: str) -> str:
    """AgentSkill → 弱契约描述（首段 + 输入要求声明，skill_to_aip 与壳对象共用）。"""
    first_para = str(skill.get("description") or "").strip().splitlines()
    desc = first_para[0].strip() if first_para else f"A2A 技能 {skill_id}"
    return f"{desc}\n{_WEAK_CONTRACT_NOTE}\n本能力由万桥网关从 A2A AgentCard 自动封装。"


def skill_to_aip(ns: str, skill: dict[str, Any]) -> dict[str, Any]:
    """A2A AgentSkill → AIP skill（§1.2）。

    关键差异：A2A 技能没有 inputSchema，如实声明弱契约（不编造参数表）——
    与 MCP 路线（translator.tool_to_skill 生成参数摘要）的区别所在。
    """
    skill_id = str(skill.get("id") or "").strip()
    if not skill_id:
        raise A2aError(f"AgentSkill 缺少必填字段 id：{list(skill)}")
    return {
        "id": f"{ns}__{skill_id}",
        "name": str(skill.get("name") or skill_id),
        "description": _skill_description(skill, skill_id),
        "version": "1.0.0",
        "tags": [ns, "A2A技能", *[str(t) for t in (skill.get("tags") or [])]],
        "examples": [str(e) for e in (skill.get("examples") or [])],
        "inputModes": ["text/plain", "application/json"],
        "outputModes": ["text/plain", "application/json"],
    }


def skills_to_aip(ns: str, skills: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [skill_to_aip(ns, s) for s in skills]


def skill_to_tool_shell(skill: dict[str, Any]) -> tuple[str, Any]:
    """AgentSkill → (技能尾段id, Tool 同构壳对象)。

    registry 的 tools 表存壳对象而非 AIP skill dict：all_tools/resolve 按
    `ns__{shell.name}` 路由（零改动），translator.tool_to_skill 按可选属性
    （title/description/inputSchema/annotations）翻译——与 MCP Tool 同一条路径。
    inputSchema 恒为 None：A2A 技能无参数表，弱契约如实声明（§1.2 关键差异）。
    """
    skill_id = str(skill.get("id") or "").strip()
    if not skill_id:
        raise A2aError(f"AgentSkill 缺少必填字段 id：{list(skill)}")
    shell = SimpleNamespace(
        name=skill_id,
        title=str(skill.get("name") or skill_id),
        description=_skill_description(skill, skill_id),
        inputSchema=None,
        annotations=None,
    )
    return skill_id, shell


def skills_to_tool_shells(skills: list[dict[str, Any]] | None) -> dict[str, Any]:
    """AgentCard.skills → 壳对象表（尾段id → shell）。id 重复时后者覆盖前者（宽容处理）。"""
    return dict(skill_to_tool_shell(s) for s in (skills or []))


def build_tasks_send_payload(
    skill_tail: str,
    arguments: dict[str, Any] | None,
    *,
    task_id: str | None = None,
    session_id: str | None = None,
) -> dict[str, Any]:
    """§1.3 请求体：text Part 承载工具约定 JSON（与 AIP dataItems 同契约）。"""
    tid = task_id or str(uuid.uuid4())
    text = json.dumps({"tool": skill_tail, "arguments": arguments or {}}, ensure_ascii=False)
    return {
        "jsonrpc": "2.0",
        "id": str(uuid.uuid4()),
        "method": "tasks/send",
        "params": {
            "id": tid,
            "sessionId": session_id or str(uuid.uuid4()),
            "message": {"role": "user", "parts": [{"type": "text", "text": text}]},
        },
    }


def _part_text(part: dict[str, Any]) -> str:
    """单个 Part → 文本（TextPart 直取；JsonPart/DataPart 序列化；FilePart 占位）。"""
    kind = part.get("type") or part.get("kind") or ""
    if kind == "text":
        return str(part.get("text") or "")
    if kind in ("data", "json"):
        payload = part["data"] if "data" in part else part.get("json")
        return json.dumps(payload, ensure_ascii=False)
    if kind == "file":
        meta = part.get("file") or {}
        label = meta.get("name") or meta.get("uri") or "未命名"
        return f"[文件 {label}]"
    return ""


def parts_to_text(parts: list[dict[str, Any]] | None) -> str:
    """Parts 依序合并为文本（§1.3：artifacts[].parts[] 依序合并为 TextDataItem）。"""
    texts = [_part_text(p) for p in (parts or [])]
    return "\n".join(t for t in texts if t)


@dataclass
class A2aCallResult:
    """一次 tasks/send 的翻译结果（raw 保留原始 Task 供排障）。"""

    a2a_state: str
    aip_state: str
    text: str
    raw: dict[str, Any] = field(default_factory=dict)


def task_to_call_result(task: dict[str, Any]) -> A2aCallResult:
    """A2A Task 响应 → A2aCallResult（§1.3 状态映射 + dataItems 文本提取）。"""
    status = task.get("status") or {}
    a2a_state = str(status.get("state") or "unknown")
    aip_state = aip_state_from_a2a(a2a_state)
    message_text = parts_to_text((status.get("message") or {}).get("parts"))

    if aip_state == "completed":
        chunks = [parts_to_text(a.get("parts")) for a in (task.get("artifacts") or [])]
        text = "\n".join(c for c in chunks if c)
    elif aip_state == "awaiting-completion":
        text = message_text  # §1.3：status.message.parts 文本透传，Leader/用户可 continue
    elif a2a_state == "canceled":
        text = "上游取消" + (f"：{message_text}" if message_text else "")
    elif aip_state == "failed":
        text = message_text or f"上游返回状态 {a2a_state}"
    else:  # working / submitted：中间态，无产物
        text = message_text
    return A2aCallResult(a2a_state=a2a_state, aip_state=aip_state, text=text, raw=task)


class A2aClient:
    """A2A JSON-RPC 客户端桩（v0：同步等待，不启用 streaming，§1.1）。"""

    def __init__(
        self,
        card: AgentCardInfo,
        *,
        client: httpx.AsyncClient | None = None,
        timeout: float = DEFAULT_TIMEOUT,
        token: str | None = None,
    ) -> None:
        self.card = card
        self._client = client or httpx.AsyncClient(timeout=timeout)
        self._owns_client = client is None
        # §1.1 鉴权（v2.0.0-alpha.2 P 池落地）：Card 声明 Bearer 时必须带 token；
        # token 只从 config 注入内存（不落盘、不打日志），本版仅支持 none/Bearer
        validate_card_auth(card, token)
        self._headers = {"Authorization": f"Bearer {token}"} if token else {}

    def _post(self, payload: dict[str, Any]):
        """带鉴权头的 JSON-RPC POST（Authorization 仅在配置了 token 时存在）。"""
        return self._client.post(self.card.url, json=payload, headers=self._headers or None)

    async def call_skill(
        self,
        skill_tail: str,
        arguments: dict[str, Any] | None = None,
        *,
        task_id: str | None = None,
        session_id: str | None = None,
    ) -> A2aCallResult:
        """tasks/send → 状态映射。超时返回 failed；连接/HTTP 错误抛 A2aError（撤架语义）。"""
        payload = build_tasks_send_payload(skill_tail, arguments, task_id=task_id, session_id=session_id)
        try:
            resp = await self._post(payload)
        except httpx.TimeoutException as exc:
            return A2aCallResult(a2a_state="failed", aip_state="failed", text=f"上游超时：{exc}")
        except httpx.HTTPError as exc:
            raise A2aError(f"tasks/send 请求失败：{self.card.url}：{exc}") from exc
        if resp.status_code >= 400:
            raise A2aError(f"tasks/send HTTP {resp.status_code}：{self.card.url}")
        try:
            body = resp.json()
        except ValueError as exc:
            raise A2aError(f"tasks/send 响应不是合法 JSON：{resp.text[:120]}") from exc
        if isinstance(body, dict) and body.get("error"):
            err = body["error"]
            return A2aCallResult(
                a2a_state="failed",
                aip_state="failed",
                text=f"JSON-RPC error {err.get('code')}: {err.get('message')}",
                raw=body,
            )
        return task_to_call_result((body or {}).get("result") or {})

    async def refetch_card(self) -> AgentCardInfo:
        """重拉 AgentCard 并更新 self.card（registry 探活周期用；上游技能集可能漂移）。

        从当前 card.url 取（跟随上游端点迁移）；网络错误按 fetch_agent_card 语义抛 A2aError。
        """
        self.card = await fetch_agent_card(self.card.url, client=self._client)
        return self.card

    async def _rpc(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        """通用 JSON-RPC 往返（tasks/get、tasks/cancel；§2.3）。

        协议级 error 以 A2aRpcError 抛出（保留 code/message/data）；
        超时/连接/HTTP 错误抛 A2aError（与 call_skill 同语义）。
        """
        payload = {"jsonrpc": "2.0", "id": str(uuid.uuid4()), "method": method, "params": params}
        try:
            resp = await self._client.post(self.card.url, json=payload)
        except httpx.TimeoutException as exc:
            raise A2aError(f"{method} 上游超时：{exc}") from exc
        except httpx.HTTPError as exc:
            raise A2aError(f"{method} 请求失败：{self.card.url}：{exc}") from exc
        if resp.status_code >= 400:
            raise A2aError(f"{method} HTTP {resp.status_code}：{self.card.url}")
        try:
            body = resp.json()
        except ValueError as exc:
            raise A2aError(f"{method} 响应不是合法 JSON：{resp.text[:120]}") from exc
        if isinstance(body, dict) and body.get("error"):
            err = body["error"]
            raise A2aRpcError(
                code=int(err.get("code", -32603)),
                message=str(err.get("message", "")),
                data=err.get("data"),
            )
        return (body or {}).get("result") or {}

    async def get_task(self, task_id: str) -> dict[str, Any]:
        """tasks/get（§2.3）：params 只传必填 id（history_length/metadata v0.2 不用）。"""
        return await self._rpc("tasks/get", {"id": task_id})

    async def cancel_task(self, task_id: str) -> dict[str, Any]:
        """tasks/cancel（§2.3）：非终态任务置 canceled 并返回 Task；
        未知 id → A2aRpcError(-32001)；终态 → A2aRpcError(-32002)。"""
        return await self._rpc("tasks/cancel", {"id": task_id})

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def __aenter__(self) -> "A2aClient":
        return self

    async def __aexit__(self, *exc_info: Any) -> None:
        await self.aclose()
