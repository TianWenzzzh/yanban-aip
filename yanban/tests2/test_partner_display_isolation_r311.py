# -*- coding: utf-8 -*-
"""轮 311 收尾（队长任务书 §5 用例 08/12/13 + §3.3/§4.1 的"链接要能点"）：来源标识、并发隔离、缺字段。

一台只钉一件事，红了就知道是哪一条松了：
  A1 用例 13／§3.3：arXiv 条目**允许没有 DOI**，但必须给出有效 arXiv 标识 + 可点开的链接；
  A2 §4.1：Crossref 条目那行必须带 `https://doi.org/…`（裸 DOI 在移动端不是链接，点不动）；
  B1 用例 08：两个任务并发进来（不同 taskId、不同主题），各自的应答不许串题；
  C1 用例 12：入站**没有 sessionId** 时（协议上 `sessionId` 是可选字段），伙伴这一支要能正常完成，
     不许把两通请求并成一条，也不许半路崩。
A1/A2 打在展示函数 `_fmt` 上（相关性判定、跨源去重、给不满要如实报那些面，
`tests2/test_sources_relevance_r309.py` 已经逐条钉住，这里不重数、不放宽）。
"""
from __future__ import annotations

import asyncio
import importlib.util
import pathlib
import sys
import time
import uuid

import pytest

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT))

import sources                                              # noqa: E402
from acps_sdk.aip import TextDataItem                       # noqa: E402
from acps_sdk.aip.aip_base_model import TaskCommand         # noqa: E402


def _load_lp():
    spec = importlib.util.spec_from_file_location("lp_under_test_r311c", str(ROOT / "literature_partner.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


lp = _load_lp()

ARXIV_HIT = {"title": "Phishing website detection via lightweight CNN", "authors": ["L. Song"],
             "year": "2024", "venue": "arXiv", "doi": "arXiv:2403.01234", "citations": None}
CROSSREF_HIT = {"title": "Phishing detection using machine learning", "authors": ["A. Author"],
                "year": 2023, "venue": "IEEE Access", "doi": "10.1109/ACCESS.2023.000001", "citations": 12}


def _cmd(text: str, task_id: str | None = None, session_id: str | None = None) -> TaskCommand:
    kw = {"type": "task-command", "id": "c-" + uuid.uuid4().hex[:8],
          "sentAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
          "senderRole": "leader", "senderId": "1.2.156.3088.1.1.LDR001.LDR001.1.0002",
          "command": "start", "taskId": task_id or ("t-" + uuid.uuid4().hex[:8]),
          "dataItems": [TextDataItem(text=text)]}
    if session_id is not None:
        kw["sessionId"] = session_id
    return TaskCommand(**kw)


# ── A1/A2：来源标识与可点开的链接 ─────────────────────────────────────────────
def test_A1_arxiv_entry_without_doi_carries_identifier_and_clickable_link():
    got = "\n".join(sources._fmt([dict(ARXIV_HIT)], "arXiv"))
    assert "arXiv:2403.01234" in got, f"arXiv 标识没了（§3.3 不许拿 DOI 当唯一稳定标识）：{got!r}"
    assert "https://arxiv.org/abs/2403.01234" in got, f"只有标识没有链接，移动端点不开：{got!r}"


def test_A2_crossref_entry_exposes_resolvable_doi_link():
    got = "\n".join(sources._fmt([dict(CROSSREF_HIT)], "Crossref"))
    assert "https://doi.org/10.1109/ACCESS.2023.000001" in got, f"裸 DOI 不是链接：{got!r}"


# ── B1：并发两通不许串题 ──────────────────────────────────────────────────────
def test_B1_two_concurrent_tasks_do_not_bleed_topics(monkeypatch):
    seen: list[str] = []

    async def fake_search(topic: str, limit: int, year_range=None) -> str:
        await asyncio.sleep(0.01)               # 交错：让两通真的在跑同一时刻
        seen.append(topic)
        return f"主题「{topic}」的结果"

    monkeypatch.setattr(lp, "_search", fake_search)
    c1 = _cmd("检索钓鱼网站检测相关的 5 篇真实论文", task_id="t-phish-0001")
    c2 = _cmd("检索多智能体协作方向的文献 5 篇", task_id="t-mas-0002")
    async def both():
        return await asyncio.gather(lp.on_start(c1, None), lp.on_start(c2, None))

    r1, r2 = asyncio.run(both())        # gather 必须在同一个 loop 里建（外面建好再 run 会 "different loop"）
    p1 = r1.products[0].dataItems[0].text
    p2 = r2.products[0].dataItems[0].text
    assert len(seen) == 2, f"两通只跑了一次检索：{seen}"
    assert "钓鱼网站检测" in p1 and "多智能体协作" not in p1, f"第一通串题：{p1!r}"
    assert "多智能体协作" in p2 and "钓鱼网站检测" not in p2, f"第二通串题：{p2!r}"


# ── C1：缺 sessionId 的入站（协议里 sessionId 可选）────────────────────────────
def test_C1_missing_session_id_still_completes_without_collapsing_tasks(monkeypatch):
    async def fake_search(topic: str, limit: int, year_range=None) -> str:
        return f"主题「{topic}」的结果"

    monkeypatch.setattr(lp, "_search", fake_search)
    got = asyncio.run(lp.on_start(_cmd("检索脉冲神经网络低功耗编码的论文 3 篇",
                                       task_id="t-nosid-0001", session_id=None), None))
    text = got.products[0].dataItems[0].text
    assert "脉冲神经网络低功耗编码" in text, f"没有 sessionId 就把任务办坏了：{text!r}"
    assert got.status.state in ("awaiting-completion", "completed"), got.status.state
