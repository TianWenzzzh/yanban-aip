# -*- coding: utf-8 -*-
"""检索意图卡：把"这一轮打算怎么查"摊开给学生看、允许改，改完仍走既有 run_search（不改闸门）。

对标 undermind 的 U3（"你可以启动搜索，也可以继续调整范围"）——学生改的是**意图**，不是提示词。

🔴 只放程序真读得上的槽位：
 · topic      → 检索式唯一来源（engine._query_from_intent，实测把偏好词拼进去会毁掉相关性判定）
 · year_from  → 进相关性判定与严格过滤（engine.py:195、436）
 · open_access→ 只影响右栏筛选（前端 oaOnly），卡上必须这样写，不能假装它改了检索式
 "排除项"这颗不放：全仓没有任何一条通道读 rejected_directions 去筛检索结果，
 摆上去就是一个看起来能控制、实际什么都不控制的假控件——那比没有更糟。
"""
from __future__ import annotations

from typing import Any


def plan_for(intent: Any) -> dict[str, Any]:
    """当前 intent 会派生出的检索式与三颗槽位。**一次网络调用都不发**（这是这张卡的契约）。"""
    from companion.literature.engine import _query_from_intent
    pp = intent.paper_preferences
    return {"topic": intent.topic or "",
            "query": _query_from_intent(intent),
            "year_from": pp.year_from,
            "open_access": bool(pp.open_access_preferred)}


def apply_edits(intent: Any, edits: dict[str, Any]) -> list[str]:
    """把卡上的改动写回 intent，返回真改动的字段名。

    没改动的不记账：把"用户没碰过"写成"用户确认过"，是这张卡最容易撒的谎。
    year_from 不是数字 ⇒ ValueError（调用方翻成 400，不要静默忽略用户改的那一格）。
    """
    upd: dict[str, Any] = {}
    topic = str(edits.get("topic") or "").strip()[:160]
    if topic and topic != intent.topic:
        upd["topic"] = topic
    if edits.get("year_from") not in (None, ""):
        try:
            year = int(edits["year_from"])
        except (TypeError, ValueError):
            raise ValueError("year_from 得是年份数字，比如 2022") from None
        if year != intent.paper_preferences.year_from:
            upd["paper_preferences.year_from"] = year
    if "open_access" in edits:
        oa = bool(edits["open_access"])
        if oa != intent.paper_preferences.open_access_preferred:
            upd["paper_preferences.open_access_preferred"] = oa
    return intent.apply_update(upd)
