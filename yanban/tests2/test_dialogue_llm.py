"""研伴 2.0 · 对话灵魂层与检索链的回归测试（全部在 LLM_MOCK 下，零真实调用）。

覆盖本次审计改造引入的行为：回复走 LLM 通道、上下文装配、防幻觉硬闸门、
一次一问的重采样、英译回退标记、反馈参与排序、TLDR 兜底、知识库导出。
"""
import os

os.environ["LLM_MOCK"] = "1"  # 与同目录其它测试一致：import 期定值

import asyncio
import re

import pytest

from companion import knowledge
from companion.conversation import mentor
from companion.intent import extractor
from companion.intent.model import ResearchIntent
from companion.literature import engine


def _intent(topic="钓鱼网站检测", text="深度学习学过一点"):
    it = ResearchIntent(project_id="t-llm", topic=topic)
    it.apply_update(extractor.extract_update(text, it))
    return it


# ── mentor：回复来源与上下文装配 ─────────────────────────────────────
def test_reply_comes_from_llm_channel_not_template():
    """P0-1 的回归门：MOCK 下回复必须是 LLM 通道的产物，而不是写死的模板串。"""
    it = _intent()
    text, degraded = mentor.ask(user_text="我先说说我的想法", intent=it)
    assert not degraded
    assert text.startswith("[MOCK-LLM]")
    assert "先跟你对齐两件事" not in text


def test_prompt_carries_history_memory_and_intent():
    it = _intent()
    prompt = mentor.build_prompt(
        user_text="我手头有一批 CSV 数据", intent=it,
        history=[{"role": "user", "content": "我是第一次做科研"}, {"role": "assistant", "content": "那先把范围收窄"}],
        memory_ctx="- [user] DL 学过一点", gaps=["publication_goal.type"],
        papers=[{"title": "PhishGNN", "year": 2022, "recommendation_reason": ["被引 24"]}])
    assert "我是第一次做科研" in prompt          # 承接上文：近 N 轮原文进上下文
    assert "DL 学过一点" in prompt               # 记忆检索进上下文
    assert "PhishGNN" in prompt                  # 候选论文（只能引用这些）
    assert "publication_goal.type" in prompt     # 缺口驱动提问
    assert "钓鱼网站检测" in mentor.intent_snapshot(it)
    assert "深度学习" in mentor.intent_snapshot(it)


# ── mentor：两条硬闸门 ───────────────────────────────────────────────
def test_invented_paper_titles_are_stripped():
    """竞赛红线：论文标题只能来自 Provider，模型编造就整句剔除。"""
    reply = "先看这篇。《根本不存在的钓鱼检测大论文》很适合你。第二轮我再给你几篇综述。"
    cleaned = mentor.strip_unverified(reply, [{"title": "PhishGNN: Graph Neural Networks"}])
    assert "根本不存在的" not in cleaned
    assert "第二轮" in cleaned                   # 只删违规句，不误伤其它内容
    assert mentor.unverified_titles(reply, [{"title": "PhishGNN: Graph Neural Networks"}])


def test_repair_resamples_when_more_than_one_question(monkeypatch):
    calls = []

    async def fake_chat(prompt, **kw):
        calls.append(prompt)
        return "你学过深度学习吗？那你时间够吗？" if len(calls) == 1 else """建议你先精读 5 篇近两年的综述，把方法谱系画成一张图。可以先拿 12k 条 URL 跑一遍 XGBoost 基线，两周能看到 90% 左右的准确率。第三步用 3 张公开数据集各跑 1 次对照，1 周内出同一口径的对比表。你导师更看重期刊还是会议？"""

    monkeypatch.setattr(mentor, "chat", fake_chat)
    text, degraded = mentor.ask(user_text="随便聊聊", intent=_intent())
    assert not degraded
    assert len(calls) == 2                       # 超标触发一次重采样
    assert mentor.question_count(text) == 1
    assert "注意" in calls[1]                    # 重采样带上了纠正语


# ── 首轮假称记得（线上实测 1/3 概率，probe_memory_phrasing.py）──────────
def test_false_memory_only_counts_when_there_is_no_history():
    assert mentor.false_memory("你之前提到想做检测", [])
    assert mentor.false_memory("你之前提到想做检测", [{"role": "user", "content": "我想做检测"}]) == []
    assert mentor.false_memory("你说想做检测，目标三个月。", []) == []


def test_rewrite_false_memory_keeps_the_sentence_reading_naturally():
    assert mentor.rewrite_false_memory("你之前提到想做多模态检测，目标是 SCI。") == "你说想做多模态检测，目标是 SCI。"
    assert mentor.rewrite_false_memory("我记得你说过第一次做科研。") == "你说第一次做科研。"


def test_repair_resamples_then_rewrites_first_turn_false_memory(monkeypatch):
    calls = []

    async def fake_chat(prompt, **kw):
        calls.append(prompt)
        return "你之前提到想做钓鱼检测，我这就去查几篇。"      # 重采样仍然不守规矩

    monkeypatch.setattr(mentor, "chat", fake_chat)
    text = mentor.repair("你之前提到想做钓鱼检测，我这就去查几篇。", user_text="我想做钓鱼检测",
                         intent=_intent(), history=[], papers=[])
    assert len(calls) == mentor.REPAIR_ROUNDS and "第一句话" in calls[0]   # 两轮纠正语都点名了这条约束
    assert "之前提到" not in text and text.startswith("你说")   # 改写兜底真的把话接回来了


def test_system_prompt_states_the_first_turn_rule():
    assert "第一句话" in mentor.SYSTEM and "最近对话原文" in mentor.SYSTEM


# ── 线上实测到的两个残留缺陷：全角角标 + 断在半空的编号 ─────────────────
def test_full_width_brackets_are_normalised_so_the_anchor_gate_can_see_them():
    assert mentor.clean("给你三篇［1］［2］") == "给你三篇[1][2]"


def test_repair_removes_dangling_anchor_and_the_empty_numbering_it_leaves():
    out = mentor.repair("我这就去查几篇。1. 2. 3.[1]", user_text="想做钓鱼检测",
                        intent=_intent(), history=[], papers=[])
    assert "[1]" not in out and "1. 2. 3." not in out
    assert out == "我这就去查几篇。"


def test_strip_empty_numbering_keeps_real_lists_and_numbers():
    assert mentor.strip_empty_numbering("我查一下。1. 2. 3.") == "我查一下。"
    assert mentor.strip_empty_numbering("1. 第一篇\n2. 第二篇") == "1. 第一篇\n2. 第二篇"
    assert mentor.strip_empty_numbering("收到 3.5 分") == "收到 3.5 分"


# ── 实质内容闸门（配对盲评实测：只承诺不给东西的回复 5 战 4 负）──────────
def test_has_substance_recognises_advice_citation_and_nothing():
    assert mentor.has_substance("建议先把范围收到 URL 文本模态，用 XGBoost 做基线。") is True
    assert mentor.has_substance("1. An effective detection approach for phishing") is True
    assert mentor.has_substance("你之前做过类似的活儿吗？") is False
    assert mentor.has_substance("") is False


def test_no_substance_flags_promise_only_and_bare_question():
    assert mentor.no_substance("我这就去查几篇相关的论文，给你一些建议。") is True
    assert mentor.no_substance("你的目标读者是谁？") is True
    assert mentor.no_substance("我建议你先只看 URL 模态；那你的数据到手了吗？") is False


def test_repair_resamples_when_the_reply_gives_nothing(monkeypatch):
    calls = []

    async def fake_chat(prompt, **kw):
        calls.append(prompt)
        return """建议你先精读 5 篇近两年的综述，把方法谱系画成一张图。可以先拿 12k 条 URL 跑一遍 XGBoost 基线，两周能看到 90% 左右的准确率。第三步用 3 张公开数据集各跑 1 次对照，1 周内出同一口径的对比表。你导师更看重期刊还是会议？"""

    monkeypatch.setattr(mentor, "chat", fake_chat)
    out = mentor.repair("我这就去查几篇相关的论文。", user_text="我想做钓鱼检测",
                        intent=_intent(), history=[{"role": "user", "content": "我想做钓鱼检测"}], papers=[])
    assert len(calls) == 1
    assert "当下没给学生任何能用上的东西" in calls[0]      # 纠正语点名了这条约束
    assert "建议" in out


def test_second_repair_round_names_whats_still_missing(monkeypatch):
    """第二轮不是把同一句纠正再念一遍——线上实测到"补一轮后模型交回一条同样空的"，
    所以纠正语按"现在还缺什么"重算：先缺实质，再缺数量。"""
    calls = []

    async def fake_chat(prompt, **kw):
        calls.append(prompt)
        if len(calls) == 1:
            return "建议你先把这个方向的综述读一遍，也可以顺手看看常用的数据集。"
        return ("建议你先精读 5 篇近两年的综述。可以先拿 12k 条 URL 跑一遍 XGBoost 基线，"
                "两周能看到 90% 的准确率。"
                "第三步用 3 张公开数据集各跑 1 次对照，1 周内出同一口径的对比表。")

    monkeypatch.setattr(mentor, "chat", fake_chat)
    out = mentor.repair("我这就去查几篇相关的论文。", user_text="我想做钓鱼检测", intent=_intent(),
                        history=[{"role": "user", "content": "我想做钓鱼检测"}], papers=[])
    assert len(calls) == 2 and mentor.REPAIR_ROUNDS >= 2
    assert "当下没给学生任何能用上的东西" in calls[0]
    assert "数量、时长或指标" in calls[1]                 # 第二轮点名的是数量
    assert "当下没给学生任何能用上的东西" not in calls[1]  # 已经不缺实质内容了
    assert "12k" in out


def test_repair_resamples_when_advice_carries_no_quantities(monkeypatch):
    """第五道闸门要真的接进 repair：只"建议读综述"、一句数量都没有 → 点名重采样。"""
    calls = []

    async def fake_chat(prompt, **kw):
        calls.append(prompt)
        return ("建议你先精读 5 篇近两年的综述。可以先拿 12k 条 URL 跑一遍 XGBoost 基线，"
                "准确率到 90% 再谈多模态。"
                "第三步用 3 张公开数据集各跑 1 次对照，1 周内出同一口径的对比表。你手上已经有数据了吗？")

    monkeypatch.setattr(mentor, "chat", fake_chat)
    out = mentor.repair("建议你先把这个方向的综述读一遍，也可以顺手看看常用的数据集。",
                        user_text="我想做钓鱼检测", intent=_intent(),
                        history=[{"role": "user", "content": "我想做钓鱼检测"}], papers=[])
    assert len(calls) == 1
    assert "数量、时长或指标" in calls[0]                   # 纠正语点名了这条约束
    assert "12k" in out and "90%" in out


def test_too_long_valve_only_fires_past_the_measured_ceiling():
    """长度阀是**保险阀**不是风格旋钮：阈值必须取在实测胜局上界之外。

    轮 51 的 n=161 重放里胜局长度分两簇 [296…405] 与 [613…925]——
    把阀定在提示词自陈的 700 会切掉上那一簇的胜局，所以它必须高于 925。
    """
    assert mentor.violations("好" * 925)["too_long"] is False
    assert mentor.violations("好" * mentor.MAX_REPLY_CHARS)["too_long"] is False
    assert mentor.violations("好" * (mentor.MAX_REPLY_CHARS + 1))["too_long"] is True


_CARDS = [{"title": "Photovoltaic power forecasting using LSTM", "year": 2023,
           "abstract": "LSTM on two years of 15-minute power curves"},
          {"title": "PV-Python for solar energy", "year": 2022, "abstract": "open source models"}]

# 有 2 张卡在手上，整段却一处来源都没挂 —— g92 夹具里这类占「有卡回复」的 33%（6 条里 2 条），
# 且抽出来看的两条都是真缺陷：学生要"找个还没人做的点"，回复让 ta 自己去读 5 篇文献。
_UNCITED = ("你手上是两年 15 分钟采样的功率曲线，那先把基线钉死再谈换模型："
            "1. 用 Pandas 算逐时均值与日累计，把夜间零值和缺失先剔干净；"
            "2. 用 persistence 做对照基线，记下同一口径的 RMSE；"
            "3. 再跑 LSTM 与它对比，对照表出来了才有资格说谁好。"
            "接下来三个月每周固定复跑一次，中途不要换数据集。")


def test_no_citation_valve_fires_only_when_cards_were_handed_over():
    """闸门的靶子是"该挂没挂"：没卡时不许开火，那是取数面的问题，不是措辞问题。"""
    assert mentor.violations(_UNCITED, papers=_CARDS)["no_citation"] is True
    assert mentor.violations(_UNCITED, papers=[])["no_citation"] is False
    assert mentor.violations(_UNCITED, papers=None)["no_citation"] is False


def test_no_citation_accepts_every_trace_the_product_reads():
    """[n]、【n】、《论文》三种都算挂上来源——口径必须与 llm/evidence 同一套。

    换口径会让闸门与产品各认一遍：模型写了《论文名》却被闸门判"零引用"，重采样只是白烧一次调用。
    """
    for trace in ("这跟第 1 篇的做法一致 [1]。", "对照基线见【2】这篇。", "《PV-Python》给的就是这条路径。"):
        assert mentor.violations(_UNCITED + trace, papers=_CARDS)["no_citation"] is False, trace


def test_no_citation_does_not_collide_with_no_substance():
    """两条管的是不同病：这条只管来源，"整段没东西"仍归 no_substance。

    负测方向也要对：抽样的句子本身有料（否则 `and not thin` 会让这条永远不咬）。
    """
    v = mentor.violations(_UNCITED, papers=_CARDS)
    assert v["no_substance"] is False and v["no_citation"] is True
    empty = mentor.violations("我这就去查几篇相关的论文，然后一并整理给你看。", papers=_CARDS)
    assert empty["no_substance"] is True and empty["no_citation"] is False


def test_repair_asks_for_a_source_when_the_reply_cited_nothing(monkeypatch):
    """闸门要真的接进重采样，不能只活在字典里。"""
    calls = []

    async def fake_chat(prompt, **kw):
        calls.append(prompt)
        return _UNCITED + "其中第 2 篇的对照口径可以直接抄 [2]。"

    monkeypatch.setattr(mentor, "chat", fake_chat)
    out = mentor.repair(_UNCITED, user_text="我们实验室在做光伏功率预测",
                        intent=_intent(), history=[], papers=_CARDS)
    assert len(calls) == 1 and "一处来源都没挂" in calls[0]
    assert "[2]" in out


def test_repair_keeps_a_reply_that_already_cited(monkeypatch):
    """已经挂了角标的回复不该被这条逼着重写（负向用例：分支方向要被测到）。"""
    calls = []

    async def fake_chat(prompt, **kw):
        calls.append(prompt)
        return "重来一遍的版本 [1]。"

    monkeypatch.setattr(mentor, "chat", fake_chat)
    src = _UNCITED + "persistence 那条对照在《PV-Python》里有现成实现 [2]。"
    out = mentor.repair(src, user_text="我们实验室在做光伏功率预测",
                        intent=_intent(), history=[], papers=_CARDS)
    assert calls == [] and out == src


def test_repair_resamples_a_runaway_reply_and_asks_for_compression(monkeypatch):
    """失控长文（实测到过 1151/1171 字）要触发一次点名"压到 N 字"的重采样。"""
    calls = []

    async def fake_chat(prompt, **kw):
        calls.append(prompt)
        return ("先固定 1 个公开集，只跑现成权重的推理，量出 CPU 上一轮评估要 6 小时；"
                "再把 500 条样本按两条通道分开标，1 周内出同一口径的对照表。")

    monkeypatch.setattr(mentor, "chat", fake_chat)
    out = mentor.repair("第一段建议先做数据清洗。" * 200,        # 2000 字，远超阀值
                        user_text="我想做钓鱼检测", intent=_intent(),
                        history=[{"role": "user", "content": "我想做钓鱼检测"}], papers=[])
    assert calls and "压到" in calls[0]
    assert len(out) < mentor.MAX_REPLY_CHARS


def test_repair_gives_up_on_length_rather_than_truncating(monkeypatch):
    """两轮都用完仍然超长时，产品的现状是**照发**，没有任何确定性截断兜底。

    这条钉的是"没有截断"这个事实（此前该分支一行测试都没有，g90b 只量到"那 1 次被修回来了"，
    1/48 撑不起"永远修得回来"）。哪天加了截断，这条会红——那时要么连 docstring 与
    ITERATION_LOG 一起改，要么就别悄悄加：静默截断会把句子切在半截上，比发一条长文更糟。
    """
    long_text = "先固定一个公开数据集只跑现成权重的推理再量一次评估耗时；" * 60   # ≈1680 字
    assert len(long_text) > mentor.MAX_REPLY_CHARS
    calls = []

    async def fake_chat(prompt, **kw):
        calls.append(prompt)
        return long_text

    monkeypatch.setattr(mentor, "chat", fake_chat)
    out = mentor.repair(long_text, user_text="我想做钓鱼检测", intent=_intent(),
                        history=[{"role": "user", "content": "我想做钓鱼检测"}], papers=[])
    assert len(calls) == mentor.REPAIR_ROUNDS                    # 重采样预算花完仍未修好
    assert len(out) == len(long_text)                            # 原样交回：一个字都没被裁掉
    assert mentor.violations(out)["too_long"] is True            # 违规还挂着，闸门放行


def test_grounded_facts_block_passes_only_verified_cells():
    """对比表每格都过了逐字依据校验，块里只能出现真事实；「未知」一格都不许带进去。"""
    cols = [("method", "方法"), ("dataset", "数据")]
    rows = [{"title": "Multimodal phishing detection", "year": 2024, "basis": "摘要",
             "cells": {"method": "CLIP 图像-文本融合", "dataset": "未知"},
             "evidence": {"method": "a CLIP-style encoder is trained on 12k pages"}}]
    block = mentor.grounded_facts_block({"columns": cols, "rows": rows})
    assert "CLIP 图像-文本融合" in block and "《Multimodal phishing detection》" in block
    assert "原文片段" in block and "依据：摘要" in block
    assert "未知" not in block                          # 空格的占位语不能喂给模型
    assert mentor.grounded_facts_block(None) == ""
    assert mentor.grounded_facts_block({"columns": cols, "rows": []}) == ""
    all_unknown = [{"title": "T", "cells": {"method": "未知", "dataset": "未知"}, "evidence": {}}]
    assert mentor.grounded_facts_block({"columns": cols, "rows": all_unknown}) == ""


def test_build_prompt_appends_verified_facts_without_extra_llm_call(monkeypatch):
    """build_prompt 只读缓存：peek_cached 之外不调 matrix.build_matrix，绝不因此多花一次调用。"""
    from companion.literature import matrix
    from companion.intent.model import ResearchIntent

    calls: list[str] = []
    monkeypatch.setattr(matrix, "peek_cached",
                        lambda pid, papers: calls.append("peek") or {"columns": [("method", "方法")],
                                                                     "rows": [{"title": "T", "year": 2024,
                                                                               "basis": "全文",
                                                                               "cells": {"method": "GNN+1.8M 节点"},
                                                                               "evidence": {}}]})
    monkeypatch.setattr(matrix, "build_matrix",
                        lambda *a, **kw: (_ for _ in ()).throw(AssertionError("不许现算对比表")))
    p = mentor.build_prompt(user_text="x", intent=ResearchIntent(project_id="p", topic="钓鱼网站检测"),
                           papers=[{"title": "T", "year": 2024}])
    assert calls == ["peek"] and "GNN+1.8M 节点" in p and "只能引用这里的说法与数字" in p


def test_search_stats_block_only_uses_numbers_computed_from_metadata():
    """补上唯一没追平裸问的机制指标（数字密度），但数字只能由 Provider 元数据现算。"""
    papers = [{"title": "A", "year": 2019, "citation_count": 312, "open_access": True},
              {"title": "B", "year": 2024, "citation_count": 8},
              {"title": "C", "year": 2025, "citation_count": 60}]
    block = mentor.search_stats_block(papers)
    assert "命中 3 篇" in block and "最高被引 312（《A》）" in block
    assert "2019—2025" in block and "中位出版年 2024" in block
    assert "被引 ≥50 的 2 篇" in block and "标注为开放获取的 1 篇" in block
    assert "不得自造" in block
    assert mentor.search_stats_block([]) == ""
    assert "【本轮检索统计" in mentor.build_prompt(user_text="x", intent=_intent(), papers=papers)
    assert "【本轮检索统计" not in mentor.build_prompt(user_text="x", intent=_intent(), papers=[])
    # 不说"另外几篇要付费"：open_access 缺省只是未知，不是订阅证据
    assert "需订阅" not in block


def test_clarify_turn_does_not_search(monkeypatch):
    """轮 40 的"澄清轮也查一轮当地基"被 n=36 盲评否掉了（16% vs 不查的 33—39%）：
    第一句话的学生要的是接下来做什么，不是一份书目；引文把可执行计划的篇幅挤掉了。
    这条测试现在守的是**反面**契约，别再改回去。"""
    import companion.literature.engine as engine
    from companion.conversation import dialogue_manager as dm
    from companion.intent.model import ResearchIntent

    calls: list[int] = []

    def fake_search(intent, limit=8):
        calls.append(limit)
        return [{"title": "Multimodal phishing detection", "doi": "10.1/x"}]

    monkeypatch.setattr(engine, "search_for_intent", fake_search)
    turn = {"project_id": "p", "intent": ResearchIntent(project_id="p", topic="多模态钓鱼网站检测"),
            "still_clarifying": True, "papers": [], "gaps": ["期望投稿时间"], "changed": [],
            "state": "CLARIFY", "history": []}
    assert dm.run_search(turn)["papers"] == [] and calls == []


def test_clarification_turn_orders_substance_before_the_question():
    """有缺口时模型倾向于"只问不给"（实测那条 67 字的回声加问句就是这么来的）。"""
    prompt = mentor.build_prompt(user_text="我第一次做科研", intent=_intent(),
                                 gaps=["期望投稿时间", "用户背景细节"])
    assert "先给 2 条此刻就能做、且带数量或时限的建议" in prompt
    assert "只能问一项" in prompt and "硬性顺序" in prompt


def test_thin_quant_needs_three_advice_items_with_quantities():
    """裸问每条回复平均 8.2 处数字，2.0 只有 2.6 处——"用什么方法"说了，"多大量/多久"没说。"""
    # 一句笼统建议 + 一句没量的补充：旧的两道闸门都放过，这一道要拦
    assert mentor.thin_quant("建议你先把这个方向的综述读一遍，也可以顺手看看常用的数据集。")
    assert not mentor.thin_quant("建议你先精读 5 篇近两年的综述。可以先拿 12k 条 URL 跑一遍基线，"
                                 "准确率到 90% 再谈换模型。第三步用 3 张公开数据集各跑 1 次对照，"
                                 "1 周内出同一口径的对比表。")
    # 两条带量的建议在新契约下仍然不够（SYSTEM 第 3 条要 3—5 条彼此独立的）
    assert mentor.thin_quant("建议你先精读 5 篇近两年的综述。可以先拿 12k 条 URL 跑一遍基线，准确率到 90%。")
    assert mentor.thin_quant("建议你先精读 5 篇近两年的综述，顺便把方法谱系画成一张图。")
    # 整段没有建议句 → 不归这道管（`violations()` 里 no_substance 先判，两者不会同时开火）
    assert not mentor.thin_quant("这篇论文很有名，大家都读过，你觉得呢？")
    v = mentor.violations("我这就去查几篇相关的论文，然后一并整理给你看。", papers=[], history=[])
    assert v["no_substance"] and not v["no_quantity"]


def test_system_prompt_demands_immediately_usable_substance():
    assert "只提问不给东西＝失败" in mentor.SYSTEM or "＝失败" in mentor.SYSTEM
    assert "此刻就能做" in mentor.SYSTEM and "我这就去查" in mentor.SYSTEM


def test_system_demands_per_item_granularity():
    """长度盲评实测：条目数与裸问打平（4 对 4）仍判负——问题在每条太虚，不在条数。"""
    assert "能立刻动手" in mentor.SYSTEM
    assert "没有对象、没有量" in mentor.SYSTEM          # 点名"多读文献"这类空句不算一条


def test_reply_token_budget_covers_the_prompt_word_ceiling():
    """提示词里的字数上限必须被 max_tokens 兜住，否则第 3 条建议会被截断成"只给了两条"。"""
    ceilings = [int(x) for x in re.findall(r"(\d+)\s*字）", mentor.SYSTEM)]
    assert ceilings, "SYSTEM 第 5 条的字数预算写法变了，这条测试要跟着改"
    assert mentor.REPLY_MAX_TOKENS > max(ceilings)


# ── chat_json 的解析口径（线上实测：漏收尾围栏会让整轮结构化通道静默作废）──────
def test_parse_json_survives_missing_closing_fence_and_prose():
    from llm import parse_json
    assert parse_json('```json\n{"items": ["a", "b"]}\n```') == {"items": ["a", "b"]}
    assert parse_json('```json\n{"items": ["a"]}') == {"items": ["a"]}      # 实测崩掉的那一种
    assert parse_json('好的，结果如下：\n{"a": 1}\n希望对你有帮助') == {"a": 1}
    assert parse_json('["x", "y"]') == ["x", "y"]
    assert parse_json("") is None and parse_json("一句人话，没有 JSON") is None


def test_chat_json_still_raises_when_nothing_is_parseable(monkeypatch):
    import asyncio

    import llm as llm_module
    monkeypatch.setattr(llm_module, "LLM_MOCK", False)
    async def fake(prompt, **kw):
        return "抱歉，我做不到"
    monkeypatch.setattr(llm_module, "chat", fake)
    with pytest.raises(llm_module.LLMError):
        asyncio.run(llm_module.chat_json("x"))


# ── 候选论文块：把摘要要点喂给模型（裸问基线 structurally 拿不到的东西）──────
def test_papers_block_gives_the_model_an_abstract_gist():
    block = mentor.papers_block([
        {"title": "Multimodal phishing detection", "year": 2024,
         "tldr": "用 URL 词法与页面截图，12k 页面准确率 97.1%"},
        {"title": "No gist here", "abstract": "  domains   linked by shared hosting  "},
    ])
    assert "摘要要点：用 URL 词法" in block
    assert "摘要要点：domains linked by shared hosting" in block
    assert "摘要要点" not in mentor.papers_block([{"title": "只有一行标题"}])
    assert "摘要要点" in mentor.SYSTEM          # 提示词点名要用它


# ── 离题滤除（盲评实测：生态学期刊被推给做钓鱼检测的学生）───────────────
def test_off_topic_papers_are_filtered_out():
    from companion.literature import engine
    toks = engine.topic_tokens(["multimodal phishing website detection"], "多模态钓鱼网站检测")
    eco = {"title": "A review of ecosystem services and research perspectives",
           "abstract": "We review 120 case studies of ecosystem services in China."}
    phish = {"title": "Multimodal phishing detection using URL and HTML features",
             "abstract": "phishing website datasets"}
    assert engine.relevance(eco, toks) == 0
    assert [p["title"] for p in engine.filter_relevant([eco, phish], toks)] == [phish["title"]]


def test_all_off_topic_leaves_nothing_to_recommend():
    from companion.literature import engine
    toks = engine.topic_tokens(["phishing detection"], "")
    junk = [{"title": "Ecosystem services review", "abstract": "wetlands and rivers"}]
    assert engine.filter_relevant(junk, toks) == []


def test_generic_words_do_not_count_as_relevance():
    from companion.literature import engine
    toks = engine.topic_tokens(["phishing detection"], "")
    only_generic = {"title": "A review and analysis of detection systems", "abstract": "novel method"}
    assert engine.relevance(only_generic, toks) == 1        # 只有 detection 命中，review/analysis 不算


def test_uav_paper_leak_is_anchored_by_the_title():
    """轮 33 实测泄漏：UAV 航空摄影的论文靠摘要里复用的 detection / learning 混进钓鱼检测的推荐列表。"""
    from companion.literature import engine
    toks = engine.topic_tokens(["multimodal phishing website detection deep learning"], "多模态钓鱼网站检测")
    uav = {"title": "Review of the light-weighted and small UAV system for aerial photography and remote sensing navigation",
           "abstract": "Deep learning based object detection with multimodal data fusion in UAV remote sensing."}
    phish = {"title": "Multimodal phishing detection using URL and HTML features",
             "abstract": "phishing website datasets"}
    assert engine.relevance(uav, toks) >= 2                  # 旧口径正是这样放它进来的
    assert [p["title"] for p in engine.filter_relevant([uav, phish], toks)] == [phish["title"]]


def test_chinese_title_is_exempt_from_the_latin_anchor():
    """中文标题没法和拉丁主题词对账——判不了就不判，只保留字数下限那道。"""
    from companion.literature import engine
    toks = engine.topic_tokens(["phishing website detection"], "")
    zh = {"title": "钓鱼网站检测研究综述", "abstract": "a survey of phishing website detection"}
    assert engine.filter_relevant([zh], toks) == [zh]


def test_cjk_topic_words_are_cut_by_window_not_greedy_chunk():
    """中文主题原来的切法只产出"多模态钓鱼网"这种没人匹配的碎片，等于滤除环节没有中文词。"""
    from companion.literature import engine
    toks = engine.topic_tokens(["多模态钓鱼网站检测"], "")
    assert {"钓鱼网站", "网站检测"} <= toks
    assert "多模态钓鱼网" not in toks and "站检测" not in toks
    junk = {"title": "高中信息科技微课多模态分析框架构建研究", "abstract": "multimodal teaching analysis"}
    good = {"title": "钓鱼网站检测研究综述", "abstract": "phishing website detection survey"}
    assert engine.filter_relevant([junk, good], toks) == [good]
    assert engine.topic_tokens(["钓鱼"], "") == {"钓鱼"}          # 短串整串作词


def test_suite_runs_in_mock_regardless_of_import_order():
    """全套件跑时 tests/ 会先 import llm；LLM_MOCK 定值早于 tests2 顶部的 env → 16 个 mock 用例偷偷打真实 API。
    这条守住那个不变量，红的时候直接指向 conftest.py，而不是留一地 LLMError。"""
    import llm
    assert llm.LLM_MOCK is True


def test_topic_is_taken_from_the_students_own_words_even_without_the_word_research():
    """实测最疼的一条：学生明说"题目是医学图像分割"，我们却抽不出主题 → 永远在反问，从不检索。"""
    from companion.intent import clarification, extractor
    from companion.intent.model import ResearchIntent

    it = ResearchIntent(project_id="t")
    for msg in ("我在准备毕业论文，题目是医学图像分割，实验室只有两块 3080，怕算力不够",
                "数据是公开的 BraTS 脑肿瘤 MRI，导师要求今年底前给出实验结果；我不太会调参。"):
        it.apply_update(extractor.extract_update(msg, it))
    assert it.topic == "医学图像分割"
    assert it.user_background.dl_level and it.research_constraints.dataset
    assert clarification.enough_facts(it) and clarification.ready_for_search(it)[0]

    vague = ResearchIntent(project_id="t2")
    vague.apply_update(extractor.extract_update("我刚入门，不知道做什么方向", vague))
    assert vague.topic == "" and not clarification.ready_for_search(vague)[0]   # 没主题就别硬查


def test_topic_capture_stays_inside_one_clause():
    """线上根因：整句窗口抓取把"科研，想做"吞进主题，英译失败后中文碎片直接进 Crossref。"""
    from companion.intent.extractor import capture_topic
    assert capture_topic("我第一次做科研，想做多模态钓鱼网站检测，深度学习只学过一点") == "多模态钓鱼网站检测"
    assert capture_topic("我想研究联邦学习中的隐私保护方法，投 CCF") == "联邦学习中的隐私保护方法"
    assert capture_topic("你好") == "" and capture_topic("") == ""
    # 只剩"IEEE 的方向"这种壳词时判空——实测它会捞回 MIMO 雷达与 WLAN 文献，宁缺毋滥
    assert capture_topic("我手上有一批高光谱遥感图像，想找发 IEEE 的方向，但没有 GPU") == ""


def test_llm_topic_channel_rejects_invented_terms_and_shell_words(monkeypatch):
    import llm as llm_module
    from companion.intent import extractor

    async def fake(prompt, **kw):
        return fake.reply
    fake.reply = {"topic": "多模态钓鱼网站检测"}
    monkeypatch.setattr(llm_module, "LLM_MOCK", False)
    monkeypatch.setattr(llm_module, "chat_json", fake)
    assert extractor.llm_topic("我想做多模态钓鱼网站检测，第一次做科研") == "多模态钓鱼网站检测"
    fake.reply = {"topic": "联邦学习隐私保护"}          # 原话里一个字都没有 = 编造
    assert extractor.llm_topic("我想做多模态钓鱼网站检测") == ""
    fake.reply = {"topic": "研究方法方向"}              # 壳词 + 原话里没有的字
    assert extractor.llm_topic("我想做多模态钓鱼网站检测") == ""
    fake.reply = {"topic": "方向"}                      # 纯壳词
    assert extractor.llm_topic("我想做多模态钓鱼网站检测") == ""
    fake.reply = {"mock": True}
    monkeypatch.setattr(llm_module, "LLM_MOCK", True)
    assert extractor.llm_topic("我想做多模态钓鱼网站检测") == ""   # MOCK 下零调用


def test_search_query_uses_the_topic_not_the_preferences():
    """偏好混进检索式 = 主题词里出现 recent/open/access/deep/learning，相关性判定当场失效。"""
    from companion.intent.model import ResearchIntent
    from companion.literature import engine
    it = ResearchIntent(project_id="t", topic="多模态钓鱼网站检测")
    it.current_focus = ["要近两年的英文文献", "开放获取优先", "深度学习只学过一点"]
    assert engine._query_from_intent(it) == "多模态钓鱼网站检测"
    no_topic = ResearchIntent(project_id="t")
    no_topic.current_focus = ["卫星遥感影像"]
    assert engine._query_from_intent(no_topic) == "卫星遥感影像"


def test_cross_domain_paper_cannot_anchor_on_deep_learning_alone():
    """逐篇体检实测：农业机器学习综述靠 deep + learning 就能过字数下限并拿到标题锚。"""
    from companion.literature import engine
    toks = engine.topic_tokens(["multimodal phishing website detection deep learning"], "")
    assert {"deep", "learning", "machine", "data"} & toks == set()
    agri = {"title": "Machine Learning in Agriculture: A Comprehensive Updated Review",
            "abstract": "deep learning applications in precision agriculture"}
    assert engine.filter_relevant([agri], toks) == []


def test_pure_cjk_query_does_not_filter_english_titles():
    """英译没生效（MOCK/超时）时中文主题配英文标题必然零命中——那不是离题证据。"""
    from companion.literature import engine
    toks = engine.topic_tokens(["钓鱼网站检测"], "钓鱼网站检测")
    assert toks and not any("a" <= c <= "z" for t in toks for c in t)
    papers = [{"title": "Survey of Phishing Detection Approaches"}, {"title": "Visual Phishing Detection with CLIP"}]
    assert engine.filter_relevant(papers, toks) == papers


# ── Provider 摘要：OpenAlex 倒排索引与 Crossref JATS（对比表填充率的真根因）──
def test_openalex_inverted_index_is_reconstructed():
    from companion.literature.providers.openalex import from_inverted

    idx = {"Deep": [0], "learning": [1], "for": [2, 5], "phishing": [3], "detection": [4], "URLs": [6]}
    assert from_inverted(idx) == "Deep learning for phishing detection for URLs"
    assert from_inverted(None) == "" and from_inverted({}) == ""
    assert from_inverted({"bad": "不是列表", "ok": [0, "x"]}) == "ok"


def test_crossref_abstract_strips_jats_markup():
    from sources import clean_abstract

    raw = "<jats:p>We propose <jats-bold>Shark-Eyes</jats-bold>, a multimodal framework.</jats:p>"
    assert clean_abstract(raw) == "We propose Shark-Eyes , a multimodal framework."
    assert clean_abstract("") == ""


def test_crossref_select_asks_for_abstract():
    from sources import SELECT

    assert "abstract" in SELECT


def _fake_crossref(monkeypatch, by_filter):
    """把 Crossref 的 HTTP 换成按 filter 分派的假响应，记录实际发出的每一档 filter。"""
    import httpx

    import sources
    calls: list[str] = []

    class Resp:
        def __init__(self, items):
            self._items = items

        def raise_for_status(self):
            pass

        def json(self):
            return {"message": {"items": self._items}}

    class Client:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def get(self, url, params=None):
            f = (params or {}).get("filter", "")
            calls.append(f)
            return Resp(by_filter.get(f, []))

    monkeypatch.setattr(httpx, "AsyncClient", Client)
    return calls


def test_crossref_first_query_demands_abstracts(monkeypatch):
    """🔴 不带 has-abstract 时实测 24 条检索 0 条有摘要（g66c）→ 综述只能凭标题编角标。"""
    import asyncio

    import sources
    calls = _fake_crossref(monkeypatch, {
        sources.ABSTRACT_FILTERS[0]: [{"title": ["A paper"], "DOI": "10.1/x",
                                       "abstract": "<jats:p>We test on 40 datasets.</jats:p>"}]})
    got = asyncio.run(sources.crossref_search("x", 5))
    assert calls == [sources.ABSTRACT_FILTERS[0]], "第一档就该带上摘要过滤，且命中即停"
    assert got[0]["abstract"] == "We test on 40 datasets."


def test_crossref_relaxes_then_gives_up_to_unfiltered(monkeypatch):
    """冷门主题四档过滤全空时必须退回「不带过滤」再试一次，而不是直接交白卷。"""
    import asyncio

    import sources
    calls = _fake_crossref(monkeypatch, {"": [{"title": ["Odd paper"], "DOI": "10.1/y"}]})
    got = asyncio.run(sources.crossref_search("冷门题目", 5))
    assert calls == list(sources.ABSTRACT_FILTERS)
    assert got and got[0]["title"] == "Odd paper"


# ── engine：英译回退、反馈排序、TLDR ─────────────────────────────────
async def test_query_fallback_is_marked_when_translation_unavailable(monkeypatch):
    async def no_translation(q):
        return q                                   # 模拟 MOCK/失败：中文原词直送英文库

    async def empty(self, query, limit=10):
        return []

    monkeypatch.setattr(engine, "_english_query", no_translation)
    from companion.literature.providers import crossref_arxiv, openalex, semantic_scholar
    for cls in (crossref_arxiv.CrossrefProvider, crossref_arxiv.ArxivProvider,
                openalex.OpenAlexProvider, semantic_scholar.SemanticScholarProvider):
        monkeypatch.setattr(cls, "safe_search", empty)
    it = ResearchIntent(project_id="t-cjk", topic="钓鱼网站检测")
    await engine.search_for_intent_async(it, limit=3)
    assert it.extra.get("query_fallback") is True   # 上层据此提示用户"相关性可能偏低"

    it2 = ResearchIntent(project_id="t-en", topic="phishing website detection")
    await engine.search_for_intent_async(it2, limit=3)
    assert not it2.extra.get("query_fallback")      # 英文主题本就不需要翻译


def _off_script_queries(seen: list[str], main: str) -> list[str]:
    """送进英文库的式子清单里，哪些**不属于**「英译主式」或「主式 + 词表体裁词」。

    轮 34 立这条的原始事故是「中文原词与英译式一起送库」；轮 154 的 P3 补检索又加了一种
    合法的扩展式（主式 + 一个受控体裁词）。这里把"合法"写成谓词而不是写成 `set(seen) == {main}`，
    是为了让**原事故仍然会被抓住**（见 test_planted_chinese_original_is_still_caught）——
    直接把旧断言删掉就等于把守卫一起删了。
    """
    bad = []
    for q in seen:
        if q == main:
            continue
        tail = q[len(main) + 1:] if q.startswith(main + " ") else None
        if tail is None or tail not in engine.FACET_VOCAB or q.count(" ") != main.count(" ") + 1:
            bad.append(q)
    return bad


async def test_only_translated_query_is_sent_when_translation_works(monkeypatch):
    """线上实测缺陷：中文原词与英译式一起送库会混进「变电站接地网」这类无关文献。"""
    seen = []

    async def translated(q):
        return "phishing site detection"

    async def record(self, query, limit=10):
        seen.append(query)
        return []

    monkeypatch.setattr(engine, "_english_query", translated)
    from companion.literature.providers import crossref_arxiv, openalex, semantic_scholar
    for cls in (crossref_arxiv.CrossrefProvider, crossref_arxiv.ArxivProvider,
                openalex.OpenAlexProvider, semantic_scholar.SemanticScholarProvider):
        monkeypatch.setattr(cls, "safe_search", record)
    it = ResearchIntent(project_id="t-mix", topic="钓鱼网站检测")
    await engine.search_for_intent_async(it, limit=3)
    main = "phishing site detection"
    assert seen and main in seen, f"连英译主式都没送：{seen}"
    assert not _off_script_queries(seen, main), f"送库的式子超出「主式 / 主式+体裁词」两种合法形状"
    assert not it.extra.get("query_fallback")


async def test_planted_chinese_original_is_still_caught():
    """负对照：把轮 34 那次事故原样喂进同一个谓词，必须判它违规（否则上一条测试是装饰）。"""
    accident = ["phishing site detection", "钓鱼网站检测"]
    bad = _off_script_queries(accident, "phishing site detection")
    assert bad == ["钓鱼网站检测"], f"中文原词裸送没被抓到：{bad}"
    # 词表外的"偏好词"也不许混进来（"近两年"英译成 recent 就是已实测的污染源）
    assert _off_script_queries(["phishing site detection recent"], "phishing site detection")


async def test_english_query_uses_text_and_rejects_chinese_echo(monkeypatch):
    """P1-5 根因回归：原实现按 JSON 解析关键词文本，导致英译永远失败、中文原词裸送英文库。"""
    import llm

    replies = {}

    async def fake_chat(prompt, **kw):
        return replies["text"]

    monkeypatch.setattr(llm, "chat", fake_chat)
    monkeypatch.setattr(llm, "LLM_MOCK", False)
    replies["text"] = "phishing site detection, machine learning"
    assert await engine._english_query("钓鱼网站检测") == "phishing site detection machine learning"
    replies["text"] = "1. phishing\n2. detection"          # 带编号也清洗成干净查询串
    assert await engine._english_query("钓鱼网站检测") == "phishing detection"
    replies["text"] = "钓鱼网站 检测"                        # 模型回中文 → 不采用
    assert await engine._english_query("钓鱼网站检测") == "钓鱼网站检测"
    assert await engine._english_query("phishing detection") == "phishing detection"


def test_paper_memory_feedback_changes_ranking():
    """N-1：用户反馈必须改变排序（此前 engine 完全不读 Paper Memory）。"""
    def mk(title, doi):
        return {"title": title, "doi": doi, "year": 2024, "citation_count": 5,
                "open_access": True, "scores": {}, "recommendation_reason": []}
    it = ResearchIntent(project_id="t-fb", topic="phishing")
    plain = [mk("Phishing A", "10.1/a"), mk("Phishing B", "10.1/b")]
    engine._attach_reasons(plain, it)
    scored = {p["title"]: p["scores"]["final"] for p in plain}

    with_fb = [mk("Phishing A", "10.1/a"), mk("Phishing B", "10.1/b")]
    engine._attach_reasons(with_fb, it, {"rejected": {"10.1/b"}, "favorite": set()})
    fb = {p["title"]: p["scores"]["final"] for p in with_fb}
    reasons = {p["title"]: p["recommendation_reason"] for p in with_fb}
    assert fb["Phishing B"] < scored["Phishing B"]
    assert any("降权" in r for r in reasons["Phishing B"])
    assert not any("降权" in r for r in reasons["Phishing A"])


async def test_tldr_falls_back_to_abstract_head_under_mock():
    abstract = "X" * 300
    papers = [{"title": "T", "abstract": abstract, "scores": {}, "recommendation_reason": []}]
    await engine._attach_tldr(papers)
    assert papers[0]["tldr"] == abstract[:120] + "…"


# ── knowledge：导出 vault ────────────────────────────────────────────
def test_export_zip_contains_grown_notes(tmp_path, monkeypatch):
    monkeypatch.setattr(knowledge, "KB_ROOT", tmp_path / "kb")
    knowledge.auto_grow_from_paper("t-kb", {"title": "PhishGNN", "year": 2022, "abstract": "g"})
    blob = knowledge.export_zip("t-kb")
    assert blob[:2] == b"PK"
    assert b"yanban-kb/" in blob


def test_list_notes_and_note_view_hide_front_matter(tmp_path, monkeypatch):
    """知识库列表/正文曾把 YAML front-matter 直接当摘要露给用户。"""
    monkeypatch.setattr(knowledge, "KB_ROOT", tmp_path / "kb")
    knowledge.upsert_note("p-fm", "论文：PhishGNN", "## 摘要\n图神经网络方法", tags=["paper"])
    item = knowledge.list_notes("p-fm")[0]
    assert not item["head"].lstrip().startswith("---") and "title:" not in item["head"]
    assert item["tags"] == ["paper"] and "图神经网络方法" in item["head"]
    body, tags = knowledge.strip_front(knowledge.get_note("p-fm", "论文：PhishGNN"))
    assert body.startswith("## 摘要") and tags == ["paper"]


def test_backlinks_index_over_grown_notes(tmp_path, monkeypatch):
    """C2：自动生长的论文笔记要能被别的笔记 [[双链]] 反向索引到。"""
    monkeypatch.setattr(knowledge, "KB_ROOT", tmp_path / "kb")
    knowledge.auto_grow_from_paper("p-bl", {"title": "PhishGNN", "year": 2022, "abstract": "g"})
    knowledge.upsert_note("p-bl", "路线A", "与 [[论文：PhishGNN]] 同路线", tags=["plan"])
    assert knowledge.backlinks("p-bl", "论文：PhishGNN") == ["路线A"]
    item = [n for n in knowledge.list_notes("p-bl") if n["note"] == "路线A"][0]
    assert item["links"] == ["论文：PhishGNN"]


def test_system_prompt_has_no_borrowable_example_sentences():
    """SYSTEM 里举的示例句会被模型当成学生真说过的话复述（实测泄漏过「深度学习学过一点」）。"""
    for leak in ("深度学习学过一点", "第一次做科研", "三个月出 SCI", "老板让我三个月"):
        assert leak not in mentor.SYSTEM, f"SYSTEM 含可被复述成假记忆的示例句: {leak}"
    assert "不要写字母 n" in mentor.SYSTEM          # 角标措辞不能被照抄成 [n]


def test_dangling_citation_markers_are_stripped():
    """没给候选论文时模型仍会写 [1]，前端会变成指向不存在卡片的死锚点。"""
    assert mentor.strip_dangling_citations("先看这篇[1]，它很基础。", []) == "先看这篇，它很基础。"
    assert mentor.strip_dangling_citations("1. A[1] 2. B[2] 3. C[7]",
                                           [{"title": "A"}, {"title": "B"}]) == "1. A[1] 2. B[2] 3. C"
    import re
    text, degraded = mentor.ask(user_text="随便聊聊", intent=_intent())   # 无候选 → 不应残留数字角标
    assert not degraded and not re.search(r"\[\d{1,2}\]", text)   # MOCK 标记 [MOCK-LLM] 不算角标


def test_fullwidth_bracket_markers_are_stripped_too():
    """轮 203 在线上截图里看到的：勾掉检索那一轮，正文写着「常用数据集【1】【2】【3】」。

    同一件事的另一半形状。`_CITE_TRACE`（mentor.py:849）认 [n]、【n】、《》 三种写法，
    而确定性兜底 `strip_dangling_citations`（:843）只删 ASCII 那一种 ⇒ 模型换成全角括号，
    一句"没有文献依据"的话照样带着三个看起来能点的编号出厂。
    """
    import re
    live = "我建议你先阅读至少5篇关于图神经网络在异常检测中应用的论文，更好地理解常用数据集【1】【2】【3】。"
    assert mentor._CITE_TRACE.search(live), "闸门认不出全角角标 ⇒ 这条测试的前提变了"
    assert mentor.strip_dangling_citations(live, []) == live.replace("【1】【2】【3】", "")
    two = [{"title": "A"}, {"title": "B"}]
    assert mentor.strip_dangling_citations("1. A【1】 2. B【2】 3. C【7】", two) == "1. A【1】 2. B【2】 3. C"
    out = mentor.strip_dangling_citations("混着写 [1] 和【2】都越界", [])
    assert not re.search(r"\[\d{1,2}\]|【\d{1,2}】", out), f"两种形状要一起剪，剩下：{out}"


def test_dangling_anchor_removal_leaves_no_gate_ledger_r288():
    """轮 287/288 实测的四条读数钉成回归：角标是清洗链摘的，而闸门**只看得到摘完之后**的那句。

    - `带角标的原文 ∧ papers=[]` ⇒ 不开任何键（＝未决 56 那族的结构性失明，现状不是认可）；
    - `摘完角标的成品 ∧ papers=[]` ⇒ 开 `no_substance`（轮 288 首跑量到的新格：盲区只在"带着角标"那一侧，
      摘掉之后这句反而被"只承诺不给东西"那族看见了 ⇒ 「删除不记账」不许引申成「出厂没人管」）；
    - 删除本身必须真的发生（`strip_dangling_citations` 改成 no-op 时这里要红）；
    - 两条**分辨力牙**（`no_substance`／`no_citation`）：同一个 `violations` 必须对别的形状开火，
      否则"判空"什么也不说——轮 287 的 `thin_citation` 按错签名调、两形状都返回空串，就是这么把
      尺子故障读成产品行为的。
    """
    text = ("先别急着查文献——「图异常检测」这个题目太大了，我暂时没法确定该给你哪一类东西[1]。"
            "你先告诉我两件事：打算投哪个会议或期刊，以及手上有没有已标注的数据集。")
    cleaned = mentor.clean(text)
    assert "[1]" in cleaned                         # 前提：角标确实进到了清洗链（不是"压根没写进去"）
    # 先判"带角标的原文"：这句必须在 repair 之前测——摘掉角标之后闸门反而看见了（下面第 4 行）
    assert not any(mentor.violations(text, papers=[]).values()), \
        "反问轮带角标这一族本应判不出（现状＝闸门结构性看不见）"
    out = mentor.repair(cleaned, plan=None, papers=[], gaps=[],
                        user_text="在吗，我有件事想请教", intent=None)
    assert "[1]" not in out and len(out) < len(cleaned), f"角标没被摘：{out}"
    assert mentor.violations(out, papers=[])["no_substance"] is True, \
        "摘完角标的成品应被 no_substance 看见（轮 288 实测）"
    assert mentor.violations("我这就去查几篇相关的论文，给你一些建议。", papers=[])["no_substance"] is True
    assert mentor.violations("1. An effective detection approach for phishing",
                             papers=[{"title": "A"}])["no_citation"] is True


async def test_dead_provider_is_tripped_and_skipped(monkeypatch):
    """必然失败的源不该每轮检索都拖一次超时：首次判定不可用后进入熔断。"""
    from companion.literature.providers import base

    base.reset_breakers()
    calls = {"n": 0}

    class Dead(base.PaperProvider):
        name = "dead_source"

        async def search(self, query, limit=10):
            calls["n"] += 1
            raise base.ProviderUnavailable(self.name, "HTTP 406")

    assert await Dead().safe_search("x") == []
    assert calls["n"] == 1 and base.breaker_open("dead_source")
    assert await Dead().safe_search("x") == []
    assert calls["n"] == 1                       # 第二次直接跳过，不再发起请求
    monkeypatch.setattr(base, "COOLDOWN", 0.0)
    base.trip("dead_source", 0.0)
    assert not base.breaker_open("dead_source")  # 冷却到期自动恢复
    base.reset_breakers()


def test_chips_exist_for_gaps_and_are_parsed_by_extractor():
    """点选式反问：每个选项点出去的那句话，必须真能被规则提取通道接住（否则是死按钮）。"""
    from companion.conversation.interview_agent import CHIP_OPTIONS, next_chips
    from companion.intent import extractor as ex

    it = ResearchIntent(project_id="t-chips")            # 全空 → 缺口最多
    chips = next_chips(it)
    assert chips and chips[0]["options"], "信息全空时应给出可点选项"
    assert all(o["say"].strip() for g in chips for o in g["options"])

    for field, (_q, opts) in CHIP_OPTIONS.items():
        fresh = ResearchIntent(project_id="t-chips")
        for label, say in opts:
            upd = ex.extract_update(say, fresh)
            assert field in upd and upd[field] not in (None, "", []), \
                f"选项「{label}」的话术没被提取器接住：{say} → {upd}"


def test_suggestions_appear_after_a_searched_turn():
    from companion.conversation.interview_agent import suggestions_for
    ready = _intent(topic="phishing detection")
    ready.apply_update({"research_stage": "experiment", "user_goal": "做检测",
                        "publication_goal.type": "SCI", "paper_preferences.year_from": 2024,
                        "user_background.dl_level": "basic", "user_background.llm_level": "none",
                        "paper_preferences.open_access_preferred": True,
                        "research_constraints.compute": "有 GPU"})
    assert suggestions_for(ready, searched=True)
    assert suggestions_for(ready, searched=False) == []


def test_used_memory_and_feedback_effects_are_visible_in_payload():
    """记忆与反馈的影响必须能被界面呈现，不能只在后端悄悄发生。"""
    from companion.conversation import dialogue_manager as dm
    turn = {"memory_items": [{"role": "user", "content": "我只有两块 3080", "score": 1.2},
                             {"kind": "intent_updated", "summary": "更新字段：research_stage"}],
            "papers": [{"title": "A" * 50, "recommendation_reason": ["你之前标过不相关，已降权"]},
                       {"title": "B", "recommendation_reason": ["与你收藏过的论文同一条路线"]},
                       {"title": "C", "recommendation_reason": ["开放获取，可直接阅读全文"]}],
            "intent": _intent(), "project_id": "p", "state": "DELIVER",
            "changed": [], "still_clarifying": False}
    mem = dm._used_memory(turn)
    assert len(mem) == 2 and mem[0]["text"] == "我只有两块 3080" and mem[1]["source"] == "intent_updated"
    eff = dm._feedback_effects(turn)
    assert len(eff) == 2 and all("《" in s for s in eff)
    assert not dm._feedback_effects({"papers": [{"title": "D", "recommendation_reason": ["标题匹配"]}]})


def test_explicit_request_for_results_overrides_clarification():
    """学生明说要论文时不能继续只问不查（线上实测曾连 3 轮卡在 conf 0.231）。"""
    from companion.conversation.interview_agent import wants_results
    for say in ("那先看看有什么能上手的论文", "给我几篇文献", "直接开始吧", "别问了，先查查看"):
        assert wants_results(say), say
    assert not wants_results("我最近在考虑要不要换个方向")
    it = ResearchIntent(project_id="t-want")
    it.apply_update(extractor.extract_update("我想研究钓鱼网站检测，我只有两块 3080，导师要求明年三月前交稿", it))
    assert it.research_constraints.compute == "有 GPU"
    assert it.research_constraints.experiment_time == "一年内"
    assert it.user_goal == "导师要求的科研任务"


def test_cite_export_uses_only_provider_metadata():
    """导出只能搬运 Provider 给过的字段：缺 DOI 就不能出现 DO 行（不补全、不过 LLM）。"""
    from companion.literature import cite
    full = {"title": "PhishGNN: Graph Neural Networks", "authors": ["张三", "Li, W."], "year": 2022,
            "venue": "IEEE Access", "doi": "10.1/abc", "url": "https://doi.org/10.1/abc",
            "abstract": "g", "type": "journal-article"}
    partial = {"title": "No DOI Paper", "year": 2020, "authors": [], "venue": "", "type": "proceedings-article"}
    ris = cite.to_ris([full, partial])
    assert "TY  - JOUR" in ris and "AU  - 张三" in ris and "DO  - 10.1/abc" in ris
    assert "TY  - CONF" in ris                                       # 会议类映射
    assert ris.count("DO  - ") == 1                                  # 没有 DOI 就不写 DO 行
    assert "ER  -" in ris
    bib = cite.to_bibtex([full, partial])
    keys = re.findall(r"@article\{([^,]+),", bib)
    assert keys and all(k.isascii() and k for k in keys)              # 引用键必须 ASCII
    assert "anon2022phishgnn" in keys                                 # 中文姓氏 → anon 而不是塞进中文键
    assert "title = {PhishGNN: Graph Neural Networks}" in bib
    assert "doi = {10.1/abc}" in bib and bib.count("doi =") == 1
    assert cite.to_ris([]) == "" and cite.to_bibtex([]) == ""


def test_ready_for_search_allows_rich_info_without_stage():
    """把目标/年限/水平/算力/OA 都说清的学生，不该因未提阶段而一直只问不查。"""
    from companion.intent import clarification as cl
    sparse = ResearchIntent(project_id="t-s", topic="钓鱼网站检测")
    sparse.apply_update({"research_stage": "exploration"})
    assert cl.ready_for_search(sparse)[0] is False          # 只有主题+阶段，信息仍少 → 继续问

    rich = ResearchIntent(project_id="t-r", topic="钓鱼网站检测")
    rich.apply_update({"user_goal": "发 SCI", "publication_goal.type": "SCI",
                       "paper_preferences.year_from": 2024, "user_background.dl_level": "intermediate",
                       "user_background.llm_level": "basic",
                       "paper_preferences.open_access_preferred": True,
                       "research_constraints.compute": "有 GPU"})
    ok, conf = cl.ready_for_search(rich)
    assert rich.research_stage == "unknown" and ok is True and conf >= 0.57

    no_topic = ResearchIntent(project_id="t-n")
    assert cl.ready_for_search(no_topic)[0] is False        # 没主题绝不检索


def test_section_scoped_qa_only_feeds_that_section(monkeypatch):
    """指定章节后只喂那一节（对标 Canvas「高亮即上下文」），不能把全文塞进去。"""
    import llm
    from companion.paper import qa as qa_mod
    captured = {}

    async def fake_chat(prompt, **kw):
        captured["prompt"] = prompt
        return "只用了这一节的内容。"

    monkeypatch.setattr(llm, "LLM_MOCK", False)
    monkeypatch.setattr(qa_mod, "parse_pdf", lambda path: {"sections": {
        "Introduction": "INTRO-TEXT-ONLY-HERE " + "a" * 50,
        "Experiments": "EXP-TEXT-SHOULD-NOT-APPEAR " + "b" * 50}})
    monkeypatch.setattr("llm.chat", fake_chat)
    out = qa_mod.answer_with_paper("这个方法怎么做的", "x.pdf", None, section="Introduction")
    assert "INTRO-TEXT-ONLY-HERE" in captured["prompt"]
    assert "EXP-TEXT-SHOULD-NOT-APPEAR" not in captured["prompt"]
    assert out["sections_used"] == ["Introduction"] and out["section"] == "Introduction"
    assert "论文「Introduction」一节" in captured["prompt"]


def test_followups_use_llm_when_available_and_fall_back_otherwise(monkeypatch):
    """追问建议要绑定本轮内容；MOCK 或失败时回落规则建议，不能卡住回复。"""
    import llm
    from companion.conversation import interview_agent as ia
    assert mentor.followups(user_text="x", intent=_intent(), reply="一段回复") == []   # LLM_MOCK=1
    items = {"items": ["只看近两年的呢", "哪篇最适合先读", "有代码的有哪些"]}
    async def fake_json(prompt, **kw):
        assert "学生刚说" in prompt and "导师刚回" in prompt
        return items
    monkeypatch.setattr(llm, "LLM_MOCK", False)
    monkeypatch.setattr(llm, "chat_json", fake_json)
    got = mentor.followups(user_text="我想研究钓鱼检测", intent=_intent(), reply="先看这三篇", papers=[])
    assert got == ["只看近两年的呢", "哪篇最适合先读", "有代码的有哪些"]
    async def boom(prompt, **kw):
        raise llm.LLMError("down")
    monkeypatch.setattr(llm, "chat_json", boom)
    assert mentor.followups(user_text="x", intent=_intent(), reply="y") == []
    assert ia.suggestions_for(_intent(), searched=True)                  # 规则兜底仍在


# ── 轮 52：先规划再写（合成阶段结构化）─────────────────────────────────
_PLAN = {"situation": "只想把相关性分析跑通，不要理论",
         "actions": [{"do": "先定相关指标", "with": "Spearman", "dose": "12 个变量",
                      "check": "每个变量对都有 p 值"},
                     {"do": "补齐缺失值", "with": "pandas", "dose": "缺失超过 15% 删列",
                      "check": "表格无空单元格"},
                     {"do": "画热图", "with": "seaborn", "dose": "1 张图",
                      "check": "色阶固定在 -1 到 1"}],
         "evidence": [2], "question": ""}


def test_make_plan_returns_none_under_mock_so_path_is_unchanged():
    assert mentor.make_plan(user_text="随便", intent=_intent()) is None
    assert mentor.plan_block(None) == ""
    assert mentor.plan_dropped("任意回复", None) is False


def test_plan_block_carries_every_action_with_its_acceptance_criterion():
    block = mentor.plan_block(_PLAN)
    for a in _PLAN["actions"]:
        assert a["do"] in block and a["with"] in block and a["dose"] in block and a["check"] in block
    assert "本轮不问问题" in block and "可引用编号：2" in block
    asked = dict(_PLAN, question="你的样本是从哪来的？")
    assert "你的样本是从哪来的" in mentor.plan_block(asked)


def test_plan_dropped_only_when_not_a_single_action_survives():
    """执行率不足不判违规——模型把「12 个变量」写成「十二个」也会算成没执行，那是误伤。"""
    followed = "先定指标：用 Spearman 算 12 个变量之间的相关，每个变量对都报 p 值。"
    assert mentor.plan_dropped(followed, _PLAN) is False
    ignored = "建议你先把这个方向读几篇综述，打好基础很重要。"
    assert mentor.plan_dropped(ignored, _PLAN) is True
    assert mentor.plan_dropped(ignored, dict(_PLAN, actions=_PLAN["actions"][:1])) is False


def test_violations_wires_plan_gate_and_repair_keeps_the_plan(monkeypatch):
    v = mentor.violations("建议你先把这个方向读几篇综述，打好基础很重要。", papers=[],
                          history=[{"role": "user"}], plan=_PLAN)
    assert v["plan_dropped"] and "plan_dropped" in mentor._REPAIR_SUFFIX
    assert mentor.violations("建议你先把这个方向读几篇综述。", papers=[],
                             history=[{"role": "user"}])["plan_dropped"] is False
    calls = []

    async def fake_chat(prompt, **kw):
        calls.append(prompt)
        return ("建议第一步先定指标：用 Spearman 算这 12 个变量之间的相关，每个变量对都报 p 值，"
                "这样表格里每一格都能核对。第二步再用 pandas 处理缺失，缺失超过 15% 的列直接删掉，"
                "保证进热图的矩阵没有空单元格。最后用 seaborn 画 1 张相关热图，色阶固定在 -1 到 1。")
    monkeypatch.setattr(mentor, "chat", fake_chat)
    out = mentor.repair("建议你先把这个方向读几篇综述，打好基础很重要。", user_text="跑通相关性",
                        intent=_intent(), history=[{"role": "user"}], papers=[], plan=_PLAN)
    assert calls and any("完全没按给定的动作清单" in c for c in calls)   # 计划被丢时点名这一条
    assert all("动作清单" in c for c in calls)                    # 每次重采样都仍带着计划
    assert "Spearman" in out


def test_normalize_plan_rejects_thin_plans_and_bad_evidence_indices():
    assert mentor.normalize_plan({"actions": [{"do": "只做一件事"}]}, 3) is None
    ok = mentor.normalize_plan({"actions": [{"do": "a", "with": "XGBoost"}, {"do": "b"}],
                                "evidence": [1, 9, "2", None]}, 3)
    assert ok and [a["do"] for a in ok["actions"]] == ["a", "b"] and ok["evidence"] == [1]


def test_plan_carries_the_axes_real_losings_were_missing():
    """逐条读败局读出三条缺失的轴：时间线、可交付物、要避开的坑。"""
    data = {"actions": [{"do": "先跑 URL 基线", "with": "XGBoost", "dose": "12k 条", "check": "出 PR-AUC"},
                        {"do": "边做边写方法节", "with": "Overleaf", "dose": "每周 1 段", "check": "初稿 800 词"}],
            "milestones": ["第 1—2 月：数据与基线", "第 3—6 月：消融与写作", "第 7—12 月：投稿与返修"],
            "artifact": "一张同口径对比表 + 方法节初稿", "pitfall": "别用准确率当主指标，正负样本 1:9"}
    plan = mentor.normalize_plan(data, 0)
    block = mentor.plan_block(plan)
    assert "第 3—6 月：消融与写作" in block and "方法节初稿" in block and "1:9" in block
    assert "milestones" in mentor.plan_prompt(user_text="想做钓鱼检测", intent=_intent())
    assert "pitfall" in mentor.plan_prompt(user_text="想做钓鱼检测", intent=_intent())
    lean = mentor.normalize_plan(dict(data, milestones=None, artifact="", pitfall=None), 0)
    assert lean and "时间线" not in mentor.plan_block(lean)     # 缺字段不得渲染出空标题


def test_plan_prompt_only_offers_retrieved_titles():
    papers = [{"title": "Real Phishing Detection", "year": 2023}, {"title": "另一篇真论文"}]
    p = mentor.plan_prompt(user_text="想做钓鱼检测", intent=_intent(), papers=papers)
    assert "Real Phishing Detection" in p and "只能用这里的编号与标题" in p
    assert "actions" in p and "check" in p


def test_stream_path_also_gets_the_plan(monkeypatch):
    """线上走的是流式；测量路径与产品路径必须是同一个 build，否则读数不代表产品。

    轮 91 起计划只等到 actions 闭合，所以这里改用流式喂计划 —— 断言一条不减：
    动作清单必须进到正文提示词里；再加一条新的：闭合之后的里程碑那段不该被等。
    """
    import asyncio

    import llm
    seen = {}
    PLAN = ('{"situation": "想做钓鱼检测", "actions": [{"do": "先跑 URL 基线", "with": "XGBoost", '
            '"dose": "12k 条", "check": "出 AUC"}, {"do": "补一张对比表", "with": "scikit-learn", '
            '"dose": "3 个数据集", "check": "同口径"}], "milestones": ["这段不该被等"]}')

    async def fake_stream(prompt, **kw):
        if str(prompt).startswith("学生这轮说"):
            seen["plan_prompt"] = prompt
            yield PLAN[:40]
            yield PLAN[40:]
        else:
            seen["reply_prompt"] = prompt
            yield "好"

    monkeypatch.setattr(llm, "LLM_MOCK", False)
    monkeypatch.setattr(llm, "chat_stream", fake_stream)
    monkeypatch.setattr(mentor, "chat_stream", fake_stream)
    out = "".join(asyncio.run(_collect(mentor.ask_stream(user_text="想做钓鱼检测",
                                                        intent=_intent()))))
    assert out == "好"
    assert "动作清单" in seen["reply_prompt"] and "先跑 URL 基线" in seen["reply_prompt"]
    assert "这段不该被等" not in seen["reply_prompt"]


async def _collect(agen):
    chunks = []
    async for c in agen:
        chunks.append(c)
    return chunks


def test_query_variants_stay_in_the_main_query_script():
    """分支查询只能跟主查询同文字：中文原词送英文库实测会捞回变电站/加油站那类偏题文献。"""
    def I(**kw):
        return ResearchIntent(project_id="p", **kw)
    en = I(topic="phishing website detection", accepted_directions=["URL text modality features"])
    qs = engine.variant_queries(en, "phishing website detection")
    assert len(qs) == 2 and "URL text modality" in qs[1]
    zh = I(topic="phishing website detection", accepted_directions=["只看 URL 文本模态"])
    assert engine.variant_queries(zh, "phishing website detection") == ["phishing website detection"]
    assert engine.variant_queries(I(topic="钓鱼网站检测"), "钓鱼网站 检测") == ["钓鱼网站 检测"]
    assert engine._backfill_query(I(topic="graph neural network"), ["x"]) == "graph neural network"
    assert engine._backfill_query(I(topic="图神经网络"), ["x"]) == ""
    assert engine._backfill_query(I(topic="graph neural network"), ["graph neural network"]) == ""


def test_search_sends_every_variant_it_kept(monkeypatch):
    """变体数要真的变成送库的查询数（只算分支不算实现等于没做）。

    必须走 monkeypatch 而不是裸 setattr：上一版直接替换 Provider 类且不还原，
    同一会话里后面的 e2e 就拿到了哑元空结果——单跑过、整跑红，是套件说谎的一种。
    """
    import asyncio

    from companion.literature.providers import crossref_arxiv as cx
    from companion.literature.providers import openalex as oa
    from companion.literature.providers import semantic_scholar as ss
    sent: list[str] = []

    class FakeProvider:
        name = "fake"

        async def safe_search(self, query, per):
            sent.append(query)
            return []

    for mod in (cx, oa, ss):
        for cls in ("CrossrefProvider", "ArxivProvider", "OpenAlexProvider", "SemanticScholarProvider"):
            if hasattr(mod, cls):
                monkeypatch.setattr(mod, cls, FakeProvider)
    intent = ResearchIntent(project_id="p", topic="phishing detection",
                            accepted_directions=["URL text features"])
    asyncio.run(engine.search_for_intent_async(intent, limit=5))
    assert "phishing detection" in sent
    assert any("URL text features" in q for q in sent)
    assert intent.extra["query_variants"] == 2


def test_cited_sentence_numbers_must_exist_in_the_cited_record():
    """真标题配假数字是引用幻觉最常见的形态，早被 strip_unverified 之外的空档漏掉。"""
    papers = [{"title": "Multimodal phishing detection", "year": 2023, "venue": "PLOS ONE",
               "abstract": "we crawl 12k urls and reach 0.93 auc on 3 datasets"}]
    keep_grounded = "这篇用 12k 条 URL 在 3 个数据集上做到 0.93 的 AUC [1]。"
    fab = "这篇在 30 个站点上做到 99% 准确率 [1]。"
    year_only = "这是 2023 年发在 PLOS ONE 上的工作 [1]。"
    no_anchor = "整个流程大概要 3 周。"
    oob = "这篇提到 77 个特征 [7]。"
    assert mentor.strip_ungrounded_numbers(keep_grounded, papers) == keep_grounded
    assert mentor.strip_ungrounded_numbers(year_only, papers) == year_only
    assert mentor.strip_ungrounded_numbers(no_anchor, papers) == no_anchor
    assert mentor.strip_ungrounded_numbers(oob, papers) == oob        # 角标越界自有另一道闸门管
    assert mentor.strip_ungrounded_numbers("开头一句。" + fab, papers) == "开头一句。"
    assert mentor.ungrounded_number_sentences(keep_grounded + fab, papers) == [fab]


def test_repair_tail_strips_fabricated_statistic_from_cited_sentence(monkeypatch):
    """确定性兜底要接在重采样之后：模型不肯改时，至少把查无此数的句子删掉。"""
    async def fake_chat(prompt, **kw):
        return "这篇在 30 个站点上做到 99% 准确率 [1]。建议先复现它的基线，用 3 个公开数据集对照。"
    monkeypatch.setattr(mentor, "chat", fake_chat)
    out = mentor.repair("这篇在 30 个站点上做到 99% 准确率 [1]。建议先复现它的基线，用 3 个公开数据集对照。",
                        user_text="想做钓鱼检测", intent=_intent(),
                        history=[{"role": "user", "content": "想做钓鱼检测"}],
                        papers=[{"title": "Multimodal phishing detection", "year": 2023,
                                 "abstract": "we crawl 12k urls and reach 0.93 auc on 3 datasets"}])
    assert "99%" not in out and "30 个站点" not in out
    assert "3 个公开数据集" in out                                  # 不带角标的一般性建议不该被牵连


def test_narrowing_student_never_gets_the_question_back():
    """学生已经自己收窄范围时，计划里那句提问被代码收回——问"你在哪个阶段"等于没听见。"""
    narrow = "我只想把实验数据的相关性分析跑通，不确定该用什么统计方法，别给我讲太多理论"
    open_ended = "我第一次做科研，想做多模态钓鱼网站检测，老师要求三个月内出一篇 SCI"
    assert mentor.narrows_scope(narrow) and not mentor.narrows_scope(open_ended)
    asked = dict(_PLAN, question="你现在处于哪个研究阶段？")
    assert mentor.apply_ask_policy(asked, narrow)["question"] == ""
    assert mentor.apply_ask_policy(asked, open_ended)["question"] == asked["question"]
    assert "本轮不问问题" in mentor.plan_block(mentor.apply_ask_policy(asked, narrow))


def test_plan_carries_decision_shaped_options():
    """学生问"该用哪个"时要的是决策式答案：候选 + 各自什么时候用。"""
    data = {"actions": [{"do": "定主指标", "with": "Spearman", "dose": "12 个变量", "check": "出 r 与 p"},
                        {"do": "稳健性复核", "with": "bootstrap", "dose": "200 次", "check": "区间不含 0"}],
            "options": [{"pick": "Pearson", "when": "两个连续变量且近似正态"},
                        {"pick": "Spearman", "when": "非线性或明显偏态"},
                        {"pick": "Kendall tau-b", "when": "样本小、有大量并列等级"}]}
    plan = mentor.normalize_plan(data, 0)
    assert [o["pick"] for o in plan["options"]] == ["Pearson", "Spearman", "Kendall tau-b"]
    block = mentor.plan_block(plan)
    assert "近似正态" in block and "偏态" in block and "并列等级" in block
    assert "options" in mentor.plan_prompt(user_text="该用哪个统计方法", intent=_intent())
    assert mentor.normalize_plan(dict(data, options=[{"nope": 1}, "x"]), 0)["options"] == []



def test_citation_gate_does_not_eat_list_numbers_or_provider_metadata():
    """两类实测样例暴露的误伤：句首列表编号、以及 Provider 自己给的"被引 10 次"。"""
    papers = [{"title": "A new formal model of collaboration", "year": 2005, "citation_count": 10}]
    listing = "1. 阅读两篇相关论文[1]。"
    meta = "1. 《A new formal model of collaboration》（2005年，被引 10 次）[1]。"
    assert mentor.ungrounded_number_sentences(listing, papers) == []
    assert mentor.ungrounded_number_sentences(meta, papers) == []
    assert mentor.ungrounded_number_sentences("这篇在 30 个站点做到 99%[1]。", papers)  # 真编造照删


def test_reply_rubric_separates_the_dimensions_the_prompt_asks_for():
    """评分卡是"提示词有没有被遵守"的实测读数，六个维度都得能各自独立翻转。"""
    papers = [{"title": "Multimodal phishing detection", "abstract": "we crawl 12k urls, auc 0.93",
               "year": 2023, "citation_count": 41}]
    rich = ("你说你只有一台轻薄本，那先把模态收到 URL 文本。第一步用 XGBoost 跑 12k 条数据，"
            "两周内做到召回 90% 以上再谈多模态；如果是偏态分布就用 Spearman 而不是 Pearson。"
            "《Multimodal phishing detection》做到 auc 0.93 [1]。第 3—6 个月做消融。")
    r = mentor.reply_rubric(rich, user_text="我只有一台轻薄本", papers=papers)
    assert all(r[k] for k in mentor.RUBRIC_KEYS), r
    poor = mentor.reply_rubric("建议你多读几篇文献，打好基础，有数据了吗？", user_text="想做钓鱼检测", papers=[])
    assert not poor["acceptance"] and not poor["timeline"] and not poor["evidence"]
    assert poor["one_question"]


def test_constraint_dimension_needs_an_echoed_quantity_not_any_shared_word():
    """中文里随便两个句子都会撞词——共同词判据实测 100% 命中，等于没有指标。"""
    generic = mentor.reply_rubric("你做研究要多读论文，方法也很重要。", user_text="我的研究方向是论文", papers=[])
    echoed = mentor.reply_rubric("你只有两块 3080，那先把 batch 压到 8 跑一天看看。",
                                 user_text="实验室只有两块 3080，怕算力不够", papers=[])
    assert generic["constraint"] is False
    assert echoed["constraint"] is True
    assert "3080" in mentor._student_quantables("实验室只有两块 3080")
    assert "两块" in mentor._student_quantables("实验室只有两块 3080")


def test_no_echo_gate_names_the_missing_quantables_and_does_not_stack():
    """闸门只在别的实质问题都干净时触发，且纠正语要点名漏掉的数（实测有胜率梯度的维度）。"""
    said = "实验室只有两块 3080，还有一年半毕业，Python 只会一点"
    deaf = "建议你先从公开数据集开始，把基线跑起来，再考虑多模态融合。"
    echoing = ("只有两块 3080 就别碰大模型全量训练；Python 只会一点也没关系，"
               "一年半的时间线够你把基线在 BraTS 上跑出来。")
    missed = mentor.missed_quantables(deaf, said)
    assert "3080" in missed and "一年" in missed and "两块" in missed
    assert mentor.missed_quantables(echoing, said) == []
    v = mentor.violations(echoing, papers=[], history=[{"role": "user"}], user_text=said)
    assert not v["no_echo"]
    w = mentor.violations(deaf, papers=[], history=[{"role": "user", "content": said}], user_text="")
    assert w["no_quantity"] and w["no_echo"]        # 历史里说过的量同样算；追加名单不与"缺数量"打架
    assert "3080" in "、".join(mentor.missed_quantables(deaf, said))
    only_echo = mentor.violations(
        "建议你先用公开数据集跑 5000 条样本，两周内出基线；建议你再用 3 张图看分布；"
        "建议你控制在 20 页以内写完方法节。",
        papers=[], history=[{"role": "user", "content": said}], user_text="")
    assert only_echo["no_echo"] and not only_echo["no_quantity"]   # 独自开火的那种形态
    calls = []

    async def fake_chat(prompt, **kw):
        calls.append(prompt)
        return echoing
    import companion.conversation.mentor as m
    orig = m.chat
    m.chat = fake_chat
    try:
        m.repair(deaf, user_text=said, intent=_intent(),
                 history=[{"role": "user", "content": said}], papers=[])
    finally:
        m.chat = orig
    assert calls and "3080" in calls[0]            # 纠正语里点名了那个数


def test_every_violation_key_has_a_repair_instruction():
    """每条违规都得在纠正语表里有一项——这次加 no_echo 时漏了它，5 条测试当场 KeyError。"""
    probe = mentor.violations("一段回复", papers=[], history=[{"role": "user"}])
    assert set(probe) == set(mentor._REPAIR_SUFFIX), (set(probe) ^ set(mentor._REPAIR_SUFFIX))
    for key, text in mentor._REPAIR_SUFFIX.items():
        assert text.strip(), key


def test_memory_injection_caps_and_dedupes_user_notes(tmp_path, monkeypatch):
    """显式笔记不封顶会把真正相关的片段挤出窗口（调研 E：干扰条目的代价远大于多塞几条）。"""
    import re as _re

    from companion import db as db_module
    from companion.memory import retriever
    # 🔴 轮 192 之前这里直接 db.get_conn()——那是进程级单例，指向本地**被 git 跟踪**的
    #    data/yanban.db：整套件每跑一次就落 7 行（实测库里已积累 756 行，全在 t-memory-cap 名下，
    #    而且那个项目根本不在 projects 表里）。"记忆清单有多少条"这类读数会被它污染。
    monkeypatch.setattr(db_module, "DB_PATH", tmp_path / "cap.db")
    db_module._CONN = None
    pid = "t-memory-cap"
    try:
        conn = db_module.get_conn()      # events 直接按 project_id 取，不需要会话行
        for i in range(6):   # 同一句话重复记 6 次 + 若干普通事件
            db_module.write_event(conn, pid, "user_note", "我必须坚持用 BraTS 数据集", None)
        db_module.write_event(conn, pid, "decision", "先做 URL 模态基线", None)
        conn.commit()
    finally:
        conn.close()          # 不关的话 Windows 下 tmp 目录删不掉
        db_module._CONN = None
    got = retriever.retrieve(pid, "该用什么数据集", k=3)
    notes = [m for m in got if m.get("kind") == "user_note"]
    assert len(notes) <= retriever.MAX_NOTES, notes
    assert len({ _re.sub(r"\s+", "", str(m["summary"]))[:40] for m in notes}) == len(notes)
    assert len(got) <= 3


def test_cite_evidence_finds_only_real_spans():
    """引用抽屉的证据只能来自我们真持有的文字；找不到就返回空，不许补一段"像依据"的话。"""
    from companion.literature import evidence

    paper = {"title": "Multimodal phishing detection", "year": 2023,
             "abstract": "We crawl 12k URLs and train a LightGBM baseline. "
                         "The model reaches 0.93 AUC on three public datasets.",
             "tldr": "URL 文本模态在钓鱼检测里已经够用"}
    hit = evidence.best_spans("这篇用 12k 条 URL 拿到 0.93 的 AUC [1]。", paper)
    assert hit and "0.93" in hit[0]["text"] and hit[0]["source"] == "摘要"
    assert evidence.best_spans("作者毕业于斯坦福大学 [1]。", paper) == []
    assert evidence.best_spans("", paper) == []
    many = evidence.best_spans("LightGBM AUC datasets baseline", paper, limit=1)
    assert len(many) == 1
    assert [s for s, _ in evidence.sources(paper)] == ["标题", "TLDR", "摘要"]
    assert evidence.pdf_sections_for("no-such-project", "") == []


def test_cite_context_endpoint_reports_found_and_says_no_when_ungrounded():
    import uuid

    from companion.api import router as r
    from companion.memory import paper_memory

    pid = "t-cite-" + uuid.uuid4().hex[:8]
    paper = {"title": "Contrastive multimodal network for phishing", "year": 2024, "doi": "10.1/x",
             "abstract": "We propose a contrastive multimodal network that fuses URL text and "
                         "visual features on 12k phishing pages, achieving 0.94 F1."}
    paper_memory.save_many(pid, [paper])
    ok = r.cite_context({"project_id": pid, "paper_index": 1,
                         "claim": "它在 12k 条钓鱼页面上做到 0.94 的 F1 [1]。"})
    assert ok["found"] and ok["spans"][0]["source"] == "摘要" and ok["note"] == ""
    no = r.cite_context({"project_id": pid, "title": "contrastive multimodal",
                         "claim": "这套方法已经在 200 家银行上线 [1]。"})
    assert no["found"] is False and no["spans"] == [] and "没有文献支撑" in no["note"]


def test_claim_survey_counts_only_real_overlap_and_labels_negation():
    """支持/相反计数必须来自真词面重合；没有否定标记就不许报"相反证据"。"""
    from companion.literature import evidence

    a = {"title": "A", "abstract": "We reach 0.94 F1 on 12k phishing pages using URL text."}
    b = {"title": "B", "abstract": "However, URL-only features fail to generalise: the same 0.94 F1 "
                                  "drops sharply on unseen brands."}
    c = {"title": "C", "abstract": "This survey reviews 40 methods for speech enhancement."}
    out = evidence.survey("URL 文本模态在 12k 条数据上做到 0.94 F1", [a, b, c])
    assert out["checked"] == 3 and out["support"] == 2 and out["against"] == 1
    assert {h["stance"] for h in out["hits"]} == {"support", "against"}
    empty = evidence.survey("", [a])
    assert empty == {"checked": 0, "support": 0, "against": 0, "hits": []}
    none = evidence.survey("完全不搭的一句关于量子误差矫正", [c])
    assert none["support"] == 0 and none["against"] == 0


def test_grounding_report_separates_cited_from_grounded():
    """"看起来有引用"与"引用得住"必须分开计量——引用痕迹本身就能讨好模型评委。"""
    from companion.literature import evidence

    papers = [{"title": "Contrastive multimodal network",
               "abstract": "We fuse URL text and visual features on 12k phishing pages and reach 0.94 F1."},
              {"title": "Survey of graph anomaly detection",
               "abstract": "This survey reviews graph methods for anomaly detection on 20 datasets."}]
    text = ("《Contrastive multimodal network》在 12k 条数据上做到 0.94 F1 [1]。"
            "这篇论文的作者曾在 200 家银行部署该系统 [1]。"
            "这篇综述用 graph 方法覆盖 20 个数据集 [2]。"
            "总之这个方向很有前景。")
    rep = evidence.grounding_report(text, papers)
    assert rep["cited_sentences"] == 3 and rep["grounded"] == 2
    assert abs(rep["coverage"] - 2 / 3) < 0.02
    assert rep["ungrounded_samples"] and "200 家银行" in rep["ungrounded_samples"][0]
    empty = evidence.grounding_report("没有引用的一段话。", papers)
    assert empty["cited_sentences"] == 0 and empty["coverage"] is None


def test_grounding_rejects_boilerplate_only_overlap():
    """每篇摘要都写的套话不能作为依据——纯词面重合的已知假阳性（ALCE/CiteCheck 一路的教训）。

    旧口径下"这篇提出了一个深度学习框架，实验结果表明方法有效"能对上任意一篇摘要，
    于是每句带角标的废话都算"查过了"，接地率会虚高到看不出真问题。
    """
    from companion.literature import evidence

    boiler = "我们提出了一个深度学习框架，实验结果表明方法有效"
    pool = [{"title": "A", "abstract": boiler + "，并融合文本与视觉特征做钓鱼检测"},
            {"title": "B", "abstract": boiler + "，用图神经网络做异常检测"},
            {"title": "C", "abstract": boiler + "，在时序负荷预测上验证"},
            {"title": "D", "abstract": boiler + "，用于医学图像分割"}]
    rare = evidence.rare_tokens(pool)
    assert rare is not None
    assert "深度学" not in rare and "实验结" not in rare   # 四篇都有 = 套话
    assert "视觉特" in rare                               # 只有一篇有 = 有区分度
    sent = "我们提出了一个深度学习框架，实验结果表明方法有效 [1]。"
    assert evidence.best_spans(sent, pool[0])             # 不看池子时的旧行为仍在（单篇核对用）
    assert evidence.best_spans(sent, pool[0], rare=rare) == []
    text = sent + "这篇把文本与视觉特征融合起来做检测 [1]。"
    rep = evidence.grounding_report(text, pool)
    assert rep["cited_sentences"] == 2 and rep["grounded"] == 1
    assert evidence.rare_tokens(pool[:2]) is None         # 池子太小，IDF 本身就是噪声

    # 三篇里两篇共有的词是"共同主题"，不是套话——滤错了会把真依据一起删掉
    mixed = [{"title": "X", "abstract": "attention mechanism for vision transformer models"},
             {"title": "Y", "abstract": "attention mechanism beats cnn on small data"},
             {"title": "Z", "abstract": "pure convolutional network baseline results"}]
    assert "attention" in evidence.rare_tokens(mixed)


def test_renumber_citations_only_moves_markers_that_the_paper_cannot_support():
    """"论文是真的、这句话不是它说的"是更常见的引用错（ALCE 的 citation precision）。

    只允许在"被引那篇对本句零支撑"时改指；整池都撑不住时必须原样留着，
    否则等于用改编号把没依据的句子伪装成有依据。
    """
    from companion.literature import evidence

    a = {"title": "Contrastive multimodal network",
         "abstract": "We fuse URL text and visual features on 12k phishing pages, reaching 0.94 F1."}
    b = {"title": "Graph anomaly survey",
         "abstract": "This survey reviews graph anomaly detection over 40 datasets."}
    pool = [a, b]

    wrong, n = evidence.renumber_citations("这篇把 URL 文本与 visual features 融合，在 12k 条上做到 0.94 F1 [2]。", pool)
    assert n == 1 and "[1]" in wrong and "[2]" not in wrong
    assert evidence.grounding_report(wrong, pool)["grounded"] == 1

    same, n2 = evidence.renumber_citations("该综述覆盖 40 个 datasets 并使用 graph 方法 [2]。", pool)
    assert n2 == 0 and same.endswith("[2]。")

    orphan, n3 = evidence.renumber_citations("这套方法已在 200 家银行上线 [2]。", pool)
    assert n3 == 0 and "[2]" in orphan
    assert evidence.grounding_report(orphan, pool)["grounded"] == 0

    one, n4 = evidence.renumber_citations("这篇在 12k 条上做到 0.94 F1 [2]。", [a])
    assert n4 == 0 and one.endswith("[2]。")     # 只有一篇时没有"改指"这回事


def test_repair_repoints_wrong_paper_marker_without_resampling(monkeypatch):
    """确认改指真的接在 repair 的确定性兜底里，而不是只活在单元测试里。"""
    from companion.conversation import mentor

    a = {"title": "Contrastive multimodal network",
         "abstract": "We fuse URL text and visual features on 12k phishing pages, reaching 0.94 F1."}
    b = {"title": "Graph anomaly survey",
         "abstract": "This survey reviews graph anomaly detection over 40 datasets."}

    def boom(*a2, **k2):
        raise AssertionError("这条用例不该触发重采样")

    monkeypatch.setattr(mentor, "chat", boom)
    out = mentor.repair("这篇把 URL 文本与 visual features 融合，在 12k 条上做到 0.94 F1 [2]。",
                        user_text="我手上有 12k 条钓鱼页面数据", intent=None,
                        history=[{"role": "user", "content": "我手上有 12k 条钓鱼页面数据"}],
                        papers=[a, b])
    assert "[1]" in out and "[2]" not in out


def test_grounding_report_splits_wrong_paper_from_real_gap():
    """没接住的引用必须分两类报：挂错篇（可机械改指）与真缺证据（要补检索）。

    合成用例对着的是线上实测：3 句"查无依据"里有 2 句在补上标题来源后变成"依据在池里别处"，
    只看一个接地率会把人推去做二次检索，而那其实是个标注错位问题。
    """
    from companion.literature import evidence

    a = {"title": "Contrastive multimodal network",
         "abstract": "We fuse URL text and visual features on 12k phishing pages, reaching 0.94 F1."}
    b = {"title": "Graph anomaly survey",
         "abstract": "This survey reviews graph anomaly detection over 40 datasets."}
    text = ("这篇把 URL 文本与 visual features 融合，在 12k 条上做到 0.94 F1 [2]。"
            "这套方法已在 200 家银行上线 [2]。")
    rep = evidence.grounding_report(text, [a, b])
    assert rep["cited_sentences"] == 2 and rep["grounded"] == 0
    assert rep["misnumbered"] == 1 and rep["true_gap"] == 1
    assert rep["coverage"] == 0.0


def test_plan_prefix_cut_only_waits_for_actions():
    """首字不该押在计划的后半段：actions 一闭合就够写正文。

    实测规划调用 19.8s、正文首字 0.7s，串行等完整份 JSON 是首字延迟的大头。
    """
    import json

    from companion.conversation import mentor

    full = ('{"situation": "两块 3080 怕算力", "actions": ['
            '{"do": "跑通 U-Net 基线", "with": "BraTS + nnU-Net", "dose": "2 周", "check": "Dice 有数"},'
            '{"do": "退到 2D", "with": "同一份数据", "dose": "1 周", "check": "显存 <20G"}], '
            '"milestones": ["第 1 段：跑通基线"], "axes": [{"axis": "按任务", "buckets": []}], '
            '"question": ""}')
    cut = mentor.plan_prefix_cut(full)
    assert cut and json.loads(cut)["actions"][1]["do"] == "退到 2D"
    plan = mentor.normalize_plan(json.loads(cut), 3)      # 缺字段不等于废计划
    assert plan and len(plan["actions"]) == 2 and "milestones" not in (full[:len(cut) - 1])
    idx = full.index('"milestones"')
    before = full[:idx]
    assert mentor.plan_prefix_cut(before[:before.rindex("]")]) is None   # actions 未闭合就不许切
    tricky = ('{"situation": "s", "actions": [{"do": "取 [0:10] 切片做增强", "with": "w", '
              '"dose": "1 周", "check": "c"}], "question": ""}')
    assert json.loads(mentor.plan_prefix_cut(tricky))["actions"][0]["do"] == "取 [0:10] 切片做增强"
    assert mentor.plan_prefix_cut('{"situation": "刚开头') is None
    assert mentor.plan_prefix_cut("") is None


def test_ask_stream_starts_reply_before_the_plan_is_finished(monkeypatch):
    """流式路径只等 actions 闭合：后半份计划（里程碑/候选/组织线）不该压着学生的首字。"""
    import asyncio

    import llm
    from companion.conversation import mentor

    PLAN_CHUNKS = [
        '{"situation": "两块 3080 怕算力", "actions": ['
        '{"do": "跑通 nnU-Net 基线", "with": "BraTS", "dose": "2 周", "check": "Dice 有数"},'
        '{"do": "退到 2D 分割", "with": "同一份数据", "dose": "1 周", "check": "显存降一半"}], ',
        '"milestones": ["等不到的"], "options": [], "axes": [], "question": ""}',
        "这一段绝不该被等到",
    ]
    calls = []

    async def fake_stream(prompt, **kw):
        is_plan = str(prompt).startswith("学生这轮说")
        rec = ["plan" if is_plan else "reply", 0]
        calls.append(rec)
        for chunk in (PLAN_CHUNKS if is_plan else ["正文第一段。", "第二段。"]):
            rec[1] += 1
            yield chunk

    seen = {}
    real_build = mentor.build_prompt

    def spy_build(**kw):
        seen["plan"] = kw.get("plan")
        return real_build(**kw)

    monkeypatch.setattr(mentor, "chat_stream", fake_stream)
    monkeypatch.setattr(llm, "chat_stream", fake_stream)   # plan_prefix 是从 llm 里现取的
    monkeypatch.setattr(mentor, "build_prompt", spy_build)
    monkeypatch.setattr(llm, "LLM_MOCK", False)

    async def drain():
        return "".join([d async for d in mentor.ask_stream(
            user_text="实验室只有两块 3080，怕算力不够", intent=None, papers=[])])

    out = asyncio.run(drain())
    plan = seen["plan"]
    assert plan and len(plan["actions"]) == 2
    assert not plan.get("milestones")            # 里程碑根本没被等
    assert [c[0] for c in calls] == ["plan", "reply"]
    assert calls[0][1] < len(PLAN_CHUNKS), "计划没吃完就该开始写正文"
    assert "正文第一段。" in out


def test_search_failure_is_labeled_apart_from_no_relevant_papers(monkeypatch):
    """限流造成的 0 篇必须留下记号 —— 「查不了」被读成「查无相关」，产品和测量都会跟着错。

    这一条是本轮 A/B 的直接教训：6 个情景里 3 个因为 provider 429 静默变成空池子，
    第一次读数是 0 篇论文的链路上跑出来的，看上去却像质量结论。
    """
    from companion.conversation import dialogue_manager as dm
    from companion.literature import engine

    def boom(intent, limit=8):
        raise RuntimeError("https://api.openalex.org/works 不可用：HTTP 429")

    monkeypatch.setattr(engine, "search_for_intent", boom)
    turn = dm.prepare_context("题目已定：多模态钓鱼网站检测，有 12k 条标注数据和两块 3080，"
                              "导师要求今年发一篇 SCI", None)
    turn["still_clarifying"] = False
    turn = dm.run_search(turn)
    assert turn["papers"] == []
    assert "429" in str(turn["intent"].extra.get("search_error") or "")
    assert "429" in str(dm.meta_event(turn)["intent"]["extra"].get("search_error") or "")


def test_provider_cooldown_tiers_and_result_cache():
    """永久拒绝 vs 临时限流分档冷却，并且同一查询只外呼一次 —— P3 的可出示证据。

    线上实测：arXiv 对本出口 IP 恒 406（五组 headers 全拒、空响应体），却和 429 共用 120s 平铺冷却，
    于是每轮检索都白等一次必然失败的往返；而多查询分支会把同一个源反复撞爆限流。
    """
    import asyncio
    import time

    from companion.literature.providers import base as pbase

    pbase.reset_breakers()

    class Ok(pbase.PaperProvider):
        name = "t-ok"

        def __init__(self):
            self.calls = 0

        async def search(self, query, limit=10):
            self.calls += 1
            return [{"title": f"Paper about {query}"}]

    class Rejected(pbase.PaperProvider):
        name = "t-rejected"

        async def search(self, query, limit=10):
            raise pbase.ProviderUnavailable(self.name, "HTTP 406", permanent=True)

    ok = Ok()
    first = asyncio.run(ok.safe_search("multimodal phishing", 5))
    second = asyncio.run(ok.safe_search("  Multimodal   PHISHING ", 5))    # 归一化后同一个查询
    assert first and second == first and ok.calls == 1, "重复查询不该再外呼"
    assert pbase.provider_stats()["cache_hits"] >= 1
    assert asyncio.run(ok.safe_search("graph anomaly detection", 5))
    assert ok.calls == 2, "不同查询不许误命中缓存"

    bad = Rejected()
    assert asyncio.run(bad.safe_search("x", 5)) == []
    assert pbase.breaker_open(bad.name)
    left = pbase._DOWN_UNTIL[bad.name] - time.monotonic()
    assert left > 3000, f"永久拒绝该进小时级冷却，实得 {left:.0f}s"

    class Throttled(pbase.PaperProvider):
        name = "t-429"

        async def search(self, query, limit=10):
            raise pbase.ProviderUnavailable(self.name, "HTTP 429")

    th = Throttled()
    asyncio.run(th.safe_search("y", 5))
    first_cool = pbase._DOWN_UNTIL[th.name] - time.monotonic()
    pbase._DOWN_UNTIL.clear()
    asyncio.run(th.safe_search("y", 5))
    second_cool = pbase._DOWN_UNTIL[th.name] - time.monotonic()
    assert 0 < first_cool <= 300 and second_cool > first_cool, "限流该按连击翻倍，且远短于永久拒绝档"
    pbase.reset_breakers()


def test_current_turn_words_are_not_reported_as_older_memory():
    """本轮刚说的那句不许被当成「你以前说过的话」——它是同一条消息，不是记忆。

    截图取证时在一个全新项目上看到「用了 4 条你以前说过的话」，查下来是
    `retrieve` 在 messages 表里搜，而本轮这句在检索前就入库了。
    """
    import uuid

    from companion.conversation import dialogue_manager as dm
    from companion.intent.model import ResearchIntent

    pid = "t-mem-" + uuid.uuid4().hex[:8]
    older = "上次说过：多模态钓鱼网站检测要同时用 URL 文本模态和截图模态"
    now = "我第一次做科研，想做多模态钓鱼网站检测，深度学习只学过一点"
    dm.add_message(pid, "user", older)
    dm.add_message(pid, "user", now)
    turn = {"project_id": pid, "intent": ResearchIntent(project_id=pid, topic="多模态钓鱼网站检测"),
            "history": [], "papers": [], "gaps": []}
    dm._mentor_kwargs(turn, now)
    texts = [str(m.get("summary") or m.get("content") or "") for m in turn["memory_items"]]
    assert texts, "旧记忆应当还能被想起来"
    assert not any(t.strip() == now.strip() for t in texts), "本轮原话不该出现在记忆里"
    assert any("URL 文本模态" in t for t in texts)


def test_provider_outage_is_recorded_and_labeled():
    """outage 必须分得清「源挂了」和「查无相关」——前端那句提示全靠它。

    不这样测就得去桩引擎里的局部 import，那种测试只会跟实现细节较劲（本轮真浪费了两轮）。
    """
    from companion.literature import engine
    from companion.literature.providers import base as pbase

    class Dead(pbase.PaperProvider):
        name = "t-dead"

        async def search(self, query, limit=10):
            raise pbase.ProviderUnavailable(self.name, "HTTP 429")

    pbase.reset_breakers()
    assert asyncio.run(Dead().safe_search("multimodal phishing", 5)) == []
    assert pbase.take_outage() == ["t-dead"], "safe_search 必须把失败源记进 outage"
    assert pbase.take_outage() == [], "取走即清空，别把上一轮的失败算到这一轮"

    intent = ResearchIntent(project_id="t-outage", topic="钓鱼网站检测")
    assert engine.note_outage(intent, kept=[]) == ""      # 没失败源就不许乱标
    pbase._OUTAGE.extend(["openalex", "arxiv", "openalex"])
    assert engine.note_outage(intent, kept=[]) == "arxiv,openalex"
    assert "arxiv,openalex" in intent.extra["search_error"]
    pbase._OUTAGE.extend(["semantic_scholar"])
    assert engine.note_outage(intent, kept=[{"title": "x"}]) == "semantic_scholar"
    # 有结果时只记 outage，不许把「不可用」往上盖
    assert intent.extra["provider_outage"] == "semantic_scholar"
    assert "arxiv,openalex" in intent.extra["search_error"]
    assert engine.note_outage(intent, kept=[{"title": "x"}]) == ""
    assert "search_error" not in intent.extra, "新一轮查好了就必须把上轮的不可用标记清掉"
    assert "provider_outage" not in intent.extra
    pbase.reset_breakers()



def test_subscription_check_only_reports_papers_not_seen_before():
    """主题跟踪的全部价值在"只报新的"：去重按 DOI，没 DOI 的按标题归一化。"""
    import uuid

    from companion.memory import subscriptions as subs

    pid = "t-sub-" + uuid.uuid4().hex[:8]
    first = [{"title": "Multimodal phishing detection", "doi": "10.1/a", "year": 2024},
             {"title": "Graph anomaly detection survey", "doi": "", "year": 2023}]
    second = [{"title": "Multimodal phishing detection", "doi": "10.1/a", "year": 2024},
              {"title": "  GRAPH-ANOMALY  detection  survey ", "doi": "", "year": 2023},
              {"title": "Contrastive phishing network", "doi": "10.1/b", "year": 2025}]
    made = subs.add(pid, "phishing detection")
    assert made["seen"] == 0
    a = subs.check(pid, made["id"], searcher=lambda q: first)
    assert a["new"] and a["seen_total"] == 2
    b = subs.check(pid, made["id"], searcher=lambda q: second)
    assert [n["doi"] for n in b["new"]] == ["10.1/b"]        # 两条旧的（含标题写法不同的）都不该再报
    assert b["seen_total"] == 3 and len(subs.list_for(pid)) == 1
    assert subs._key({"title": "无 DOI 的 论文！", "doi": ""}) == subs._key({"title": "无DOI的论文", "doi": ""})


def test_share_snapshot_is_readonly_scoped_and_revocable():
    """分享链接三条底线：撤销后立刻读不到、不能拿别人的 token 撤销、只存论文与对话不存隐私字段。"""
    import uuid

    from companion.memory import shares

    pid = "t-share-" + uuid.uuid4().hex[:8]
    made = shares.create(pid, papers=[{"title": "A paper", "doi": "10.1/a", "year": 2024,
                                       "abstract": "不该被带进快照的长文本" * 40}],
                         messages=[{"role": "user", "content": "我想做钓鱼检测"},
                                   {"role": "assistant", "text": "先看这三篇"}],
                         intent={"topic": "phishing", "research_stage": "proposal", "user_goal": "发 SCI"},
                         title="我的开题")
    snap = shares.get(made["token"])
    assert snap and snap["intent"]["topic"] == "phishing" and len(snap["messages"]) == 2
    assert "abstract" not in snap["papers"][0], "快照只带题录，不该把整段摘要复制出去"
    assert shares.get(made["token"][:8]) is None and shares.get("") is None
    assert shares.revoke(made["token"], "别的项目") is False
    assert shares.revoke(made["token"], pid) is True
    assert shares.get(made["token"]) is None


def test_paper_block_shows_the_abstract_sentences_that_carry_numbers():
    """截断到 120 字常常正好把 12k / 0.94 F1 截在后面——模型看不见就只能编，编了就被核验闸门删句。"""
    from companion.literature import evidence

    p = {"title": "Contrastive multimodal phishing detection", "year": 2024,
         "abstract": "We introduce a contrastive multimodal network. "
                     "On 12k phishing URLs the model reaches 0.94 F1, beating text-only baselines. "
                     "A second study over 3 datasets confirms the trend at 0.88 accuracy."}
    q = mentor.quotable_numbers(p)
    assert "12k" in q and "0.94" in q
    assert "0.；" not in q                                      # 小数点曾被当句界劈开，切出过「0.；94 F1」
    three = {"abstract": p["abstract"] + " We also measured 27 ablations over 6 weeks."}
    assert len(mentor.quotable_numbers(three).split("；")) == 2  # 上限 2 条，控制提示词体积
    assert "27 ablations" not in mentor.quotable_numbers(three)
    assert "12k" in mentor.papers_block([p]) and "0.94" in mentor.papers_block([p])
    assert mentor.quotable_numbers({"title": "无摘要的一篇", "abstract": ""}) == ""
    # 递出去的数字必须能在同一篇的记录里核到，否则等于喂给模型一条会被删的引用
    assert evidence.best_spans("这篇在 12k 条 URL 上做到 0.94 F1 [1]。", p)


def test_candidate_axes_are_checked_for_parent_sibling_confusion():
    """组织线的互斥性由结构检查保证，不靠提示词祈愿——线上真翻车在「MARL 与 RL 并列」。"""
    parent_sibling = {"actions": [{"do": "先定线", "with": "Zotero", "dose": "20 篇",
                                  "check": "每篇都能归类"}],
                    "axes": [{"axis": "按算法", "relation": "并列", "when_bad": "算法交叉时",
                               "buckets": [{"name": "强化学习", "test": "出现 RL 或强化学习"},
                                           {"name": "多智能体强化学习", "test": "出现 MARL"}]}]}
    fine = {"actions": [{"do": "先定线", "with": "Zotero", "dose": "20 篇", "check": "每篇都能归类"}],
            "axes": [{"axis": "按任务", "relation": "并列", "when_bad": "任务重叠时",
                      "buckets": [{"name": "协作导航", "test": "有导航指标"},
                                  {"name": "资源调度", "test": "有调度指标"},
                                  {"name": "通信协议", "test": "有通信建模"}]},
                     {"axis": "按数据类型", "relation": "并列", "when_bad": "混合仿真",
                      "buckets": [{"name": "仿真", "test": "纯仿真实验"},
                                  {"name": "真实机器人", "test": "有实体机数据"}]}]}
    problems = mentor.axes_problems(parent_sibling)
    assert any("父子当兄弟" in x for x in problems), problems
    assert mentor.axes_problems(fine) == []
    assert "候选组织线：按任务" in mentor.plan_block(fine)
    assert "结构问题" in mentor.plan_block(parent_sibling)
    ok = mentor.normalize_plan({"actions": [{"do": "a"}, {"do": "b"}], "axes": fine["axes"]}, 0)
    assert ok["axes"][0]["buckets"][0]["name"] == "协作导航"
    reply = ("建议你按任务分三类：协作导航、资源调度、通信协议，各读 5 篇核对指标；"
             "再按算法分两类各挑 3 篇；两周内做一轮对照。")
    assert not mentor.violations(reply, papers=[], history=[{"role": "user"}], plan=fine)["bad_axes"]


def test_grounding_by_section_points_at_the_weak_section():
    """只报总接地率会藏问题：套话往往集中在"缺口/批判"那一节，必须逐节暴露。"""
    from companion.literature import evidence

    papers = [{"title": "Contrastive phishing", "abstract": "Fuses URL text and visual features on 12k pages, 0.94 F1."}]
    body = ("## 方法谱系\n《Contrastive phishing》融合 URL 文本与视觉特征，在 12k 页面上做到 0.94 F1 [1]。\n"
            "## 研究缺口\n未来的研究应当更加关注该领域内在的复杂性与多样性，\n"
            "并在更广泛的场景中持续探索其潜在价值与可能性 [1]。\n")
    parts = evidence.grounding_by_section(body, papers)
    assert {d["section"] for d in parts} == {"方法谱系", "研究缺口"}
    by = {d["section"]: d for d in parts}
    assert by["方法谱系"]["coverage"] == 1.0
    assert by["研究缺口"]["coverage"] == 0.0 and by["研究缺口"]["cited_sentences"] == 1
    assert evidence.grounding_by_section("没有二级标题也没有引用的一段话", papers) == []


def test_plan_failure_records_a_reason_for_postmortem():
    """计划没产出时不能只留一个 None——44% 的产出率要能归因。"""
    import llm
    from companion.intent.model import ResearchIntent

    intent = ResearchIntent(project_id="p", topic="phishing")

    async def boom(prompt, **kw):
        raise llm.LLMError("timeout after 45s")
    orig_json, orig_mock = llm.chat_json, llm.LLM_MOCK
    llm.LLM_MOCK = False            # make_plan 内部是 `from llm import chat_json`，得改源模块
    llm.chat_json = boom
    try:
        assert mentor.make_plan(user_text="想做钓鱼检测", intent=intent, papers=[]) is None
    finally:
        llm.chat_json = orig_json
    assert intent.extra["plan_fail"].startswith("通道:LLMError"), intent.extra

    intent2 = ResearchIntent(project_id="p", topic="phishing")

    async def thin(prompt, **kw):
        return {"actions": [{"do": "只有一条"}], "situation": "x"}
    llm.chat_json = thin
    try:
        assert mentor.make_plan(user_text="想做钓鱼检测", intent=intent2, papers=[]) is None
    finally:
        llm.chat_json = orig_json
        llm.LLM_MOCK = orig_mock
    assert "动作数 1" in intent2.extra["plan_fail"], intent2.extra


def test_plan_quota_fits_the_token_budget_it_asks_for():
    """计划的字段配额与输出预算必须同源：44% 产出率的真因就是配额≈2.6k token 而 max_tokens 只有 700。"""
    q, tokens = mentor.plan_quota_chars(), mentor.plan_quota_tokens()
    assert q < 3000, q
    assert tokens <= mentor.PLAN_MAX_TOKENS * 0.85, (tokens, mentor.PLAN_MAX_TOKENS)   # 至少留 15% 余量
    p = mentor.plan_prompt(user_text="该按什么线组织", intent=_intent(), papers=[])
    assert f"≤{mentor.PLAN_BUDGET['do']}字" in p and f"≤{mentor.PLAN_BUDGET['name']}字" in p
    assert "不超过 40 字" not in p          # 提示词里的数字必须来自同一份常量，不允许两处各写一遍
    long_bucket = {"actions": [{"do": "做" * 200, "with": "用" * 200, "dose": "量" * 200,
                                "check": "判" * 200},
                               {"do": "第二条" * 60, "with": "用" * 200, "dose": "量" * 200,
                                "check": "判" * 200}],
                   "situation": "环" * 300}
    plan = mentor.normalize_plan(long_bucket, 0)
    assert len(plan["situation"]) <= mentor.PLAN_BUDGET["situation"]
    assert len(plan["actions"][0]["check"]) <= mentor.PLAN_BUDGET["check"]


def test_zero_evidence_turn_strips_invented_scale_statistics():
    """轮 204：零证据那一轮不许把编造的规模数字留在正文里。

    线上截图（docs/screenshots/r203b_live_after_deploy_1280.png）里同一屏写着
    「这轮你勾掉了检索，下面的话没有文献依据」，正文却给出 Cora 2,708 个节点／5,429 条边。
    现有的 strip_ungrounded_numbers 管不到：它只审「带 ASCII 角标且命中 RESULT_RE 性能词表」的句子，
    而零证据轮在交付前角标已被剪光、"节点/边数"也不在那张词表里（轮 204 探针量的分布见
    输出/_logs/r204_probe_zero_evidence.log：149 句里 RESULT_RE 一条都不命中）。
    """
    say = "用一句话说明图神经网络在异常检测里最常用的一个公开数据集是什么，不要展开。"
    reply = ('1. "Cora"：这是一个用于节点分类的图数据集，包含27个类别的2,708个节点和5,429条边。'
             '2. "CiteSeer"：这是一个用于文档分类的图数据集，包含331个类别的4,328个节点和4,732条边。')
    out = mentor.strip_ungrounded_scale(reply, [], user_text=say)
    for fab in ("2,708", "5,429", "4,328", "4,732"):
        assert fab not in out, f"编造的规模数字仍然出厂：{fab}"


def test_zero_evidence_scale_gate_keeps_proper_names_and_student_numbers():
    """误删的代价比漏删大：`CIFAR-100` 是专名、3080/161 是学生自己给过的量，两句都不许动。"""
    say = "实验室只有两块 3080，我手上标好的数据只有 161 条。"
    keep = "可以先在 CIFAR-100 上跑一遍，再对照你那 161 条与两块 3080 的显存。"
    assert mentor.strip_ungrounded_scale(keep, [], user_text=say) == keep
    # 手里有论文卡时这条完全不动手——那是 strip_ungrounded_numbers 的领地，两道闸门不抢活
    papers = [{"title": "Cora", "abstract": "2,708 nodes and 5,429 edges"}]
    assert mentor.strip_ungrounded_scale(keep, papers, user_text=say) == keep


def test_scale_gate_keeps_numbers_we_handed_the_model():
    """复述我们喂给它的数不算"编造"：轮 204 第一次上真通道就误删过整条回复（提示词里那行 0.31）。

    同屏对照：同一句不带 context 时照删——证明放行它的是"这个数在我们给它的料里"，不是闸门坏了。
    """
    sent = "这一轮我对你的任务理解度只有 0.31，先把范围收窄。"
    ctx = "【这个学生目前的研究状态】\n我对这次任务的理解度：0.31（1.00 为充分）\n"
    assert mentor.strip_ungrounded_scale(sent, [], user_text="随便聊聊", context=ctx) == sent
    assert mentor.strip_ungrounded_scale(sent, [], user_text="随便聊聊") != sent
    # 千分位的上下文数也要认：料里写 "2,708" 而模型复述成 "2708" 是同一个数
    assert mentor.strip_ungrounded_scale("图数据一般是 2708 个节点。", [], context="规模：2,708 个节点") \
        == "图数据一般是 2708 个节点。"


def test_scale_gate_is_wired_into_the_reply_backstop(monkeypatch):
    """判据必须接在真出口上：走 ask() 一次，编造规模不该到用户眼前（留着能站住的那半句）。"""
    async def fake_chat(prompt, **kw):
        return "最常用的公开数据集是 Cora。它包含27个类别的2,708个节点和5,429条边，适合做异常检测的基线。"

    monkeypatch.setattr(mentor, "chat", fake_chat)
    text, degraded = mentor.ask(user_text="用一句话说明最常用的公开数据集，不要展开。",
                                intent=_intent(), papers=[])
    assert not degraded, text
    assert "Cora" in text and "2,708" not in text and "5,429" not in text


def _stats_reply():
    return ("Cora 是图神经网络异常检测里最常用的公开数据集。\n\n"
            "【本轮检索统计】\n命中论文：5篇\n年跨度：2018-2023\n最高被引：42次\n开放获取：2篇\n\n"
            "你可以先从 Cora 跑一个基线。")


def test_fabricated_stats_block_is_dropped_when_no_papers():
    """本轮没论文 ⇒ 那个块只可能是编的，整块删掉，但正文两句都得留着。"""
    out = mentor.strip_fabricated_search_stats(_stats_reply(), [])
    assert "【本轮检索统计" not in out, out
    assert "Cora 是图神经网络异常检测里最常用的公开数据集。" in out, out
    assert "你可以先从 Cora 跑一个基线。" in out, out


def test_the_real_stats_block_survives_when_papers_are_given():
    """有论文时同一段原样放过：那是真统计，删它才是丢信息。"""
    papers = [{"title": "Cora", "abstract": "a", "year": 2021, "citation_count": 42}]
    assert mentor.strip_fabricated_search_stats(_stats_reply(), papers) == _stats_reply()


def test_the_stats_block_marker_has_exactly_one_source():
    """块头在 mentor.py 里只许出现一次（常量定义）：注入侧与闸门侧都引用它，不许各抄一份。"""
    import io as _io
    import os as _os
    src = _io.open(_os.path.join(_os.path.dirname(mentor.__file__), "mentor.py"),
                   encoding="utf-8").read()
    assert src.count('"【本轮检索统计"') == 1, src.count('"【本轮检索统计"')
    assert "SEARCH_STATS_MARK" in src and src.count("SEARCH_STATS_MARK") >= 3, src.count("SEARCH_STATS_MARK")


def test_stats_gate_is_wired_into_the_reply_backstop(monkeypatch):
    """判据必须接在真出口上：走 ask() 一次，编造的检索统计不该到用户眼前。"""
    async def fake_chat(prompt, **kw):
        return _stats_reply()

    monkeypatch.setattr(mentor, "chat", fake_chat)
    text, degraded = mentor.ask(user_text="介绍一下图神经网络常用的数据集",
                                intent=_intent(), papers=[])
    assert not degraded, text
    assert "【本轮检索统计" not in text, text
    assert "命中论文：5篇" not in text, text


def _shape_violations(text, user_text, papers=None, plan=None):
    return mentor.violations(text, papers=papers or [], plan=plan, user_text=user_text)


_SHAPE_TURN = "用一句话说明图神经网络在异常检测里最常用的一个公开数据集是什么，不要展开。"
_BLANK_TURN = "图神经网络在异常检测里常用哪些公开数据集？我想做个基线。"
_ONE_LINE = "最常用的是 Cora，它常被当作图神经网络异常检测的基线数据集。"
_STEPS = ("好的，我来帮你整理一下：\n"
          "1. 确认 Cora 的规模与切分：用 Planetoid，3 个划分，能说出比例。\n"
          "2. 跑一个 GCN 基线：用 PyG，在 1 周内完成，F1 记进表。\n"
          "3. 对照 CiteSeer：用 Planetoid，2 组，差值写清楚。\n"
          "4. 写失败案例：记 10 条，列进下一轮。")
# 🔴 名字不许叫 _PLAN：那一份是 1048 行起的旧夹具（轮 52 那批用例吃它），模块级重名会把后一份
# 整体盖掉——全量跑当场红了 plan_block 与 plan_dropped 两条老用例（它们测的东西静默变质）。
_R215_PLAN = {"actions": [{"do": "确认 Cora 的规模与切分", "with": "Planetoid", "dose": "3 个划分",
                           "check": "能说出比例"},
                          {"do": "跑一个 GCN 基线", "with": "PyG", "dose": "1 周内", "check": "F1 记进表"}]}
# 下面两组夹具由 输出/_scratch/probe_r215_shape_fixture_yield.py 现量选出（零模型调用）：
# 都要 thin=False（否则 no_quantity/plan_dropped 的 `not thin` 前提就不成立），且空白轮真判出那一条。
_NOQTY = ("你可以先把 Cora 的切分方式记下来，再用同一个随机种子跑一遍 GCN，"
          "把复现结果与你实验室里已有的那条基线逐条对照，差在哪一步就写在同一段落旁边。")
_DROP = ("建议你这三件事一起做：1. 把 20 篇综述按年份排成一列，各写 80 字结论；"
         "2. 用 2 周时间复现 1 个公开基线；3. 把 3 个候选方向各写 150 字，挑出最有把握的那个。")
# 与 _DROP 零词面重合的计划：数字 48/6/12000/8400 都不在正文里，中文 3 字窗口也不撞（撞了闸门就永不触发）
_PLAN_X = mentor.normalize_plan(
    {"actions": [{"do": "核对仪器状态", "with": "Thermo TSQ 8400", "dose": "每 48 小时一次",
                  "check": "质量偏差低于零点一个道尔顿"},
                 {"do": "更换色谱柱", "with": "ZORBAX Eclipse", "dose": "每 6 个月一支",
                  "check": "塔板数高于 12000"}]}, n_papers=0)


def test_the_shape_ruler_only_accepts_explicit_statements():
    """形态尺子只认显式说法：三句该认的全认，两句没说的不认（宁可漏判，不误判）。"""
    assert mentor.shape_requested(_SHAPE_TURN)
    assert mentor.shape_requested("就给三条，别多说。")
    assert mentor.shape_requested("用表格列出来。")
    assert not mentor.shape_requested(_BLANK_TURN)
    assert not mentor.shape_requested("")


def test_a_one_line_answer_is_not_called_lacking_substance_when_the_student_asked_for_that_shape():
    """轮 214 量到的那条违例：学生要一句话，`no_substance` 却判红并用「先给 3—5 条」重采样——现在不该再判。"""
    v = _shape_violations(_ONE_LINE, _SHAPE_TURN, plan=_R215_PLAN)
    assert not v["no_substance"], v
    assert not v["no_quantity"], v
    assert not v["plan_dropped"], v


def test_without_a_shape_request_the_same_one_liner_is_still_called_lacking():
    """负向对照：闸门没被改废——同样的句子在没提形态要求那一轮照旧判红（否则整条让位是装饰）。"""
    v = _shape_violations(_ONE_LINE, _BLANK_TURN, plan=_R215_PLAN)
    assert v["no_substance"], v


def test_the_quantity_gate_yields_only_because_the_student_set_the_shape():
    """`no_quantity` 那条 `and not shape` 有分辨力：同一句 75 字、撑得住但全句无数量的回答，
    空白轮判虚（要它补量），形态轮不判——不是把闸门关掉，是让给学生自己说过的形状。"""
    assert _shape_violations(_NOQTY, _BLANK_TURN)["no_quantity"]
    assert not _shape_violations(_NOQTY, _SHAPE_TURN)["no_quantity"]


def test_the_plan_gate_yields_only_because_the_student_set_the_shape():
    """`plan_dropped` 那条 `and not shape` 同样有分辨力：正文与计划两条动作零词面重合，
    空白轮判「一条都没执行」，形态轮不判。逐键断言，不比 fire 集合（同轮还会让掉 no_quantity）。"""
    assert _shape_violations(_DROP, _BLANK_TURN, plan=_PLAN_X)["plan_dropped"]
    assert not _shape_violations(_DROP, _SHAPE_TURN, plan=_PLAN_X)["plan_dropped"]


def test_the_honesty_gates_do_not_yield_to_the_shape_request():
    """诚实性闸门一条都不许因为“学生要短”而让位：手里给了论文卡却一处角标都没挂，形态轮照样判 no_citation。

    轮 215 实测过那句 32 字短答：它本身就被判「没给东西」（no_substance），而 no_citation 按设计
    不与它叠开火（`and not thin`）——所以拿它验诚实性会让位判成假绿。这里用能自己站住的 153 字四条：
    形态轮里 no_substance 让位、no_citation 照旧开火，两件事同一屏对账。
    """
    papers = [{"title": "Graph Convolutional Networks", "abstract": "semi-supervised", "year": 2017,
               "citation_count": 40000}]
    v = _shape_violations(_STEPS, _SHAPE_TURN, papers=papers)
    assert not v["no_substance"], v
    assert v["no_citation"], v
    # 反向：没给论文卡时不该要求角标（否则形态轮会被两条闸门夹击）
    assert not _shape_violations(_STEPS, _SHAPE_TURN, papers=[])[
        "no_citation"], "没给论文卡却要求挂角标"


def test_the_shape_ruler_split_by_family_still_matches_exactly_the_old_set():
    """轮 228 把形态尺拆成三段（短式／条数／呈现）是为了给「超形状」用界，不是为了改判据。

    这条把旧那份正则逐字钉在测试里，拿一批话让新旧两边各判一次：多认一条、少认一条都红。
    重点是 "给三条"——旧口径里它不算（必须"只给三条"或"给三条就行/好/够"），拆段时最容易顺手放宽。
    """
    legacy = re.compile(
        r"一句话|一句就|简单说|简短说|长话短说|别展开|不要展开|不用展开|别多说|不要多说|"
        r"只给\s*[一二三四五六七八九十\d]+\s*条|给\s*[一二三四五六七八九十\d]+\s*条就[行好够]|"
        r"用表格|别引用|不要引用|不用引用")
    corpus = [_SHAPE_TURN, _BLANK_TURN, "", "只给三条", "给三条", "给三条就行", "给三条也好",
              "给三条够了", "给我三条", "用表格列出来。", "别引用", "不要引用这里的说法",
              "简单说", "长话短说", "这个数据集叫什么，一句话能说完吗", "多给几条"]
    for t in corpus:
        assert bool(legacy.search(t)) == mentor.shape_requested(t), t


def test_over_shape_fires_when_the_student_asked_for_one_line_but_the_body_runs_four_sentences():
    """轮 227 的分布：学生点名形状那 23 轮里句数中位=6、超 2 句占 0.61 ⇒ 提示词守不住，这条闸门要咬人。

    夹具是现成的四条清单 `_STEPS`（4 句、4 个列表项），对上一句话的要求。
    """
    assert mentor.sentence_count(_STEPS) == 4
    assert _shape_violations(_STEPS, _SHAPE_TURN)["over_shape"]


def test_over_shape_lets_a_two_sentence_answer_through():
    """边界钉死：阈值 2 是"超过两句才判"，两句正常回答（带一句补充）不许被重采样。"""
    two = ("最常用的是 Cora，它常被当作图神经网络异常检测的基线数据集。"
           "第二个常用的是 CiteSeer，规模与切分方式相近。")
    assert mentor.sentence_count(two) == 2
    assert not _shape_violations(two, _SHAPE_TURN)["over_shape"]


def test_over_shape_stays_silent_when_the_student_never_named_a_shape():
    """负向对照（§5.1：不为"像它"压长度）：同一句四句清单，空白轮一律不判——判据只认学生原话。"""
    assert not _shape_violations(_STEPS, _BLANK_TURN)["over_shape"]
    assert not _shape_violations(_STEPS, "")["over_shape"]


def test_over_shape_uses_the_students_own_number_when_they_counted_the_items():
    """条数那一族按学生说的那个数判：三条过、四条超——不是统一压到两句。"""
    three = ("1. 确认 Cora 的规模与切分：用 Planetoid，3 个划分。\n"
             "2. 跑一个 GCN 基线：用 PyG，在 1 周内完成。\n"
             "3. 对照 CiteSeer：用 Planetoid，2 组差值。")
    assert mentor.sentence_count(three) == 3
    assert not _shape_violations(three, "只给三条。")["over_shape"]
    assert _shape_violations(_STEPS, "只给三条。")["over_shape"]


def test_over_shape_does_not_guess_a_bound_it_cannot_read():
    """两种"没有数量上界可核对"的形状一律不判：只说呈现方式（用表格），以及认不出的数（十二条）。"""
    assert not _shape_violations(_STEPS, "用表格列出来。")["over_shape"]
    assert not _shape_violations(_STEPS, "只给十二条。")["over_shape"]


def test_the_shape_gate_actually_reaches_the_repair_loop(monkeypatch):
    """闸门不是装饰：只超形状时，`repair()` 真的重采样，且纠正语里点名了"超出形状"这件事。"""
    calls = []

    async def fake_chat(prompt, **kw):      # `repair` 走 run_async(chat(...))，桩必须是协程
        calls.append(prompt)
        return _ONE_LINE

    monkeypatch.setattr(mentor, "chat", fake_chat)
    out = mentor.repair(_STEPS, user_text=_SHAPE_TURN, intent=_intent(),
                        history=[{"role": "user", "content": _SHAPE_TURN}], papers=[])
    assert calls, "over_shape 没进重采样循环"
    assert "超出了那个形状" in calls[0]
    assert out == _ONE_LINE


class _TraceIntent:
    """只要 `.extra` 是个字典就够——`note_deletions` 走的就是这条最窄的口。"""

    def __init__(self):
        self.extra = {}
        self.topic = "图神经网络"
        self.stage = "追问"


_NL = chr(10)   # 用 chr(10) 而不是 \n：这段文本由补丁脚本落盘，转义层会把它吃成真换行（本轮真踩过）
_FAB = ("我建议你先跑一个基线：用 PyG，两周内出 F1，这一版先不看别的。" + _NL +
        "【本轮检索统计】" + _NL + "命中论文：9 篇" + _NL + "年跨度：2019-2024。" + _NL + _NL +
        "另外 Cora 有 2,708 个节点、5,429 条边，够你第一天用。")
_CLEAN_OK = ("你说要做异常检测的基线，最相关的是 Graph Convolutional Networks，它做的是半监督分类 [1]。"
             "建议先跑 2 周基线，用 PyG，把 F1 记进表；再对照 CiteSeer，两组差值写清楚；"
             "最后记 10 条失败案例，列进下一轮的表里，这样下个月就有可比的东西。")


def test_a_block_the_gate_deleted_is_recorded_under_the_gate_own_name():
    """闸门真删了东西 ⇒ 按它自己的函数名记下删掉的字数——这才是"看得见它开过火"。"""
    it = _TraceIntent()
    out = mentor.strip_chain(_FAB, intent=it, papers=[], user_text="给我三条建议")
    got = it.extra["clean_deletions"]
    assert got.get("strip_fabricated_search_stats", 0) > 0, got
    assert "【本轮检索统计" not in out and "2,708" not in out, (out[:120], got)


def test_a_round_that_deleted_nothing_still_records_an_empty_dict():
    """一条都没删也要记：空字典＝"这轮没开火"，缺键＝"这条路没记账"，两者必须分得开。"""
    it = _TraceIntent()
    papers = [{"title": "Graph Convolutional Networks", "abstract": "semi-supervised classification",
               "year": 2017, "citation_count": 40000}]
    mentor.strip_chain(_CLEAN_OK, intent=it, papers=papers, user_text="给我三条建议")
    assert it.extra["clean_deletions"] == {}, it.extra


def test_the_key_is_the_gate_function_name_so_a_rename_cannot_silently_desync_it():
    """键名取自函数本身：这几道闸门的名字必须在册（写死的字符串会在改名那天脱钩）。"""
    names = [f.__name__ for f in (mentor.strip_unverified, mentor.strip_ungrounded_numbers,
                                  mentor.strip_dangling_citations, mentor.strip_empty_numbering,
                                  mentor.strip_fabricated_search_stats, mentor.strip_ungrounded_scale)]
    it = _TraceIntent()
    mentor.strip_chain(_FAB, intent=it, papers=[], user_text="给我三条建议")
    assert set(it.extra["clean_deletions"]) <= set(names), (it.extra, names)
    assert set(it.extra["clean_deletions"]) - {"strip_unverified", "strip_ungrounded_numbers",
                                               "strip_dangling_citations", "strip_empty_numbering",
                                               "strip_fabricated_search_stats",
                                               "strip_ungrounded_scale"} == set(), it.extra


def _shape_priority_line(system: str):
    """取 SYSTEM 里那句「以 ta 说的为准」的整行；没有就返回 None（三条判据共用，不各抄一份）。"""
    for ln in system.splitlines():
        if "以 ta 说的为准" in ln:
            return ln
    return None

def test_the_system_grants_the_students_stated_shape_priority():
    """学生这轮显式要的形态，必须在 SYSTEM 里有一条压倒默认条数/字数的话。"""
    line = _shape_priority_line(mentor.SYSTEM)
    assert line is not None, "SYSTEM 里没有「以 ta 说的为准」那句形态优先"
    assert "一句话" in line and "用表格" in line, line


def test_that_shape_priority_comes_before_the_default_count_mandate():
    """那句必须排在「必须先给 3—5 条」之前——模型先读到优先级，才会在列清单前收手。"""
    line = _shape_priority_line(mentor.SYSTEM)
    assert line is not None, "SYSTEM 里没有那句形态优先"
    pos_priority = mentor.SYSTEM.index("以 ta 说的为准")
    pos_mandate = mentor.SYSTEM.index("3—5 条")
    assert pos_priority < pos_mandate, f"形态优先在第 {pos_priority} 字，硬指令在第 {pos_mandate} 字"


def test_the_honesty_clauses_are_not_exempted_by_shape_priority():
    """形态可以让位，依据/角标/诚实性不行——这半截是那句自己的限定，不许被顺手删掉。"""
    line = _shape_priority_line(mentor.SYSTEM)
    assert line is not None, "SYSTEM 里没有那句形态优先"
    assert "不让位" in line, line
    assert "依据" in line and "角标" in line, line
