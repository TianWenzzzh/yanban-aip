"""P4 评审回炉：只对**机检判不过的句子**发一次受控子查询，再让同一把机检复算。

为什么不叫"再想一想"：无外部信号的自我批评是负的（GPT-3.5 CommonSenseQA 75.8→38.1、
GPT-4 GSM8K 95.5→89.0）。这里每一轮的判据都是产品已有的两类二值机检：
  ① 这个标题在不在检索结果里（`evidence` 的标题锚，假标题整句剔除那条闸门在用）；
  ② 这句在不在依据池里找得到支撑（`grounding_report` 的逐句回连，产物台账在用）。
所以"回炉"不是让模型重述，而是**换一批料再判一次**：找不到支撑的句子仍然算没过，
不会因为多跑了一轮就自动变绿。

词表来自句子本身（稀有词优先），不让模型自由发挥——与 P3 的受控子查询同一条纪律。
"""
from __future__ import annotations

import re

from companion.literature.engine import GENERIC_TOKENS

REFINE_ROUNDS_MAX = 1        # 每份报告最多回炉一轮（写死，不给"再想一想"留口子）
MAX_CLAIM_TERMS = 4          # 一条未接地句子最多抽几个词进子查询
MIN_TERM_LEN = 3


def _terms(sentence: str) -> list[str]:
    """句子里能当检索词的词：先拉丁词（长词稀有），再 4 字中文窗口，停用词一律不要。"""
    low = sentence.lower()
    latin = [w for w in re.findall(r"[a-z]{6,}", low) if w not in GENERIC_TOKENS]
    cjk: list[str] = []
    for run in re.findall(r"[一-鿿]{4,}", low):
        cjk += [run[i:i + 4] for i in range(len(run) - 3)]
    out: list[str] = []
    for t in latin + cjk:
        if t not in out:
            out.append(t)
    return out[:MAX_CLAIM_TERMS]


def claim_queries(sentences: list[str], topic: str = "") -> list[str]:
    """给每条未接地的句子拼一条**只含句子自身词**的子查询；句子太长时截到 MAX_CLAIM_TERMS 个词。"""
    out: list[str] = []
    for s in sentences:
        ts = _terms(s)
        if not ts:
            continue
        q = " ".join(ts)
        if q not in out:
            out.append(q)
    return out


def ungrounded_sentences(report_text: str, papers: list[dict]) -> list[dict]:
    """机检②：带出处却回连不上原文的句子（挂错篇 + 整池查无），逐字返回。"""
    from companion.literature import evidence
    ev = evidence.grounding_report(report_text, papers)
    return [r for r in (ev.get("rows") or []) if r.get("status") in ("misnumbered", "true_gap")]


def grounding_counts(report_text: str, papers: list[dict]) -> dict:
    from companion.literature import evidence
    ev = evidence.grounding_report(report_text, papers)
    return {"cited": ev.get("cited_sentences", 0), "grounded": ev.get("grounded", 0),
            "misnumbered": ev.get("misnumbered", 0), "true_gap": ev.get("true_gap", 0)}


def span_in_pool(span_text: str, pool: list[dict]) -> bool:
    """那段"支撑"必须逐字躺在某篇的标题/摘要/TLDR 里——编出来的摘录一律不算救回。"""
    if not span_text:
        return False
    return any(span_text in ((p.get("abstract") or "") + " " + (p.get("tldr") or "")
                              + " " + (p.get("title") or "")) for p in pool)


def _key(p: dict) -> str:
    return str(p.get("id") or p.get("doi") or (p.get("title") or "")[:60])


def _sent_key(s: str) -> str:
    """句子的稳定身份 = **去掉角标之后**的正文。

    回炉会改角标（[1]→[3]），拿原句当键去比"改后还在不在未接地集合里"永远对不上，
    于是救回数恒为 0、机制看着无效（本轮的测试就是这么抓到的）。
    """
    return re.sub(r"\s+", "", re.sub(r"\[\d{1,2}\]", "", s or ""))


def refine_pass(report_text: str, papers: list[dict], more_papers: list[dict]) -> dict:
    """一轮回炉的**记账**：补进来的料并进依据池 → 让机检自己改角标 → 同一把尺子复算。

    只做两件不需要模型自觉的事：① 把新论文并入依据池；② `renumber_citations`
    （它只在"被引那篇对本句零支撑、而另一篇确实撑得住"时才动角标，整池都撑不住就原样留着）。
    `rescued` 因此必须带逐字摘录：救回却没有原文片段，就不算救回。
    """
    from companion.literature import evidence
    before = {_sent_key(r["sentence"]): r for r in ungrounded_sentences(report_text, papers)}
    have = {_key(p) for p in papers}
    pool = papers + [p for p in more_papers if _key(p) not in have]
    text_after, moved = evidence.renumber_citations(report_text, pool)
    after_rows = {_sent_key(r["sentence"]): r
                  for r in evidence.grounding_report(text_after, pool)["rows"]}
    rescued: list[dict] = []
    still: list[str] = []
    for k, row in before.items():
        now = after_rows.get(k) or {}
        if now.get("status") == "grounded":
            rescued.append({"sentence": row["sentence"], "span": now.get("span") or {},
                            "status_after": now.get("status", "?")})
        else:
            still.append(row["sentence"])
    for r in rescued:
        r["span_ok"] = span_in_pool((r["span"] or {}).get("text") or "", pool)
    return {"rounds": 1, "cap": REFINE_ROUNDS_MAX,
            "before_ungrounded": len(before), "after_ungrounded": len(still),
            "rescued": rescued, "still_ungrounded": still,
            "markers_moved": moved, "text_after": text_after,
            "pool_before": len(papers), "pool_after": len(pool)}
