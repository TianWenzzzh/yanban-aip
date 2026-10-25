"""PDF Resolver + Downloader：DOI/OA → 合法 PDF 定位 → 下载缓存（2.0 任务书 §10/§16）。"""
from __future__ import annotations

import pathlib
import re
from uuid import uuid4

import httpx

PDF_DIR = pathlib.Path(__file__).resolve().parent.parent / "data" / "papers" / "pdf"
MAX_PDF_BYTES = 50 * 1024 * 1024  # 50MB


async def resolve_pdf_url(paper: dict) -> str | None:
    """论文 → 合法 PDF URL。优先级：显式 pdf_url → arXiv PDF → Unpaywall OA。"""
    url = paper.get("url") or ""
    if url.lower().endswith(".pdf"):
        return url
    doi = paper.get("doi") or ""
    if doi.startswith("arXiv:"):
        return f"https://arxiv.org/pdf/{doi[len('arXiv:')]}.pdf"
    if doi:
        from companion.literature.providers.unpaywall import resolve_pdf_for_doi
        oa = await resolve_pdf_for_doi(doi)
        if oa:
            return oa
    return None


async def download_pdf(paper: dict, project_id: str) -> pathlib.Path | None:
    """下载并缓存到 data/papers/pdf/{project_id}/{paper_id}.pdf。失败返回 None。"""
    url = await resolve_pdf_url(paper)
    if not url:
        return None
    paper_id = paper.get("id") or uuid4().hex[:12]
    out_dir = PDF_DIR / project_id
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / f"{paper_id}.pdf"
    if out.exists() and out.stat().st_size > 0:
        return out
    try:
        async with httpx.AsyncClient(timeout=90, follow_redirects=True) as client:
            async with client.stream("GET", url) as r:
                r.raise_for_status()
                buf = bytearray()
                async for chunk in r.aiter_bytes(64 * 1024):
                    buf += chunk
                    if len(buf) > MAX_PDF_BYTES:
                        raise ValueError("PDF 超过 50MB 上限")
                if not buf[:5].startswith(b"%PDF"):
                    return None  # 非 PDF 内容（如 HTML 登录页）——拒绝保存
                out.write_bytes(bytes(buf))
    except (httpx.HTTPError, ValueError) as exc:
        import logging
        logging.getLogger("yanban.companion.paper").warning("PDF 下载失败 %s: %s", url, exc)
        return None
    return out


def pdf_path_for(project_id: str, paper_id: str) -> pathlib.Path | None:
    p = PDF_DIR / project_id / f"{paper_id}.pdf"
    return p if p.exists() and p.stat().st_size > 0 else None


def sanitize_filename(name: str) -> str:
    return re.sub(r"[^\w\-.]+", "_", (name or "paper").encode("ascii", "ignore").decode())[:60] or uuid4().hex[:8]
