"""OpenAlex Provider：免费、无需 Key、覆盖面广（含引用数与 OA 标记）。"""
from __future__ import annotations

from .base import PaperProvider, get_json


def from_inverted(index: dict | None) -> str:
    """OpenAlex 不给摘要文本，只给倒排索引 {词: [位置…]}——不还原就等于每篇都没摘要（实测过）。"""
    if not isinstance(index, dict):
        return ""
    words: dict[int, str] = {}
    for word, positions in index.items():
        for pos in positions or []:
            if isinstance(pos, int):
                words[pos] = str(word)
    return " ".join(words[i] for i in sorted(words)).strip()


class OpenAlexProvider(PaperProvider):
    name = "openalex"
    BASE = "https://api.openalex.org/works"

    async def search(self, query: str, limit: int = 10) -> list[dict]:
        params = {
            "search": query,
            "per-page": min(limit, 25),
            "mailto": "team@yanban.local",
        }
        data = await get_json(self.BASE, params=params, name=self.name)
        out = []
        for it in data.get("results", [])[:limit]:
            year = it.get("publication_year")
            out.append({
                "title": (it.get("title") or it.get("display_name") or "").strip(),
                "authors": [a.get("author", {}).get("display_name", "") for a in (it.get("authorships") or [])][:3],
                "year": year,
                "venue": ((it.get("primary_location") or {}).get("source") or {}).get("display_name", "") or "",
                "doi": (it.get("doi") or "").replace("https://doi.org/", ""),
                "citations": it.get("cited_by_count", 0),
                "abstract": from_inverted(it.get("abstract_inverted_index")),
                "open_access": bool((it.get("open_access") or {}).get("is_oa")),
                "url": (it.get("primary_location") or {}).get("landing_page_url", ""),
            })
        return out
