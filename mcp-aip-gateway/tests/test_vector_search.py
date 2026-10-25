"""向量检索离线单元测试（T5，无网络：注入假 embed_fn）。"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from gateway.vector_search import VectorIndex, VectorSearchError, _cosine


def run(coro):
    return asyncio.run(coro)


def fake_catalog() -> dict:
    def tool(name, desc):
        return SimpleNamespace(name=name, description=desc)
    return {
        "yanban__literature_search": (None, tool("literature_search", "学术文献检索 arXiv")),
        "yanban__summarize_paper": (None, tool("summarize_paper", "论文摘要压缩")),
        "fetch__fetch": (None, tool("fetch", "网页抓取")),
        "git__git_log": (None, tool("git_log", "仓库提交历史查询")),
    }


def fake_embed_factory(vectors_by_text: dict[str, list[float]]):
    async def embed(texts, **kw):
        return [vectors_by_text[t] for t in texts]
    return embed


VEC = {"lit": [1.0, 0.0, 0.0], "sum": [0.0, 1.0, 0.0], "fetch": [0.0, 0.0, 1.0],
       "git": [0.7, 0.7, 0.0], "q_lit": [0.9, 0.1, 0.0]}


def test_cosine_basics():
    assert _cosine([1, 0], [1, 0]) == 1.0
    assert _cosine([1, 0], [0, 1]) == 0.0
    assert _cosine([], []) == 0.0


def test_vector_query_ranks_semantically():
    mapping = {"yanban__literature_search literature_search 学术文献检索 arXiv": VEC["lit"],
               "yanban__summarize_paper summarize_paper 论文摘要压缩": VEC["sum"],
               "fetch__fetch fetch 网页抓取": VEC["fetch"],
               "git__git_log git_log 仓库提交历史查询": VEC["git"],
               "帮我检索学术论文": VEC["q_lit"]}
    idx = VectorIndex(embed_fn=fake_embed_factory(mapping))
    results = run(idx.query(fake_catalog(), "帮我检索学术论文", limit=3))
    assert results[0]["id"] == "yanban__literature_search"
    assert 0 < results[0]["score"] <= 1.0


def test_vector_query_caches_across_calls():
    calls = {"n": 0}

    async def counting_embed(texts, **kw):
        calls["n"] += len(texts)
        return [VEC["lit"]] * len(texts)

    idx = VectorIndex(embed_fn=counting_embed)
    run(idx.query(fake_catalog(), "q1"))
    run(idx.query(fake_catalog(), "q2"))  # 目录未变 → 目录向量缓存命中（只增量编码查询）
    assert calls["n"] == 4 + 1 + 1  # 首建 4 + 两次查询各 1


def test_no_key_raises_and_caller_degrades():
    async def boom(texts, **kw):
        raise VectorSearchError("未配置 WANBRIDGE_EMBEDDING_API_KEY")
    idx = VectorIndex(embed_fn=boom)
    with pytest.raises(VectorSearchError):
        run(idx.query(fake_catalog(), "q"))


def test_tool_search_route_vector_fallback(tmp_path):
    """路由级：无 key 时 mode=vector 降级 keyword 并标记 fallback_reason。"""
    import os
    from fastapi.testclient import TestClient
    from gateway.a2a_server import create_a2a_app

    os.environ.pop("WANBRIDGE_EMBEDDING_API_KEY", None)

    class R:
        def resolve(self, skill_id):
            return None

        async def call(self, *a):
            return None

        def all_tools(self):
            t = type("T", (), {"name": "echo", "description": "回声测试工具"})()
            s = type("S", (), {"spec": type("P", (), {"ns": "x"})()})()
            return {"x__echo": (s, t)}

        def build_acs(self):
            return {"aic": "A", "name": "g", "version": "1", "skills": [
                {"id": "x__echo", "name": "echo", "description": "回声测试工具", "tags": [], "examples": []}]}

    app = create_a2a_app(R(), self_url="http://test/")
    c = TestClient(app)
    r = c.get("/tools/search", params={"q": "echo", "mode": "vector"}).json()
    assert r["mode"] == "keyword" and "fallback_reason" in r
    assert r["results"] and r["results"][0]["id"] == "x__echo"


def test_rrf_fuse_merges_two_rankers():
    from gateway.a2a_server import rrf_fuse
    kw = [{"id": "a", "score": 5}, {"id": "b", "score": 3}, {"id": "c", "score": 1}]
    vec = [{"id": "b", "score": 0.9}, {"id": "d", "score": 0.8}, {"id": "a", "score": 0.7}]
    fused = rrf_fuse([kw, vec], limit=4)
    ids = [x["id"] for x in fused]
    # b 两路都靠前 → RRF 冠军；a 关键词冠军+向量第三 → 次席
    assert ids[0] == "b" and set(ids) >= {"a", "b"}
    assert all("rrf" in x for x in fused)


def test_rrf_fuse_single_list_passthrough():
    from gateway.a2a_server import rrf_fuse
    kw = [{"id": "x", "score": 1}]
    assert rrf_fuse([kw])[0]["id"] == "x"
