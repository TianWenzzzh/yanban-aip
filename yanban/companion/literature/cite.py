"""文献导出：RIS 与 BibTeX。

对标 Elicit 的分层导出（表格 CSV / 引文 RIS-BIB）。
红线：只输出检索 Provider 给过的元数据字段，缺失就整行省略——绝不补全、绝不用 LLM 生成。
"""
from __future__ import annotations

import csv
import io
import re

_TYPE_MAP = {
    "proceedings-article": "CONF", "journal-article": "JOUR", "posted-content": "RPRT",
    "book-chapter": "CHAP", "book": "BOOK",
}


def _clean(v: object) -> str:
    return re.sub(r"\s+", " ", str(v or "")).strip()


def _tag_pairs(paper: dict) -> list[tuple[str, str]]:
    pairs: list[tuple[str, str]] = [("TY", _TYPE_MAP.get(_clean(paper.get("type")), "JOUR")),
                                    ("TI", _clean(paper.get("title")))]
    for author in (paper.get("authors") or []):
        if _clean(author):
            pairs.append(("AU", _clean(author)))
    if paper.get("year"):
        pairs.append(("PY", str(paper["year"])))
    if _clean(paper.get("venue")):
        pairs.append(("T2", _clean(paper.get("venue"))))
    if _clean(paper.get("doi")):
        pairs.append(("DO", _clean(paper.get("doi"))))
    if _clean(paper.get("url")):
        pairs.append(("UR", _clean(paper.get("url"))))
    if _clean(paper.get("abstract")):
        pairs.append(("AB", _clean(paper.get("abstract"))))
    pairs.append(("ER", ""))
    return [(k, v) for k, v in pairs if k == "ER" or v]


def to_ris(papers: list[dict]) -> str:
    blocks = []
    for p in papers:
        if not _clean(p.get("title")):
            continue
        blocks.append("\n".join(f"{k}  - {v}" for k, v in _tag_pairs(p)))   # ER 也要带 "  - " 前缀，才合 RIS 规范
    return "\n\n".join(blocks) + ("\n" if blocks else "")


# CSV 表头：**列名与顺序在这里定义一次**，导出端与判据端都从这里取。
# 只列检索 Provider 给过的字段（open_access / doi 缺就留空），不放任何 LLM 生成的东西。
CSV_COLUMNS: tuple[tuple[str, str], ...] = (
    ("标题", "title"), ("年份", "year"), ("来源", "venue"), ("被引", "citation_count"),
    ("开放获取", "open_access"), ("状态", "status"), ("作者", "authors"),
    ("DOI", "doi"), ("链接", "url"),
)


def to_csv(papers: list[dict]) -> str:
    """表格导出（对标文件头那句"表格 CSV"——此前只有 RIS/BIB，CSV 一直是空头承诺）。

    与 to_ris 同一条红线：没有标题的行整行不给（宁少一行，不给一行空壳）。
    """
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")     # 定死 \n：Windows 上默认 \r\n 会让判据数错行
    w.writerow([label for label, _ in CSV_COLUMNS])
    for p in papers:
        if not _clean(p.get("title")):
            continue
        row = []
        for _, key in CSV_COLUMNS:
            v = p.get(key)
            row.append("; ".join(_clean(a) for a in v if _clean(a)) if key == "authors" and isinstance(v, list)
                       else ("" if v is None else _clean(v)))
        w.writerow(row)
    return buf.getvalue()


def _cite_key(paper: dict) -> str:
    """引用键必须 ASCII：中文名/带空格姓名会让 Zotero 与 LaTeX 出错。"""
    first = _clean((paper.get("authors") or [""])[0])
    tokens = [t for t in re.split(r"[\s,;]+", first) if t]
    surname = re.sub(r"[^a-z]", "", (tokens[-1] if tokens else "").lower())
    title_words = re.findall(r"[a-zA-Z]{3,}", _clean(paper.get("title")))
    word = (title_words[0] if title_words else "paper").lower()
    return f"{surname or 'anon'}{paper.get('year') or ''}{word[:12]}"


def to_bibtex(papers: list[dict]) -> str:
    out, used = [], set()
    for p in papers:
        title = _clean(p.get("title"))
        if not title:
            continue
        key, n = _cite_key(p), 1
        while key in used:
            key = f"{_cite_key(p)}{n}"; n += 1
        used.add(key)
        fields = [("title", title)]
        if _clean(p.get("authors")):
            fields.append(("author", " and ".join(_clean(a) for a in p["authors"] if _clean(a))))
        if p.get("year"):
            fields.append(("year", str(p["year"])))
        if _clean(p.get("venue")):
            journal = "booktitle" if _clean(p.get("type")) == "proceedings-article" else "journal"
            fields.append((journal, _clean(p.get("venue"))))
        if _clean(p.get("doi")):
            fields.append(("doi", _clean(p.get("doi"))))
        if _clean(p.get("url")):
            fields.append(("url", _clean(p.get("url"))))
        out.append("@article{%s,\n%s\n}" % (key, ",\n".join(f'  {k} = {{{v}}}' for k, v in fields)))
    return "\n\n".join(out) + ("\n" if out else "")
