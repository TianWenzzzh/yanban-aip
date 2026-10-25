"""对话灵魂层：把上下文交给 LLM 生成「导师式」回复。

规则提取器此后只负责更新 ResearchIntent（结构化状态），不再决定回复文本；
LLM 失败时由调用方回落 interview_agent 的模板并加 [降级] 前缀，保证不崩且可被验证脚本识别。
"""
from __future__ import annotations

import re
from typing import Any, AsyncIterator, Iterable

from llm import LLMError, chat, chat_stream, run_async

DEGRADE_PREFIX = "[降级] "
MAX_HISTORY = 6
MAX_PAPER_CANDIDATES = 6
TITLE_RE = re.compile(r"《[^》]{4,200}》")

# 提示词约束挡不住所有幻觉，所以论文标题另做一道硬校验：回复里出现的《标题》
# 必须能在本轮真实检索结果里找到，否则整句剔除（竞赛诚信红线：元数据只能来自 Provider）。
CORRECTIVE = "\n\n注意：你上一版提到了并未提供给我们的论文标题。请重写，只写上面给过的论文；没给就一篇也别提。"
CORRECTIVE_Q = "\n\n注意：你上一版问了不止一个问题。请重写，整条回复只保留最该问的那一个问题，全文只出现一个问号。"
CORRECTIVE_S = ("\n\n注意：你上一版只提了问题、或只承诺「我这就去查」，当下没给学生任何能用上的东西。"
                "请重写：先给 3—5 条此刻就能做、彼此独立的建议（点名方法、数据或下一步），要问就最后只问一句。")
CORRECTIVE_N = ("\n\n注意：你上一版的建议几乎没有一处数量、时长或指标，学生照着做不了。"
                "请重写：至少两条建议各带一个可核对的量（几篇/几周/多少条数据/什么指标的百分之几），"
                "没有依据的数字不要编。")
CORRECTIVE_L = ("\n\n注意：你上一版长得收不住（超过 1200 字），学生读不完，也拖慢了整段回复。"
                "请压到 700 字以内：只留最可执行的那几条，删掉铺垫、复述与重复，不要新增内容。")
CORRECTIVE_P = ("\n\n注意：你上一版完全没按给定的动作清单来写，学生拿不到规划好的那几件事。"
                "请重写：把清单里每一条动作都写成一句完整的话，保留它带的工具/数据集/指标、量，"
                "以及「做完的判据」；一条都不许丢，也不许另外加没在清单里的动作。")
CORRECTIVE_A = ("\n\n注意：你上一版给的组织线里有类别互相包含或父子并列，这种分类会直接误导综述骨架。"
                "请只改分类结构：同一条线里的类别必须两两互斥（一个父集不能与它的子集并列），"
                "每个类别配一句能在题录或摘要里核对的归入判据，并写清这条线在什么情况下不适用。")
CORRECTIVE_E = ("\n\n注意：你上一版把学生自己给出的数字与专名一个都没用进去。"
                "它们就是方案的边界，重写时逐条接住（照 ta 的说法写，不要改成约数、不要四舍五入），"
                "但也不要靠复述这些词凑长度，不得新增没出现过的数字。漏掉的是：")
# 引用密度闸门（g96 前测：线上 6 条"手里有论文卡"的回复里 2 条零引用痕迹 = 33%，梯度成立才开这条）
CORRECTIVE_K = (
    "\n\n注意：你把学生**尚未回报完成**的那几件事说成了已经完成。一件都别替他宣布——"
    "只许问「那件事做到哪一步了」，或者往下给下一步；学生没说过做完了，你就当它没做完。")
CORRECTIVE_C = ("\n\n注意：你上一版一处来源都没挂，而这一轮上面明明给了论文卡。"
                "重写时至少两处把给出的判断落到具体论文上（句末写 [编号]，编号取上面论文卡的序号）；"
                "宁可少说一句判断，也不要把没挂来源的话当结论写。")
CORRECTIVE_O = ("\n\n注意：学生这轮明确说了要什么形状（一句话／不要展开／就给几条），你上一版超出了那个形状。"
                "请重写：按 ta 说的形状给——没点条数就压到两句以内，点了条数就正好不超过那条数。"
                "只删铺垫、复述和重复，已经写进去的依据、角标、学生给过的量一个都不许丢，也不许新增没出现过的数字。")



def question_count(text: str) -> int:
    return (text or "").count("？") + (text or "").count("?")


# 首轮没有任何历史对话，却说「你之前提到」= 假称记得。线上实测过 1/3 概率（probe_memory_phrasing.py），
# 提示词点不动，所以这里既重采样、也做确定性改写兜底。动词一律长式优先，免得只匹配半截留下残字。
_VERB = r"(?:提到过|说过|讲到|说到|提到|讲过|说|讲)"
FALSE_MEMORY_RE = re.compile(
    rf"(?:你?之前(?:曾)?{_VERB}|上次(?:你)?{_VERB}|(?:我)?记得你{_VERB}|我们?之前(?:讨论过|聊过|谈过|讨论|聊|谈))")
CORRECTIVE_M = ("\n\n注意：这是这位学生的第一句话，之前没有任何对话记录。"                "请把「你之前提到」「我记得你说过」这类说法改成「你说」，不要假称记得。")


def false_memory(text: str, history: list[dict] | None) -> list[str]:
    if history:
        return []
    return FALSE_MEMORY_RE.findall(text or "")


def rewrite_false_memory(text: str) -> str:
    return FALSE_MEMORY_RE.sub("你说", text or "")


# 配对盲评（n=6，2.0 胜 1 负 5）实测：输的三例都是"只提问/只承诺去查，当下什么也没给"。
# SYSTEM 里明写「只提问不给东西＝失败」，但这条从来没有闸门兜着——提示词挡不住，就得写代码。
# 只认"能落地"的建议句式；"给你一些建议"这种承诺不算实质内容（评测里的原句就是这种）。
SUBSTANCE_RE = re.compile(r"(建议你先|建议先|建议你|我的建议|可以先|不妨|通常|比如|例如|第一步|优先"
                          r"|不适合|适合|因为|换成|先看)")
PROMISE_ONLY_RE = re.compile(r"(我这就去|我现在就去|我去查|稍等|马上给|接下来我会|我会先)")


def has_substance(text: str, papers: list[dict] | None = None) -> bool:
    """当下能用上的东西：一条具体判断/建议，或至少内联引用了一篇真论文。"""
    t = (text or "").strip()
    if not t:
        return False
    if SUBSTANCE_RE.search(t):
        return True
    return bool(re.search(r"\[\d{1,2}\]", t) or re.search(r"(^|\s)1[.、]\s*\S", t))


QUANT_RE = re.compile(r"\d|[一二三四五六七八九十]+(?:个|篇|条|次|周|天|小时|个月|步|成)"
                      r"|每(?:周|天|次|篇)|前(?:几|十|五)|百分之")


ADVISORY_RE = re.compile(r"(建议|不妨|可以先|可以试着|应该|优先|第一步|第[一二三四五六]步|首先|其次|再次|然后|接着"
                         r"|比如|例如|换成|适合|因为|先看|考虑|试着|我的判断|你可以|我建议你)")
_ITEM_SPLIT = re.compile(r"[。！？!?；;\n]|(?=首先|其次|再次|然后|接着|最后|第[一二三四五六]步|\d[.、)])")


def items_of(text: str) -> list[str]:
    """按"一条能做的事"切：句子结尾、编号、以及"首先/其次"这类序数连接词都算分界。

    只按逗号切会把"建议先只做 URL 模态，比如用 12k 条数据"劈成两截——
    数量落在后一截，前一截就被判成空话（实测把 474 字的好回复误判违规）。
    """
    return [s.strip(" *-\t") for s in _ITEM_SPLIT.split(text or "") if len(s.strip(" *-\t")) >= 8]


def thin_quant(text: str) -> bool:
    """凑不出"两条带数量的建议"就判虚。

    长度盲评实测：2.0 每条回复平均只有 2.6 处数字，裸问有 8.2 处——我们说"用什么方法"，
    不说"多大量、跑多久、读到什么程度"，学生照着做不了。而 `no_substance` 只认句式，
    "建议你先把综述读一遍"这种一句到底的回复两道闸门都放过（18 条里 10 条 ≤150 字）。
    """
    advice = [c for c in items_of(text) if ADVISORY_RE.search(c)]
    if not advice:
        return False          # 连建议句都没有 → 归 no_substance 那套管
    return len(advice) < 3 or sum(1 for c in advice if QUANT_RE.search(c)) < 2


def no_substance(text: str, papers: list[dict] | None = None) -> bool:
    """只承诺去查、或整段只剩一个问题 → 判"没给东西"。"""
    t = (text or "").strip()
    if has_substance(t, papers):
        return False
    return bool(PROMISE_ONLY_RE.search(t)) or question_count(t) >= 1 or len(t) < 90


SYSTEM = """你是「研伴」，一位温和但判断力很强的研究生导师，正在和一位学生一对一聊 ta 的研究想法。
下面会给你学生的研究状态快照、相关记忆、最近对话原文，以及（有时）一轮真实检索得到的候选论文。

必须做到：
（这一条优先于下面各条：ta 这轮要是自己说了要什么形状——一句话、不要展开、就给三条、用表格——就以 ta 说的为准，条数与字数都让位给它；但依据、角标、诚实性不让位，也不许拿「照 ta 的话办」当借口交空话。）
1. 第一句先接住学生这轮话里的具体信息，用 ta 自己的词复述 ta 的处境，让 ta 觉得被听懂了。禁止万能开场白、禁止自我介绍、禁止把每轮都当成对话开头。谈「你之前说过什么」时，只允许引用下面「最近对话原文」与「相关记忆」里逐字出现过的内容；那里没有的，一律当作学生没说过——绝不猜测、绝不编造。「最近对话原文」为空时，这就是 ta 的第一句话，只能写"你说"，写"之前/上次/记得"都是编造记忆。若下面给了「关于 ta 的更早记忆」，优先从里面挑一条自然地用起来：那是 ta 真说过、而你也确实记得的事。
2. 整条回复最多问一个问题，全文最多出现一个问号，并且把问题自然嵌在句子里。**只在缺了它就真没法往下走时才问**——这一轮已经给出可执行计划的，结尾不必再挂一个问题。不要用"先跟你对齐两件事""请问……；……？"这种问卷腔，不要用分号并列两问，不要列问题清单。
3. 除了提问，必须先给 **3—5 条**此刻就能做、彼此独立的具体东西（裸问赢我们的主要就是这里：它一次给 4—8 个互不重复的办法，我们常停在 2 条就收笔）。每条都要落到"能立刻动手"的粒度：说清做什么 + 用什么（工具/数据集/指标/文献名）+ 大概多少量或多久。只说"多读读文献""打好基础""注意积累"这种没有对象、没有量的句子，不算一条。只提问、或只说"我这就去查"而不给任何当下能用的东西＝失败。给了【本轮检索统计】时，至少一条建议要引用其中的真实数字（命中几篇、年跨度、最高被引、开放获取几篇）——但**只能引用那个块里给到的数**，自己不得推算或编造统计值。
4. 给了候选论文时，把最相关的至多 3 篇用 "1. 2. 3." 编号写进正文，各配一句"为什么适合你"：这句必须用上该篇「摘要要点」里给出的具体信息（它用的方法、数据规模、指标或场景），并结合 ta 的水平、目标、约束说清它凭什么排得上——只说"这篇很相关""这篇很经典"不算。每篇末尾紧跟一个方括号角标，里面只写该篇在上面候选列表里的阿拉伯数字编号（列表第 2 篇就写 [2]；不要写字母 n，不要把标题抄进方括号，界面靠它跳转到对应论文卡）。**只能引用给你的条目里的标题，一个字都不要改写**；「摘要要点」里没有的数字和结论一律不许出现。没给你候选论文时，绝对不要提任何论文标题或篇数，可以说"我这就去查几篇"。绝不编造论文、作者、DOI、年份、引用数。
5. 全中文，口语但准确；450—700 字（内联论文时可到 850 字）。空话 700 字不如实在的 450 字，但**不要为了显得干脆而砍掉能用的细节**：宁可一条建议多写一句"用什么、做到什么程度"，也不要少写。不用 emoji，不用 markdown 标题，不加免责声明。
6. 你在扮演一位真实存在的导师，不要提到自己是 AI 或模型。
"""

# 提示词字数预算（450—700，内联论文 850）需要 max_tokens 兜得住：中文约 1 字 ≈ 1 token，
# 原来 600 会在写到第 3 条建议时截断，看起来就像"只给了两条"。
REPLY_MAX_TOKENS = 1300
# 长度**保险阀**，不是风格旋钮：提示词自陈 450—700（内联论文 850），但线上实测到过 1151/1171 字，
# 而 n=161 重放里的胜局长度上到 925 —— 阀定在 700 会切掉已经赢过的那一簇，所以取 1200：
# 只拦失控输出（整段延迟与前端可读性都由它引起），不干预正常篇幅。触发率由
# `输出/scripts/probe_gate_yield_v2.py` 体检，误伤率不为 0 就该再往上抬。
MAX_REPLY_CHARS = 1200
# 一轮 450—700 字的回复，线上实测整段 27—34s（`probe_stream_latency.py`，2026-09-21 21:21）。
# 原来的 45s/30s 超时是 150—350 字那档留下的，预算抬上去之后已经贴着上限，慢一点就被掐成半条回复；
# 宁等不截断——流式首字不受影响。
REPLY_TIMEOUT = 90.0


def _get(obj: Any, key: str) -> Any:
    return obj.get(key) if isinstance(obj, dict) else getattr(obj, key, None)


def _join(parts: Iterable[Any], sep: str = "、") -> str:
    return sep.join(str(p) for p in parts if p not in (None, "", [], {}))


def intent_snapshot(intent: Any) -> str:
    """把结构化意图写成模型读得懂的中文快照（空字段不占篇幅）。"""
    lines: list[str] = []

    def add(label: str, value: Any) -> None:
        text = value if isinstance(value, str) else _join(value if isinstance(value, list) else [value])
        if text and text.strip():
            lines.append(f"{label}：{text}")

    add("研究主题", _get(intent, "topic"))
    add("想解决的问题", _get(intent, "user_goal"))
    add("科研阶段", _get(intent, "research_stage"))
    bg = _get(intent, "user_background")
    for key, label in (("education_level", "学历"), ("major", "专业"), ("programming_level", "编程"),
                       ("ml_level", "机器学习"), ("dl_level", "深度学习"), ("llm_level", "大模型"),
                       ("paper_reading_level", "论文阅读")):
        add(f"水平·{label}", _get(bg, key))
    pg = _get(intent, "publication_goal")
    add("发表目标", _join([_get(pg, "type"), _get(pg, "level"), _get(pg, "timeline")], " "))
    pp = _get(intent, "paper_preferences")
    add("文献偏好", _join([
        f"{_get(pp, 'year_from')}年起" if _get(pp, "year_from") else "",
        f"语种 {'/'.join(_get(pp, 'language') or [])}" if _get(pp, "language") else "",
        "偏好开放获取" if _get(pp, "open_access_preferred") else "",
        "偏好可复现" if _get(pp, "reproducibility_preferred") else "",
    ]))
    rc = _get(intent, "research_constraints")
    add("现实约束", _join([_get(rc, "compute"), _get(rc, "dataset"),
                          _get(rc, "coding_difficulty"), _get(rc, "experiment_time")]))
    add("当前关注的方向", _get(intent, "current_focus"))
    add("已被否掉的方向", _get(intent, "rejected_directions"))
    add("还没答上来的问题", _get(intent, "unresolved_questions"))
    conf = _get(intent, "confidence")
    if conf is not None:
        lines.append(f"我对这次任务的理解度：{float(conf):.2f}（1.00 为充分）")
    return "\n".join(lines)


def history_block(messages: list[dict]) -> str:
    """最近 N 轮原文（含本轮之前），让学生感到"它记得我上一句"。"""
    role_cn = {"user": "学生", "assistant": "研伴"}
    tail = [m for m in messages if m.get("content")][-MAX_HISTORY:]
    return "\n".join(f"{role_cn.get(m.get('role'), m.get('role'))}：{(m.get('content') or '')[:300]}"
                     for m in tail)


def search_stats_block(papers: list[dict]) -> str:
    """把本轮真实检索结果的**可核对数字**算给模型看。

    长度盲评里唯一没追平裸问的机制指标就是数字密度（4.3 vs 8.1）：模型写得出
    "这篇很经典"，却给不出"这 8 篇里 5 篇是 2023 年后的、最高被引 312"——因为没人把
    这些数算给它。这里全部由 Provider 返回的元数据现算，模型只能引用、不能自造，
    所以既补上具体度，又不碰"论文元数据不得由 LLM 生成"这条红线。
    """
    ps = [p for p in (papers or []) if (p.get("title") or "").strip()]
    if not ps:
        return ""
    years = sorted(y for y in (p.get("year") for p in ps) if isinstance(y, int))
    cits = [(int(p.get("citation_count") or 0), p["title"].strip()) for p in ps]
    hi, hi_title = max(cits, key=lambda t: t[0])
    lines = [f"本轮真实检索命中 {len(ps)} 篇"]
    if years:
        lines.append(f"出版年跨度 {years[0]}—{years[-1]}，中位出版年 {years[len(years) // 2]}")
    lines.append(f"最高被引 {hi}（《{hi_title[:38]}》）；被引 ≥50 的 {sum(1 for c, _ in cits if c >= 50)} 篇")
    oa = sum(1 for p in ps if p.get("open_access"))
    lines.append(f"Provider 标注为开放获取的 {oa} 篇")
    return f"{SEARCH_STATS_MARK}，引用数字只能从这里取，不得自造】\n" + "；".join(lines) + "。"


def grounded_facts_block(matrix: dict | None) -> str:
    """把**已过逐字依据校验**的对比表事实压成几行，交给回复当可引用的硬材料。

    对标顶级工具的差别不在文采而在"给的是算过的东西"：对比表每格都过了
    `_cell()` 的逐字比对（证据不是原文子串、或冒出原文没有的数字，整格直接判未知），
    所以这里只可能引用真事实。而且走 `peek_cached`，一次 LLM 调用都不多花。
    """
    rows = (matrix or {}).get("rows") or []
    cols = (matrix or {}).get("columns") or []
    lines: list[str] = []
    for r in rows[:3]:
        bits = [f"{label}＝{r['cells'][key]}" for key, label in cols
                if (r.get("cells") or {}).get(key, "") not in ("", "未知")]
        if not bits:
            continue
        ev = next((r["evidence"][k] for k, _ in cols
                   if (r.get("evidence") or {}).get(k)), "")
        title = (r.get("title") or "").strip()[:60]
        lines.append(f"- 《{title}》（{r.get('year') or '年份未知'}，依据：{r.get('basis') or '摘要'}）"
                     + "；".join(bits) + (f"。原文片段：「{ev[:80]}」" if ev else ""))
    if not lines:
        return ""
    return ("【已逐字核对过的本方向事实——只能引用这里的说法与数字，不得外推】\n" + "\n".join(lines))


def _cached_facts(intent: Any, papers: list[dict]) -> str:
    """只读对比表缓存；没缓存就返回空——绝不为一条回复去多花一次 LLM 调用。"""
    try:
        from companion.literature import matrix
        project_id = getattr(intent, "project_id", "") or ""
        if not project_id:
            return ""
        return grounded_facts_block(matrix.peek_cached(project_id, papers))
    except Exception:  # noqa: BLE001 — 硬材料是加分项，读不到就照常回复
        return ""


def quotable_numbers(paper: dict, *, limit: int = 2) -> str:
    """把摘要里**带数字的原句**单独递到模型眼前。

    为什么：主指标改成"引用接地率"之后，卡点变得很具体——摘要被截到 120 字时，
    里面的 12k / 0.94 F1 / 3 datasets 常常正好落在截断之后，模型看不见就只能自己编一个数，
    而编出来的数会被供应商的核验闸门删句。给它可核对的原句，同时解决了"说不出来"和"说错被删"。
    """
    body = str(paper.get("abstract") or paper.get("tldr") or "")
    # 句界必须"拉丁句点后跟空白"或中文句点，否则 0.94 / 12k 会被从小数点劈开（实测切出过 "0.；94 F1"）
    hits = [s.strip()[:140] for s in re.split(r"(?<=[.!?])\s+|(?<=[。！？])|\n", body)
            if re.search(r"\d", s) and len(s.strip()) >= 20][:limit]
    return "；".join(hits)


def papers_block(papers: list[dict]) -> str:
    lines = []
    for i, p in enumerate(papers[:MAX_PAPER_CANDIDATES], 1):
        title = (p.get("title") or "").strip()
        if not title:
            continue
        meta = _join([p.get("year"), p.get("venue"),
                      f"被引 {p.get('citation_count')}" if p.get("citation_count") else ""], "，")
        why = _join((p.get("recommendation_reason") or p.get("reasons") or [])[:3], "；")
        gist = (p.get("tldr") or "").strip() or re.sub(r"\s+", " ", (p.get("abstract") or "").strip())
        lines.append(f"{i}. {title}（{meta}）" + (f" 推荐理由：{why}" if why else "")
                     + (f"\n   摘要要点：{gist[:120]}" if gist else "")
                     + (f"\n   可核对的原文数字：{q}" if (q := quotable_numbers(p)) else ""))
    return "\n".join(lines)


def build_prompt(*, user_text: str, intent: Any, history: list[dict] | None = None,
                 memory_ctx: str = "", papers: list[dict] | None = None,
                 gaps: list[str] | None = None, plan: dict | None = None,
                 followup_ctx: str = "", profile_ctx: str = "") -> str:
    parts = [f"【这个学生目前的研究状态】\n{intent_snapshot(intent)}"]
    if profile_ctx:
        parts.append(profile_ctx)
    if memory_ctx:
        parts.append(f"【关于 ta 的更早记忆，可用于承接】\n{memory_ctx}")
    if followup_ctx:
        parts.append(followup_ctx)
    if history:
        parts.append(f"【最近对话原文（从旧到新）】\n{history_block(history)}")
    block = papers_block(papers or [])
    if block:
        parts.append(f"【本轮真实检索出的候选论文，只能引用这里的编号和标题】\n{block}")
        facts = _cached_facts(intent, papers or [])
        if facts:
            parts.append(facts)
        stats = search_stats_block(papers or [])
        if stats:
            parts.append(stats)
    if gaps:
        parts.append("【这一轮的硬性顺序】先给 2 条此刻就能做、且带数量或时限的建议，"
                     "最后才从下面这些缺口里挑最重要的一项提问（只能问一项）：\n"
                     + "\n".join(f"- {g}" for g in gaps[:3]))
    block = plan_block(plan)
    if block:
        parts.append(block)
    parts.append(f"【学生这轮说】\n{user_text}\n请按系统要求回复这位学生。")
    return "\n\n".join(parts)


PLAN_SYSTEM = "你在为一段导师回复做规划，不写给学生看的话。只输出 JSON，字段名一字不差。"
# 配额与预算必须同源：PLAN_BUDGET 既是提示词里写的"不超过多少字"，也是归一化时的截断长度，
# 还是测试断言的对象（配额换算出的 token 必须装得进 PLAN_MAX_TOKENS）。
PLAN_BUDGET = {"situation": 48, "do": 32, "with": 32, "dose": 20, "check": 36,
               "milestone": 40, "artifact": 56, "pitfall": 56,
               "pick": 20, "when": 36, "axis": 24, "name": 20, "test": 36,
               "relation": 18, "when_bad": 32, "question": 48}
PLAN_COUNTS = {"actions": 4, "milestones": 2, "options": 3, "axes": 2, "buckets": 3, "evidence": 3}
PLAN_KEY_OVERHEAD = 1.25      # 键名、引号与标点的额外体积


def plan_quota_chars() -> int:
    """模型写满配额时要产出的字数（含 JSON 键名开销）。"""
    b, c = PLAN_BUDGET, PLAN_COUNTS
    return int(round((
        b["situation"] + c["actions"] * (b["do"] + b["with"] + b["dose"] + b["check"])
        + c["milestones"] * b["milestone"] + b["artifact"] + b["pitfall"]
        + c["options"] * (b["pick"] + b["when"])
        + c["axes"] * (b["axis"] + c["buckets"] * (b["name"] + b["test"])
                       + b["relation"] + b["when_bad"])
        + c["evidence"] * 4 + b["question"]) * PLAN_KEY_OVERHEAD))


def plan_quota_tokens() -> float:
    """中文约 1.4 字/token，取保守一侧按 1.2 折算。"""
    return plan_quota_chars() / 1.2


# 曾经只有 700：配额算出来就装不下，模型写满就被截断 → chat_json 解析失败 →
# make_plan 返回 None → 线上表现就是"只有一半回复真的先规划"。溢出要被算出来，不能等它掉出来。
PLAN_MAX_TOKENS = 2000   # 必须覆盖最坏配额（测试守着）。实测：配额从 1790 砍到 995 字，规划耗时 20.8s→19.1s 几乎不动
                         # ——模型不贴着配额写，所以砍配额既省不到时间也不省 token，别再走这条路（见轮 86）
PLAN_TIMEOUT = 45.0   # 真因是配额溢出（见 plan_quota_tokens），超时只是次要嫌疑


def _plan_fail(intent: Any, why: str) -> None:
    """把"这次为什么没走到先规划再写"记进 intent.extra——44% 的计划产出率必须能归因，否则下一轮只能猜。"""
    try:
        intent.extra["plan_fail"] = why[:120]
    except Exception:  # noqa: BLE001 — 诊断字段写不进去不该影响回复
        pass


def note_plan(intent: Any, plan: dict | None) -> None:
    """把这一轮产出的行动清单记进承诺台账——下一轮才有东西可跟进。

    记不记不成事：台账写失败不能拖累这条回复，所以整体吞异常（与 _diagnose 同一口径）。
    """
    if not plan:
        return
    try:
        from companion.conversation import commitments
        commitments.record(getattr(intent, "project_id", "") or "", plan)
    except Exception:  # noqa: BLE001 — 台账写不进去不能影响回复
        pass


def strip_chain(text: str, *, intent: Any, papers: list[dict], user_text: str,
                history: list[dict] | None = None, memory_ctx: str = "", gaps: list[str] | None = None,
                plan: dict | None = None, followup_ctx: str = "", profile_ctx: str = "") -> str:
    """确定性删除尾链：重采样挡不住的东西在这里剪掉，每一步删了多少字各记一笔。

    为什么从 `repair()` 里抽出来：整条链一次模型调用都不做，抽出来才不必拿桩去测它——
    而"闸门到底有没有删过"这个问题，只有这条链自己带读数才答得上来（见 `note_deletions`）。
    顺序与抽取前逐字相同：先改指角标，再 假标题→无依据数字→越界角标→空编号；
    零证据那一轮再补 伪造检索统计→无依据规模（后者必须拿到喂给模型的上下文当"已知数"，
    否则它复述我们自己给的数会被删空整条回复）。
    """
    from functools import partial

    from companion.literature import evidence
    # 先把"论文是真的、这句不是它说的"的角标改指到撑得住的那篇，再删越界角标
    text = evidence.renumber_citations(text, papers)[0]
    steps = [partial(strip_unverified, papers=papers),
             partial(strip_ungrounded_numbers, papers=papers),
             partial(strip_dangling_citations, papers=papers),
             partial(strip_empty_numbering)]
    # 零证据那一轮：编造的具体规模（"2,708 个节点"）不在 RESULT_RE 的语境里，上面那几道看不见它。
    if not papers:
        steps.append(partial(strip_fabricated_search_stats, papers=papers))
        steps.append(partial(strip_ungrounded_scale, papers=papers, user_text=user_text,
                             history=history,
                             context=build_prompt(user_text=user_text, intent=intent, history=history,
                                                  memory_ctx=memory_ctx, papers=papers, gaps=gaps,
                                                  plan=plan, followup_ctx=followup_ctx,
                                                  profile_ctx=profile_ctx)))
    drops = {}
    for step in steps:
        before = text
        text = step(text)
        if text != before:      # 只记真动过手的；一条没动也要写空字典，见 note_deletions
            drops[step.func.__name__] = len(before) - len(text)
    note_deletions(intent, drops)
    return text


def note_deletions(intent: Any, drops: dict) -> None:
    """记下"确定性删除这一趟真删了什么、各删掉多少字"。

    为什么单独记：重采样类闸门有 `gate_fires`，删除类没有 ⇒ 屏上"没有那个块"既可能是模型没写、
    也可能是被删掉，事后永远分不清（未决 19 后半截与未决 20 撞的是同一堵墙）。
    `intent.extra` 随 `model_dump()` 进 meta 事件，所以这条读数线上直接读得到，不需要新管道。
    空字典也要写：读的人要分得清"这轮一条没删"与"这条路没记账"。
    """
    try:
        extra = getattr(intent, "extra", None)
        if isinstance(extra, dict):
            extra["clean_deletions"] = dict(drops)
    except Exception:      # 读数记不上不能拖累回复
        pass


def note_gates(intent: Any, rounds: int, by_rule: dict) -> None:
    """记下"这条回复被闸门退回过几次、各是哪条规则"——没有它，重采样的代价只能靠现场盯着看。

    为什么要记：g185 的分段读数里，正文最后一滴到 `done` 之间的"收尾段"是一条不可忽视的独立成本
    （具体几条开火、多少秒以那份证据文件为准），而 `repair()` 只在本地算 violations，
    算完就丢 ⇒ 产品事后回答不了"哪条规则在反复开火"。
    只多记两件事（轮数与规则计数），不改任何判定；`rounds=0` 也要记，
    不然读的人分不清"没开火"与"这条路没记"。
    """
    try:
        extra = getattr(intent, "extra", None)
        if isinstance(extra, dict):
            extra["gate_fires"] = {"rounds": rounds, "by_rule": dict(by_rule)}
    except Exception:  # noqa: BLE001 — 诊断记不上不能影响回复
        pass


def make_plan(*, user_text: str, intent: Any, history: list[dict] | None = None,
              memory_ctx: str = "", papers: list[dict] | None = None,
              followup_ctx: str = "", profile_ctx: str = "") -> dict | None:
    """先产出一份机器可读的回复计划：处境复述 + 3—5 条各带「做什么/用什么/多少量/完成判据」的动作。

    两条一手依据：①深度研究系统的消融里"最终合成阶段"是唯一不可去的组件
    （Improving and Evaluating Open Deep Research Agents, 2025：去掉结构化合成 25%→0%）；
    ②Chain-of-Abstraction（2024）先复述用户已声明的约束再作答，平均 +6% 且更快。
    盲评败因词频最集中的一条是"具体可执行"，而"完成判据"是它最接近机器检查的形式。
    MOCK 或失败返回 None → 整条链路退回单次生成，不阻塞回复。
    """
    from llm import LLM_MOCK, chat_json

    if LLM_MOCK:
        return None
    try:
        data = run_async(chat_json(plan_prompt(user_text=user_text, intent=intent, history=history,
                                               memory_ctx=memory_ctx, papers=papers,
                                               followup_ctx=followup_ctx,
                                               profile_ctx=profile_ctx),
                                   system=PLAN_SYSTEM, temperature=0.2,
                                   max_tokens=PLAN_MAX_TOKENS, retries=1, timeout=PLAN_TIMEOUT))
    except LLMError as exc:
        _plan_fail(intent, f"通道:{type(exc).__name__} {exc}")
        return None
    plan = apply_ask_policy(normalize_plan(data, len(papers or [])), user_text)
    if plan is None:
        acts = data.get("actions") if isinstance(data, dict) else None
        shape = f"动作数 {len(acts)}" if isinstance(acts, list) else "actions 不是列表"
        _plan_fail(intent, f"结构不合规：{shape}；返回 {str(data)[:60]}")
    return plan


def plan_prompt(*, user_text: str, intent: Any, history: list[dict] | None = None,
                memory_ctx: str = "", papers: list[dict] | None = None,
                followup_ctx: str = "", profile_ctx: str = "") -> str:
    """规划阶段的提示词：只喂学生说过的、快照里的与真检索到的，不给任何自由发挥空间。"""
    parts = [f"学生这轮说：{user_text[:400]}", f"研究状态快照：\n{intent_snapshot(intent)}"]
    if history:
        parts.append("最近对话原文：\n" + history_block(history))
    if memory_ctx:
        parts.append("关于 ta 的更早记忆：" + memory_ctx[:400])
    if profile_ctx:
        parts.append(profile_ctx)
    if followup_ctx:
        parts.append(followup_ctx)
    cand = papers_block(papers or [])
    if cand:
        parts.append("可引用论文（只能用这里的编号与标题）：\n" + cand[:1500])
    b, c = PLAN_BUDGET, PLAN_COUNTS
    parts.append('只输出 JSON，每个字段都不得超过括号里的字数（超了会被截断、整份计划作废）：'
                 f'{{"situation": "用学生自己的词复述 ta 的处境，≤{b["situation"]}字，'
                 '不得加入 ta 没说过的信息",'
                 f' "actions": [{{"do": "要做的事 ≤{b["do"]}字", "with": "用什么工具/数据集/指标 '
                 f'≤{b["with"]}字", "dose": "多大量或多久 ≤{b["dose"]}字",'
                 f' "check": "做完的判据，一眼能核对 ≤{b["check"]}字"}}]，3 到 {c["actions"]} 条，彼此独立；'
                 f' "milestones": ["第 1 段：几个月做到什么 ≤{b["milestone"]}字"]，'
                 f'最多 {c["milestones"]} 段，按 ta 自己给的总时长切，一直排到投稿/开题/答辩那个节点；'
                 f' "artifact": "这一段留下什么可交付物（一张表/一份脚本/一节初稿）≤{b["artifact"]}字"；'
                 f' "options": [{{"pick": "候选名 ≤{b["pick"]}字", "when": "什么情况选它 ≤{b["when"]}字"}}]，'
                 f'学生问该用哪个时给 2—{c["options"]} 条，否则空数组；'
                 f' "pitfall": "这一轮最该避开的具体的坑 ≤{b["pitfall"]}字"；'
                 f' "axes": [{{"axis": "组织线名 ≤{b["axis"]}字（如按任务/按算法/按数据类型）",'
                 f' "buckets": [{{"name": "类别名 ≤{b["name"]}字",'
                 f' "test": "归进这类要核对到的一句话 ≤{b["test"]}字"}}]，每条线 ≤{c["buckets"]} 类且两两互斥'
                 '（子集不能与父集并列），'
                 f' "relation": "并列还是包含 ≤{b["relation"]}字",'
                 f' "when_bad": "何时不适用 ≤{b["when_bad"]}字"}}]，'
                 f'学生问该按什么线组织/怎么分类时最多给 {c["axes"]} 条候选线；其他情况给空数组；'
                 f' "evidence": [可引用的编号，最多 {c["evidence"]} 个，没有合适的给空数组]；'
                 f' "question": "只有缺了就没法往下走才问的一句话，否则空字符串"}}')
    return "\n\n".join(parts)


def plan_prefix_cut(text: str) -> str | None:
    """把「正在生成的计划」切成能立刻用的合法前缀：`actions` 一闭合就够写正文了。

    为什么要切：实测规划调用 19.8s、而正文首字只要 0.7s —— 等整份 JSON 写完才开写正文，
    就是把学生的首字押在最没有信息增量的后半段（milestones/options/axes）上。
    字段顺序由 plan_prompt 固定（situation → actions → …），所以 actions 一闭合，
    「这几件事做什么、用什么、多大量、怎么验收」已经齐了。
    没闭合到就返回 None；不靠字符串找 `]`（值里可能带方括号），按 JSON 结构走。
    """
    start = (text or "").find("{")
    if start < 0:
        return None
    body = text[start:]
    depth = 0
    in_str = esc = False
    key = pending = ""
    for i, ch in enumerate(body):
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            else:
                pending += ch
            continue
        if ch == '"':
            in_str, pending = True, ""
        elif ch == ":" and depth == 1:
            key = pending
        elif ch in "[{":
            depth += 1
        elif ch in "]}":
            depth -= 1
            if depth < 0:
                return None
            if depth == 1 and ch == "]" and key == "actions":
                return body[:i + 1] + "}"
    return None


def normalize_plan(data: Any, n_papers: int) -> dict | None:
    """只接受结构过关的计划；动作少于 2 条就不如不规划——多一次调用换一条更差回复不划算。"""
    if not isinstance(data, dict):
        return None
    actions = []
    for a in (data.get("actions") or [])[:PLAN_COUNTS["actions"]]:
        if not isinstance(a, dict):
            continue
        do = str(a.get("do", "")).strip()[:PLAN_BUDGET["do"]]
        if not do:
            continue
        actions.append({"do": do, "with": str(a.get("with", "")).strip()[:PLAN_BUDGET["with"]],
                        "dose": str(a.get("dose", "")).strip()[:PLAN_BUDGET["dose"]],
                        "check": str(a.get("check", "")).strip()[:PLAN_BUDGET["check"]]})
    ev = [i for i in (data.get("evidence") or []) if isinstance(i, int) and 1 <= i <= n_papers]
    ev = ev[:PLAN_COUNTS["evidence"]]
    axes = []
    for ax in (data.get("axes") or [])[:PLAN_COUNTS["axes"]]:
        if not isinstance(ax, dict):
            continue
        buckets = [{"name": str(one.get("name", "")).strip()[:PLAN_BUDGET["name"]],
                    "test": str(one.get("test", "")).strip()[:PLAN_BUDGET["test"]]}
                   for one in (ax.get("buckets") or [])[:PLAN_COUNTS["buckets"]]
                   if isinstance(one, dict) and str(one.get("name", "")).strip()]
        axes.append({"axis": str(ax.get("axis", "")).strip()[:PLAN_BUDGET["axis"]], "buckets": buckets,
                     "relation": str(ax.get("relation", "")).strip()[:PLAN_BUDGET["relation"]],
                     "when_bad": str(ax.get("when_bad", "")).strip()[:PLAN_BUDGET["when_bad"]]})
    plan = {"situation": str(data.get("situation", "")).strip()[:PLAN_BUDGET["situation"]], "actions": actions,
            "axes": axes,
            "milestones": [str(m).strip()[:PLAN_BUDGET["milestone"]]
                           for m in (data.get("milestones") or [])[:PLAN_COUNTS["milestones"]]
                           if str(m).strip()],
            "options": [{"pick": str(o.get("pick", "")).strip()[:PLAN_BUDGET["pick"]],
                         "when": str(o.get("when", "")).strip()[:PLAN_BUDGET["when"]]}
                        for o in (data.get("options") or [])[:PLAN_COUNTS["options"]]
                        if isinstance(o, dict) and str(o.get("pick", "")).strip()],
            "artifact": str(data.get("artifact", "")).strip()[:PLAN_BUDGET["artifact"]],
            "pitfall": str(data.get("pitfall", "")).strip()[:PLAN_BUDGET["pitfall"]],
            "evidence": ev, "question": str(data.get("question", "")).strip()[:PLAN_BUDGET["question"]]}
    return plan if len(actions) >= 2 else None


NARROW_RE = re.compile(r"我只想|只要|别给我讲|不需要(讲)?理论|不用讲理论|直接告诉(我)?|"
                       r"简单(点|说)|就说|跑通|先不管|不关心")


def narrows_scope(user_text: str) -> bool:
    """学生已经自己收窄了范围：这轮就该给答案，不该再把问题抛回去。"""
    return bool(NARROW_RE.search(user_text or ""))


def apply_ask_policy(plan: dict | None, user_text: str) -> dict | None:
    """把"要不要提问"从模型的手里收回代码里判一次。

    实测依据：0/5 全败的那个人设里，学生说"别给我讲太多理论"，我方仍结尾问"你现在处于哪个阶段"；
    那句问句还会被评测的抽取步算成一条"可执行条目"（实测条目『告知当前研究阶段』），
    等于自己把清单里的名额让给了一条不是动作的东西。
    """
    if plan and plan.get("question") and narrows_scope(user_text):
        plan = dict(plan, question="")
    return plan


AXIS_PROBLEMS_MAX = 4


def _norm_bucket(name: str) -> str:
    return re.sub(r"[\s（）()、，,./·\-]+", "", str(name or "")).lower()


def axes_problems(plan: dict | None) -> list[str]:
    """候选组织的结构体检：类别必须两两互斥、数量合规、线不重复、关系要声明。

    线上实测的翻车形态是"强化学习"与"多智能体强化学习"并列——父子当兄弟。
    归一化后互为子串即判不合格：这条不依赖模型自觉，所以它下次不会再犯。
    """
    problems: list[str] = []
    seen_axis: set[str] = set()
    for ax in ((plan or {}).get("axes") or []):
        name = str(ax.get("axis", "")).strip()
        buckets = [b for b in (ax.get("buckets") or []) if str(b.get("name", "")).strip()]
        if not name or len(buckets) < 2 or len(buckets) > PLAN_COUNTS["buckets"] + 2:
            problems.append(f"「{name or '(无名线)'}」的类别数不合规")
            continue
        if name in seen_axis:
            problems.append(f"组织线重复：{name}")
            continue
        seen_axis.add(name)
        norm = [_norm_bucket(b.get("name")) for b in buckets]
        for i in range(len(norm)):
            for j in range(i + 1, len(norm)):
                if norm[i] and norm[j] and (norm[i] in norm[j] or norm[j] in norm[i]):
                    problems.append(f"「{name}」把父子当兄弟：{buckets[i]['name']} × {buckets[j]['name']}")
        if not str(ax.get("relation", "")).strip():
            problems.append(f"「{name}」没声明类别之间是并列还是包含")
    return problems[:AXIS_PROBLEMS_MAX]


def plan_block(plan: dict | None) -> str:
    if not plan or not plan.get("actions"):
        return ""
    lines = [f"{i}. 做：{a['do']}" + (f"｜用：{a['with']}" if a["with"] else "")
             + (f"｜量：{a['dose']}" if a["dose"] else "")
             + (f"｜完成判据：{a['check']}" if a["check"] else "")
             for i, a in enumerate(plan["actions"], 1)]
    parts = ["【本轮已规划好的动作清单，正文必须逐条写全，不得增删、不得只挑一半】"]
    if plan.get("situation"):
        parts.append("处境复述（用学生自己的词）：" + plan["situation"])
    parts += lines
    if plan.get("milestones"):
        parts.append("时间线（按学生给的总时长切段，要写到投稿/开题那个节点）："
                     + "；".join(plan["milestones"]))
    if plan.get("options"):
        parts.append("候选与选择规则（学生问到怎么选时必须写全，说清每种情况选哪个）："
                     + "；".join(f"{o['pick']}→{o['when']}" for o in plan["options"]))
    if plan.get("artifact"):
        parts.append("这一段做完留下的可交付物：" + plan["artifact"])
    if plan.get("pitfall"):
        parts.append("要避开的具体坑：" + plan["pitfall"])
    for ax in (plan.get("axes") or []):
        buckets = [b for b in (ax.get("buckets") or []) if str(b.get("name", "")).strip()]
        if not buckets:
            continue
        parts.append(f"候选组织线：{ax.get('axis', '')}（{ax.get('relation') or '未声明关系'}）｜"
                     + "；".join(f"{b['name']}＝{b.get('test', '')}" for b in buckets)[:900]
                     + (f"｜不适用：{ax['when_bad']}" if ax.get("when_bad") else ""))
    bad = axes_problems(plan)
    if bad:
        parts.append("上面这些线有结构问题，写正文前必须改对：" + "；".join(bad))
    if plan.get("evidence"):
        parts.append("可引用编号：" + ", ".join(str(i) for i in plan["evidence"]))
    parts.append("结尾问题：" + (plan["question"] if plan.get("question") else "本轮不问问题"))
    return "\n".join(parts)


TOKEN_RE = re.compile(r"[A-Za-z][A-Za-z0-9\-]{2,}|\d+")


def _tokens(s: str) -> set[str]:
    """拉丁词与数字是强信号；纯中文句子没有它们，所以再补 3 字窗口，
    否则一条中文回复的 token 集是空的，闸门永远不会触发（写第一版就被测试抓到）。"""
    out = {t.lower() for t in TOKEN_RE.findall(s or "")}
    run = "".join(ch if ("一" <= ch <= "鿿") else " " for ch in (s or ""))
    for seg in run.split():
        out |= {seg[i:i + 3] for i in range(len(seg) - 2)}
    return out


def plan_dropped(text: str, plan: dict | None) -> bool:
    """计划一条都没被执行才算违规。

    阈值故意取"零命中"而不是"命中不足"：模型把「12k 条」写成「1.2 万条」时
    词面对不上但内容没错，按覆盖率判会误伤好回复（本仓库已三次栽在闸门误伤上）。
    """
    if not plan or not plan.get("actions"):
        return False
    body = _tokens(text)
    if not body:
        return False
    for a in plan["actions"]:
        keys = _tokens(a["with"]) | _tokens(a["dose"]) | _tokens(a["check"]) | _tokens(a["do"])
        if body & keys:
            return False
    return len(plan["actions"]) >= 2



RUBRIC_KEYS = ("acceptance", "constraint", "timeline", "decision", "evidence", "one_question")


def _student_quantables(text: str) -> set[str]:
    """学生亲手给出的量与专名：一年 / 三个月 / 两块 3080 / 12k / BraTS / Zotero。

    constraint 这一维最初的写法是"回复与学生原话有共同词"，实测 161 条历史回复 100% 命中——
    那不是"接住了处境"，那是中文里随便两个句子都会撞词。只有复述了对方给过的**具体量或专名**
    才算数。
    """
    out = set(re.findall(r"[一二两三四五六七八九十\d]+\s*(?:个月|台|块|篇|周|天|年|次|个|组|份|k|K)", text or ""))
    out |= {t.lower() for t in re.findall(r"[A-Za-z][A-Za-z0-9\-]{2,}", text or "")}
    out |= {t for t in re.findall(r"\d{3,}", text or "")}
    return {s.strip().lower() for s in out if s.strip()}


def student_quantables(user_text: str, history: list[dict] | None = None) -> list[str]:
    """学生（本轮 + 历史里 ta 说过的话）给过的量与专名。"""
    said = " ".join([user_text or ""] + [str(m.get("content", "")) for m in (history or [])
                                        if m.get("role") == "user"])
    return sorted(_student_quantables(said))


def missed_quantables(reply: str, user_text: str,
                      history: list[dict] | None = None) -> list[str]:
    """上面这些里，这条回复一个都没接住的那些。"""
    low = (reply or "").lower()
    return [q for q in student_quantables(user_text, history) if q not in low]


def reply_rubric(text: str, *, user_text: str = "", intent: Any = None,
                 papers: list[dict] | None = None) -> dict[str, bool]:
    """六个可以代码判真伪的维度。写进提示词的约束只有落到这里才算被遵守。"""
    t = text or ""
    digits = re.findall(r"\d+(?:[.,]\d+)?%?|\d+[kK]\b", t)
    rec = " ".join(_paper_record(p) for p in (papers or []))
    asked = [a for a in re.split(r"[。！？!?\n]", t) if a.strip().endswith("？") or a.strip().endswith("?")]
    return {
        "acceptance": bool(re.search(r"直到|以内|之内|以上|开外|不少于|至少|能看到|算得出|跑得出|"
                                     r"可复现|通过|达到|做到|降到|稳定在", t))
                    and len(digits) >= 2,
        "constraint": any(q in t.lower() for q in _student_quantables(user_text)),
        "timeline": bool(re.search(r"(?:第\s*)?[\d一二两三四五六七八九十]+\s*[—\-~至]\s*"
                                   r"[\d一二两三四五六七八九十]+\s*(?:个月|周|天)|"
                                   r"[\d一二两三四五六七八九十]+\s*(?:个月|周|天)[之]?[内后]", t)),
        "decision": bool(len(asked) <= 1 and re.search(r"如果|若|当.{0,12}时|优先|而不是|除非|选.{0,10}因为", t)),
        "evidence": bool(re.search(r"\[\d{1,2}\]|《[^》]{10,}》", t))
                    and any(d.rstrip("%") in rec for d in digits if len(d) > 2),
        "one_question": question_count(t) <= 1,
    }


def clean(text: str) -> str:
    """去掉模型偶尔自己加的前缀与多余空行。"""
    out = (text or "").strip()
    for prefix in ("研伴：", "研伴:", "回复："):
        if out.startswith(prefix):
            out = out[len(prefix):].strip()
    # 全角方括号［1］和半角 [1] 是一回事，不统一的话角标闸门会漏检（线上实测过一条）
    out = out.replace("［", "[").replace("］", "]")
    while "\n\n\n" in out:
        out = out.replace("\n\n\n", "\n\n")
    return out


EMPTY_NUMBERING_RE = re.compile(r"(?:\s*\d+\.)+\s*$")


def strip_empty_numbering(text: str) -> str:
    """「1. 2. 3.」断在半空、编号后面没有内容时整串删掉——线上实测模型会这么收尾。"""
    return EMPTY_NUMBERING_RE.sub("", text or "").rstrip()


def _norm_title(t: str) -> str:
    return re.sub(r"[\s《》\"'“”‘’.,;:!?()（）\[\]-]", "", (t or "").lower())


def unverified_titles(reply: str, papers: list[dict]) -> list[str]:
    """回复里提到、但在本轮真实检索结果中找不到的论文标题。"""
    allowed = [_norm_title(p.get("title") or "") for p in (papers or []) if p.get("title")]
    return [hit for hit in TITLE_RE.findall(reply or "")
            if not any((t := _norm_title(hit.strip("《》"))) and (t in a or a in t) for a in allowed)]


def strip_unverified(reply: str, papers: list[dict]) -> str:
    """剔除提到未核实论文标题的句子——宁可少说，不可编造。"""
    bad = unverified_titles(reply, papers)
    if not bad:
        return reply
    kept = [s for s in re.split(r"(?<=[。！？!?])\s*", reply) if not any(b in s for b in bad)]
    return clean("".join(kept))


def _paper_record(p: dict) -> str:
    """一篇论文可以拿来当依据的全部文字面。"""
    # citation_count / 推荐理由也必须算进依据面：样例实测到"（2005年，被引10次）"被当成
    # 编造统计量删句——那些数正是 Provider 给的真数据，删掉它们比留着更糟。
    return " ".join(str(p.get(k) or "") for k in
                    ("title", "venue", "year", "tldr", "abstract", "doi", "citation_count",
                     "recommendation_reason", "reasons")).lower()


SENT_SPLIT = re.compile(r"(?<=[。！？!?])|\n")

# 只在"这句话正在陈述实验结果"时核对数字。不加这层限制时，"选取 1-2 篇"这种我们自己给的量
# 会被当成论文统计量删句（真实转储上实测到 1% 这类误伤）。
RESULT_RE = re.compile(r"准确率|精确率|召回|F1|AUC|ROUGE|BLEU|SOTA|最优|性能|效果|达到|做到|高达|低至|优于|超过|"
                       r"提升|降低|误差|收敛|泛化|样本|数据集|规模|耗时|加速比|误报率|参数量")


def ungrounded_number_sentences(text: str, papers: list[dict]) -> list[str]:
    """带角标的那句里，出现"这篇的记录里查无此数"的数字 → 整句列出待删。

    不算数的三类：角标本身 [3]、四位年份（1900—2099）、以及记录里已经出现的数字。
    """
    bad: list[str] = []
    for sent in SENT_SPLIT.split(text or ""):
        s = sent.strip()
        if not s or not re.search(r"\[\d{1,2}\]", s) or not RESULT_RE.search(s):
            continue
        idxs = [int(m) - 1 for m in re.findall(r"\[(\d{1,2})\]", s)]
        rec = " ".join(_paper_record(papers[i]) for i in idxs if 0 <= i < len(papers))
        if not rec:
            continue
        # 去掉句首的列表编号与 markdown 项目符号：它们是排版，不是"这篇的统计量"
        body = re.sub(r"^\s*(?:[-*]\s*)?\d+[.、)]?", "", s)
        body = re.sub(r"\[(\d{1,2})\]", " ", body).replace("[", " ").replace("]", " ")
        for num in re.findall(r"\d+(?:\.\d+)?", body):
            if 1900 <= int(float(num)) <= 2099 and len(num) == 4:
                continue          # 年份本来就在记录与引用里
            if num in rec or num.rstrip(".0") in rec or f"{num} " in rec:
                continue
            bad.append(sent)
            break
    return list(dict.fromkeys(bad))


def strip_ungrounded_numbers(text: str, papers: list[dict]) -> str:
    """删掉这些句子——宁可少一句，也不能把编造的统计量留在带引用的话里。"""
    out = text or ""
    for sent in ungrounded_number_sentences(out, papers):
        out = out.replace(sent, "")
    return out.strip() if out != text else out


_SCALE_NUM = re.compile(r"\d{1,3}(?:,\d{3})+|\d{3,}(?:\.\d+)?|\d+\.\d+")
# 字母-数字专名（CIFAR-100、ResNet-50、YOLOv8）里的数字是名字的一部分，不是"这篇报的规模"
_NAMED_NUM = re.compile(r"[A-Za-z]+[-–_]\d+|[A-Za-z]\d+")


def ungrounded_scale_sentences(text: str, *, user_text: str = "",
                               history: list[dict] | None = None,
                               context: str = "") -> list[str]:
    """零证据那一轮里，"报了具体规模但手上没有任何东西支撑"的句子。

    为什么不复用 `RESULT_RE`：那张词表管的是"准确率/性能/达到"这类语境，而线上真实编造是
    数据集规模（"2,708 个节点和 5,429 条边"），轮 204 在 149 句上一句都不命中——所以那条路
    走不通，判据得换成"数字本身的形状"（千分位、≥3 位、带小数）。
    学生自己说过的量不算：`no_echo` 要求接住它们（"两块 3080""n=161"），删了就是两道闸门互斥。
    我们喂给模型的上下文里的数也不算它编的（`context`）：实测过一次真误杀——提示词里那行
    "我对这次任务的理解度：0.31" 被复述回来，闸门把整条回复删空。
    """
    known = set()
    for src in [*(student_quantables(user_text, history) or []), context or ""]:
        known.update(re.findall(r"\d+(?:\.\d+)?", str(src).replace(",", "")))
    bad: list[str] = []
    for sent in SENT_SPLIT.split(text or ""):
        s = sent.strip()
        if not s:
            continue
        body = _NAMED_NUM.sub(" ", s)
        body = re.sub(r"\[\d{1,2}\]|【\d{1,2}】", " ", body)   # 角标不是统计量
        for tok in _SCALE_NUM.findall(body):
            flat = tok.replace(",", "")
            if "." not in tok and 1900 <= float(flat) <= 2099:
                continue                                       # 年份本来就不是编造
            if flat in known:
                continue
            bad.append(sent)
            break
    return list(dict.fromkeys(bad))


def strip_ungrounded_scale(text: str, papers: list[dict], *, user_text: str = "",
                           history: list[dict] | None = None, context: str = "") -> str:
    """手里没有候选论文卡时，删掉报规模的句子——宁可少一句，也不能把编造的统计量说成事实。

    只在 `papers` 为空时动手：有卡的时候"这句的数字是不是这篇说的"归 `strip_ungrounded_numbers` 管，
    两道闸门各管一段，不抢活也不留缝（轮 203 那条"只认半种括号"就是留缝的代价）。
    """
    if papers:
        return text
    out = text or ""
    for sent in ungrounded_scale_sentences(out, user_text=user_text, history=history,
                                           context=context):
        out = out.replace(sent, "")
    return out.strip() if out != text else out


SEARCH_STATS_MARK = "【本轮检索统计"


def fabricated_stats_blocks(text: str) -> list[str]:
    """列出正文里每一个「【本轮检索统计】…」块（从块头到下一个空行，或到结尾）。"""
    src = text or ""
    out, i = [], src.find(SEARCH_STATS_MARK)
    while i >= 0:
        j = src.find("\n\n", i)
        out.append(src[i:j] if j >= 0 else src[i:])
        i = src.find(SEARCH_STATS_MARK, j if j >= 0 else len(src))
    return out


def strip_fabricated_search_stats(text: str, papers: list[dict]) -> str:
    """本轮没给候选论文时，删掉正文里那些「本轮检索统计」块。

    注入侧已经保证「有论文才给这个块」（`build_prompt` 只在 papers 非空时拼它），所以 `papers` 为空
    时屏上出现的块只可能是模型照 SYSTEM 第 3 条点过的块名自己编的——块里每个数都没有来源。
    有论文时整块原样放过（那是真统计，删它才是丢信息）。
    """
    if papers:
        return text
    out = text or ""
    for seg in fabricated_stats_blocks(out):
        out = out.replace(seg, "")
    return re.sub(r"\n{3,}", "\n\n", out).strip() if out != text else out


def strip_dangling_citations(text: str, papers: list[dict]) -> str:
    """删掉指向不存在论文卡的角标：本轮没给候选、或编号超出候选数量，都是模型幻觉。

    🔴 两种括号都得剪。`_CITE_TRACE` 认 [n]、【n】、《》 三种痕迹，而这里过去只认 ASCII 那一种：
    模型把角标写成全角【1】，一句"本轮没有文献依据"的话就照样带着三个看起来能点的编号出厂
    （轮 203 从线上截图里看到的，图 docs/screenshots/r203_live_declined_1280.png）。
    """
    n = len(papers or [])

    def keep(m):
        k = int(m.group(1) or m.group(2))
        return m.group(0) if 1 <= k <= n else ""

    return re.sub(r"\[(\d{1,2})\]|【(\d{1,2})】", keep, text or "")


REPAIR_ROUNDS = 2

# 引用痕迹：与 evidence/llm 认的是同一套（[n]、【n】、《论文》），换口径会让闸门与产品各说一遍
_CITE_TRACE = re.compile(r"\[\d{1,2}\]|【\d{1,2}】|《")


def thin_citation(text: str, papers: list[dict] | None) -> bool:
    """手里给了论文卡、正文却一处来源都没挂。

    为什么单开一条：`no_substance` 只管"有没有能用的东西"，一句不引来源的建议也算合格。
    线上量到"有卡却零引用"占这类回复的 33%（g92 夹具重放：有论文卡的 6 条里 2 条，
    两条都是"手里有 8 篇却让学生自己去读文献"），梯度成立才开这条闸门。
    """
    return bool(papers) and not _CITE_TRACE.search(text or "")


# 学生这轮自己说了要什么形状（“一句话”“不要展开”“就给三条”…）。只认显式说法，宁可漏判不误判：
# 它让位的是「多给几条／多给点量／把清单每条写成一句」这三条**生成类**闸门；
# 诚实性闸门（假标题、无引用、回声接住学生给过的量、长度上限）一概不让位。
# 三段各自命名，是因为「超形状」那道闸门（over_shape）只对前两段有可核对的上界：
# 短式段的上界是句数，条数段的上界是学生自己说的那个数，而"用表格/别引用"只约束呈现方式、没有数量界。
_SHAPE_TERSE = r"一句话|一句就|简单说|简短说|长话短说|别展开|不要展开|不用展开|别多说|不要多说"
# 注意 "给 N 条" 单独不算（必须是"只给 N 条"或"给 N 条就行/好/够"）——这是轮 215 定下的口径，改它要先有读数
_SHAPE_NCOUNT = r"只给\s*([一二三四五六七八九十\d]+)\s*条|给\s*([一二三四五六七八九十\d]+)\s*条就[行好够]"
_SHAPE_FORM = r"用表格|别引用|不要引用|不用引用"
_SHAPE_REQUEST = re.compile(f"{_SHAPE_TERSE}|{_SHAPE_NCOUNT}|{_SHAPE_FORM}")
_SHAPE_NCOUNT_RE = re.compile(_SHAPE_NCOUNT)

# 「一句话／不要展开」那一族的句数上界。2 不是整数偏好，是轮 227 从线上分布取的：
# 学生点名形状的那 23 轮里，回复句数中位=6、超过 2 句占 0.61（14/23）——也就是说提示词第 131 行
# 那句"以 ta 说的为准"实测守不住，而"两句以内"是既不把一句正常追问一起咬掉、又能把中位 6 句判下来的界。
SHAPE_MAX_SENTENCES = 2
_SENTENCE_END = re.compile(r"[。！？!?]+")
_LIST_ITEM = re.compile(r"^[ \t]*(?:[-*+·]|\d+[.、)])[ \t]+", re.M)
_CN_NUM = {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5,
           "六": 6, "七": 7, "八": 8, "九": 9, "十": 10}


def sentence_count(text: str) -> int:
    """正文有几句话：按终止标点切段，认不出标点的碎片不算。"""
    return len([s for s in _SENTENCE_END.split(text or "") if s.strip()])


def over_shape(text: str, user_text: str) -> bool:
    """学生点名了形状，正文却超出那个形状。

    为什么不只看字数：轮 227 量到形状轮的问题不是"长"（那是 `too_long` 管的 1200 字），
    而是"说好一句话却写了六句"——长度闸门够不着这种。
    已知限制：只在终止标点（。！？）上切句，所以一整篇没有句号的清单式回复会漏判；
    这是"宁可漏判不误判"的代价，与上面形态尺同一条口径。
    """
    if not shape_requested(user_text):        # 学生没说形状 ⇒ 一律不判（§5.1：不为好看压长度）
        return False
    if re.search(_SHAPE_TERSE, user_text):
        # 轮 232 线上实例把上面那条"已知限制"从假设变成了事故：模型交回
        # 「1. KDD Cup 99 / 2. Enron Corpus / 3. CIC IDS 2012」——整篇不带句号，切句数出 1 句，
        # 于是"一句话、不要展开"被读成"接住了"。点名要短句就不要清单，与句数同一条界。
        return sentence_count(text) > SHAPE_MAX_SENTENCES or bool(_LIST_ITEM.search(text or ""))
    m = _SHAPE_NCOUNT_RE.search(user_text)
    if m:
        raw = m.group(1) or m.group(2)
        n = int(raw) if raw.isdigit() else _CN_NUM.get(raw, 0)
        if not n:
            return False      # "只给十二行"这类认不出的数不判，别拿猜出来的界去重采样
        return len(_LIST_ITEM.findall(text or "")) > n
    return False


def shape_requested(user_text: str) -> bool:
    """学生这轮是否显式说了回复的形状——形态让位的唯一判据，只读原话，不猜。"""
    return bool(_SHAPE_REQUEST.search(user_text or ""))


def violations(text: str, *, papers: list[dict] | None = None,
               history: list[dict] | None = None, plan: dict | None = None,
               user_text: str = "", open_items: list[dict] | None = None) -> dict[str, bool]:
    """一条回复违反了哪几条硬约束（闸门与纠正语都从这里取，避免两处各写一遍）。"""
    papers = papers or []
    thin = no_substance(text, papers)
    # 形态让位只让上面那三条「要更多东西」的闸门；thin 本身照旧算给 no_citation／no_echo 用
    shape = shape_requested(user_text)
    v = {"fake_title": bool(unverified_titles(text, papers)),
         "multi_question": question_count(text) > 1,
         "false_memory": bool(false_memory(text, history)),
         "no_substance": thin and not shape,
         "no_quantity": not thin and not shape and thin_quant(text),
         # 只在"一条动作都没被执行"时判违规；已有别的违规时不叠这条，见 plan_dropped 的注释
         "plan_dropped": plan_dropped(text, plan) and not thin and not shape,
         "no_echo": False}
    # 学生给过的量与专名**一个都没接住**才开火：n=161 重放里这类回复 3 胜 21 负（9%），同期全体 30%。
    # 口径换成"漏掉任意一个"时触发率飙到 42%、其中 35% 落在胜局上——那是误伤，实测过就不再采用。
    # 允许与 no_quantity / plan_dropped 同轮开火：它只往纠正语后面追加"漏掉的是哪几个"，
    # 不与那两条指令打架；但结构性问题（只提问、假称记得）发生时不该再叠第三件事。
    said = student_quantables(user_text, history)
    v["no_echo"] = bool(said) and len(missed_quantables(text, user_text, history)) == len(said) \
        and not thin and not v["false_memory"]
    # 组织线不互斥时正文写得再顺也是错的分类；真人最容易被这种骨架带偏
    v["bad_axes"] = bool(axes_problems(plan)) and not any(v.values())
    # 排在 bad_axes 之后：它进 `any(v.values())` 会让 bad_axes 少开火，先算完那条再挂它
    v["no_citation"] = thin_citation(text, papers) and not thin
    # 轮 228：学生点名形状而正文超形状。排在 bad_axes 之后算，和 no_citation/too_long 同一个理由——
    # 它进了 `any(v.values())` 会让 bad_axes 在超形状的回复上闭嘴，而分类线错了这件事跟长短无关。
    v["over_shape"] = over_shape(text, user_text)
    # 放在最后：它进 `any(v.values())` 会让 bad_axes 在长回复上闭嘴，先算完那条再挂它
    v["too_long"] = len(text) > MAX_REPLY_CHARS
    # 键必须**永远存在**（不传 open_items 时是 False）：调用方按字典取键，缺键就是 KeyError
    v["false_completion"] = False
    # 替学生宣布完成：只在"确实有未结项"时才可能开火（无台账 ⇒ 没东西可冒领）
    if open_items:
        from companion.conversation import commitments
        v["false_completion"] = commitments.premature_claims(text, open_items) > 0
    return v


_REPAIR_SUFFIX = {"fake_title": CORRECTIVE, "multi_question": CORRECTIVE_Q,
                  "false_memory": CORRECTIVE_M, "no_substance": CORRECTIVE_S,
                  "no_quantity": CORRECTIVE_N, "plan_dropped": CORRECTIVE_P,
                  "no_echo": CORRECTIVE_E, "bad_axes": CORRECTIVE_A, "too_long": CORRECTIVE_L,
                  "no_citation": CORRECTIVE_C, "false_completion": CORRECTIVE_K,
                  "over_shape": CORRECTIVE_O}


def repair(text: str, *, user_text: str, intent: Any, history: list[dict] | None = None,
           memory_ctx: str = "", papers: list[dict] | None = None,
           gaps: list[str] | None = None, plan: dict | None = None,
           followup_ctx: str = "", profile_ctx: str = "",
           open_items: list[dict] | None = None) -> str:
    """违反硬约束就重采样，最多两轮；仍违规则由后面的确定性兜底处理。

    提示词只能提高遵守率，不能保证——所以这里是要素级的硬闸门。
    原来只补一轮：实测到"我这就去查几篇"被纠正后，模型第二次交的仍然是一句回声加一个问句
    （46 字那条），闸门照放——第二轮针对"这一轮还缺什么"重新算纠正语，不是同一句话再问一遍。
    """
    papers = papers or []
    by_rule: dict[str, int] = {}
    rounds = 0
    for _ in range(REPAIR_ROUNDS):
        v = violations(text, papers=papers, history=history, plan=plan, user_text=user_text,
                       open_items=open_items)
        if not any(v.values()):
            break
        rounds += 1
        for k, on in v.items():
            if on:
                by_rule[k] = by_rule.get(k, 0) + 1
        suffix = "".join(_REPAIR_SUFFIX[k] for k, on in v.items() if on)
        if v["no_echo"]:      # 纠正语末尾点名漏了哪几个数——泛泛说"要接住学生"实测没用
            suffix += "、".join(missed_quantables(text, user_text, history)) + "。"
        prompt = build_prompt(user_text=user_text, intent=intent, history=history,
                              memory_ctx=memory_ctx, papers=papers, gaps=gaps, plan=plan,
                              followup_ctx=followup_ctx, profile_ctx=profile_ctx) + suffix
        try:
            text = clean(run_async(chat(prompt, system=SYSTEM, temperature=0.3,
                                        max_tokens=REPLY_MAX_TOKENS, retries=1,
                                        timeout=REPLY_TIMEOUT)))
        except LLMError:
            break   # 通道挂了就用手上的原文，下面的确定性兜底照做
    note_gates(intent, rounds, by_rule)
    text = strip_chain(text, intent=intent, papers=papers, user_text=user_text, history=history,
                       memory_ctx=memory_ctx, gaps=gaps, plan=plan, followup_ctx=followup_ctx,
                       profile_ctx=profile_ctx)
    return text if history else rewrite_false_memory(text)   # 改写兜底：重采样也挡不住时至少不假称记得


def ask(*, user_text: str, intent: Any, history: list[dict] | None = None, memory_ctx: str = "",
        papers: list[dict] | None = None, gaps: list[str] | None = None,
        followup_ctx: str = "", profile_ctx: str = "", open_items: list[dict] | None = None,
        timeout: float = REPLY_TIMEOUT) -> tuple[str, bool]:
    """生成一条回复。返回 (文本, 是否降级)；LLM 失败时文本为空串交由调用方兜底。"""
    plan = make_plan(user_text=user_text, intent=intent, history=history,
                     memory_ctx=memory_ctx, papers=papers, followup_ctx=followup_ctx,
                     profile_ctx=profile_ctx)
    note_plan(intent, plan)
    prompt = build_prompt(user_text=user_text, intent=intent, history=history,
                         memory_ctx=memory_ctx, papers=papers, gaps=gaps, plan=plan,
                         followup_ctx=followup_ctx, profile_ctx=profile_ctx)
    try:
        text = run_async(chat(prompt, system=SYSTEM, temperature=0.7, max_tokens=REPLY_MAX_TOKENS,
                              retries=1, timeout=timeout))
    except LLMError:
        return "", True
    text = repair(clean(text), user_text=user_text, intent=intent, history=history,
                  memory_ctx=memory_ctx, papers=papers, gaps=gaps, plan=plan,
                  followup_ctx=followup_ctx, profile_ctx=profile_ctx, open_items=open_items)
    return ("", True) if not text else (text, False)


def followups(*, user_text: str, intent: Any, reply: str, papers: list[dict] | None = None) -> list[str]:
    """3 条与本轮内容绑定的追问建议（对标 Perplexity 的 related questions）。

    失败或 MOCK 时返回空列表，由调用方回落到规则建议——建议是锦上添花，不该拖慢或卡住回复。
    """
    from llm import LLM_MOCK, chat_json, run_async

    NL = chr(10)
    if LLM_MOCK or not reply:
        return []
    lines = [f"- {(p.get('title') or '')[:70]}" for p in (papers or [])[:5] if p.get("title")]
    titles = NL.join(lines)
    parts = [f"学生刚说：{user_text[:140]}", f"导师刚回：{reply[:260]}"]
    if titles:
        parts.append("刚检索到的论文：" + NL + titles)
    parts.append("请给出这个学生下一步最可能想问的 3 个短问题（每条不超过 18 个字，中文，"
                 "要具体到可以直接发出去问，不要客套话）。"
                 '只输出 JSON：{"items": ["问题一", "问题二", "问题三"]}')
    prompt = NL.join(parts)
    try:
        data = run_async(chat_json(prompt, temperature=0.5, max_tokens=220, retries=1, timeout=25.0))
        items = data.get("items") if isinstance(data, dict) else None
        raw = [str(x).strip() for x in (items or []) if str(x).strip()]
        cleaned = [re.sub(r"^(?:问题\s*[一二三四五六七八九十\d]+\s*[:：、]|\d+\s*[\.、\)])\s*", "", s)[:24]
                   for s in raw]
        return [c for c in dict.fromkeys(cleaned) if len(c) >= 4][:3]
    except Exception:  # noqa: BLE001 — 建议生成失败就回落规则版
        return []


def _plan_from(text: str, n_papers: int, user_text: str) -> dict | None:
    from llm import parse_json
    data = parse_json(text)
    if not isinstance(data, dict):
        return None
    return apply_ask_policy(normalize_plan(data, n_papers), user_text)


async def plan_prefix(*, user_text: str, intent: Any, history: list[dict] | None = None,
                      memory_ctx: str = "", papers: list[dict] | None = None,
                      followup_ctx: str = "", profile_ctx: str = "") -> dict | None:
    """流式读规划，**actions 一闭合就收手**——学生不该等完整份 JSON 才看到正文。

    实测：规划调用 19.8s、正文首字 0.7s，串行等整份计划是首字延迟的大头；
    而正文真正依赖的是 actions（做什么/用什么/多大量/怎么验收）。
    切不出可用计划时按原样降级（plan=None 走无规划路径），与 make_plan 失败时同一口径。
    """
    from llm import chat_stream
    n = len(papers or [])
    buf = ""
    agen = None
    try:
        agen = chat_stream(plan_prompt(user_text=user_text, intent=intent, history=history,
                                       memory_ctx=memory_ctx, papers=papers,
                                       followup_ctx=followup_ctx, profile_ctx=profile_ctx),
                           system=PLAN_SYSTEM, temperature=0.2,
                           max_tokens=PLAN_MAX_TOKENS, timeout=PLAN_TIMEOUT)
        async for delta in agen:
            buf += delta
            cut = plan_prefix_cut(buf)
            if cut:
                plan = _plan_from(cut, n, user_text)
                if plan:
                    return plan
    except LLMError as exc:
        _plan_fail(intent, f"通道:{type(exc).__name__} {exc}")
    finally:
        if agen is not None:
            await agen.aclose()
    return _plan_from(buf, n, user_text)      # 计划可能本来就写完了，别浪费已经收下的段


async def ask_stream(*, user_text: str, intent: Any, history: list[dict] | None = None,
                     memory_ctx: str = "", papers: list[dict] | None = None,
                     gaps: list[str] | None = None, followup_ctx: str = "", profile_ctx: str = "",
                     plan_sink: dict | None = None,
                     open_items: list[dict] | None = None) -> AsyncIterator[str]:
    """流式生成，逐段 yield 文本增量；LLM 失败抛 LLMError 交由端点降级。

    规划阶段在这里也跑（与 ask 同一条链路），否则测出来的质量与线上发的不是同一个 build。
    但只等 actions 闭合（见 plan_prefix），不再把首字押在整份计划上。
    不能在这条 async 路径里调 run_async，所以直接走 async 生成器。
    """
    from llm import LLM_MOCK
    plan = None
    if not LLM_MOCK:
        plan = await plan_prefix(user_text=user_text, intent=intent, history=history,
                                 memory_ctx=memory_ctx, papers=papers,
                                 followup_ctx=followup_ctx, profile_ctx=profile_ctx)
    note_plan(intent, plan)
    # 正文的闸门要看这份计划才知道"动作清单有没有被执行"（H5）：
    # 以前流式路径把 plan 关在 ask_stream 里，repair 收到 plan=None ⇒ plan_dropped / bad_axes
    # 对线上正文从不生效，只在走 ask() 的测试里生效。用一个显式 sink 传出去，不藏进程级全局。
    if plan_sink is not None:
        plan_sink["plan"] = plan
    prompt = build_prompt(user_text=user_text, intent=intent, history=history,
                         memory_ctx=memory_ctx, papers=papers, gaps=gaps, plan=plan,
                         followup_ctx=followup_ctx, profile_ctx=profile_ctx)
    async for delta in chat_stream(prompt, system=SYSTEM, temperature=0.7,
                                   max_tokens=REPLY_MAX_TOKENS, timeout=REPLY_TIMEOUT):
        yield delta
