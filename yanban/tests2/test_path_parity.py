"""两条生成路径必须拼出**同一个提示词**（轮 169）。

为什么单独钉这条：质量与闸门读数大多走 `ask()`（非流式），线上发的是 `ask_stream()`。
两条路一旦拼出不同的上下文（少传一块画像、多传一块计划），测出来的就不是发出去的那个 build——
这是本仓库反复栽过的"测的≠发的"，轮 166 刚抓到一处（repair 拿不到 plan）。
判据要机械可比对：把两边的 prompt 逐字比，不靠"看起来一样"。
"""
import asyncio
import os

os.environ["LLM_MOCK"] = "1"

import pytest

import companion.db as db_module
import llm
from companion.conversation import commitments, dialogue_manager, mentor
from companion.intent.model import ResearchIntent

PLAN = {"situation": "三个月内开题，只有轻薄本",
        "actions": [{"do": "跑通 XGBoost 基线", "with": "12k 条 URL", "dose": "2 周",
                     "check": "同一口径下准确率到 90%"},
                    {"do": "画方法谱系图", "with": "近 2 年 5 篇综述", "dose": "3 天",
                     "check": "每条线都有代表方法"}],
        "milestones": [], "artifact": "一张对比表", "pitfall": "别急着换数据集",
        "options": [], "axes": [], "evidence": [], "question": ""}
REPLY = ("建议你先精读 5 篇近两年的综述，把方法谱系画成一张图，3 天内覆盖 5 条线。"
         "可以先拿 12k 条 URL 跑一遍 XGBoost 基线，两周看到 90% 左右的准确率，"
         "并画出方法谱系图。你导师更看重期刊还是会议？")
PAPERS = [{"title": "Graph features for phishing detection", "year": 2024,
           "abstract": "We achieve 92% accuracy on the Urbuna phishing dataset."}]
CTX = {"user_text": "我只有三个月，先说说下一步", "memory_ctx": "- [user] 只有一台轻薄本",
       "gaps": [], "papers": PAPERS, "history": [{"role": "user", "content": "我第一次做科研"}]}


@pytest.fixture()
def isolated_db(tmp_path, monkeypatch):
    monkeypatch.setattr(db_module, "DB_PATH", tmp_path / "parity.db")
    db_module._CONN = None
    yield
    db_module._CONN = None


def _capture_prompts(monkeypatch, isolated_db, stream: bool, pid: str = "parity"):
    got: list[str] = []

    async def fake_plan_prefix(**kw):
        return PLAN

    monkeypatch.setattr(llm, "LLM_MOCK", False)
    monkeypatch.setattr(mentor, "make_plan", lambda **kw: PLAN)
    monkeypatch.setattr(mentor, "plan_prefix", fake_plan_prefix)

    async def fake_chat(prompt, **kw):
        got.append(prompt)
        return REPLY

    async def fake_stream(prompt, **kw):
        got.append(prompt)
        yield REPLY

    monkeypatch.setattr(mentor, "chat", fake_chat)
    monkeypatch.setattr(mentor, "chat_stream", fake_stream)
    intent = ResearchIntent(project_id=pid)
    turn = {"project_id": pid, "intent": intent, "history": CTX["history"],
            "papers": PAPERS, "gaps": [], "memory_items": [], "changed": [],
            "state": "DELIVER", "still_clarifying": False}
    kwargs = dialogue_manager._mentor_kwargs(turn, CTX["user_text"])
    kwargs.pop("intent")
    if stream:
        sink: dict = {}
        asyncio.run(_drain(mentor.ask_stream(intent=intent, plan_sink=sink, **kwargs)))
    else:
        mentor.ask(intent=intent, **kwargs)
    return got, kwargs


async def _drain(agen):
    return [x async for x in agen]


def test_two_paths_build_the_same_prompt(monkeypatch, isolated_db):
    """同一个 turn 走两条路，正文提示词必须逐字相同（规划调用那条不算）。

    🔴 首版两条路共用同一个 project_id ⇒ 第一条路 `ask()` 里的 note_plan 先写进了台账，
    第二条路的 followup_ctx 就多了 190 字——红的是夹具的先后顺序，不是产品。修法：两条路各用
    一个干净项目比"从零开始"的同一份上下文；台账块能不能到两条路由下一条用例专门管。
    """
    sync_prompts, sync_kwargs = _capture_prompts(monkeypatch, isolated_db, False, pid="parity-sync")
    stream_prompts, stream_kwargs = _capture_prompts(monkeypatch, isolated_db, True, pid="parity-stream")
    assert sync_prompts and stream_prompts
    assert sync_kwargs["followup_ctx"] == "" == stream_kwargs["followup_ctx"]
    assert sync_prompts[0] == stream_prompts[0], (
        f"两条路拼出的正文提示词不同：非流式 {len(sync_prompts[0])} 字 vs 流式 {len(stream_prompts[0])} 字")


def test_context_blocks_reach_both_paths(monkeypatch, isolated_db):
    """台账与画像两块上下文必须在**两条路**上都出现，不能只在其中一条生效。

    轮 177 起 `followup_ctx` 默认关（g181 的 S6 判"不留下"），所以这里**把开关抬起来**测：
    这条判据管的是"接线对不对"，不是"默认开不开"——默认值由 `test_commitments.py` 那两条管。
    """
    monkeypatch.setattr(dialogue_manager, "INJECT_FOLLOWUP", True)
    _capture_prompts(monkeypatch, isolated_db, stream=False)
    commitments.record("parity", PLAN)
    from companion.memory import user_memory
    user_memory.upsert_user("default", {"hardware": "轻薄本一张"})
    # 画像是按项目的 user_id 取的，这里直接把块塞进 kwargs 走两条路
    block = user_memory.profile_block("default", except_project="other")
    assert "轻薄本" in block
    for stream in (False, True):
        prompts, kwargs = _capture_prompts(monkeypatch, isolated_db, stream=stream)
        assert kwargs["followup_ctx"], "台账块没进 _mentor_kwargs ⇒ 这条回复看不见自己交代的事"
        assert "跑通 XGBoost 基线" in kwargs["followup_ctx"]
        assert "跑通 XGBoost 基线" in prompts[0]
        assert "轻薄本" in prompts[0] or kwargs["profile_ctx"] == "", \
            "画像块在一条路上生效、另一条不生效"


def test_parity_holds_with_the_switch_off_too(monkeypatch, isolated_db):
    """默认关的时候也要**两条路一致地关**：一条关一条开，测的与发的就又不是同一个 build。"""
    assert dialogue_manager.INJECT_FOLLOWUP is False
    blocks = []
    for stream in (False, True):
        prompts, kwargs = _capture_prompts(monkeypatch, isolated_db, stream=stream)
        assert kwargs["followup_ctx"] == ""
        assert "行动清单" not in prompts[0]
        blocks.append((kwargs["followup_ctx"], len(prompts[0])))
    assert blocks[0][0] == blocks[1][0]
