"""流式路径的正文必须看得见自己的计划（H5）。

以前 `stream_reply` 调 `mentor.repair(streamed, **_mentor_kwargs(...))`，而 `_mentor_kwargs` 里没有 plan
⇒ repair 收到 plan=None ⇒ `plan_dropped` / `bad_axes` 这两条闸门对**线上正文从不生效**，
只在走 `ask()` 的非流式测试里生效。这类"仪器测的和线上发的不是同一条路"是本项目的历史事故类型。
"""
import asyncio
import os

os.environ["LLM_MOCK"] = "1"

import pytest

import companion.db as db_module
import llm
from companion.conversation import commitments, dialogue_manager, mentor
from companion.intent.model import ResearchIntent

PLAN = {"situation": "三个月内开题，只有 12k 条 URL",
        "actions": [{"do": "跑通 XGBoost 基线", "with": "12k 条 URL", "dose": "2 周",
                     "check": "同一口径下准确率到 90%"},
                    {"do": "画方法谱系图", "with": "近 2 年 5 篇综述", "dose": "3 天",
                     "check": "图上每条线都有代表方法"}],
        "milestones": [], "artifact": "一张基线对比表", "pitfall": "别一上来就换数据集",
        "options": [], "axes": [], "evidence": [], "question": ""}

# 一句"其它都合规、就是没执行计划"的回复：有数量、有一问、不假称记得。
# 🔴 数字要避开计划里出现过的（首版写了"两周/2 页"，与 action.dose 的"2 周"共用裸数字 2，
# 被 `_tokens` 判成命中 ⇒ plan_dropped 不开火，闸门看着没咬人，其实是夹具与计划共用了一个符号）。
OFF_PLAN = ("建议你先花 9 周精读 7 篇英文教材，再写 4 页笔记把思路收拢。"
            "可以先从公开基准入手，本月底能看到初步结果。你要不要先把课程表排出来？")


@pytest.fixture()
def isolated_db(tmp_path, monkeypatch):
    monkeypatch.setattr(db_module, "DB_PATH", tmp_path / "test.db")
    db_module._CONN = None
    yield
    db_module._CONN = None


def _stream_events(turn, text):
    async def go():
        return [ev async for ev in dialogue_manager.stream_reply(turn, text)]
    return asyncio.run(go())


def _turn(isolated_db):
    pid = dialogue_manager.create_project(name="流式计划")
    return dialogue_manager.prepare_context("我想做钓鱼网站检测，只剩三个月", pid)


def test_stream_path_records_artifact_into_the_ledger(isolated_db, monkeypatch):
    """评审清单里挂着一条"artifact 在流式路径恒空"。这条要么证伪它、要么把洞钉住——
    两种结果都比留着一句"应该是空的"强（跟进卡上那行"这一段要留下的东西"就是它）。"""
    monkeypatch.setattr(llm, "LLM_MOCK", False)

    async def fake_plan_prefix(**kw):
        return PLAN

    async def fake_stream(prompt, **kw):
        text = ("建议你先跑通 XGBoost 基线，用 12k 条 URL，两周看到 90% 左右的准确率，"
                "并画出方法谱系图，3 天内覆盖 5 篇综述。你导师更看重期刊还是会议？")
        for piece in (text[:40], text[40:]):
            yield piece

    monkeypatch.setattr(mentor, "plan_prefix", fake_plan_prefix)
    monkeypatch.setattr(mentor, "chat_stream", fake_stream)
    turn = _turn(isolated_db)
    _stream_events(turn, "我想做钓鱼网站检测，只剩三个月")
    led = commitments.latest(turn["project_id"])
    assert led, "流式路径根本没写台账（那 H5 的接线也无从谈起）"
    assert led["artifact"] == PLAN["artifact"], f"artifact 空了：{led['artifact']!r}"
    assert led["situation"] == PLAN["situation"], f"situation 空了：{led['situation']!r}"


def test_off_plan_reply_violates_only_when_plan_is_known():
    """纯函数层面先钉住：这条回复在 `plan=PLAN` 下确实违规，在 plan=None 下看不见。"""
    v_with = mentor.violations(OFF_PLAN, papers=[], history=[], plan=PLAN, user_text="我只有三个月")
    v_without = mentor.violations(OFF_PLAN, papers=[], history=[], plan=None, user_text="我只有三个月")
    assert v_with["plan_dropped"] is True
    assert v_without["plan_dropped"] is False


def test_stream_repair_receives_the_plan_and_resamples_on_it(isolated_db, monkeypatch):
    seen = {"plan": "未调用", "prompts": []}
    monkeypatch.setattr(llm, "LLM_MOCK", False)

    async def fake_plan_prefix(**kw):
        return PLAN

    async def fake_stream(prompt, **kw):
        seen["prompts"].append(prompt)
        for piece in (OFF_PLAN[:40], OFF_PLAN[40:]):
            yield piece

    async def fake_chat(prompt, **kw):
        seen["prompts"].append(prompt)
        return ("建议你先跑通 XGBoost 基线，用 12k 条 URL，两周看到 90% 左右的准确率，"
                "并画出方法谱系图，3 天内覆盖 5 篇综述。")

    real_repair = mentor.repair

    def spy(text, **kw):
        seen["plan"] = kw.get("plan")
        return real_repair(text, **kw)

    monkeypatch.setattr(mentor, "plan_prefix", fake_plan_prefix)
    monkeypatch.setattr(mentor, "chat_stream", fake_stream)
    monkeypatch.setattr(mentor, "chat", fake_chat)
    monkeypatch.setattr(mentor, "repair", spy)

    turn = _turn(isolated_db)
    events = _stream_events(turn, "我想做钓鱼网站检测，只剩三个月")
    assert seen["plan"] == PLAN, "流式路径的 repair 又拿不到计划了（H5 回退）"
    names = [n for n, _ in events]
    assert "amend" in names                       # 闸门改写过，前端要收到新版
    assert any("动作清单" in p for p in seen["prompts"][1:])   # 纠正语点名了这条违规


def test_mock_path_still_passes_a_none_plan_without_breaking(isolated_db, monkeypatch):
    """MOCK 下 plan 本来就是 None：sink 里必须是 None，且整条链路照常出字。"""
    captured = {}
    real_repair = mentor.repair

    def spy(text, **kw):
        captured["plan"] = kw.get("plan", "缺失")
        return real_repair(text, **kw)

    monkeypatch.setattr(mentor, "repair", spy)
    turn = _turn(isolated_db)
    events = _stream_events(turn, "我想做钓鱼网站检测，只剩三个月")
    assert captured["plan"] is None
    assert any(n == "delta" for n, _ in events)


def test_sink_is_the_only_channel_and_has_one_wiring_pair():
    """不许再出现第二份"把计划传给正文闸门"的通道（模块级全局就是第二个出处）。"""
    dm = os.path.join(os.path.dirname(dialogue_manager.__file__), "dialogue_manager.py")
    text = open(dm, encoding="utf-8").read()
    assert text.count("plan_sink=sink") == 1
    assert text.count('plan=sink.get("plan")') == 2          # 正常路径与异常兜底路径各一次
    assert "_MENTOR_PLAN" not in text                        # 没有进程级全局的第二条路
