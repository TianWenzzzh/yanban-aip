"""tests2 的离线口径：任何想真连外网的检索都在 httpx 层被掐掉，并由 Provider 降级成空。

轮 40 起澄清轮也会检索，于是好几个原本不碰网络的用例（比如只测综述缓存的那条）
开始真连 Crossref——整套件从 45s 涨到 68s，而且结果依赖外网。
所以把"不许出门"变成结构性保证，而不是靠每个用例自觉打桩。
需要检索结果的用例自己 patch：`safe_search`（见 test_phase_ef_flow.py）
或 `engine.search_for_intent`（见 test_phase_a_cognition.py）。
"""
import httpx
import pytest


class OfflineRequest(RuntimeError):
    """测试里出现了真连外网的请求。"""


@pytest.fixture(autouse=True)
def _forbid_real_network(monkeypatch):
    async def _send(self, request, *args, **kwargs):
        raise OfflineRequest(f"tests2 禁止真连外网：{request.method} {request.url}；"
                             "需要检索结果请 patch safe_search / search_for_intent")

    monkeypatch.setattr(httpx.AsyncClient, "send", _send)
