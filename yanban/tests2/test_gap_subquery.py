"""P3 缺口→受控子查询：先写会咬人的判据，再动 `companion/literature/engine.py`。

为什么是这条：`gaps` 过去只喂反问（`dialogue_manager` 里 `turn["gaps"]` 只进 `gap_labels`/`next_chips`），
从不喂检索；`search_for_intent` 一次成型。调研结论里三类"何时再检索一次"的触发中，
FLARE/DRAGIN 要 logprobs（GLM 通道不给）、Self-RAG/Adaptive-RAG 的分类器论文自报 54.52%，
只有 FAIR-RAG 那类**机械外部信号**（显式缺口）便宜且可核对。

既有实测事实必须尊重（`_query_from_intent` 的 docstring 记着）：把"近两年/开放获取/只学过一点"
这类**偏好原词**拼进检索式，英译会产出 recent/open/access/deep/learning 这种跨领域通用词，
相关性判定从此分不出钓鱼检测与生态学综述。所以这里的子查询只许用**受控词表的文献类型面词**，
且相关性门槛**沿用主轮的 tokens**（补进来的论文必须过同一把尺子）。

判据：
  1 词表派生自 FIELD_QUESTIONS 的字段名，项必须是拉丁文面且不在 GENERIC_TOKENS 里（否则等于加了个停用词）。
  2 没缺口 ⇒ 一次额外调用都不发（负测，挡"每次都多打一轮"）。
  3 有缺口 ⇒ 每轮硬上限 1 次补检索（即使命中多个缺口）。
  4 补进来的篇数硬上限 MAX_SUBQUERY_PAPERS，且这个上限**会随常量变小而变小**（挡恒真上限）。
  5 相关性门槛不因补检索降低：离题的补检索结果必须被滤掉。
  6 记账 `intent.extra["subquery"]` 的 added 必须等于 after-before，且与真新增的 id 集合一致。
"""
from __future__ import annotations

import pytest

from companion.intent.clarification import FIELD_QUESTIONS
from companion.intent.model import ResearchIntent
from companion.literature import engine

MAIN_Q = "phishing site detection"


def _paper(title: str, abstract=None, year: int = 2024, doi: str = "") -> dict:
    return {"title": title, "abstract": abstract or f"about {title}", "year": year, "doi": doi}


def _stub(monkeypatch, main_papers, sub_papers=None, facet: str = "survey"):
    """把四个 provider 换成记账假件：主轮返回 main_papers，带 facet 的式子返回 sub_papers。"""
    seen: list[str] = []
    from companion.literature.providers import crossref_arxiv, openalex, semantic_scholar

    async def fake(self, query, limit=10):
        seen.append(query)
        if sub_papers is not None and query.endswith(facet):
            return list(sub_papers)
        return list(main_papers)

    for cls in (crossref_arxiv.CrossrefProvider, crossref_arxiv.ArxivProvider,
                openalex.OpenAlexProvider, semantic_scholar.SemanticScholarProvider):
        monkeypatch.setattr(cls, "safe_search", fake)
    async def no_translate(q):
        return q
    monkeypatch.setattr(engine, "_english_query", no_translate)
    return seen


def _intent(**kw) -> ResearchIntent:
    it = ResearchIntent(project_id="t-gapsub", topic=MAIN_Q)
    for k, v in kw.items():
        setattr(it, k, v)
    return it


def test_facet_table_is_derived_and_in_the_controlled_vocab():
    """词表不许悬空、不许自由发挥：键必须是反问在问的字段，值必须是声明过的体裁词。"""
    fields = {f for f, _w, _q in FIELD_QUESTIONS}
    assert set(engine.GAP_FACETS) <= fields, f"词表里有 FIELD_QUESTIONS 之外的字段：{set(engine.GAP_FACETS) - fields}"
    assert engine.GAP_FACETS, "词表为空 ⇒ 整条链路是装饰"
    assert set(engine.GAP_FACETS.values()) <= engine.FACET_VOCAB, "出现了词表外的体裁词"
    for field, term in engine.GAP_FACETS.items():
        assert term.isascii() and " " not in term and term == term.lower(), (
            f"{field} 的 facet 不是单个拉丁词：{term!r}（中文/多词式送英文库是已实测的污染源）")


@pytest.mark.asyncio
async def test_no_gap_means_zero_extra_calls(monkeypatch):
    """事实已经够了（没有映射到词表的缺口）⇒ 一次额外检索都不许多发。"""
    seen = _stub(monkeypatch, [_paper(f"phishing site detection survey paper {i}") for i in range(5)])
    it = _intent()
    it.research_stage = "exploration"
    it.publication_goal.type = "sci_journal"
    it.paper_preferences.year_from = 2022
    it.user_background.dl_level = "beginner"
    it.user_background.llm_level = "beginner"
    it.user_background.paper_reading_level = "advanced"
    it.research_constraints.compute = "laptop"
    await engine.search_for_intent_async(it, limit=8)
    extra = [q for q in seen if any(q.endswith(t) for t in engine.FACET_VOCAB)]
    assert not extra, f"没有缺口却补了检索：{extra}"
    assert len(seen) == 4, f"主轮应是 1 条式子 × 4 个源 = 4 次，实为 {len(seen)}（多出来的就是补检索）"
    assert "subquery" not in it.extra


@pytest.mark.asyncio
async def test_gap_fires_at_most_one_extra_round(monkeypatch):
    """缺 research_stage 与缺 dl_level 同时成立 ⇒ 仍然只补一轮（硬上限）。"""
    facet = engine.GAP_FACETS["research_stage"]
    seen = _stub(monkeypatch, [_paper("phishing site detection baseline")],
                 [_paper(f"phishing site detection {facet} review 2024")], facet=facet)
    it = _intent()
    await engine.search_for_intent_async(it, limit=8)
    subs = {q for q in seen if q.endswith(facet)}
    assert subs == {f"{MAIN_Q} {facet}"}, f"补检索的式子不对：{subs}"
    # 一轮 = 四个源各打一次；主轮 4 次 + 补检索 4 次 = 8。多出来的轮次会在这里露出来
    assert len(seen) == 8, f"调用次数 {len(seen)} ≠ 主轮 4 + 补检索 4 ⇒ 每轮 ≤1 次补检索没接住"


@pytest.mark.asyncio
async def test_subquery_added_papers_are_capped(monkeypatch):
    """假件一次回 50 篇 ⇒ 真加进来的不得超过 MAX_SUBQUERY_PAPERS。"""
    facet = engine.GAP_FACETS["research_stage"]
    many = [_paper(f"phishing site detection {facet} study {i}", doi=f"10.1/sub{i}") for i in range(50)]
    _stub(monkeypatch, [_paper("phishing site detection baseline", doi="10.1/main")], many, facet=facet)
    it = _intent()
    out = await engine.search_for_intent_async(it, limit=50)
    added = [p for p in out if p["doi"].startswith("10.1/sub")]
    assert len(added) <= engine.MAX_SUBQUERY_PAPERS, (
        f"补检索一次加进 {len(added)} 篇 > 上限 {engine.MAX_SUBQUERY_PAPERS} ⇒ 上限没接进生产路径")


@pytest.mark.asyncio
async def test_cap_shrinks_when_constant_shrinks(monkeypatch):
    """上限必须**会随常量动**：钉死一个数不算上限（挡恒真判据）。"""
    facet = engine.GAP_FACETS["research_stage"]
    many = [_paper(f"phishing site detection {facet} study {i}", doi=f"10.2/sub{i}") for i in range(30)]
    it = _intent()
    monkeypatch.setattr(engine, "MAX_SUBQUERY_PAPERS", 2)
    _stub(monkeypatch, [_paper("phishing site detection baseline", doi="10.2/main")], many, facet=facet)
    out = await engine.search_for_intent_async(it, limit=50)
    assert len([p for p in out if p["doi"].startswith("10.2/sub")]) <= 2


def test_the_registered_caps_are_not_quietly_raised():
    """上一条与上一条**都抓不到"把常量抬大"**：断言读的就是那个常量，抬多高都恒真。

    本轮实测过：把 `MAX_SUBQUERY_PAPERS` 从 4 改成 999，那两条测试照旧 7/7 绿——
    闸门只挡"没接线"，不挡"接了根粗管子"。所以契约值本身必须有一处钉住并写明**为什么是这个数**：
    4 = 9 对可用对的 A/B 里一次补进 4 篇已经开始摊薄 recall（`g157_gap_subquery_ab.txt`），
    1 = 每轮最多补一次；抬它要新的实测，不是顺手改数。
    """
    assert engine.MAX_SUBQUERY_ROUNDS == 1, "每轮补检索次数契约被改：需要新的实测理由（见轮 154 A/B）"
    assert engine.MAX_SUBQUERY_PAPERS == 4, "每次补进篇数契约被改：需要新的实测理由（见轮 154 A/B）"


@pytest.mark.asyncio
async def test_relevance_bar_is_not_lowered_by_the_extra_round(monkeypatch):
    """补检索带回落后的离题文献 ⇒ 必须被**主轮同一把尺子**滤掉。

    夹具用拉丁文离题标题（轮 33 的 UAV 那一类）：产品自己的 `filter_relevant` 对
    "标题文字与 tokens 不同种"的条目是**放行**的（`_tokens_for` 拿不到同种词 ⇒ 判不了不等于离题），
    所以拿一条中文标题来测会测到那条既有豁免上，量不到我要量的东西。
    """
    facet = engine.GAP_FACETS["research_stage"]
    junk = _paper("UAV aerial photogrammetry for crop monitoring", doi="10.3/junk")
    _stub(monkeypatch, [_paper("phishing site detection baseline", doi="10.3/main")],
          [junk, _paper(f"phishing site detection {facet} work", doi="10.3/good")], facet=facet)
    it = _intent()
    out = await engine.search_for_intent_async(it, limit=20)
    titles = " | ".join(p["title"] for p in out)
    assert "UAV" not in titles, f"离题文献经补检索混进来了：{titles}"
    assert any(facet in p["title"] for p in out), f"补检索什么都没买到，但这一场该买到：{titles}"


@pytest.mark.asyncio
async def test_subquery_ledger_agrees_with_the_numbers(monkeypatch):
    """记账不许是第二份算术：added == after - before == 真新增 id 数。"""
    facet = engine.GAP_FACETS["research_stage"]
    many = [_paper(f"phishing site detection {facet} study {i}", doi=f"10.4/sub{i}") for i in range(4)]
    _stub(monkeypatch, [_paper("phishing site detection baseline", doi="10.4/main")], many, facet=facet)
    it = _intent()
    await engine.search_for_intent_async(it, limit=20)
    led = it.extra.get("subquery")
    assert led, "补检索发生了却没记账 ⇒ 买到了什么无从核对"
    assert led["gap"] in engine.GAP_FACETS
    assert led["added"] == led["after"] - led["before"], f"账不平：{led}"
    assert led["added"] <= engine.MAX_SUBQUERY_PAPERS


@pytest.mark.asyncio
async def test_subquery_ledger_records_which_papers_arrived(monkeypatch):
    """轮 159：账上必须写清**带回来了哪几篇**，否则「这几篇撑住了哪句」永远核对不了。

    为什么要单独钉：`added` 只是个数量。轮 158 判 P3 判不动的那件事（补检索到底有没有材料）
    需要逐句对账，而当时的台账里只有六个数字、没有任何身份 ⇒ 事后无法重建"哪几篇是补的"。
    """
    facet = engine.GAP_FACETS["research_stage"]
    many = [_paper(f"phishing site detection {facet} study {i}", doi=f"10.4/sub{i}") for i in range(4)]
    _stub(monkeypatch, [_paper("phishing site detection baseline", doi="10.4/main")], many, facet=facet)
    it = _intent()
    out = await engine.search_for_intent_async(it, limit=20)
    led = it.extra["subquery"]
    ids, titles = led["added_ids"], led["added_titles"]
    assert len(ids) == led["added"] == len(titles), f"身份数与 counted 不符：{led['added']} vs {ids}"
    assert len(set(ids)) == len(ids), f"同一篇记了两次：{ids}"
    by_id = {p["id"]: p for p in out}
    assert all(i in by_id for i in ids), "记的身份在返回的池子里找不到 ⇒ 账是编的"
    assert [by_id[i]["title"] for i in ids] == titles, "titles 必须与 ids 同序同源（配对不能错行）"


@pytest.mark.asyncio
async def test_frozen_n6_cross_round_duplicate_enters_the_pool_twice(monkeypatch):
    """现状冻结（缺陷 N6，本轮按人的决定**只登记不修**）：跨轮去重拿每轮新铸的 id 比，恒不命中。

    这条钉的是"症状长什么样"，不是"应该这样"：同一篇被主轮和补检索都取到时，它会重复进池、
    `added` 把重复的那份也算成新增。谁去修那段，这条会红——必须连同本条与
    `docs/METHOD_BACKLOG.md` 的 N6 一起改口，而不是悄悄把期望从「重复」换成「不重复」。
    """
    facet = engine.GAP_FACETS["research_stage"]
    shared = _paper(f"phishing site detection {facet} study shared", doi="10.4/dup")
    # 主轮给满 4 篇：少于 4 会触发 `_backfill_query` 那一轮，那条路会先按篇名去重，
    # 就测不到「补检索这一轮带回来一篇主轮已经有的」这个现场了。
    main = [shared] + [_paper(f"phishing site detection baseline {i}", doi=f"10.4/main{i}")
                       for i in range(3)]
    _stub(monkeypatch, main, [shared], facet=facet)
    it = _intent()
    out = await engine.search_for_intent_async(it, limit=20)
    dup = [p for p in out if p["title"] == shared["title"]]
    led = it.extra["subquery"]
    assert len(dup) == 2 and led["added"] == 1, (
        f"症状变了：重复入库这条现状不再成立（dup={len(dup)} added={led['added']}）"
        "⇒ N6 已修，请连同本条与方法论队列里的 N6 一起改口")
    assert len(set(led["added_ids"])) == len(led["added_ids"]) == 1
