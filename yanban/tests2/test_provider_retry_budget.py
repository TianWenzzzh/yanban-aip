"""N2：撞限流的源不许在 HTTP 层连敲三包（429 交给熔断，不交给重试循环）。

为什么单独钉这条：OpenAlex / Semantic Scholar 的 429 带**日额度**级 Retry-After，
退避重试不会让它提前放行，只会把当天的额度更早烧完、把源更早关在门外。
实测在场：`g166` 五体检四次外发 >4 包；`g168` 起跑前置 6 包 = crossref 1 + arXiv 1
+ OpenAlex 1 + Semantic Scholar 3（每失败源 1 次首发 + 2 次退避重试）。
熔断本来就按 120s 起翻倍在管"暂时别碰这个源"这件事，`get_json` 的循环再管一遍就是放大。

桩只打在 httpx 传输层（`AsyncClient.send`，与云端体检 `supply_gate` 数的同一个必经点）。
被测的是产品入口本身：`get_json` / `provider.search` / `safe_search`。
不打桩产品逻辑——那等于在测我自己写的桩。
"""
from __future__ import annotations

import pathlib
import sys

import httpx
import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from companion.literature.providers.base import (  # noqa: E402
    BREAKER_STATUS, COOLDOWN, LONG_COOLDOWN, MAX_COOLDOWN, PaperProvider,
    ProviderUnavailable, breaker_open, get_json, reset_breakers, trip_for,
)
from companion.literature.providers.crossref_arxiv import ArxivProvider  # noqa: E402
from companion.literature.providers.openalex import OpenAlexProvider  # noqa: E402


def _stub_send(monkeypatch, status: int) -> list:
    """恒返回该状态码的传输层桩，顺带数出发出去几包。"""
    sent: list = []

    async def _send(self, request, *args, **kwargs):
        sent.append(str(request.url))
        return httpx.Response(status, content=b"{}", request=request)

    monkeypatch.setattr(httpx.AsyncClient, "send", _send)
    return sent


async def test_429_sends_exactly_one_packet(monkeypatch):
    sent = _stub_send(monkeypatch, 429)
    with pytest.raises(ProviderUnavailable):
        await get_json("https://api.test/graph", name="semantic_scholar")
    assert len(sent) == 1, f"429 应在 HTTP 层不重试，实发 {len(sent)} 包"


async def test_5xx_still_retries_three_packets(monkeypatch):
    sent = _stub_send(monkeypatch, 503)
    with pytest.raises(ProviderUnavailable):
        await get_json("https://api.test/graph", name="openalex")
    assert len(sent) == 3, "5xx 是真抖动，产品默认的 retries=2 必须还在重试"


async def test_network_error_still_retries(monkeypatch):
    sent: list = []

    async def _send(self, request, *args, **kwargs):
        sent.append(str(request.url))
        raise httpx.ConnectError("connection refused")

    monkeypatch.setattr(httpx.AsyncClient, "send", _send)
    with pytest.raises(httpx.ConnectError):
        await get_json("https://api.test/graph", retries=1, name="openalex")
    assert len(sent) == 2


async def test_429_stays_on_strike_cooldown_not_permanent(monkeypatch):
    """429 走连击档（120s 起翻倍），403/406 走永久档——这条守的是 `crossref_arxiv.py`
    里拿 `RETRY_STATUS` 判 permanent 的那处耦合：把 429 从重试集合里摘掉时，
    分档不能跟着被顺手改掉。"""
    sent = _stub_send(monkeypatch, 429)
    with pytest.raises(ProviderUnavailable) as ei:
        await ArxivProvider().search("phishing detection")
    assert ei.value.permanent is False
    assert len(sent) == 1

    sent = _stub_send(monkeypatch, 406)
    with pytest.raises(ProviderUnavailable) as ei:
        await ArxivProvider().search("phishing detection")
    assert ei.value.permanent is True, "406 是「这台出口 IP 被拒」，必须留在永久档"


def test_cooldown_tiers_unchanged():
    reset_breakers()
    throttle = ProviderUnavailable("openalex", "HTTP 429", permanent=False)
    assert trip_for("openalex", throttle) == COOLDOWN
    assert trip_for("openalex", throttle) == COOLDOWN * 2
    assert trip_for("openalex", throttle) == COOLDOWN * 4
    assert trip_for("openalex", throttle) == MAX_COOLDOWN, "翻倍要封顶"
    assert trip_for("arxiv", ProviderUnavailable("arxiv", "HTTP 406", permanent=True)) \
        == LONG_COOLDOWN


def test_breaker_still_covers_every_failed_status():
    assert set(BREAKER_STATUS) == {429, 403, 406, 500, 502, 503, 504}, \
        "不重试不等于不熔断：429 退出重试集合后仍必须在熔断集合里"


async def test_safe_search_cools_the_throttled_provider(monkeypatch):
    # 防自欺：若有用例把 safe_search 换成桩且没还原，这条测的就是那个桩而不是产品
    # （test_phase_ef_flow.py 曾这样泄漏过，全量跑才暴露）。
    assert OpenAlexProvider.safe_search is PaperProvider.safe_search
    reset_breakers()
    sent = _stub_send(monkeypatch, 429)
    assert await OpenAlexProvider().safe_search("deep learning detection") == []
    assert len(sent) == 1
    assert breaker_open("openalex") is True, "撞限流的源要立刻进冷却，而不是重试到第三包再冷却"
    reset_breakers()
