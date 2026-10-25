"""主题跟踪订阅（P4，对标 Research Rabbit 的 "find potential gaps" + Litmaps daily alerts）。

学生做完一轮检索后最常问的是"以后有新文献告诉我"。这里做的是最小可用版：
订阅 = 一条检索式；检查 = 用同一条链路的检索跑一遍，只把**没见过的 DOI**当新文献返回，
并把见过的 DOI 累计存回订阅记录——去重按 DOI，没有 DOI 的按标题归一化。

不假装会推送：本模块只回答"有没有新的、有哪几篇"，什么时候来看由前端或定时任务决定。
"""
from __future__ import annotations

import json
import re

from companion import db



def _conn():
    return db.get_conn()   # 建表在 db.SCHEMA 里，与其他表同一处迁移点


def _key(paper: dict) -> str:
    doi = str(paper.get("doi") or "").strip().lower()
    if doi:
        return f"doi:{doi}"
    title = re.sub(r"[^a-z0-9\u4e00-\u9fff]+", "", str(paper.get("title") or "").lower())
    return f"t:{title[:60]}" if title else ""


def add(project_id: str, query: str, seen: list[dict] | None = None) -> dict:
    query = query.strip()[:200]
    if not query:
        raise ValueError("订阅要有一条检索式")
    keys = [k for k in (_key(p) for p in (seen or [])) if k]
    cur = _conn().execute(
        "INSERT INTO subscriptions(project_id, query, seen_json, created_at) VALUES (?,?,?,?)",
        (project_id, query, json.dumps(keys, ensure_ascii=False), db.utcnow()))
    _conn().commit()
    return {"id": int(cur.lastrowid or 0), "query": query, "seen": len(keys)}


def list_for(project_id: str) -> list[dict]:
    rows = _conn().execute(
        "SELECT id, query, seen_json, created_at, checked_at FROM subscriptions "
        "WHERE project_id=? ORDER BY id", (project_id,)).fetchall()
    return [{"id": r["id"], "query": r["query"], "seen": len(json.loads(r["seen_json"] or "[]")),
             "created_at": r["created_at"], "checked_at": r["checked_at"]} for r in rows]


def check(project_id: str, sub_id: int, *, searcher=None, limit: int = 6) -> dict:
    """跑一次检索，返回没见过的新文献，并把它们的 key 记进已见集合。

    searcher 可注入（测试与云端探针都用得上的接缝）；默认走真检索链路。
    """
    row = _conn().execute("SELECT query, seen_json FROM subscriptions WHERE id=? AND project_id=?",
                          (sub_id, project_id)).fetchone()
    if not row:
        raise KeyError("没有这条订阅")
    seen = set(json.loads(row["seen_json"] or "[]"))
    if searcher is None:
        from companion.intent.model import ResearchIntent
        from companion.literature import engine

        def searcher(query: str) -> list[dict]:
            intent = ResearchIntent(project_id=project_id, topic=query)
            intent.confidence = 1.0
            return engine.search_for_intent(intent, limit=max(limit * 2, 8))
    found = searcher(row["query"]) or []
    fresh = []
    for p in found:
        k = _key(p)
        if not k or k in seen:
            continue
        seen.add(k)
        fresh.append({"title": p.get("title"), "year": p.get("year"), "doi": p.get("doi"),
                      "venue": p.get("venue"), "citation_count": p.get("citation_count")})
        if len(fresh) >= limit:
            break
    _conn().execute("UPDATE subscriptions SET seen_json=?, checked_at=? WHERE id=?",
                    (json.dumps(sorted(seen), ensure_ascii=False), db.utcnow(), sub_id))
    _conn().commit()
    return {"new": fresh, "scanned": len(found), "seen_total": len(seen)}
