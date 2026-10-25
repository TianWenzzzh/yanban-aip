"""PDF 解析：PyMuPDF 页文本 + 章节结构启发式（2.0 任务书 §16/§21）。"""
from __future__ import annotations

import re

import pymupdf

SECTION_PATTERNS = [
    ("abstract", r"^\s*abstract\b"),
    ("introduction", r"^\s*(1\.?\s+)?introduction\b"),
    ("related_work", r"^\s*(2\.?\s+)?related\s+work\b"),
    ("method", r"^\s*(3\.?\s+)?(method|methodology|approach|model|proposed)\b"),
    ("experiments", r"^\s*(4\.?\s+)?(experiments?|evaluation|results?)\b"),
    ("discussion", r"^\s*\d?\.?\s*discussion\b"),
    ("conclusion", r"^\s*\d?\.?\s*(conclusion|summary)\b"),
    ("references", r"^\s*references\b"),
]


def parse_pdf(path: str) -> dict:
    """返回 {"pages": [{"page": n, "text": ...}], "sections": {name: text}, "meta": {...}}。"""
    doc = pymupdf.open(path)
    pages = []
    full_lines: list[tuple[int, str]] = []
    for i, page in enumerate(doc, start=1):
        text = page.get_text("text") or ""
        pages.append({"page": i, "text": text})
        for line in text.splitlines():
            full_lines.append((i, line.strip()))
    meta = {}
    if doc.metadata:
        meta = {k: v for k, v in doc.metadata.items() if v}
    doc.close()

    # 章节启发式：扫描标题行 → 收集到下一标题前的文本
    # marks 顺手记下"每个标题出现在第几页"，页面归属由它派生（同一趟扫描，不再读一遍 PDF）
    sections: dict[str, str] = {}
    marks: list[tuple[int, str]] = []
    current = None
    buf: dict[str, list[str]] = {}
    for page_no, line in full_lines:
        matched = None
        low = line.lower()
        if 3 < len(line) < 80:  # 标题行长度启发
            for name, pat in SECTION_PATTERNS:
                if re.match(pat, low):
                    matched = name
                    break
        if matched:
            current = matched
            buf.setdefault(current, [])
            marks.append((page_no, matched))
            continue
        if current:
            buf[current].append(line)
    for name, lines in buf.items():
        sections[name] = "\n".join(lines)[:6000]
    # 一页只给一个名字：取"这一页开始时已经生效"的那节（一页里跨两节时按先出现的算）
    page_sections = {str(p): next((name for start, name in reversed(marks) if start <= p), None)
                     for p in range(1, len(pages) + 1)}
    return {"pages": pages, "sections": sections, "page_sections": page_sections, "meta": meta}


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", s or "").strip().lower()


def locate(parsed: dict, span: str) -> dict:
    """这段原文在第几页、属于哪一节。找不到就 found=False——由调用方说出来，不许静默当成第 1 页。

    PDF 一行一句、回复里一句跨行是常事，所以两侧都把空白折成一个空格再比：
    逐字匹配的是内容，不是换行位置。
    """
    needle = _norm(span)
    if len(needle) < 20:
        return {"found": False, "page": None, "section": None}
    for p in parsed.get("pages") or []:
        if needle in _norm(p.get("text", "")):
            return {"found": True, "page": p["page"],
                    "section": (parsed.get("page_sections") or {}).get(str(p["page"]))}
    return {"found": False, "page": None, "section": None}


def section_text(parsed: dict, name: str) -> str:
    return parsed.get("sections", {}).get(name, "")
