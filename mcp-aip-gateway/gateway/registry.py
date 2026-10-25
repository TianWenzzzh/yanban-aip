"""上游注册表与连接治理（万桥 v2.0：MCP stdio/http + A2A 三种上游统一治理）。

职责：
- 按 config.toml 管理多上游（stdio / streamable-http / **a2a**）
- 每个上游一个专属 owner task（anyio cancel scope 限制）+ 探活循环
- 失联自动"撤架"（从聚合 ACS 中移除该 server 的工具），恢复自动"上架"
- 聚合工具目录：`{ns}__{tool}` 全局唯一，反向解析路由到对应 session/客户端

A2A 上游（万桥 v2.0.0-alpha，docs/design_wanbridge_a2a.md §1）：
- 无 MCP session；owner 持有 httpx 客户端 + A2aClient，探活 = 周期 refetch AgentCard
- Card.skills 经 skills_to_tool_shells 包成 Tool 同构壳对象进 tools 表——
  all_tools/resolve/build_acs 零改动（壳对象与 MCP Tool 属性同构）
- registry.call 对 a2a 上游返回 MCP CallToolResult 同构适配壳
  （content[].text + isError），partner/a2a_server 的取值路径零改动
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import sys
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from gateway.bridge_a2a import A2aClient, skills_to_tool_shells
from gateway.translator import tools_to_acs

GATEWAY_NAME = "研伴·MCP工具网关"
GATEWAY_DESC = (
    "MCP↔AIP 协议适配网关：把多个上游 MCP Server 的工具零改动翻译为 AIP 国标 Partner 能力，"
    "自动生成 ACS、命名空间隔离、失联自动撤架。梧桐平台上的 Leader 智能体发现本网关后，"
    "即可通过 AIP 任务调用整个 MCP 工具生态。"
)


@dataclass
class ServerSpec:
    ns: str
    transport: str  # stdio | http(streamable-http) | a2a
    description: str = ""
    command: str = ""
    args: list[str] = field(default_factory=list)
    env: dict[str, str] = field(default_factory=dict)
    url: str = ""  # http: streamable-http 端点；a2a: AgentCard 基地址
    token: str = ""  # a2a 上游 Bearer 鉴权（§1.1；只存内存，不打日志不落盘）


@dataclass
class ServerState:
    spec: ServerSpec
    online: bool = False
    tools: dict[str, Any] = field(default_factory=dict)  # tool.name -> Tool
    error: str = ""
    # v1.2.1 修复：多上游启动竞态。旧版共用一个全局 Event，首个就绪上游即放行 start()，
    # 其余上游还在初始化就被 /health 当作"已撤架"。改为逐上游 ready，全部首刷（或失败）后才就绪。
    ready: asyncio.Event = field(default_factory=asyncio.Event)


def call_result_is_error(result: Any) -> bool:
    """MCP CallToolResult 的错误标志双键名兼容（SDK 1.x isError / 2.x is_error）。

    v1.2 实测：SDK 2.x 已移除 isError 属性，直取会让空 content 的工具结果触发
    AttributeError（AIP 面 500）。此 helper 是唯一取值入口。
    """
    return bool(getattr(result, "isError", getattr(result, "is_error", False)))


class McpRegistry:
    def __init__(self, config_path: Path, endpoint_url: str, gateway_aic: str) -> None:
        raw = tomllib.loads(config_path.read_text(encoding="utf-8"))
        self.specs: list[ServerSpec] = [self._parse(s) for s in raw.get("server", [])]
        self.probe_interval = float(raw.get("gateway", {}).get("probe_interval_seconds", 30))
        self.states: dict[str, ServerState] = {s.ns: ServerState(spec=s) for s in self.specs}
        self._sessions: dict[str, Any] = {}
        self._a2a_clients: dict[str, A2aClient] = {}
        self._owner_tasks: list[asyncio.Task] = []
        self._probe_task: asyncio.Task | None = None
        self._shutdown = asyncio.Event()
        # v2.0.1（Codex 问题②）：重连退避参数（测试可覆写加速）
        self._reconnect_backoff_start = 2.0
        self._reconnect_backoff_max = 30.0
        self._endpoint_url = endpoint_url
        self._gateway_aic = gateway_aic
        self._reload_event = asyncio.Event()

    @staticmethod
    def _parse(s: dict[str, Any]) -> ServerSpec:
        return ServerSpec(
            ns=str(s["ns"]),
            transport=str(s.get("transport", "stdio")),
            description=str(s.get("description", "")),
            command=str(s.get("command", "")),
            args=list(s.get("args", [])),
            env=dict(s.get("env", {})),
            url=str(s.get("url", "")),
            token=str(s.get("token", "")),
        )

    # ── 生命周期 ────────────────────────────────────────────────────────────

    async def start(self) -> None:
        for state in self.states.values():
            self._owner_tasks.append(asyncio.create_task(self._owner(state), name=f"mcp-{state.spec.ns}"))
        self._probe_task = asyncio.create_task(self._probe_loop(), name="mcp-probe")
        if self.states:
            await asyncio.gather(*(s.ready.wait() for s in self.states.values()))

    async def stop(self) -> None:
        self._shutdown.set()
        for t in self._owner_tasks + ([self._probe_task] if self._probe_task else []):
            # 显式 cancel：shutdown 事件唤不醒 asyncio.sleep，优雅等待会拖最长一个探活周期
            t.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await t

    async def _owner(self, state: ServerState) -> None:
        """单一任务持有该上游的连接与重连循环（MCP：session 上下文；A2A：A2aClient 上下文）。

        v2.0.1 修复（Codex 问题②）：原先异常后 owner 直接结束——撤架生效但"自动重连"
        不成立，上游恢复后需重启网关。现包进重连循环：异常 → 撤架 → 指数退避 → 重试，
        上游恢复即自动上架。
        """
        spec = state.spec
        backoff = self._reconnect_backoff_start
        while not self._shutdown.is_set():
            try:
                if spec.transport == "a2a":
                    async with self._a2a_client_factory(spec) as client:
                        self._a2a_clients[spec.ns] = client
                        await self._serve_a2a(state, client)
                else:
                    async with self._connect(spec) as session:
                        await self._serve(state, session)
                if self._shutdown.is_set():
                    return
                backoff = self._reconnect_backoff_start  # 连接正常断开（罕见）：立即重试
            except Exception as exc:  # noqa: BLE001
                # anyio 会把真实异常包成 ExceptionGroup，解开取根因便于排障
                while isinstance(exc, BaseExceptionGroup) and exc.exceptions:
                    exc = exc.exceptions[0]
                state.online = False
                state.error = f"{type(exc).__name__}: {exc}"
                state.tools.clear()
                self._sessions.pop(spec.ns, None)
                self._a2a_clients.pop(spec.ns, None)
                state.ready.set()
                self._reload_event.set()
                print(f"[registry] 上游 {spec.ns} 不可用并已撤架: {state.error}"
                      f"（{backoff:.0f}s 后重连）", file=sys.stderr, flush=True)
                if self._shutdown.is_set():
                    return
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, self._reconnect_backoff_max)

    def _connect(self, spec: ServerSpec):
        """MCP 连接工厂：stdio / streamable-http → ClientSession 上下文。

        可注入点：测试覆写本方法（实例属性赋值）即可用假会话驱动 owner 循环，
        不碰网络——多上游启动竞态回归测试（bb87f00 还债）即走此注入。
        A2A 上游不走这里，见 _a2a_client_factory。
        """
        return self._connect_ctx(spec)

    @contextlib.asynccontextmanager
    async def _connect_ctx(self, spec: ServerSpec):
        if spec.transport == "stdio":
            from mcp import ClientSession, StdioServerParameters
            from mcp.client.stdio import stdio_client

            env = {**os.environ, **spec.env}
            # Windows 下子进程 PATH 里未必有 python/uvx；"python" 一律解析为当前解释器
            command = sys.executable if spec.command in ("python", "python3") else spec.command
            params = StdioServerParameters(command=command, args=spec.args, env=env)
            async with stdio_client(params) as (read, write), ClientSession(read, write) as session:
                yield session
        elif spec.transport in ("http", "streamable-http"):
            from mcp import ClientSession
            from mcp.client.streamable_http import streamable_http_client

            async with streamable_http_client(spec.url) as ctx:
                # SDK 2.x 返回 (read, write[, get_session_id])，按实际长度解包
                read, write = ctx[0], ctx[1]
                async with ClientSession(read, write) as session:
                    yield session
        else:
            raise ValueError(f"transport {spec.transport!r} 不支持")

    @contextlib.asynccontextmanager
    async def _a2a_client_factory(self, spec: ServerSpec):
        """A2A 连接工厂：httpx 客户端 + 双路径 Card 发现 → A2aClient。

        可注入点：测试覆写本方法注入假 A2aClient（无网络）。
        """
        import httpx

        from gateway.bridge_a2a import fetch_agent_card

        async with httpx.AsyncClient(timeout=30.0) as client:
            card = await fetch_agent_card(spec.url, client=client)
            yield A2aClient(card, client=client, token=spec.token or None)

    async def _serve(self, state: ServerState, session: Any) -> None:
        """握手 → 上架 → 周期探活（由 _owner 的连接上下文调用）。"""
        await session.initialize()
        self._sessions[state.spec.ns] = session
        await self._refresh(state, session)
        state.ready.set()
        while not self._shutdown.is_set():
            await asyncio.sleep(self.probe_interval)
            await self._refresh(state, session)

    async def _serve_a2a(self, state: ServerState, client: A2aClient) -> None:
        """Card 发现 → 上架 → 周期探活（A2A 版 _serve，语义对称：探活失败即撤架）。"""
        await self._refresh_a2a(state, client)
        state.ready.set()
        while not self._shutdown.is_set():
            await asyncio.sleep(self.probe_interval)
            await self._refresh_a2a(state, client)

    async def _refresh_a2a(self, state: ServerState, client: A2aClient) -> None:
        """Card 探活：成功→skills 重包壳上架（技能集漂移即刷新）；失败→抛异常进撤架。"""
        card = await client.refetch_card()
        new_tools = skills_to_tool_shells(card.skills)
        changed = set(new_tools) != set(state.tools)
        state.tools = new_tools
        if not state.online or changed:
            state.online = True
            state.error = ""
            self._reload_event.set()
            print(f"[registry] A2A 上游 {state.spec.ns} 在线，{len(new_tools)} 技能: {sorted(new_tools)}", flush=True)

    async def _refresh(self, state: ServerState, session: Any) -> None:
        """list_tools 探活：成功→上架并刷新工具表；失败→抛异常进入撤架。"""
        result = await session.list_tools()
        new_tools = {t.name: t for t in result.tools}
        changed = set(new_tools) != set(state.tools)
        state.tools = new_tools
        if not state.online or changed:
            state.online = True
            state.error = ""
            self._reload_event.set()
            print(f"[registry] 上游 {state.spec.ns} 在线，{len(new_tools)} 工具: {sorted(new_tools)}", flush=True)

    async def _probe_loop(self) -> None:
        """观测撤架信号并通知 ACS 重建（owner 内部的 refresh 已做探活，这里只做信号消费）。"""
        while not self._shutdown.is_set():
            with contextlib.suppress(asyncio.TimeoutError):
                await asyncio.wait_for(self._reload_event.wait(), timeout=5)
            self._reload_event.clear()

    # ── 查询面 ─────────────────────────────────────────────────────────────

    def online_states(self) -> list[ServerState]:
        return [s for s in self.states.values() if s.online]

    def all_tools(self) -> dict[str, tuple[ServerState, Any]]:
        """聚合目录：完整 skill id（ns__tool）→ (server, Tool)。"""
        catalog: dict[str, tuple[ServerState, Any]] = {}
        for state in self.online_states():
            for tool_name, tool in state.tools.items():
                catalog[f"{state.spec.ns}__{tool_name}"] = (state, tool)
        return catalog

    def resolve(self, skill_id: str) -> tuple[ServerState, Any] | None:
        """支持完整 id（ns__tool）或裸工具名（唯一时）。"""
        catalog = self.all_tools()
        if skill_id in catalog:
            return catalog[skill_id]
        bare = [(k, v) for k, v in catalog.items() if k.split("__", 1)[1] == skill_id]
        if len(bare) == 1:
            return bare[0][1]
        return None

    async def call(self, ns: str, tool_name: str, arguments: dict[str, Any]) -> Any:
        """上游调用：MCP 走 session.call_tool；A2A 走 A2aClient.call_skill 并包适配壳。

        适配壳与 MCP CallToolResult 同构（content[].text + isError），
        partner.py / a2a_server.py 的取值路径零改动。
        isError 语义（§1.3）：aip_state=failed → True；其余态（含 awaiting-completion
        的 input-required 透传、completed）→ False，文本内容由调用方透传。
        """
        state = self.states.get(ns)
        if state is not None and state.spec.transport == "a2a":
            client = self._a2a_clients.get(ns)
            if client is None:
                raise RuntimeError(f"上游 {ns} 不在线")
            result = await client.call_skill(tool_name, arguments)
            return SimpleNamespace(
                content=[SimpleNamespace(text=result.text)],
                isError=(result.aip_state == "failed"),
            )
        session = self._sessions.get(ns)
        if session is None:
            raise RuntimeError(f"上游 {ns} 不在线")
        return await session.call_tool(tool_name, arguments)

    def build_acs(self) -> dict[str, Any]:
        """按当前在线上游重建聚合 ACS（失联的自动不在列，即'撤架'）。"""
        import datetime as _dt

        skills = []
        for state in self.online_states():
            acs_one = tools_to_acs(
                agent_name=GATEWAY_NAME,
                description=GATEWAY_DESC,
                server_ns=state.spec.ns,
                tools=list(state.tools.values()),
                endpoint_url=self._endpoint_url,
                aic=self._gateway_aic,
            )
            skills.extend(acs_one["skills"])
        return {
            "aic": self._gateway_aic,
            "active": True,
            "lastModifiedTime": _dt.datetime.now(_dt.timezone.utc).isoformat(),
            "protocolVersion": "02.02",
            "name": GATEWAY_NAME,
            "description": GATEWAY_DESC
            + " 当前聚合上游："
            + "、".join(f"{s.spec.ns}（{len(s.tools)} 工具）" for s in self.online_states()),
            "version": "1.1.0",
            "provider": {"organization": "研伴科研协作团队", "department": "", "url": "", "license": ""},
            "securitySchemes": {"mtls": {"type": "mutualTLS", "description": "智能体间mTLS双向认证"}},
            "endPoints": [{"url": self._endpoint_url, "transport": "JSONRPC", "security": [{"mtls": []}]}],
            "capabilities": {"streaming": False, "notification": False, "messageQueue": []},
            "defaultInputModes": ["text/plain", "application/json"],
            "defaultOutputModes": ["text/plain", "application/json"],
            "skills": skills,
        }
