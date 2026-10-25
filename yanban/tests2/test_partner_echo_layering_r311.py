# -*- coding: utf-8 -*-
"""轮 311（队长补充指示 ①）：回声分层靠字面锚点，所以「用户恰好也引用了我们那句话」这一族必须逐形状钉住。

实测链（`输出/_scratch/r311_p14_measure_quoted_echo.py` 的 E1/E2/E5/E6，本文件从它派生判据）：
`_user_span` 原来把锚点之后一路摘到 `。；）`——用户把我们那句话**括在自己引号里**引述时，
这一刀会连他后半句的真需求一起摘掉（E1/E2 摘完只剩 2—6 个字，主题变空 ⇒ 该检索的被反问）。
JSON 包装那发（E5）更糟：信封字面 `{"text` 自己成了"主题"。
⇒ 分层不能只看"我们的话在不在"，还要看**是谁的引号包着它**。

一台只钉一件事：
  L1/L2 用户用「」与“”引述我们的兜底句 ⇒ 引号内照旧摘掉，**后半句必须留下并成为主题**。
  L3 引述形状不吞真需求（结构性：摘完的 span 里必须还看得见用户给的词）。
  L4 JSON 信封不许被当主题（信封字面既不是名词短语，就该整条屏掉，然后取到里面的真词）。
  L5 部分回声（只带「主题的尺子」半句）⇒ 用户那半句的真实主题要能选出来。
  L6 幂等：带引号的回声摘一遍和摘两遍结果一样（重复执行不逐次吞内容）。
  L7 回归保护：我们自己的整段提示（未被引号包住的常规回声）照旧整段摘除，提示里的示例不参与竞争。
"""
from __future__ import annotations

import asyncio
import importlib.util
import pathlib
import sys

pytest_path = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(pytest_path))


def _load_lp():
    path = pytest_path / "literature_partner.py"
    spec = importlib.util.spec_from_file_location("lp_under_test_r311b", str(path))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


lp = _load_lp()

OLD_HINT = ("请补充研究主题：一个名词短语就行，例如「多智能体协作」「RAG 综述框架」。"
            "（主题的尺子：长度 2—20 字）")


def topic_of(text: str) -> str:
    return asyncio.run(lp._extract_topic(text))


# ── L1/L2：用户引述我们的话，不是我们的话──引号是他的，后半句也是他的 ──────────────
def test_user_quoting_our_hint_with_corner_brackets_keeps_their_own_request():
    got = topic_of("按「请补充研究主题」那句里说的名词短语，查多智能体协作的论文")
    assert "多智能体协作" in got, f"引号里引述我们的话，结果把用户后半句一起摘了：topic={got!r}"


def test_user_quoting_our_hint_with_curly_quotes_keeps_their_own_request():
    got = topic_of('你上一轮说“请补充研究主题”，我现在要的是钓鱼网站的论文 3 篇')
    assert "钓鱼网站" in got, f"弯引号那支同样不许吞掉真需求：topic={got!r}"


# ── L3：结构性下限——摘完的 span 里必须还留着用户给的词 ────────────────────────────
def test_echo_cut_never_swallows_the_tail_of_a_quoted_reference():
    span = lp._user_span("按「请补充研究主题」那句里说的名词短语，查多智能体协作的论文")
    assert "多智能体协作" in span, f"span 里没有用户的词＝那一刀砍过头：{span!r}"
    assert "请补充研究主题" not in span, "引号里引述我们的话照旧要摘掉"


# ── L4：JSON 信封 ────────────────────────────────────────────────────────────────
def test_json_wrapped_echo_does_not_make_the_envelope_the_topic():
    text = '{"text": "%s 钓鱼网站"}' % OLD_HINT[:60]
    got = topic_of(text)
    assert "{" not in got and '"' not in got and "text" not in got, f"信封字面被当成了主题：{got!r}"
    assert "RAG" not in got and got != "多智能体协作", f"我们提示里的示例参与了竞争（队长 §2.2）：{got!r}"
    # 认得出用户的词就选它，认不出就**保守澄清**——这两种都合规；拿信封或示例去检索都不合规
    assert got in ("", "钓鱼网站"), f"既不是用户的词也不是空：{got!r}"


# ── L5：部分回声 ─────────────────────────────────────────────────────────────────
def test_partial_echo_leaves_the_users_real_topic():
    got = topic_of("（主题的尺子：长度 2—20 字）我要查脉冲神经网络低功耗编码")
    assert got == "脉冲神经网络低功耗编码", f"部分回声摘完，用户那半句要能选出真主题：{got!r}"


# ── L6：幂等 ─────────────────────────────────────────────────────────────────────
def test_quoted_echo_isolation_is_idempotent():
    text = '按「请补充研究主题」那句里说的名词短语，查多智能体协作的论文'
    once = lp._user_span(text)
    twice = lp._user_span(once)
    assert twice == once, f"重复执行还在吞内容（队长 §2.4 幂等）：{once!r} → {twice!r}"


# ── L7：常规（未被引用的）整段回声照旧摘干净，示例不参与竞争 ────────────────────────
def test_unquoted_full_echo_is_still_removed_whole_and_its_examples_do_not_compete():
    span = lp._user_span(OLD_HINT + "\n" + "检索多智能体协作方向的文献 5 篇")
    for frag in ("请补充研究主题", "例如", "主题的尺子", "RAG 综述框架"):
        assert frag not in span, f"整段回声里还剩 {frag!r}：{span!r}"
    assert topic_of(OLD_HINT + "\n" + "检索多智能体协作方向的文献 5 篇") == "多智能体协作"
