"""闸门台账（轮 180）：重采样了几次、各是哪条规则开火，必须留在这一轮的 intent.extra 里。

起因是 g185 的分段读数——正文最后一滴到 `done` 之间有一段独立的"收尾"成本，
但 `repair()` 里算出的 violations 用完就丢，产品事后答不出"哪条规则在反复开火"。
没有这份台账，下一轮想治收尾只能整场重跑一遍付费探针。
"""
import asyncio
import os
from types import SimpleNamespace

os.environ["LLM_MOCK"] = "1"

import pytest

import companion.db as db_module
import llm
from companion.conversation import dialogue_manager, mentor
from companion.intent.model import ResearchIntent

PLAN = {"situation": "三个月内开题，只有 12k 条 URL",
        "actions": [{"do": "跑通 XGBoost 基线", "with": "12k 条 URL", "dose": "2 周",
                      "check": "同一口径下准确率到 90%"},
                    {"do": "画方法谱系图", "with": "近 2 年 5 篇综述", "dose": "3 天",
                      "check": "图上每条线都有代表方法"}],
        "milestones": [], "artifact": "一张基线对比表", "pitfall": "别一上来就换数据集",
        "options": [], "axes": [], "evidence": [], "question": ""}

# 一句"其它都合规、就是没执行计划"的回复（数字刻意避开 PLAN 里出现过的，见 test_stream_plan_gates 的注释）
OFF_PLAN = ("建议你先花 9 周精读 7 篇英文教材，再写 4 页笔记把思路收拢。"
            "可以先从公开基准入手，本月底能看到初步结果。你要不要先把课程表排出来？")
ON_PLAN = ("建议你先跑通 XGBoost 基线，用 12k 条 URL，两周看到 90% 左右的准确率，"
           "并画出方法谱系图，3 天内覆盖 5 篇综述。你导师更看重期刊还是会议？")


@pytest.fixture()
def isolated_db(tmp_path, monkeypatch):
    monkeypatch.setattr(db_module, "DB_PATH", tmp_path / "gate_ledger.db")
    db_module._CONN = None
    yield
    db_module._CONN = None


def _intent():
    return ResearchIntent(project_id="p1")


class NoExtra:
    """一个刻意没有 `extra` 属性的假 intent——诊断代码不许对它提要求。"""


def test_repair_records_rule_and_rounds(monkeypatch):
    """第一稿违规 ⇒ 记下重采样轮数与开火的规则名——这是"代价落在哪条规则上"的唯一出处。

    单因子：把 `thin_quant` 关掉（与 tests2 里给 MIN_REPLY_CHARS 降噪同一做法），
    否则这条夹具会因为"建议句不足 3 条"再叠一条 no_quantity，读不出台账记的是谁。
    """
    monkeypatch.setattr(mentor, "thin_quant", lambda text: False)
    calls = []

    async def fake_chat(prompt, **kw):
        calls.append(prompt)
        return "你学过深度学习吗？那你时间够吗？" if len(calls) == 1 else ON_PLAN

    monkeypatch.setattr(mentor, "chat", fake_chat)
    intent = _intent()
    mentor.ask(user_text="随便聊聊", intent=intent)
    got = intent.extra["gate_fires"]
    assert got["rounds"] == 1, f"重采样轮数没记对：{got}"
    assert got["by_rule"].get("multi_question") == 1, f"开火的规则没记下来：{got}"


def test_clean_reply_records_zero_rounds(monkeypatch):
    """没开火也要记：读的人要能分清"这一轮真的没退回"与"这条路根本没记"。"""
    monkeypatch.setattr(mentor, "thin_quant", lambda text: False)

    async def fake_chat(prompt, **kw):
        return ON_PLAN

    monkeypatch.setattr(mentor, "chat", fake_chat)
    intent = _intent()
    mentor.ask(user_text="随便聊聊", intent=intent)
    assert intent.extra["gate_fires"] == {"rounds": 0, "by_rule": {}}


def test_note_gates_never_breaks_a_reply():
    """诊断写不上也不能影响这条回复（与 note_plan 同一口径）。"""
    mentor.note_gates(NoExtra(), 1, {"fake_title": 1})            # 没有 extra 属性
    mentor.note_gates(SimpleNamespace(extra="不是 dict"), 1, {})   # extra 类型不对
    mentor.note_gates(None, 0, {})                                 # 连 intent 都没有
    assert True                                                    # 走到这里就是没抛


def test_stream_path_writes_the_same_ledger(isolated_db, monkeypatch):
    """线上走的是流式这条路：台账必须两条路径都写得到，否则测的和发的不是同一个东西。"""
    monkeypatch.setattr(llm, "LLM_MOCK", False)

    async def fake_plan_prefix(**kw):
        return PLAN

    async def fake_stream(prompt, **kw):
        for piece in (OFF_PLAN[:40], OFF_PLAN[40:]):
            yield piece

    async def fake_chat(prompt, **kw):
        return ON_PLAN

    monkeypatch.setattr(mentor, "plan_prefix", fake_plan_prefix)
    monkeypatch.setattr(mentor, "chat_stream", fake_stream)
    monkeypatch.setattr(mentor, "chat", fake_chat)

    pid = dialogue_manager.create_project(name="闸门台账")
    turn = dialogue_manager.prepare_context("我想做钓鱼网站检测，只剩三个月", pid)

    async def go():
        return [ev async for ev in dialogue_manager.stream_reply(turn, "我想做钓鱼网站检测，只剩三个月")]

    events = asyncio.run(go())
    done = [p for n, p in events if n == "done"]
    assert done, f"流式没走到 done：{[n for n, _p in events]}"
    got = done[0]["intent"]["extra"].get("gate_fires")
    assert got and got["rounds"] >= 1, f"流式路径没写台账：{got}"
    assert got["by_rule"].get("plan_dropped") == 1, f"流式记的不是那条规则：{got}"
