"""只读分享（P6，对标 Elicit / Litmaps / Research Rabbit 的 share link）。

设计取向是"能撤回、不外泄"：
- token 用 16 字节随机 hex，不做自增 id，避免被人顺着猜；
- 分享的是**快照**（当时那批论文 + 当时的对话），不是实时视图——学生后面改了什么不该被看见；
- 可撤销（revoked 后立刻 404），只读（这个模块没有任何写对方数据的口子）。
"""
from __future__ import annotations

import json
import secrets

from companion import db


def _conn():
    return db.get_conn()   # 建表在 db.SCHEMA 里，与其他表同一处迁移点


def create(project_id: str, *, papers: list[dict], messages: list[dict],
           intent: dict | None = None, title: str = "") -> dict:
    token = secrets.token_hex(16)
    snap = {"title": title[:120],
            "intent": {"topic": (intent or {}).get("topic", ""),
                       "stage": (intent or {}).get("research_stage", ""),
                       "goal": (intent or {}).get("user_goal", "")},
            "papers": [{"title": p.get("title"), "year": p.get("year"), "doi": p.get("doi"),
                        "venue": p.get("venue"), "citation_count": p.get("citation_count"),
                        "url": p.get("url"), "tldr": (p.get("tldr") or "")[:400]}
                       for p in papers[:30]],
            "messages": [{"role": m.get("role"), "text": str(m.get("content") or m.get("text") or "")[:2000]}
                         for m in messages[:60]]}
    _conn().execute("INSERT INTO shares(token, project_id, snapshot_json, created_at) VALUES (?,?,?,?)",
                    (token, project_id, json.dumps(snap, ensure_ascii=False), db.utcnow()))
    _conn().commit()
    return {"token": token, "papers": len(snap["papers"]), "messages": len(snap["messages"])}


def get(token: str) -> dict | None:
    if not token or len(token) > 64:
        return None
    row = _conn().execute("SELECT snapshot_json, revoked_at FROM shares WHERE token=?",
                          (token,)).fetchone()
    if not row or row["revoked_at"]:
        return None
    return json.loads(row["snapshot_json"])


def revoke(token: str, project_id: str) -> bool:
    cur = _conn().execute("UPDATE shares SET revoked_at=? WHERE token=? AND project_id=? AND revoked_at=''",
                          (db.utcnow(), token, project_id))
    _conn().commit()
    return cur.rowcount > 0


def list_for(project_id: str) -> list[dict]:
    rows = _conn().execute(
        "SELECT token, created_at, revoked_at FROM shares WHERE project_id=? ORDER BY created_at DESC",
        (project_id,)).fetchall()
    return [{"token": r["token"], "created_at": r["created_at"], "active": not r["revoked_at"]}
            for r in rows]
