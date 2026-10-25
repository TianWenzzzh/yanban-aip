"""综述报告：把已检索到的论文集写成结构化 markdown，落进知识库并接上 [[双链]]。

红线不变：只允许引用给过的论文。标题级幻觉由 mentor 的硬闸门剔除，越界角标由本模块剪掉，
剪掉了多少如实返回（removed_titles），不假装没发生。
"""
from __future__ import annotations

import re
from typing import Any

from companion import knowledge
from companion.conversation import mentor, report_brief

SECTIONS = ["研究背景与问题", "相关工作分类", "方法对比", "研究空白与可切入方向", "下一步建议"]
SECTION_WORDS = (120, 320)     # 单节重写的目标字数
LOCKED_SECTIONS = {"引用论文"}  # 这节是按论文清单自动生成的，让 LLM 重写只会编出假链接
MAX_PAPERS = 12

SYSTEM = """你是「研伴」的综述写手，为一位研究生写一份短而具体的中文综述。
只依据下面给到的论文标题与摘要写作：禁止编造论文、作者、DOI、年份、数值；
引用某篇时在句末加方括号角标 [n]，n 必须是给到的编号；没有依据的句子一律不写。
输出 markdown，用 ## 分成指定的小节，不要开场白、不要免责声明、不要 emoji。"""


def matrix_block(matrix: dict | None) -> str:
    if not matrix or not matrix.get("rows"):
        return ""
    cols = matrix["columns"]
    lines = [f"跨论文对比表（{'｜'.join(label for _k, label in cols)}）："]
    for row in matrix["rows"]:
        lines.append(f"- {row['title']}：" + "｜".join(row["cells"].get(k, "未知") for k, _ in cols))
    return "\n".join(lines)


def citable_papers(papers: list[dict]) -> list[dict]:
    """报告可引用的清单（编号 = 这个列表的位置）。产物、流式与非流式三条路共用它。

    无标题的记录必须在这里剔掉：`papers_block` 的编号是列表序号，留着它们会让
    报告里的 [3] 与产物参考文献里的 ref-3 指向两篇不同的论文。
    """
    return [p for p in papers if p.get("title")][:MAX_PAPERS]


def build_prompt(intent: Any, papers: list[dict], matrix: dict | None = None) -> str:
    head = (f"研究主题：{getattr(intent, 'topic', '') or '（未明确）'}\n"
            f"发表目标：{intent.publication_goal.type if getattr(intent, 'publication_goal', None) else '未明确'}"
            f"｜研究阶段：{getattr(intent, 'research_stage', 'unknown')}\n")
    tail = (f"\n\n请写一份中文综述，小节固定为 {'、'.join(SECTIONS)} 这五节。"
            "「方法对比」要落到这些论文之间的具体差异，"
            "「研究空白」必须是从上面这些摘要里读出来的缺口，不要写放之四海皆准的话。\n"
            "每节至少挂一条 [编号] 角标（编号就是上面论文列表里的序号，只能用这些）；"
            "挂不上角标的话就别写那句——宁可少一句，也不要留一句看起来对但没有出处的话。")
    block = matrix_block(matrix)
    # 简报块插在论文清单之后、写作要求之前：它说的是"这几节怎么取舍"，必须紧跟在料后面读到，
    # 但排在五节契约之后会自相矛盾（前面刚说要平衡，后面又叫重心挪走）。
    return (head + (block + "\n\n" if block else "") + mentor.papers_block(papers)
            + report_brief.prompt_block(intent) + tail)


def clean_report_with_ledger(text: str, papers: list[dict]) -> tuple[str, int]:
    """报告生成文本的唯一清洗入口，返回 (正文, 被改指的角标数)。

    四道闸门按"先删后改"的顺序排，因为后一步只处理前一步留下的东西：
    标题级幻觉与越界角标交给 mentor 的硬闸门（papers 已截到 MAX_PAPERS），
    最后才是 `renumber_citations`——**篇是真的、编号挂错了**是模型最常见的引用错误
    （`g158` 线上 12 份报告实测：26 句未接地里 12 句的依据本来就在同一批论文集里）。
    放在最后有两个好处：被删掉的句子不会白计一笔，而它只会在"编号超出范围已被剪光"的
    文本上动手，不会把幻觉编号洗白成合法编号。
    """
    from companion.literature import evidence

    stripped = mentor.strip_dangling_citations(
        mentor.clean(mentor.strip_unverified(text, papers)), papers)
    # 🔴 轮 174 曾在这里开跨语言桥（`bilingual=True`），轮 178 按 g183 撤掉：
    #   拿 8 份线上中文报告做"换一批不相干论文集"的安全性对照，字面尺改指 6 处、
    #   开桥改指 12 处（3/8 份变多，最坏一份 3→7）。多出来的那些"依据"只由**翻出来的通用词**
    #   （method / accuracy / dataset 这一类）撑住——桥词在错配池里同样能凑够重合，
    #   于是程序会把角标改到一篇其实没说过这句话的论文上。改指是**改写学生的出处**，
    #   不是读数，宁可漏改也不能错改。P6 那次证的是"覆盖率会随池序轮转塌陷"，
    #   管不住这条：改指动的正是"被引那篇得分 0"的句子，那时桥找的是"池里任何一篇"。
    #   桥仍然保留在**只展示、不改写**的那条路上（`/cite/context` 取依据片段）。
    return evidence.renumber_citations(stripped, papers)


def clean_report(text: str, papers: list[dict]) -> str:
    return clean_report_with_ledger(text, papers)[0]


def normalize_sections(body: str) -> tuple[str, list[str]]:
    """小节契约由代码兜底：小节名被模型改写时认回来，整节缺失时补占位并如实上报。

    线上实测过的两种跑偏：GLM 自己简写成「工作分类」（子串认得回），以及把字序打乱写成
    「工作相关分类」（`g74` 情景 2——子串认不回，于是旧版既留着跑偏的节、又补了一节同名占位，
    用户在一份报告里看到两个相近小节）。改名一律**不许造出第二个同名节**。
    缺节占位句是给学生看的：说清「这节没生成」并指到界面上真有的「重写这节」按钮，
    不写「需要补」这种分不清该谁补的话。
    """
    def _head(line: str) -> str:
        m = re.match(r"^##\s+(.+?)\s*$", line)
        return m.group(1) if m else ""

    lines = body.splitlines()
    have = {_head(l) for l in lines}
    for i, line in enumerate(lines):
        head = _head(line)
        if not head:
            continue
        canon = next((s for s in SECTIONS if s != head
                      and (s in head or head in s or sorted(head) == sorted(s))), None)
        if canon and canon not in have:
            lines[i] = f"## {canon}"
            have.add(canon)
    body = "\n".join(lines)
    missing = [s for s in SECTIONS if f"## {s}" not in body]
    if missing:
        body = body.rstrip() + "\n\n" + "\n\n".join(
            f"## {s}\n（这一节这次没有生成出来。点本节标题旁的「重写这节」可以再试一次。）"
            for s in missing) + "\n"
    return body, missing


def split_sections(body: str) -> list[list[str]]:
    """[[标题, 正文], …]；标题不含 "## "。开头没有标题的部分记在标题 "" 下。"""
    chunks = re.split(r"^##\s+", body or "", flags=re.M)
    out: list[list[str]] = [["", chunks[0]]]
    for chunk in chunks[1:]:
        head, _, rest = chunk.partition("\n")
        out.append([head.strip(), rest])
    return out


def join_sections(sections: list[list[str]]) -> str:
    return "".join((f"## {head}\n" if head else "") + text for head, text in sections)


def _section_prompt(intent: Any, papers: list[dict], section: str, current: str,
                    instruction: str, facts: str = "") -> str:
    ask = f"\n学生的额外要求：{instruction}\n" if instruction.strip() else ""
    return (f"下面这份综述只重写「{section}」这一节，其余章节一个字都不动。\n"
            f"研究主题：{getattr(intent, 'topic', '') or '（未明确）'}{ask}\n"
            f"这一节现在的样子：\n{current.strip()[:1200] or '（空的）'}\n\n"
            f"可引用的论文（编号就是角标）：\n{mentor.papers_block(papers)}\n\n"
            f"{facts}"
            f"重写要求：中文，{SECTION_WORDS[0]}—{SECTION_WORDS[1]} 字，"
            f"只依据上面这些摘要，引用时在句末加 [n]，不得提到列表之外的论文，不要写 ## 标题、不要解释，"
            f"只输出这一节的正文。")


def _pool_has_text(papers: list[dict]) -> bool:
    """论文池里至少两篇带 ≥80 字原文，才允许对「这一节没依据」动手。

    实测线上论文库摘要长度中位为 0（`g66b`/`g66c`）：拿空池去逼模型补引用、补反证，
    只会补出对不上原文的角标。两道闸门（补引用 / 注反证）共用这一条前置。
    """
    return len([p for p in papers if len((p.get("abstract") or "").strip()) >= 80]) >= 2


def _cite_missing_sections(intent: Any, papers: list[dict], body: str) -> tuple[str, list[str]]:
    """一条引用都没挂的节，单独重写那一节补上（最多 2 节），返回 (正文, 补上的节名)。

    为什么不是再改一句提示词：提示词已经写明「每节至少挂一条 [编号]」并发了版，
    带角标的节只从 2/12（`g65`）抬到 3/12（`g66`）——12 节里仍有 9 节一条引用都没挂。
    提示词只能提高遵守率，剩下的要靠闸门：改完必须自己通过 `grounding_report` 的判定才算数，
    模型说补了不算。
    """
    from llm import LLM_MOCK, chat, run_async

    from companion.literature import evidence
    if LLM_MOCK or not _pool_has_text(papers):
        return body, []
    sections = split_sections(body)
    todo = [i for i, (h, t) in enumerate(sections)
            if h and h not in LOCKED_SECTIONS and t.strip()
            and evidence.grounding_report(t, papers)["cited_sentences"] == 0][:2]
    fixed: list[str] = []
    for i in todo:
        head = sections[i][0]
        try:
            raw = run_async(chat(_section_prompt(
                intent, papers, head, sections[i][1],
                "这一节现在一条依据都没挂：请保留能挂上 [n] 的句子，挂不上的删掉"),
                system=SYSTEM, max_tokens=900, temperature=0.5, timeout=120.0))
        except Exception:  # noqa: BLE001 — 补引用失败就保留原节，不能把整篇写坏
            continue
        new_text = chr(10) + clean_report(raw, papers) + chr(10)
        if evidence.grounding_report(new_text, papers)["cited_sentences"] >= 1:
            sections[i][1] = new_text
            fixed.append(head)
    return join_sections(sections), fixed


def _first_sentence(text: str) -> str:
    for s in re.split(r"(?<=[。！？!?\n])", text or ""):
        if len(s.strip()) >= 12:
            return s.strip()
    return (text or "").strip()[:120]


def _limitation_hits(papers: list[dict], only: set[int], limit: int = 2) -> list[dict]:
    """从**这一节已经引到的那几篇**原文里，挑作者自己写了否定/局限的句子。

    为什么不用 `evidence.survey(节首句)` 的 against 来找：`g72` 三个情景 19 节 against 恒为 0，
    而逐篇扫池能扫出每池 1—4/8 篇带 "However, …" 的原文句（`g72_counter_supply.txt`）。
    机制在 `_tokens`：中文按三元组切、英文按词切，**中文论断与英文原文词面永远不相交**，
    重合词元凑不满门槛，于是既数不出反证也证明不了分歧。所以找料按「本节已引到的篇」定位，
    **验收仍用 survey 那把尺**——并要求新首句照抄原文术语，否则读者也核对不回原文。
    """
    from companion.literature import evidence

    # `survey` 只认池子里"少数篇才有"的词（`rare_tokens`），套话一律丢掉。
    # 所以要的句子里必须至少留着 `MIN_TOKENS` 个稀有词，否则模型照抄也核不回来 ——
    # `g88` 那次退回（编号挂上=True、支持 0 相反 0）就是这个形状：料是真的，但抄不动。
    rare = evidence.rare_tokens(papers)
    out: list[dict] = []
    for n in sorted(only):
        if not 1 <= n <= len(papers):
            continue
        hit = None
        for label, body in evidence.sources(papers[n - 1]):
            for sent in evidence._sentences(body) or ([body.strip()] if body.strip() else []):
                if not evidence.NEGATION_RE.search(sent):
                    continue
                if rare is not None and len(evidence._tokens(sent) & rare) < evidence.MIN_TOKENS:
                    continue
                hit = {"idx": n, "title": str(papers[n - 1].get("title") or "")[:120],
                       "source": label, "text": sent[:400]}
                break
            if hit:
                break
        if hit:
            out.append(hit)
            if len(out) >= limit:
                break
    return out


def _counterev_facts(material: list[dict], papers: list[dict] | None = None) -> str:
    """把 `_limitation_hits` 找到的原文拼成递给模型的那段事实与写法要求。

    单独成函数是为了让**测量能复用同一段文本**：`probe_counterev_position.py` 要在不改写
    提示词的前提下量"反证必须落在首句"这个要求损失了多少落地率。措辞留在函数里而不在
    调用点复制一份，量的和发的才是同一句话。
    """
    from companion.literature import evidence

    hits = "；".join(f"[{m['idx']}]《{m['title']}》（{m['source']}）：{m['text']}" for m in material)
    terms = sorted({t for m in material for t in evidence._tokens(m["text"])
                    if t.isascii() and not t.isdigit() and len(t) > 4})
    if papers is not None:
        # 递给模型的"可抄词"必须先过 `survey` 自己那把尺：池子里到处都有的套话会被它整个丢掉，
        # 让模型照抄一批抄了也核不回的词，就是 `g88` 那次「编号挂上=True、支持 0 相反 0」的形状。
        rare = evidence.rare_tokens(papers)
        if rare is not None:
            terms = [t for t in terms if t in rare] or terms
    terms = terms[:6]
    return (f"库里核对：这几篇原文里作者自己写了否定或局限（下面引号内的英文是原文，一个字都不许改写）："
            f"{hits}\n"
            f"写法要求：第一句就点出这套做法的争议或受限之处，句末挂那条对应的 [编号]，"
            f"并在同一句里用括号**逐字照抄那条原句里连续的 4—6 个英文词**"
            f"（照抄两个词以内，程序就核不回原文；单句可用的词有：{', '.join(terms)}）。"
            f"纯中文转述读者也核对不回原文，所以必须带这段原文；"
            f"不得新增任何数字、百分比或年份；其余句子能挂上 [n] 的保留、挂不上的删掉。\n\n")


def _counterev_sentence_prompt(intent: Any, papers: list[dict], section: str, current: str,
                               facts: str) -> str:
    """只让模型写**那一句**反证，由程序插到节首——不再整节重写。

    为什么改（`g87`，6 小节 × 4 个核对口径）：
    - 首句过关率 17%、整节 50%（3.0 倍、+33pp）→ 预登记判据①成立，**位置是主卡点**；
    - 而唯一一次首句就过关的改写被"接地率不得下降"退回（0.833→0.417）：
      整节重写会顺手重排无关句子，把它们的无据表述一起算进分母（分割那节 1.0→0.5 同因）。
    只写一句 + 程序插入之后：原节一字不动 → 位置问题消失，接地率也不会被无关重排拖低，
    `cov_new ≥ cov_old` 从此只在"新写的这句自己不带依据"时失败——那正是它该失败的时候。
    """
    return (f"下面这份综述里「{section}」这一节现在的样子：\n{current.strip()[:1200]}\n\n"
            f"可引用的论文（编号就是角标）：\n{mentor.papers_block(papers)}\n\n"
            f"{facts}"
            f"只写**一句话**（不超过 80 字）：指出上面这些论文里已有的证据与这一节论断的冲突或局限，"
            f"句末加 [n] 指向该原文所在编号。不要重写本节、不要解释、不要引号、不要 ## 标题，"
            f"只输出这一句。")


def _counter_evidence(intent: Any, papers: list[dict],
                      body: str) -> tuple[str, list[str], list[str]]:
    """把库里**真有的**局限原文注入最弱那一节（一次模型调用，最多一节，**只写那一句**）。

    为什么不靠提示词：`g72` 相反证据率 0/13=0%，`g68` 那个 11% 里的 1 还落在自动生成的
    「引用论文」节——正文等于零。提示词只能让模型更想说「但也有争议」，不给它原文就只能编，
    那比没有分歧更糟。所以程序把带局限的那句原文连编号递给模型。
    改完用同一把尺复算：survey 数不出反证、编号没挂上、接地率下降，一律整节退回。

    🔴 从 `g87` 起改成"只写一句 + 程序插到节首"（见 `_counterev_sentence_prompt`）：
    整节重写会让无关句子被重排，接地率被一起拖下去（实测 0.833→0.417、1.0→0.5），
    而它同时是"反证写在第二句之后"这种位置退回的根源。

    第三个返回值是「为什么没落地」的说明。`g75` 情景 1 实测到「有料 2 篇、落地=空」，
    可退回到底是没挂编号、复算数不出反证、还是调用超时，正文里查不到第二份证据——
    这跟轮 106 P0 修的是同一个毛病：只报结果不报分支，下一轮就只能猜。

    试过再给一版（把失败原因回递给模型）但 `g78` 三情景仍 0 落地，白多一次调用，
    已撤回；实现在 493d6f3 里，下一轮若把「引文单独成句」做进去可以再拿回来。
    """
    from llm import LLM_MOCK, chat, run_async

    from companion.literature import evidence
    if LLM_MOCK or not _pool_has_text(papers):
        return body, [], ["闸门没开工：论文池里带原文的不到两篇"]
    # 轮 178：反证这一路的三处 coverage 判定**不开桥**（轮 174 开过，g183 撤的，理由见
    # `clean_report_with_ledger` 上面那段）。开桥会把 `true_gap` 读成"有依据"，
    # 而这条判据的用途是**决定要不要把一段新话插进报告正文**——判错的方向是往报告里写东西。
    cands = []
    for i, (head, text) in enumerate(split_sections(body)):
        if not head or head in LOCKED_SECTIONS or not text.strip():
            continue
        cov = evidence.grounding_report(text, papers)["coverage"]
        cited = {int(x) for x in re.findall(r"\[(\d{1,2})\]", text)}
        cands.append((cov if cov is not None else 2.0, i, head, text, cited))
    cands.sort(key=lambda c: c[0])               # 最弱先试；一条引用都没有的节归补引用闸门管
    picked = None
    for _cov, i, head, text, cited in cands:
        material = _limitation_hits(papers, cited)
        if material:
            picked = (i, head, text, material)
            break
    if picked is None:
        return body, [], [f"找料落空：{len(cands)} 个小节所引的论文里都没有否定/局限句"]
    i, head, old_text, material = picked
    facts = _counterev_facts(material, papers)
    cov_old = evidence.grounding_report(old_text, papers)["coverage"]
    try:
        raw = run_async(chat(_counterev_sentence_prompt(intent, papers, head, old_text, facts),
                             system=SYSTEM, max_tokens=220, temperature=0.5, timeout=120.0))
    except Exception as exc:  # noqa: BLE001 — 注入失败就保留原节，不能把整篇写坏
        return body, [], [f"「{head}」改写的模型调用没成：{type(exc).__name__}"]
    # 模型若不安分地多写了几句，只取它的第一句；插进节首时原节其余文字一个字符都不动
    raw_sentence = _first_sentence(raw).strip()   # 判"编号是它自己挂的还是程序替它挂的"要用这一份
    sentence = _first_sentence(clean_report(raw, papers)).strip()
    if not sentence:
        return body, [], [f"「{head}」模型没交回可用的句子（改写：{str(raw)[:60]}）"]
    new_text = chr(10) + sentence + chr(10) + old_text.strip(chr(10)) + chr(10)
    sv_new = evidence.survey(sentence, papers)
    # 改前/改后必须同一把尺（轮 178 起两侧都是**字面尺**，桥被 g183 撤回）：
    # 两侧口径不一致时，"新句没带依据"会被尺子换向读成进步。
    cov_new = evidence.grounding_report(new_text, papers)["coverage"]
    # 只看**新插的那一句**有没有挂上局限句的编号：整节文字原样保留，
    # 在 new_text 上找会得到恒真——原节本来就带 [1]，这条检查会当场变成假绿
    # （正是改完之后 test_gate_rejects_a_rewrite_without_the_citation 红出来告诉我的）。
    # 🔴 而且必须看**清洗前模型原样交回的那句**：`clean_report` 里含机械改指（轮 174 起还带桥），
    #    模型自己没挂对编号时，是程序替它挂上的——拿改后的文本判，等于闸门给自己发合格章。
    cited_ok = any(f"[{m['idx']}]" in raw_sentence for m in material)
    if not (cited_ok and sv_new["checked"] >= 2 and sv_new["against"] >= 1
            and cov_new is not None and (cov_old is None or cov_new >= cov_old)):
        return body, [], [f"「{head}」改写没通过复算，整节退回："
                          f"局限句的编号挂上={cited_ok}"
                          f"｜新首句复算 支持 {sv_new['support']} 相反 {sv_new['against']} 核对 {sv_new['checked']}"
                          f"｜接地 {cov_old}→{cov_new}"]
    sections = split_sections(body)
    sections[i][1] = new_text
    return join_sections(sections), [head], []


def rewrite_section(project_id: str, intent: Any, papers: list[dict], title: str,
                    section: str, instruction: str = "") -> dict:
    """只重写综述里的某一节，其余节逐字保留——改完仍过同一套落地闸门。"""
    from llm import LLM_MOCK, chat, run_async

    section = (section or "").strip()
    if section in LOCKED_SECTIONS:
        return {"ok": False, "note": f"「{section}」是按论文清单自动生成的，改它得回论文卡那边操作"}

    raw_note = knowledge.get_note(project_id, title)
    if raw_note is None:
        return {"ok": False, "note": f"没有找到笔记《{title}》，请先生成综述"}
    papers = citable_papers(papers)
    if not papers:
        return {"ok": False, "note": "本项目还没有论文，无法重写"}

    body, _tags = knowledge.strip_front(raw_note)
    sections = split_sections(body)
    idx = next((i for i, (h, _t) in enumerate(sections) if h == section), None)
    if idx is None:
        return {"ok": False, "note": f"综述里没有「{section}」这一节"}

    degraded = bool(LLM_MOCK)
    raw = ""
    if degraded:
        new_text = f"\n{mentor.DEGRADE_PREFIX}（降级：未调用 LLM，这一节保持原样）\n"
    else:
        try:
            raw = run_async(chat(_section_prompt(intent, papers, section, sections[idx][1], instruction),
                                  system=SYSTEM, max_tokens=900, temperature=0.5, timeout=120.0))
        except Exception as exc:  # noqa: BLE001 — 重写失败就保留原节，不能把整篇写坏
            return {"ok": False, "note": f"这一节重写暂时不可用（{str(exc)[:120]}）"}
        new_text = "\n" + clean_report(raw, papers) + "\n"

    removed = mentor.unverified_titles(raw, papers) if raw else []
    sections[idx][1] = new_text
    merged = join_sections(sections)
    knowledge.upsert_note(project_id, title, merged, tags=["report"])
    return {"ok": True, "title": title, "section": section, "text": new_text.strip(),
            "body": merged, "words": len(merged), "removed_titles": removed,
            "degraded": degraded, "untouched": [h for i, (h, _t) in enumerate(sections)
                                                if h and i != idx]}


def _finalize(project_id: str, intent: Any, title: str, papers: list[dict], raw: str,
              degraded: bool, used_matrix: bool) -> dict:
    """过闸门 → 补小节 → 拼双链 → 落知识库。生成路径只有一条，流式与非流式共用。"""
    missing: list[str] = []
    cited_fixed: list[str] = []
    counter_fixed: list[str] = []
    counter_note: list[str] = []
    renumbered = 0
    if degraded:
        body = mentor.DEGRADE_PREFIX + "\n\n" + "\n\n".join(
            f"## {s}\n（降级骨架，未调用 LLM）" for s in SECTIONS)
    else:
        body, renumbered = clean_report_with_ledger(raw, papers)
        body, missing = normalize_sections(body)
        body, cited_fixed = _cite_missing_sections(intent, papers, body)
        body, counter_fixed, counter_note = _counter_evidence(intent, papers, body)
    removed = mentor.unverified_titles(raw, papers) if (raw and not degraded) else []
    links = "\n".join(f"- [[论文：{p['title']}]]" for p in papers)
    body = f"{body}\n\n## 引用论文\n{links}\n"
    note_name = knowledge.upsert_note(project_id, title, body, tags=["report"])
    return {"ok": True, "title": title, "body": body, "note_name": note_name,
            "words": len(body), "sections": re.findall(r"^## (.+)$", body, re.M),
            "papers": len(papers), "removed_titles": removed, "sections_missing": missing,
            "degraded": degraded, "used_matrix": used_matrix,
            "sections_cited": cited_fixed, "sections_counterev": counter_fixed,
            "counterev_note": counter_note,
            # 只记整篇那一次清洗挪动的角标数；逐节补引用与反证注入各自在
            # sections_cited / sections_counterev 里记账，混进来会让这个数变成多因子
            "citations_renumbered": renumbered}


async def stream_report(project_id: str, intent: Any, papers: list[dict],
                        matrix: dict | None = None):
    """逐段吐字写综述：产出 (event, payload)，event ∈ delta / done / error。

    整篇一次性生成时，前端会在几十秒里毫无反馈——线上真的出现过"看起来卡死"的超时。
    """
    from llm import LLM_MOCK, chat_stream

    papers = citable_papers(papers)
    if not papers:
        yield ("error", {"ok": False, "note": "还没有论文，先聊到出结果再写综述"})
        return
    title = f"综述：{(getattr(intent, 'topic', '') or papers[0]['title'])[:40]}"
    raw = ""
    try:
        async for piece in chat_stream(build_prompt(intent, papers, matrix), system=SYSTEM,
                                       max_tokens=2600, temperature=0.4, timeout=180.0):
            raw += piece
            yield ("delta", {"text": piece})
    except Exception as exc:  # noqa: BLE001 — 生成失败要如实报，不落半篇进知识库
        yield ("error", {"ok": False, "note": f"综述生成暂时不可用（{str(exc)[:120]}）"})
        return
    yield ("done", _finalize(project_id, intent, title, papers, raw, bool(LLM_MOCK),
                             bool(matrix and matrix.get("rows"))))


def build_report(project_id: str, intent: Any, papers: list[dict],
                 matrix: dict | None = None) -> dict:
    """非流式入口：内部走同一条生成路径，只是把增量攒起来。"""
    from llm import run_async

    async def _collect() -> dict:
        last: dict = {"ok": False, "note": "没有产出"}
        async for event, payload in stream_report(project_id, intent, papers, matrix):
            if event in ("done", "error"):
                last = payload
        return last

    return run_async(_collect())
