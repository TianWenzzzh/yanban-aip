"""Unpaywall Provider：DOI → 合法 OA PDF 定位（需邮箱参数，无 Key）。"""
from __future__ import annotations

import httpx

from .base import _client


async def resolve_pdf_for_doi(doi: str, email: str = "team@yanban.local") -> str | None:
    """Unpaywall API：返回最佳 OA PDF URL（无 OA 返回 None）。"""
    if not doi or doi.startswith("arXiv:"):
        return None
    url = f"https://api.unpaywall.org/v2/{doi}"
    try:
        async with _client(timeout=20.0) as client:
            r = await client.get(url, params={"email": email})
            if r.status_code != 200:
                return None
            data = r.json()
    except httpx.HTTPError:
        return None
    best = data.get("best_oa_location") or {}
    return best.get("url_for_pdf") or best.get("url")
