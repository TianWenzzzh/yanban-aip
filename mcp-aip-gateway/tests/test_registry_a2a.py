"""registry A2A 上游集成离线单元测试（无网络：注入假工厂/假会话驱动 owner 循环）。

设计依据：docs/design_wanbridge_a2a.md §1 + 任务书 N2。覆盖：
技能壳对象（Tool 同构）/ a2a spec 解析 / owner 生命周期（上架-探活-撤架-漂移）/
call 分发与适配壳（isError 语义）/ 混合上游聚合路由 / 多上游启动竞态回归（bb87f00 还债）。
"""

from __future__ import annotations

import asyncio
import contextlib
import time
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

from gateway.bridge_a2a import A2aCallResult, A2aClient, A2aError, parse_agent_card, skills_to_tool_shells
from gateway.registry import McpRegistry
from gateway.translator import tool_to_skill


def run(coro):
    """同步包装：沿用既有测试的同步风格，避免引入 pytest-asyncio 依赖。"""
    return asyncio.run(coro)


CARD = {
    "name": "Remote Agent",
    "description": "A remote A2A agent",
    "url": "http://a2a.test/rpc",
    "version": "1.0.0",
    "skills": [
        {"id": "search", "name": "Search", "description": "Do a search", "tags": ["x"], "examples": ["ex"]},
        {"id": "summarize", "name": "Summarize", "description": "Summarize text"},
    ],
}


def card_with(skills: list[dict], name: str = "Remote Agent") -> dict:
    return {**CARD, "name": name, "skills": skills}


class FakeA2aClient:
    """假 A2aClient：cards 队列逐次出队（末张粘滞），Exception 元素表示该轮探活失败。"""

    def __init__(self, cards: list, results: dict[str, A2aCallResult] | None = None):
        self.cards = list(cards)
        self.results = results or {}
        self.calls: list[tuple[str, dict]] = []

    async def refetch_card(self):
        item = self.cards.pop(0) if len(self.cards) > 1 else self.cards[0]
        if isinstance(item, Exception):
            raise item
        return parse_agent_card(item) if isinstance(item, dict) else item

    async def call_skill(self, skill_tail, arguments=None, **kwargs):
        self.calls.append((skill_tail, arguments))
        result = self.results.get(skill_tail, A2aCallResult("completed", "completed", "ok"))
        if isinstance(result, Exception):
            raise result
        return result


class FakeSession:
    """假 MCP ClientSession：initialize/list_tools/call_tool 最小实现。"""

    def __init__(self, tools: list[str], delay: float = 0.0, text: str = "mcp-ok"):
        self.tools = tools
        self.delay = delay
        self.text = text
        self.calls: list[tuple[str, dict]] = []

    async def initialize(self):
        if self.delay:
            await asyncio.sleep(self.delay)

    async def list_tools(self):
        return SimpleNamespace(
            tools=[SimpleNamespace(name=n, description="d", inputSchema=None) for n in self.tools]
        )

    async def call_tool(self, name, arguments):
        self.calls.append((name, arguments))
        return SimpleNamespace(content=[SimpleNamespace(text=self.text)], isError=False)


def inject_a2a(registry: McpRegistry, client: FakeA2aClient) -> None:
    @contextlib.asynccontextmanager
    async def factory(spec):
        yield client

    registry._a2a_client_factory = factory


def inject_mcp(registry: McpRegistry, sessions: dict[str, FakeSession]) -> None:
    @contextlib.asynccontextmanager
    async def factory(spec):
        yield sessions[spec.ns]

    registry._connect = factory


def write_config(tmp_path: Path, servers: list[dict], probe: float = 0.05) -> Path:
    lines = [f'probe = {probe}', "[gateway]", f"probe_interval_seconds = {probe}"]
    for s in servers:
        lines.append("[[server]]")
        for k, v in s.items():
            if isinstance(v, str):
                lines.append(f'{k} = "{v}"')
            elif isinstance(v, list):
                lines.append(f"{k} = [" + ", ".join(f'"{x}"' for x in v) + "]")
            else:
                lines.append(f"{k} = {v}")
    p = tmp_path / "config.toml"
    p.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return p


def make_registry(tmp_path: Path, servers: list[dict], probe: float = 0.05) -> McpRegistry:
    reg = McpRegistry(
        config_path=write_config(tmp_path, servers, probe),
        endpoint_url="http://localhost:8021/rpc",
        gateway_aic="1.2.156.3088.1.1.MCPGW1.GATE01.1.0001",
    )
    reg.probe_interval = probe
    return reg


# ── 技能壳对象（bridge 侧）────────────────────────────────────────────────

def test_skill_shell_fields():
    shells = skills_to_tool_shells(CARD["skills"])
    assert set(shells) == {"search", "summarize"}
    shell = shells["search"]
    assert shell.name == "search" and shell.title == "Search"
    assert "Do a search" in shell.description
    assert "【输入要求】" in shell.description  # 弱契约声明（§1.2 关键差异）
    assert shell.inputSchema is None and shell.annotations is None


def test_skill_shell_missing_id_raises():
    with pytest.raises(A2aError):
        skills_to_tool_shells([{"name": "no-id"}])


def test_skill_shell_empty_desc_fallback():
    (tail, shell), = skills_to_tool_shells([{"id": "solo"}]).items()
    assert tail == "solo"
    assert shell.description.startswith("A2A 技能 solo")


def test_skills_to_tool_shells_duplicate_id_last_wins():
    shells = skills_to_tool_shells([{"id": "dup", "name": "A"}, {"id": "dup", "name": "B"}])
    assert len(shells) == 1 and shells["dup"].title == "B"


def test_shell_translates_via_translator():
    """壳对象走 translator.tool_to_skill 同一翻译路径（与 MCP Tool 零差异）。"""
    shells = skills_to_tool_shells(CARD["skills"])
    skill = tool_to_skill("remote", shells["search"])
    assert skill["id"] == "remote__search"
    assert skill["name"] == "Search"
    assert "弱契约" in skill["description"] or "【输入要求】" in skill["description"]
    assert "remote" in skill["tags"]


# ── spec 解析与生命周期 ───────────────────────────────────────────────────

def test_parse_a2a_spec(tmp_path):
    reg = make_registry(tmp_path, [
        {"ns": "remote", "transport": "a2a", "url": "http://127.0.0.1:8070", "description": "远端"},
    ])
    spec = reg.states["remote"].spec
    assert spec.transport == "a2a" and spec.url == "http://127.0.0.1:8070"


def test_a2a_upstream_online_and_acs(tmp_path):
    reg = make_registry(tmp_path, [{"ns": "remote", "transport": "a2a", "url": "http://a2a.test"}])
    inject_a2a(reg, FakeA2aClient([CARD]))

    async def scenario():
        await reg.start()
        try:
            state = reg.states["remote"]
            assert state.ready.is_set() and state.online
            assert set(state.tools) == {"search", "summarize"}
            assert reg._a2a_clients["remote"] is not None
            acs = reg.build_acs()
            ids = {s["id"] for s in acs["skills"]}
            assert "remote__search" in ids and "remote__summarize" in ids
            desc = next(s for s in acs["skills"] if s["id"] == "remote__search")["description"]
            assert "【输入要求】" in desc
            assert "remote" in acs["description"]  # 聚合描述列出上游
        finally:
            await reg.stop()

    run(scenario())


def test_a2a_probe_failure_decommissions(tmp_path):
    reg = make_registry(tmp_path, [{"ns": "remote", "transport": "a2a", "url": "http://a2a.test"}])
    inject_a2a(reg, FakeA2aClient([CARD, A2aError("上游失联")]))

    async def scenario():
        await reg.start()
        assert reg.states["remote"].online
        await asyncio.sleep(0.25)  # 等一个探活周期（probe=0.05）
        state = reg.states["remote"]
        assert not state.online and not state.tools and state.error
        assert "remote" not in reg._a2a_clients
        assert not reg.all_tools()  # 撤架：聚合目录为空
        await reg.stop()

    run(scenario())


def test_a2a_card_drift_refreshes_tools(tmp_path):
    drifted = card_with([CARD["skills"][0], {"id": "translate", "name": "Translate"}])
    reg = make_registry(tmp_path, [{"ns": "remote", "transport": "a2a", "url": "http://a2a.test"}])
    inject_a2a(reg, FakeA2aClient([CARD, drifted]))

    async def scenario():
        await reg.start()
        assert set(reg.states["remote"].tools) == {"search", "summarize"}
        await asyncio.sleep(0.25)
        assert set(reg.states["remote"].tools) == {"search", "translate"}
        await reg.stop()

    run(scenario())


def test_unsupported_transport_decommissions(tmp_path):
    reg = make_registry(tmp_path, [{"ns": "bad", "transport": "xyz", "url": "http://x"}])

    async def scenario():
        await reg.start()
        state = reg.states["bad"]
        assert state.ready.is_set() and not state.online
        assert "不支持" in state.error
        await reg.stop()

    run(scenario())


# ── call 分发与适配壳 ────────────────────────────────────────────────────

def make_online_a2a_registry(tmp_path, results=None) -> tuple[McpRegistry, FakeA2aClient]:
    reg = make_registry(tmp_path, [{"ns": "remote", "transport": "a2a", "url": "http://a2a.test"}])
    client = FakeA2aClient([CARD], results=results)
    inject_a2a(reg, client)

    async def bring_up():
        await reg.start()
        await reg.stop()

    # start/stop 全周期只为了进 _a2a_clients；call 测试直接手动注入等价状态
    reg2 = make_registry(tmp_path / "b" if False else tmp_path, [
        {"ns": "remote", "transport": "a2a", "url": "http://a2a.test"}
    ]) if False else reg
    # 直接置运行态（绕过事件循环）
    reg2._a2a_clients["remote"] = client
    reg2.states["remote"].online = True
    reg2.states["remote"].tools = skills_to_tool_shells(CARD["skills"])
    reg2.states["remote"].ready.set()
    return reg2, client


def test_a2a_call_completed_shell(tmp_path):
    reg, client = make_online_a2a_registry(tmp_path)
    result = run(reg.call("remote", "search", {"q": "llm"}))
    assert result.content[0].text == "ok"
    assert result.isError is False
    assert client.calls == [("search", {"q": "llm"})]


def test_a2a_call_failed_sets_is_error(tmp_path):
    reg, _ = make_online_a2a_registry(tmp_path, results={
        "search": A2aCallResult("failed", "failed", "上游返回状态 failed"),
    })
    result = run(reg.call("remote", "search", {}))
    assert result.isError is True
    assert result.content[0].text == "上游返回状态 failed"


def test_a2a_call_awaiting_completion_not_error(tmp_path):
    """input-required → awaiting-completion 透传：是正常流（Leader 可 continue），非错误。"""
    reg, _ = make_online_a2a_registry(tmp_path, results={
        "search": A2aCallResult("input-required", "awaiting-completion", "请补充研究主题"),
    })
    result = run(reg.call("remote", "search", {}))
    assert result.isError is False
    assert result.content[0].text == "请补充研究主题"


def test_a2a_call_offline_raises(tmp_path):
    reg = make_registry(tmp_path, [{"ns": "remote", "transport": "a2a", "url": "http://a2a.test"}])
    with pytest.raises(RuntimeError):
        run(reg.call("remote", "search", {}))


# ── 混合上游聚合与路由 ───────────────────────────────────────────────────

def test_all_tools_mixed_and_call_routes_by_transport(tmp_path):
    reg = make_registry(tmp_path, [
        {"ns": "remote", "transport": "a2a", "url": "http://a2a.test"},
        {"ns": "local", "transport": "stdio", "command": "python", "args": ["demo.py"]},
    ])
    a2a_client = FakeA2aClient([CARD])
    mcp_session = FakeSession(["echo"])
    inject_a2a(reg, a2a_client)
    inject_mcp(reg, {"local": mcp_session})

    async def scenario():
        await reg.start()
        try:
            catalog = reg.all_tools()
            assert set(catalog) == {"remote__search", "remote__summarize", "local__echo"}
            assert reg.resolve("remote__search") is not None
            assert reg.resolve("echo") is not None  # 裸名唯一时也可解析

            mcp_result = await reg.call("local", "echo", {"x": 1})
            assert mcp_result.content[0].text == "mcp-ok"
            a2a_result = await reg.call("remote", "search", {"q": 1})
            assert a2a_result.content[0].text == "ok"
            assert mcp_session.calls and a2a_client.calls  # 各自路由到各自通道
        finally:
            await reg.stop()

    run(scenario())


# ── bb87f00 竞态还债：慢上游不阻塞快上游上架，start() 等全部就绪 ─────────

def test_start_race_regression_slow_upstream(tmp_path):
    reg = make_registry(tmp_path, [
        {"ns": "fast", "transport": "stdio", "command": "python", "args": ["fast.py"]},
        {"ns": "slow", "transport": "stdio", "command": "python", "args": ["slow.py"]},
    ], probe=0.05)
    inject_mcp(reg, {
        "fast": FakeSession(["echo"]),
        "slow": FakeSession(["heavy"], delay=0.4),
    })

    async def scenario():
        t0 = time.monotonic()
        await reg.start()
        duration = time.monotonic() - t0
        try:
            # 修复语义：start() 返回时两个上游都已完成首刷（旧 bug：fast 先就绪即放行，
            # start() 返回时 slow 还在初始化、被 /health 当作已撤架）
            assert reg.states["fast"].online and reg.states["fast"].ready.is_set()
            assert reg.states["slow"].online and reg.states["slow"].ready.is_set()
            assert set(reg.all_tools()) == {"fast__echo", "slow__heavy"}
            assert duration >= 0.35  # start() 确实等到了慢上游
        finally:
            await reg.stop()

    run(scenario())


# ── P 池：Bearer 鉴权（§1.1；token 只经 config 注入内存）─────────────────

def make_bearer_client(handler, card, token=None) -> A2aClient:
    transport = httpx.MockTransport(handler)
    return A2aClient(card, client=httpx.AsyncClient(transport=transport), token=token)


def test_bearer_token_sent_when_configured():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["auth"] = request.headers.get("Authorization")
        return httpx.Response(200, json={"jsonrpc": "2.0", "id": "1", "result": {
            "id": "t", "status": {"state": "completed"}, "artifacts": []}})

    card = parse_agent_card({"name": "S", "url": "http://a2a.test/rpc", "skills": []})
    client = make_bearer_client(handler, card, token="sekrit-token")
    run(client.call_skill("search", {}))
    assert seen["auth"] == "Bearer sekrit-token"


def test_no_token_no_auth_header():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["auth"] = request.headers.get("Authorization")
        return httpx.Response(200, json={"jsonrpc": "2.0", "id": "1", "result": {
            "id": "t", "status": {"state": "completed"}, "artifacts": []}})

    card = parse_agent_card({"name": "S", "url": "http://a2a.test/rpc", "skills": []})
    client = make_bearer_client(handler, card)
    run(client.call_skill("search", {}))
    assert seen["auth"] is None


def test_bearer_card_without_token_raises():
    card = parse_agent_card({"name": "S", "url": "http://a2a.test/rpc",
                             "authentication": {"schemes": ["Bearer"]}})
    with pytest.raises(A2aError):
        make_bearer_client(lambda r: httpx.Response(200), card, token=None)


def test_unsupported_scheme_rejected():
    card = parse_agent_card({"name": "S", "url": "http://a2a.test/rpc",
                             "authentication": {"schemes": ["mtls"]}})
    with pytest.raises(A2aError):
        make_bearer_client(lambda r: httpx.Response(200), card, token="x")


def test_config_token_parsed(tmp_path):
    reg = make_registry(tmp_path, [
        {"ns": "remote", "transport": "a2a", "url": "http://a2a.test", "token": "cfg-secret"},
    ])
    assert reg.states["remote"].spec.token == "cfg-secret"


# ── v2.0.1 断线自动重连（Codex 问题②修复）────────────────────────────────

def test_upstream_recovers_without_restart(tmp_path):
    """上游挂 → 撤架；恢复后 owner 自动重连上架（无需重启网关）。"""
    reg = make_registry(tmp_path, [
        {"ns": "remote", "transport": "a2a", "url": "http://a2a.test"},
    ], probe=0.05)
    reg._reconnect_backoff_start = 0.05
    reg._reconnect_backoff_max = 0.1
    fail_client = FakeA2aClient([A2aError("探活失败")])  # 粘滞失败
    good_client = FakeA2aClient([CARD])
    invocations = {"n": 0}

    @contextlib.asynccontextmanager
    async def factory(spec):
        invocations["n"] += 1
        # 前两次连接（首次 + 第一次重连）失败，第三次起恢复
        yield fail_client if invocations["n"] <= 2 else good_client

    reg._a2a_client_factory = factory

    async def scenario():
        await reg.start()
        assert not reg.states["remote"].online  # 首连失败 → 撤架
        await asyncio.sleep(0.5)                # 重连循环第三次连接成功
        assert reg.states["remote"].online, f"重连失败: {reg.states['remote'].error}"
        assert set(reg.states["remote"].tools) == {"search", "summarize"}
        assert invocations["n"] >= 3
        await reg.stop()

    run(scenario())


def test_persistent_failure_stays_decommissioned_no_crash(tmp_path):
    reg = make_registry(tmp_path, [
        {"ns": "remote", "transport": "a2a", "url": "http://a2a.test"},
    ], probe=0.05)
    reg._reconnect_backoff_start = 0.03
    reg._reconnect_backoff_max = 0.06
    inject_a2a(reg, FakeA2aClient([A2aError("一直挂")]))  # 粘滞失败

    async def scenario():
        await reg.start()
        await asyncio.sleep(0.3)  # 多轮重连均失败
        state = reg.states["remote"]
        assert not state.online and not state.tools and state.error
        assert len(reg._owner_tasks) == 1 and not reg._owner_tasks[0].done()  # owner 仍活着
        await reg.stop()

    run(scenario())
