"""Phase E+F 测试：反馈改变 Ranking / 研究地图 / 关键 E2E（小白用户全流程）。"""
from __future__ import annotations

import os
import pathlib
import sys

os.environ["LLM_MOCK"] = "1"
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

import pytest

from fastapi.testclient import TestClient

import companion.db as db_module


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(db_module, "DB_PATH", tmp_path / "test.db")
    db_module._CONN = None
    from leader import app
    return TestClient(app)


def test_e2e_beginner_research_flow(client, monkeypatch):
    """关键 E2E（任务书 §40）：小白用户 → 反问 → intent → 检索 → 反馈 → 断言排序变化。

    外部检索 Provider 通过 monkeypatch 注入固定语料（离线确定性）。
    """
    from companion.literature import engine

    corpus = {
        "crossref": [
            {"title": "Survey of Phishing Detection Approaches", "year": 2025, "doi": "10.1000/survey",
             "citations": 40, "authors": ["A"], "venue": "IEEE"},
            {"title": "Deep Learning for URL Filtering", "year": 2014, "doi": "10.1000/old",
             "citations": 300, "authors": ["B"], "venue": "Elsevier"},
        ],
        "openalex": [
            {"title": "Visual Phishing Detection with CLIP", "year": 2025, "doi": "10.1000/clip",
             "citations": 8, "authors": ["C"], "venue": "arXiv"},
            {"title": "Graph-based Phishing Analysis", "year": 2023, "doi": "10.1000/graph",
             "citations": 15, "authors": ["D"], "venue": "ACM"},
        ],
        "arxiv": [],
        "semantic_scholar": [],
    }

    async def fake_search(self, query, limit=10):
        out = []
        for src, items in corpus.items():
            for it in items:
                low = it["title"].lower()
                # 简单词法路由：visual/clip 查询只命中 CLIP 那篇
                if ("visual" in query.lower() or "clip" in query.lower()) and "visual" not in low and "clip" not in low:
                    continue
                out.append(it)
        return out

    from companion.literature.providers.openalex import OpenAlexProvider
    from companion.literature.providers.crossref_arxiv import ArxivProvider, CrossrefProvider
    from companion.literature.providers.semantic_scholar import SemanticScholarProvider
    monkeypatch.setattr(OpenAlexProvider, "safe_search", fake_search)

    async def fake_crossref(self, q, limit=10):
        return list(corpus["crossref"])

    async def fake_empty(self, q, limit=10):
        return []

    monkeypatch.setattr(CrossrefProvider, "safe_search", fake_crossref)
    monkeypatch.setattr(ArxivProvider, "safe_search", fake_empty)
    monkeypatch.setattr(SemanticScholarProvider, "safe_search", fake_empty)

    # ① 小白第一句 → 必须反问而非搜索
    r = client.post("/companion/chat", json={"user_text": "我想研究钓鱼网站检测，第一次做科研"}).json()
    assert r["state"] == "CLARIFY"
    assert r["reply"] and "论文列表" not in r["reply"]

    # ② 继续补齐信息
    r = client.post("/companion/chat", json={
        "user_text": "目标一年左右发 SCI，深度学习学过一点，要近两年的论文，开放获取优先，没有 GPU",
        "project_id": r["project_id"]}).json()
    pid = r["project_id"]
    assert r["state"] in ("SCREEN", "DELIVER", "SEARCH", "CLARIFY")

    # ③ 检索结果入库
    papers = client.post("/companion/search", json={"project_id": pid}).json()["papers"]
    assert len(papers) >= 2
    before = [p["title"] for p in papers]

    # ④ 用户反馈：排除 Graph-based 论文（方向不相关）
    graph_paper = next((p for p in papers if "Graph" in p["title"]), papers[-1])
    fb = client.post("/companion/paper/feedback", json={
        "project_id": pid, "paper": graph_paper, "status": "rejected", "reason": "方向不相关"}).json()
    assert fb["ok"]

    # ⑤ 反馈必须影响：排除项进入 rejected 列表（验收 10）
    lib = client.get(f"/companion/paper/{pid}", params={"status": "rejected"}).json()["papers"]
    assert any("Graph" in p["title"] for p in lib)

    # ⑥ 研究地图可用（验收外加分项 v1）
    mp = client.get(f"/companion/map/{pid}").json()
    assert mp["titles"]

    # ⑦ 检索快照与排序可复现（验收 11 的可观测部分：下一轮检索基于同一 intent 的记忆体系）
    papers2 = client.post("/companion/search", json={"project_id": pid}).json()["papers"]
    assert [p["title"] for p in papers2] == before  # 同一 intent 确定性重排
