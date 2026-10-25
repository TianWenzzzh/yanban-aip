"""Crossref / arXiv Provider。

arXiv 自带实现而不再复用 sources.arxiv_search_raw，且中文查询必须走英文关键词。
⚠ 406 的成因已改写：2026-09-22 用 probe_arxiv_406.py 在云端逐个试变量
（https/http × 自定义 UA / 无 UA / 邮箱型 UA / 带 Accept: application/atom+xml），
**五组全部 HTTP 406 且响应体为空** —— 之前注释写的「默认 python-httpx UA 会被拒」不是原因，
真实原因是这台出口 IP 被 arXiv 侧（或中间 WAF）恒定拒绝。别再往 headers 上找解法，
按 base.py 的熔断走永久失败处理。
"""
from __future__ import annotations

import asyncio
import re
from xml.etree import ElementTree as ET

import httpx

from sources import crossref_search

from .base import (BREAKER_STATUS, PERMANENT_STATUS, RETRY_STATUS, UA, PaperProvider,
                   ProviderUnavailable, retry_seconds)

ATOM = "{http://www.w3.org/2005/Atom}"


class CrossrefProvider(PaperProvider):
    name = "crossref"

    async def search(self, query: str, limit: int = 10) -> list[dict]:
        return await crossref_search(query, limit)


class ArxivProvider(PaperProvider):
    name = "arxiv"
    BASE = "https://export.arxiv.org/api/query"

    async def search(self, query: str, limit: int = 10) -> list[dict]:
        words = [w for w in re.split(r"\s+", (query or "").strip()) if w][:4]
        sq = " AND ".join(f'all:"{w}"' for w in words) or f"all:{query}"
        params = {"search_query": sq, "start": 0,
                  "max_results": min(max(limit, 1), 20), "sortBy": "relevance"}
        root, last = None, ""
        async with httpx.AsyncClient(timeout=30.0, headers=UA) as client:
            for attempt in range(3):
                try:
                    r = await client.get(self.BASE, params=params)
                    if r.status_code == 200:
                        # arXiv 响应属外部输入：去掉 DTD 声明再解析，挡开实体展开类攻击
                        root = ET.fromstring(re.sub(r"(?s)<!DOCTYPE.*?>", "", r.text))
                        break
                    last = f"HTTP {r.status_code}"
                    if r.status_code in BREAKER_STATUS:
                        # 406/403 不是「太挤」而是「这台出口 IP 被拒」，必须走永久档：
                        # 少了 permanent，arXiv 实测只拿到 120/240s 连击档，每轮仍白等一次必然失败的往返。
                        # 分档只看 PERMANENT_STATUS——过去写成 not in RETRY_STATUS，把 429 移出重试集合时会误判成永久档。
                        raise ProviderUnavailable(
                            self.name, f"arXiv 不可用：{last}",
                            permanent=r.status_code in PERMANENT_STATUS,
                            retry_after=retry_seconds(r.headers))
                    if r.status_code not in RETRY_STATUS:
                        break
                except (httpx.TimeoutException, httpx.TransportError) as exc:
                    last = str(exc)
                await asyncio.sleep(1.5 * (2 ** attempt))
        if root is None:
            raise RuntimeError(f"arXiv 不可用：{last}")
        out = []
        for e in root.findall(f"{ATOM}entry"):
            title = " ".join((e.findtext(f"{ATOM}title") or "").split())
            if not title:
                continue
            link = (e.findtext(f"{ATOM}id") or "").strip()
            out.append({
                "title": title,
                "authors": [a.findtext(f"{ATOM}name") for a in e.findall(f"{ATOM}author")][:3],
                "year": (e.findtext(f"{ATOM}published") or "")[:4],
                "venue": "arXiv",
                "doi": link.replace("http://arxiv.org/abs/", "arXiv:"),
                "citations": None,
                "abstract": " ".join((e.findtext(f"{ATOM}summary") or "").split())[:600],
                "url": link,
                "open_access": True,  # arXiv 预印本本身即开放获取
            })
        return out
