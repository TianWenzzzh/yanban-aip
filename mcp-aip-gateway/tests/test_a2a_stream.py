"""流式（message/stream + tasks/resubscribe + /tools/search）离线单元测试（W5，§2.4）。"""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from gateway.a2a_server import create_a2a_app


class FakeRegistry:
    def __init__(self):
        self.tool = type("T", (), {"name": "echo"})()
        self.state = type("S", (), {"spec": type("P", (), {"ns": "x"})()})()

    def resolve(self, skill_id):
        return (self.state, self.tool) if skill_id in ("x__echo", "echo") else None

    async def call(self, ns, tool_name, arguments):
        return type("R", (), {"content": [type("C", (), {"text": "pong"})()]})()

    def all_tools(self):
        return {"x__echo": (self.state, self.tool)}

    def build_acs(self):
        return {"aic": "AIC.T", "name": "g", "version": "1", "skills": [
            {"id": "x__echo", "name": "echo", "description": "回声测试", "tags": [], "examples": []}]}


@pytest.fixture()
def client():
    app = create_a2a_app(FakeRegistry(), self_url="http://test/")
    return TestClient(app)


def _send(client, method="tasks/send", task_id="ts-1"):
    return client.post("/", json={"jsonrpc": "2.0", "id": "1", "method": method,
                                  "params": {"id": task_id, "message": {"parts": [
                                      {"type": "text", "text": '{"tool": "x__echo"}'}]}}})


def test_stream_sse_frames_final_true(client):
    r = _send(client, method="message/stream")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/event-stream")
    frames = [json.loads(line[6:]) for line in r.text.splitlines() if line.startswith("data: ")]
    assert frames[0]["status"]["state"] == "working" and frames[0]["final"] is False
    assert frames[0]["kind"] == "status-update"
    assert any(f["kind"] == "artifact-update" and "pong" in json.dumps(f) for f in frames)
    assert frames[-1]["status"]["state"] == "completed" and frames[-1]["final"] is True


def test_stream_v02_alias_send_subscribe(client):
    r = _send(client, method="tasks/sendSubscribe")
    assert "text/event-stream" in r.headers["content-type"]


def test_stream_stores_task_then_resubscribe(client):
    _send(client, method="tasks/send", task_id="ts-res")
    r = client.post("/", json={"jsonrpc": "2.0", "id": "2", "method": "tasks/resubscribe",
                               "params": {"id": "ts-res"}})
    frames = r.json()["result"]["frames"]
    assert frames[0]["status"]["state"] == "completed"
    assert frames[-1]["final"] is True
    assert "pong" in json.dumps(frames)


def test_resubscribe_unknown_32001(client):
    r = client.post("/", json={"jsonrpc": "2.0", "id": "3", "method": "tasks/resubscribe",
                               "params": {"id": "ghost"}})
    assert r.json()["error"]["code"] == -32001


def test_stream_missing_message_32602(client):
    r = client.post("/", json={"jsonrpc": "2.0", "id": "4", "method": "message/stream", "params": {}})
    assert r.json()["error"]["code"] == -32602


def test_card_declares_streaming(client):
    card = client.get("/.well-known/agent-card.json").json()
    assert card["capabilities"]["streaming"] is True


def test_tools_search_keyword(client):
    r = client.get("/tools/search", params={"q": "echo", "limit": 5})
    results = r.json()["results"]
    assert results and results[0]["id"] == "x__echo"
    empty = client.get("/tools/search", params={"q": "完全无关词"}).json()
    assert empty["results"] == []
