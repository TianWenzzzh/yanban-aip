"""轮 106 P1：反证注入闸门——料只能来自原文，改完必须用同一把尺复算。

走 `build_report` 完整产品路径（不是直接调私有函数），并带一条
「模型只说换了词、没挂编号」的退回测试：闸门不许被话糊过去。
"""
import os
import pathlib

os.environ["LLM_MOCK"] = "1"  # 与同目录其它测试一致：import 期定值

import pytest

import companion.db as db_module
import companion.knowledge as knowledge
from companion.conversation import report
from companion.intent.model import ResearchIntent

ABS1 = ("We forecast photovoltaic output with a hybrid LSTM model trained on two years "
        "of interval data. However, nonstationarity and protracted cloud transients "
        "remain unresolved in operational photovoltaic dispatch across seasons.")
ABS2 = ("A transformer variant reduces forecast error for distributed energy resources "
        "and reports stable behaviour on several public benchmark series each year.")
ABS3 = ("A feature selection study for building load prediction with gradient boosting "
        "shows consistent gains on measured datasets at fifteen minute granularity.")
ABS4 = ("A review of uncertainty quantification for renewable generation summarises "
        "conformal intervals and argues calibration matters more than sharpness.")

PAPERS = [
    {"id": "a1", "title": "Hybrid LSTM photovoltaic forecasting", "abstract": ABS1, "year": 2024},
    {"id": "a2", "title": "Transformer forecasting of energy series", "abstract": ABS2, "year": 2024},
    {"id": "a3", "title": "Feature selection for building load prediction", "abstract": ABS3, "year": 2023},
    {"id": "a4", "title": "Uncertainty quantification for renewable generation", "abstract": ABS4, "year": 2023},
]


def _skeleton() -> str:
    """每节都带一条 [n]，让补引用闸门不触发，只有反证注入这一道会调模型。"""
    return "\n\n".join(f"## {s}\n这类做法在光伏预测里被反复使用 [1]。多数基准上误差下降 [2][3][4]。"
                       for s in report.SECTIONS)


@pytest.fixture()
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(db_module, "DB_PATH", tmp_path / "t.db")
    monkeypatch.setattr(knowledge, "KB_ROOT", tmp_path / "kb")
    db_module._CONN = None
    yield
    db_module._CONN = None


GLOSS_MARK = "把下面每一句转成"


def _rewrite(calls):
    """只看改写那一条：轮 174 起，反证这一路还会为跨语言桥发几次取词调用，
    把它们混进"只许一次调用"的计数里，判据就分不清"重试复活"与"桥词取词"两种红因。"""
    return [c for c in calls if GLOSS_MARK not in c]


def _gloss(calls):
    return [c for c in calls if GLOSS_MARK in c]


def _run(isolated, monkeypatch, reply: str, papers=None, boom: bool = False):
    import llm

    papers = PAPERS if papers is None else papers
    calls: list[str] = []

    async def fake(prompt, **kw):
        calls.append(prompt)
        if GLOSS_MARK in prompt:
            # 给**真词**而不是空表：空表会让 `gm` 全程为空，"桥到底有没有进判定"在这份
            # 夹具里就永远测不到（独立评审量的正是这个）。词面取自被引那篇的摘要。
            return ('{"items": [{"i": "0", "k": ["photovoltaic", "dispatch", "seasons"]},'
                    ' {"i": "1", "k": ["forecast", "error", "benchmark", "series"]},'
                    ' {"i": "2", "k": ["photovoltaic", "output", "LSTM"]}]}')
        if "这一节现在的样子" in prompt:
            if boom:
                raise TimeoutError("测试造的调用超时")
            return reply
        return _skeleton()

    async def fake_stream(prompt, **kw):
        for chunk in (_skeleton()[:400], _skeleton()[400:]):
            if chunk:
                yield chunk

    monkeypatch.setattr(llm, "LLM_MOCK", False)
    monkeypatch.setattr(llm, "chat", fake)
    monkeypatch.setattr(llm, "chat_stream", fake_stream)
    out = report.build_report("projCE", ResearchIntent(project_id="projCE", topic="光伏功率预测"), papers)
    return out, calls


def test_limitation_sentence_reaches_the_prompt_verbatim(isolated, monkeypatch):
    out, calls = _run(isolated, monkeypatch,
                      "nonstationarity 与 protracted cloud transients 至今未被解决 [1]。"
                      "这类做法在光伏预测里被反复使用 [1]。多数基准上误差下降 [2][3][4]。")
    assert out["ok"]
    assert len(_rewrite(calls)) == 1, f"反证注入只许一次改写调用，实得 {len(_rewrite(calls))}"
    # 轮 178：反证这一路**一次桥都不该取**（g183 把写路径的桥撤了：错配池上开桥把改指从 6 抬到 12）。
    # 桩仍然返回真词——这样"0 批"才证明是**产品没去调**，而不是"调了但拿回空表"。
    assert len(_gloss(calls)) == 0, f"反证路径又去取桥词了（{len(_gloss(calls))} 批）"
    prompt = _rewrite(calls)[0]
    # 递给模型的必须是原文那句，连编号一起——不是「你去谈谈争议」这种空话
    assert "However, nonstationarity and protracted cloud transients" in prompt
    assert "[1]" in prompt
    # 光给原文不够，得把「照抄几个词」的下限一起递过去：
    # `exp_quote_floor.py` 量到 MIN_TOKENS=2，一个词核不回、两个词起判得出 against=1
    assert "逐字照抄" in prompt and "4—6" in prompt
    assert out["sections_counterev"], "改成了就要如实上报"
    assert out["counterev_note"] == [], "落地了就不该有解释要写"
    head = out["sections_counterev"][0]
    first = report._first_sentence(dict(report.split_sections(out["body"]))[head])
    assert "nonstationarity" in first and "[1]" in first


def test_facts_block_is_one_shared_builder_not_a_copy(isolated, monkeypatch):
    """闸门递的那段事实必须出自 `_counterev_facts`——探针靠它复用同一段文本。

    为什么钉这条：位置实验（`probe_counterev_position.py`）要回答"是不是首句这个要求卡住了落地"，
    前提是它递给模型的 facts 与线上逐字相同。措辞一旦在两处各存一份，测的就不是发的。
    """
    material = report._limitation_hits(PAPERS, {1})
    assert material, "夹具里那篇必须带局限句，否则这条测试什么都没测"
    facts = report._counterev_facts(material)
    assert "However, nonstationarity and protracted cloud transients" in facts
    assert "4—6 个英文词" in facts
    _out, calls = _run(isolated, monkeypatch,
                       "nonstationarity 与 protracted cloud transients 至今未被解决 [1]。"
                       "这类做法在光伏预测里被反复使用 [1]。多数基准上误差下降 [2][3][4]。")
    assert facts in _rewrite(calls)[0], "闸门递进模型的 facts 与 _counterev_facts 的输出不一致"


def test_marker_moved_by_the_program_does_not_count_as_cited(isolated, monkeypatch):
    """🔴 编号必须是**模型自己挂的**才算数：`clean_report` 里含机械改指（轮 174 起还带桥），
    模型把局限句挂到 [3] 时，程序会替它挪到 [1]——拿清洗后的文本判 `cited_ok`，
    闸门就是给自己发合格章（独立评审用夹具量到过：正文落地 [1]、sections 非空、note 为空）。"""
    out, calls = _run(isolated, monkeypatch,
                      "nonstationarity 与 protracted cloud transients 至今未被解决 [3]。"
                      "这类做法在光伏预测里被反复使用 [1]。多数基准上误差下降 [2][3][4]。")
    assert out["ok"]
    assert out["sections_counterev"] == [], f"程序替它挂的编号被当成模型自己挂的：{out}"
    note = "｜".join(out["counterev_note"])
    assert "编号挂上=False" in note, f"退回原因要点名这一条：{note}"
    # 前提自证：这句确实**能**被程序改指到 [1]（否则这条测试什么都没测，只是模型没挂上号）
    cleaned = report.clean_report("nonstationarity 与 protracted cloud transients 至今未被解决 [3]。",
                                  PAPERS)
    assert "[1]" in cleaned and "[3]" not in cleaned, cleaned


def test_counter_evidence_takes_no_gloss_at_all(isolated, monkeypatch):
    """轮 174 在这里开过桥（还配过"两侧同一把尺"的修法），轮 178 按 g183 全撤：
    这一路的 coverage 决定的是**要不要把一句新话插进报告正文**，判错的方向是往里写东西。
    钉法用调用点文本，不用"函数里有没有 gm 变量"——变量名换了判据就瞎了。"""
    body = _func_body()
    assert body.count("evidence.grounding_report(") == 3
    assert "gloss_map=" not in body and "english_gloss(" not in body
    assert "bilingual" not in body


def _func_body() -> str:
    src = pathlib.Path(report.__file__).read_text(encoding="utf-8")
    i, j = src.index("def _counter_evidence("), src.index("def rewrite_section(")
    return src[i:j]


def test_only_the_new_sentence_is_inserted_and_the_rest_is_untouched(isolated, monkeypatch):
    """轮 111 的全部要点：模型多写的东西不许进正文，原节其余文字必须逐字还在。

    对着 `g87` 的实测退回原因写这条：整节重写把接地率 0.833 拖到 0.417（分割那节 1.0→0.5），
    因为无关句子被重排过就一定对不上原文。"只插一句、别的不动" 是可机检的，就机检它。
    """
    out, calls = _run(isolated, monkeypatch,
                      "nonstationarity 与 protracted cloud transients 至今未被解决 [1]。"
                      "我把整节都重写了一遍，再加两条自己想的建议 [2]。更多废话。")
    assert out["ok"] and out["sections_counterev"], "首句过关的改写这次必须落地"
    head = out["sections_counterev"][0]
    body = dict(report.split_sections(out["body"]))[head]
    assert "我把整节都重写了一遍" not in body and "更多废话" not in body
    assert "这类做法在光伏预测里被反复使用 [1]。多数基准上误差下降 [2][3][4]。" in body, \
        "原节的句子一个字都不许被改写挪动"


def test_gate_rejects_a_rewrite_without_the_citation(isolated, monkeypatch):
    """🔴 模型只换了说法、没挂上那篇的编号 → 必须整节退回，不能算「补了反证」。"""
    out, calls = _run(isolated, monkeypatch,
                      "这套办法其实也有做不到的地方，未来还需要更多验证。"
                      "这类做法在光伏预测里被反复使用 [2]。多数基准上误差下降 [3][4]。")
    assert out["ok"]
    assert len(_rewrite(calls)) == 1, "只许一次改写调用（重试在 g78 被证伪，见 493d6f3 与日志）"
    assert out["sections_counterev"] == []
    note = "｜".join(out["counterev_note"])
    assert "没通过复算" in note and "编号挂上=False" in note, f"退回必须说清为什么：{note}"
    for head, text in report.split_sections(out["body"]):
        if head and head not in report.LOCKED_SECTIONS:
            assert "做不到的地方" not in text, "被拒的改写不许留在正文里"


def test_gate_stays_shut_when_no_paper_admits_a_limitation(isolated, monkeypatch):
    """池子里没有否定/局限原文时，闸门必须一言不发——不许编一个分歧出来。"""
    calm = [dict(p, abstract=p["abstract"].replace(
        "However, nonstationarity and protracted cloud transients remain unresolved in operational photovoltaic dispatch across seasons.",
        "Besides, the model tracks seasonal variation closely on the studied benchmarks."))
        for p in PAPERS]
    out, calls = _run(isolated, monkeypatch, "随便写点什么 [1]。", calm)
    assert out["ok"]
    assert out["sections_counterev"] == []
    assert sum("库里核对" in c for c in calls) == 0, "没有素材就不该发起那一次调用"
    assert "找料落空" in "｜".join(out["counterev_note"]), "没料也要说，别让读数分不清是没料还是没跑"


def test_call_failure_is_named_as_such(isolated, monkeypatch):
    """`g75` 线上「有料、落地=空」的第三种可能：那次改写调用根本没成。

    超时不能被吞成「闸门没动手」——那是 P0 修过的老毛病（只报结果不报分支）。
    """
    out, calls = _run(isolated, monkeypatch, "", boom=True)
    assert out["ok"] and out["sections_counterev"] == []
    assert len(_rewrite(calls)) == 1        # 只许一次改写调用；取词那几次不是"改写重试"
    note = "｜".join(out["counterev_note"])
    assert "TimeoutError" in note, f"要报出异常类型：{note}"


def test_first_sentence_extraction():
    assert report._first_sentence("短句。第二句够长够长够长够长够长。") == "第二句够长够长够长够长够长。"
    assert report._first_sentence("") == ""
    assert report._first_sentence("只有一句且足够长的一段话用于核对。") == "只有一句且足够长的一段话用于核对。"


BOILER = ("However, the reported results remain limited because the method was evaluated "
          "only on the same dataset with the same model and the same metric.")


def test_facts_block_only_offers_words_the_checker_can_actually_see():
    """递给模型的「可抄词」必须 ∈ rare_tokens(池子)。

    为什么钉这条：`survey` 先把论断词元与 `rare_tokens` 求交，池子里到处都有的套话会被整个丢掉。
    原实现只按「ASCII、非数字、长度>4」筛，于是可能让模型照抄一批**抄了也核不回**的词——
    `g88` 唯一那次退回就是那个形状（编号挂上=True、支持 0、相反 0）。
    """
    from companion.literature import evidence

    rare = evidence.rare_tokens(PAPERS)
    assert rare is not None, "夹具池子不足 3 篇时稀有词判定整条关掉，这条测试什么都没测"
    material = report._limitation_hits(PAPERS, {1})
    facts = report._counterev_facts(material, PAPERS)
    seg = facts.split("单句可用的词有：", 1)[1].split("）", 1)[0]
    offered = {t.strip() for t in seg.split(",") if t.strip()}
    assert offered, "可用词被过滤成空 → 提示词里会出现残句，模型无从照抄"
    assert offered <= rare, f"这些词 survey 根本不看：{sorted(offered - rare)}"


def test_limitation_hits_refuses_a_quote_made_only_of_boilerplate():
    """只有套话局限句的那篇**不该进闸门**：抄得再全是核不回来的，白叫一次模型。

    夹具做法：四篇摘要逐字相同（每个词 df=4 → 全是套话），因此任何一句都不含稀有词。
    加这条之前 `_limitation_hits` 会照收不误（它只看否定标记），这正是一次注定失败的调用。
    """
    pool = [{"id": f"b{i}", "title": f"Paper {i} on distinct topics", "abstract": BOILER,
             "year": 2024} for i in range(4)]
    assert report._limitation_hits(pool, {1, 2}) == [], "套话局限句仍被当成可用材料送进闸门"
    # 正对照：同一函数在真料上必须仍然找得到（否则上面那条只是"永远返回空"的假绿）
    assert report._limitation_hits(PAPERS, {1}), "真料被一起滤掉了——那不是收紧判据，是关掉闸门"
