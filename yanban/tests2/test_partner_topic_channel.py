# -*- coding: utf-8 -*-
"""轮 301：文献伙伴的主题提取必须走**产品自己的尺子**，不再拿剥词正则的整句残片去检索。

判线来自实测（`输出/_logs/r301_a4b_topic_dist.log`、`r301_a5_topic_v2.log`、`r301_a7_llm_census.log`）：
  · 现尺（剥词正则单独定夺）在 1403 条真实 user 行上 100% 非空——把"我编程还行但没跑过模型"整句当主题送去检索；
  · 直接换成 `capture_topic` 会掐掉 61.4% ⇒ 已被同一批实测否掉，别当修法；
  · 上线形状：剥词候选（须过 `_topic_ok`）→ `capture_topic` → 产品的 LLM 兜底通道。
    普查 12 条含真请求词的候选里 LLM 救回 10，剩 2 条**已知**救不回——钉在下面，不许读成"没有误伤"。
"""
from __future__ import annotations

import asyncio
import pathlib
import sys
import time
import uuid

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

import importlib.util

# 🔴 不以 `import literature_partner` 的形式引入：那会把它连同**导入时的 AMP_LOG_DIR**缓存进
#    sys.modules，后面 tests2/test_amp_access_audit.py 用 tmp_path 再取同一份模块就拿到旧的 emitter
#    （本轮全量跑就是这么红的：那条用例断言"自己的 tmp 目录里有 access 日志"）。
#    用独立模块名加载 ⇒ 不碰那个键，谁先跑都不互相污染。
def _load_lp():
    path = pathlib.Path(__file__).resolve().parent.parent / "literature_partner.py"
    spec = importlib.util.spec_from_file_location("lp_under_test_r301", str(path))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


lp = _load_lp()
from acps_sdk.aip import TextDataItem
from acps_sdk.aip.aip_base_model import TaskCommand

RAMBLING = ("我编程还行但没跑过模型", "老板让我三个月内出一篇 SCI，我完全没思路")
KNOWN_MISS = ("按我导师的要求，我下一步该先看哪一类文献？",
              "不查文献也想问清楚：请具体介绍 Cora、CiteSeer、PubMed 这三个引文网络数据集各自的节点数")


def _cmd(text: str) -> TaskCommand:
    return TaskCommand(type="task-command", id="c-" + uuid.uuid4().hex[:8],
                       sentAt=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                       senderRole="leader", senderId="1.2.156.3088.1.1.LDR001.LDR001.1.0002",
                       command="start", taskId="t-" + uuid.uuid4().hex[:8],
                       dataItems=[TextDataItem(text=text)])


def _topic(text: str) -> str:
    return asyncio.run(lp._extract_topic(text))


class Spy:
    def __init__(self):
        self.args = []

    async def __call__(self, topic, limit, year_range=None):
        self.args.append((topic, limit))
        return "SPY-OUTPUT"

    def called_topic(self):
        assert self.args, "检索根本没被调用"
        return self.args[0][0]


@pytest.fixture()
def clean(monkeypatch):
    """规则两通道能定的事不许偷偷花配额：默认让 LLM 兜底通道**炸**，检索换成 spy。"""
    def boom(t):
        raise AssertionError("这条用例里不该调用 llm_topic（规则两通道就该定）")
    assert not hasattr(lp, "llm_topic"), "造主题通道不该被接回来"
    sp = Spy()
    monkeypatch.setattr(lp, "_search", sp)
    return sp


def test_rules_decide_without_llm_and_query_is_the_topic_not_the_sentence(clean):
    got = asyncio.run(lp.on_start(_cmd("检索多智能体协作方向的文献"), None))
    assert clean.called_topic() == "多智能体协作"
    assert got.status.state.value == "awaiting-completion"


def test_capability_question_asks_for_topic_instead_of_searching(monkeypatch, clean):
    got = asyncio.run(lp.on_start(_cmd("只回一句：你现在能正常工作吗？不要检索。"), None))
    assert got.status.state.value == "awaiting-input"
    assert clean.args == []
    assert "请补充研究主题" in got.status.dataItems[0].text


def test_hanging_sentence_is_never_used_as_query(monkeypatch):
    for raw in RAMBLING:
        assert _topic(raw) == "", raw


def test_extract_topic_prefers_product_rule_channel(monkeypatch):
    monkeypatch.setattr(lp, "capture_topic", lambda t: "脑影像分割")
    assert _topic("随便说点什么") == "脑影像分割"


def test_stripped_candidate_is_accepted_only_when_product_predicate_approves(monkeypatch):
    monkeypatch.setattr(lp, "capture_topic", lambda t: "")
    assert _topic("检索多智能体协作方向的文献") == "多智能体协作"
    assert _topic("我编程还行但没跑过模型") == ""


def test_known_cost_is_pinned_not_washed_away(monkeypatch):
    """普查里 LLM 也救不回的两条：必须走「请补充主题」，**不许**退回整句检索。"""
    for raw in KNOWN_MISS:
        assert _topic(raw) == "", raw


def test_partner_wires_the_product_ruler_and_no_fabrication_channel():
    """接产品的尺子，且**不接** LLM 造主题通道（宁缺毋造，理由写在 literature_partner 的注释里）。"""
    src = (pathlib.Path(__file__).resolve().parent.parent / "literature_partner.py").read_text(encoding="utf-8")
    for needle in ("from companion.intent.extractor import", "capture_topic", "_topic_ok"):
        assert needle in src, "partner 的主题提取没接产品的尺子（第二把尺还活着）：%s" % needle
    assert "import llm_topic" not in src and "llm_topic(" not in src,         "partner 又接上了造主题通道：外部调用方会拿到被编出来的主题 + 一列论文"

def test_shortest_valid_candidate_wins(monkeypatch):
    """两条规则通道都过筛时取最短：带动词的「检索多智能体协作」不如「多智能体协作」。"""
    monkeypatch.setattr(lp, "capture_topic", lambda t: "检索多智能体协作")
    monkeypatch.setattr(lp, "_strip_candidate", lambda t: "多智能体协作")
    assert _topic("检索多智能体协作方向的文献") == "多智能体协作"


def test_comma_residue_is_rejected_even_if_length_is_ok(monkeypatch):
    """线上 A2 实测的形状：剥词给出「多智能体协作，给」——长度/代词都过，但中间带标点 ⇒ 不许当主题。"""
    monkeypatch.setattr(lp, "capture_topic", lambda t: "")
    monkeypatch.setattr(lp, "_strip_candidate", lambda t: "多智能体协作，给")
    assert _topic("检索多智能体协作方向的文献，给 5 篇") == ""


def test_leading_punctuation_no_longer_bounces_a_normal_request(monkeypatch, clean):
    """轮 307 线上实测的形状：`检索：RAG 与 Agent 方向的文献 5 篇` 与 `检索：图神经网络的文献`
    都是正常请求，剥词通道留下 `：…` 后被「不带标点」那道屏整枚否掉 ⇒ 第一次调用只收到
    「请补充研究主题」。修的是**两头**的标点，中间那道（`test_comma_residue_…`）一字未松。"""
    got = asyncio.run(lp.on_start(_cmd("检索：RAG 与 Agent 方向的文献 5 篇"), None))
    assert got.status.state.value == "awaiting-completion", got.status.state.value
    topic = clean.called_topic()
    assert topic == "RAG 与 Agent", f"主题该是修掉两头标点后那枚：{topic!r}"

    clean.args.clear()
    got2 = asyncio.run(lp.on_start(_cmd("检索：图神经网络的文献"), None))
    assert got2.status.state.value == "awaiting-completion", got2.status.state.value
    assert "：" not in clean.called_topic()


def test_near_miss_echoes_the_candidate_and_still_does_not_search(monkeypatch, clean):
    """该回弹的照旧回弹（宁缺毋造没被削弱），但**要说清**收到的是什么、卡在哪条尺上。"""
    got = asyncio.run(lp.on_start(
        _cmd("综述：多模态大模型在科研文献理解里的应用脉络 3 篇"), None))
    assert got.status.state.value == "awaiting-input", got.status.state.value
    assert clean.args == [], "回弹那一支不许把残片送去检索（未决 64(a) 的根因）"
    text = got.status.dataItems[0].text
    assert text.startswith("请补充研究主题"), f"轮 301 立的契约句不许换：{text[:40]}"
    assert "科研" in text and "尺子" in text, text[:120]


def test_a_full_sentence_is_never_echoed_as_a_candidate_topic(monkeypatch, clean):
    """轮 307 第一版的错：把「只回一句：你现在能正常工作吗？不要」当成"我替你收的主题"印回调用方脸上。
    整句（带疑问/指代或超 20 字）一律不回显，退回那句干净的建议。"""
    got = asyncio.run(lp.on_start(_cmd("只回一句：你现在能正常工作吗？不要检索。"), None))
    text = got.status.dataItems[0].text
    assert "我把你说的收成" not in text, text[:120]
    assert text.startswith("请补充研究主题"), text[:60]


def test_trim_edges_only_touches_the_edges():
    assert lp._trim_edges("：RAG 与 Agent。") == "RAG 与 Agent"
    assert lp._trim_edges("多智能体协作，给") == "多智能体协作，给"   # 中间那道不归它管
    assert lp._trim_edges("") == ""


def test_punctuation_only_input_yields_no_topic(monkeypatch, clean):
    """全是标点＝没有主题，宁可反问也不许把标点串当检索式发出去。"""
    assert _topic("：，。、！") == ""
