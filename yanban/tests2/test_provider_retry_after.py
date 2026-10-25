# -*- coding: utf-8 -*-
"""轮 162 · 429 的冷却时长该听服务端的 `Retry-After`，而不是固定 120 秒起步翻倍。

判据先写死、看完实测再动产品码（顺序不能反）。为什么现在有资格提这条改动：
  · `输出/scripts/probe_retry_after_window.py` 在容器里同一份矩阵跑出**两个源给出不同答案**：
      OpenAlex：429 带 `retry-after: 15` → 按它等 17s → **200**（服务端说的秒数兑现）
      Semantic Scholar：429 **没有** Retry-After 头 → 盲等 22s 四次 → 仍 429（不兑现，要的是 Key）
  · 而 `providers/base.py:43` 的注释写着「429 的 Retry-After 是**日额度级**的」，
    `ProviderUnavailable` 根本没带这个头，于是 `trip_for()` 只会按 120→240→480→900 秒阶梯冷却。
    在一个长驻服务里，这等于把"其实 15 秒后就放行"的源锁死到 15 分钟——轮 161 量到的
    「供给主导补检索收益」里，**这一块是可修的**。

所以判据必须同时钉住三件事，缺一都会留下假绿：
  A1 带 `Retry-After` 的 429 要把秒数**送到** `ProviderUnavailable` 上（不然下游无从 honored）。
  A2 冷却秒数 = 服务端要的秒数（不是 120 起步，也不再翻倍）；服务端乱报时封顶在 MAX_COOLDOWN。
  A3 没有 `Retry-After` 头时**必须保留**原连击翻倍阶梯（S2 那类：保护真实日额度），
     这条与 A2 是成对的反向判据——只写 A2 的话，改错方向会一路绿到底。
     403/406 永久档也一样不许被新字段影响。
  A4 🔴 本条是防"顺手把 N2 改回去"：冷却变短**不等于**在同一轮里再打一次包。
     429 仍然只发 1 包（轮 157 的不变量），否则这次改动就把 N2 的战果吃掉了。
"""
from __future__ import annotations

import asyncio
import sys
import time
from pathlib import Path

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from companion.literature.providers.base import (  # noqa: E402
    COOLDOWN, LONG_COOLDOWN, MAX_COOLDOWN, ProviderUnavailable, breaker_open, get_json,
    reset_breakers, trip_for)


class _FakeResp:
    def __init__(self, status, headers=None):
        self.status_code = status
        self.headers = headers or {}
        self.text = ""


def _stub_send(monkeypatch, resp):
    n = {"pkts": 0}

    async def fake_send(self, request, **kw):
        n["pkts"] += 1
        return resp

    monkeypatch.setattr(httpx.AsyncClient, "send", fake_send)
    return n


@pytest.fixture(autouse=True)
def _clean_breakers():
    reset_breakers()
    yield
    reset_breakers()


@pytest.mark.asyncio
async def test_a1_429_with_retry_after_carries_the_seconds(monkeypatch):
    """服务端明确要 17 秒 ⇒ 这个数字必须活着到达 trip_for，不能被只留一个状态码。"""
    n = _stub_send(monkeypatch, _FakeResp(429, {"retry-after": "17"}))
    with pytest.raises(ProviderUnavailable) as ei:
        await get_json("https://api.openalex.org/works", name="openalex")
    assert getattr(ei.value, "retry_after", "缺这个字段") == 17.0, (
        f"Retry-After 没被读出来（拿到 {getattr(ei.value, 'retry_after', None)!r}）⇒ "
        "下游只能按固定阶梯冷却，正是本轮要修的根因")
    assert n["pkts"] == 1


@pytest.mark.asyncio
async def test_a1b_429_without_header_stays_none(monkeypatch):
    """S2 实测就没有这个头：此时**不许**凭空造一个秒数。"""
    _stub_send(monkeypatch, _FakeResp(429, {}))
    with pytest.raises(ProviderUnavailable) as ei:
        await get_json("https://api.semanticscholar.org/x", name="s2")
    assert getattr(ei.value, "retry_after", "缺这个字段") is None


@pytest.mark.asyncio
async def test_a2_cooldown_follows_server_seconds_not_the_ladder(monkeypatch):
    """有头 ⇒ 冷却≈服务端要的秒数；连续两次按各自秒数走，不再逐次翻倍。"""
    for hdr, want in (({"retry-after": "15"}, 15.0), ({"retry-after": "40"}, 40.0)):
        _stub_send(monkeypatch, _FakeResp(429, hdr))
        with pytest.raises(ProviderUnavailable) as ei:
            await get_json("https://api.openalex.org/works", name="openalex")
        got = trip_for("openalex", ei.value)
        assert abs(got - want) < 1.0, (
            f"服务端要 {want}s，我们冷却了 {got}s ⇒ 阶梯没让位给服务端（第二次还翻倍就更错）")


@pytest.mark.asyncio
async def test_a2b_absurd_retry_after_is_capped(monkeypatch):
    _stub_send(monkeypatch, _FakeResp(429, {"retry-after": "99999"}))
    with pytest.raises(ProviderUnavailable) as ei:
        await get_json("https://api.openalex.org/works", name="openalex")
    assert trip_for("openalex", ei.value) == MAX_COOLDOWN, "服务端乱报时不许锁死一整天"


@pytest.mark.asyncio
async def test_a3_no_header_keeps_the_escalating_ladder(monkeypatch):
    """A2 的反向判据：没头就必须回到 120→240→480→900（S2 的日额度靠这个保护）。"""
    seq = []
    for _ in range(4):
        _stub_send(monkeypatch, _FakeResp(429, {}))
        with pytest.raises(ProviderUnavailable) as ei:
            await get_json("https://api.semanticscholar.org/x", name="s2")
        seq.append(trip_for("s2", ei.value))
    assert seq == [COOLDOWN, COOLDOWN * 2, COOLDOWN * 4, MAX_COOLDOWN], (
        f"无头时的阶梯被改动了：{seq} ⇒ 这不是本轮要修的那个源，改回去")


@pytest.mark.asyncio
async def test_a3b_permanent_tier_ignores_retry_after(monkeypatch):
    """403/406 是「这台出口被拒」，秒数再小也不许变成短冷却。"""
    _stub_send(monkeypatch, _FakeResp(406, {"retry-after": "5"}))
    with pytest.raises(ProviderUnavailable) as ei:
        await get_json("https://export.arxiv.org/api/query", name="arxiv")
    assert ei.value.permanent is True
    assert trip_for("arxiv", ei.value) == LONG_COOLDOWN


@pytest.mark.asyncio
async def test_a4_shorter_cooldown_does_not_add_a_packet(monkeypatch):
    """🔴 守 N2：冷却归冷却，本轮仍然只发 1 包（不许借"按秒重试"把重试加回来）。"""
    n = _stub_send(monkeypatch, _FakeResp(429, {"retry-after": "3"}))
    with pytest.raises(ProviderUnavailable) as ei:
        await get_json("https://api.openalex.org/works", name="openalex")
    trip_for("openalex", ei.value)
    assert n["pkts"] == 1, f"撞一次限流实发 {n['pkts']} 包 ⇒ 把轮 157 治掉的东西又放回来了"


@pytest.mark.asyncio
async def test_a5_breaker_actually_reopens_after_server_seconds(monkeypatch):
    """冷却秒数必须**真的写进 breaker**：下游读的是 `_DOWN_UNTIL`，不是 trip_for 的返回值。

    只断言 trip_for 的返回值会漏掉一种错法：函数返回 15 但 trip() 里仍按阶梯写 120。
    """
    import companion.literature.providers.base as base

    _stub_send(monkeypatch, _FakeResp(429, {"retry-after": "15"}))
    with pytest.raises(ProviderUnavailable) as ei:
        await get_json("https://api.openalex.org/works", name="openalex")
    trip_for("openalex", ei.value)
    remaining = base._DOWN_UNTIL["openalex"] - time.monotonic()
    assert 14.0 <= remaining <= 16.5, (
        f"breaker 里实际锁了 {remaining:.1f}s，服务端只说了 15s ⇒ 长驻服务会把活源锁死 8 倍时间")
    assert breaker_open("openalex")
    base._DOWN_UNTIL["openalex"] = time.monotonic() - 1     # 只做时钟推进，不改判定逻辑
    assert not breaker_open("openalex"), "过点就该放行"
