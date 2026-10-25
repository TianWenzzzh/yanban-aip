"""Semantic Scholar Provider：无 Key 可低频使用；429 时自动降级（调用方 safe_search）。"""
from __future__ import annotations

from .base import PaperProvider, get_json


class SemanticScholarProvider(PaperProvider):
    name = "semantic_scholar"
    BASE = "https://api.semanticscholar.org/graph/v1/paper/search"

    async def search(self, query: str, limit: int = 10) -> list[dict]:
        params = {
            "query": query,
            "limit": min(limit, 20),
            "fields": "title,authors,year,venue,externalIds,citationCount,abstract,isOpenAccess",
        }
        data = await get_json(self.BASE, params=params, timeout=20.0, name=self.name)
        out = []
        for it in data.get("data", [])[:limit]:
            out.append({
                "title": (it.get("title") or "").strip(),
                "authors": [a.get("name", "") for a in (it.get("authors") or [])][:3],
                "year": it.get("year"),
                "venue": it.get("venue") or "",
                "doi": (it.get("externalIds") or {}).get("DOI", ""),
                "citations": it.get("citationCount", 0),
                "abstract": (it.get("abstract") or "")[:600],
                "open_access": bool(it.get("isOpenAccess")),
            })
        return out
