"""双面同进程（gateway/app.py）+ A2A 面 AMP 审计离线单元测试（N4，无网络）。

覆盖：AIP /rpc 与 A2A / 两面共存互不遮蔽 / Card 双路径同卡 /
A2A 面 AccessEmitter NDJSON 落盘（tmp_path 注入）/ tasks/get 经双面可检索。
"""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import pytest
from acps_sdk.amp import AccessEmitter
from fastapi.testclient import TestClient

import gateway.partner as partner
from gateway.app import create_app


def run(coro):
    return asyncio.run(coro)


class FakeRegistry:
    """最小 registry 桩：partner 侧 handlers 与 A2A 面共用。"""

    def __init__(self):
        self.tool = type("T", (), {"name": "echo"})()
        self.state = type("S", (), {"spec": type("P", (), {"ns": "x"})()})()
        self.states = {"x": self.state}
        self.started = False

    async def start(self):
        self.started = True
        self.state.online = True
        self.state.tools = {"echo": self.tool}

    async def stop(self):
        self.started = False

    def resolve(self, skill_id):
        return (self.state, self.tool) if skill_id in ("x__echo", "echo") else None

    async def call(self, ns, tool_name, arguments):
        return type("R", (), {"content": [type("C", (), {"text": "pong"})()]})()

    def all_tools(self):
        return {"x__echo": (self.state, self.tool)}

    def build_acs(self):
        return {
            "aic": "AIC.TEST", "name": "gateway", "version": "1.0.0",
            "skills": [{"id": "x__echo", "name": "echo", "description": "回声",
                        "tags": ["x", "MCP工具"], "examples": []}],
        }


WELL_KNOWN_PATHS = {"/.well-known/agent-card.json", "/.well-known/agent.json"}


@pytest.fixture()
def dual_client(tmp_path, monkeypatch):
    fake = FakeRegistry()
    monkeypatch.setattr(partner, "registry", fake)
    # 拆除上次测试挂的 A2A 路由与幂等标记（AIP 的 /rpc、/health、/acs 保留）
    partner.app.router.routes = [
        r for r in partner.app.router.routes
        if getattr(r, "path", "") not in WELL_KNOWN_PATHS | {"/"}
    ]
    if hasattr(partner.app.state, "_wanbridge_a2a_wired"):
        del partner.app.state._wanbridge_a2a_wired
    emitter = AccessEmitter(tmp_path / "amp_access_a2a-8060.jsonl", aic="AIC.TEST")
    app = create_app(a2a_port=8060, access_emitter=emitter)
    # create_app 内部经 partner.registry 模块属性引用（已被 monkeypatch 替换）；
    # lifespan 同样按模块全局名解析——TestClient 触发时用的也是 fake
    with TestClient(app) as client:
        yield client, fake, tmp_path


def test_a2a_surface_alive_and_card(dual_client):
    client, fake, tmp_path = dual_client
    card_new = client.get("/.well-known/agent-card.json")
    card_old = client.get("/.well-known/agent.json")
    assert card_new.status_code == 200 and card_old.status_code == 200
    assert card_new.json() == card_old.json()  # 双路径同卡
    assert card_new.json()["metadata"]["aic"] == "AIC.TEST"
    assert any(s["id"] == "echo" for s in card_new.json()["skills"])


def test_both_surfaces_coexist(dual_client):
    """POST /（A2A）与 POST /rpc（AIP）同 app 共存：同一能力，两种协议。"""
    client, fake, tmp_path = dual_client
    a2a = client.post("/", json={"jsonrpc": "2.0", "id": "a1", "method": "tasks/send",
                                 "params": {"id": "ta", "message": {"parts": [{"type": "text", "text": '{"tool": "x__echo"}'}]}}})
    assert a2a.status_code == 200
    body = a2a.json()
    assert body["result"]["status"]["state"] == "completed"
    assert body["result"]["artifacts"][0]["parts"][0]["text"] == "pong"

    aip = client.post("/rpc", json={"jsonrpc": "2.0", "id": "r1", "method": "rpc",
                                    "params": {"command": {"type": "task-command", "id": "c1",
                                                           "taskId": "task-dual-1",
                                                           "sentAt": "2026-09-12T02:00:00Z",
                                                           "senderRole": "leader", "senderId": "test",
                                                           "command": "start",
                                                           "dataItems": [{"id": "d1", "type": "text",
                                                                          "text": '{"tool": "x__echo", "arguments": {}}'}]}}})
    assert aip.status_code == 200
    r = aip.json()
    # AIP 面返回 RpcResponse（TaskResult 内嵌），而非 A2A Task
    assert r.get("jsonrpc") == "2.0"
    result = r.get("result") or {}
    assert "state" in result or ("result" in r and "artifacts" not in r)


def test_a2a_audit_ndjson_written(dual_client):
    client, fake, tmp_path = dual_client
    client.post("/", json={"jsonrpc": "2.0", "id": "a2", "method": "tasks/send",
                           "params": {"id": "tb", "message": {"parts": [{"type": "data", "data": {"tool": "x__echo"}}]}}})
    log = tmp_path / "amp_access_a2a-8060.jsonl"
    assert log.exists()
    record = json.loads(log.read_text(encoding="utf-8").splitlines()[-1])
    body = record.get("body") or record
    assert body["request"]["route"] == "/"
    assert body["callee"]["serviceName"] == "wanbridge-a2a"
    assert body["response"]["statusCode"] == 200


def test_tasks_get_via_a2a_surface(dual_client):
    client, fake, tmp_path = dual_client
    client.post("/", json={"jsonrpc": "2.0", "id": "a3", "method": "tasks/send",
                           "params": {"id": "t-store", "message": {"parts": [{"type": "text", "text": '{"tool": "x__echo"}'}]}}})
    got = client.post("/", json={"jsonrpc": "2.0", "id": "a4", "method": "tasks/get", "params": {"id": "t-store"}})
    assert got.json()["result"]["id"] == "t-store"


def test_unknown_tool_failed_with_available_list(dual_client):
    client, fake, tmp_path = dual_client
    r = client.post("/", json={"jsonrpc": "2.0", "id": "a5", "method": "tasks/send",
                               "params": {"id": "tc", "message": {"parts": [{"type": "text", "text": '{"tool": "nope"}'}]}}})
    task = r.json()["result"]
    assert task["status"]["state"] == "failed"
    assert "x__echo" in task["status"]["message"]["parts"][0]["text"]
