"""引用依据片段：把回复里的一句话回连到"我们真的拿到的文字"上。

对标 Elicit/Scite/Semantic Reader 的"角标 → 原文上下文"——这是行业标配而我们此前只做到
"角标 → 论文卡"。差别很关键：卡片证明这篇存在，片段才证明这句话有依据。

只搜我们自己持有的文本（Provider 给的摘要/TLDR，以及已缓存并解析过的 PDF 正文），
找不到就如实返回空：**"没有依据"本身就是产品该显示的信息**，不许拿模型的话补一段假依据。
"""
from __future__ import annotations

import re
from typing import Any, Iterable

MIN_TOKENS = 2          # 至少两个可核对的词面重合，避免"研究/方法"这种单字巧合
RARE_DF_RATIO = 0.34    # 一个词出现在超过 1/3 的候选里，它就是套话而不是依据
BOILER_MIN_DOCS = 3     # 但至少要三篇都用过：三篇里两篇共有的是"共同主题"，不是套话


GLOSS_SYSTEM = "只输出 JSON。把中文学术句子转成 2—8 个英文词（术语、方法名、数据集名、数字保留），不解释不成句。"
GLOSS_MIN_WORDS, GLOSS_MAX_WORDS = 2, 12
GLOSS_MAX_CHARS = 120
GLOSS_BATCH = 6        # 一次调用最多带几句：再多会被 max_tokens 截断整份 JSON（g178 实测）
CJK_RE = re.compile(r"[一-鿿]")
CITED_TRACE_RE = re.compile(r"\[\d{1,2}\]|【\d{1,2}】|《")


def gloss_key(sent: str) -> str:
    """同一句话在台账与桥表里的身份：去角标、去空白。带角标的句子改指后还要认得出。"""
    return re.sub(r"\s+|\[\d{1,2}\]|【\d{1,2}】", "", sent or "")[:120]


def _clean_gloss(v: Any) -> str:
    """把一条桥词返回值收成可核对的英文词面；形状不合就返回空串（调用方丢这一行）。

    🔴 为什么必须容忍数组：g177 从**真实通道**采到的返回是
    `{"i": "0", "k": ["Graph Neural Network", "Fishing Detection", "92% Accuracy"]}`
    ——`k` 是数组、`i` 是字符串。首版按"字符串 + 纯 ASCII 词"校验，把 3/3 行全丢了，
    于是这条桥在线上**空转**，而离线用例全绿（它测的是我自己编的形状）。
    本仓库旧账同型：归一化不许照猜测写，要照返回体形状写。
    """
    if isinstance(v, (list, tuple)):
        v = " ".join(str(x) for x in v)
    words = re.sub(r"\s+", " ", str(v or "")).strip()[:GLOSS_MAX_CHARS]
    if not words or CJK_RE.search(words):
        return ""                              # 没翻，或夹了中文解释
    if any(ord(ch) > 126 for ch in words):
        return ""                              # 全角标点/破折号：不是可核对的词面
    n = len([w for w in re.split(r"[ ;,]+", words) if w])
    return words if GLOSS_MIN_WORDS <= n <= GLOSS_MAX_WORDS else ""


def needs_gloss(text: str) -> bool:
    """只有"中文为主 + 带引用痕迹"的产物才需要跨语言桥。

    英文产物一律走原路：这样所有已发布的历史读数（g68 的 7/13、g92 的 77%、g160 的 85.1%→92.0%）
    不必重算也不会被这条改动动到含义。
    """
    body = text or ""
    return len(CJK_RE.findall(body)) >= 20 and bool(CITED_TRACE_RE.search(body))


def english_gloss(sentences: list[str]) -> dict[str, str]:
    """把中文句转成英文可核对词面，按 GLOSS_BATCH 分批调用。MOCK、失败、格式不合 ⇒ 返回 {}。

    为什么要有这一步：`_tokens` 对中文取的是三字组，对英文摘要取的是词面，**两侧永远不相交**
    （轮 154 实测：中文回复对英文摘要恒不接地）。也就是说整套"引用诚实性"闸门在产品的母语上是瞎的：
    中文句永远落进 true_gap，改指救不了它，补检索也是照着瞎尺子发的。
    依据：Multilingual FACTScore（arXiv:2406.19415，参考语料的语言覆盖是主因）、
    Poly-FEVER（2503.16541）——公开做法就是"先转成与证据同语言的词面再判"。

    🔴 桥只用来**找重合**，不用来下结论：翻出来的词若在原文里找不到，结局照旧是没依据。
    所以这一步不会把"没依据"洗成"有依据"，只会让"本来有依据却因语言看不见"露出来。
    """
    todo = [s for s in sentences if (s or "").strip()]
    if not todo:
        return {}
    from llm import LLM_MOCK
    if LLM_MOCK:
        return {}
    got: dict[str, str] = {}
    # 🔴 分批：g178 实测 9 份可用报告里只有 3 份的桥真的启用了。一份中文报告常有 17—22 句，
    # 一次全塞进去 max_tokens=420 装不下整份 JSON，解析失败就静默退回 {}——
    # 于是这条桥又成了「离线全绿、线上空转」。切段后一段坏不连累其它段。
    for start in range(0, len(todo), GLOSS_BATCH):
        got.update(_gloss_chunk(todo[start:start + GLOSS_BATCH]))
    return got


def _gloss_chunk(sentences: list[str]) -> dict[str, str]:
    """一批（≤ GLOSS_BATCH 句）的桥。取不到就返回 {}，其余批次照旧。"""
    from llm import chat_json, run_async
    numbered = "\n".join(f"{i}. {s[:160]}" for i, s in enumerate(sentences))
    prompt = (f"把下面每一句转成 {GLOSS_MIN_WORDS}—{GLOSS_MAX_WORDS} 个英文关键词"
              "（方法名、数据集、指标、数字要原样保留；不要造句、不要解释）。\n"
              '只输出 JSON：{"items": [{"i": 编号, "k": "english words"}]}\n' + numbered)
    try:
        data = run_async(chat_json(prompt, system=GLOSS_SYSTEM, temperature=0.0,
                                   max_tokens=420, retries=1, timeout=40.0))
    except Exception:  # noqa: BLE001 — 桥取不到就照旧判，不许把降级读成"没依据"
        return {}
    items = data.get("items") if isinstance(data, dict) else None
    got: dict[str, str] = {}
    for it in items or []:
        if not isinstance(it, dict):
            continue
        try:
            idx = int(it.get("i"))          # type: ignore[arg-type]
        except (TypeError, ValueError):
            continue
        words = _clean_gloss(it.get("k"))
        if not (0 <= idx < len(sentences)) or not words:
            continue                          # 没翻、带中文解释、越界编号：一律丢
        got[gloss_key(sentences[idx])] = words
    return got


def _tokens(text: str) -> set[str]:
    out = {t.lower() for t in re.findall(r"[A-Za-z][A-Za-z0-9\-]{2,}", text or "")}
    out |= {t for t in re.findall(r"\d+(?:\.\d+)?%?", text or "")}
    cjk = "".join(ch if ("一" <= ch <= "鿿") else " " for ch in (text or ""))
    for seg in cjk.split():
        out |= {seg[i:i + 3] for i in range(len(seg) - 2)}
    return out


def rare_tokens(papers: list[dict]) -> set[str] | None:
    """池子里"只属于少数几篇"的词——纯词面重合会在方法学套话上假阳性（文献已知失效模式）。

    "提出深度学习框架/实验结果表明/性能提升"这类词每篇摘要都有，两句共享它们不等于
    那句有依据。池子小于 3 篇时返回 None：样本太少，算出来的 IDF 本身就是噪声。
    """
    if len(papers or []) < 3:
        return None
    df: dict[str, int] = {}
    for p in papers:
        recs = sources(p)
        if not recs:
            continue
        seen: set[str] = set()
        for _, body in recs:
            seen |= _tokens(body)
        for t in seen:
            df[t] = df.get(t, 0) + 1
    n = sum(1 for p in papers if sources(p))
    if n < 3:
        return None
    return {t for t, c in df.items() if not (c >= BOILER_MIN_DOCS and c / n > RARE_DF_RATIO)}


def _sentences(text: str) -> list[str]:
    parts = re.split(r"(?<=[.!?。！？])\s+|(?<=[。！？])|\n", text or "")
    return [p.strip() for p in parts if len(p.strip()) >= 25]


def sources(paper: dict, pdf_sections: Iterable[tuple[str, str]] = ()) -> list[tuple[str, str]]:
    """这篇论文手里有哪些文字可以用：(来源标签, 原文)。

    标题必须算一路来源：中文回复里最常见的"引用"就是把英文标题原样搬进《》，
    不给它就没法核对（线上实测：综述场景三条带角标句全被判 no_evidence，依据只在标题里）。
    """
    out: list[tuple[str, str]] = []
    title = str(paper.get("title") or "").strip()
    if title:
        out.append(("标题", title))
    for key, label in (("tldr", "TLDR"), ("abstract", "摘要")):
        v = str(paper.get(key) or "").strip()
        if v:
            out.append((label, v))
    for name, body in pdf_sections or []:
        if (body or "").strip():
            out.append((f"PDF·{name}"[:40], body))
    return out


def best_spans(claim: str, paper: dict, *, pdf_sections: Iterable[tuple[str, str]] = (),
               limit: int = 2, rare: set[str] | None = None,
               gloss: str = "") -> list[dict[str, Any]]:
    """返回最能支撑 claim 的原文片段（带来源与重合度），没有就返回空列表。

    rare 给了就只认"少数几篇才有"的词：不给依据的句子会被套话判成有依据，
    那比判成没依据更糟——UI 会拿它当"这句查过了"。
    """
    want = _tokens(claim) | _tokens(gloss)
    if rare is not None:
        want &= rare
    if not want:
        return []
    scored: list[dict[str, Any]] = []
    for label, body in sources(paper, pdf_sections):
        # 短来源（标题整条往往不足 25 字）过不了分句下限，整条当一句用
        for sent in _sentences(body) or ([body.strip()] if body.strip() else []):
            have = _tokens(sent)
            overlap = want & have
            if len(overlap) < MIN_TOKENS:
                continue
            scored.append({"text": sent[:600], "source": label,
                           "overlap": len(overlap),
                           "coverage": round(len(overlap) / max(1, len(want)), 3)})
    scored.sort(key=lambda d: (-d["coverage"], -d["overlap"]))
    picked: list[dict[str, Any]] = []
    seen: set[str] = set()
    for s in scored:
        key = s["text"][:60]
        if key in seen:
            continue
        seen.add(key)
        picked.append(s)
        if len(picked) >= limit:
            break
    return picked


def grounding_by_section(text: str, papers: list[dict]) -> list[dict]:
    """按 markdown 二级标题切节，逐节给引用接地率——只报一个总数字会藏住"哪一节是空的"。

    线上真实形态：综述报告的「批判性分析/缺口」节最容易写成没依据的套话，
    而总分 70% 时看不出问题出在哪一节。
    """
    chunks: list[tuple[str, list[str]]] = [("(开头)", [])]
    for line in (text or "").splitlines():
        head = re.match(r"^##\s+(.+)$", line)
        if head:
            chunks.append((head.group(1).strip()[:60], []))
        else:
            chunks[-1][1].append(line)
    out = []
    for head, lines in chunks:
        body = "\n".join(lines).strip()
        if not body:
            continue
        g = grounding_report(body, papers)
        out.append({"section": head, **g})
    return [c for c in out if c["cited_sentences"]]


def pdf_parsed_for(project_id: str, filename: str) -> dict:
    """已缓存 PDF 的整份解析结果（逐页文本 + 章节 + 页→节）；没缓存就空——不为了凑证据去下载。"""
    if not filename:
        return {}
    from companion.paper.downloader import PDF_DIR
    from companion.paper.parser import parse_pdf

    path = PDF_DIR / project_id / filename
    if not path.exists():
        return {}
    return parse_pdf(str(path)) or {}


def pdf_sections_for(project_id: str, filename: str) -> list[tuple[str, str]]:
    """已缓存 PDF 的章节文本；没缓存就空——不为了凑证据去下载。"""
    sections = pdf_parsed_for(project_id, filename).get("sections") or {}
    return [(str(k), str(v or "")) for k, v in sections.items()]


NEGATION_RE = re.compile(r"然而|但与|并不|未能|不支持|反驳|质疑|无法复现|局限性|"
                         r"however|not\s+able|fail(?:s|ed|ure)?|contradict|inconclusive|"
                         r"no\s+significant|underperform", re.I)


def survey(claim: str, papers: list[dict], *, limit: int = 12, gloss: str = "") -> dict:
    """在项目论文库里数一遍：有几篇的原文真的支持这句、有几篇出现相反说法。

    "反对"只算**同时满足**"与这句话词面重合"且"重合处附近有否定/局限标记"的句子，
    否则宁可不显示这个数字——把关键词匹配说成结论判定，是产品最容易骗人的地方。
    """
    want = _tokens(claim) | _tokens(gloss)
    rare = rare_tokens(papers)
    if rare is not None:
        want &= rare
    if not want:
        return {"checked": 0, "support": 0, "against": 0, "hits": []}
    support = against = checked = 0
    hits: list[dict] = []
    for n, p in enumerate(papers[:limit], start=1):
        recs = sources(p)
        if not recs:
            continue
        checked += 1
        best = None
        neg = False
        for label, body in recs:
            for sent in _sentences(body):
                if len(_tokens(sent) & want) < MIN_TOKENS:
                    continue
                if best is None:
                    best = {"source": label, "text": sent[:400]}
                if NEGATION_RE.search(sent):
                    neg = True
        if best:
            support += 1
            hits.append({"idx": n, "title": str(p.get("title") or "")[:120],
                         "stance": "against" if neg else "support", **best})
        if neg:
            against += 1
    return {"checked": checked, "support": support, "against": against, "hits": hits[:4]}


def _classify(sent: str, idxs: list[int], papers: list[dict], rare: set[str] | None,
              gloss_map: dict[str, str] | None = None) -> tuple[str, dict | None]:
    """一句的三种结局之一，并带上第一个真能撑住它的片段（片段记着出自第几篇）。

    判序与旧实现逐字一致：先只看被引的那几篇（接住 = grounded），接不住才扫整池
    （别处有 = 挂错编号，可机械改指；整池没有 = 真缺证据，要补检索）。
    把分类与取片段并成一个函数，是为了让产物里的「逐句依据」与汇总数**必然同源**：
    两条各自实现一遍，迟早会一个说接地 18 句、另一个只列得出 15 句。
    """
    gloss = (gloss_map or {}).get(gloss_key(sent), "")
    pool = [(i, papers[i]) for i in idxs if 0 <= i < len(papers)] or list(enumerate(papers))
    for label, cand in (("grounded", pool), ("misnumbered", list(enumerate(papers)))):
        for i, p in cand:
            spans = best_spans(sent, p, rare=rare, gloss=gloss)
            if spans:
                return label, {**spans[0], "paper": i}
    return "true_gap", None


def _cited(text: str) -> list[tuple[str, list[int]]]:
    """带角标（或《篇名》）的句子，连着它引用的编号（0 基）。**分句与筛选口径只这一处。**

    `grounding_report` 与「补检索可救上限」都从这里取句子：两边各自 split 一遍，
    迟早一个说 18 句、另一个只列得出 15 句——那种对账错是最难发现的，因为两个数各自都"看着合理"。
    """
    out: list[tuple[str, list[int]]] = []
    for raw in re.split(r"(?<=[。！？!?\n])", text or ""):
        s = raw.strip()
        if len(s) < 8 or not (re.search(r"\[\d{1,2}\]", s) or "《" in s):
            continue
        out.append((s, [int(m) - 1 for m in re.findall(r"\[(\d{1,2})\]", s)]))
    return out


def grounding_report(text: str, papers: list[dict], *, bilingual: bool = False,
                     gloss_map: dict[str, str] | None = None) -> dict:
    """这篇产物里带引用的句子，有多少句真能在被引那篇的原文里找到依据。

    为什么需要它：调研实测到"引用痕迹"本身就能讨好模型评委（Search Arena β_citations=+0.209，
    而"来源真的支持"与"来源无关"几乎等效），所以**不能让"看起来有引用"充当质量证据**。
    这个读数把"有引用"与"引用得住"分开计量——它是产品自己的可核对性指标，不需要评委。

    `rows` 是逐句台账（句、被引编号、结局、支撑片段）；四个计数全部由它算出，
    所以下游拿计数与拿片段对账，不可能对不上还各自绿。

    `gloss_map` 给了就直接用（同一段文字要连判几次时，桥词只该取一份——
    轮 174 首版没这个入参，反证闸门那一处就一次报告多发了十次翻译调用，被自家成本用例抓到）。
    """
    rare = rare_tokens(papers)
    cited_rows = _cited(text)
    # 桥只在"中文为主且带引用"时起作用；起了作用就要在输出里声明，读数不许不知道自己用的哪把尺
    if gloss_map is None:
        gloss_map = english_gloss([s for s, _i in cited_rows]) if (bilingual and needs_gloss(text)) else {}
    rows: list[dict] = []
    for s, idxs in cited_rows:
        status, span = _classify(s, idxs, papers, rare, gloss_map)
        rows.append({"sentence": s[:120],
                     "cited": [i + 1 for i in idxs if 0 <= i < len(papers)],
                     "status": status, "span": span})
    def _n(status: str) -> int:
        return sum(1 for r in rows if r["status"] == status)
    cited = len(rows)
    grounded = _n("grounded")
    # 🔴 `bilingual` 声明的是"这份读数里真有几句是带着桥词判的"，不是"我取到了一份桥词表"：
    #    逐节调用时，表是整份共用的，某一节可能一句都没用上（轮 174 首版按表非空声明，
    #    于是那一节会对着读者自称"经跨语言核对"——独立评审抓到的）。
    gloss_used = sum(1 for s, _i in cited_rows if gloss_map.get(gloss_key(s)))
    # supports 是"这篇该不该被引"那一半的读数（ALCE 把 recall 与 precision 分开报）：
    # 每篇真撑住了几句。它必须由同一批 rows 算出，所以恒等式 sum(supports) == grounded+misnumbered
    # 成立——两边各算一遍就会印出「六篇来源」而台账里只有五篇有依据。
    supports = [0] * len(papers)
    for r in rows:
        if r["span"] is not None:
            supports[r["span"]["paper"]] += 1
    return {"cited_sentences": cited, "grounded": grounded, "bilingual": bool(gloss_used),
            "gloss_used": gloss_used,
            "misnumbered": _n("misnumbered"), "true_gap": _n("true_gap"),
            "supports": supports,
            "coverage": round(grounded / cited, 3) if cited else None,
            "ungrounded_samples": [r["sentence"] for r in rows if r["status"] != "grounded"][:3],
            "rows": rows}


def supplement_value(text: str, papers: list[dict], extra: list[dict]) -> dict:
    """补检索的**可救数**：整池都接不住的那些句子，有多少能被**这一份给定候选集**接住。

    为什么要另起一把尺：接地率（grounded/cited）在真实读数上已经饱和——g168 的 15 对里有 11 对
    分子等于分母，"补检索有没有用"在那把尺上**没有可表达的空间**（轮 158 实测，
    docs/evidence/g170_n1_channel_and_power_projection.txt 正文 D）。补检索唯一能发挥作用的地方
    是 `true_gap`：挂了编号、把整池扫一遍也接不住的句子。这里量的就是这批句子的可救比例。

    三条口径写死在这儿，免得被读歪：
      · 它回答"材料在不在"，不回答效果（模型有没有把它写进正文）——后者要靠配对 A/B；
        而且它**不是上界**：对候选集不单调。多给几篇会把它们共用的判别词挤进套话档
        （df 越过 BOILER_MIN_DOCS 与 RARE_DF_RATIO），那句话的 want 变空 ⇒ 救回数可能**变小**
        （用例 `tests2/test_supplement_value.py::test_v6_...` 钉住，两条植入都会让它红）。
        ⇒ 换一份候选集就不许与另一份比大小；引用这个数必须同时写清候选集是怎么来的；
      · 判据与 `grounding_report` 同源：同一份 `_cited` 分句、同一个 `rare_tokens`、同一个 `MIN_TOKENS`；
        但词频集必须按**合并后的语料**重算——拿主池的 rare 去量补充论文，
        补充论文独有的词压根不在主池词表里，上限会恒等于 0（那是尺子的错，不是改动没用）；
      · 已 grounded / misnumbered 的句子一律不进分子：那是主轮本来就该做的活，不是补检索的功劳。
    `rescued` 是逐句台账（含救回它的那篇在 extra 里的下标与片段），所有计数都由它派生。
    """
    g = grounding_report(text, papers)
    rows = g["rows"]
    rare = rare_tokens([*(papers or []), *(extra or [])])
    # 粗筛：`best_spans` 要求**同一句原文**里凑够 MIN_TOKENS 个 want 词，
    # 所以整篇的词集与 want 相交不足 MIN_TOKENS 时必然为空 —— 删掉它们是必要条件过滤，
    # 不是第二把尺（对账用例：`tests2/test_supplement_value.py` 里那条
    # 「brute-force 扫必须与粗筛同数」，把这条过滤改成恒假/恒真都会红或暴露依赖）。
    want_of = {}
    have_of = []
    for p in (extra or []):
        have = set()
        for _label, body in sources(p):
            have |= _tokens(body)
        have_of.append(have)
    rescued = []
    for n, (full, _idxs) in enumerate(_cited(text)):
        if n >= len(rows) or rows[n]["status"] != "true_gap":
            continue
        want = _tokens(full) & rare if rare is not None else _tokens(full)
        if len(want) < MIN_TOKENS:
            continue
        for j, p in enumerate(extra or []):
            if len(want & have_of[j]) < MIN_TOKENS:
                continue
            spans = best_spans(full, p, rare=rare)
            if spans:
                rescued.append({"sentence": full[:120], "extra": j,
                                "paper": (p or {}).get("title", ""), "span": spans[0]})
                break
    gap = len([r for r in rows if r["status"] == "true_gap"])
    return {"cited_sentences": g["cited_sentences"], "grounded": g["grounded"],
            "misnumbered": g["misnumbered"], "true_gap": gap,
            "rescuable": len(rescued), "rescued": rescued, "extra_n": len(extra or []),
            "rescue_rate_of_gap": round(len(rescued) / gap, 3) if gap else None,
            "rescue_rate_of_cited": (round(len(rescued) / g["cited_sentences"], 3)
                                     if g["cited_sentences"] else None)}


def _score(claim: str, paper: dict, rare: set[str] | None, gloss: str = "") -> float:
    spans = best_spans(claim, paper, rare=rare, gloss=gloss)
    return max((float(s.get("coverage") or 0) for s in spans), default=0.0)


def renumber_citations(text: str, papers: list[dict], *, bilingual: bool = False) -> tuple[str, int]:
    """把挂错篇的角标改指到"这句真的能在它原文里找到依据"的那一篇，返回 (文本, 改动数)。

    模型最常见的引用错误不是编造论文（那条已有硬闸门），而是**论文是真的、这句话不是它说的**
    ——ALCE 管这叫 citation precision。这里只在"被引那篇对本句零支撑"时才动角标，
    并且改到确实撑得住的篇；一篇都撑不住就原样留着——那种句子该由接地率读数暴露出来，
    不该靠改编号把它伪装成有依据。

    `bilingual=True` 时先给带角标的句子取一份英文桥词再打分：轮 172 的 g179/g180 实测
    线上中文报告里 **25.9—26.4%** 的带角标句是"literal 判没依据、经桥才接得住"，
    而经桥那把尺自己也会因池序轮转塌陷（0.743→0.128），所以它分得清"引的是哪一篇"。
    不开桥时，这些句子在改指这一环是隐形的——报告是中文写的，这就是产品母语上的盲区。
    """
    ps = [p for p in (papers or []) if p]
    if len(ps) < 2:
        return text or "", 0
    rare = rare_tokens(ps)
    # 只给"带数字角标"的句子取词：`_cited` 也收只带《篇名》的句子，但 `fix()` 只处理带 [n] 的分片，
    # 那些句子翻出来永远不会被用（白花批次）。另外 `_cited` 有 ≥8 字的门槛，比 `fix()` 的分片口径窄——
    # 短角标片段（"详见 [5]。"）因此走字面尺，这是**已知且刻意**的窄，不静默扩。
    gloss_map = (english_gloss([s for s, _i in _cited(text) if re.search(r"\[\d{1,2}\]", s)])
                 if (bilingual and needs_gloss(text)) else {})
    changed = 0

    def fix(raw: str) -> str:
        nonlocal changed
        if not re.search(r"\[\d{1,2}\]", raw):
            return raw
        g = gloss_map.get(gloss_key(raw), "")
        scores = [_score(raw, p, rare, g) for p in ps]
        used = {int(m) - 1 for m in re.findall(r"\[(\d{1,2})\]", raw)}

        def repl(m: re.Match) -> str:
            nonlocal changed
            k = int(m.group(1)) - 1
            if not (0 <= k < len(ps)) or scores[k] > 0:
                return m.group(0)          # 超范围的交给 strip_dangling_citations 处理
            alt = max(((i, s) for i, s in enumerate(scores) if s > 0 and i not in used),
                      key=lambda t: t[1], default=None)
            if alt is None:
                return m.group(0)          # 整池都撑不住：不改，让它留在"没接地"读数里
            used.add(alt[0])
            changed += 1
            return f"[{alt[0] + 1}]"

        return re.sub(r"\[(\d{1,2})\]", repl, raw)

    out = "".join(fix(part) for part in re.split(r"(?<=[。！？!?\n])", text or ""))
    return (out, changed) if changed else (text, 0)
