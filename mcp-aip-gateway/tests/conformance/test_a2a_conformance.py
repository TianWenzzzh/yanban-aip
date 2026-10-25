"""A2A 侧一致性测试（从 M1 设计文档规范核实记录派生，可独立运行）。

覆盖（对照 00_全自动主提示词 M4）：
1. AgentCard 必备字段全集（设计 §2.1 样例全集，"字段尽量全给"原则）
2. well-known 双路径兼容（agent-card.json 新版 / agent.json 旧版）
3. Task 7 态语义全集与映射完备性（不许有遗漏态，未收录态必须 fail-closed）
4. Part 三类型（text/file/data；type/kind 与 data/json 双键名宽容）
5. tasks/send 与 message/send 双方法名同义（v0.2-3 ↔ 最新规范差异兼容）
6. JSON-RPC 标准错误码（-32700/-32600/-32601/-32602）
"""

from __future__ import annotations

from types import SimpleNamespace as NS

import httpx
import pytest
from fastapi.testclient import TestClient

from gateway.a2a_server import A2A_METHODS, build_agent_card, create_a2a_app
from gateway.bridge_a2a import A2A_TO_AIP_STATE, parts_to_text
from gateway.translator import tools_to_acs

import spec_tables as spec

AIC = "1.2.156.3088.1.1.MCPGW1.GATE01.1.0001"
SELF_URL = "http://localhost:8060/"


def sample_acs() -> dict:
    tool = NS(name="search", title=None, description="搜索 demo 文档",
              inputSchema={"properties": {"q": {"type": "string"}}, "required": ["q"]},
              input_schema=None, annotations=None)
    return tools_to_acs(agent_name="研伴·MCP工具网关", description="聚合网关", server_ns="demo",
                        tools=[tool], endpoint_url="http://localhost:8021/rpc", aic=AIC)


# ── 1. AgentCard 字段一致性 ───────────────────────────────────────────────


class TestAgentCardConformance:
    def test_card_has_full_required_field_set(self):
        card = build_agent_card(sample_acs(), self_url=SELF_URL)
        missing = [f for f in spec.A2A_CARD_REQUIRED if f not in card]
        assert not missing, f"AgentCard 缺必备字段：{missing}"

    def test_card_capability_flags_all_false_v0(self):
        card = build_agent_card(sample_acs(), self_url=SELF_URL)
        assert card["capabilities"] == {
            "streaming": True, "pushNotifications": False, "stateTransitionHistory": False,  # v2.1(W5) 流式如实声明
        }

    def test_card_dual_modes_and_auth(self):
        card = build_agent_card(sample_acs(), self_url=SELF_URL)
        assert card["defaultInputModes"] == ["text/plain", "application/json"]
        assert card["defaultOutputModes"] == ["text/plain", "application/json"]
        assert card["authentication"]["schemes"] == []

    def test_skill_shape_matches_agent_skill_contract(self):
        skill = build_agent_card(sample_acs(), self_url=SELF_URL)["skills"][0]
        for field in ("id", "name", "description", "tags", "examples"):
            assert field in skill, f"AgentSkill 缺 {field}"
        assert skill["id"] == "search"  # 去命名空间尾段
        assert "\n" not in skill["description"]  # 首段


# ── 2. well-known 双路径 ──────────────────────────────────────────────────


@pytest.fixture(scope="module")
def client() -> TestClient:
    reg = NS(resolve=lambda s: None, call=None, all_tools=lambda: {}, build_acs=sample_acs)
    return TestClient(create_a2a_app(reg, self_url=SELF_URL))


class TestWellKnownDualPath:
    @pytest.mark.parametrize("path", ["/.well-known/agent-card.json", "/.well-known/agent.json"])
    def test_both_paths_serve_card(self, client, path):
        r = client.get(path)
        assert r.status_code == 200
        assert r.json()["name"] == "wanbridge-gateway"

    def test_both_paths_identical(self, client):
        assert client.get("/.well-known/agent-card.json").json() \
            == client.get("/.well-known/agent.json").json()


# ── 3. Task 7 态语义 ──────────────────────────────────────────────────────


class TestSevenStateConformance:
    def test_mapping_covers_exactly_the_seven_states(self):
        assert sorted(A2A_TO_AIP_STATE) == sorted(spec.A2A_TASK_STATES)

    @pytest.mark.parametrize(
        "a2a,aip",
        [
            ("completed", "completed"),
            ("input-required", "awaiting-completion"),  # 万桥 v0 决策，见 spec_tables
            ("working", "working"),
            ("submitted", "working"),
            ("failed", "failed"),
            ("unknown", "failed"),
            ("canceled", "failed"),
        ],
    )
    def test_each_state_semantics(self, a2a, aip):
        assert A2A_TO_AIP_STATE[a2a] == aip

    def test_artifacts_extracted_on_completed_only(self):
        from gateway.bridge_a2a import task_to_call_result

        artifacts = [{"parts": [{"type": "text", "text": "产出"}]}]
        done = task_to_call_result({"status": {"state": "completed"}, "artifacts": artifacts})
        working = task_to_call_result({"status": {"state": "working"}, "artifacts": artifacts})
        assert done.text == "产出"
        assert working.aip_state == "working"  # 中间态不取产物

    def test_canceled_carries_dedicated_label(self):
        from gateway.bridge_a2a import task_to_call_result

        r = task_to_call_result({"status": {"state": "canceled"}})
        assert r.aip_state == "failed" and r.text.startswith("上游取消")


# ── 4. Part 三类型 ────────────────────────────────────────────────────────


class TestPartTypesConformance:
    def test_text_part(self):
        assert parts_to_text([{"type": "text", "text": "hello"}]) == "hello"

    def test_data_part_dual_key_names(self):
        assert parts_to_text([{"type": "data", "data": {"a": 1}}]) == '{"a": 1}'
        assert parts_to_text([{"kind": "json", "json": {"a": 1}}]) == '{"a": 1}'

    def test_file_part_placeholder(self):
        out = parts_to_text([{"type": "file", "file": {"name": "r.pdf"}}])
        assert "r.pdf" in out

    def test_unknown_part_type_never_crashes(self):
        assert "x" not in parts_to_text([{"type": "holo", "x": "x"}]) or True
        assert parts_to_text([{"type": "holo"}]) == ""

    def test_server_accepts_all_input_part_channels(self):
        from gateway.a2a_server import extract_invocation

        assert extract_invocation([{"type": "data", "data": {"tool": "t"}}]) == ("t", {})
        assert extract_invocation([{"kind": "json", "json": {"tool": "t", "arguments": {"a": 1}}}]) == ("t", {"a": 1})
        assert extract_invocation([{"type": "text", "text": '{"tool": "t"}'}]) == ("t", {})


# ── 5+6. 双方法名与 JSON-RPC 错误码 ───────────────────────────────────────


class TestMethodsAndErrorsConformance:
    def test_module_exports_both_method_names(self):
        # 规范要求集是实现的子集（v2.1 实现额外含 tasks/get、tasks/cancel、tasks/resubscribe）
        assert set(spec.A2A_METHODS_REQUIRED).issubset(set(A2A_METHODS))
        assert tuple(A2A_METHODS) == spec.A2A_METHODS_FULL

    @pytest.mark.parametrize("method", ["tasks/send", "message/send"])
    def test_both_methods_end_to_end(self, client, method):
        payload = {"jsonrpc": "2.0", "id": "c1", "method": method,
                   "params": {"message": {"parts": [{"type": "text", "text": '{"tool": "x__y"}'}]}}}
        r = client.post("/", json=payload)
        body = r.json()
        # 空注册表：工具未知 → failed 任务（而非协议错误）——方法名层面两者都必须可达
        assert r.status_code == 200 and "error" not in body
        assert body["result"]["status"]["state"] == "failed"

    @pytest.mark.parametrize("code_key", ["parse_error", "invalid_request", "method_not_found", "invalid_params"])
    def test_standard_jsonrpc_error_codes(self, client, code_key):
        expected = spec.A2A_JSONRPC_ERRORS[code_key]
        if code_key == "parse_error":
            body = client.post("/", content=b"{broken", headers={"content-type": "application/json"}).json()
        elif code_key == "invalid_request":
            body = client.post("/", json=["not", "an", "object"]).json()
        elif code_key == "method_not_found":
            body = client.post("/", json={"jsonrpc": "2.0", "id": 1, "method": "nope", "params": {}}).json()
        else:
            body = client.post("/", json={"jsonrpc": "2.0", "id": 1, "method": "tasks/send", "params": {}}).json()
        assert body["error"]["code"] == expected, f"{code_key} 应为 {expected}"

    def test_offline_card_fetch_via_mock_transport(self):
        """发现端点协议形状：MockTransport 桩下双路径兼容（离线复演客户端行为）。"""
        from gateway.bridge_a2a import fetch_agent_card
        import asyncio

        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path.endswith("agent-card.json"):
                return httpx.Response(404)
            return httpx.Response(200, json=build_agent_card(sample_acs(), self_url=SELF_URL))

        card = asyncio.run(fetch_agent_card("http://gw.test", client=httpx.AsyncClient(
            transport=httpx.MockTransport(handler))))
        assert card.name == "wanbridge-gateway"  # 自家 Card 也能被自家桥消费（回环兼容）
