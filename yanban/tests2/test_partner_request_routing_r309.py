# -*- coding: utf-8 -*-
"""轮 309（队长交接文档验收③④）：外部一次性请求的两条实测缺陷。

根因来自诊断件 `code/yanban/_diag_r309_topic.py`（import 产品真函数，不重打实现），证据
`输出/证据/轮309/业务复现.log`：
  A 路由对**整段**文本做关键词子串匹配 ⇒「……不要生成综述模板。」里的「综述」把检索请求推进了综述支；
  B 剥词通道吃**整段多分句**请求 ⇒ 剥完残留中间标点，「中间不带标点」那道屏必然否掉它 ⇒
    主题明明说清楚了，调用方却收到「请补充研究主题」。
句子用的是队长交接文档里那句业务验收原话，不是设想出来的形状。
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
    spec = importlib.util.spec_from_file_location("lp_under_test_r309", str(path))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


lp = _load_lp()
from acps_sdk.aip import TextDataItem  # noqa: E402
from acps_sdk.aip.aip_base_model import TaskCommand  # noqa: E402

# 队长文档「业务验收」那句，逐字
CAPTAIN = ("检索钓鱼网页检测相关的 3 篇真实论文，列出题名、作者、年份、DOI 或原文链接，"
           "不要生成综述模板。")


def _cmd(text: str) -> TaskCommand:
    return TaskCommand(type="task-command", id="c-" + uuid.uuid4().hex[:8],
                       sentAt=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                       senderRole="leader", senderId="1.2.156.3088.1.1.LDR001.LDR001.1.0002",
                       command="start", taskId="t-" + uuid.uuid4().hex[:8],
                       dataItems=[TextDataItem(text=text)])


@pytest.fixture()
def branches(monkeypatch):
    """两条能力各一枚桩，返回它们的入参——判「进了哪一支」看的是**谁被调用**，不是回答的措辞。"""
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


def test_negated_review_word_does_not_push_a_search_request_into_the_review_branch(branches):
    got = asyncio.run(lp.on_start(_cmd(CAPTAIN), None))
    assert branches["review"] == [], "根因 A：否定句里的「综述」把检索请求推进了综述支"
    assert branches["search"], "该走真实检索能力"
    assert _product_text(got) == "SEARCH-OUTPUT"


def test_the_request_states_a_topic_so_the_query_is_the_topic_not_a_followup(branches):
    """根因 B：主题在这句里是清楚的 ⇒ 不许反问，且送进检索的那枚必须是干净名词短语。"""
    got = asyncio.run(lp.on_start(_cmd(CAPTAIN), None))
    assert got.status.state.value == "awaiting-completion", got.status.state.value
    topic = branches["search"][0][0]
    assert topic == "钓鱼网页检测", f"检索式该是「钓鱼网页检测」，实收 {topic!r}"
    assert branches["search"][0][1] == 3, "句里写了 3 篇就该按 3 篇取"


def test_group_channel_uses_the_same_routing_as_the_http_one(branches):
    """`_run_capability`（群组 MQ 那条）与 `on_start` 是同一判据的第二个调用点，不能各修一半。"""
    content, skill = asyncio.run(lp._run_capability(CAPTAIN))
    assert skill == "yanban.literature.search", skill
    assert content == "SEARCH-OUTPUT"
    assert branches["review"] == []


def test_a_genuine_review_request_still_takes_the_review_branch(branches):
    """修否定词不许把正向的综述请求一起改掉（削弱判据的负控）。"""
    got = asyncio.run(lp.on_start(_cmd("梳理多智能体协作的综述框架"), None))
    assert branches["review"], "正向综述请求没进综述支"
    assert branches["search"] == []
    assert _product_text(got) == "REVIEW-OUTPUT"


def test_review_request_with_a_negated_secondary_word_still_reviews(branches):
    """同一句里既有否定又有真需求：「不要只给零散论文，梳理 X 的综述框架」该走综述——
    摘掉否定段不能把**另一处**的真综述需求一起抹掉。"""
    asyncio.run(lp.on_start(_cmd("不要只给一串零散论文，梳理多智能体协作的综述框架"), None))
    assert branches["review"], "否定词后面的真综述需求被一起抹掉了"


def test_verb_only_request_still_asks_for_the_topic(branches):
    """没有主题照旧追问——新增的抓取通道不许把动词壳当主题送进检索。"""
    got = asyncio.run(lp.on_start(_cmd("帮我检索一下"), None))
    assert got.status.state.value == "awaiting-input", got.status.state.value
    assert branches["search"] == []
    assert got.status.dataItems[0].text.startswith("请补充研究主题")


def test_search_verb_channel_is_wired_in_the_product_ruler():
    """外部请求的「检索/搜索/查找 X 相关的 N 篇论文」由**产品的尺子**接住（轮 301 定的方向：
    partner 不自成一派）。这里断言的是通道存在且开火，不抄句子结果。"""
    from companion.intent.extractor import capture_topic
    assert capture_topic("检索图神经网络的文献 5 篇") == "图神经网络"
    assert capture_topic("检索不到相关文献怎么办") == ""
