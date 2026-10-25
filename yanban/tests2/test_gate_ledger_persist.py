"""轮 231：闸门台账必须随回复落库（轮 230 线上实测取不到 ⇒ 无法归因）。

来龙去脉：轮 180 把「重采样几次、各是哪条规则开火」写进 `intent.extra["gate_fires"]`，
轮 216 的删除链记账同路。但 `note_gates` 只改**内存里**的那份 intent，而回复这条路上
（`dialogue_manager.finalize_turn`，非流式与流式四条出口都收口在它）原先没有任何一处再调
`save_intent` ⇒ 轮 230 只读探针实测：线上 `research_intents` 往回 12 行里带 `gate_fires` 的 **0 行**，
屏上那句「上一版（按核对结果改写过）」只能证明发生过重写，不能证明是 `over_shape` 触发的。
"""
import os

os.environ["LLM_MOCK"] = "1"

import pytest  # noqa: E402

import companion.db as db_module  # noqa: E402
from companion.conversation import dialogue_manager  # noqa: E402
from companion.intent.model import ResearchIntent  # noqa: E402


@pytest.fixture()
def isolated_db(tmp_path, monkeypatch):
    monkeypatch.setattr(db_module, "DB_PATH", tmp_path / "ledger_persist.db")
    db_module._CONN = None
    yield
    db_module._CONN = None


def _turn(pid, **extra_kv):
    intent = ResearchIntent(project_id=pid)
    intent.extra.update(extra_kv)
    return {"project_id": pid, "intent": intent, "state": "deliver", "changed": [],
            "papers": [], "still_clarifying": False, "search_ran": False}


def test_the_gate_ledger_reaches_the_database(isolated_db):
    """台账不是内存里的临时物：finalize_turn 之后，重开连接读回来还得是同一份。"""
    pid = dialogue_manager.create_project(name="台账落库")
    turn = _turn(pid, gate_fires={"rounds": 1, "by_rule": {"over_shape": 1}})
    dialogue_manager.finalize_turn(turn, "最常用的是 Cora。", degraded=False)
    db_module._CONN = None                      # 强制重开连接：读盘上那份，不是内存那份
    back = dialogue_manager.load_intent(pid)
    assert back is not None, "意图行没落库"
    assert back.extra.get("gate_fires") == {"rounds": 1, "by_rule": {"over_shape": 1}}, \
        f"闸门台账没随回复落库 ⇒ 线上事后无法归因：{back.extra.get('gate_fires')}"


def test_the_deletion_ledger_reaches_the_database_too(isolated_db):
    """同一条通道上的第二类读数（轮 216 的删除链记账，真键名 `clean_deletions`）一起落库。"""
    pid = dialogue_manager.create_project(name="删除链落库")
    turn = _turn(pid, clean_deletions={"no_citation": 2, "total": 2})
    dialogue_manager.finalize_turn(turn, "另一句话答完。", degraded=False)
    db_module._CONN = None
    back = dialogue_manager.load_intent(pid)
    assert back.extra.get("clean_deletions") == {"no_citation": 2, "total": 2}, \
        f"删除链记账没落库：{back.extra.get('clean_deletions')}"


def test_a_broken_ledger_write_does_not_eat_the_reply(isolated_db, monkeypatch):
    """落库是诊断，不是回复的前置条件：save_intent 抛异常时回复照常返回、照常入库。"""
    pid = dialogue_manager.create_project(name="写坏也不能影响回复")
    turn = _turn(pid, gate_fires={"rounds": 1, "by_rule": {"over_shape": 1}})
    calls = []
    orig = dialogue_manager.save_intent

    def boom(intent):
        calls.append(intent)
        raise RuntimeError("模拟写库失败")

    monkeypatch.setattr(dialogue_manager, "save_intent", boom)
    out = dialogue_manager.finalize_turn(turn, "照样答。", degraded=False)
    assert calls, "桩根本没被调到 ⇒ 这条在测别的东西"
    assert out["reply"] == "照样答。"
    db_module._CONN = None
    assert dialogue_manager.recent_messages(pid)[-1]["content"] == "照样答。"
    monkeypatch.setattr(dialogue_manager, "save_intent", orig)
