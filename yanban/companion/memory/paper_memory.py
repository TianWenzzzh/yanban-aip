"""Paper Memory：记录用户对每篇论文的行为与反馈，驱动下一轮 Ranking。"""
from __future__ import annotations

import json

from companion import db


def save_many(project_id: str, papers: list[dict], status: str = "recommended") -> int:
    """批量写入推荐结果（去重：同项目同标题更新而非重复插入）。

    返回**真正插进去的条数**。原先 return len(papers)，去重吃掉多少完全看不出来，
    调用方眼里「入库 0 篇」和「入库 8 篇」是同一个数（轮 106 查「产品吞结果」时踩实）。
    """
    conn = db.get_conn()
    now = db.utcnow()
    inserted = 0
    for p in papers:
        title = p.get("title", "")
        row = conn.execute(
            "SELECT id FROM papers WHERE project_id=? AND paper_json LIKE ?",
            (project_id, f'%{title[:60]}%'),
        ).fetchone()
        if row:
            continue
        conn.execute(
            "INSERT INTO papers (id, project_id, paper_json, status, created_at) VALUES (?,?,?,?,?)",
            (p.get("id"), project_id, json.dumps(p, ensure_ascii=False), status, now),
        )
        inserted += 1
    conn.commit()
    return inserted


def record_feedback(project_id: str, paper: dict, status: str, reason: str = "") -> dict:
    """用户反馈（favorite/read/rejected/too_hard...）→ upsert Paper Memory。"""
    conn = db.get_conn()
    title = paper.get("title", "")
    row = conn.execute(
        "SELECT id FROM papers WHERE project_id=? AND paper_json LIKE ?",
        (project_id, f'%{title[:60]}%'),
    ).fetchone()
    pid = paper.get("id") or (row["id"] if row else __import__("uuid").uuid4().hex[:12])
    existing = conn.execute("SELECT paper_json FROM papers WHERE id=?", (pid,)).fetchone()
    merged = json.loads(existing["paper_json"]) if existing else paper
    merged.update({k: v for k, v in paper.items() if v})
    conn.execute(
        "INSERT INTO papers (id, project_id, paper_json, status, user_feedback, created_at) "
        "VALUES (?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET status=excluded.status, "
        "user_feedback=excluded.user_feedback, paper_json=excluded.paper_json",
        (pid, project_id, json.dumps(merged, ensure_ascii=False), status, reason, db.utcnow()),
    )
    db.write_event(conn, project_id, "paper_feedback",
                   f"论文[{title[:40]}] 状态→{status}" + (f"（{reason}）" if reason else ""),
                   {"status": status, "reason": reason, "title": title})
    conn.commit()
    return {"paper_id": pid, "title": title, "status": status, "reason": reason}


def list_papers(project_id: str, status: str | None = None) -> list[dict]:
    if status:
        rows = db.get_conn().execute(
            "SELECT paper_json, status, user_feedback FROM papers WHERE project_id=? AND status=? "
            "ORDER BY created_at DESC", (project_id, status),
        ).fetchall()
    else:
        rows = db.get_conn().execute(
            "SELECT paper_json, status, user_feedback FROM papers WHERE project_id=? "
            "ORDER BY created_at DESC", (project_id,),
        ).fetchall()
    return [dict(json.loads(r["paper_json"]), status=r["status"], user_feedback=r["user_feedback"]) for r in rows]


def visible_papers(project_id: str, *, status: str | None = None, year_from: int = 0,
                   open_access: bool = False, min_citations: int = 0,
                   sort: str = "relevance") -> list[dict]:
    """表内可见的那批料：**筛选与排序只在这里实现一次**，导出端与列表端都走它。

    为什么要收在一处：前台表格按这三个条件筛（它需要保留全量来做"N/M 篇"的分母），
    导出端一旦各写一遍筛法，"导出与表内逐行一致"就迟早各说一套。
    排序同值时按 id 二次排（再退到标题），否则同一份库两次打开顺序会抖。
    """
    rows = list_papers(project_id, status=status)
    out = [p for p in rows
           if (not year_from or (p.get("year") or 0) >= year_from)
           and (not open_access or p.get("open_access"))
           and (p.get("citation_count") or 0) >= min_citations]
    if sort in ("citations", "year"):
        key = "citation_count" if sort == "citations" else "year"
        out.sort(key=lambda p: (-(p.get(key) or 0), str(p.get("id") or p.get("title") or "")))
    elif sort != "relevance":
        raise ValueError(f"sort 只支持 relevance / citations / year，收到 {sort!r}")
    return out


def library_counts(project_id: str) -> dict[str, int]:
    """本项目库里现成的料：有摘要的篇数、PDF 已缓存的篇数（顶部计数条用）。"""
    rows = db.get_conn().execute(
        "SELECT id, paper_json FROM papers WHERE project_id=?", (project_id,),
    ).fetchall()
    from companion.paper.downloader import pdf_path_for
    abstracts = pdfs = 0
    for r in rows:
        if (json.loads(r["paper_json"]).get("abstract") or "").strip():
            abstracts += 1
        if pdf_path_for(project_id, r["id"]):
            pdfs += 1
    return {"abstracts": abstracts, "pdfs": pdfs}
