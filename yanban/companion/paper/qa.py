"""论文上下文问答（§20）：PDF 打开时基于解析文本 + Memory 上下文回答。"""
from __future__ import annotations

from companion.memory import retriever
from companion.paper.parser import parse_pdf


def answer_with_paper(question: str, pdf_path: str, project_id: str | None = None,
                      section: str = "") -> dict:
    """结合论文内容回答问题；给了 section 就只喂那一节（对标 Canvas「高亮即上下文」）。"""
    parsed = parse_pdf(pdf_path)
    sections = parsed.get("sections", {})

    if section and section in sections:
        ranked = [(section, sections[section])]        # 用户明确指定了节，不再猜相关节
    else:
        q_low = question.lower()
        ranked = sorted(
            sections.items(),
            key=lambda kv: sum(1 for w in re_split(q_low) if w in kv[1].lower()),
            reverse=True,
        )[:3]
    context = "\n\n".join(f"[{name}]\n{text[:1500]}" for name, text in ranked)
    memory_ctx = retriever.summarize_context(project_id, question, k=3) if project_id else ""

    from llm import LLM_MOCK, chat, run_async

    scope = f"论文「{section}」一节" if section and section in sections else "论文相关章节"
    prompt = (
        f"你是科研助手「研伴」。以下是{scope}的内容：\n\n{context}\n\n"
        f"{'用户相关记忆：\n' + memory_ctx if memory_ctx else ''}\n"
        f"用户问题：{question}\n"
        "请基于所给内容回答；内容里没有的信息要明确说明「论文该部分未提及」。"
    )
    if LLM_MOCK:
        answer = (f"[MOCK] 基于{scope}（{', '.join(n for n, _ in ranked) or '无匹配节'}）"
                  f"对问题「{question[:50]}」的回答。")
    else:
        answer = run_async(chat(prompt, max_tokens=1024, timeout=45.0))
    return {"question": question, "answer": answer, "sections_used": [n for n, _ in ranked],
            "section": section or None}


def re_split(text: str) -> list[str]:
    import re
    return [w for w in re.findall(r"[a-zA-Z]{3,}|[\u4e00-\u9fff]{2}", text.lower())]
