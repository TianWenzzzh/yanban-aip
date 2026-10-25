# -*- coding: utf-8 -*-
"""轮 312（队长任务书 §1.2「年份、篇数、来源等条件」＋ §4.1「先写清关键筛选条件」＋ §3.3「宁缺毋造」）：
叮当入口这一支以前**只把年份词从主题里剥掉**（`literature_partner.py` 的 `_strip_candidate`），
剥完就没人管年份了——能力对照清单 §2 那行写的就是这条（本地有严格过滤 `engine.py:436-438`，伙伴无）。
用户说「近三年」，拿回 2019 年的条目，产物里也没说这一轮到底按哪年筛过。

判据全部打在**产品那把尺**上：年份表用 `companion/intent/extractor.py:_YEAR_HINTS`（网页侧 `extract_update`
吃的同一枚），不在伙伴层另抄一份年份口径（§1.3：不要复制一套长期分叉的逻辑）。

一台只钉一件事：
  Y1 年份窗解析（有／没有年份词各是什么）；
  Y2 检索层按窗滤除，且**在脸上写明**窗是哪两年、滤掉几颗；
  Y3 反向对照：没给窗时行为逐字不变（不许把过滤器做成恒开的）；
  Y4 窗内一条都没有 ⇒ 如实报 0 篇并说明是**年份**造成的，不许拿窗外的条目凑数，
     也不许套用"离题"那句解释（那是另一件事）；
  Y5/Y6 伙伴入口把窗**接到检索调用上**（解析对了但没往下传＝还是没过滤），
     并且年份词不许混进检索主题。
"""
from __future__ import annotations

import asyncio
import importlib.util
import pathlib
import sys
import time
import uuid

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT))

import sources                                              # noqa: E402
from acps_sdk.aip import TextDataItem                       # noqa: E402
from acps_sdk.aip.aip_base_model import TaskCommand         # noqa: E402
from companion.intent.extractor import year_range           # noqa: E402


def _load_lp():
    spec = importlib.util.spec_from_file_location("lp_under_test_r312", str(ROOT / "literature_partner.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


lp = _load_lp()

TOPIC = "phishing webpage detection"
IN_2025 = {"title": "Phishing webpage detection with deep features", "authors": ["A. One"],
           "year": 2025, "venue": "IEEE Access", "doi": "10.1000/in2025", "citations": 4,
           "abstract": "detect phishing webpage"}
OUT_2019 = {"title": "Phishing webpage detection using URL n-grams", "authors": ["B. Two"],
            "year": 2019, "venue": "IEEE Access", "doi": "10.1000/out2019", "citations": 90,
            "abstract": "detect phishing webpage"}
OUT_2021 = {"title": "Webpage phishing detection at scale", "authors": ["C. Three"],
            "year": 2021, "venue": "ACM TOPS", "doi": "10.1000/out2021", "citations": 12,
            "abstract": "phishing webpage"}
AX_IN = {"title": "Phishing webpage detection revisited", "authors": ["D. Four"],
         "year": "2024", "venue": "arXiv", "doi": "arXiv:2404.00001", "citations": None}
AX_OUT = {"title": "Early phishing webpage detection", "authors": ["E. Five"],
          "year": "2017", "venue": "arXiv", "doi": "arXiv:1701.00002", "citations": None}


def _cmd(text: str, task_id: str | None = None) -> TaskCommand:
    return TaskCommand(**{
        "type": "task-command", "id": "c-" + uuid.uuid4().hex[:8],
        "sentAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "senderRole": "leader", "senderId": "1.2.156.3088.1.1.LDR001.LDR001.1.0002",
        "command": "start", "taskId": task_id or ("t-" + uuid.uuid4().hex[:8]),
        "dataItems": [TextDataItem(text=text)], "sessionId": "s-r312"})


class TwoSources:
    """桩掉两路源（用例不许连外网，也不许花英译那次模型配额）。"""

    def __init__(self, crossref, arxiv):
        self.cr, self.ax = crossref, arxiv

    async def _cr(self, topic, limit):
        return list(self.cr)

    async def _ax(self, topic, limit):
        return list(self.ax)


def _run(monkeypatch, crossref, arxiv, **kw) -> str:
    stub = TwoSources(crossref, arxiv)
    monkeypatch.setattr(sources, "crossref_search", stub._cr)
    monkeypatch.setattr(sources, "arxiv_search_raw", stub._ax)

    async def no_translate(q):                      # 拉丁主题本来就不该调英译，这里把它钉成"调了就红"
        raise AssertionError("这一发不该花英译的模型配额")
    monkeypatch.setattr(sources, "_english_query", no_translate)
    return asyncio.run(sources.multi_source_search(TOPIC, limit=kw.pop("limit", 3),
                                                   budget_s=None, **kw))


# ── Y1：年份窗解析走产品那张表 ────────────────────────────────────────────────
# 期望值按**现年**推（轮 312 之后 `year_range` 是相对窗）：把 2026 写死进用例＝一枚会自腐的对照，
# 到 2027 年它会红在产品之外。窗宽（3／5／2 年偏移）由 `test_year_window_relative_r312.py` 钉死。
_TODAY = time.strftime("%Y")


def test_Y1_year_window_parsed_from_product_table():
    y = int(_TODAY)
    assert year_range("检索钓鱼网页检测近三年的论文 3 篇") == (y - 3, y)
    assert year_range("近五年的多智能体协作文献") == (y - 5, y)
    assert year_range("想看近两年的综述") == (y - 2, y)
    assert year_range("检索多智能体协作方向的文献 5 篇") is None      # 没说年份就别凭空加窗


# ── Y2：检索层按窗滤除，并把窗与滤除数写在脸上 ────────────────────────────────
def test_Y2_out_of_window_entries_are_dropped_and_the_window_is_stated(monkeypatch):
    out = _run(monkeypatch, crossref=[IN_2025, OUT_2019, OUT_2021], arxiv=[AX_IN],
               year_range=(2023, 2026))
    assert "Phishing webpage detection with deep features" in out, f"窗内的条目被弄丢了：{out[:400]}"
    assert "Phishing webpage detection revisited" in out, f"arXiv 窗内条目被弄丢了：{out[:400]}"
    for gone in ("URL n-grams", "at scale"):
        assert gone not in out, f"窗外条目还在推荐列表里（{gone}）：{out[:400]}"
    assert "2023" in out and "2026" in out, f"产物没写明这一轮按哪两年筛（§4.1 关键筛选条件）：{out[:400]}"
    assert "年份" in out, f"没写明年份窗滤掉了几颗：{out[:400]}"


# ── Y3：反向对照——没给窗时不许有任何年份过滤 ─────────────────────────────────
def test_Y3_without_a_window_nothing_is_year_filtered(monkeypatch):
    out = _run(monkeypatch, crossref=[IN_2025, OUT_2019], arxiv=[AX_OUT])
    assert "URL n-grams" in out, f"没要求年份却出现了年份过滤（过滤器做成了恒开的）：{out[:400]}"
    assert "Early phishing webpage detection" in out, f"arXiv 的 2017 条目被无端滤掉：{out[:400]}"
    assert "年份" not in out, f"没要求年份却在产物里报了年份口径：{out[:400]}"


# ── Y4：窗内一条都没有 ⇒ 如实说 0 篇，且原因是年份不是"离题" ──────────────────
def test_Y4_window_with_no_hits_says_zero_because_of_year_not_relevance(monkeypatch):
    out = _run(monkeypatch, crossref=[IN_2025], arxiv=[AX_IN], year_range=(2030, 2031), limit=3)
    assert "deep features" not in out and "revisited" not in out, f"窗外条目被拿来凑数：{out[:400]}"
    assert "0 篇" in out, f"没如实报篇数缺口：{out[:400]}"
    assert "2030" in out, f"没说是哪一年的窗造成的空：{out[:400]}"
    assert "只沾着个别通用词" not in out, f"把年份造成的空档说成「离题/只沾通用词」，那是另一件事：{out[:400]}"


# ── Y5/Y6：伙伴入口把窗接到检索调用上，年份词不污染主题 ───────────────────────
def _capture_search(monkeypatch):
    seen: list[dict] = []

    async def fake_search(topic: str, limit: int, year_range=None) -> str:
        seen.append({"topic": topic, "limit": limit, "year_range": year_range})
        return f"主题「{topic}」的结果"
    monkeypatch.setattr(lp, "_search", fake_search)
    return seen


def test_Y5_the_partner_entry_passes_the_window_down_to_retrieval(monkeypatch):
    seen = _capture_search(monkeypatch)
    got = asyncio.run(lp.on_start(_cmd("检索钓鱼网页检测近三年的论文 2 篇", task_id="t-r312-y5a"), None))
    assert got.status.state in ("awaiting-completion", "completed"), got.status.state
    assert len(seen) == 1, f"这一发没跑检索：{seen}"
    assert seen[0]["year_range"] == (int(_TODAY) - 3, int(_TODAY)), \
        f"年份窗没接到检索上（解析对了但没传＝还是没过滤）：{seen}"


def test_Y6_no_year_words_means_no_window_and_no_topic_pollution(monkeypatch):
    seen = _capture_search(monkeypatch)
    asyncio.run(lp.on_start(_cmd("检索脉冲神经网络低功耗编码的论文 2 篇", task_id="t-r312-y6a"), None))
    assert len(seen) == 1, seen
    assert seen[0]["year_range"] is None, f"没说年份却带了窗：{seen}"
    assert "年" not in seen[0]["topic"], f"年份词混进检索式了：{seen}"
