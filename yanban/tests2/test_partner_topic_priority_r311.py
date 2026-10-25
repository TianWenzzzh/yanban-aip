# -*- coding: utf-8 -*-
"""轮 311（队长任务书 §2.1—§2.4、§3.1）：主题与路由的**优先级**判据。

🔴 这份文件被写过两次：第一次落盘后 13 条全部跑绿（`输出/_logs/r311_p4_fivefiles.log` FIVE_RC=0），
   随后的全量里它变成 9719 字节**全是 0xFF**（长度在、内容没了，
   `输出/_logs/r311_p5_full.log` 的三条红全是各完整性门 `read_text` 撞上它）。
   盘会静默吞写入 ⇒ 本仓新写文件一律"落盘后回读 + 数条数"，这份文件的重写就是这么发现的。

起因是实测，不是设计偏好。读数在 `输出/_logs/r311_p1_topic_echo_runB.log`（MEASURE_RC=3，7 条判线红），
逐候选的来源与过筛结果都在那份日志里，本文件每条用例都对得上其中一发形状：

  S2/S3/S4  我们自己的兜底文案被外部网关并回下一轮请求，用户只补了一个词「钓鱼网站」
            ⇒ 实测选出的主题是 `'主题'`（它抠的是**我们那句话里的"研究主题"四个字**）。
  S12       网关改写形状里，候选池同时有 `'主题'`、`'钓鱼网站检测'`、`'钓鱼网站检测相关的 真实'`
            ⇒ 实测选 `'主题'`：真词已经抽到了，是"过筛者取最短"把它扔了。
  S11       「只给 RAG 综述提纲，不要检索论文」⇒ 实测走**检索支**：
            取数优先那道门跑在摘除否定小句**之前**，否定段里的"检索/论文"照样算取数要求。
  S5        「找关于“钓鱼网站检测”的论文」⇒ 实测主题 `'找“钓鱼网站检测”的'`：
            残渣带着取数动词与引号字符进了检索式。

🔴 两条不许越界的边界（队长 §2.3/§2.4 点名）：
  · 把「主题」判成壳词之后，「主题模型」「主题发现」这类**复合真术语**必须照旧可用；
  · 回声隔离**不许**用"把引号里的内容整段删掉"来实现——那会误删用户自己用引号括起来的真实主题。
    所以这里的判据是"选出来的主题等于用户那个词"，不是"引号内容消失"。
"""
from __future__ import annotations

import asyncio
import importlib.util
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))


def _load_lp():
    path = pathlib.Path(__file__).resolve().parent.parent / "literature_partner.py"
    spec = importlib.util.spec_from_file_location("lp_under_test_r311", str(path))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


lp = _load_lp()

USER = "钓鱼网站"

# 轮 309 那版 A 支兜底文案里的示例（旧字面，测试夹具专用：产品代码里已不许再出现它）。
# 与 r310 那份夹具逐字同一串——它仍会从外部回声进来，所以必须继续测。
LEGACY_ECHO = ("请补充研究主题：一个名词短语就行，例如「多智能体协作」「RAG 综述框架」。"
               "（主题的尺子：长度 2—20 字）")


def topic_of(text: str) -> str:
    """走产品自己的通道：`_extract_topic` 是 async，且它就是送进检索那一步的输入。"""
    return asyncio.run(lp._extract_topic(text))


# ─────────────── 泛词「主题」不该过筛（实测 `_usable_topic("主题")=True`）───────────────
def test_bare_word_theme_is_not_a_usable_topic():
    """红＝「主题」重新能当主题：它是我们那句提示里的词，不是用户说的研究内容。

    `_TOPIC_SHELL` 原本只列 `方向|领域|课题|题目`（`companion/intent/extractor.py:142`），
    而 `_topic_ok` 三条判据（长度 2—20、非壳、无指代疑问）它全过 ⇒ 2 个字的它必然在"取最短"里赢。
    """
    assert lp._usable_topic("主题") is False


def test_compound_terms_built_on_theme_stay_usable():
    """反向钉住"别把判据写宽"：「主题模型」「主题发现」是真术语，不许被壳词表一起杀掉。

    队长 §2.3 明确要求的保护面；少了这条，上一条可以靠"凡含主题一律否掉"作弊通过。
    """
    assert lp._usable_topic("主题模型") is True
    assert lp._usable_topic("主题发现") is True


# ─────────────── 真词不许被"取最短"扔掉 ───────────────
def test_gateway_rewritten_text_keeps_the_specific_topic_over_the_generic_word():
    """S12：候选池里同时有 `'主题'` 与 `'钓鱼网站检测'` ⇒ 必须选后者。

    这条只钉**选择规则**（`literature_partner.py` 的 `min(valid, key=len)`），不钉抽取——
    抽取本来就已经给出真词，实测读数里它在候选池中。
    """
    text = ("任务：请补充研究主题，输出为名词短语；"
            "用户需求：检索钓鱼网站检测相关的 3 篇真实论文")
    assert topic_of(text) == "钓鱼网站检测"


# ─────────────── 回声不参与主题竞争（用户只补一个词的那一发）───────────────
def test_one_word_reply_after_our_own_hint_becomes_the_topic():
    """S2：旧兜底文案在前 + 用户补「钓鱼网站」⇒ 主题必须是「钓鱼网站」。

    队长截图那格的形状。修好泛词表还不够：整段里只有我们那句话带得动抽取正则，
    用户那个孤零零的词没有通道命中 ⇒ 这一步真正要求的是**先分层、把回声区间从主题通道摘出去**。
    """
    assert topic_of(LEGACY_ECHO + " " + USER) == USER


def test_one_word_reply_before_our_own_hint_becomes_the_topic():
    """S3：顺序反过来（有的网关把用户输入放在后面）⇒ 同一结论，不许依赖拼接次序。"""
    assert topic_of(USER + " " + LEGACY_ECHO) == USER


def test_one_word_reply_after_the_current_hint_becomes_the_topic():
    """S4：换成**当前**那版兜底文案（轮 310 改过示例词）也必须接住。

    只测旧字面＝把修复绑在一份字符串上；文案再改一次就又失守。
    """
    assert topic_of(lp._topic_hint("科研文献方向") + " " + USER) == USER


def test_examples_inside_our_own_hint_never_become_the_topic():
    """我们提示里举的例子（「多智能体协作」这种**合法名词短语**）不许顶替用户说的词。

    泛词用例的加强面：示例本身过得了尺子，所以"剔掉泛词"救不了它——必须按**来源**分层。
    """
    assert topic_of(lp._topic_hint("") + " " + USER) == USER


def test_killing_one_generic_word_does_not_promote_the_next_one():
    """杀了「主题」之后不许换成人选 `'尺子'`——它同样出自我们那句「（主题的尺子：…）」。

    实测就是这么红的（S2 在壳词表补「主题」之后选出 `'尺子'`）。
    这条存在的全部理由：防着下一轮用"再往表里加一个词"交差。
    """
    got = topic_of(LEGACY_ECHO + " " + USER)
    assert got not in ("主题", "尺子", "名词短语", "例子", "例"), got


def test_echo_isolation_is_idempotent():
    """§2.4 明写的判据：归一化重复执行不许逐次吞掉主题。"""
    once = lp._user_span(LEGACY_ECHO + " " + USER)
    twice = lp._user_span(once)
    assert once == twice, "归一化不幂等：一遍=%r 两遍=%r" % (once, twice)
    assert USER in once, "回声摘除把用户自己的词一起摘掉了：%r" % once
    assert "例如" not in once and "尺子" not in once


# ─────────────── 明确否定"不要检索"不许被拉去检索 ───────────────
def test_explicit_negation_of_retrieval_still_takes_the_outline_branch():
    """S11：「只给 RAG 综述提纲，不要检索论文」⇒ 综述支（队长 §3.1 与用例 05）。

    实测红在 `_review_requested`：取数优先那道门对**原文**查 `_FETCH_VERB`（"检索"）与
    `_DOC_NOUN`（否定段里的"论文"），命中就直接 `return False`，而否定小句要到下一行才被摘掉
    ⇒ 归一化跑在判据之后（轮 310 引入，本轮修）。
    """
    assert lp._review_requested("只给 RAG 综述提纲，不要检索论文") is True


def test_a_real_fetch_demand_next_to_an_outline_word_still_searches():
    """反向边界：收窄那道门之后，真取数要求照旧必须走检索（轮 310 的 G3 不许被改坏）。"""
    assert lp._review_requested("找 5 篇钓鱼网站检测的论文，顺便说说这个领域的脉络") is False


# ─────────────── 用户自己的引号主题必须干净地留下 ───────────────
def test_quoted_topic_reaches_the_query_without_residue():
    """S5：「找关于“钓鱼网站检测”的论文」⇒ 主题是 `钓鱼网站检测`，不带引号字符也不带"找"。

    实测给的是 `'找“钓鱼网站检测”的'`——引号内容**没**被误删（§2.4 的指控方向在这一面上不成立），
    但残渣进了检索式：那是查询质量问题（§3.2），也是相关性打折的来源之一。
    """
    assert topic_of("找关于“钓鱼网站检测”的论文") == "钓鱼网站检测"


def test_fetch_verb_without_a_trigger_word_is_still_cleaned():
    """S1：「找 5 篇多智能体协作论文」⇒ 主题 `多智能体协作`，实测给的是 `'找 多智能体协作'`。

    剥词表里有 `检索|查找|搜索` 却没有 `找`，于是带着空格与动词进 Crossref。
    """
    assert topic_of("找 5 篇多智能体协作论文") == "多智能体协作"
