"""跨论文结构化对比表（Elicit 式 evidence table）。

诚信约束：本模块不新增任何事实。LLM 只做「从给定的标题+摘要里抽取」，
且每个单元格过一道落地校验——出现摘要里没有的数字或英文术语就判为编造，一律改回「未知」。
"""
from __future__ import annotations

import hashlib
import json
import pathlib
import re
from typing import Any, Iterable

MATRIX_DIR = pathlib.Path(__file__).resolve().parent.parent / "data" / "matrix"
UNKNOWN = "未知"
BASE_COLUMNS: list[tuple[str, str]] = [
    ("method", "方法"), ("dataset", "数据与任务"), ("metric", "指标与结果"), ("limitation", "局限"),
]
# 温度锁 0 之后模型只肯填「方法」一列（线上实测 8/32）；逐字闸门并不背锅（strict 两臂结果完全一致），
# 缺的是"这一列该去抄什么"的说明。
COLUMN_HINTS = {
    "method": "提出/使用的模型、算法、框架名（如 CLIP、CNN、Shark-Eyes）",
    "dataset": "数据集/语料/样本规模与任务类型（如 50000 pages、PhishTank、二分类）",
    "metric": "指标名与数值（accuracy、F1、AUC、召回率、%），原文出现数字就照抄",
    "limitation": "limitations / future work / however / still challenging 那类句子",
}
MAX_PAPERS = 8
MAX_CELL = 60
EXTRACT_TEMPERATURE = 0.0   # 抽取不是创作：线上实测同一批论文重复抽，填充率极差能有 25 个百分点

_STOP = {"the", "and", "for", "with", "our", "we", "can", "may", "use", "used", "uses",
         "based", "via", "from", "that", "this", "are", "was", "were", "proposed", "novel"}


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").lower()).strip()


ABSTRACT_CHARS = 1200      # 只用摘要时的上下文上限
FULLTEXT_CHARS = 2600      # 已解析 PDF 正文时放宽到这里


def source_text(paper: dict, full: str = "") -> str:
    """只喂 Provider 的标题/摘要/TLDR，外加这篇若已解析过的 PDF 正文；其余一律不进上下文。"""
    parts = [paper.get("title", ""), paper.get("abstract", "") or "", paper.get("tldr", "") or "", full]
    return re.sub(r"\s+", " ", " ; ".join(p for p in parts if p)).strip()


def _txt_cache(project_id: str, paper_id: str) -> pathlib.Path:
    return MATRIX_DIR / project_id / f"{paper_id}.txt"


def full_text(project_id: str, paper: dict) -> str:
    """取已下载并解析过的 PDF 正文；解析结果落盘，避免每次重建表都重解一遍 PDF。"""
    pid = str(paper.get("id") or "")
    if not pid:
        return ""
    cache = _txt_cache(project_id, pid)
    if cache.exists():
        return cache.read_text(encoding="utf-8")[:FULLTEXT_CHARS]
    try:
        from companion.paper.downloader import pdf_path_for
        from companion.paper.parser import parse_pdf
        path = pdf_path_for(project_id, pid)
        if not path:
            return ""
        sections = (parse_pdf(str(path)) or {}).get("sections") or {}
        body = re.sub(r"\s+", " ", " ".join(str(v) for v in sections.values())).strip()
    except Exception:  # noqa: BLE001 — 解析不了就退回只用摘要，不能让整张表挂掉
        return ""
    if body:
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_text(body, encoding="utf-8")
    return body[:FULLTEXT_CHARS]


def _key_of(paper: dict) -> str:
    return str(paper.get("id") or paper.get("title") or "")


def collect_sources(project_id: str, papers: list[dict], *, use_fulltext: bool = True) -> dict[str, tuple[str, str]]:
    """paper → (可用于落地校验的原文, 依据=全文/摘要)。use_fulltext=False 用来做 A/B 对照。"""
    out: dict[str, tuple[str, str]] = {}
    for p in papers:
        body = full_text(project_id, p) if use_fulltext else ""
        out[_key_of(p)] = (source_text(p, body), "全文" if body else "摘要")
    return out


def fabricated(cell: str, source: str) -> bool:
    """单元格里出现摘要中不存在的数字或英文术语 → 判定为编造。

    已知口径：中文措辞无法逐字校验（允许改写），只拦最危险的两类幻觉——具体数值与专有名词。
    """
    text, src = _norm(cell), _norm(source)
    if not text or text == UNKNOWN:
        return False
    for num in re.findall(r"\d[\d.,]*%?", text):
        if num.rstrip(".,%") and num.rstrip(".,%") not in src:
            return True
    for word in re.findall(r"[a-z][a-z0-9+.-]{2,}", text):
        if word not in _STOP and word not in src:
            return True
    return False


def _prompt(papers: list[dict], columns: list[tuple[str, str]],
            sources: dict[str, tuple[str, str]] | None = None) -> str:
    sources = sources or {}
    fields = "；\n".join(f"{k}＝{label}" + (f"（该去抄：{COLUMN_HINTS[k]}）" if k in COLUMN_HINTS
                                        else "（按这个提问去原文里找对应句子）") for k, label in columns)
    blocks = []
    for i, p in enumerate(papers, 1):
        text = sources.get(_key_of(p), ("", ""))[0] or source_text(p)
        blocks.append(f"[{i}] id={p.get('id')}\n标题：{p.get('title', '')}\n"
                      f"原文：{text[:FULLTEXT_CHARS if len(text) > ABSTRACT_CHARS else ABSTRACT_CHARS]}")
    return (f"下面给出 {len(papers)} 篇论文的真实原文（标题+摘要，部分含已解析的开放获取全文正文；未经改写）。\n"
            f"请**只依据每篇给出的文本**填写这些字段：{fields}。\n"
            f"每个字段都写成对象：{{\"a\": \"中文短语（≤{MAX_CELL} 字）\", \"e\": \"支撑它的原文逐字片段\"}}；\n"
            f"「e」必须是从上面原文里**一字不差抄下来**的一段（英文照抄即可，别翻译、别改写、别拼凑），"
            f"抄不出来就整个字段写字符串「{UNKNOWN}」；只给字符串而没有 e 的字段会被判无效，等于白填。\n"
            f"禁止用先验知识补全，禁止编造数值、模型名、数据集名；也别把别的论文的内容混进来。\n"
            f'只输出 JSON 数组，每项形如 {{\"id\": \"…\", \"{columns[0][0]}\": {{\"a\": \"…\", \"e\": \"…\"}}}}，不要解释。\n\n'
            + "\n\n".join(blocks))


NO_CONTENT = ("未提供", "没有提供", "未提及", "未说明", "未给出", "原文未", "not provided", "未报告")


def _cell(val: Any, src: str, strict: bool = True) -> tuple[str, str]:
    """(显示值, 逐字证据)。证据片段不是原文子串，或答案里冒出原文没有的数值/专名 → 整格判「未知」。

    strict=False 只用于 A/B 量闸门成本：跳过逐字比对，仍保留数值/专名启发式。
    「未提供具体数值」这类回答不算信息——它就是把「未知」说了三遍，留着的唯一作用是让表看起来更满。
    """
    if isinstance(val, dict):
        ans, ev = str(val.get("a") or "").strip()[:MAX_CELL], str(val.get("e") or "").strip()
    else:
        ans, ev = str(val or "").strip()[:MAX_CELL], ""
    if not ans or ans == UNKNOWN or any(t in ans for t in NO_CONTENT):
        return UNKNOWN, ""
    if fabricated(ans, src) or (strict and ev and _norm(ev) not in _norm(src)):
        return UNKNOWN, ""
    return ans, ev[:220]


def _parse_rows(raw: Any, papers: list[dict], columns: list[tuple[str, str]],
                sources: dict[str, tuple[str, str]] | None = None, *, strict: bool = True) -> list[dict]:
    """按 id 对齐（LLM 漏项/多项都不影响），并对每个单元格做落地校验。"""
    sources = sources or {}
    items = raw if isinstance(raw, list) else (raw.get("items") if isinstance(raw, dict) else None) or []
    by_id = {str(it.get("id")): it for it in items if isinstance(it, dict)}
    by_title = {_norm(str(it.get("title", ""))): it for it in items if isinstance(it, dict)}
    rows: list[dict] = []
    for p in papers:
        pid = str(p.get("id") or "")
        it = by_id.get(pid) or by_title.get(_norm(p.get("title", ""))) or {}
        src, basis = sources.get(_key_of(p)) or (source_text(p), "摘要")
        cells: dict[str, str] = {}
        evidence: dict[str, str] = {}
        for key, _label in columns:
            cells[key], evidence[key] = _cell(it.get(key), src, strict)
        rows.append({"id": pid, "title": p.get("title", ""), "year": p.get("year"),
                     "doi": p.get("doi"), "cells": cells, "evidence": evidence, "basis": basis,
                     "unknown_count": sum(1 for v in cells.values() if v == UNKNOWN)})
    return rows


def _cache_key(project_id: str, picked: list[dict], columns: list[tuple[str, str]],
               sources: dict[str, tuple[str, str]] | None = None) -> str:
    """键与论文顺序无关；原文长度也进键——新解析出 PDF 全文后必须让旧缓存失效。"""
    payload = json.dumps([project_id, sorted(_key_of(p) for p in picked), columns,
                          {k: len(v[0]) for k, v in sorted((sources or {}).items())}],
                         ensure_ascii=False, sort_keys=True)
    return hashlib.sha1(payload.encode("utf-8")).hexdigest()[:16]


def _load(pid: str) -> dict:
    path = MATRIX_DIR / f"{pid}.json"
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001 — 缓存缺失/损坏都按未命中处理
        return {}


def _save(pid: str, cache: dict) -> None:
    MATRIX_DIR.mkdir(parents=True, exist_ok=True)
    (MATRIX_DIR / f"{pid}.json").write_text(json.dumps(cache, ensure_ascii=False), encoding="utf-8")


def peek_cached(project_id: str, papers: list[dict]) -> dict | None:
    """只读缓存，绝不再花一次 LLM 调用（综述生成用得上就带上）。"""
    picked = [p for p in papers if p.get("title")][:MAX_PAPERS]
    if not picked:
        return None
    sources = collect_sources(project_id, picked)
    rows = _load(project_id).get(_cache_key(project_id, picked, BASE_COLUMNS, sources))
    return {"columns": BASE_COLUMNS, "rows": rows} if rows else None


def normalize_columns(extra: Iterable[dict] | None) -> list[tuple[str, str]]:
    """自定义列：key 由后端分配（c1/c2…），label 是用户的提问原话，最多 3 列。"""
    cols = list(BASE_COLUMNS)
    for i, item in enumerate(list(extra or [])[:3]):
        label = str(item.get("label", "")).strip()[:40]
        if label:
            cols.append((f"c{i + 1}", label))
    return cols


def build_matrix(project_id: str, papers: list[dict], *, extra_columns: list[dict] | None = None,
                 use_cache: bool = True, use_fulltext: bool = True, strict: bool = True) -> dict:
    from llm import LLM_MOCK, chat_json, run_async

    papers = [p for p in papers if p.get("title")][:MAX_PAPERS]
    columns = normalize_columns(extra_columns)
    if not papers:
        return {"columns": columns, "rows": [], "note": "还没有可对比的论文"}

    sources = collect_sources(project_id, papers, use_fulltext=use_fulltext)
    key = _cache_key(project_id, papers, columns, sources) + ("" if strict else "-loose")
    cache = _load(project_id) if use_cache else {}
    if key in cache:
        return {"columns": columns, "rows": cache[key], "cached": True, **_basis(cache[key])}

    if LLM_MOCK:
        rows = _parse_rows([], papers, columns, sources, strict=strict)   # MOCK 不发网络请求，整表「未知」
    else:
        try:
            raw = run_async(chat_json(_prompt(papers, columns, sources), max_tokens=1800, timeout=120.0,
                                      temperature=EXTRACT_TEMPERATURE))
        except Exception as exc:  # noqa: BLE001 — 抽取失败要如实标注，不给假表
            return {"columns": columns, "rows": [], "note": f"抽取暂时不可用（{str(exc)[:120]}）"}
        rows = _parse_rows(raw, papers, columns, sources, strict=strict)

    cache[key] = rows
    if use_cache:
        _save(project_id, cache)
    return {"columns": columns, "rows": rows, "cached": False, **_basis(rows)}


def _basis(rows: list[dict]) -> dict:
    """这张表的"依据"指标：多少行用了 PDF 全文、多少格有逐字出处——填充率与出处率都要能被量化。"""
    filled = sum(1 for r in rows for v in r["cells"].values() if v != UNKNOWN)
    sourced = sum(1 for r in rows for k, v in r["cells"].items()
                  if v != UNKNOWN and (r.get("evidence") or {}).get(k))
    return {"fulltext_rows": sum(1 for r in rows if r.get("basis") == "全文"),
            "rows_total": len(rows), "filled": filled, "sourced": sourced,
            "sourced_ratio": round(sourced / filled, 3) if filled else None}
