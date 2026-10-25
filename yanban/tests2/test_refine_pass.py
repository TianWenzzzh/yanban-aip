"""P4 回炉的判据：只对机检判不过的句子发子查询，且"救回"必须有逐字摘录。

依据量级（写在这里是为了下次不必重新查）：LiRA 2510.05138 引用 F1 0.63→0.76；
OpenScholar 证据绑定 + 自反馈正确率 +12%；**但**无外部信号的自我批评是负的
（GPT-3.5 CommonSenseQA 75.8→38.1、GPT-4 GSM8K 95.5→89.0）⇒ 回炉必须挂二值检查单
（TICK 2410.03608）。这里用的两类机检都是产品已有的：
① 标题在不在检索结果里；② 这句在不在依据池里找得到支撑（`grounding_report`）。

夹具形状是**同语言**的：`grounding_report` 的支撑判定是字面重合，
中文句子对英文摘要永远零重合 ⇒ 拿"中文句 + 英文新料"做夹具会测到语言闸门而不是回炉机制
（本轮第一版就踩在这上面：补进 p3 之后状态纹丝不动，误以为机制无效）。
"""
from __future__ import annotations

from companion.conversation import refine
from companion.literature.engine import GENERIC_TOKENS

PAPERS = [
    {"id": "p1", "title": "Contrastive multimodal network",
     "abstract": "We fuse URL text and visual features on phishing pages."},
    {"id": "p2", "title": "Survey of graph anomaly detection",
     "abstract": "This survey reviews graph methods for anomaly detection."},
]
# 这句的真依据不在它挂的那篇里（p1 撑不住），而在**补检索才能拿到**的 p3 里
TEXT = "这套方法在 12k 条钓鱼页面上达到 0.94 F1 [1]。这篇综述回顾了图异常检测方法 [2]。"
RESCUER = {"id": "p3", "title": "钓鱼网站检测的工业部署",
           "abstract": "我们在 12k 条钓鱼页面上验证这套方法，达到 0.94 F1 的准确率。"}


def test_claim_queries_use_only_the_sentence_s_own_words():
    s = "这套方法在 12k 条钓鱼页面上达到 0.94 F1 [1]。"
    qs = refine.claim_queries([s, s])
    assert len(qs) == 1, f"同一句不该发两条子查询：{qs}"
    words = [w for w in qs[0].split() if w not in GENERIC_TOKENS]
    assert words and len(words) <= refine.MAX_CLAIM_TERMS, f"词数不在上限内：{words}"
    for w in words:
        assert w in s.lower(), f"子查询里冒出句子之外的词：{w!r}（词表必须来自句子本身）"


def test_span_predicate_rejects_a_made_up_quote():
    """正/负对照各一条：真摘录判 True，编的判 False（这条是整个维度的会咬人处）。"""
    assert refine.span_in_pool("钓鱼页面上验证这套方法", [RESCUER])
    assert not refine.span_in_pool("在 5000 家医院验证这套方法", [RESCUER])
    assert not refine.span_in_pool("", [RESCUER])


def test_refine_rescues_only_what_the_new_evidence_actually_supports():
    before = refine.grounding_counts(TEXT, PAPERS)
    # 两句都判不过：p2 的英文摘要与中文句子没有字面重合（这把尺子按字面算，实测如此）
    assert before["true_gap"] == 2, f"夹具形状变了，这条测试要重新对：{before}"
    r = refine.refine_pass(TEXT, PAPERS, [RESCUER])
    assert r["before_ungrounded"] == 2
    assert r["markers_moved"] == 1, f"没走改角标这一步就不可能救回：{r}"
    assert r["after_ungrounded"] == 1, f"只该救回那一句，另一句仍无支撑：{r['still_ungrounded']}"
    assert len(r["rescued"]) == 1
    assert r["rescued"][0]["span_ok"] is True, "救回却没有逐字摘录 ⇒ 不许记成救回"
    assert "12k" in r["rescued"][0]["sentence"]


def test_refine_does_not_invent_a_rescue_when_nothing_supports_it():
    """负向：补回来的论文跟那句无关 ⇒ 句子照旧判不过，不许因为"多跑了一轮"就变绿。"""
    noise = {"id": "p9", "title": "Deep sea sediment acoustics",
             "abstract": "Acoustic surveys of sediment layers in the Baltic basin."}
    r = refine.refine_pass(TEXT, PAPERS, [noise])
    assert r["before_ungrounded"] == 2 and r["after_ungrounded"] == 2
    assert r["rescued"] == [] and r["markers_moved"] == 0


def test_round_cap_is_one_and_reported():
    assert refine.REFINE_ROUNDS_MAX == 1, "回炉上限写死成 1；抬它需要新的实测，不是顺手改数"
    r = refine.refine_pass(TEXT, PAPERS, [RESCUER])
    assert r["rounds"] <= r["cap"]
