"""tasks/get、tasks/cancel 离线单元测试（§2.3；R12 规范依据见 design_wanbridge_a2a.md）。

覆盖：send 落表 / get 往返与 -32001 / cancel 终态 -32002 与非终态取消 /
params 校验 -32602 / 方法名分发回归 / 客户端 get_task、cancel_task 与 A2aRpcError 透传。
"""

from __future__ import annotations

import asyncio
import json

import httpx
import pytest

from gateway.a2a_server import (
    A2A_ERR_TASK_NOT_CANCELABLE,
    A2A_ERR_TASK_NOT_FOUND,
    handle_jsonrpc,
    handle_tasks_cancel,
    handle_tasks_get,
    handle_tasks_send,
)
from gateway.bridge_a2a import A2aClient, A2aError, A2aRpcError, parse_agent_card


def run(coro):
    return asyncio.run(coro)


class FakeRegistry:
    """最小 registry 桩：一个在线工具 echo（x__echo）。"""

    def __init__(self):
        self.tool = type("T", (), {"name": "echo"})()
        self.state = type("S", (), {"spec": type("P", (), {"ns": "x"})()})()

    def resolve(self, skill_id):
        return (self.state, self.tool) if skill_id in ("x__echo", "echo") else None

    async def call(self, ns, tool_name, arguments):
        return type("R", (), {"content": [type("C", (), {"text": "pong"})()]})()

    def all_tools(self):
        return {"x__echo": (self.state, self.tool)}


def send_payload(task_id="t-1"):
    return {"jsonrpc": "2.0", "id": "r1", "method": "tasks/send",
            "params": {"id": task_id, "message": {"parts": [{"type": "text", "text": '{"tool": "x__echo"}'}]}}}


def make_store() -> dict:
    reg = FakeRegistry()
    store: dict = {}
    run(handle_jsonrpc(send_payload(), resolve=reg.resolve, call=reg.call,
                       available_ids=reg.all_tools, store=store))
    return store


# ── 服务端 ───────────────────────────────────────────────────────────────

def test_send_records_task_in_store():
    store = make_store()
    assert set(store) == {"t-1"}
    assert store["t-1"]["status"]["state"] == "completed"
    assert store["t-1"]["artifacts"][0]["parts"][0]["text"] == "pong"


def test_tasks_get_round_trip():
    store = make_store()
    reg = FakeRegistry()
    resp = run(handle_jsonrpc({"jsonrpc": "2.0", "id": 2, "method": "tasks/get",
                               "params": {"id": "t-1"}},
                              resolve=reg.resolve, call=reg.call,
                              available_ids=reg.all_tools, store=store))
    assert resp["result"]["id"] == "t-1"
    assert resp["result"]["status"]["state"] == "completed"


def test_tasks_get_unknown_id_32001():
    store = make_store()
    reg = FakeRegistry()
    resp = run(handle_jsonrpc({"jsonrpc": "2.0", "id": 3, "method": "tasks/get",
                               "params": {"id": "nope"}},
                              resolve=reg.resolve, call=reg.call,
                              available_ids=reg.all_tools, store=store))
    assert resp["error"]["code"] == A2A_ERR_TASK_NOT_FOUND == -32001
    assert resp["error"]["message"] == "Task not found"


def test_tasks_get_missing_id_32602():
    store = make_store()
    reg = FakeRegistry()
    resp = run(handle_jsonrpc({"jsonrpc": "2.0", "id": 4, "method": "tasks/get", "params": {}},
                              resolve=reg.resolve, call=reg.call,
                              available_ids=reg.all_tools, store=store))
    assert resp["error"]["code"] == -32602


def test_tasks_cancel_terminal_32002():
    store = make_store()  # send 同步完成 → completed（终态）
    reg = FakeRegistry()
    resp = run(handle_jsonrpc({"jsonrpc": "2.0", "id": 5, "method": "tasks/cancel",
                               "params": {"id": "t-1"}},
                              resolve=reg.resolve, call=reg.call,
                              available_ids=reg.all_tools, store=store))
    assert resp["error"]["code"] == A2A_ERR_TASK_NOT_CANCELABLE == -32002
    assert "completed" in resp["error"]["message"]


def test_tasks_cancel_non_terminal_sets_canceled():
    store = {"t-w": {"id": "t-w", "sessionId": "s", "status": {"state": "working"}}}
    task = handle_tasks_get  # noqa: F841 — 占位可读性
    out = handle_tasks_cancel({"id": "t-w"}, store=store)
    assert out["status"]["state"] == "canceled"
    assert store["t-w"]["status"]["state"] == "canceled"


def test_tasks_cancel_unknown_32001():
    store = make_store()
    reg = FakeRegistry()
    resp = run(handle_jsonrpc({"jsonrpc": "2.0", "id": 6, "method": "tasks/cancel",
                               "params": {"id": "ghost"}},
                              resolve=reg.resolve, call=reg.call,
                              available_ids=reg.all_tools, store=store))
    assert resp["error"]["code"] == -32001


def test_failed_send_also_retrievable():
    """failed 任务同样落表（§2.3：send 执行结果含 failed 全部记录）。"""
    reg = FakeRegistry()
    store: dict = {}
    payload = {"jsonrpc": "2.0", "id": "r2", "method": "tasks/send",
               "params": {"id": "t-bad", "message": {"parts": [{"type": "text", "text": '{"tool": "ghost"}'}]}}}
    run(handle_jsonrpc(payload, resolve=reg.resolve, call=reg.call,
                       available_ids=reg.all_tools, store=store))
    assert store["t-bad"]["status"]["state"] == "failed"
    got = handle_tasks_get({"id": "t-bad"}, store=store)
    assert got["id"] == "t-bad"


def test_unknown_method_regression_32601():
    store = make_store()
    reg = FakeRegistry()
    resp = run(handle_jsonrpc({"jsonrpc": "2.0", "id": 9, "method": "tasks/subscribe", "params": {}},
                              resolve=reg.resolve, call=reg.call,
                              available_ids=reg.all_tools, store=store))
    assert resp["error"]["code"] == -32601


# ── 客户端（MockTransport 桩）────────────────────────────────────────────

def make_client(handler) -> A2aClient:
    card = parse_agent_card({"name": "Remote", "url": "http://a2a.test/rpc", "skills": []})
    transport = httpx.MockTransport(handler)
    return A2aClient(card, client=httpx.AsyncClient(transport=transport))


def test_client_get_task_round_trip():
    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        assert body["method"] == "tasks/get" and body["params"] == {"id": "t-1"}
        return httpx.Response(200, json={"jsonrpc": "2.0", "id": body["id"],
                                         "result": {"id": "t-1", "status": {"state": "completed"}}})

    client = make_client(handler)
    task = run(client.get_task("t-1"))
    assert task["id"] == "t-1" and task["status"]["state"] == "completed"


def test_client_cancel_round_trip():
    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        assert body["method"] == "tasks/cancel" and body["params"] == {"id": "t-w"}
        return httpx.Response(200, json={"jsonrpc": "2.0", "id": body["id"],
                                         "result": {"id": "t-w", "status": {"state": "canceled"}}})

    client = make_client(handler)
    task = run(client.cancel_task("t-w"))
    assert task["status"]["state"] == "canceled"


def test_client_rpc_error_raises_with_code():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"jsonrpc": "2.0", "id": "x",
                                         "error": {"code": -32001, "message": "Task not found"}})

    client = make_client(handler)
    with pytest.raises(A2aRpcError) as ei:
        run(client.get_task("ghost"))
    assert ei.value.code == -32001 and ei.value.message == "Task not found"
    assert isinstance(ei.value, A2aError)  # 仍是 A2aError 子类，调用方可统一捕获


def test_client_transport_error_still_a2a_error():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("boom")

    client = make_client(handler)
    with pytest.raises(A2aError) as ei:
        run(client.cancel_task("t-1"))
    assert not isinstance(ei.value, A2aRpcError)  # 传输错误不带协议码
