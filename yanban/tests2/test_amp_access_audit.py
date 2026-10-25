"""AMP access 审计（轮 181c）：**被调用必须留下一条数得出来的记录**。

起因是 2026-09-30 的只读体检：注册在梧桐上的「研伴·文献检索智能体」的 /rpc 走的是 SDK 裸路由
`add_aip_rpc_router`（函数体里没有任何 AccessEmitter/emit），容器内打一次 /rpc，
`amp_access_*.jsonl` 增量 **0** ⇒ 别人调用它，我们这边一条记录都没有。
官方那句"非平台上注册的智能体无法形成可对接的调用记录"反过来说就是：**没有记录就没有分数**。
"""
from __future__ import annotations

import importlib
import json
import sys
import uuid
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import partner_base  # noqa: E402
from partner_base import _caller_of, _corr_of  # noqa: E402

CALLEE_AIC = "1.2.156.3088.1.1.PTN01.PTN01.1.0001"
CALLER_AIC = "1.2.156.3088.1.1.LDR001.LDR001.1.0002"


async def _echo_skill(text: str) -> str:
    return f"echo:{text[:40]}"


def _body(sender: str, command: str = "start") -> dict:
    return {"jsonrpc": "2.0", "id": "r1", "method": "rpc",
            "params": {"command": {"type": "task-command", "id": "c1",
                                   "taskId": str(uuid.uuid4()),
                                   "sentAt": "2026-09-30T12:00:00Z", "senderRole": "leader",
                                   "senderId": sender, "command": command,
                                   "dataItems": [{"id": "d1", "type": "text", "text": "查一下多模态钓鱼检测"}]}}}


def _records(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


@pytest.fixture()
def partner_client(tmp_path, monkeypatch):
    monkeypatch.setattr(partner_base, "AMP_LOG_DIR", tmp_path)
    app = partner_base.create_partner_app(name="ptn", aic=CALLEE_AIC, port=8099, run_skill=_echo_skill)
    return TestClient(app), tmp_path / "amp_access_ptn.jsonl"


def test_one_inbound_call_writes_exactly_one_access_record(partner_client):
    """这条就是"能不能数出来"的正身：调一次 ⇒ 文件里多一条，且 callee 是我们。"""
    client, log = partner_client
    assert len(_records(log)) == 0
    r = client.post("/rpc", json=_body(CALLER_AIC))
    assert r.status_code == 200, r.text
    recs = _records(log)
    assert len(recs) == 1, f"调用一次之后应有 1 条 access 记录，实得 {len(recs)}"
    assert recs[0]["body"]["callee"]["aic"] == CALLEE_AIC


def test_access_record_names_the_real_caller_not_us(partner_client):
    """调用方写死成本队 leader 的话，日志就答不出"被**别的智能体**调用了几次"。"""
    client, log = partner_client
    client.post("/rpc", json=_body(CALLER_AIC))
    assert _records(log)[-1]["body"]["caller"]["aic"] == CALLER_AIC


def test_literature_partner_is_no_longer_on_the_bare_sdk_route(tmp_path, monkeypatch):
    """注册在梧桐上的就是这一个：它的 /rpc 必须自己留记录（走裸路由时这里恒 0 条）。"""
    monkeypatch.setenv("AMP_LOG_DIR", str(tmp_path))
    monkeypatch.setenv("LLM_MOCK", "1")
    lit = importlib.import_module("literature_partner")
    TestClient(lit.app).post("/rpc", json=_body(CALLER_AIC))
    log = Path(tmp_path) / f"amp_access_{lit.PARTNER_NAME}.jsonl"
    assert log.exists(), "研伴·文献检索智能体的 /rpc 仍然不写 access 日志（那被调用就数不到）"
    assert _records(log)[-1]["body"]["callee"]["aic"] == lit.PARTNER_AIC


def test_a_failing_call_still_leaves_a_record(partner_client, monkeypatch):
    """处理里抛异常时，finally 要读的那个状态码以前没初始化 ⇒ UnboundLocalError 被 suppress 咽掉，
    失败的调用一条都不留。现在先兜底 500：调了没成功也要数得出来（"健壮性"本身就是排名口径之一）。

    🔴 轮 306 改了这条的**出口形状**：以前异常直接穿到 FastAPI ⇒ 调用方收到 HTTP 500 崩栈
    （外部实测：缺 taskId 的 start 会撞进 SDK 自己的兜底再抛一次）。现在回 JSON-RPC 错误体，
    计数口径不变（statusCode 仍是 500）。
    """
    client, log = partner_client

    async def boom(*a, **kw):
        raise RuntimeError("通道炸了")

    monkeypatch.setattr(partner_base, "handle_rpc_request", boom)
    r = client.post("/rpc", json=_body(CALLER_AIC))
    assert r.status_code == 200, f"内部异常不该以崩栈面对调用方：{r.text[:160]}"
    assert (r.json().get("error") or {}).get("code") == -32603, r.json()
    recs = _records(log)
    assert len(recs) == 1, f"失败的调用也该留一条记录，实得 {len(recs)}"
    b = recs[0]["body"]
    assert b["response"]["statusCode"] == 500, b["response"]
    assert b["caller"]["aic"] == CALLER_AIC


@pytest.mark.parametrize("raw, want", [
    (json.dumps(_body(CALLER_AIC)).encode(), CALLER_AIC),
    (b"not json", "unknown"),
    (json.dumps({"params": {"command": {"senderId": ""}}}).encode(), "unknown"),
])
def test_caller_extraction_never_invents_a_caller(raw, want):
    assert _caller_of(raw) == want


def test_access_record_carries_correlation_id_the_platform_pairs_on(partner_client):
    """轮 308c：平台按 correlationId 把双侧记录配成"1 次调用"（docs.html「日志上报要求」；
    官方 demo-partner 与 SDK 客户端两侧都填 `sessionId`）。我们的 callee 记录缺这个字段时，
    就算把账本上报到平台也配不上对——"被调用次数"永远数不上去。
    """
    client, log = partner_client
    body = _body(CALLER_AIC)
    body["params"]["command"]["sessionId"] = "sess-r308-count"
    r = client.post("/rpc", json=body)
    assert r.status_code == 200, r.text
    assert _records(log)[-1].get("correlation_id") == "sess-r308-count"


@pytest.mark.parametrize("raw, want", [
    (json.dumps({"params": {"command": {"sessionId": "s1", "taskId": "t1"}}}).encode(), "s1"),
    (json.dumps({"params": {"command": {"taskId": "t1"}}}).encode(), "t1"),
    (json.dumps({"params": {"command": {"sessionId": ""}}}).encode(), None),
    (b"{", None),
])
def test_corr_extraction_prefers_session_then_task(raw, want):
    """口径：sessionId 优先（与官方两侧一致），缺了退 taskId；两个都没有就留空。"""
    assert _corr_of(raw) == want
