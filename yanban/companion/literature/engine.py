"""文献检索引擎：ResearchIntent → 多源检索 → 去重 → 排序 → 推荐理由。

Phase A：最小桩——复用 sources.multi_source_search（Crossref+arXiv），
输出统一 Paper dict。Phase C 扩展为 Provider 体系 + OpenAlex/SemanticScholar + 七级 Ranking。
"""
from __future__ import annotations

import asyncio
import logging
import re
from datetime import datetime, timezone
from uuid import uuid4

log = logging.getLogger("yanban.companion.literature")


# 一条查询只覆盖主题一个面；下面这些变体都只从学生自己给过的字段里取词。
MAX_QUERY_VARIANTS = 3

# 缺口 → 受控子查询（FAIR-RAG 那类"只为显式缺口发子查询"的做法，词表只此一份、模型不参与选词）。
# 为什么不用学生的偏好原词：`_query_from_intent` 的 docstring 记着线上实测——把"近两年/开放获取/
# 只学过一点"拼进检索式，英译会产出 recent / open / access / deep / learning 这类跨领域通用词，
# 相关性判定从此分不出钓鱼检测与生态学综述。所以这里只在**已经成立的英文式**后面挂一个库侧体裁词，
# 并且补检索回来的论文仍用**主轮的 tokens** 判相关性（门槛绝不因为多打了一轮而放松）。
FACET_VOCAB = frozenset({"survey", "tutorial", "benchmark"})
GAP_FACETS: dict[str, str] = {
    "research_stage": "survey",                    # 不知道走到哪一步 ⇒ 先看综述最不容易走偏
    "user_background.dl_level": "tutorial",        # 不知道深浅 ⇒ 带教程性质的材料
    "publication_goal.type": "benchmark",          # 不知道要交什么 ⇒ 可复现的基准类工作
    "user_background.paper_reading_level": "survey",
}
MAX_SUBQUERY_ROUNDS = 1        # 每轮对话最多补一次检索
MAX_SUBQUERY_PAPERS = 4        # 这一次最多带进来几篇（超出的一律不入库）


def gap_subqueries(gaps, queries: list[str]) -> list[tuple[str, str]]:
    """把显式缺口翻成受控子查询：只认词表里的字段，每轮最多一条。"""
    base = queries[0] if queries else ""
    if not base:
        return []
    out: list[tuple[str, str]] = []
    for field in gaps:
        term = GAP_FACETS.get(field)
        if not term:
            continue
        q = f"{base} {term}"
        if q in queries:
            continue
        out.append((field, q))
        if len(out) >= MAX_SUBQUERY_ROUNDS:
            break
    return out


def variant_queries(intent, main_query: str) -> list[str]:
    """主查询之外，再用「主题 + 学生接受的方向」「主题 + 他自己的说法」各拼一条。

    只收拉丁文面的 facet：线上实测过把中文原词一起送英文库，会捞回
    「变电站接地网施工」「加油站VOCs监测」这种只沾了「检测」二字的无关文献
    （既有测试 test_only_translated_query_is_sent_when_translation_works 就是这条的守卫）。
    中文面要分支，得先把facet也英译，那是另一次调用，不在这轮里偷偷加。

    返回去重后的查询列表（含主查询），最多 MAX_QUERY_VARIANTS 条。
    """
    topic = (getattr(intent, "topic", "") or "").strip()
    out = [main_query]
    if not _is_latin(main_query):
        return out
    facets = [d.strip() for d in (getattr(intent, "accepted_directions", None) or []) if d.strip()]
    goal = (getattr(intent, "user_goal", "") or "").strip()
    seeds = [f for f in facets[:1] if _is_latin(f)]
    if not seeds and goal and _is_latin(goal):
        seeds = [goal]
    for extra in seeds[:1]:
        piece = " ".join(extra.split()[:6])
        if piece and piece.lower() not in main_query.lower():
            out.append(f"{topic} {piece}".strip() if _is_latin(topic) else piece)
    return list(dict.fromkeys(q for q in out if q))[:MAX_QUERY_VARIANTS]


def _is_latin(text: str) -> bool:
    """中英混排时按空格取词，纯中文只能整段截断——送英文库时这条差异会直接影响命中。"""
    return bool(text) and sum(ch.isascii() for ch in text) > len(text) * 0.6


def _backfill_query(intent, queries: list[str]) -> str:
    """结果不够时的回炉式：退回到更宽的主题式（去掉修饰词），仍只用学生给过的词，
    且必须是英文式——中文式送英文库只会带回落后的偏题文献。"""
    topic = (getattr(intent, "topic", "") or "").strip()
    if topic and _is_latin(topic) and topic not in queries:
        return topic
    return ""


def _query_from_intent(intent) -> str:
    """检索式只用研究主题。

    线上实测：current_focus 里装的多半是"近两年""开放获取优先""深度学习只学过一点"这类
    **偏好与约束**，一旦拼进检索式，英译就产出 recent / open / access / deep / learning
    这种跨领域通用词——相关性判定从此分不出钓鱼检测和生态学综述，标题锚也形同虚设。
    偏好本来就有专门的通道（paper_preferences 影响过滤、_attach_reasons 影响排序），不该当检索词。
    """
    topic = (intent.topic or "").strip()
    if topic:
        return topic
    focus = [f for f in (intent.current_focus or []) if f]
    return " ".join(focus) or (intent.user_goal or "")[:60] or "research"


def _needs_en_translation(text: str) -> bool:
    """含 CJK 字符 → arXiv/OpenAlex 等英文库需要英译关键词。"""
    return any("\u4e00" <= ch <= "\u9fff" for ch in text)


async def _english_query(query: str) -> str:
    """中文主题 → 英文检索关键词。

    线上实测根因：原实现让模型"输出空格分隔的关键词"却用 chat_json 按 JSON 解析，
    于是**每次**都解析失败被静默吞掉，英译从未真正生效过（有真实 key 也一样）。
    改为按文本取词，并校验结果确实不含中文才采用。
    """
    from llm import LLM_MOCK, chat

    if not _needs_en_translation(query):
        return query
    if LLM_MOCK:
        return query
    try:
        text = await chat(
            "把下面的中文研究主题转成英文学术检索关键词。只输出 3-5 个关键词本身，用空格分隔，"
            "不要编号、不要标点、不要解释：\n主题：" + query,
            temperature=0.1, max_tokens=80, retries=1, timeout=25.0)
        words = re.sub(r"\b\d+\s*[\.、）)]\s*", " ", text or "")   # 去掉 "1. " 之类的列表编号
        words = re.sub(r"[,;，；、]+", " ", words)                  # 分隔符统一成空格
        words = " ".join(words.split()).strip(" 。.=\"'“”‘’（）()[]【】")
        if words and not _needs_en_translation(words) and len(words) <= 160:
            return words
    except Exception:  # noqa: BLE001 — 翻译失败用原查询，但上层会打 query_fallback 标记
        pass
    return query


def _normalize(raw: dict, source: str) -> dict:
    """把 sources.py 原始条目规范为统一 Paper dict。"""
    # 链接形状合同只有**一份**实现：`sources._source_link`（轮 312 第十三段在伙伴侧条目行上立的）。
    # 在函数内 import 而不是顶层：`sources.py` 顶层就 import 本模块，顶层反向 import 会成环——
    # 本文件 `_english_query` 里那句 `from llm import …` 是同一个写法。
    from sources import _source_link

    title = (raw.get("title") or "").strip()
    doi = (raw.get("doi") or "").strip()
    year = raw.get("year")
    try:
        year = int(year)
    except (TypeError, ValueError):
        year = None
    return {
        "id": uuid4().hex[:12],
        "title": title,
        "authors": raw.get("authors") or [],
        "year": year,
        "venue": raw.get("venue") or "",
        "doi": doi,
        # provider 自己给了落地页就用它；没给时**认得出形状才拼**。现读实测：`crossref` 与 `semantic_scholar`
        # 两路压根不写 `url` 键（`providers/crossref_arxiv.py` 只有 arXiv 那一支写），全靠这一行；
        # 旧写法"有 doi 就无条件拼 doi.org"能把 `arXiv:…` 整成 `https://doi.org/arXiv:…` 这种点不开的假链接。
        "url": raw.get("url") or _source_link(doi),
        "abstract": (raw.get("abstract") or "")[:1600],   # 600 → 1600：对比表抽取的落地校验只能依据入库文本，截太短会大面积「未知」
        "citation_count": raw.get("citations"),
        "open_access": bool(raw.get("open_access") or False),
        "source": [source],
        "scores": {},
        "recommendation_reason": [],
    }


def _dedup(papers: list[dict]) -> list[dict]:
    seen: set[str] = set()
    out: list[dict] = []
    for p in papers:
        norm_title = re.sub(r"\W+", "", p["title"].lower())
        key = ("doi:" + p["doi"].lower()) if p["doi"] else ("t:" + norm_title[:60])
        if key in seen or not p["title"]:
            continue
        seen.add(key)
        out.append(p)
    return out


def _attach_reasons(papers: list[dict], intent, feedback: dict | None = None) -> None:
    """打分 + 推荐理由。评分原则（§13）：引用不是唯一——近年论文不被高引老论文碾压。

    feedback={"rejected": {keys}, "favorite": {keys}}：用户反馈必须改变排序（任务书验收项）。
    """
    import math

    def keys(p: dict) -> set[str]:
        return {k for k in ((p.get("doi") or "").lower().strip(),
                            re.sub(r"\W+", "", (p.get("title") or "").lower())[:60]) if k}

    rejected = (feedback or {}).get("rejected") or set()
    favorites = (feedback or {}).get("favorite") or set()
    year_from = intent.paper_preferences.year_from
    for p in papers:
        reasons = []
        score = 0.4
        year = p.get("year")
        if year_from and year:
            if year >= year_from:
                score += 0.25
                reasons.append(f"{year} 年发表，符合你要求的近几年论文")
            else:
                score -= 0.3
        cit = p.get("citation_count") or 0
        if cit >= 50:
            score += 0.2
            reasons.append(f"被引 {cit}，是该方向的代表性工作")
        elif cit >= 10:
            score += 0.1
        if intent.topic and intent.topic.lower() in p["title"].lower():
            score += 0.15
            reasons.append("标题与你的研究主题直接匹配")
        if p.get("open_access"):
            score += 0.1
            reasons.append("开放获取，可直接阅读全文")
        overlap = keys(p)
        if overlap & rejected:
            score -= 0.6
            reasons.append("你之前标过不相关，已降权")
        if overlap & favorites:
            score += 0.3
            reasons.append("与你收藏过的论文同一条路线")
        if not reasons:
            reasons = ["与你的研究主题语义相关"]
        p["recommendation_reason"] = reasons
        p["scores"]["final"] = round(max(0.05, min(1.0, score)), 3)


def _intent_feedback(project_id: str) -> dict | None:
    """读 Paper Memory 里用户给过的收藏/排除，喂给排序（失败不影响检索）。"""
    try:
        from companion.memory import paper_memory
        return {
            "rejected": {k for p in paper_memory.list_papers(project_id, status="rejected")
                         for k in ((p.get("doi") or "").lower().strip(),
                                   re.sub(r"\W+", "", (p.get("title") or "").lower())[:60]) if k},
            "favorite": {k for p in paper_memory.list_papers(project_id, status="favorite")
                         for k in ((p.get("doi") or "").lower().strip(),
                                   re.sub(r"\W+", "", (p.get("title") or "").lower())[:60]) if k},
        }
    except Exception:  # noqa: BLE001 — 反馈读不到就按无反馈排序
        return None


async def _attach_tldr(papers: list[dict]) -> None:
    """一句话 TLDR：LLM 从 abstract 压缩；MOCK/失败时用摘要前 120 字兜底（不新增信息）。

    红线：LLM 只碰 tldr 这一个字段，title/doi/year/venue/citations 一律保持 Provider 原值。
    """
    from llm import LLM_MOCK, chat_json

    for p in papers:
        abstract = (p.get("abstract") or "").strip()
        p["tldr"] = abstract[:120] + ("…" if len(abstract) > 120 else "")
    todo = [(i, p) for i, p in enumerate(papers[:5]) if (p.get("abstract") or "").strip()]
    if not todo or LLM_MOCK:
        return
    listing = "\n".join(f"[{i}] {p['title'][:90]} :: {(p['abstract'] or '')[:400]}" for i, p in todo)
    try:
        data = await chat_json(
            "给每篇论文写一句中文 TLDR（≤40字，只能依据给出的摘要，不得添加摘要里没有的信息）。"
            '返回 JSON：{"tldr": {"序号": "句子"}}\n\n' + listing,
            temperature=0.2, max_tokens=500)
        got = data.get("tldr") if isinstance(data, dict) else None
        if isinstance(got, dict):
            for i, p in todo:
                v = str(got.get(str(i)) or "").strip()
                if v:
                    p["tldr"] = v
    except Exception:  # noqa: BLE001 — TLDR 生成失败就留摘要兜底
        pass


# 离题滤除用的通用词：这些词命中了不算相关（生态学的 review 不该被推给做钓鱼检测的学生）。
# 下半区是线上逐篇体检实测出来的"跨领域高频但零信息量"的词——
# 只按上半区时，机器学习农业综述能靠 deep + learning 两个词过字数下限、并拿到标题锚。
GENERIC_TOKENS = {"review", "reviews", "study", "studies", "analysis", "approach", "based", "using",
                  "novel", "research", "system", "systems", "method", "methods", "application",
                  "effect", "effects", "result", "results", "case", "new", "a", "an", "of", "and",
                  "for", "with", "on", "in", "to", "the", "检测", "研究", "分析", "系统", "方法",
                  "综述", "基于", "应用", "问题",
                  "deep", "learning", "machine", "data", "image", "images", "model", "models",
                  "network", "networks", "framework", "approaches", "technique", "techniques",
                  "survey", "computer", "information", "technology", "science", "engineering",
                  "open", "access", "recent", "self", "high", "highly", "effective", "improved",
                  "proposal", "proposals", "perspective", "perspectives", "view", "case", "cases",
                  "article", "papers", "literature", "future", "challenges", "taxonomy"}


_LATIN = re.compile(r"[a-zA-Z]")
_CJK = re.compile(r"[一-鿿]")


def topic_tokens(queries: list[str], topic: str = "") -> set[str]:
    """主题词：拉丁文按词切，中文按窗口切。

    中文原来是 `[一-鿿]{2,6}` 的贪婪匹配——"多模态钓鱼网站检测"只能切出
    {"多模态钓鱼网", "站检测"} 这种谁也匹配不上的碎片，等于中文主题在滤除环节根本没有词。
    改成：短串（≤4 字）整串作词，长串取 4 字滑窗。这样既有"钓鱼网站""网站检测"，
    又不会因为留下"多模态"这种两字碎片而把《高中信息科技微课多模态分析框架》当成相关文献。
    """
    toks: set[str] = set()
    for text in [*queries, topic]:
        low = (text or "").lower()
        for w in re.findall(r"[a-zA-Z]{3,}", low):
            if w not in GENERIC_TOKENS:
                toks.add(w)
        for run in re.findall(r"[一-鿿]+", low):
            pieces = [run] if len(run) <= 4 else [run[i:i + 4] for i in range(len(run) - 3)]
            toks |= {p for p in pieces if len(p) >= 2 and p not in GENERIC_TOKENS}
    return toks


def relevance(paper: dict, tokens: set[str]) -> int:
    text = f"{paper.get('title', '')} {paper.get('abstract') or ''} {paper.get('tldr') or ''}".lower()
    return sum(1 for t in tokens if t in text)


def _tokens_for(title: str, tokens: set[str]) -> set[str]:
    """只用与该篇标题同种文字的主题词判定：中文标题配拉丁词、英文标题配中文词都必然零命中，
    那种零命中是"判不了"，不是"离题"（英译没生效时尤其常见）。"""
    pat = _CJK if _CJK.search(title) else _LATIN
    return {t for t in tokens if pat.search(t)}


def filter_relevant(papers: list[dict], tokens: set[str]) -> list[dict]:
    """命中主题词太少、或标题里一个主题词都没有的，直接不推荐。

    两条口径都是线上实测逼出来的：
    ① 字数下限：轮 32 的生态学 review 只靠"检测/detection"这种泛词混进来；
    ② 标题锚：轮 33 实测 2/18 把 UAV 航空摄影论文推给做钓鱼检测的学生——它摘要里复用了
       detection / learning 两个词就过了字数下限；同一轮还实测到《高中信息科技微课多模态
       分析框架》靠"多模态"混进列表，所以中文标题也要有同种文字的锚，不能再豁免。
    全滤光就返回空，让上层如实说"这轮没找到直接相关的"，宁可少推也不硬凑。
    """
    kept: list[dict] = []
    for p in papers:
        title = p.get("title") or ""
        pool = _tokens_for(title, tokens)
        if not pool:
            kept.append(p)
            continue
        if relevance(p, pool) < (1 if len(pool) <= 3 else 2):
            continue
        if not any(t in title.lower() for t in pool):
            continue
        kept.append(p)
    return kept


async def search_for_intent_async(intent, limit: int = 8) -> list[dict]:
    """入口（异步）：ResearchIntent → 多 Provider 并行检索 → 去重 → 排序 → 理由。

    Provider 失败自动降级（safe_search 返回空），单源故障用户无感知。
    中文主题自动英译后再查英文库（arXiv/OpenAlex 对中文查询效果差）。
    """
    from companion.literature.providers.base import _client  # noqa: F401
    from companion.literature.providers.crossref_arxiv import ArxivProvider, CrossrefProvider
    from companion.literature.providers.openalex import OpenAlexProvider
    from companion.literature.providers.semantic_scholar import SemanticScholarProvider

    base_query = _query_from_intent(intent)
    if not base_query:
        return []
    en_query = await _english_query(base_query)
    cjk = _needs_en_translation(base_query)
    translated = cjk and en_query != base_query
    if cjk and not translated:
        # 英译没生效（MOCK/超时/失败）会拿中文原词直送英文库——标记出来让上层如实提示。
        intent.extra["query_fallback"] = True
    # 线上实测：翻译成功时再把中文原词一起送库，会混进「变电站接地网施工」「加油站VOCs监测」
    # 这类只沾了「检测」二字的无关文献，所以译成就只用英文式查询。
    queries = [en_query] if translated else [base_query]
    queries += [q for q in variant_queries(intent, queries[0]) if q not in queries]
    providers = [CrossrefProvider(), ArxivProvider(), OpenAlexProvider(), SemanticScholarProvider()]
    per = max(limit, 6)

    async def _gather(qs: list[str]):
        tasks = []
        for q in qs:
            tasks += [p.safe_search(q, per) for p in providers]
        return await asyncio.gather(*tasks)

    async def _collect(qs: list[str]):
        results = await _gather(qs)
        source_names = [p.name for p in providers] * len(qs)
        found: list[dict] = []
        for name, items in zip(source_names, results):
            found += [_normalize(r, name) for r in (items or [])]
        return found

    papers = _dedup(await _collect(queries))
    # 离题滤除：线上盲评实测到 2/18 轮把生态学期刊推给做钓鱼检测的学生，宁可少推也不硬凑
    tokens = topic_tokens(queries, getattr(intent, "topic", "") or "")
    kept = filter_relevant(papers, tokens)
    if dropped := len(papers) - len(kept):
        intent.extra["off_topic_dropped"] = dropped
    # 分支后仍然不足 4 篇：退回更宽的主题式再试一次（只多花一轮，不降低相关性门槛）
    wider = _backfill_query(intent, queries) if len(kept) < 4 else ""
    if wider:
        queries.append(wider)
        papers = _dedup(papers + await _collect([wider]))
        tokens = topic_tokens(queries, getattr(intent, "topic", "") or "")
        kept = filter_relevant(papers, tokens)
        intent.extra["off_topic_dropped"] = max(0, len(papers) - len(kept))
        intent.extra["query_backfilled"] = True
    intent.extra["query_variants"] = len(queries)
    intent.extra["low_relevance"] = bool(papers) and not kept
    # 补检索：只为**词表里的显式缺口**发一次；回来的论文过的是**主轮那把尺子**（tokens 不重算），
    # 超出 MAX_SUBQUERY_PAPERS 的一律不要。买到了什么必须落进 extra（会持久化），
    # 否则"这一轮多花的四次源调用换来了几篇"就只有当时在场的人知道。
    from companion.intent.clarification import clarification_gaps
    for field, sub_q in gap_subqueries(list(clarification_gaps(intent)), queries):
        before = len(kept)
        fetched = _dedup(await _collect([sub_q]))
        have = {p["id"] for p in kept}
        fresh = [p for p in filter_relevant(fetched, tokens) if p["id"] not in have]
        added = fresh[:MAX_SUBQUERY_PAPERS]
        kept += added
        # 身份必须进账：只记数量的话，「带回来的那几篇到底撑住了哪一句」事后无从核对
        # （轮 158 的 N1 前置体检就卡在这里：台账里只有六个数字，没有任何一篇的名字）。
        # 🔴 去重条件沿用 `p["id"]`（每轮新铸 ⇒ 跨轮同一篇会重复入库）——那是**已登记的 N6**，
        #    本轮按人的决定只登记不修；`added_ids` 记的是"这一轮带回来的那个对象"，不代表跨轮唯一。
        intent.extra["subquery"] = {"gap": field, "q": sub_q, "fetched": len(fetched),
                                    "before": before, "after": len(kept), "added": len(added),
                                    "added_ids": [p.get("id", "") for p in added],
                                    "added_titles": [p.get("title", "") for p in added]}
        break
    # 「源挂了」与「查无相关」必须分开：provider 的 429/406 在 safe_search 里被咽成空列表，
    # 不这样捞回来的话，产品会在毫无证据时照常推荐，用户看到的是一句自信的断言。
    # 并发检索会共享这份名单（只影响诊断字段，最多把别人的失败算进来），不值得为它加锁。
    note_outage(intent, kept)
    papers = kept
    if year_from := intent.paper_preferences.year_from:
        strict = [p for p in papers if p.get("year") and p["year"] >= year_from - 1]
        papers = strict or papers
    _attach_reasons(papers, intent, _intent_feedback(intent.project_id))
    papers.sort(key=lambda p: -p["scores"].get("final", 0))
    top = papers[:limit]
    await _attach_tldr(top)
    return top


def note_outage(intent, kept) -> str:
    """把「源挂了」与「查无相关」分开记进 intent.extra，返回失败源名单。

    provider 的 429/406 在 safe_search 里被咽成空列表，不捞回来的话产品会在毫无证据时
    照常推荐。并发检索共享同一份 outage 名单（只影响诊断字段），不值得为它加锁。
    """
    from .providers.base import take_outage
    outage = ",".join(sorted(set(take_outage())))
    if not outage:
        # intent.extra 是持久化的：新一轮明明查好了，就得把上一轮的「检索不可用」标记清掉，
        # 否则那句"没有文献依据"会一直挂在后面每一轮好结果上。
        intent.extra.pop("search_error", None)
        intent.extra.pop("provider_outage", None)
        return ""
    intent.extra["provider_outage"] = outage
    if not kept:
        intent.extra["search_error"] = f"本轮检索源全部失败：{outage}"
    return outage


def search_for_intent(intent, limit: int = 8) -> list[dict]:
    """入口（同步上下文专用）：ResearchIntent → 多 Provider 检索 → 去重 → 排序 → 理由。

    注意：内部用 asyncio.run，禁止在已有事件循环中调用（FastAPI 同步端点运行于
    线程池，无此问题）。
    """
    return asyncio.run(search_for_intent_async(intent, limit))
