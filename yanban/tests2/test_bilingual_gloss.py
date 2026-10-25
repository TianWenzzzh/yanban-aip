"""跨语言接地桥（中文句 ↔ 英文摘要）的回归测试，全部在 LLM_MOCK 或桩化通道下，零真实调用。

要守的三件事：
① 拿不到桥（MOCK、抛错、格式不合）时行为必须与今天**逐字相同**——降级不许被读成"没依据"；
② 桥只能用来**找重合**，不能把没依据的句子洗成有依据（负例必须仍然 true_gap）；
③ 用了桥就要在输出里声明（`bilingual`），读数不许不知道自己拿的是哪把尺。
"""
import os
import pathlib
import re

os.environ["LLM_MOCK"] = "1"

import pytest

import companion.db as db_module
import llm
from companion.literature import evidence

ZH = "这篇图神经网络方法在钓鱼检测上做到 92% 的准确率[1]，用的是公开数据集。"
EN = "This graph neural network method reaches 92% accuracy on phishing detection datasets [1]."

PHISH = {"title": "Graph features for phishing detection",
         "abstract": "We achieve 92% accuracy on the Urbuna phishing dataset using graph features."}
MARINE = {"title": "Coral reef classification with remote sensing",
          "abstract": "Sea temperature drives coral bleaching rates across the Great Barrier Reef."}


def stub_gloss(monkeypatch, items):
    """桩化那条翻译调用：返回固定 JSON，且不真的出门。"""
    monkeypatch.setattr(llm, "LLM_MOCK", False)

    async def fake_chat_json(prompt, **kw):
        return {"items": items}

    monkeypatch.setattr(llm, "chat_json", fake_chat_json)


@pytest.fixture()
def isolated_db(tmp_path, monkeypatch):
    monkeypatch.setattr(db_module, "DB_PATH", tmp_path / "test.db")
    db_module._CONN = None
    yield
    db_module._CONN = None


# ── 什么时候起桥 ──────────────────────────────────────────────────────
def test_needs_gloss_only_for_chinese_with_citations():
    assert evidence.needs_gloss(ZH)
    assert not evidence.needs_gloss(EN)                    # 英文产物走原路，历史读数不受影响
    assert not evidence.needs_gloss("这句话很长但没有角标也没有篇名，纯粹在讲处境。")
    assert not evidence.needs_gloss("短[1]")               # 太短不值得多一次调用
    assert not evidence.needs_gloss("")


def test_mock_and_failure_both_degrade_to_literal():
    """MOCK 下必须一个字都不改地等于不开桥。"""
    a = evidence.grounding_report(ZH, [PHISH])
    b = evidence.grounding_report(ZH, [PHISH], bilingual=True)
    assert a == b and b["bilingual"] is False


def test_bridge_failure_does_not_break_the_reading(monkeypatch):
    monkeypatch.setattr(llm, "LLM_MOCK", False)

    async def boom(prompt, **kw):
        raise llm.LLMError("通道挂了")

    monkeypatch.setattr(llm, "chat_json", boom)
    assert evidence.english_gloss([ZH]) == {}
    got = evidence.grounding_report(ZH, [PHISH], bilingual=True)
    assert got["cited_sentences"] == 1 and got["bilingual"] is False


# ── 桥的校验：只收能核对的形状 ────────────────────────────────────────
def test_gloss_drops_every_unusable_row(monkeypatch):
    stub_gloss(monkeypatch, [
        {"i": 0, "k": "phishing graph accuracy 92"},        # 合格
        {"i": 1, "k": "phishing detection"},                # 越界编号
        {"i": 0, "k": "钓鱼 检测"},                          # 非 ASCII（没翻）
        {"i": 0, "k": "accuracy"},                          # 词数下限以下
        {"i": 0, "k": " ".join(["word"] * 14)},             # 词数超上限
        {"i": 0, "k": ["方法", "有效"]},                     # 数组里夹中文
        {"i": "x", "k": "phishing graph"},                  # 编号不是整数
        "不是对象",
    ])
    got = evidence.english_gloss([ZH])
    assert got == {evidence.gloss_key(ZH): "phishing graph accuracy 92"}


def test_gloss_accepts_the_shape_the_channel_actually_returns(monkeypatch):
    """夹具形状必须来自**真实返回体**，不是我自己编的。

    g177 从容器里打出来的原样返回是 `{"i": "0", "k": ["Graph Neural Network", ...]}`
    ——`k` 是数组、`i` 是字符串。首版校验按"字符串 + 纯 ASCII 词"写，把 3/3 行全丢了，
    于是这条桥在线上是空转的，而离线用例全绿（因为它测的是我编的形状）。
    """
    real = {"items": [
        {"i": "0", "k": ["Graph Neural Network", "Fishing Detection", "92% Accuracy", "Public Dataset"]},
        {"i": "1", "k": ["Contrastive Learning", "Pre-training", "Encoder", "5 Points Superior"]},
    ]}
    monkeypatch.setattr(llm, "LLM_MOCK", False)

    async def fake_chat_json(prompt, **kw):
        return real

    monkeypatch.setattr(llm, "chat_json", fake_chat_json)
    got = evidence.english_gloss([ZH, "作者提出用对比学习预训练编码器，在三个基准上超过已有方法。"])
    assert len(got) == 2, f"真实返回形状被丢了：{got}"
    assert got[evidence.gloss_key(ZH)] == "Graph Neural Network Fishing Detection 92% Accuracy Public Dataset"


def test_temperature_and_retries_are_pinned(monkeypatch):
    """桥是判定用的一条腿：温度必须 0，否则同一句两次翻出不同词面，读数不可复现。"""
    seen = {}

    monkeypatch.setattr(llm, "LLM_MOCK", False)

    async def capture(prompt, **kw):
        seen.update(kw)
        return {"items": [{"i": 0, "k": "phishing graph accuracy"}]}

    monkeypatch.setattr(llm, "chat_json", capture)
    evidence.english_gloss([ZH])
    assert seen.get("temperature") == 0.0 and seen.get("max_tokens") == 420


# ── 分批：长报告不许一次塞满 ─────────────────────────────────────────
G178_LONG_REPORT = 19
"""夹具的句子数取自 g178 实测的中文报告句数区间（17—22）取中。
🔴 不许写成 GLOSS_BATCH 的倍数：那样一旦有人把上限调到能一次装下整份报告，
用例的期望会跟着一起改，测试就永远测不出"又回到一次全塞"这个真实的退化形状。"""


def zh_sents(n: int) -> list[str]:
    """n 句互不相同、每句带一个 ASCII 编号的中文句子（编号用来验桥词有没有认回原句）。"""
    return [f"这一句是 M{i} 号，讲图神经网络在钓鱼检测上做到 92% 准确率[1]。" for i in range(n)]


def bridge_words(line: str) -> str:
    return "graph phishing " + re.search(r"M\d+", line).group(0)


def batch_lines(prompt: str) -> list[str]:
    return [l for l in prompt.splitlines() if re.match(r"^\d+\. ", l)]


def batch_ids(lines: list[str]) -> set[str]:
    return {re.search(r"M(\d+)", l).group(1) for l in lines}


def test_long_report_is_split_into_batches(monkeypatch):
    """g178 实测：一份中文报告常有 17—22 句，一次全塞进去 max_tokens=420 装不下整份 JSON，
    解析失败就静默退回 {}——9 份可用报告里只有 3 份的桥真的启用。
    分批后：单批不许超过上限、长报告必须切成多批，且每段回来的编号要认得回**本段**的原句。"""
    sizes: list[int] = []
    monkeypatch.setattr(llm, "LLM_MOCK", False)

    async def fake(prompt, **kw):
        lines = batch_lines(prompt)
        sizes.append(len(lines))
        return {"items": [{"i": i, "k": bridge_words(l)} for i, l in enumerate(lines)]}

    monkeypatch.setattr(llm, "chat_json", fake)
    sents = zh_sents(G178_LONG_REPORT)
    got = evidence.english_gloss(sents)
    assert len(sizes) >= 2, f"19 句的报告只发了一次调用（{sizes}）⇒ 桥会被 max_tokens 截断"
    assert max(sizes) <= evidence.GLOSS_BATCH, f"单批 {max(sizes)} 句 > 上限 {evidence.GLOSS_BATCH}"
    assert len(got) == len(sents)
    for i, s in enumerate(sents):
        assert got[evidence.gloss_key(s)] == bridge_words(s), f"第 {i} 句的桥词认错了人"


def test_one_batch_failing_does_not_void_the_others(monkeypatch):
    """第二段超时只能丢第二段：整份报告的桥不许因一次失败全空（那正是 g178 的空转形状）。
    哪些句子属于第二段，从**实际发出的调用**里读，不拿 GLOSS_BATCH 反推。"""
    monkeypatch.setattr(llm, "LLM_MOCK", False)
    groups: list[set[str]] = []

    async def half_dead(prompt, **kw):
        lines = batch_lines(prompt)
        groups.append(batch_ids(lines))
        if len(groups) == 2:
            raise llm.LLMError("第二段通道超时")
        return {"items": [{"i": i, "k": bridge_words(l)} for i, l in enumerate(lines)]}

    monkeypatch.setattr(llm, "chat_json", half_dead)
    got = evidence.english_gloss(zh_sents(G178_LONG_REPORT))
    assert len(groups) >= 2, f"没切成多批（{len(groups)} 批），这条用例验不了段间隔离"
    dead, alive = groups[1], set().union(*groups) - groups[1]
    assert dead and alive
    assert batch_ids(list(got.values())) == alive          # 其余段落照旧拿到桥词


def test_short_report_still_makes_one_call(monkeypatch):
    """分批不许把短句报告变成多次调用：每次调用都是真金白银，且全空句不该发出去。"""
    sizes: list[int] = []
    monkeypatch.setattr(llm, "LLM_MOCK", False)

    async def fake(prompt, **kw):
        sizes.append(len(batch_lines(prompt)))
        return {"items": []}

    monkeypatch.setattr(llm, "chat_json", fake)
    evidence.english_gloss(zh_sents(2))
    assert sizes == [2]
    assert evidence.english_gloss(["", "  "]) == {}
    assert sizes == [2], "全是空句也发了一次调用"


# ── 起桥之后判据往哪边走 ─────────────────────────────────────────────
def test_chinese_claim_becomes_grounded_only_via_bridge(monkeypatch):
    assert evidence.grounding_report(ZH, [PHISH])["true_gap"] == 1        # 瞎尺子：看不见依据
    stub_gloss(monkeypatch, [{"i": 0, "k": "phishing graph accuracy 92 urbuna"}])
    got = evidence.grounding_report(ZH, [PHISH], bilingual=True)
    assert got["bilingual"] is True
    assert got["grounded"] == 1 and got["true_gap"] == 0
    span = got["rows"][0]["span"]
    assert span["text"] in PHISH["abstract"]          # 片段必须逐字来自原文，不是模型的话
    assert span["paper"] == 0


def test_bridge_cannot_invent_grounding(monkeypatch):
    """翻了也没用：池子里没有那些词，结局照旧是没依据。这条防的是"翻译即接地"的假阳性。"""
    stub_gloss(monkeypatch, [{"i": 0, "k": "phishing graph accuracy 92 urbuna"}])
    got = evidence.grounding_report(ZH, [MARINE], bilingual=True)
    assert got["bilingual"] is True and got["true_gap"] == 1 and got["grounded"] == 0


def test_wrong_number_still_readable_as_misnumbered(monkeypatch):
    """[2] 挂错篇、依据在池子别处：桥把这类从 true_gap 里分出来，改指才有对象。"""
    stub_gloss(monkeypatch, [{"i": 0, "k": "coral bleaching sea temperature reef"}])
    claim = "海水温度上升导致大堡礁珊瑚白化率上升[2]，这套遥感分类方法也用上了。"
    literal = evidence.grounding_report(claim, [MARINE, PHISH])
    bridged = evidence.grounding_report(claim, [MARINE, PHISH], bilingual=True)
    assert literal["true_gap"] == 1
    assert bridged["misnumbered"] == 1 and bridged["true_gap"] == 0


def test_english_text_unaffected_by_the_flag(monkeypatch):
    stub_gloss(monkeypatch, [{"i": 0, "k": "should not be used"}])
    a = evidence.grounding_report(EN, [PHISH])
    b = evidence.grounding_report(EN, [PHISH], bilingual=True)
    assert a == b                                          # needs_gloss 为假 ⇒ 一次调用都不发


# ── 单一出处与端点 ───────────────────────────────────────────────────
# 消费者清单判据搬走了：`tests2/test_report_bridge.py::test_bridge_consumers_are_exactly_the_read_only_paths`
# 那份把"谁在用桥"与"写路径不许用"合在一起管。留两份就是留一对迟早各说一套的抄本
# （本轮就漂过一次：这份还写着 report.py 有两处，而 report.py 已经按 g183 撤干净了）。


def test_cite_context_endpoint_reports_the_bridge_state(client_isolated):
    from companion.memory import paper_memory
    pid = "cite-bl"
    paper_memory.save_many(pid, [dict(PHISH, id="p1")], status="recommended")
    body = {"project_id": pid, "claim": ZH, "paper_index": 1}
    r = client_isolated.post("/companion/cite/context", json=body)
    assert r.status_code == 200 and r.json()["found"] is False and r.json()["bilingual"] is False


def test_cite_context_actually_uses_the_bridge(client_isolated, monkeypatch):
    """光声明 bilingual 不够：端点必须真的把桥词喂进 best_spans（否则这条用户可见的路径没接上）。"""
    from companion.memory import paper_memory
    pid = "cite-bl2"
    paper_memory.save_many(pid, [dict(PHISH, id="p1")], status="recommended")
    stub_gloss(monkeypatch, [{"i": 0, "k": "phishing graph accuracy 92 urbuna"}])
    r = client_isolated.post("/companion/cite/context",
                             json={"project_id": pid, "claim": ZH, "paper_index": 1})
    body = r.json()
    assert body["bilingual"] is True and body["found"] is True
    assert body["spans"][0]["text"] in PHISH["abstract"]


@pytest.fixture()
def client_isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(db_module, "DB_PATH", tmp_path / "cite.db")
    db_module._CONN = None
    from leader import app
    from fastapi.testclient import TestClient
    return TestClient(app)
