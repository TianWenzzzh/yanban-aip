# -*- coding: utf-8 -*-
"""轮 309（队长交接文档验收③的第三格）：外部调用拿到的必须是**与主题相关**的真实论文。

线上实测的根因（`输出/证据/轮309/线上应答原样.json`，2026-10-07 13:2x）：把队长那句发到登记入口，
状态是 `awaiting-completion`、确实走了 `yanban.literature.search`、也确实给了带 DOI 的三条——
但三条是《通信工程网络系统稳定性检测与改进措施》《给排水管网智能化监测与维护研究》《基于物联网的
公路桥梁健康监测与检测技术研究》。**全是「检测」二字沾边的中文建工期刊**，与"钓鱼网页检测"无关。

这与轮 32/33 在线上量到过的形状同源，而产品那侧早就把规矩定死了（`companion/literature/engine.py:370—378`）：
「翻译成功时再把中文原词一起送库，会混进只沾了『检测』二字的无关文献，所以**译成就只用英文式查询**」，
再过一道 `filter_relevant`（标题锚 + 命中数下限，「宁可少推也不硬凑」）。
伙伴这条 `sources.multi_source_search` 是没接这两条尺的**第二条路径**——它直接把中文原词送 Crossref/arXiv。
所以本轮的修法不是发明新判据，而是让伙伴这条走产品同一把尺（轮 301 定的方向）。
"""
from __future__ import annotations

import asyncio
import importlib
import pathlib
import re
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

sources = importlib.import_module("sources")

TOPIC_CJK = "钓鱼网页检测"
EN_QUERY = "phishing webpage detection"
OFF_TOPIC = [
    {"title": "通信工程网络系统稳定性检测与改进措施", "authors": ["军民 葛"], "year": 2025,
     "venue": "建筑监督检测与造价", "doi": "10.61654/a", "citations": 0, "abstract": "网络系统稳定性检测…"},
    {"title": "给排水管网智能化监测与维护研究", "authors": ["晓晨 王"], "year": 2025,
     "venue": "建筑监督检测与造价", "doi": "10.61654/b", "citations": 0, "abstract": "管网监测…"},
]
ON_TOPIC = [
    {"title": "Phishing webpage detection using visual and URL features", "authors": ["A B"],
     "year": 2023, "venue": "IEEE Access", "doi": "10.1109/x", "citations": 3,
     "abstract": "we detect phishing webpages"},
]


class Rec:
    """记下两路各自收到什么查询、返回什么条目（桩＝不让用例真连外网）。"""

    def __init__(self, crossref, arxiv):
        self.cr_items, self.ax_items = crossref, arxiv
        self.cr_q = self.ax_q = None

    async def cr(self, topic, limit):
        self.cr_q = topic
        return list(self.cr_items)

    async def ax(self, topic, limit):
        self.ax_q = topic
        return list(self.ax_items)


@pytest.fixture()
def stub(monkeypatch):
    def apply(crossref=OFF_TOPIC, arxiv=ON_TOPIC, en=EN_QUERY, en_boom=False):
        rec = Rec(crossref, arxiv)
        monkeypatch.setattr(sources, "crossref_search", rec.cr)
        monkeypatch.setattr(sources, "arxiv_search_raw", rec.ax)

        async def _en(q):
            if en_boom:
                raise AssertionError("这条路径不许调用英译")
            return en if en is not None else q
        monkeypatch.setattr(sources, "_english_query", _en)
        return rec
    monkeypatch.apply = apply
    return monkeypatch


def _run(topic: str) -> str:
    return asyncio.run(sources.multi_source_search(topic, limit=3, budget_s=None))


def test_translated_cjk_topic_is_queried_in_english_only(stub):
    """译成了就只用英文式送库——中文原词一起送正是线上那批建工期刊的混源成因。"""
    rec = stub.apply()
    out = _run(TOPIC_CJK)
    assert rec.cr_q == EN_QUERY, f"Crossref 收到的查询不是英译式：{rec.cr_q!r}"
    assert rec.ax_q == EN_QUERY, f"arXiv 收到的查询不是英译式：{rec.ax_q!r}"
    assert TOPIC_CJK not in (rec.cr_q or ""), "中文原词又一起进了英文库"
    assert "phishing" in out.lower(), out[:200]


def test_latin_topic_spends_no_translation_call(stub):
    """拉丁主题不调英译（一次模型调用是真实配额，外部调用方每次都要等它）。"""
    rec = stub.apply(crossref=ON_TOPIC, arxiv=ON_TOPIC, en_boom=True)
    out = _run("multi agent collaboration")
    assert rec.cr_q == "multi agent collaboration"
    assert rec.ax_q == "multi agent collaboration"


def test_off_topic_hits_are_dropped_by_the_product_ruler(stub):
    """只沾「检测」二字的中文建工条目必须被产品的尺滤掉，且**如实说滤掉几颗**。"""
    stub.apply(crossref=OFF_TOPIC, arxiv=ON_TOPIC)
    out = _run(TOPIC_CJK)
    assert "通信工程网络系统稳定性检测" not in out, "离题条目还在推荐列表里（未决 64(a) 那一族）"
    assert "给排水管网智能化监测" not in out
    assert "Phishing webpage detection" in out
    assert "离题" in out and "2" in out, f"没写明滤除了几颗：{out[:300]}"


def test_all_dropped_says_so_instead_of_padding_the_list(stub):
    """两路全被滤光 ⇒ 明说"这一轮没有与主题直接相关的条目"，不许拿别的条目凑数、也不许当成功没发生。"""
    stub.apply(crossref=OFF_TOPIC, arxiv=[])
    out = _run(TOPIC_CJK)
    assert "没有" in out and ("相关" in out), out[:300]
    for junk in ("通信工程网络系统", "给排水管网"):
        assert junk not in out, f"滤光之后又把它捞回来了：{junk}"


def test_translation_failure_is_declared_not_swallowed(stub):
    """英译没生效（MOCK/超时/失败）⇒ 用原词直送可以，但必须**标记出来**（产品口径 `query_fallback` 同义）。"""
    stub.apply(crossref=ON_TOPIC, arxiv=ON_TOPIC, en=TOPIC_CJK)   # 等于没译
    out = _run(TOPIC_CJK)
    assert "英译" in out, f"没写明这一轮的相关性会打折：{out[:240]}"


def test_the_same_paper_under_two_dois_is_listed_once(stub):
    """线上实测的形状（`输出/证据/轮309/线上应答原样_修复后.json`）：Crossref 回来的 3 条里
    第 1、2 条题名、作者、年份、期刊**逐字相同**，只有 DOI 一颗不同（10.30630/… 与 10.62527/…）
    ⇒ 调用方要 3 篇，实际只拿到 2 篇。产品主链路的 `_dedup` 键是「有 DOI 就只按 DOI」，
    这种同篇双号它认不出来，所以这里按**题名归一**再收一道，并把去掉几颗写在脸上。"""
    dup = {**ON_TOPIC[0], "doi": "10.62527/x"}
    stub.apply(crossref=[ON_TOPIC[0], dup, ON_TOPIC[0]], arxiv=[])
    out = _run(TOPIC_CJK)
    assert out.count("Phishing webpage detection using visual") == 1, \
        f"同一篇列了多遍：{out[:400]}"
    assert "重复" in out, f"没写明去掉几颗重复：{out[-260:]}"


def test_overfetch_then_trim_so_the_requested_count_is_honoured(stub):
    """调用方写「3 篇」就要给到 3 篇（够的话）。线上实测第二发只给了 2 篇——根因是每源只取
    `limit//2`（3 篇 ⇒ 每源 1…3 条）再经滤除／去重，池子本来就浅。产品主链路取的是
    `per = max(limit, 6)`（`companion/literature/engine.py`），这里接同一个形状：
    每源多拿一些，两源合并去重后**裁到调用方要的那个数**。"""
    pool = [{"title": f"Phishing webpage detection study number {i}", "authors": ["X Y"],
             "year": 2020 + i, "venue": "IEEE Access", "doi": f"10.1109/p{i}",
             "citations": i, "abstract": "phishing webpage detection"} for i in range(1, 7)]
    rec = stub.apply(crossref=pool, arxiv=[])
    out = asyncio.run(sources.multi_source_search(TOPIC_CJK, limit=3, budget_s=None))
    assert rec.cr_q == EN_QUERY
    n = len(re.findall(r"^\s*\d+\.\s+\*\*(.+?)\*\*", out, re.M))
    assert n == 3, f"要 3 篇却列了 {n} 条：{out[-320:]}"


def test_shortfall_is_stated_when_the_pools_cannot_fill_the_count(stub):
    """池子里相关的条数不够调用方要的那个数 ⇒ 明说「要 N 篇、实际只有 M 篇相关」，
    不静默少给，也不拿离题条目凑数（交接文档：宁可如实的部分结果）。"""
    stub.apply(crossref=ON_TOPIC, arxiv=[])
    out = asyncio.run(sources.multi_source_search(TOPIC_CJK, limit=5, budget_s=None))
    assert "要 5 篇" in out and "1" in out, f"没写明缺口：{out[-300:]}"
