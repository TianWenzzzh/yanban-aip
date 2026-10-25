# -*- coding: utf-8 -*-
"""轮 310（平台侧应答固定提纲）：G1 对外文案零触发词 / G2 引号内示例不参与路由 / G3 取数优先。

真实成因（线上实测，`输出/_logs/r310_p0_diag_calls.log` 与 `r310_p0_read_diag.log`）：
外部网关把我们那句兜底提示**原样并回**下一轮请求 ⇒ 里面的「RAG 综述框架」被当成"这句话在要综述"，
一次本来要可核验论文的检索请求进了 GLM 综述支（产物 0 条 DOI）。同窗口里自家网页端那 4 种
形状的入站（正文 45—69 字节）全部走检索支 ⇒ 差异只在"外部请求里混进了我们自己的话"。

每台只钉一件事，红了才知道是哪一条松了：
  G1 产品**返回给调用方**的文案里不许有 REVIEW_KEYWORDS 的任何成员字（含超时/失败提示）。
  G2 「」『』“” 包住的示例不参与路由；关键词表本身一字不许动（反向钉住"不许删表"）。
  G3 同一句里既有取数要求又有提纲词 ⇒ 检索优先；只有纯提纲措辞才走综述支。
  G4 群组那条通道与 HTTP 那条用同一判据，不许只修一半。
"""
from __future__ import annotations

import asyncio
import importlib.util
import pathlib
import sys
import time
import uuid

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))


def _load_lp():
    path = pathlib.Path(__file__).resolve().parent.parent / "literature_partner.py"
    spec = importlib.util.spec_from_file_location("lp_under_test_r310", str(path))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


lp = _load_lp()
from acps_sdk.aip import TextDataItem  # noqa: E402
from acps_sdk.aip.aip_base_model import TaskCommand  # noqa: E402

KEYWORDS = lp.REVIEW_KEYWORDS

# 轮 310 之前那句 A 支兜底文案里的示例（旧字面，测试夹具专用：产品代码里已不许再出现它）。
LEGACY_ECHO = ("请补充研究主题：一个名词短语就行，例如「多智能体协作」「RAG 综述框架」。"
               "（主题的尺子：长度 2—20 字）")
QUERY = "检索多智能体协作方向的文献 5 篇"


def _cmd(text: str) -> TaskCommand:
    return TaskCommand(type="task-command", id="c-" + uuid.uuid4().hex[:8],
                       sentAt=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                       senderRole="leader", senderId="1.2.156.3088.1.1.LDR001.LDR001.1.0002",
                       command="start", taskId="t-" + uuid.uuid4().hex[:8],
                       dataItems=[TextDataItem(text=text)])


def _hits(text: str) -> list:
    return [k for k in KEYWORDS if k in text]


@pytest.fixture()
def branches(monkeypatch):
    called: dict[str, list] = {"search": [], "review": []}

    async def fake_search(topic, limit, year_range=None):
        called["search"].append((topic, limit))
        return "SEARCH-OUTPUT"

    async def fake_review(topic, purpose):
        called["review"].append((topic, purpose))
        return "REVIEW-OUTPUT"

    monkeypatch.setattr(lp, "_search", fake_search)
    monkeypatch.setattr(lp, "_glm_review", fake_review)
    return called


def _product_text(got) -> str:
    return got.products[0].dataItems[0].text


# ───────────────────────── G1：对外文案零触发词 ─────────────────────────
def test_hint_branch_a_carries_no_routing_keyword_and_keeps_the_contract_sentence():
    """:168 那支（抽不出候选主题时）——契约句开头必须还在，且一个字都不参与路由。"""
    got = lp._topic_hint("")
    assert got.startswith("请补充研究主题"), "轮 301 立下的对外契约句开头不许换"
    assert _hits(got) == [], "兜底提示里嵌着路由触发词＝给自激回路留燃料"


def test_hint_branch_b_carries_no_routing_keyword():
    got = lp._topic_hint("科研文献方向")
    assert got.startswith("请补充研究主题")
    assert "我把你说的收成" in got, "这支没被走到＝这条用例在重复测 A 支，B 支没覆盖"
    assert _hits(got) == []


def test_budget_timeout_message_carries_no_routing_keyword():
    """超时提示也会原样回给调用方，同样不许带触发词。"""
    async def slow():
        await asyncio.sleep(0.05)
        return "x"

    with pytest.raises(asyncio.TimeoutError) as exc:
        asyncio.run(lp._await_within(slow(), 0.001, "文献检索"))
    assert _hits(str(exc.value)) == []


# ───────────────────────── G2：引号内的示例不参与路由 ─────────────────────────
def test_keyword_table_itself_is_untouched():
    """反向钉住"窄门"：加窄门不等于换判据——关键词表一字不许动。"""
    assert KEYWORDS == ("综述", "框架", "脉络", "开题", "研究空白", "相关工作")


def test_a_keyword_appearing_only_inside_quotes_does_not_route():
    """"综述"三个字只出现在引号里（在谈这个词本身／在举例子）≠ 在要综述。"""
    assert lp._review_requested("请把「综述」这三个字从我的提纲里去掉") is False


def test_a_real_outside_quote_demand_still_routes():
    """剔除引号内示例之后，引号外的真需求照旧进综述支。"""
    assert lp._review_requested("我的提纲先放着，请先梳理多智能体协作的综述框架") is True


def test_legacy_echo_of_our_own_hint_no_longer_pushes_a_search_request_into_review():
    """旧回声形状（我们那句带「RAG 综述框架」的提示＋检索需求）此后必须走检索支。"""
    assert lp._review_requested(LEGACY_ECHO + "\n" + QUERY) is False


# ───────────────────────── G3：取数动词优先 ─────────────────────────
def test_fetch_demand_wins_when_an_outline_word_is_also_present():
    """同一句里既有取数要求（找＋论文）又有提纲词（脉络）⇒ 走检索，不给死提纲。"""
    assert lp._review_requested("找 5 篇多智能体协作的论文，顺便说说这个领域的脉络") is False


def test_pure_outline_demand_without_fetch_still_reviews():
    assert lp._review_requested("梳理多智能体协作的研究脉络与相关工作") is True


def test_gateway_echo_shape_goes_to_search_on_the_http_channel(branches):
    """线上复现过的那一发（诊断件 r310diag-echo）：修复后必须交回可核验条目那条路。"""
    got = asyncio.run(lp.on_start(_cmd(LEGACY_ECHO + "\n" + QUERY), None))
    assert branches["review"] == [], "回声自激没修掉"
    assert branches["search"], "该走真实检索能力"
    assert _product_text(got) == "SEARCH-OUTPUT"


def test_gateway_echo_shape_goes_to_search_on_the_group_channel(branches):
    """群组 MQ 那条是同一判据的第二个调用点，不许只修 HTTP 那一半。"""
    content, skill = asyncio.run(lp._run_capability(LEGACY_ECHO + "\n" + QUERY))
    assert skill == "yanban.literature.search", skill
    assert content == "SEARCH-OUTPUT"
    assert branches["review"] == []
