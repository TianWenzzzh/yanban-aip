# -*- coding: utf-8 -*-
"""写报告前先讨论：动笔之前问两颗问题，答案进 intent.extra 并**真的改变提示词里各节的取舍**。

对标 undermind 的 U10（生成报告前先回两个编号问题再动笔）。借鉴的是"先问再写"这个契约，
不是它的界面。

🔴 一条硬规矩（轮 184 那颗「排除项」的教训）：每颗选项都必须自带 effect，并且 effect 会被
逐字拼进 build_prompt。做不到"程序读得上"的选项不许摆上去——一个看起来能控制、其实什么都不
控制的开关，比没有这个开关更糟。所以这里只有两颗问题、四颗选项，全部有下游读者。

🔴 跳过必须记账：note_skip 写 skipped=True，与"从没问过"（extra 里根本没有 report_brief）
是两种不同状态。把"没问"显示成"问过并跳过"，或反过来，都是这张卡在撒谎。
"""
from __future__ import annotations

from typing import Any

QUESTIONS: list[dict[str, Any]] = [
    {"key": "purpose", "text": "这份报告主要拿来干什么？", "options": [
        {"value": "gap", "label": "找还没人做的点",
         "effect": "「研究空白与可切入方向」写成整份的重心，「方法对比」只保留支撑这些缺口所必需的差异"},
        {"value": "method", "label": "把这批方法摆开比较",
         "effect": "「方法对比」逐篇写清差异（数据、指标、代价），「研究空白」收敛成一段、不外推"},
    ]},
    {"key": "scope", "text": "范围要多宽？", "options": [
        {"value": "strict", "label": "只写这批论文里有的",
         "effect": "「研究背景与问题」和「相关工作分类」只许陈述上面列到的论文，相邻领域一律不写"},
        {"value": "adjacent", "label": "允许点到相邻方向",
         "effect": "可以在「下一步建议」里点到相邻方向，但那句仍然必须挂上面列表里的角标，挂不上就别写"},
    ]},
]

_KEY = "report_brief"


def brief_for(intent: Any) -> dict[str, Any]:
    """当前这张简报的读数：问题清单（含每颗选项会改什么）＋已答/已跳过。**一次模型调用都不发**。"""
    b = (getattr(intent, "extra", None) or {}).get(_KEY) or {}
    answers = b.get("answers") or {}
    return {"questions": [{"key": q["key"], "text": q["text"],
                           "options": [{"value": o["value"], "label": o["label"], "effect": o["effect"]}
                                       for o in q["options"]]}
                          for q in QUESTIONS],
            "answers": answers,
            "answered": bool(answers),
            "skipped": bool(b.get("skipped")),
            "recorded": bool(b)}


def apply_brief(intent: Any, answers: dict[str, Any]) -> list[str]:
    """记下学生的选择，返回真改动的题号。缺题、乱值都抛 ValueError（调用方翻成 400）。

    答过一次之后再答，skipped 必须被清掉：留着 skipped=True 会让"到底问没问过"读出两个答案。
    """
    answers = answers or {}
    known = {q["key"]: {o["value"] for o in q["options"]} for q in QUESTIONS}
    unknown_keys = [k for k in answers if k not in known]
    if unknown_keys:
        raise ValueError(f"不认识的问题：{'、'.join(sorted(unknown_keys))}")
    # 先报"值不对"再报"没答"：用户已经点过的那颗点了个不认识的选项，比另一颗没点更该先被说出来
    bad = [f"{q['key']}={answers.get(q['key'])}" for q in QUESTIONS
           if q["key"] in answers and answers.get(q["key"]) not in known[q["key"]]]
    if bad:
        raise ValueError(f"选项不在清单里：{'、'.join(bad)}（可选：" +
                         "；".join(f"{q['key']}∈{sorted(known[q['key']])}" for q in QUESTIONS) + "）")
    missing = [q["text"] for q in QUESTIONS if not str(answers.get(q["key"]) or "").strip()]
    if missing:
        raise ValueError(f"这两颗都得答，跳过整份才算跳过：{'、'.join(missing)}")
    extra = intent.extra if isinstance(getattr(intent, "extra", None), dict) else {}
    prev = (extra.get(_KEY) or {}).get("answers") or {}
    changed = [k for k in known if answers.get(k) != prev.get(k)]
    extra[_KEY] = {"answers": {k: answers[k] for k in known}, "skipped": False}
    intent.extra = extra
    return changed


def note_skip(intent: Any) -> bool:
    """记下"问过但学生跳过了"。返回本次是否新记了一笔（已经记过就不重复刷）。"""
    extra = intent.extra if isinstance(getattr(intent, "extra", None), dict) else {}
    if extra.get(_KEY, {}).get("skipped"):
        intent.extra = extra
        return False
    extra[_KEY] = {"answers": {}, "skipped": True}
    intent.extra = extra
    return True


def prompt_block(intent: Any) -> str:
    """把答案翻成提示词里的一段；没答过（含跳过）就是空串——不拿默认偏好冒充学生说的话。"""
    answers = ((getattr(intent, "extra", None) or {}).get(_KEY) or {}).get("answers") or {}
    if not answers:
        return ""
    lines = ["\n学生在动笔前明确说过这两条（它们会改变上面各节的取舍）："]
    for q in QUESTIONS:
        opt = next((o for o in q["options"] if o["value"] == answers.get(q["key"])), None)
        if opt:
            lines.append(f"- {q['text']}「{opt['label']}」⇒ {opt['effect']}")
    return "\n".join(lines) + "\n"
