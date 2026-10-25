"""插件集：关键词检索 / 翻译 / 思维导图(Mermaid) 生成。"""
from __future__ import annotations

import re

from companion import knowledge
from companion.memory import paper_memory, retriever


# ── 关键词检索（跨论文/记忆/知识库）──────────────────────────────────
def keyword_search(project_id: str, query: str, limit: int = 20) -> dict:
    q = query.strip().lower()
    if not q:
        return {"papers": [], "memories": [], "notes": []}
    papers = [p for p in paper_memory.list_papers(project_id)
              if q in (p.get("title", "") + " " + p.get("abstract", "")).lower()][:limit]
    memories = retriever.retrieve(project_id, query, k=limit)
    notes = knowledge.search(project_id, query, limit=limit)
    return {"papers": papers[:limit], "memories": memories[:limit], "notes": notes}


# ── 翻译 ─────────────────────────────────────────────────────────────
async def translate(text: str, target: str = "zh") -> dict:
    """中英互译（LLM）。MOCK 模式返回确定性占位。"""
    from llm import LLM_MOCK, chat

    target_name = "中文" if target.startswith("zh") else "English"
    direction = "翻译成中文（学术语境，保留术语原文括注）" if target.startswith("zh") else "Translate to academic English"
    prompt = f"{direction}：\n\n{text}\n\n只输出译文，不要解释。"
    if LLM_MOCK:
        return {"target": target, "translation": f"[MOCK-翻译→{target_name}] {text[:80]}", "chars": len(text)}
    out = await chat(prompt, temperature=0.2, max_tokens=2048)
    return {"target": target, "translation": out.strip(), "chars": len(text)}


# ── 思维导图（Mermaid mindmap）/ 流程图（Mermaid flowchart）──────────
def _safe(text: str) -> str:
    """mermaid 标签消毒：方括号等会破语法，尖括号与 & 会破安全——标题来自 Crossref，属外部输入。"""
    return re.sub(r"[\[\](){},;:\"`<>&]", " ", (text or "").strip())[:40] or "未知"


def citations_of(paper: dict) -> int:
    try:
        return int(paper.get("citation_count") or 0)
    except (TypeError, ValueError):
        return 0


def filter_papers(papers: list[dict], *, min_citations: int = 0,
                  only_status: str | None = None) -> list[dict]:
    """按被引下限与状态过滤——导图默认被一堆 3 次引用的论文淹没。"""
    return [p for p in papers
            if citations_of(p) >= max(0, min_citations)
            and (not only_status or p.get("status", "recommended") == only_status)]


def mindmap_from_project(intent, papers: list[dict], *, cap: int = 6) -> str:
    """ResearchIntent + 论文集合 → Mermaid mindmap 文本。

    mermaid mindmap 不支持按权重缩放节点，所以权重用两件能表达的事传达：
    每段内按被引降序排（最重的排最前）+ 节点上直接标「被引 N」。
    """
    lines = ["mindmap", "  root((研伴研究图))"]
    topic = _safe(intent.topic if intent.topic else "待定主题")
    lines.append(f"    {topic}")
    stage = intent.research_stage if intent.research_stage != "unknown" else "调研中"
    lines.append(f"      阶段·{_safe(stage)}")
    if intent.current_focus:
        lines.append("      关注方向")
        for f in intent.current_focus[:3]:
            lines.append(f"        {_safe(f)}")
    if intent.rejected_directions:
        lines.append("      已排除")
        for f in intent.rejected_directions[:3]:
            lines.append(f"        {_safe(f)}")
    by_status: dict[str, list[dict]] = {}
    for p in papers:
        by_status.setdefault(p.get("status", "recommended"), []).append(p)
    for status, group in by_status.items():
        label = {"recommended": "推荐", "to_read": "待读", "read": "已读",
                 "favorite": "收藏", "rejected": "已排除"}.get(status, status)
        group = sorted(group, key=citations_of, reverse=True)
        lines.append(f"      {label}·{len(group)}篇")
        for p in group[:max(1, cap)]:
            lines.append(f"        {_safe(p.get('title', ''))}（被引{citations_of(p)}）")
    return "\n".join(lines)


def flowchart_of_pipeline() -> str:
    """当前研伴工作流的 Mermaid flowchart（可视化说明用）。"""
    return "\n".join([
        "flowchart LR",
        "  U[用户输入] --> C{意图分类}",
        "  C -->|快问快答| LLM[研伴直接回答]",
        "  C -->|检索/综述| S[多源检索引擎]",
        "  C -->|实验/写作| O[Leader 多智能体编排]",
        "  O --> P[Partner 集群]",
        "  S --> R[去重与排序]",
        "  R --> D[论文卡片 + PDF 阅读]",
        "  D --> F[用户反馈]",
        "  F --> M[(记忆系统)]",
        "  M --> C",
    ])
