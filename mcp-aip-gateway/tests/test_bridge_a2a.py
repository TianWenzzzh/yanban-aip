"""bridge_a2a.py 离线单元测试（无网络：httpx.MockTransport 桩模拟 A2A 上游）。

设计依据：docs/design_wanbridge_a2a.md §1。覆盖：
双路径发现 / AgentCard 解析 / AgentSkill→AIP 弱契约翻译 / tasks/send 请求构造 /
Task 7 态映射 / Part 三类型解析 / 超时与连接错误的撤架语义。
"""

from __future__ import annotations

import asyncio
import json

import httpx
import pytest

from gateway.bridge_a2a import (
    A2aCallResult,
    A2aCardNotFound,
    A2aClient,
    A2aError,
    aip_state_from_a2a,
    build_tasks_send_payload,
    fetch_agent_card,
    parse_agent_card,
    parts_to_text,
    skill_to_aip,
    skills_to_aip,
    slugify,
    task_to_call_result,
)


def run(coro):
    """同步包装：沿用既有测试的同步风格，避免引入 pytest-asyncio 依赖。"""
    return asyncio.run(coro)


CARD = {
    "name": "Research Assistant",
    "description": "A helper agent for research tasks",
    "url": "http://a2a.test/rpc",
    "version": "0.1.0",
    "protocolVersion": "0.3.0",
    "provider": {"organization": "ACME"},
    "capabilities": {"streaming": True},
    "authentication": {"schemes": []},
    "skills": [
        {
            "id": "summarize",
            "name": "总结",
            "description": "总结一段文本。\n第二段不应出现。",
            "tags": ["nlp"],
            "examples": ["请总结这篇文章"],
        },
        {"id": "translate", "name": "翻译", "description": "翻译文本"},
    ],
}


def mock_client(handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


# ── slugify / 状态映射 ────────────────────────────────────────────────────


class TestSlugify:
    def test_folds_non_alnum_to_dash(self):
        assert slugify("Research Assistant") == "research-assistant"
        assert slugify("研伴 Agent!") == "agent"  # 非 ASCII 折叠后去首尾

    def test_collapses_repeats_and_strips(self):
        assert slugify("--A  B--") == "a-b"

    def test_empty_falls_back(self):
        assert slugify("研伴") == "a2a-agent"


class TestAipStateFromA2a:
    @pytest.mark.parametrize(
        "a2a,aip",
        [
            ("completed", "completed"),
            ("input-required", "awaiting-completion"),
            ("working", "working"),
            ("submitted", "working"),
            ("failed", "failed"),
            ("unknown", "failed"),
            ("canceled", "failed"),
        ],
    )
    def test_seven_states(self, a2a, aip):
        assert aip_state_from_a2a(a2a) == aip

    def test_unlisted_state_fails_closed(self):
        assert aip_state_from_a2a("some-future-state") == "failed"


# ── AgentCard 发现与解析 ──────────────────────────────────────────────────


class TestParseAgentCard:
    def test_full_card(self):
        info = parse_agent_card(CARD)
        assert info.ns == "research-assistant"
        assert info.url == "http://a2a.test/rpc"
        assert info.streaming is True
        assert len(info.skills) == 2

    def test_missing_name_or_url_rejected(self):
        with pytest.raises(A2aError):
            parse_agent_card({"url": "http://x"})
        with pytest.raises(A2aError):
            parse_agent_card({"name": "x"})

    def test_optional_fields_default(self):
        info = parse_agent_card({"name": "x", "url": "http://x"})
        assert info.skills == []
        assert info.streaming is False


class TestFetchAgentCard:
    def test_new_path_first(self):
        seen = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(str(request.url))
            return httpx.Response(200, json=CARD)

        info = run(fetch_agent_card("http://a2a.test", client=mock_client(handler)))
        assert info.name == "Research Assistant"
        assert seen == ["http://a2a.test/.well-known/agent-card.json"]

    def test_fallback_to_legacy_agent_json(self):
        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path.endswith("agent-card.json"):
                return httpx.Response(404)
            return httpx.Response(200, json=CARD)

        info = run(fetch_agent_card("http://a2a.test", client=mock_client(handler)))
        assert info.url == "http://a2a.test/rpc"

    def test_both_404_raises_card_not_found(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(404)

        with pytest.raises(A2aCardNotFound):
            run(fetch_agent_card("http://a2a.test", client=mock_client(handler)))

    def test_connect_error_raises_a2a_error(self):
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("refused", request=request)

        with pytest.raises(A2aError):
            run(fetch_agent_card("http://a2a.test", client=mock_client(handler)))

    def test_timeout_raises_a2a_error(self):
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ReadTimeout("slow", request=request)

        with pytest.raises(A2aError, match="超时"):
            run(fetch_agent_card("http://a2a.test", client=mock_client(handler)))


# ── AgentSkill → AIP skill（弱契约） ─────────────────────────────────────


class TestSkillToAip:
    def test_mapping_and_weak_contract(self):
        skill = CARD["skills"][0]
        out = skill_to_aip("research-assistant", skill)
        assert out["id"] == "research-assistant__summarize"
        assert out["name"] == "总结"
        # 弱契约如实声明：有【输入要求】段，且不编造参数表
        assert "【输入要求】" in out["description"]
        assert "自行解释" in out["description"]
        assert "必填" not in out["description"]
        # description 只取首段
        assert "第二段不应出现" not in out["description"]
        assert out["tags"] == ["research-assistant", "A2A技能", "nlp"]
        assert out["examples"] == ["请总结这篇文章"]

    def test_missing_skill_id_rejected(self):
        with pytest.raises(A2aError):
            skill_to_aip("ns", {"name": "no-id"})

    def test_skills_to_aip_bulk(self):
        outs = skills_to_aip("ns", CARD["skills"])
        assert [o["id"] for o in outs] == ["ns__summarize", "ns__translate"]


# ── tasks/send 请求构造 ───────────────────────────────────────────────────


class TestBuildTasksSendPayload:
    def test_shape_and_text_contract(self):
        p = build_tasks_send_payload("summarize", {"text": "hello"}, task_id="t1", session_id="s1")
        assert p["jsonrpc"] == "2.0"
        assert p["method"] == "tasks/send"
        assert p["params"]["id"] == "t1"
        assert p["params"]["sessionId"] == "s1"
        part = p["params"]["message"]["parts"][0]
        assert part["type"] == "text"
        assert json.loads(part["text"]) == {"tool": "summarize", "arguments": {"text": "hello"}}

    def test_none_arguments_defaults_to_empty_object(self):
        p = build_tasks_send_payload("x", None)
        assert json.loads(p["params"]["message"]["parts"][0]["text"])["arguments"] == {}


# ── Part 解析与 Task 状态映射 ─────────────────────────────────────────────


class TestPartsToText:
    def test_three_part_types_in_order(self):
        parts = [
            {"type": "text", "text": "第一段"},
            {"kind": "data", "data": {"k": "值"}},  # 旧 DataPart 键名
            {"type": "json", "json": [1, 2]},  # 新 JsonPart 键名
            {"type": "file", "file": {"name": "a.pdf"}},
            {"type": "future", "x": 1},  # 未知类型宽容跳过
        ]
        out = parts_to_text(parts)
        assert out == "第一段\n{\"k\": \"值\"}\n[1, 2]\n[文件 a.pdf]"


def _task(state: str, **kw) -> dict:
    return {"id": "t1", "status": {"state": state}, **kw}


class TestTaskToCallResult:
    def test_completed_merges_artifacts_in_order(self):
        task = _task(
            "completed",
            artifacts=[
                {"name": "r1", "parts": [{"type": "text", "text": "A"}, {"type": "text", "text": "B"}]},
                {"name": "r2", "parts": [{"kind": "data", "data": {"n": 1}}]},
            ],
        )
        r = task_to_call_result(task)
        assert (r.aip_state, r.a2a_state) == ("completed", "completed")
        assert r.text == "A\nB\n{\"n\": 1}"

    def test_input_required_maps_awaiting_completion_with_message(self):
        task = _task(
            "input-required",
            status={"state": "input-required", "message": {"role": "agent", "parts": [{"type": "text", "text": "请补充年份"}]}},
        )
        r = task_to_call_result(task)
        assert r.aip_state == "awaiting-completion"
        assert r.text == "请补充年份"

    def test_working_maps_working(self):
        assert task_to_call_result(_task("working")).aip_state == "working"
        assert task_to_call_result(_task("submitted")).aip_state == "working"

    def test_canceled_maps_failed_with_label(self):
        r = task_to_call_result(_task("canceled"))
        assert r.aip_state == "failed"
        assert r.text.startswith("上游取消")

    def test_failed_uses_status_message(self):
        task = _task("failed", status={"state": "failed", "message": {"parts": [{"type": "text", "text": "炸了"}]}})
        assert task_to_call_result(task).text == "炸了"

    def test_unknown_and_unlisted_states_fail(self):
        assert task_to_call_result(_task("unknown")).aip_state == "failed"
        r = task_to_call_result(_task("wat"))
        assert r.aip_state == "failed"
        assert "wat" in r.text


# ── A2aClient（MockTransport 全链路） ────────────────────────────────────


class TestA2aClient:
    def test_roundtrip_completed(self):
        captured = {}

        def handler(request: httpx.Request) -> httpx.Response:
            captured["url"] = str(request.url)
            captured["body"] = json.loads(request.content)
            return httpx.Response(200, json={"jsonrpc": "2.0", "id": "x", "result": _task(
                "completed", artifacts=[{"parts": [{"type": "text", "text": "结果"}]}]
            )})

        async def go():
            async with A2aClient(parse_agent_card(CARD), client=mock_client(handler)) as cli:
                return await cli.call_skill("summarize", {"text": "hi"})

        r = run(go())
        assert captured["url"] == "http://a2a.test/rpc"
        body = captured["body"]
        assert body["method"] == "tasks/send"
        assert json.loads(body["params"]["message"]["parts"][0]["text"]) == {
            "tool": "summarize", "arguments": {"text": "hi"},
        }
        assert isinstance(r, A2aCallResult)
        assert (r.aip_state, r.text) == ("completed", "结果")

    def test_jsonrpc_error_object_maps_failed(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"jsonrpc": "2.0", "id": "x", "error": {"code": -32000, "message": "技能不存在"}})

        async def go():
            async with A2aClient(parse_agent_card(CARD), client=mock_client(handler)) as cli:
                return await cli.call_skill("nope")

        r = run(go())
        assert r.aip_state == "failed"
        assert "-32000" in r.text and "技能不存在" in r.text

    def test_timeout_returns_failed_not_raise(self):
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ReadTimeout("slow", request=request)

        async def go():
            async with A2aClient(parse_agent_card(CARD), client=mock_client(handler)) as cli:
                return await cli.call_skill("summarize")

        r = run(go())  # §1.3：超时 → failed（不撤架）
        assert r.aip_state == "failed" and "超时" in r.text

    def test_connect_error_raises_for_takedown(self):
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("refused", request=request)

        async def go():
            async with A2aClient(parse_agent_card(CARD), client=mock_client(handler)) as cli:
                await cli.call_skill("summarize")

        with pytest.raises(A2aError):  # §1.3：连接失败 → 抛出，探活判负撤架
            run(go())

    def test_http_500_raises_for_takedown(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(500)

        async def go():
            async with A2aClient(parse_agent_card(CARD), client=mock_client(handler)) as cli:
                await cli.call_skill("summarize")

        with pytest.raises(A2aError, match="500"):
            run(go())

    def test_non_json_body_raises(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, text="not json")

        async def go():
            async with A2aClient(parse_agent_card(CARD), client=mock_client(handler)) as cli:
                await cli.call_skill("summarize")

        with pytest.raises(A2aError, match="JSON"):
            run(go())
