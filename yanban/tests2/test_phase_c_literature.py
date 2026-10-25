"""Phase C 文献引擎测试：去重 / 排序 / 推荐理由 / Provider 降级。全部离线。"""
from __future__ import annotations

import sys
import pathlib

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from companion.intent.model import ResearchIntent
from companion.literature.engine import _dedup, _attach_reasons, _normalize
from companion.literature.providers.base import PaperProvider


def _paper(title, doi="", year=2025, citations=10, source="crossref"):
    return _normalize({"title": title, "doi": doi, "year": year, "citations": citations,
                       "authors": ["A"], "venue": "IEEE Access"}, source)


def test_dedup_same_doi_once():
    a = _paper("Fishing Website Detection", doi="10.1000/x")
    b = _paper("Fishing Website Detection", doi="10.1000/x")  # 同 DOI 重复
    c = _paper("A Different Paper", doi="10.1000/y")
    out = _dedup([a, b, c])
    assert len(out) == 2


def test_dedup_same_title_no_doi():
    a = _paper("Visual Phishing Detection with CLIP")
    b = _paper("Visual Phishing Detection with CLIP")  # 无 DOI，标题相同
    out = _dedup([a, b])
    assert len(out) == 1


def test_ranking_prefers_recent_cited_oa():
    intent = ResearchIntent(project_id="p")
    intent.apply_update({"paper_preferences.year_from": 2024})
    papers = [
        _paper("Old paper on phishing", year=2015, citations=200),
        _paper("Recent OA phishing survey", year=2025, citations=12, source="openalex"),
        _paper("Mid paper", year=2024, citations=30),
    ]
    for p in papers:
        p["open_access"] = p["title"].startswith("Recent")
    _attach_reasons(papers, intent)
    papers.sort(key=lambda p: -p["scores"].get("final", 0))
    assert papers[0]["title"].startswith("Recent OA")
    assert papers[0]["recommendation_reason"], "每篇必须有推荐理由"


def test_reasons_not_generic():
    p = _paper("Phishing Website Detection Using Visual Analysis", year=2025)
    intent = ResearchIntent(project_id="p")
    intent.apply_update({"topic": "钓鱼网站检测"})
    _attach_reasons([p], intent)
    assert p["recommendation_reason"]
    assert all("高度相关" not in r for r in p["recommendation_reason"]), "禁止废话式理由"


def test_provider_fallback_silent(monkeypatch):
    class BadProvider(PaperProvider):
        name = "bad"

        async def search(self, query, limit=10):
            raise RuntimeError("API down")

    class GoodProvider(PaperProvider):
        name = "good"

        async def search(self, query, limit=10):
            return [{"title": "Good Paper", "year": 2025}]

    import asyncio

    async def run():
        return await asyncio.gather(BadProvider().safe_search("q"), GoodProvider().safe_search("q"))

    bad, good = asyncio.run(run())
    assert bad == []      # 坏 Provider 静默降级（用户无感知）
    assert good and good[0]["title"] == "Good Paper"
