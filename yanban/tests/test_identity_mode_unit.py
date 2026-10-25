"""身份绑定模式离线单元测试（v5 T0，R21）：demo 回归钉 + trusted 伪造拒绝。

trusted 接线语义：peer AIC 由 mTLS 中间件落 request.state.peer_aic（生产=证书 CN；
测试=桩中间件按注入的 peer 模拟），handle_rpc_request 校验 payload.senderId == peer AIC，
不匹配 → -32009。
"""

from __future__ import annotations

import sys
import uuid
from pathlib import Path

import pytest
from fastapi import Request
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import identity_mode  # noqa: E402
from partner_base import create_partner_app  # noqa: E402

LEADER_AIC = "1.2.156.3088.1.1.LDR001.LDR001.1.0002"
FORGED_AIC = "1.2.156.3088.1.1.LDR001.LDR001.1.9999"  # 格式合法但身份不同


async def _echo_skill(text: str) -> str:
    return f"echo:{text[:40]}"


def _rpc_body(sender: str, task_id: str) -> dict:
    return {"jsonrpc": "2.0", "id": "r1", "method": "rpc",
            "params": {"command": {"type": "task-command", "id": "c1", "taskId": task_id,
                                   "sentAt": "2026-09-12T12:00:00Z", "senderRole": "leader",
                                   "senderId": sender, "command": "start",
                                   "dataItems": [{"id": "d1", "type": "text", "text": "测试任务"}]}}}


def make_client(peer_aic: str | None, monkeypatch, trusted: bool) -> TestClient:
    monkeypatch.setenv("YANBAN_IDENTITY_MODE", "trusted" if trusted else "demo")
    app = create_partner_app(name="ident-test", aic="1.2.156.3088.1.1.PTN01.PTN01.1.0001",
                             port=8099, run_skill=_echo_skill)

    if trusted:
        # 桩中间件：模拟生产 mTLS 中间件——把 peer 证书解析出的 AIC 落 request.state
        @app.middleware("http")
        async def stub_peer_cert(request: Request, call_next):
            request.state.peer_aic = peer_aic
            return await call_next(request)

    return TestClient(app)


def test_demo_mode_forged_sender_still_accepted(monkeypatch):
    """R21 回归钉：demo 模式（默认）不校验身份——伪造 senderId 也通过，行为与 v4 一致。"""
    client = make_client(peer_aic=FORGED_AIC, monkeypatch=monkeypatch, trusted=False)
    r = client.post("/rpc", json=_rpc_body(FORGED_AIC, f"t-{uuid.uuid4().hex[:6]}"))
    assert r.status_code == 200
    assert r.json().get("error") is None


def test_trusted_mode_matching_sender_passes(monkeypatch):
    client = make_client(peer_aic=LEADER_AIC, monkeypatch=monkeypatch, trusted=True)
    r = client.post("/rpc", json=_rpc_body(LEADER_AIC, f"t-{uuid.uuid4().hex[:6]}"))
    assert r.status_code == 200
    assert r.json().get("error") is None


def test_trusted_mode_forged_sender_rejected_32009(monkeypatch):
    """核心验收：trusted 下伪造 senderId（与证书 peer AIC 不符）→ -32009 拒绝。"""
    client = make_client(peer_aic=LEADER_AIC, monkeypatch=monkeypatch, trusted=True)
    r = client.post("/rpc", json=_rpc_body(FORGED_AIC, f"t-{uuid.uuid4().hex[:6]}"))
    assert r.status_code == 200  # 协议层 200，错误在 JSON-RPC error 对象
    err = r.json().get("error") or {}
    assert err.get("code") == -32009, f"期望 -32009，实得 {err}"


def test_trusted_mode_missing_peer_cert_rejected_32008(monkeypatch):
    """trusted 下无证书 → 认证缺失 -32008（官方映射：PeerAicMissing/InvalidCert→-32008，
    SenderMismatch→-32009，认证/授权语义分离，aip_identity.py:273-279）。"""
    client = make_client(peer_aic=None, monkeypatch=monkeypatch, trusted=True)
    r = client.post("/rpc", json=_rpc_body(LEADER_AIC, f"t-{uuid.uuid4().hex[:6]}"))
    err = (r.json() or {}).get("error") or {}
    assert err.get("code") == -32008
    assert err.get("message") == "AuthenticationRequiredError"


def test_mode_resolution(monkeypatch):
    monkeypatch.delenv("YANBAN_IDENTITY_MODE", raising=False)
    assert identity_mode.identity_mode() == "demo" and not identity_mode.identity_binding_enabled()
    monkeypatch.setenv("YANBAN_IDENTITY_MODE", "trusted")
    assert identity_mode.identity_binding_enabled() is True
    monkeypatch.setenv("YANBAN_IDENTITY_MODE", "garbage")
    assert identity_mode.identity_mode() == "demo"  # 非法值回退 demo（兜底）
