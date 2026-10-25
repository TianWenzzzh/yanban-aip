"""Episodic Memory：重要科研决策事件的存取与检索。"""
from __future__ import annotations

from companion import db


def record(project_id: str, kind: str, summary: str, detail: dict | None = None) -> None:
    conn = db.get_conn()
    db.write_event(conn, project_id, kind, summary, detail)
    conn.commit()


def recent(project_id: str, limit: int = 20) -> list[dict]:
    rows = db.get_conn().execute(
        "SELECT kind, summary, created_at FROM events WHERE project_id=? "
        "ORDER BY id DESC LIMIT ?", (project_id, limit),
    ).fetchall()
    return [dict(r) for r in rows]

# 「这条记忆是谁写的」只按生产写入点认：kind 不在这张表里 = 不知道，不猜一个来填。
SOURCE_WHO = {
    "user_note": "你显式让我记住",
    "paper_feedback": "你在论文上的标记",
    "assumption_feedback": "你确认/纠正了研伴的理解",
    "intent_updated": "研伴从你这轮话里抽取",
    "profile_learned": "研伴从你这轮话里学的画像",
    "state_change": "研伴状态机记的阶段切换",
    "commitment": "研伴规划时记的台账",
}


def source_label(kind: str, detail: dict | None) -> str:
    """把 events.detail_json 里已有的东西翻成一行出处；库里没有就返回空串。

    空串是有效读数，不是失败：前端据此显式写「无出处记录」。库里查不到的字段一律不补，
    因为记忆面板的意义是"这条凭什么算数"，编一句出处等于把它变成装饰。
    """
    d = detail or {}
    who = SOURCE_WHO.get(kind, "")
    if kind == "paper_feedback" and d.get("title"):
        return f"{who}：《{str(d['title'])[:80]}》"
    if kind == "assumption_feedback" and d.get("text"):
        return f"{who}：「{str(d['text'])[:60]}」"
    return who

# 界面上那颗短徽标：面板与「这轮参考了哪几条」两处共用同一张表（轮 192 之前前端另有一份 6 个键的
# KIND_LABEL，措辞与这张不一致、还漏了 3 个 kind ⇒ 同一行里两种口径）。
# 不在表里的 kind 一律退回原始 kind：那是库里真有的字符串，不算编。
SOURCE_TAG = {
    "user_note": "你让我记住",
    "paper_feedback": "论文标记",
    "assumption_feedback": "理解纠正",
    "intent_updated": "意图更新",
    "profile_learned": "画像",
    "state_change": "阶段切换",
    "commitment": "台账",
    "project_created": "新建项目",
}
# 没有 "search_error" 这一项——它**不是** events.kind（只出现在 messages 的 detail 与 intent.extra 里，
# 实测 events 表 0 行）。旧前端表里那颗死条目别抄回来。
# 召回还会取到 messages 表的行（没有 kind，只有 role）：那两句必须分得开是谁说的
ROLE_TAG = {"user": "你说过的话", "assistant": "研伴说过的话"}


def source_tag(kind: str, role: str = "") -> str:
    """kind 优先（事件行），没有 kind 才按 role 翻（聊天记录行）；两个都没有就返回空串。"""
    if kind:
        return SOURCE_TAG.get(kind, kind)
    return ROLE_TAG.get(role, role or "")

