"""Interview Agent：判断当前是否真正理解用户，产出反问。

不负责找论文。输入 ResearchIntent，输出 1-2 个高增益问题（或 None=信息已足够）。
"""
from __future__ import annotations

import re

from companion.intent.clarification import (FIELD_QUESTIONS, clarification_gaps, next_questions,
                                            ready_for_search)
from companion.intent.model import ResearchIntent


# 学生明说"先给我看看论文"这类话时，再追问就是查户口——先用手里信息给一轮结果才是导师做法
WANTS_RESULTS = re.compile(
    r"(先看看|有什么(能)?(上手|做)?的?(论文|文献|文章)|给我(几篇|来几篇|一些|几篇)|查(一下|几篇|些)?(论文|文献|资料)"
    r"|直接(给|开始|上)|开始吧|看看结果|别问了|哪一篇最适合)")


def wants_results(user_text: str) -> bool:
    return bool(WANTS_RESULTS.search(user_text or ""))


def should_interview(intent: ResearchIntent) -> bool:
    ok, _conf = ready_for_search(intent)
    return not ok


def interview_questions(intent: ResearchIntent, max_questions: int = 2) -> list[str]:
    return next_questions(intent, max_questions=max_questions)


def compose_interview_reply(intent: ResearchIntent, max_questions: int = 2) -> str | None:
    """把问题包装成自然的对话回复；信息足够时返回 None。"""
    if not should_interview(intent):
        return None
    qs = interview_questions(intent, max_questions=max_questions)
    if not qs:
        return None
    if len(qs) == 1:
        return qs[0]
    head = "先跟你对齐两件事："
    return head + "；".join(q.rstrip("？?") + "？" for q in qs)


# ── 点选式反问（对标 Codex request_user_input：反问＝有限选项，不是自由填空）──
# 每项 = (chip 文案, 点选后替学生说出的那句话)。句子必须能被 extractor 的规则通道接住——
# tests2/test_dialogue_llm.py 会逐个选项验证，接不住就是死按钮，测试直接红。
CHIP_OPTIONS: dict[str, tuple[str, list[tuple[str, str]]]] = {
    "research_stage": ("你现在在哪一步", [
        ("先摸清这个方向有什么", "我刚接触这个领域，算入门阶段"),
        ("已经在读文献了", "我已经在读文献了，想做个综述"),
        ("准备定题", "我想定题，挑一个做得动的方向"),
        ("在做方法或实验", "我准备做实验，复现几个 baseline"),
        ("在写论文", "我在写论文，准备投稿"),
    ]),
    # 每条 say 都必须被规则通道 `extractor._GOAL_HINTS` 接得住，否则这颗 chip 是假按钮：
    # 点了之后 user_goal 还是空的，反问照旧。由 tests2/test_user_goal_slot.py 逐条核对。
    "user_goal": ("这次想先拿到什么", [
        ("定一个做得动的方向", "我想先找一个还没人做过、又做得动的切入点"),
        ("完成毕业论文", "我是为了毕业论文"),
        ("一年内投出去", "我打算一年内把论文投出去"),
        ("参加比赛", "这是参加比赛用的"),
    ]),
    "publication_goal.type": ("目标是什么类型", [
        ("发 SCI", "我的目标是发一篇 SCI"),
        ("投会议", "投个会议就行"),
        ("毕业论文", "为了毕业论文"),
        ("参加比赛", "这是参加比赛用的"),
    ]),
    "user_background.dl_level": ("深度学习什么水平", [
        ("完全没接触", "深度学习我完全没接触过"),
        ("学过一点", "深度学习学过一点，能看懂但没怎么跑过模型"),
        ("能自己改模型", "深度学习我很熟练，能自己改模型"),
    ]),
    "paper_preferences.year_from": ("想看多新的文献", [
        ("近两年", "只看近两年的论文"),
        ("近五年", "近五年的论文就行"),
    ]),
    "paper_preferences.open_access_preferred": ("要不要能直接下全文", [
        ("要能下载的", "优先给我开放获取、能直接下全文的"),
        ("无所谓", "付费也行，不用管开放获取"),
    ]),
    "research_constraints.compute": ("算力条件", [
        ("有 GPU", "我有 GPU 可以跑实验"),
        ("只有一台笔记本", "我只有一台笔记本，做不了大实验"),
        ("没有 GPU", "我没有 GPU，只能做轻量级实验"),
    ]),
    "user_background.llm_level": ("大模型这块了解多少", [
        ("不了解", "大模型我不太了解"),
        ("用过 API", "大模型我用过现成 API"),
        ("自己微调过", "大模型我自己微调部署过"),
    ]),
    "user_background.paper_reading_level": ("读英文论文吃力吗", [
        ("吃力，多解释术语", "我读英文论文比较吃力，术语帮我多解释一下"),
        ("还行", "英文论文读着还行"),
    ]),
}

# 检索之后的下一步建议（对标 Perplexity 的 related questions：给可点的下一步，而不是让用户自己想）
SEARCH_SUGGESTIONS = [
    ("只看近两年的", "只看近两年的，再给我一轮"),
    ("只要开放获取的", "接下来只给能直接下全文的开放获取论文"),
    ("挑一篇最适合入门的", "这几篇里哪一篇最适合我先动手读，为什么"),
    ("换个角度再查一轮", "换个角度再帮我查一轮，看看有没有别的技术路线"),
]


def next_chips(intent: ResearchIntent, max_groups: int = 1) -> list[dict]:
    """把最高增益的缺口变成可点选项；信息够了就不再问（返回空）。"""
    ready, _conf = ready_for_search(intent)
    if ready:
        return []
    out = []
    for field in clarification_gaps(intent):
        entry = CHIP_OPTIONS.get(field)
        if not entry:
            continue
        label, opts = entry
        out.append({"field": field, "question": label,
                    "options": [{"label": t, "say": s} for t, s in opts]})
        if len(out) >= max_groups:
            break
    return out


def gap_labels(field_names: list[str]) -> list[dict[str, str]]:
    """把缺口字段名翻成中文标签，供右栏「研究进度」卡显示。

    chip 一次只给一组，卡片要列的是当前全部缺口，所以标签必须从**已有的两张表**派生：
    chip 文案（短，适合列表）优先，没有 chip 的字段退回 FIELD_QUESTIONS 的问句。
    前端另抄一份中文映射的话，后端改字段名时卡片会安静地显示出 `paper_preferences.year_from`。
    """
    q_by_field = {f: q for f, _w, q in FIELD_QUESTIONS}
    out: list[dict[str, str]] = []
    for f in field_names:
        entry = CHIP_OPTIONS.get(f)
        out.append({"field": f, "label": entry[0] if entry else q_by_field.get(f, f)})
    return out


def suggestions_for(intent: ResearchIntent, searched: bool) -> list[dict]:
    """下一步建议 chips：检索过给"再收敛一轮"类，没检索过给"补信息"类。"""
    if searched:
        return [{"label": t, "say": s} for t, s in SEARCH_SUGGESTIONS]
    return []
