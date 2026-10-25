"""多源学术检索（literature_partner 的数据源层）。

数据源优先级：
1. Crossref（无需 key，覆盖 1.4 亿+ DOI 记录，含中文期刊）——主力
2. arXiv（英文预印本，已有）——并行补充
3. Semantic Scholar（无 key 限流极紧，暂列备用，带退避；申请 key 后可升主力）

统一输出结构化 Markdown 清单；单源失败不影响其它源（部分结果优于无结果）。

🔴 轮 309：这条路径以前**没有接产品的两把尺**——中文主题被原样送进 Crossref/arXiv，
线上实测（`输出/证据/轮309/线上应答原样.json`）队长那句"检索钓鱼网页检测相关的 3 篇真实论文"拿回来的是
《通信工程网络系统稳定性检测与改进措施》《给排水管网智能化监测与维护研究》——三条都有 DOI、都是真论文，
但没有一条与"钓鱼网页检测"有关（只沾了「检测」二字）。产品主链路早就定死两条（
`companion/literature/engine.py:370—378`）：①**译成就只用英文式查询**（"再把中文原词一起送库会混进
只沾『检测』二字的无关文献"）；②回来的条目过 `filter_relevant`（标题锚＋命中数下限，"宁可少推也不硬凑"）。
所以这里不是新造判据，而是**接同一把尺**（轮 301 定下的方向：partner 不自成一派）。
"""

from __future__ import annotations

import asyncio
import logging
import re
import xml.etree.ElementTree as ET

import httpx

from companion.literature.engine import (  # noqa: E402
    _english_query,
    _needs_en_translation,
    filter_relevant,
    topic_tokens,
)

log = logging.getLogger("yanban.companion.sources")

ATOMIC = "{http://www.w3.org/2005/Atom}"
UA = {"User-Agent": "YanbanResearchAgent/1.0 (mailto:yanban-team@example.org)"}


def clean_abstract(raw: str) -> str:
    """Crossref 的摘要带 JATS 标记（<jats:p>…），不剥掉标签就是一堆噪声。"""
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", raw or "")).strip()


SELECT = "title,author,issued,DOI,container-title,is-referenced-by-count,abstract"


def _to_hit(it: dict) -> dict:
    title = (it.get("title") or ["(untitled)"])[0]
    authors = [f"{a.get('given', '')} {a.get('family', '')}".strip() for a in (it.get("author") or [])][:3]
    year_parts = (it.get("issued") or {}).get("date-parts") or [[None]]
    return {
        "title": title,
        "authors": authors,
        "year": year_parts[0][0],
        "venue": (it.get("container-title") or [""])[0],
        "doi": it.get("DOI", ""),
        "citations": it.get("is-referenced-by-count", 0),
        "abstract": clean_abstract(it.get("abstract"))[:1600],
    }


ABSTRACT_FILTERS = ("has-abstract:true,type:journal-article,from-pub-date:2021-01-01",
                    "has-abstract:true,type:journal-article",
                    "has-abstract:true",
                    "")   # 最后一档＝不带过滤：冷门主题宁可拿回没摘要的条目，也别空手


async def _crossref_once(topic: str, limit: int, filters: str = "") -> list[dict]:
    params = {"query": topic, "rows": min(limit, 20), "select": SELECT, "sort": "relevance"}
    if filters:
        # 逗号是 Crossref 的 AND
        params["filter"] = filters
    async with httpx.AsyncClient(timeout=30, headers=UA) as client:
        r = await client.get("https://api.crossref.org/works", params=params)
        r.raise_for_status()
    items = r.json()["message"]["items"]
    return [_to_hit(it) for it in items]


async def crossref_search(topic: str, limit: int) -> list[dict]:
    """Crossref：返回 [{title, authors, year, venue, doi, citations, abstract}]。

    带 `has-abstract:true` 是实测决定的（轮 102）：不带时 24 条检索结果里 **0 条有摘要**
    （`g66c`），而唯一给摘要的 OpenAlex 被共享出口 IP 的每日额度打满（`g66d`：Retry-After
    ≈9.6 小时）。没有摘要，`papers_block` 就只剩标题可写，综述里的 [n] 角标全对不上原文。
    逐级放松（限期刊+新 → 限期刊 → 只要有摘要 → 不限），拿到结果就停。
    """
    for filters in ABSTRACT_FILTERS:
        got = await _crossref_once(topic, limit, filters)
        if got:
            return got
    return []


async def arxiv_search_raw(topic: str, limit: int) -> list[dict]:
    """arXiv：返回统一结构（复用既有限流退避经验：429/超时退避重试）。"""
    query = " AND ".join(f"all:{w}" for w in topic.split() if w)
    params = {"search_query": query or f"all:{topic}", "start": 0, "max_results": min(limit, 20), "sortBy": "relevance"}
    root = None
    last_exc = None
    for attempt in range(3):
        try:
            async with httpx.AsyncClient(timeout=45) as client:
                r = await client.get("https://export.arxiv.org/api/query", params=params)
            if r.status_code == 200:
                root = ET.fromstring(r.text)
                break
            if r.status_code == 429 or r.status_code >= 500:
                last_exc = RuntimeError(f"arXiv HTTP {r.status_code}")
                await asyncio.sleep(10 * (attempt + 1))
                continue
            r.raise_for_status()
        except (httpx.TimeoutException, httpx.TransportError) as exc:
            last_exc = exc
            await asyncio.sleep(10 * (attempt + 1))
            continue
    if root is None:
        raise RuntimeError(f"arXiv 不可用: {last_exc}")
    out = []
    for entry in root.findall(f"{ATOMIC}entry"):
        title = " ".join((entry.findtext(f"{ATOMIC}title") or "").split())
        authors = [a.findtext(f"{ATOMIC}name") for a in entry.findall(f"{ATOMIC}author")][:3]
        year = (entry.findtext(f"{ATOMIC}published") or "")[:4]
        out.append({
            "title": title,
            "authors": [a for a in authors if a],
            "year": year,
            "venue": "arXiv",
            "doi": (entry.findtext(f"{ATOMIC}id") or "").replace("http://arxiv.org/abs/", "arXiv:"),
            "citations": None,
        })
    return out


_STOP_NOTE = {"the", "and", "for", "with", "that", "this", "from", "based", "a", "of", "on", "in"}


def _year_of(p: dict):
    """条目年份取成整数：Crossref 给 `2023`、arXiv 给 `"2024"`，都没有或畸形就给 `None`（不猜）。"""
    try:
        return int(str(p.get("year"))[:4])
    except (TypeError, ValueError):
        return None


# 🔴 轮 312（队长 §4.1「移除面向开发者的…原始异常…低层诊断」＋ §4.4「记录实际错误分类」）。
# 以前两处把异常原文直接发出去：`sources` 的「暂不可用（{str(exc)[:80]}）」、
# `literature_partner` 的 `f"能力调用失败：{exc}"`。本轮自己就撞出一条真证据：
# 改签名时一个 `TypeError: hang() got an unexpected keyword argument 'year_range'` **整句进了应答**
# （`输出/_logs/r312_p4_full_after_year_fix.log`）。
# 但消毒不能一刀切：下面这几句是产品**自己写给调用方**的、带下一步动作的话，必须一字不改照旧外发
# （`tests2/test_call_budget.py` B1/B5 钉的就是它们）。所以放行按**形状**白名单，不按"是不是我们抛的"。
# 🔴 白名单里只许「字面量 ＋ 数字位」：本轮第一版把第三句写成 `所有数据源均不可用（[^）]*）`，
# 括号里那个通配等于在**外发口**上留了个能吞任意文本的洞（今天只有一处 raise 用它，将来任何一处
# 把上游原文塞进括号就原样发出去了）。形状由 `tests2/test_partner_error_hygiene_r312.py` 的 E9 常驻守着，
# 会放行原文这件事由 E8 用植入的金丝雀串钉住。
_OURS_PATTERNS = (
    r"(?:文献检索|提纲生成)未在 \d+ 秒预算内完成，请直接重试一次（或把主题写窄一点）",
    r"未在 \d+ 秒预算内返回，已跳过",
    r"所有数据源均不可用（可能触发限流，请约 1 分钟后重试）",
    r"请补充研究主题",
)
_OURS_RE = re.compile("^(?:" + "|".join(_OURS_PATTERNS) + ")$")


def outward_text(exc: BaseException) -> str:
    """只有产品自己拟的那几句原话可以外发；其余返回空串，由调用处换成分类话。"""
    msg = str(exc)
    return msg if _OURS_RE.fullmatch(msg) else ""


def failure_kind(exc: BaseException) -> str:
    """把一次失败归成**一类原因**——网络／限流／身份／超时／上游异常／内部缺陷各说各的话，
    不把三种结局混成一句（§4.4），也不带任何原始文本。"""
    if isinstance(exc, asyncio.TimeoutError):
        return "未在预算内完成"
    if isinstance(exc, httpx.TimeoutException):
        return "上游响应超时"
    if isinstance(exc, httpx.HTTPStatusError):
        code = exc.response.status_code
        if code == 429:
            return "上游限流"
        if code in (401, 403):
            return "上游拒绝我们的访问身份"
        if code >= 500:
            return "上游服务异常"
        return f"上游返回 HTTP {code}"
    if isinstance(exc, httpx.TransportError):
        return "连不上上游检索源"
    return "伙伴内部处理没有走完"


def unavailable_reason(exc: BaseException) -> str:
    return outward_text(exc) or failure_kind(exc)


_ARXIV_BARE = re.compile(r"^\d{4}\.\d{4,5}(v\d+)?$")
_DOI_SHAPE = re.compile(r"^10\.\d{4,9}/\S+$")
NO_OPENABLE_LINK = "无可点开的链接（源未给可辨认的 DOI／arXiv 标识）"


def _source_link(ident: str) -> str:
    """把稳定标识换成**能点开**的 URL；形状认不出来就返回空串，**不拿它去拼 DOI 解析器**。

    §3.3 明写 arXiv 条目允许没有 DOI，所以 `arXiv:` 前缀与裸号都走 abs 页；DOI 一侧只认注册前缀 `10.`。
    旧写法是"其余一律 `https://doi.org/` + 原串"，实测会把 `not-an-identifier` 渲染成一条**看着能点开、
    实际点不开**的链接（`输出/_logs/r312_p349_probe_ident_shapes.log` 的 P2 两格）——那比不写链接更坏。
    认不出来时由调用处（`_fmt`）明写"没有可点开的链接"。
    """
    ident = (ident or "").strip()
    if not ident:
        return ""
    if ident.lower().startswith("arxiv:"):
        return "https://arxiv.org/abs/" + ident.split(":", 1)[1]
    if _ARXIV_BARE.match(ident):
        return "https://arxiv.org/abs/" + ident
    if ident.lower().startswith(("http://", "https://")):
        return ident
    if _DOI_SHAPE.match(ident):
        return "https://doi.org/" + ident
    return ""


def _relevance_note(p: dict, tokens) -> str:
    """这一条为什么被留下——只报**标题里真命中的主题词**，没命中就不写这句（§3.3：不许空口"已通过相关性检查"）。"""
    if not tokens:
        return ""
    title = (p.get("title") or "").lower()
    hit = [t for t in sorted(tokens) if len(t) >= 2 and t not in _STOP_NOTE and t in title]
    return "、".join(hit[:3]) if hit else ""


def _fmt(papers: list[dict], source: str, tokens=None) -> list[str]:
    lines = []
    for i, p in enumerate(papers, 1):
        authors = ", ".join(p["authors"]) if p["authors"] else "佚名（源未提供）"
        cit = f" · 被引 {p['citations']}" if p.get("citations") is not None else ""
        ident = (p.get("doi") or "").strip()
        link = _source_link(ident)
        line = (f"   [{p['year'] or '年份未知'}] {authors} ｜ {p['venue'] or '未知来源'} ｜ "
                f"{ident or '无 DOI'}{cit}")
        line += f" ｜ {link}" if link else f" ｜ {NO_OPENABLE_LINK}"
        lines.append(f"{i}. **{p['title']}**")
        lines.append(line + f" ｜ 源:{source}")
        note = _relevance_note(p, tokens)
        if note:
            lines.append(f"   依据：标题命中主题词 {note}")
    return lines


async def multi_source_search(topic: str, limit: int = 10,
                              budget_s: "float | None" = 15.0,
                              year_range: "tuple[int, int] | None" = None) -> str:
    """多源并发检索，聚合为 Markdown 清单；单源失败保留另一源结果。

    `budget_s` 是整次检索的**墙钟预算**（默认 15 秒）。为什么默认要带预算而不是等两路都跑完：
    Crossref 内部有四档放松重试（每档 httpx 30 秒）、arXiv 有三发退避（每发 45 秒），
    源被限流时这一趟实测能拖到 **88.9 秒**（`docs/UNDERMIND_BORROW.md` 未决 64，审计 durationMs=88915），
    而平台那侧用 `acps_sdk` 默认客户端的调用方 **30 秒就超时**（`aip_rpc_client.py` 里
    `timeout=30.0` 是写死的）——我们答完了它也已经放弃，那次"被调用"就变成一次对方视角的失败。
    超预算的那一支按「暂不可用（未在 N 秒预算内返回）」写进结果，另一支照发。
    传 `budget_s=None` 回到旧形状（两路都等到底），留给离线仪器与批量跑用。

    🔴 轮 309 加的两件事（都在产品那把尺上，不是新口径）：
      · **中文主题先英译，译成就只用英文式送库**（`_english_query`）——以前中文原词直送 Crossref，
        「检测」这种单字面就把建工期刊推进了推荐列表（线上读数见模块 docstring）；
      · **两路回来的条目都过 `filter_relevant`**，滤掉的逐源如实报数；全滤光就说"这一轮没有直接相关的"，
        不拿别的条目凑数，也不把它报成"源不可用"。

    🔴 轮 312 加的第三件事：`year_range`（用户点明的年份窗；`extractor.year_range` 按现年推，
    「近三年」在 2026 年里＝`(2023, 2026)`，2027 年里＝`(2024, 2027)`）。
    伙伴这条路径以前**只听不说**：`literature_partner._strip_candidate` 把「近三年」从主题里剥掉，
    剥完就没人再管年份，于是用户要近三年、拿回 2019 年的条目，产物里也没写这一轮按哪年筛过
    （队长 §1.2 要求「年份、篇数、来源等条件」、§4.1 要求先写清关键筛选条件；本地主链路早有严格过滤
    `companion/literature/engine.py:436-438`）。窗的口径来自产品那张表（`extractor.year_range`），
    这里只负责执行与如实报数：**窗外、以及没有年份无法核实的条目都不列入**（宁缺毋造，
    §3.3），滤掉几颗写在脸上。`year_range=None` 时行为与轮 311 逐字相同（过滤器不是恒开的）。
    """
    per = max(limit, 6)     # 与产品主链路同一个数（`engine`：`per = max(limit, 6)`）——池子太浅时
                            # 离题滤除＋去重之后就不够填满调用方要的条数
    if year_range:
        # 带年份窗时把池子挖深一档：源那边不支持按年筛（不改 `crossref_search`/`arxiv_search_raw`
        # 的形状，桩与用例都吃这两枚参数），只能在这里"多捞—再滤"；池子浅的话
        # 「要 3 篇」很容易只回来 1 篇，而窗外其实还有一堆相关的。
        per = min(max(limit * 2, 12), 20)
    # 拉丁主题直接跳过英译那一步（`_english_query` 内部本来也会短路，但在调用点写明＝这一发
    # 不花模型配额、也不占一次往返，外部调用方的 15 秒预算里少一段等待）。
    en = await _english_query(topic) if _needs_en_translation(topic) else topic
    translated = bool(en) and en != topic
    query = en if translated else topic
    tokens = topic_tokens([query], topic)
    if budget_s is None:
        results = await asyncio.gather(
            crossref_search(query, per),
            arxiv_search_raw(query, per),
            return_exceptions=True,
        )
    else:
        tasks = [asyncio.create_task(crossref_search(query, per), name="crossref"),
                 asyncio.create_task(arxiv_search_raw(query, per), name="arxiv")]
        done, pending = await asyncio.wait(tasks, timeout=budget_s)
        results = []
        for t in tasks:
            if t in pending:
                results.append(RuntimeError(f"未在 {budget_s:.0f} 秒预算内返回，已跳过"))
            elif t.cancelled():
                results.append(RuntimeError("检索任务被取消"))
            else:
                try:
                    results.append(t.result())
                except Exception as exc:  # noqa: BLE001 — 与 gather(return_exceptions=True) 同形
                    results.append(exc)
        for t in pending:      # 取消掉没跑完的，别留悬挂任务（也别让它们的异常没人收）
            t.cancel()
        if pending:
            await asyncio.wait(pending, timeout=2)
    crossref, arxiv = results
    sections = [f"主题「{topic}」多源检索结果（Crossref + arXiv 并发）：", ""]
    # 送库查询是什么必须写在脸上：调用方看到「只用英文式」才知道离题滤除是按哪串词判的，
    # 也才知道英译没生效时这一轮的相关性会打折（产品主链路把这一步记成 `query_fallback`）。
    if translated:
        sections.append(f"送库查询：`{query}`（中文主题已英译；译成就只用英文式，不再把原词一起送库）")
    elif _needs_en_translation(topic):
        sections.append(f"送库查询：`{query}`（🔴 英译没生效，用原词直送英文库 ⇒ 这一轮相关性会打折，"
                        "条数宁缺毋造）")
    else:
        sections.append(f"送库查询：`{query}`")
    sections.append("")
    # §4.1：用户看得懂的结果要**先**写清主题和关键筛选条件。年份窗是用户点明的条件，
    # 不写就等于让调用方以为这一发根本没按年份筛过。
    if year_range:
        sections.append(f"筛选条件：年份 {year_range[0]}—{year_range[1]}（你点明了年份窗 ⇒ 落在窗外的、"
                        "以及源没给年份因而无法核实的条目都不列入）")
        sections.append("")

    dropped_total = 0
    dup_total = 0
    year_out = 0        # 过了相关性尺、但落在用户点明的年份窗之外
    year_unknown = 0    # 过了相关性尺、但源没给年份 ⇒ 无法核实它在窗内，按宁缺毋造不列入

    def _kept(items) -> list[dict]:
        """一源一筛：先过产品的相关性尺（`filter_relevant`），再按年份窗滤；两道的计数都如实报。"""
        nonlocal dropped_total, year_out, year_unknown
        if not isinstance(items, list):
            return []
        kept = filter_relevant(items, tokens)
        dropped_total += len(items) - len(kept)
        if year_range:
            lo, hi = year_range
            inside = []
            for p in kept:
                y = _year_of(p)
                if y is None:
                    year_unknown += 1
                elif lo <= y <= hi:
                    inside.append(p)
                else:
                    year_out += 1
            kept = inside
        return kept

    # 池子取 `per = max(limit, 6)`（与产品主链路同一个数），两源都多拿一些，再**跨源**按题名归一去重、
    # 裁到调用方要的那个数。以前是每源 `limit//2`：滤除＋去重之后池子太浅，线上「要 3 篇实给 2 篇」
    # 就是这么来的（`输出/证据/轮309/业务验收_live4.log`）。
    # 去重键用**题名归一**而不是 DOI：产品主链路的 `_dedup` 认「有 DOI 就只按 DOI」，而线上实测到过
    # 题名/作者/年份/期刊逐字相同、只有 DOI 不同的两条（10.30630/jitsi.4.3.181 与 10.62527/…，
    # 见 `输出/证据/轮309/线上应答原样_修复后.json`）。
    # 短到 12 字以下的题名**不参与去重**：那种串（"(untitled)" 之类）不是标题锚，拿它归一会把
    # 真正不同的条目并成一条。
    merged: list[dict] = []
    for items, src_name in ((crossref, "Crossref"), (arxiv, "arXiv")):
        merged.extend({**p, "_source": src_name} for p in _kept(items))
    uniq: list[dict] = []
    seen: set[str] = set()
    for p in merged:
        key = re.sub(r"\W+", "", (p.get("title") or "").lower())
        if len(key) >= 12:
            if key in seen:
                dup_total += 1
                continue
            seen.add(key)
        uniq.append(p)
    listed = uniq[:max(limit, 1)]
    kept_total = len(listed)
    crossref_kept = [p for p in listed if p["_source"] == "Crossref"]
    arxiv_kept = [p for p in listed if p["_source"] == "arXiv"]

    if isinstance(crossref, list) and crossref_kept:
        sections.append(f"### Crossref（期刊/会议，{len(crossref_kept)} 篇）")
        sections.extend(_fmt(crossref_kept, "Crossref", tokens))
        sections.append("")
    elif isinstance(crossref, Exception):
        log.warning("Crossref 这一支失败（原因分类不外发原文）：%r", crossref)
        sections.append(f"### Crossref 暂不可用（{unavailable_reason(crossref)}；原始报错只记服务端日志，不随应答外发）")
        sections.append("")

    if isinstance(arxiv, list) and arxiv_kept:
        sections.append(f"### arXiv（预印本，{len(arxiv_kept)} 篇）")
        sections.extend(_fmt(arxiv_kept, "arXiv", tokens))
    elif isinstance(arxiv, Exception):
        log.warning("arXiv 这一支失败（原因分类不外发原文）：%r", arxiv)
        sections.append(f"### arXiv 暂不可用（{unavailable_reason(arxiv)}；原始报错只记服务端日志，不随应答外发）")

    ok_sources = sum(1 for r in results if isinstance(r, list) and r)
    if ok_sources == 0:
        # 这一支只表示「源真的没给东西」：连条目都没回来，谈不上相关性滤除。
        raise RuntimeError("所有数据源均不可用（可能触发限流，请约 1 分钟后重试）")
    if kept_total == 0 and (year_out + year_unknown):
        # 相关的条目回来了，只是都不在用户点明的年份里 ⇒ 原因必须写成**年份**。
        # 套用下面那句"离题/只沾通用词"是把两件事混成一件事（§4.4 同一条纪律：
        # 网络失败、权限失败、业务空结果不许混为一谈），会让调用方去改主题而不是放宽年份。
        sections.append("")
        sections.append(f"### 这一轮在 {year_range[0]}—{year_range[1]} 年内没有该主题的条目："
                        f"源可用、过了相关性尺的 {year_out + year_unknown} 条都不在这个年份窗里"
                        f"（窗外 {year_out} 条、源未给年份无法核实的 {year_unknown} 条）"
                        f"⇒ 宁缺毋造，不拿窗外的条目凑数。要 {limit} 篇，实际 0 篇。"
                        "把年份放宽一点（或不写年份要求）再发一次通常就有。")
    elif kept_total == 0:
        # 源可用、条目也回来了，但按产品的尺一条都不相关 ⇒ 如实报"没有"，不凑数（未决 64(a) 的根因形状）。
        sections.append("")
        sections.append(f"### 这一轮没有与该主题直接相关的条目：源可用、共回来 "
                        f"{dropped_total + dup_total} 条，但每条都只沾着个别通用词（如「检测/detection」），"
                        f"标题里没有同种文字的主题词锚 ⇒ 宁缺毋造，不把它们放进清单。要 {limit} 篇，实际 0 篇。"
                        "把主题写窄一点（或给一组英文关键词）再发一次通常就有。")
    elif kept_total < limit:
        # 给不满调用方要的数＝相关的就这么几颗：明说缺口，不拿离题、窗外或重复的条目补齐。
        year_bit = (f"、{year_out + year_unknown} 条不在 {year_range[0]}—{year_range[1]} 年份窗内"
                    if year_range else "")
        sections.append("")
        sections.append(f"（要 {limit} 篇，实际只有 {kept_total} 篇通过相关性尺子："
                        f"另有 {dropped_total} 条离题、{dup_total} 条重复没计入{year_bit}"
                        "——宁可少给也不凑数。）")
    parts = [f"相关性尺子：滤除 {dropped_total} 条离题条目"]
    if year_range:
        parts.append(f"年份窗 {year_range[0]}—{year_range[1]}：滤除 {year_out} 条窗外条目"
                     + (f"、{year_unknown} 条源未给年份无法核实的条目" if year_unknown else ""))
    if dup_total:
        parts.append(f"去掉 {dup_total} 条重复条目（题名逐字相同、只有 DOI 不同）")
    parts.append(f"保留 {kept_total} 条")
    sections.append(f"\n（{'；'.join(parts)}）")
    return "\n".join(sections)
