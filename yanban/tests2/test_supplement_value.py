"""N3 量具的判据：补检索的「可救上限」怎么量才算数（先写死，再动 `evidence.py`）。

为什么换尺子：接地率（grounded/cited）在 A/B 里已经饱和——g168 的 15 对有 11 对分子等于分母，
"补检索有没有用"在那把尺上**没有可表达的空间**（轮 158 实测，见 docs/evidence/
g170_n1_channel_and_power_projection.txt 正文 D）。真正的作用面是 `true_gap`：挂了编号、
整池都接不住的那些句子。新尺报的是「这些句子里有多少真能被补进来的论文撑住」，
它是**上限**（这些句子有了材料能不能被接住），不是效果（模型有没有真写好）。

判据（跑前写死）：
  V1 只有 true_gap 进分母：已 grounded、以及 misnumbered（池内别处本来就接得住）都不算救回；
  V2 救回必须用**同一把尺**：同一个 rare 集、同一个 MIN_TOKENS，只重合套话不算救回；
  V3 正对照 = 逐字真摘录造的补充池必须救回；错配对照 = 同等数量、无关主题的池必须救回 0 篇
     （否则这个数只是"池子变大"的机械产物，不是补检索的价值）；
  V4 分母为 0 ⇒ 率是 None 而不是 0（没有分母的率不许出现在结论里）；
  V5 分句与编号解析与 `grounding_report` **同源**：两条各自实现，迟早一个说 18 句、一个说 15 句；
     这条要用一个「120 字之后才出现关键证据」的长句来验——拿截断后的台账文本必判漏。
"""
from __future__ import annotations

import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from companion.literature import evidence  # noqa: E402
from companion.literature.evidence import grounding_report, supplement_value  # noqa: E402


def _paper(title: str, abstract: str = "") -> dict:
    return {"title": title, "abstract": abstract or f"about {title}", "year": 2024, "doi": ""}


# 主池四篇：彼此主题不相干，但都带同一串方法学套话（rare 集必须把套话滤掉）
BOILER = "the proposed deep learning framework experimental results show performance improvement"
POOL = [
    _paper("Clustering web pages by link structure",
           f"link graph clustering groups pages by hyperlink structure. {BOILER}"),
    _paper("Certificate lifetime predicts fraudulent stores",
           f"certificate lifetime and issuer entropy predict fraudulent stores. {BOILER}"),
    _paper("Human factors in security warning dialogs",
           f"users respond to warning dialogs depending on colour and wording. {BOILER}"),
    _paper("Cache timing leaks in shared caches",
           f"cache timing leaks reveal access patterns in shared caches. {BOILER}"),
]
# 补检索才可能带回来的两篇（各撑住一句 true_gap）
EXTRA = [
    _paper("Entropy drift in short lived issuers",
           "entropy drift across short lived issuers exposes freshly registered storefronts "
           "at 0.87 precision on monthly cohorts."),
    _paper("Breadcrumb navigation harms recall",
           "breadcrumb navigation harms recall of risk cues among elderly participants "
           "in a nine minute trail study."),
    # 第三篇故意与主池第 2 篇**同义**：它专门用来抓"把已接住的句子也算进分子"那种实现
    # （拿掉 true_gap 过滤后，这句会被算成救回 ⇒ 植入当场判红；没这篇的话那条植入是打空气）。
    _paper("Duplicate certificate findings",
           "certificate lifetime and issuer entropy predict fraudulent stores across "
           "monthly cohorts at 0.9 accuracy"),
]
UNRELATED = [
    _paper("Crop rotation effects on soil carbon",
           "crop rotation increases soil organic carbon across eighteen field trials."),
    _paper("Quenching rates in liquid argon",
           "quenching factors for nuclear recoils in liquid argon time projection chambers."),
    _paper("Retail pricing elasticity online",
           "online retail price elasticity estimated from six million transactions."),
]

SENT_RESCUED_A = ("entropy drift across short lived issuers exposes freshly registered "
                  "storefronts at 0.87 precision[1]")
SENT_RESCUED_B = "breadcrumb navigation harms recall of risk cues among elderly participants[2]"
SENT_GROUNDED = "certificate lifetime and issuer entropy predict fraudulent stores[2]"
SENT_MISNUMBERED = "cache timing leaks reveal access patterns in shared caches[3]"
BOILER_SENT = "the proposed deep learning framework shows performance improvement[1]"


def _body(*sents: str) -> str:
    """拼正文：每句后面必须带分隔符，否则两句话会被分句器并成**一句**（第一版就栽在这儿）。"""
    return "综述正文。\n\n" + "".join(s + "\n" for s in sents)


def test_v1_only_true_gap_sentences_enter_the_denominator():
    txt = _body(SENT_RESCUED_A, SENT_GROUNDED)
    g = grounding_report(txt, POOL)
    assert (g["grounded"], g["true_gap"]) == (1, 1), f"前提变了：{g['rows']}"
    out = supplement_value(txt, POOL, EXTRA)
    assert out["cited_sentences"] == 2
    assert out["true_gap"] == 1
    assert out["rescuable"] == 1, "已接住的那句不许再算一次功劳"
    assert out["rescue_rate_of_gap"] == pytest.approx(1.0)
    assert out["rescue_rate_of_cited"] == pytest.approx(0.5)


def test_v1_misnumbered_is_not_a_gap_so_it_stays_out_of_the_denominator():
    txt = _body(SENT_MISNUMBERED)
    assert grounding_report(txt, POOL)["misnumbered"] == 1, "前提：这句在池内别处能接住"
    out = supplement_value(txt, POOL, EXTRA)
    assert out["true_gap"] == 0
    assert out["rescuable"] == 0
    assert out["rescue_rate_of_gap"] is None, "分母为 0 时报 None，不许报 0.0"


def test_v2_boilerplate_only_overlap_never_counts_as_rescued():
    """套话重合不算救回：词频集虽然按合并语料重算了，DF 门槛仍须把套话挡住。

    （这一条就是"合并语料"版本的 rare 是否还管用的证明：补进来的那篇**只会重复主池四篇都写的
    方法学套话**，若门槛失效，这句会被判成"补检索救回来了"。）
    """
    txt = _body(BOILER_SENT)
    assert grounding_report(txt, POOL)["true_gap"] == 1
    junk = EXTRA + [_paper(BOILER)]
    out = supplement_value(txt, POOL, junk)
    assert out["rescuable"] == 0, "补进来的论文只会重复套话 ⇒ 这句仍然没依据"


def test_v3_positive_control_rescues_and_mismatch_control_rescues_nothing():
    txt = _body(SENT_RESCUED_A, SENT_RESCUED_B)
    assert grounding_report(txt, POOL)["true_gap"] == 2
    good = supplement_value(txt, POOL, EXTRA)
    assert (good["true_gap"], good["rescuable"]) == (2, 2), f"正对照没救回：{good}"
    bad = supplement_value(txt, POOL, UNRELATED)
    assert (bad["true_gap"], bad["rescuable"]) == (2, 0), \
        f"错配对照也判得出救回 ⇒ 这个数只是池子变大的机械产物：{bad}"


def test_v3_adding_unrelated_papers_does_not_inflate_the_count():
    txt = _body(SENT_RESCUED_A, SENT_RESCUED_B)
    alone = supplement_value(txt, POOL, EXTRA)
    padded = supplement_value(txt, POOL, EXTRA + UNRELATED)
    assert alone["rescuable"] == padded["rescuable"], "多给三篇无关论文就把数抬高了"
    assert padded["extra_n"] == len(EXTRA) + len(UNRELATED)


def test_v4_empty_extra_is_measured_as_zero_rescued_not_no_denominator():
    txt = _body(SENT_RESCUED_A)
    out = supplement_value(txt, POOL, [])
    assert out["true_gap"] == 1 and out["rescuable"] == 0
    assert out["rescue_rate_of_gap"] == pytest.approx(0.0)
    assert out["extra_n"] == 0


def test_v5_rescue_reads_the_full_sentence_not_the_truncated_ledger_text():
    """台账里的句子被截到 120 字；关键证据在 120 字之后时必须仍然判可救。

    这条就是「分句与解析必须同源」的行为证明：若实现图省事拿 rows 里的截断文本再判一次，
    这句会静默判漏——读数照样能看，只是永远偏低。
    """
    tail = "freshly registered storefronts at 0.87 precision on monthly cohorts"
    long_sent = ("this paragraph enumerates many caveats and qualifiers " * 4) + tail + "[1]"
    assert len(long_sent) > 150, f"构造失败，句子不够长：{len(long_sent)}"
    txt = _body(long_sent)
    assert grounding_report(txt, POOL)["true_gap"] == 1, "前提：主池接不住这句"
    assert grounding_report(txt, POOL)["rows"][0]["sentence"].endswith("…") is False
    out = supplement_value(txt, POOL, EXTRA)
    assert out["rescuable"] == 1, (
        f"长句被按截断文本判 ⇒ 静默漏计：{out}\n台账里存的是：{grounding_report(txt, POOL)['rows'][0]['sentence'][:40]}")


def test_v5_brute_force_scan_must_give_the_same_readout_as_the_prefilter():
    """粗筛只是省时间：**不许改变读数**。这里拿同一批数据再做一遍"不加粗筛"的暴力扫，两边必须同数。"""
    from companion.literature.evidence import _cited, best_spans, rare_tokens

    txt = _body(SENT_RESCUED_A, SENT_RESCUED_B, SENT_GROUNDED, SENT_MISNUMBERED, BOILER_SENT)
    got = supplement_value(txt, POOL, EXTRA + UNRELATED)
    rows = grounding_report(txt, POOL)["rows"]
    rare = rare_tokens(list(POOL) + list(EXTRA) + list(UNRELATED))
    brute = 0
    for (full, _idxs), r in zip(_cited(txt), rows):
        if r["status"] != "true_gap":
            continue
        if any(best_spans(full, p, rare=rare) for p in EXTRA + UNRELATED):
            brute += 1
    assert got["rescuable"] == brute > 0, \
        f"粗筛改变了读数或两边都是 0（读不出方向）：{got['rescuable']} vs {brute}"


def test_v5_headline_numbers_come_from_the_same_rows_as_grounding_report():
    txt = _body(SENT_RESCUED_A, SENT_GROUNDED, SENT_MISNUMBERED, BOILER_SENT)
    g, s = grounding_report(txt, POOL), supplement_value(txt, POOL, EXTRA)
    for key in ("cited_sentences", "grounded", "misnumbered", "true_gap"):
        assert s[key] == g[key], f"{key} 两边不一致：{s[key]} vs {g[key]} ⇒ 分句逻辑分叉了"
    assert sum(g["supports"]) == g["grounded"] + g["misnumbered"]


def test_v6_rescuable_is_NOT_monotone_in_the_candidate_set():
    """**多给论文会让救回数从 1 掉到 0** —— 这条不是 bug，是把"天花板"这个词的边界钉住。

    `supplement_value` 的 `rare_tokens` 按**合并语料**重算（不合并的话补充论文独有的词永远不在
    表里，上限恒 0，那是尺子的错）。合并带来一个反直觉后果：三篇候选**共用同一串判别词**时，
    这串词的文档频率会越过 `BOILER_MIN_DOCS` 与 `RARE_DF_RATIO` 两道线，被判成方法学套话滤掉，
    于是那句话的 want 集合变空 ⇒ 从"救回"掉成"不计"。

    为什么值得单独一条用例：轮 159/160 把这台量具的读数称作「上限/天花板」，并据此在仪器里写了
    「并集是超集 ⇒ 天花板 ≥ 每个变体」这种**当定理用**的判据。轮 161 实测到线上读数里就有反例
    （`b99bc2d465` 那行：整张表那份候选救回 0 句，而一份小得多的并集救回 1 句）⇒
    「上限」只能读成**该场候选集上的可救回数**，不是所有候选集的上界，两侧候选集不同就不许对比。
    行为一字不改（改它会作废轮 159/160 的读数），改的是措辞与这条钉住它的用例。
    """
    carrier = "quorum drift exposes storefront ledger gaps"
    pool = [_paper(f"Pool topic {i}", f"pool topic {i} studies widget {i} behaviour. {BOILER}")
            for i in range(4)]
    one = _paper("Eps quorum drift", f"{carrier} near the 0.41 quartile.")
    two = _paper("Zeta quorum drift", f"{carrier} beyond the 0.62 decile.")
    three = _paper("Eta quorum drift", f"{carrier} inside the 3.5 window.")
    txt = "综述。\n\n" + carrier + "[1]\n"

    base = supplement_value(txt, pool, [])
    assert base["true_gap"] == 1 and base["rescuable"] == 0, "夹具失效：这句本来就该接不住"
    assert supplement_value(txt, pool, [one])["rescuable"] == 1, "夹具失效：单给一篇要能接住"
    assert supplement_value(txt, pool, [one, two])["rescuable"] == 1, \
        "两篇还不够套话档（df 未达 BOILER_MIN_DOCS）⇒ 夹具与常量脱钩了"
    got = supplement_value(txt, pool, [one, two, three])
    assert got["rescuable"] == 0 < 1, (
        f"给到三篇反而丢了救回才是这条要钉的行为（现在读出 {got['rescuable']}）⇒ "
        "要么 rare 阈值常量变了，要么有人把合并语料改回主池——两种都让「天花板」的口径重新漂移")
