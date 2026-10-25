"""对话编排入口：用户消息 → 规则通道更新结构化意图 → LLM 生成导师式回复。

回复文本由 companion.conversation.mentor 交给 LLM；本模块只管状态机推进、落库与降级保底。
"""
from __future__ import annotations

import json
import time
import uuid
from typing import Any

from companion import db
from companion.conversation import commitments, mentor
from companion.conversation.interview_agent import (compose_interview_reply, gap_labels, next_chips,
                                                   suggestions_for, wants_results)
from companion.conversation.state_machine import State, can_transition
from companion.intent.clarification import clarification_gaps, search_threshold
from companion.intent.extractor import extract_update
from companion.intent.model import RESEARCH_STAGES, ResearchIntent
from companion.memory import episodic_memory, retriever, user_memory

# 把"上次交代的事"端进提示词这一腿的开关。默认关：g181（40 对 A/B）收益未证明、
# 而 S6 护栏开火（闸门违规 B 23 > A 16）。台账、学生回报结项、跟进卡都不受它影响。
# 要重新打开必须先有一场 A/B 在 S6 上不劣化，别拿措辞去赌。
INJECT_FOLLOWUP = False


def _conn():
    return db.get_conn()


# ── 项目与意图存取 ────────────────────────────────────────────────────
def create_project(user_id: str = "default", name: str = "") -> str:
    conn = _conn()
    pid = uuid.uuid4().hex[:12]
    now = db.utcnow()
    conn.execute(
        "INSERT INTO projects (id, user_id, name, state, created_at, updated_at) VALUES (?,?,?,?,?,?)",
        (pid, user_id, name or "新研究", State.DISCOVER.value, now, now),
    )
    conn.execute(
        "INSERT INTO research_intents (project_id, intent_json, updated_at) VALUES (?,?,?)",
        (pid, db.dumps(ResearchIntent(project_id=pid).model_dump()), now),
    )
    conn.commit()
    db.write_event(conn, pid, "project_created", f"创建研究项目 {name or pid}")
    conn.commit()
    return pid


def load_intent(project_id: str) -> ResearchIntent | None:
    row = _conn().execute(
        "SELECT intent_json FROM research_intents WHERE project_id=?", (project_id,)
    ).fetchone()
    return ResearchIntent(**json.loads(row["intent_json"])) if row else None


def save_intent(intent: ResearchIntent) -> None:
    conn = _conn()
    conn.execute(
        "UPDATE research_intents SET intent_json=?, updated_at=? WHERE project_id=?",
        (db.dumps(intent.model_dump()), db.utcnow(), intent.project_id),
    )
    conn.commit()


def set_state(project_id: str, state: State) -> None:
    conn = _conn()
    conn.execute(
        "UPDATE projects SET state=?, updated_at=? WHERE id=?",
        (state.value, db.utcnow(), project_id),
    )
    conn.commit()


def get_state(project_id: str) -> State:
    row = _conn().execute("SELECT state FROM projects WHERE id=?", (project_id,)).fetchone()
    return State(row["state"]) if row else State.DISCOVER


def transition(project_id: str, target: State, reason: str = "") -> State:
    """按转移表推进：非法转移不改状态（返回当前态），真的挪了一步才留一条 state_change 事件。

    这张表不是装饰——八态里 SCREEN/READ/REFINE 之前从没被设置过，
    现在由筛选、读 PDF、给反馈这三类真实动作驱动，界面上也看得见。
    """
    cur = get_state(project_id)
    if cur is target or not can_transition(cur, target):
        return cur
    set_state(project_id, target)
    conn = _conn()
    db.write_event(conn, project_id, "state_change",
                   f"{cur.value} → {target.value}" + (f"（{reason}）" if reason else ""),
                   {"from": cur.value, "to": target.value, "reason": reason})
    conn.commit()
    return target


def project_user(project_id: str) -> str:
    """这个项目属于哪个用户——画像是用户级的，取不到就什么都不做（不猜一个 default）。"""
    row = _conn().execute("SELECT user_id FROM projects WHERE id=?", (project_id,)).fetchone()
    return str(row["user_id"]) if row and row["user_id"] else ""


def add_message(project_id: str, role: str, content: str, meta: dict | None = None) -> None:
    conn = _conn()
    conv = conn.execute(
        "SELECT id FROM conversations WHERE project_id=? ORDER BY created_at DESC LIMIT 1",
        (project_id,),
    ).fetchone()
    if not conv:
        cid = uuid.uuid4().hex[:12]
        conn.execute(
            "INSERT INTO conversations (id, project_id, created_at) VALUES (?,?,?)",
            (cid, project_id, db.utcnow()),
        )
    else:
        cid = conv["id"]
    conn.execute(
        "INSERT INTO messages (conversation_id, role, content, meta_json, created_at) VALUES (?,?,?,?,?)",
        (cid, role, content, db.dumps(meta or {}), db.utcnow()),
    )
    conn.commit()


def project_snapshot(project_id: str) -> dict[str, Any]:
    conn = _conn()
    proj = conn.execute("SELECT * FROM projects WHERE id=?", (project_id,)).fetchone()
    intent = load_intent(project_id)
    msgs = conn.execute(
        "SELECT role, content, created_at FROM messages WHERE conversation_id IN "
        "(SELECT id FROM conversations WHERE project_id=?) ORDER BY created_at LIMIT 200",
        (project_id,),
    ).fetchall()
    papers = conn.execute(
        "SELECT paper_json, status FROM papers WHERE project_id=? ORDER BY created_at DESC LIMIT 100",
        (project_id,),
    ).fetchall()
    return {
        "project_id": project_id,
        # 画像是**用户级**的，前端要读它就得知道这个项目属于谁；在这里带出去，
        # 而不是再开一条"按项目查画像"的端点（同一件事两个入口迟早各说一套）
        "user_id": proj["user_id"] if proj else None,
        "state": proj["state"] if proj else State.DISCOVER.value,
        "intent": intent.model_dump() if intent else None,
        "messages": [dict(m) for m in msgs],
        "papers": [dict(json.loads(p["paper_json"]), status=p["status"]) for p in papers],
    }


# ── 核心入口 ─────────────────────────────────────────────────────────
def recent_messages(project_id: str, limit: int = 12) -> list[dict]:
    conn = _conn()
    rows = conn.execute(
        "SELECT role, content FROM messages WHERE conversation_id IN "
        "(SELECT id FROM conversations WHERE project_id=?) ORDER BY id DESC LIMIT ?",
        (project_id, limit),
    ).fetchall()
    return [dict(r) for r in reversed(rows)]


def prepare_context(user_text: str, project_id: str | None = None) -> dict[str, Any]:
    """落库用户消息 + 规则通道更新结构化意图。不检索——检索单独一步，好让流式能分步展示进度。"""
    if not project_id:
        project_id = create_project(name=user_text[:24])
    conn = _conn()
    if not conn.execute("SELECT id FROM projects WHERE id=?", (project_id,)).fetchone():
        raise ValueError(f"project {project_id} 不存在")
    intent = load_intent(project_id) or ResearchIntent(project_id=project_id)
    history = recent_messages(project_id)
    add_message(project_id, "user", user_text)

    update = extract_update(user_text, intent)      # 后台结构化通道，不决定回复文本
    changed = intent.apply_update(update)
    if changed:
        save_intent(intent)
        db.write_event(conn, project_id, "intent_updated",
                       f"更新字段：{','.join(changed)}", {"changed": changed})
        conn.commit()

    # 稳定画像：从**已经结构化好的** intent 机械派生（不多调一次模型、不从自由文本里猜）
    user = project_user(project_id)
    if user:
        learned = user_memory.learn(user, user_memory.facts_from_intent(intent), project_id)
        if learned:
            db.write_event(conn, project_id, "profile_learned",
                           "画像更新：" + "、".join(user_memory.PROFILE_LABELS.get(k, k) for k in learned),
                           {"keys": learned, "user_id": user})
            conn.commit()

    still_clarifying = compose_interview_reply(intent) is not None and not wants_results(user_text)
    if still_clarifying:
        transition(project_id, State.CLARIFY, "信息不足，继续追问")
    return {"project_id": project_id, "intent": intent, "history": history, "papers": [],
            "gaps": list(clarification_gaps(intent))[:3] if still_clarifying else [],
            "changed": changed, "state": get_state(project_id).value,
            "still_clarifying": still_clarifying}


def _diagnose(intent: ResearchIntent, **kv: Any) -> None:
    """检索路径的诊断读数一律写进 extra 并**落库**。

    引擎一直在往内存里的 intent.extra 写 query_variants / provider_outage，但这条路径上
    没有任何一处调 save_intent，于是探针 load_intent 回来永远是 {}——「产品吞了结果」
    因此完全不可观测（轮 105 的 g69b 一份读数都拿不到就是这个原因）。
    诊断本身不许成为新的静默失败点，所以整体包 except。
    """
    try:
        extra = dict(intent.extra or {})
        extra.update(kv)
        intent.extra = extra
        save_intent(intent)
    except Exception:  # noqa: BLE001 — 诊断写不进去不能影响回复
        pass


NO_SEARCH_NOTE = "本轮没有文献依据：你在这条消息上勾掉了检索，下面这些只按你已说过的信息推。"

# 「这一步会做什么」的唯一出处：SSE 的 step 事件和 composer 发送前的清单都读这一份。
# 让前端抄一份常量就是第二个真相，后端加一步它不会跟着变（B1 计数条立的同一条规矩）。
# tool 填的是真能 grep 到的模块路径——用户能勾掉的那颗必须对得上代码里真有的东西。
CHAT_STEPS: list[dict[str, Any]] = [
    {"id": "read", "label": "读你这句话，顺便翻相关记忆",
     "tool": "companion.memory.retriever.retrieve", "optional": False},
    {"id": "search", "label": "检索文献（Crossref / OpenAlex / arXiv / Semantic Scholar）",
     "tool": "companion.literature.engine.search_for_intent", "optional": True,
     "decline_label": "本轮不检索"},
    {"id": "write", "label": "组织回复",
     "tool": "companion.conversation.mentor.ask_stream", "optional": False},
]


def step(step_id: str) -> dict[str, Any]:
    return next(s for s in CHAT_STEPS if s["id"] == step_id)


def run_search(turn: dict[str, Any], no_search: bool = False) -> dict[str, Any]:
    """信息够了才推进状态机；失败写一条带原因的 [系统] 消息，不再留空气泡。

    no_search=True 是**学生这一轮勾掉了检索**：一个外呼都不发、状态机一步都不走，
    并在这轮 turn 上留一个标记，让回复带上"本轮无文献依据"。
    🔴 这个标记故意只活在当轮、不进 intent.extra：它是"这一句不查"，不是"以后都别查"，
    落库会让下一轮在用户没同意的情况下继续不查（也不继续报"无依据"）。
    与"检索挂了"（search_error）也故意分开：把拒绝读成故障，等于替用户编造了一次事故。

    轮 40 试过"澄清轮也查一轮文献当地基"，n=36 盲评把它否掉了（19/36 对时 2.0 胜 16%，
    比不查的 33—39% 明显更差）：机制指标全往"更好看"走（每条具名术语 6.3 → 11.4、
    ≤150 字回复 0 条），评委却按"更像导师给的路子"判负——第一句话的学生要的是接下来做什么，
    不是一份书目，回复的篇幅被"这三篇为什么适合你"吃掉之后，可执行计划就变薄了。
    所以这里恢复"反问轮不检索"，并把结论留在原地别再踩。
    """
    if no_search:
        turn["search_declined"] = True
        turn["papers"] = []
        turn["gaps"] = []
        return turn
    project_id, intent = turn["project_id"], turn["intent"]
    if turn["still_clarifying"]:
        # 反问轮不检索不是错，不检索却一个字都不留才是错（轮 105 情景 3 就卡在这）
        _diagnose(intent, search_skipped="clarifying",
                  search_confidence=intent.recompute_confidence(),
                  search_missing=intent.missing_fields()[:5])
        return turn
    transition(project_id, State.FORMULATE, "把研究需求写成检索式")
    transition(project_id, State.SEARCH, "多源检索")
    papers: list[dict] = []
    stored = 0
    err = ""
    exc_type = ""
    ms: int | None = None
    try:
        from companion.literature.engine import search_for_intent
        from companion.memory import paper_memory
        # 只掐 search_for_intent 这一段：入库(save_many)是本地写库，混进来会让"检索变慢了"
        # 和"库变大了"两种成因在前台读成同一个数。炸在半路就没有读数，写 None 不写 0。
        started = time.perf_counter()
        papers = search_for_intent(intent, limit=8)
        ms = int((time.perf_counter() - started) * 1000)
        if papers:
            stored = paper_memory.save_many(project_id, papers, status="recommended")
    except Exception as exc:  # noqa: BLE001 — 检索失败不阻断对话
        add_message(project_id, "assistant",
                    f"[系统] 文献检索这一步暂时不可用（{str(exc)[:120]}），先按已有信息继续和你讨论。",
                    {"kind": "search_error", "error": str(exc)[:200]})
        # 「这轮没有相关论文」和「这轮根本查不了」必须分开：把它们混成同一个空列表，
        # 产品会在没有证据的情况下继续推荐，探针也会把限流当成内容质量读（本轮 A/B 真踩过）。
        err = str(exc)[:160]
        exc_type = type(exc).__name__
    # 三项读数无条件落库：引擎给回几篇 / 真进了库几篇 / 有没有炸。
    # returned>stored 是去重吃掉的；returned=0 而库里没有 search_error 是供给问题。
    # 这两件事以前在 intent 上完全看不出来。
    diag: dict[str, Any] = {"search_engine_returned": len(papers), "search_stored": stored,
                            "search_ms": ms}
    if err:
        diag["search_error"] = err
        diag["search_exc_type"] = exc_type
    _diagnose(intent, **diag)
    # 这一轮真发过一次检索（哪怕抛了也算发过：那五个数是真的读数，不是抄来的）
    turn["search_ran"] = True
    turn["papers"] = papers
    turn["gaps"] = []
    transition(project_id, State.SCREEN if papers else State.DELIVER,
               "候选论文待筛选" if papers else "这轮没有可交付的论文")
    turn["state"] = get_state(project_id).value
    return turn


def prepare_turn(user_text: str, project_id: str | None = None,
                 no_search: bool = False) -> dict[str, Any]:
    """非流式入口：准备上下文 + （必要时）检索。no_search=用户在 composer 上勾掉了检索。"""
    return run_search(prepare_context(user_text, project_id), no_search=no_search)


def _template_fallback(turn: dict[str, Any]) -> str:
    """LLM 不可用时的保底回复（既有模板 + [降级] 前缀，便于验证脚本一眼识别）。"""
    intent = turn["intent"]
    if turn["still_clarifying"]:
        return mentor.DEGRADE_PREFIX + (compose_interview_reply(intent) or "可以再多说一点你的想法吗？")
    query = intent.topic or intent.user_goal[:40] or "研究文献"
    return (mentor.DEGRADE_PREFIX + f"信息够了，我先按「{query}」给你第一轮文献（{len(turn['papers'])} 篇）。"
            "看到结果后告诉我哪些感兴趣、哪些不需要，我会继续收敛。")


def _mentor_kwargs(turn: dict[str, Any], user_text: str) -> dict[str, Any]:
    intent = turn["intent"]
    if "memory_items" not in turn:
        items_all = retriever.retrieve(turn["project_id"], intent.topic or "", k=8)
        blocked = [b for b in (intent.extra.get("memory_blocklist") or []) if b]
        now = "".join((user_text or "").split())

        def _just_said(m: dict[str, Any]) -> bool:
            """本轮这句在检索前就已入库，不滤掉的话它会被当成「你以前说过的话」注入并显示，
            既挤掉真正相关的旧记忆，又给模型递上「你之前说过」的假记忆素材。"""
            t = "".join(str(m.get("summary") or m.get("content") or "").split())
            return bool(now) and bool(t) and t in now

        items_all = [m for m in items_all
                     if not _just_said(m)
                     and not any(b in (m.get("summary") or m.get("content") or "")
                                 for b in blocked)][:5]
        turn["memory_items"] = items_all
    items = turn["memory_items"]
    memory_ctx = "\n".join(
        f"- [{m.get('kind') or m.get('role') or '?'}] {(m.get('summary') or m.get('content') or '')[:160]}"
        for m in items)
    # 台账先按学生这句话改状态，再决定这一轮要不要提它：顺序反了就会拿"已完成"的事去追问。
    # 🔴 一轮里 _mentor_kwargs 会被调用**两次**（正文一次、repair 重采样再一次），不缓存的话
    #    同一句"基线跑完了"能结掉两条台账——那是把学生的一句话当成两句用（轮 169 自查发现）。
    if "followup_updates" not in turn:
        turn["followup_updates"] = commitments.apply_student_report(turn["project_id"], user_text)
        # 🔴 g181（40 对 A/B，带 false_completion 硬闸门的 build）把这条注入判"不留下"：
        #   收益未证明（跟进率 B 56.2% vs A 53.1%，CI [-21.9,+28.1]，p=0.500），
        #   而 S6 护栏开火：闸门开火总数 B 23 > A 16（多问句、少具体量）⇒ 按预登记判线
        #   "B 明显更高 ⇒ 不留下"。所以**默认不再把清单端进提示词**。
        #   台账本身照记、学生回报照结、跟进卡照旧显示（那三件没有被动到，也没有测到伤害）——
        #   被关掉的只是"替模型决定这一轮要谈哪件事"这一腿。`open_items` 仍要传，
        #   因为 false_completion 那道硬闸门靠它才知道"这句是不是在替学生宣布完成"。
        turn["followup_ctx"] = commitments.followup_block(turn["project_id"]) if INJECT_FOLLOWUP else ""
        # 闸门要看这份未结项清单才知道"这句是不是在替学生宣布完成"（g176 实测 4/32 冒领）
        turn["open_items"] = commitments.open_items(turn["project_id"])
    user = project_user(turn["project_id"])
    turn["profile_ctx"] = user_memory.profile_block(user, except_project=turn["project_id"]) if user else ""
    return {"user_text": user_text, "intent": intent, "history": turn["history"],
            "memory_ctx": memory_ctx, "papers": turn["papers"], "gaps": turn["gaps"],
            "followup_ctx": turn["followup_ctx"], "profile_ctx": turn["profile_ctx"],
            "open_items": turn.get("open_items") or []}


def _suggestions(turn: dict[str, Any], reply: str, user_text: str) -> list[dict[str, str]]:
    """优先用 LLM 生成的、与本轮内容绑定的追问建议；拿不到就回落到规则建议。"""
    gen = mentor.followups(user_text=user_text, intent=turn["intent"], reply=reply,
                           papers=turn["papers"])
    if gen:
        return [{"label": s, "say": s} for s in gen]
    return [s for s in suggestions_for(turn["intent"], searched=bool(turn["papers"])) if s["say"]]


def _used_memory(turn: dict[str, Any]) -> list[dict[str, Any]]:
    """把本轮真正注入模型的记忆列出来，让"它记得我"在界面上可查（对标 ChatGPT Memory）。"""
    return [{"source": m.get("kind") or m.get("role") or "?",     # 技术名：「不再引用」等逻辑按它匹配
             "tag": episodic_memory.source_tag(m.get("kind") or "", m.get("role") or ""),
             "event_id": m.get("id"),     # 事件行才有；聊天行没有 ⇒ None（不许编一个）
             "text": (m.get("summary") or m.get("content") or "").strip()[:160],
             "score": m.get("score")} for m in turn.get("memory_items", []) if m][:5]


def _feedback_effects(turn: dict[str, Any]) -> list[str]:
    """用户的收藏/排除怎样改变了这一轮结果——把隐藏的排序影响说给用户听。"""
    out = []
    for p in turn.get("papers", []):
        for r in p.get("recommendation_reason") or []:
            if "降权" in r or "收藏" in r or "同一条路线" in r:
                out.append(f"《{(p.get('title') or '')[:36]}》：{r}")
    return out[:6]


# 只有「这一轮真发过检索」才存在的五个读数；与下面那两颗项目级计数分开对待。
ROUND_KEYS = ("search_engine_returned", "search_stored", "off_topic_dropped",
              "query_variants", "search_ms")


def evidence_basis(turn: dict[str, Any]) -> dict[str, Any]:
    """对话区顶部那条「证据底座」——这一轮的回答踩在多少料上，每个数都由后端算。

    None = 程序这一轮确实没有这个数；0 = 有，且确实是零。两者在前台必须长得不一样，
    否则"没查"会被读成"查了但什么都没有"（对标 undermind 的计数条，U2）。
    """
    extra = turn["intent"].extra or {}
    from companion.memory import paper_memory
    lib = paper_memory.library_counts(turn["project_id"])
    # 🔴 轮 199：这一轮没查（用户勾掉、或还在反问）时，extra 里躺着的是**上一轮**的读数。
    # 直接抄给前台，会让「本轮没有文献依据」与「引擎返回 2 · 入库 2」出现在同一屏上。
    return {
        **{k: (extra.get(k) if turn.get("search_ran") else None) for k in ROUND_KEYS},
        "library_pdfs": lib["pdfs"],
        "library_abstracts": lib["abstracts"],
    }


def finalize_turn(turn: dict[str, Any], reply: str, *, degraded: bool, user_text: str = "") -> dict[str, Any]:
    add_message(turn["project_id"], "assistant", reply,
                {"kind": "clarify" if turn["still_clarifying"] else "deliver",
                 "changed": turn["changed"], "llm": not degraded,
                 "paper_count": len(turn["papers"])})
    # 轮 230 实测：线上 `research_intents` 往回 12 行里带 `gate_fires` 的 **0 行**——闸门台账（轮 180）
    # 与删除链记账（轮 216）都只写在内存里的 intent.extra，而这条回复路径上原先没人再调 save_intent，
    # 于是"这一轮到底哪条规则开过火"事后在线上答不出。诊断随回复一起落库，写不进不能影响回复。
    try:
        save_intent(turn["intent"])
    except Exception:  # noqa: BLE001 — 台账写不进去不是回复失败
        pass
    out: dict[str, Any] = {"project_id": turn["project_id"], "state": turn["state"],
                           "reply": reply, "intent": turn["intent"].model_dump(),
                           "degraded": degraded,
                           "evidence_basis": evidence_basis(turn),
                           "used_memory": _used_memory(turn),
                           "chips": next_chips(turn["intent"]),
                           "suggestions": _suggestions(turn, reply, user_text)}
    led = commitments.stats(turn["project_id"])
    if led:
        out["commitments"] = led
    user = project_user(turn["project_id"])
    if user:
        out["profile"] = user_memory.get_profile(user)
    if turn.get("followup_updates"):
        out["followup_updates"] = turn["followup_updates"]
    out["papers"] = turn["papers"]      # 澄清轮也带：回复里引了哪几篇，界面就要能点开哪几篇
    # 「你勾掉了检索」挂在**这一条回复**上，与 search_error（检索挂了）分列两个键：
    # 合成一条会把"用户不让查"演成"系统查不动"，那是替用户编造一次事故。
    if turn.get("search_declined"):
        out["search_note"] = NO_SEARCH_NOTE
    if turn["still_clarifying"]:
        out["gaps"] = turn["gaps"]
    else:
        effects = _feedback_effects(turn)
        if effects:
            out["feedback_effects"] = effects
        extra = turn["intent"].extra or {}
        if extra.get("low_relevance"):
            out["low_relevance"] = True
        if extra.get("off_topic_dropped"):
            out["off_topic_dropped"] = extra["off_topic_dropped"]
        if turn["intent"].extra.get("query_fallback"):
            out["query_fallback"] = True
        if turn["intent"].extra.get("search_error"):
            out["search_error"] = turn["intent"].extra["search_error"]
    return out


def handle_message(user_text: str, project_id: str | None = None,
                   no_search: bool = False) -> dict[str, Any]:
    """用户一句话 → 推进状态机 → 由 LLM 生成导师式回复。"""
    turn = prepare_turn(user_text, project_id, no_search=no_search)
    reply, degraded = mentor.ask(**_mentor_kwargs(turn, user_text))
    if degraded:
        reply = _template_fallback(turn)
    return finalize_turn(turn, reply, degraded=degraded, user_text=user_text)


def meta_event(turn: dict[str, Any]) -> dict[str, Any]:
    it = turn["intent"]
    return {"project_id": turn["project_id"], "state": turn["state"],
            "intent": it.model_dump(), "gaps": turn["gaps"],
            "evidence_basis": evidence_basis(turn),
            # 右栏「研究进度」卡要画的那条放行线：随阶段/事实数变，所以给**当前适用的值**而不是常量；
            # null = 事实已经够 6 项，本来就不该拿线拦人。前端只显示，不自己定数。
            "ready_threshold": search_threshold(it), "gap_labels": gap_labels(turn["gaps"]),
            # 阶段中文名也在后端有一张表（model.RESEARCH_STAGES）；前端自己抄一份就是第二个出处。
            "stage_labels": RESEARCH_STAGES,
            "papers": turn["papers"], "still_clarifying": turn["still_clarifying"],
            "used_memory": _used_memory(turn),
            "chips": next_chips(it),
            "suggestions": suggestions_for(it, searched=bool(turn["papers"])),
            "commitments": commitments.stats(turn["project_id"])}


async def stream_reply(turn: dict[str, Any], user_text: str):
    """流式吐字并持久化；客户端中途断开也要把已生成的部分落库，不留半截空会话。

    产出 (event, payload) 二元组，由 API 层序列化成 SSE。步骤事件由 API 层发（它才知道何时开始检索）。
    """
    pieces: list[str] = []
    sink: dict[str, Any] = {}
    try:
        async for delta in mentor.ask_stream(**_mentor_kwargs(turn, user_text), plan_sink=sink):
            pieces.append(delta)
            yield ("delta", {"text": delta})
    except Exception as exc:  # noqa: BLE001 — 流式失败也要给出可用回复
        turn_error = str(exc)[:200]
        fallback = _template_fallback(turn)
        yield ("delta", {"text": fallback})
        pieces = [fallback]
        streamed = mentor.clean("".join(pieces))
        reply = mentor.repair(streamed, plan=sink.get("plan"), **_mentor_kwargs(turn, user_text)) or fallback
        result = finalize_turn(turn, reply, degraded=True, user_text=user_text)
        result["llm_error"] = turn_error
        yield ("done", result)
        return
    except BaseException:      # GeneratorExit / 取消：用户点了「停止生成」
        partial = mentor.clean("".join(pieces))
        if partial:
            finalize_turn(turn, partial + " …（已停止）", degraded=False, user_text=user_text)
        raise

    streamed = mentor.clean("".join(pieces))
    reply = mentor.repair(streamed, plan=sink.get("plan"), **_mentor_kwargs(turn, user_text)) \
        or _template_fallback(turn)
    if reply != streamed:      # 硬闸门改过内容 → amend 覆盖前端已吐的字
        yield ("amend", {"reply": reply,
                         "removed_titles": mentor.unverified_titles(streamed, turn["papers"]),
                         "question_count": mentor.question_count(streamed)})
    yield ("done", finalize_turn(turn, reply, degraded=False, user_text=user_text))
