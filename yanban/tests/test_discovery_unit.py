"""梧桐 Discovery 客户端与 Leader 三层解析离线单元测试（W1，无网络）。

覆盖：ADP 请求契约 / 响应宽容解析（acs_map→endPoint）/ 错误降级语义 /
Orchestrator 三层端点解析（discovery→fallback static / 纯静态模式回归 R18）。
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import discovery as disc  # noqa: E402
from discovery import DiscoveryCandidate, DiscoveryError, discover, match_score  # noqa: E402
from orchestrator import Orchestrator  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]


def run(coro):
    return asyncio.run(coro)


def adp_response() -> dict:
    """按官方 SDK 模型形状构造的最小合法响应。"""
    return {
        "result": {
            "acs_map": {
                "1.2.156.3088.1.X.AG01.1.0001": {
                    "name": "远程文献 Agent",
                    "description": "学术文献检索与综述",
                    "endPoints": [{"url": "http://10.0.0.9:8031/rpc", "transport": "JSONRPC"}],
                    "skills": [{"id": "literature_search"}],
                },
            },
            "agents": [
                {"group": "1.2.156.3088.1.X.AG01.1.0001",
                 "agent_skills": [
                     {"aic": "1.2.156.3088.1.X.AG01.1.0001", "skill_id": "literature_search",
                      "ranking": 2, "memo": "检索学术文献与综述"},
                 ]},
            ],
        }
    }


# ── discovery.py 契约级 ──────────────────────────────────────────────────

def test_discover_request_matches_adp_contract():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["body"] = httpx.Response(200, json=adp_response()) and __import__("json").loads(request.content)
        return httpx.Response(200, json=adp_response())

    async def go():
        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(transport=transport) as client:
            return await discover("http://disc.test", "检索多智能体文献", limit=3, client=client)

    cands = run(go())
    assert seen["url"].endswith("/acps-adp-v2/discover")
    assert seen["body"]["type"] == "explicit"
    assert seen["body"]["query"] == "检索多智能体文献"
    assert seen["body"]["limit"] == 3
    assert cands and cands[0].endpoint == "http://10.0.0.9:8031/rpc"
    assert cands[0].skill_id == "literature_search" and cands[0].aic.startswith("1.2.156.")


def test_discover_lenient_missing_fields():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"result": {"agents": [{"group": "g", "agent_skills": [
            {"skill_id": "s1"}, {"aic": "aic-2"}, "垃圾元素"
        ]}]}})  # 无 acs_map、缺字段、混入非 dict

    async def go():
        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(transport=transport) as client:
            return await discover("http://disc.test", "q", client=client)

    cands = run(go())
    assert len(cands) == 2  # 宽容解析：缺字段不炸，非 dict 元素跳过
    assert all(c.endpoint == "" for c in cands)


def test_discover_protocol_error_raises():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"error": {"code": 404, "message": "no agent"}})

    async def go():
        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(transport=transport) as client:
            return await discover("http://disc.test", "q", client=client)

    with pytest.raises(DiscoveryError):
        run(go())


def test_discover_http_error_and_timeout():
    def handler_500(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500)

    def handler_timeout(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectTimeout("boom")

    async def go(handler):
        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(transport=transport) as client:
            return await discover("http://disc.test", "q", client=client)

    with pytest.raises(DiscoveryError):
        run(go(handler_500))
    with pytest.raises(DiscoveryError):
        run(go(handler_timeout))


def test_match_score_and_ranking():
    a = DiscoveryCandidate(skill_memo="检索学术文献与综述", ranking=1)
    b = DiscoveryCandidate(skill_memo="生成随机数", ranking=9)
    ranked = disc.rank_candidates([b, a], "帮我检索文献")
    assert ranked[0] is a  # 关键词重叠优先于 ranking


# ── Orchestrator 三层解析 ────────────────────────────────────────────────

@pytest.fixture()
def orch(monkeypatch):
    monkeypatch.delenv("DISCOVERY_URL", raising=False)
    return Orchestrator(ROOT / "partners.toml")


def test_static_mode_when_discovery_unset(orch):
    """R18：DISCOVERY_URL 为空 = 纯静态，行为与改造前一致，且不发起发现。"""
    called = {"n": 0}

    async def fake_discover(url, query, **kw):
        called["n"] += 1
        raise DiscoveryError("不应被调用")

    orch._discover_fn = fake_discover
    endpoint, source = run(orch._resolve_endpoint("检索文献", "literature"))
    assert source == "static" and endpoint == "http://localhost:8031/rpc"
    assert called["n"] == 0


def test_discovery_mode_uses_dynamic_endpoint(orch, monkeypatch):
    monkeypatch.setenv("DISCOVERY_URL", "http://disc.test")
    orch.discovery_url = "http://disc.test"

    async def fake_discover(url, query, **kw):
        return [DiscoveryCandidate(
            aic="aic-x", skill_id="literature_search", skill_memo="检索学术文献与综述",
            endpoint="http://10.0.0.9:8031/rpc", agent_name="远程文献 Agent")]

    orch._discover_fn = fake_discover
    endpoint, source = run(orch._resolve_endpoint("检索大模型文献 3 篇", "literature"))
    assert source == "discovery" and endpoint == "http://10.0.0.9:8031/rpc"


def test_discovery_down_falls_back_static(orch, monkeypatch):
    monkeypatch.setenv("DISCOVERY_URL", "http://disc.test")
    orch.discovery_url = "http://disc.test"

    async def fake_discover(url, query, **kw):
        raise DiscoveryError("服务挂了")

    orch._discover_fn = fake_discover
    endpoint, source = run(orch._resolve_endpoint("检索文献", "literature"))
    assert source == "static" and endpoint == "http://localhost:8031/rpc"


def test_discovery_single_token_mismatch_falls_back(orch, monkeypatch):
    """T2 联测教训：候选与子任务仅 1 个通用词重叠（如"智能体"）→ 不足阈值回落静态。"""
    monkeypatch.setenv("DISCOVERY_URL", "http://disc.test")
    orch.discovery_url = "http://disc.test"

    async def fake_discover(url, query, **kw):
        from discovery import DiscoveryCandidate
        return [DiscoveryCandidate(skill_memo="北京美食推荐智能体服务",
                                   endpoint="http://10.0.0.99/rpc", ranking=9)]

    orch._discover_fn = fake_discover
    endpoint, source = orch_run(orch, "检索大模型多智能体文献 3 篇")
    assert source == "static" and endpoint == "http://localhost:8031/rpc"


def orch_run(orch, text):
    import asyncio
    return asyncio.run(orch._resolve_endpoint(text, "literature"))


def test_discovery_zero_score_falls_back_static(orch, monkeypatch):
    """候选与子任务毫无关键词重叠 → 视为不相关，回落静态（防错配）。"""
    monkeypatch.setenv("DISCOVERY_URL", "http://disc.test")
    orch.discovery_url = "http://disc.test"

    async def fake_discover(url, query, **kw):
        return [DiscoveryCandidate(skill_memo="生成随机数", endpoint="http://10.0.0.99/rpc", ranking=9)]

    orch._discover_fn = fake_discover
    endpoint, source = run(orch._resolve_endpoint("检索量子计算文献 3 篇", "literature"))
    assert source == "static" and endpoint == "http://localhost:8031/rpc"
