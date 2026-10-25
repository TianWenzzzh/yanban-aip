"""a2a_server.py 离线单元测试（无网络：fake registry + FastAPI TestClient）。

设计依据：docs/design_wanbridge_a2a.md §2。覆盖：
AgentCard 动态生成（尾段 id/首段描述/去内部标签/metadata）/ well-known 双路径 /
双方法名 tasks/send+message/send / Part 三类型入参提取 / 未知工具自纠错清单 /
JSON-RPC 标准错误码。不测 create_standalone_app（会拉起真实 McpRegistry 子进程）。
"""

from __future__ import annotations

import json
from types import SimpleNamespace as NS

import pytest
from fastapi.testclient import TestClient

from gateway.a2a_server import (
    A2A_METHODS,
    A2A_SEND_METHODS,
    build_agent_card,
    create_a2a_app,
    extract_invocation,
    handle_jsonrpc,
    handle_tasks_send,
)
from gateway.translator import tools_to_acs

AIC = "1.2.156.3088.1.1.MCPGW1.GATE01.1.0001"
SELF_URL = "http://localhost:8060/"


def sample_acs() -> dict:
    tool = NS(
        name="search",
        title=None,
        description="搜索 demo 文档",
        inputSchema={"properties": {"q": {"type": "string"}}, "required": ["q"]},
        input_schema=None,
        annotations=None,
    )
    return tools_to_acs(
        agent_name="研伴·MCP工具网关",
        description="聚合网关",
        server_ns="demo",
        tools=[tool],
        endpoint_url="http://localhost:8021/rpc",
        aic=AIC,
    )


def fake_registry():
    state = NS(spec=NS(ns="demo"))
    tool = NS(name="search")

    async def call(ns, name, arguments):
        return NS(content=[NS(text=f"demo/{name} => {arguments.get('q', '')}")], isError=False)

    return NS(
        resolve=lambda sid: (state, tool) if sid in ("demo__search", "search") else None,
        call=call,
        all_tools=lambda: {"demo__search": (state, tool)},
        build_acs=sample_acs,
    )


# ── AgentCard 动态生成 ────────────────────────────────────────────────────


class TestBuildAgentCard:
    def test_card_top_level_fields(self):
        card = build_agent_card(sample_acs(), self_url=SELF_URL)
        assert card["name"] == "wanbridge-gateway"
        assert card["url"] == SELF_URL
        assert card["protocolVersion"] == "0.3.0"
        assert card["capabilities"] == {
            "streaming": True, "pushNotifications": False, "stateTransitionHistory": False,  # v2.1(W5)
        }
        assert card["authentication"] == {"schemes": []}
        assert card["metadata"] == {"aic": AIC, "upstreams": 1, "tools": 1}

    def test_skill_mapping_tail_id_first_line_tags(self):
        card = build_agent_card(sample_acs(), self_url=SELF_URL)
        skill = card["skills"][0]
        assert skill["id"] == "search"  # 去命名空间尾段
        assert skill["description"] == "搜索 demo 文档"  # 首段（不含【输入要求】等内部段）
        assert "【输入要求】" not in skill["description"]
        assert skill["tags"] == []  # demo 与 MCP工具 均为内部标签，已去除
        assert skill["examples"] and isinstance(skill["examples"], list)

    def test_empty_acs_yields_empty_skills(self):
        card = build_agent_card({"aic": AIC, "skills": []}, self_url=SELF_URL)
        assert card["skills"] == []
        assert card["metadata"]["upstreams"] == 0


# ── 入参提取（Part 三类型） ───────────────────────────────────────────────


class TestExtractInvocation:
    def test_json_part_type_data_key(self):
        hit = extract_invocation([{"type": "data", "data": {"tool": "demo__search", "arguments": {"q": 1}}}])
        assert hit == ("demo__search", {"q": 1})

    def test_json_part_kind_json_key(self):
        hit = extract_invocation([{"kind": "json", "json": {"tool": "search"}}])
        assert hit == ("search", {})

    def test_text_part_contract_json(self):
        hit = extract_invocation([{"type": "text", "text": '{"tool": "demo__search", "arguments": {"q": "x"}}'}])
        assert hit == ("demo__search", {"q": "x"})

    def test_text_part_lenient_llm_style(self):
        hit = extract_invocation([{"type": "text", "text": 'demo__search {"q": "x"}'}])
        assert hit == ("demo__search", {"q": "x"})

    def test_text_part_beats_later_parts_none(self):
        assert extract_invocation([{"type": "text", "text": "闲聊"}]) is None
        assert extract_invocation([]) is None
        assert extract_invocation(None) is None


# ── tasks/send 执行体 ─────────────────────────────────────────────────────


def run_tasks(params: dict) -> dict:
    reg = fake_registry()
    import asyncio

    return asyncio.run(handle_tasks_send(
        params, resolve=reg.resolve, call=reg.call, available_ids=reg.all_tools,
    ))


class TestHandleTasksSend:
    def test_completed_with_artifact_and_echo_ids(self):
        params = {
            "id": "t-9", "sessionId": "s-9",
            "message": {"role": "user", "parts": [{"type": "data", "data": {"tool": "demo__search", "arguments": {"q": "万桥"}}}]},
        }
        task = run_tasks(params)
        assert task["id"] == "t-9" and task["sessionId"] == "s-9"
        assert task["status"]["state"] == "completed"
        assert task["artifacts"][0]["name"] == "result"
        assert task["artifacts"][0]["parts"][0]["text"] == "demo/search => 万桥"

    def test_generated_ids_when_missing(self):
        task = run_tasks({"message": {"parts": [{"type": "text", "text": '{"tool": "search"}'}]}})
        assert task["id"] and task["sessionId"]
        assert task["status"]["state"] == "completed"

    def test_unknown_tool_self_corrects_with_available_list(self):
        params = {"message": {"parts": [{"type": "data", "data": {"tool": "nope"}}]}}
        task = run_tasks(params)
        assert task["status"]["state"] == "failed"
        assert "nope" in task["status"]["message"]["parts"][0]["text"]
        assert "demo__search" in task["status"]["message"]["parts"][0]["text"]

    def test_upstream_exception_maps_failed(self):
        reg = fake_registry()

        async def boom(ns, name, arguments):
            raise RuntimeError("上游断了")

        reg.call = boom
        import asyncio

        task = asyncio.run(handle_tasks_send(
            {"message": {"parts": [{"type": "data", "data": {"tool": "demo__search"}}]}},
            resolve=reg.resolve, call=reg.call, available_ids=reg.all_tools,
        ))
        assert task["status"]["state"] == "failed"
        assert "上游断了" in task["status"]["message"]["parts"][0]["text"]

    def test_no_invocation_guidance(self):
        task = run_tasks({"message": {"parts": [{"type": "text", "text": "你好"}]}})
        assert task["status"]["state"] == "failed"
        assert "JsonPart" in task["status"]["message"]["parts"][0]["text"]


# ── JSON-RPC 分发（双方法名） ─────────────────────────────────────────────


class TestHandleJsonrpc:
    def test_both_method_names_synonyms(self):
        reg = fake_registry()

        async def go(method):
            payload = {
                "jsonrpc": "2.0", "id": "r1", "method": method,
                "params": {"message": {"parts": [{"type": "data", "data": {"tool": "search"}}]}},
            }
            return await handle_jsonrpc(payload, resolve=reg.resolve, call=reg.call, available_ids=reg.all_tools)

        import asyncio

        for method in A2A_SEND_METHODS:  # send 双方法名同义；tasks/get、tasks/cancel 见 test_a2a_tasks.py
            resp = asyncio.run(go(method))
            assert resp["id"] == "r1"
            assert resp["result"]["status"]["state"] == "completed"

    def test_method_not_found_32601(self):
        import asyncio

        resp = asyncio.run(handle_jsonrpc(
            {"jsonrpc": "2.0", "id": 7, "method": "tasks/subscribe", "params": {}},
            resolve=None, call=None, available_ids=None,
        ))
        assert resp["error"]["code"] == -32601 and resp["id"] == 7

    def test_invalid_params_32602(self):
        import asyncio

        reg = fake_registry()
        resp = asyncio.run(handle_jsonrpc(
            {"jsonrpc": "2.0", "id": 1, "method": "tasks/send", "params": {"id": "x"}},
            resolve=reg.resolve, call=reg.call, available_ids=reg.all_tools,
        ))
        assert resp["error"]["code"] == -32602

    def test_invalid_request_32600(self):
        import asyncio

        resp = asyncio.run(handle_jsonrpc("not a dict", resolve=None, call=None, available_ids=None))
        assert resp["error"]["code"] == -32600


# ── HTTP 层（TestClient 全链路） ──────────────────────────────────────────


@pytest.fixture()
def client() -> TestClient:
    return TestClient(create_a2a_app(fake_registry(), self_url=SELF_URL))


class TestHttpLayer:
    def test_well_known_dual_paths_identical(self, client):
        r1 = client.get("/.well-known/agent-card.json")
        r2 = client.get("/.well-known/agent.json")
        assert r1.status_code == r2.status_code == 200
        assert r1.json() == r2.json()
        assert r1.json()["skills"][0]["id"] == "search"

    def test_post_root_tasks_send(self, client):
        payload = {
            "jsonrpc": "2.0", "id": "http-1", "method": "tasks/send",
            "params": {"message": {"parts": [{"type": "text", "text": '{"tool": "demo__search", "arguments": {"q": "a2a"}}'}]}},
        }
        r = client.post("/", json=payload)
        assert r.status_code == 200
        assert r.json()["result"]["artifacts"][0]["parts"][0]["text"] == "demo/search => a2a"

    def test_post_rpc_message_send(self, client):
        payload = {
            "jsonrpc": "2.0", "id": "http-2", "method": "message/send",
            "params": {"message": {"parts": [{"type": "data", "data": {"tool": "search", "arguments": {}}}]}},
        }
        r = client.post("/rpc", json=payload)
        assert r.status_code == 200
        assert r.json()["result"]["status"]["state"] == "completed"

    def test_invalid_json_parse_error_32700(self, client):
        r = client.post("/", content=b"{broken", headers={"content-type": "application/json"})
        assert r.status_code == 200  # JSON-RPC 错误走 200 + error 对象（宽容传输层）
        assert r.json()["error"]["code"] == -32700


# ── MCP 结果错误标志兼容（v1.2 实测坑：SDK 2.x 移除 isError） ─────────────


class TestCallResultIsError:
    def test_dual_key_names(self):
        from gateway.registry import call_result_is_error

        assert call_result_is_error(NS(isError=True)) is True  # SDK 1.x 风格
        assert call_result_is_error(NS(is_error=True)) is True  # SDK 2.x 风格
        assert call_result_is_error(NS(isError=False)) is False
        assert call_result_is_error(NS()) is False  # 双缺省不炸

    def test_empty_content_yields_is_error_payload_not_crash(self):
        """空 content 工具结果：A2A 面应产出 isError JSON 兜底，而非 AttributeError（旧代码 500 路径）。"""
        import asyncio

        reg = fake_registry()

        async def empty(ns, name, arguments):
            return NS(content=[], is_error=True)  # SDK 2.x 风格且无文本

        reg.call = empty
        task = asyncio.run(handle_tasks_send(
            {"message": {"parts": [{"type": "data", "data": {"tool": "demo__search"}}]}},
            resolve=reg.resolve, call=reg.call, available_ids=reg.all_tools,
        ))
        assert task["status"]["state"] == "completed"
        assert '"isError": true' in task["artifacts"][0]["parts"][0]["text"]
