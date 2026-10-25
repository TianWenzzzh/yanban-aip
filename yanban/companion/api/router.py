"""研伴 2.0 Companion API：全部挂载在 /companion 前缀（追加式，不动既有端点）。"""
from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, HTTPException
from fastapi.responses import PlainTextResponse, StreamingResponse
from pydantic import BaseModel
from starlette.concurrency import run_in_threadpool

from companion import db, knowledge, plugins
from companion.api import tools as tool_table
from companion.conversation import dialogue_manager
from companion.conversation.state_machine import State
from companion.literature import engine
from companion.memory import episodic_memory, paper_memory, project_memory, user_memory
from companion.workflow_router import route_plan

router = APIRouter(prefix="/companion", tags=["companion-2.0"])


def _sse(event: str, data: Any) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


class ChatBody(BaseModel):
    user_text: str
    project_id: str | None = None
    user_id: str = "default"
    no_search: bool = False    # 学生在 composer 上勾掉了「本轮检索」


class FeedbackBody(BaseModel):
    project_id: str
    paper: dict[str, Any]
    status: str  # recommended | to_read | read | favorite | rejected
    reason: str = ""


class SearchBody(BaseModel):
    project_id: str
    limit: int = 8


# ── 项目与会话 ────────────────────────────────────────────────────────
@router.post("/project")
def create_project(body: dict | None = None) -> dict:
    name = (body or {}).get("name", "")
    user = (body or {}).get("user_id", "default")
    pid = project_memory.create_project(user_id=user, name=name)
    return {"project_id": pid}


@router.get("/project/{project_id}")
def get_project(project_id: str) -> dict:
    snap = dialogue_manager.project_snapshot(project_id)
    if snap["intent"] is None and not snap["messages"]:
        raise HTTPException(404, f"project {project_id} 不存在")
    return snap


@router.post("/chat")
def chat(body: ChatBody) -> dict:
    if not body.user_text.strip():
        raise HTTPException(400, "user_text 不能为空")
    try:
        return dialogue_manager.handle_message(body.user_text.strip(), body.project_id,
                                               body.no_search)
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from exc


@router.get("/chat/steps")
def chat_steps() -> dict:
    """发送前给 composer 看的「这一步会做什么」清单。

    哪一颗可以被勾掉由后端的 optional 决定，前端不自己判——它一判就成了第二个真相。
    """
    return {"steps": dialogue_manager.CHAT_STEPS,
            "declinable": [s["id"] for s in dialogue_manager.CHAT_STEPS if s["optional"]]}


@router.get("/tools")
def list_tools() -> dict:
    """对外命名工具表——mcp-aip-gateway 与梧桐 AIP 侧都从这里取，不要在两处各写一遍。

    方法与路径每次现从 router 自己的路由表里取，所以这张表不会和真实端点漂移：
    端点少了、改了名或多了没点名的一条，build() 直接抛错而不是返回一份"差不多"的清单。
    """
    return {"tools": tool_table.build(router.routes)}


@router.post("/chat/stream")
async def chat_stream(body: ChatBody) -> StreamingResponse:
    """SSE 流式对话：step* → meta → delta* → amend? → done。

    步骤事件让前端能把"它在干什么"显示成清单（对标 Codex/Claude Code 的进度透明），
    而不是只有一句"思考中"；检索单独占一步，因为它最慢。
    """
    if not body.user_text.strip():
        raise HTTPException(400, "user_text 不能为空")
    text = body.user_text.strip()

    async def events():
        read = dialogue_manager.step("read")
        yield _sse("step", {**read, "status": "doing"})
        try:
            turn = await run_in_threadpool(dialogue_manager.prepare_context, text, body.project_id)
        except ValueError as exc:
            yield _sse("error", {"detail": str(exc)})
            return
        yield _sse("step", {**read, "status": "done", "changed": turn["changed"]})
        find = dialogue_manager.step("search")
        if body.no_search:
            # 「你勾掉了」和「查不动」分两种状态发：都显示成失败等于替用户编造一次事故。
            yield _sse("step", {**find, "status": "skipped", "reason": "declined"})
            turn = await run_in_threadpool(dialogue_manager.run_search, turn, True)
        elif not turn["still_clarifying"]:
            yield _sse("step", {**find, "status": "doing"})
            turn = await run_in_threadpool(dialogue_manager.run_search, turn)
            yield _sse("step", {**find, "status": "done", "count": len(turn["papers"])})
        yield _sse("meta", dialogue_manager.meta_event(turn))
        write = dialogue_manager.step("write")
        yield _sse("step", {**write, "status": "doing"})
        async for name, payload in dialogue_manager.stream_reply(turn, text):
            yield _sse(name, payload)
        yield _sse("step", {**write, "status": "done"})

    return StreamingResponse(events(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@router.get("/commitments/{project_id}")
def get_commitments(project_id: str) -> dict:
    """上一轮交代给学生的行动清单与各条状态（前端「上次的事」卡用）。"""
    from companion.conversation import commitments
    return {"commitments": commitments.stats(project_id)}


@router.post("/commitments/{project_id}/status")
def set_commitment_status(project_id: str, body: dict | None = None) -> dict:
    """学生点界面按钮显式回报某一项：比让模型猜"他做没做"可靠（猜错就是假记忆）。"""
    from companion.conversation import commitments
    try:
        out = commitments.set_status(project_id, int((body or {}).get("index", -1)),
                                     str((body or {}).get("status", "")))
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    if out is None:
        raise HTTPException(404, "这一轮没有可更新的行动清单")
    return {**out, "commitments": commitments.stats(project_id)}


@router.post("/commitments/{project_id}/assumption")
def note_commitment_assumption(project_id: str, body: dict | None = None) -> dict:
    """学生对「研伴这么理解我」点同意或纠正。"""
    from companion.conversation import commitments
    agree = bool((body or {}).get("agree"))
    eid = commitments.note_assumption(project_id, agree, str((body or {}).get("text") or ""))
    return {"recorded": eid, "agree": agree}


@router.post("/search")
def run_search(body: SearchBody) -> dict:
    intent = dialogue_manager.load_intent(body.project_id)
    if intent is None:
        raise HTTPException(404, f"project {body.project_id} 不存在")
    papers = engine.search_for_intent(intent, limit=body.limit)
    paper_memory.save_many(body.project_id, papers, status="recommended")
    return {"count": len(papers), "papers": papers}


# ── 反馈（写 Paper Memory，影响下一轮 Ranking）────────────────────────
@router.post("/paper/feedback")
def paper_feedback(body: FeedbackBody) -> dict:
    rec = paper_memory.record_feedback(
        project_id=body.project_id, paper=body.paper, status=body.status, reason=body.reason,
    )
    grown = ""
    try:  # 反馈要留下用户可见的足迹：收藏/已读 → 论文笔记，排除 → 决策笔记
        if body.status in ("favorite", "read", "to_read"):
            grown = knowledge.auto_grow_from_paper(body.project_id, {**body.paper, "status": body.status})
        elif body.status == "rejected":
            grown = knowledge.auto_grow_from_decision(
                body.project_id, f"排除《{body.paper.get('title') or '未命名'}》", body.reason or "（未填原因）")
    except OSError:
        pass
    state = dialogue_manager.transition(body.project_id, State.REFINE, "学生对论文给了反馈，进入收敛")
    return {"ok": True, "paper_memory": rec, "grown_note": grown, "state": state.value}


@router.get("/paper/{project_id}")
def list_papers(project_id: str, status: str | None = None) -> dict:
    return {"papers": paper_memory.list_papers(project_id, status=status)}


# ── 用户与记忆（只读快照）────────────────────────────────────────────
@router.get("/paper/{project_id}/export")
def paper_export(project_id: str, format: str = "ris", status: str | None = None,
                 year_from: int = 0, open_access: bool = False, min_citations: int = 0,
                 sort: str = "relevance") -> PlainTextResponse:
    """导出论文元数据为 CSV / RIS / BibTeX（对标 Elicit 的分层导出）。

    三个筛选参数与前台表格用的是同一颗状态：表和导出都走 `paper_memory.visible_papers`
    这一个实现，所以"导出内容与表内逐行一致"不靠两边各自写对。
    字段只取检索 Provider 给过的值，缺什么就不写那一行——不补全、不经 LLM。
    """
    from companion.literature import cite
    try:
        papers = paper_memory.visible_papers(project_id, status=status, year_from=year_from,
                                             open_access=open_access, min_citations=min_citations,
                                             sort=sort)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    if format == "ris":
        body, mime, ext = cite.to_ris(papers), "application/x-research-info-systems", "ris"
    elif format == "bib":
        body, mime, ext = cite.to_bibtex(papers), "application/x-bibtex", "bib"
    elif format == "csv":
        # BOM 是给 Excel 的：没有它，中文标题在 Excel 里按 GBK 解码就是一屏乱码
        body, mime, ext = "﻿" + cite.to_csv(papers), "text/csv", "csv"
    else:
        raise HTTPException(400, "format 只支持 ris / bib / csv")
    return PlainTextResponse(body, media_type=mime,
                             headers={"Content-Disposition": f'attachment; filename="yanban-{project_id}.{ext}"'})

class MatrixBody(BaseModel):
    columns: list[dict] = []          # 自定义抽取列 [{"label": "是否用了CLIP"}]，最多 3 列
    status: str | None = None


@router.get("/report/{project_id}/brief")
def report_brief_read(project_id: str) -> dict:
    """写报告前那两颗问题＋当前读数（答了什么／跳没跳过）。**一次模型调用都不发**——它是预览。"""
    from companion.conversation import report_brief
    intent = dialogue_manager.load_intent(project_id)
    if intent is None:
        raise HTTPException(404, f"project {project_id} 不存在")
    return {"project_id": project_id, **report_brief.brief_for(intent)}


class BriefBody(BaseModel):
    answers: dict = {}
    skip: bool = False


@router.post("/report/{project_id}/brief")
def report_brief_write(project_id: str, body: BriefBody) -> dict:
    """记下答案，或记下"问过但跳过了"。

    跳过必须留痕：没问与问了但跳过是两种状态，前端要分开显示——把"没问"演成"学生说过不用问"
    是这张简报最容易撒的谎。缺题或乱值判 400，不静默按默认走。
    """
    from companion.conversation import report_brief
    intent = dialogue_manager.load_intent(project_id)
    if intent is None:
        raise HTTPException(404, f"project {project_id} 不存在")
    try:
        changed = (report_brief.note_skip(intent) if body.skip
                   else report_brief.apply_brief(intent, body.answers))
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    dialogue_manager.save_intent(intent)
    return {"project_id": project_id, "changed": [changed] if isinstance(changed, bool) else changed,
            **report_brief.brief_for(intent)}


@router.post("/report/{project_id}")
def make_report(project_id: str) -> dict:
    """一键综述报告：只喂本项目已检索到的论文，产出直接落进知识库笔记。"""
    from companion.conversation import report
    from companion.literature.matrix import peek_cached
    intent = dialogue_manager.load_intent(project_id)
    if intent is None:
        raise HTTPException(404, f"project {project_id} 不存在")
    papers = paper_memory.list_papers(project_id)
    out = report.build_report(project_id, intent, papers, peek_cached(project_id, papers))
    if not out.get("ok"):
        raise HTTPException(400, out.get("note", "综述生成失败"))
    out["state"] = dialogue_manager.transition(project_id, State.DELIVER, "综述报告已交付").value
    return out


class SectionBody(BaseModel):
    title: str
    section: str
    instruction: str = ""


@router.post("/report/{project_id}/section")
def rewrite_report_section(project_id: str, body: SectionBody) -> dict:
    """只重写综述里的一节（可带学生的一句要求），其余节逐字保留。"""
    from companion.conversation import report
    intent = dialogue_manager.load_intent(project_id)
    if intent is None:
        raise HTTPException(404, f"project {project_id} 不存在")
    out = report.rewrite_section(project_id, intent, paper_memory.list_papers(project_id),
                                 body.title, body.section, body.instruction)
    if not out.get("ok"):
        raise HTTPException(400, out.get("note", "这一节没改成"))
    return out


@router.post("/report/{project_id}/stream")
async def report_stream(project_id: str) -> StreamingResponse:
    """流式生成综述：step → delta* → done。

    整篇一次性返回时，前端会在几十秒里毫无反馈——线上真的出现过这种"看起来卡死"的超时。
    """
    from companion.conversation import report
    from companion.literature.matrix import peek_cached

    intent = await run_in_threadpool(dialogue_manager.load_intent, project_id)
    if intent is None:
        raise HTTPException(404, f"project {project_id} 不存在")
    papers = await run_in_threadpool(paper_memory.list_papers, project_id)
    matrix = await run_in_threadpool(peek_cached, project_id, papers)

    async def events():
        label = "写综述（只依据已检索到的摘要，逐段吐字）"
        yield _sse("step", {"id": "write", "label": label, "status": "doing"})
        async for name, payload in report.stream_report(project_id, intent, papers, matrix):
            if name == "done":
                payload["state"] = (await run_in_threadpool(
                    dialogue_manager.transition, project_id, State.DELIVER, "综述报告已交付")).value
                yield _sse("step", {"id": "write", "label": label, "status": "done"})
            yield _sse(name, payload)

    return StreamingResponse(events(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@router.post("/literature/{project_id}/matrix")
def literature_matrix(project_id: str, body: MatrixBody, fresh: bool = False, deep: bool = True,
                      strict: bool = True) -> dict:
    """跨论文结构化对比表：LLM 只从摘要/已解析全文原文抽取，过不了落地校验的单元格写「未知」。

    fresh=1 跳缓存、deep=0 只用摘要、strict=0 跳过逐字出处比对——三个开关是给 A/B 量数用的，不是日常入口。
    """
    from companion.literature import matrix
    papers = paper_memory.list_papers(project_id, status=body.status)
    return matrix.build_matrix(project_id, papers, extra_columns=body.columns,
                               use_cache=not fresh, use_fulltext=deep, strict=strict)


@router.get("/paper/{project_id}/{filename}/sections")
def paper_sections(project_id: str, filename: str) -> dict:
    """列出已解析论文的章节名与篇幅，供前端做「选一段来问」的按钮。"""
    from companion.paper.downloader import PDF_DIR
    from companion.paper.parser import parse_pdf
    if "/" in filename or "\\" in filename or ".." in filename:
        raise HTTPException(400, "非法文件名")
    path = PDF_DIR / project_id / filename
    if not path.exists():
        raise HTTPException(404, "PDF 未缓存")
    sections = parse_pdf(str(path)).get("sections", {})
    return {"sections": [{"name": k, "chars": len(v or "")} for k, v in sections.items()
                         if (v or "").strip()]}


@router.post("/cite/context")
def cite_context(body: dict) -> dict:
    """回复里的一句话 ↔ 论文原文片段（对标 Scite / Elicit 的"引用上下文抽屉"）。

    卡片只能证明这篇存在，片段才能证明这句有依据。找不到依据就明确 found=False 并给提示，
    绝不用模型再编一段"看起来像依据"的话——那正是本项目防幻觉红线要挡的东西。
    """
    from companion.literature import evidence
    from companion.memory import paper_memory

    pid = str(body.get("project_id", "")).strip()
    claim = str(body.get("claim", "")).strip()
    if not pid or not claim:
        raise HTTPException(400, "project_id 与 claim 必填")
    papers = paper_memory.list_papers(pid)
    idx = body.get("paper_index")
    paper = None
    if isinstance(idx, int) and 1 <= idx <= len(papers):
        paper = papers[idx - 1]
    else:
        title = str(body.get("title", "")).strip().lower()
        paper = next((p for p in papers if title and title[:24] in str(p.get("title", "")).lower()), None)
    if not paper:
        raise HTTPException(404, "这篇论文不在该项目的库里")
    filename = str(body.get("filename") or paper.get("pdf_file") or paper.get("local_file") or "")
    gloss = (evidence.english_gloss([claim]).get(evidence.gloss_key(claim), "")
             if evidence.needs_gloss(claim) else "")
    # 一次解析同时供"章节文本"与"这段在第几页"：两处各 parse_pdf 一遍，迟早一份是旧的
    parsed = evidence.pdf_parsed_for(pid, filename)
    spans = evidence.best_spans(claim, paper, gloss=gloss,
                                pdf_sections=[(k, v) for k, v in (parsed.get("sections") or {}).items()],
                                rare=evidence.rare_tokens(papers))
    # 页码定位：找到就报第几页/哪一节，找不到必须留 found=False 让前台说出来。
    # 摘要与标题那两路来源往往根本不在 PDF 文本里——那不是缺陷，是"这句的依据不在正文页面上"，
    # 所以绝不许退化成"默认第 1 页"（那会把一个不知道的数伪装成一个知道的数）。
    from companion.paper.parser import locate
    for s in spans:
        s.update(locate(parsed, s["text"]))
    survey = evidence.survey(claim, papers, gloss=gloss)
    # 三种结局必须来自**同一次扫描**：被引那篇有片段=grounded；被引那篇没有、别篇有=misnumbered
    # （挂错编号，可机械改指）；整池没有=true_gap。把 status 留在后端算，前端只按它选文案，
    # 否则"这句有没有依据"会出现两份各说一份的口径。
    clicked = papers.index(paper) + 1
    alt = next((h for h in survey["hits"] if h["idx"] != clicked), None)
    status = "grounded" if spans else ("misnumbered" if alt else "true_gap")
    return {"found": bool(spans), "status": status, "spans": spans,
            "supporter": alt, "survey": survey,
            "bilingual": bool(gloss),
            "paper": {"title": paper.get("title"), "year": paper.get("year"),
                      "doi": paper.get("doi")},
            "note": "" if spans else "这句目前没有文献支撑——库里（含已缓存正文）找不到能撑住这句话的原文片段。"
                                     "不代表说错了，但请不要当作有出处的话转述"}


@router.get("/intent/plan/{project_id}")
def intent_plan(project_id: str) -> dict:
    """检索意图卡：这一轮打算怎么查（检索式 + 三颗槽位）。**一次外呼都不发**——它是预览。"""
    from companion.intent import search_plan
    intent = dialogue_manager.load_intent(project_id)
    if intent is None:
        raise HTTPException(404, f"project {project_id} 不存在")
    return {"project_id": project_id, **search_plan.plan_for(intent)}


@router.post("/intent/plan/{project_id}")
def intent_plan_edit(project_id: str, body: dict) -> dict:
    """改卡上的槽位：写回 intent 并回一份新预览。仍然不发检索——发检索是"启动"那一步的事。"""
    from companion.intent import search_plan
    intent = dialogue_manager.load_intent(project_id)
    if intent is None:
        raise HTTPException(404, f"project {project_id} 不存在")
    try:
        changed = search_plan.apply_edits(intent, body or {})
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    dialogue_manager.save_intent(intent)
    return {"project_id": project_id, "changed": changed, **search_plan.plan_for(intent)}


@router.post("/memory/{project_id}/block")
def block_memory(project_id: str, body: dict) -> dict:
    """学生说「这条别再提」——把该记忆片段排除出后续上下文（非删除，可撤销）。"""
    text = (body or {}).get("text", "").strip()[:160]
    if not text:
        raise HTTPException(400, "text 必填")
    intent = dialogue_manager.load_intent(project_id)
    if intent is None:
        raise HTTPException(404, f"project {project_id} 不存在")
    block = intent.extra.get("memory_blocklist") or []
    if text not in block:
        block.append(text)
    intent.extra["memory_blocklist"] = block
    dialogue_manager.save_intent(intent)
    return {"ok": True, "blocked": len(block)}


@router.post("/memory/{project_id}/unblock")
def unblock_memory(project_id: str, body: dict) -> dict:
    text = (body or {}).get("text", "").strip()[:160]
    intent = dialogue_manager.load_intent(project_id)
    if intent is None:
        raise HTTPException(404, f"project {project_id} 不存在")
    block = [b for b in (intent.extra.get("memory_blocklist") or []) if b != text]
    intent.extra["memory_blocklist"] = block
    dialogue_manager.save_intent(intent)
    return {"ok": True, "blocked": len(block)}


@router.post("/memory/{project_id}/note")
def add_memory_note(project_id: str, body: dict) -> dict:
    """学生显式「记住这件事」→ 落成一条 user_note，检索时被优先召回，可单独删除。"""
    text = (body or {}).get("text", "").strip()[:200]
    if not text:
        raise HTTPException(400, "text 必填")
    conn = db.get_conn()
    if not conn.execute("SELECT id FROM projects WHERE id=?", (project_id,)).fetchone():
        raise HTTPException(404, f"project {project_id} 不存在")
    nid = db.write_event(conn, project_id, "user_note", text, {"explicit": True})
    conn.commit()
    return {"ok": True, "id": nid, "text": text}


@router.get("/memory/{project_id}/all")
def list_memories(project_id: str, limit: int = 40) -> dict:
    """把系统记得的东西全列出来（对话片段 / 事件 / 显式笔记），并标出哪些已被「不再引用」。"""
    intent = dialogue_manager.load_intent(project_id)
    if intent is None:
        raise HTTPException(404, f"project {project_id} 不存在")
    conn = db.get_conn()
    rows = conn.execute(
        "SELECT id, kind, summary, detail_json, created_at FROM events WHERE project_id=? "
        "ORDER BY id DESC LIMIT ?", (project_id, max(1, min(limit, 200)))).fetchall()
    block = intent.extra.get("memory_blocklist") or []
    out = [{"id": r["id"], "kind": r["kind"], "text": r["summary"][:200], "created_at": r["created_at"],
            "blocked": any(b in r["summary"] for b in block),
            "source": episodic_memory.source_label(r["kind"], json.loads(r["detail_json"] or "{}")),
            "tag": episodic_memory.source_tag(r["kind"]),
            "deletable": r["kind"] == "user_note"} for r in rows]
    return {"memories": out, "blocked": block, "total": len(out)}


@router.delete("/memory/{project_id}/note/{note_id}")
def delete_memory_note(project_id: str, note_id: int) -> dict:
    """只删学生自己写的笔记；对话与事件是审计痕迹，不想被再提走「不再引用」。"""
    conn = db.get_conn()
    row = conn.execute("SELECT kind FROM events WHERE id=? AND project_id=?",
                       (note_id, project_id)).fetchone()
    if row is None:
        raise HTTPException(404, "没有这条记忆")
    if row["kind"] != "user_note":
        raise HTTPException(400, "只有你显式让我记住的笔记能删除；对话记录请用「不再引用」")
    conn.execute("DELETE FROM events WHERE id=? AND project_id=?", (note_id, project_id))
    conn.commit()
    return {"ok": True, "deleted": note_id}


@router.get("/memory/{user_id}")
def memory_snapshot(user_id: str) -> dict:
    return user_memory.snapshot(user_id)


# ── PDF（Phase D）────────────────────────────────────────────────────
class FetchPdfBody(BaseModel):
    project_id: str
    paper: dict[str, Any]


@router.get("/users/{user_id}/profile")
def get_user_profile(user_id: str) -> dict:
    """画像对学生自己可见：哪一条、是什么、从哪个项目学来的。"""
    from companion.memory import user_memory
    return {"user_id": user_id, "profile": user_memory.get_profile(user_id),
            "records": user_memory.profile_records(user_id),
            "labels": user_memory.PROFILE_LABELS}


@router.post("/users/{user_id}/profile/forget")
def forget_user_profile(user_id: str, body: dict | None = None) -> dict:
    """「这条别记了」是硬删除——留着软删标记就还是会被拼进提示词。"""
    from companion.memory import user_memory
    key = str((body or {}).get("key") or "")
    if key not in user_memory.PROFILE_KEYS:
        raise HTTPException(400, "未知画像键")
    return {"forgotten": key, "removed": user_memory.forget(user_id, key)}


@router.post("/paper/fetch-pdf")
async def fetch_pdf(body: FetchPdfBody) -> dict:
    """解析 OA 来源 → 下载缓存 → 记录路径。"""
    from companion.paper.downloader import download_pdf
    path = await download_pdf(body.paper, body.project_id)
    if not path:
        raise HTTPException(404, "未找到可下载的合法 OA PDF（该论文可能仅摘要可见）")
    paper_id = body.paper.get("id") or path.stem
    state = dialogue_manager.transition(body.project_id, State.READ, "开始精读这篇 PDF")
    return {"paper_id": paper_id, "path": str(path), "state": state.value,
            "pdf_url": f"/companion/paper/{body.project_id}/{path.name}"}


@router.get("/paper/{project_id}/{filename}")
def serve_pdf(project_id: str, filename: str):
    """流式返回已缓存的 PDF 文件。"""
    from fastapi.responses import FileResponse
    from companion.paper.downloader import PDF_DIR
    if "/" in filename or "\\" in filename or ".." in filename:
        raise HTTPException(400, "非法文件名")
    path = PDF_DIR / project_id / filename
    if not path.exists():
        raise HTTPException(404, "PDF 未缓存")
    return FileResponse(str(path), media_type="application/pdf", filename=filename)


@router.post("/paper/{project_id}/{filename}/parse")
def parse_paper_pdf(project_id: str, filename: str) -> dict:
    from companion.paper.downloader import PDF_DIR
    from companion.paper.parser import parse_pdf
    if "/" in filename or "\\" in filename or ".." in filename:
        raise HTTPException(400, "非法文件名")
    path = PDF_DIR / project_id / filename
    if not path.exists():
        raise HTTPException(404, "PDF 未缓存")
    parsed = parse_pdf(str(path))
    return {"sections": list(parsed["sections"].keys()), "pages": len(parsed["pages"]),
            "section_previews": {k: v[:200] for k, v in parsed["sections"].items()}}


class QABody(BaseModel):
    question: str
    filename: str
    section: str = ""   # 只问某一节（高亮即上下文），留空则自动选相关节


@router.get("/map/flowchart")
def flowchart() -> dict:
    return {"format": "mermaid-flowchart", "mermaid": plugins.flowchart_of_pipeline()}


@router.get("/map/{project_id}")
def research_map(project_id: str) -> dict:
    """研究地图 v1：按关键词把论文聚类为路线，标记阅读状态。"""
    papers = paper_memory.list_papers(project_id)
    root: dict[str, dict] = {}
    for p in papers:
        words = {w.lower() for w in (p.get("title", "") + " " + p.get("abstract", "")).split()
                 if len(w) > 4}
        primary = p.get("title", "未分类")[:24]
        for w in sorted(words)[:2]:
            node = root.setdefault(w.capitalize(), {"papers": 0, "statuses": {}})
            node["papers"] += 1
            node["statuses"][p.get("status", "recommended")] = node["statuses"].get(p.get("status", "recommended"), 0) + 1
        root.setdefault("_titles", {}).setdefault(primary, p.get("status", "recommended"))
    return {"project_id": project_id, "routes": {k: v for k, v in root.items() if k != "_titles"},
            "titles": root.get("_titles", {})}


# ── 插件与自适应路由（迭代轮新增）────────────────────────────────────
@router.get("/search/keywords")
def keyword_search(project_id: str, q: str, limit: int = 20) -> dict:
    return plugins.keyword_search(project_id, q, limit=limit)


@router.post("/translate")
async def translate(body: dict) -> dict:
    text = (body or {}).get("text", "").strip()
    if not text:
        raise HTTPException(400, "text 不能为空")
    return await plugins.translate(text, target=(body or {}).get("target", "zh"))


@router.get("/map/mindmap/{project_id}")
def mindmap(project_id: str, min_citations: int = 0, only_status: str | None = None,
            cap: int = 6) -> dict:
    """研究导图：段内按被引降序 + 标出被引数；可按被引下限/状态过滤掉噪声论文。"""
    intent = dialogue_manager.load_intent(project_id)
    if intent is None:
        raise HTTPException(404, f"project {project_id} 不存在")
    papers = paper_memory.list_papers(project_id)
    kept = plugins.filter_papers(papers, min_citations=min_citations, only_status=only_status)
    mermaid = plugins.mindmap_from_project(intent, kept, cap=cap)
    return {"project_id": project_id, "format": "mermaid-mindmap", "mermaid": mermaid,
            "shown": len(kept), "total": len(papers), "dropped": len(papers) - len(kept),
            "top_cited": max((plugins.citations_of(p) for p in kept), default=0)}


# ── 自生长知识库 ─────────────────────────────────────────────────────
@router.get("/knowledge/{project_id}")
def kb_list(project_id: str) -> dict:
    return {"notes": knowledge.list_notes(project_id)}


@router.get("/knowledge/{project_id}/note")
def kb_note(project_id: str, title: str) -> dict:
    text = knowledge.get_note(project_id, title)
    if text is None:
        raise HTTPException(404, "笔记不存在")
    body, tags = knowledge.strip_front(text)   # YAML front-matter 不该出现在用户看到的正文里
    return {"title": title, "body": body, "tags": tags,
            "backlinks": knowledge.backlinks(project_id, title)}


@router.post("/knowledge/{project_id}/note")
def kb_upsert(project_id: str, body: dict) -> dict:
    title = (body or {}).get("title", "").strip()
    if not title:
        raise HTTPException(400, "title 必填")
    name = knowledge.upsert_note(project_id, title, (body or {}).get("body", ""), (body or {}).get("tags"))
    return {"note": name}


@router.get("/knowledge/{project_id}/search")
def kb_search(project_id: str, q: str) -> dict:
    return {"hits": knowledge.search(project_id, q)}


@router.post("/knowledge/{project_id}/grow")
def kb_grow(project_id: str, body: dict) -> dict:
    """自生长：从论文/决策自动生成笔记。body: {"paper": {...}} 或 {"decision": "..."}"""
    if (body or {}).get("paper"):
        note = knowledge.auto_grow_from_paper(project_id, body["paper"])
        return {"grown": note}
    if (body or {}).get("decision"):
        note = knowledge.auto_grow_from_decision(project_id, body["decision"], body.get("detail", ""))
        return {"grown": note}
    raise HTTPException(400, "需要 paper 或 decision")


@router.get("/knowledge/{project_id}/export")
def kb_export(project_id: str) -> StreamingResponse:
    """导出该项目知识库（zip of *.md，Obsidian 可直接作为 vault 打开）。"""
    data = knowledge.export_zip(project_id)
    return StreamingResponse(
        iter([data]), media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="yanban-kb-{project_id}.zip"'})


@router.get("/artifact/{project_id}")
def artifact_html(project_id: str, download: int = 0) -> StreamingResponse:
    """自包含 HTML 产物：双击即读、角标能点回参考文献（对标 WorkBuddy 的「可点击交付物」）。

    与 `/knowledge/{pid}/export`（zip 数据包）的区别是**接收方不需要任何工具**。
    项目不存在 ⇒ 404，不给一个看起来正常的空文件冒充产物。
    """
    from companion import artifact

    row = db.get_conn().execute("SELECT name FROM projects WHERE id=?", (project_id,)).fetchone()
    if not row:
        raise HTTPException(404, f"project {project_id} 不存在")
    notes = [{"note": m["note"], "body": knowledge.note_body(project_id, m["note"]) or "",
              "tags": m["tags"]}
             for m in knowledge.list_notes(project_id)]
    ev = [dict(r) for r in db.get_conn().execute(
        "SELECT kind, summary, created_at FROM events WHERE project_id=? ORDER BY id",
        (project_id,)).fetchall()]
    html_text = artifact.render_artifact(
        row["name"], notes, paper_memory.list_papers(project_id),
        generated_at=db.utcnow(), events=ev)
    disposition = "attachment" if download else "inline"
    return StreamingResponse(
        iter([html_text.encode("utf-8")]), media_type="text/html; charset=utf-8",
        headers={"Content-Disposition": f'{disposition}; filename="yanban-{project_id}.html"'})


# ── 自适应工作流路由 ──────────────────────────────────────────────────
@router.post("/route")
def route_workflow(body: dict) -> dict:
    message = (body or {}).get("message", "")
    project_id = (body or {}).get("project_id")
    conf, topic_known = 0.0, False
    if project_id:
        intent = dialogue_manager.load_intent(project_id)
        if intent:
            conf = intent.confidence
            topic_known = bool(intent.topic)
    return route_plan(message, conf, topic_known)


@router.post("/paper/{project_id}/qa")
def paper_qa(project_id: str, body: QABody) -> dict:
    from companion.paper.downloader import PDF_DIR
    from companion.paper.qa import answer_with_paper
    if "/" in body.filename or ".." in body.filename:
        raise HTTPException(400, "非法文件名")
    path = PDF_DIR / project_id / body.filename
    if not path.exists():
        raise HTTPException(404, "PDF 未缓存")
    return answer_with_paper(body.question, str(path), project_id, body.section)


@router.get("/report/{project_id}/grounding")
def report_grounding(project_id: str, title: str = "") -> dict:
    """综述报告的可核对性读数：带引用的句子有多少句能在被引论文原文里找到依据。"""
    from companion import knowledge
    from companion.literature import evidence
    from companion.memory import paper_memory

    notes = knowledge.list_notes(project_id)
    pick = next((n for n in notes if title and title in str(n.get("note", ""))),
                next((n for n in notes if "综述" in str(n.get("note", ""))), None))
    if not pick:
        raise HTTPException(404, "还没有生成过综述报告")
    body = knowledge.get_note(project_id, str(pick["note"])) or ""
    ps = paper_memory.list_papers(project_id)
    out = evidence.grounding_report(body, ps)
    out["by_section"] = evidence.grounding_by_section(body, ps)
    out["weakest"] = sorted(out["by_section"],
                            key=lambda d: d["coverage"] if d["coverage"] is not None else 1.0)[:1]
    out["note_title"] = str(pick["note"])[:80]
    return out


@router.post("/subscribe")
def subscribe(body: dict) -> dict:
    """订阅一个主题（P4）：把这条检索式记下来，之后每次 check 只报没见过的新文献。"""
    from companion.memory import subscriptions as subs

    pid = str(body.get("project_id", "")).strip()
    query = str(body.get("query", "")).strip()
    if not pid or not query:
        raise HTTPException(400, "project_id 与 query 必填")
    seen = [{"doi": p.get("doi", ""), "title": p.get("title", "")}
            for p in paper_memory.list_papers(pid)]
    return subs.add(pid, query, seen=seen)


@router.get("/subscriptions/{project_id}")
def subscriptions_list(project_id: str) -> dict:
    from companion.memory import subscriptions as subs

    return {"items": subs.list_for(project_id)}


@router.post("/subscriptions/{project_id}/{sub_id}/check")
def subscription_check(project_id: str, sub_id: int) -> dict:
    """手动检查一次。定时任务接入后由它调同一个函数，不另写一套逻辑。"""
    from companion.memory import subscriptions as subs

    try:
        return subs.check(project_id, sub_id)
    except KeyError as exc:
        raise HTTPException(404, str(exc)) from exc


@router.post("/share")
def share_create(body: dict) -> dict:
    """生成一个只读分享快照（P6）。快照而非实时视图：之后学生改了什么不该被链接另一边看到。"""
    from companion.memory import shares

    pid = str(body.get("project_id", "")).strip()
    if not pid:
        raise HTTPException(400, "project_id 必填")
    proj = project_memory.get_project(pid) or {}
    return shares.create(pid, papers=paper_memory.list_papers(pid),
                         messages=dialogue_manager.recent_messages(pid, limit=60),
                         intent=proj.get("intent") or {}, title=str(proj.get("name") or ""))


@router.get("/share/{token}")
def share_read(token: str) -> dict:
    from companion.memory import shares

    snap = shares.get(token)
    if not snap:
        raise HTTPException(404, "链接无效或已撤销")
    return snap


@router.post("/share/{token}/revoke")
def share_revoke(token: str, project_id: str = "") -> dict:
    from companion.memory import shares

    return {"revoked": shares.revoke(token, project_id)}
