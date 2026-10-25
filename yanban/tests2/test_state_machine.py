"""八态状态机可达性（轮 29）：转移表被真正执行，SCREEN/READ/REFINE 由真实动作驱动。"""
from __future__ import annotations

import os
import pathlib
import sys

os.environ["LLM_MOCK"] = "1"
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

import pytest

from fastapi.testclient import TestClient

import companion.db as db_module
import companion.knowledge as knowledge
from companion.conversation import dialogue_manager as dm
from companion.conversation.state_machine import State
from companion.memory import paper_memory


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(db_module, "DB_PATH", tmp_path / "test.db")
    monkeypatch.setattr(knowledge, "KB_ROOT", tmp_path / "kb")
    db_module._CONN = None
    from leader import app
    return TestClient(app)


def _events(pid: str) -> list[dict]:
    rows = db_module.get_conn().execute(
        "SELECT summary FROM events WHERE project_id=? AND kind='state_change' ORDER BY id", (pid,)).fetchall()
    return [r["summary"] for r in rows]


def _new_project(client) -> str:
    return client.post("/companion/project", json={"name": "状态机"}).json()["project_id"]


def test_illegal_transition_does_not_move_or_write(client):
    pid = _new_project(client)
    assert dm.transition(pid, State.READ, "没读任何东西就想跳精读") is State.DISCOVER
    assert dm.get_state(pid) is State.DISCOVER
    assert _events(pid) == []


def test_legal_transition_moves_and_leaves_one_event(client):
    pid = _new_project(client)
    assert dm.transition(pid, State.CLARIFY) is State.CLARIFY
    assert dm.transition(pid, State.CLARIFY, "重复不该留痕") is State.CLARIFY
    evs = _events(pid)
    assert len(evs) == 1 and evs[0].startswith("DISCOVER → CLARIFY")


def test_all_eight_states_are_reachable_in_one_walk(client):
    pid = _new_project(client)
    walk = [State.CLARIFY, State.FORMULATE, State.SEARCH, State.SCREEN,
            State.READ, State.REFINE, State.DELIVER]
    for target in walk:
        assert dm.transition(pid, target) is target, f"走不到 {target}"
    assert dm.get_state(pid) is State.DELIVER
    assert len(_events(pid)) == 7
    visited = {State.DISCOVER.value} | {t.value for t in walk}
    assert len(visited) == 8                      # 八态全部真到过，不是文档里的摆设


def test_delivery_is_allowed_while_still_clarifying(client):
    """学生一句话就能要一份综述，不能因为还在追问态就拒绝交付（线上回归实测到这条）。"""
    pid = _new_project(client)
    assert dm.transition(pid, State.CLARIFY) is State.CLARIFY
    assert dm.transition(pid, State.DELIVER, "学生直接要一份文档") is State.DELIVER


def test_search_with_papers_lands_on_screen_not_deliver(client, monkeypatch):
    from companion.literature import engine
    pid = _new_project(client)
    monkeypatch.setattr(engine, "search_for_intent",
                        lambda intent, limit=8: [{"id": "a", "title": "论文甲", "citation_count": 5},
                                                 {"id": "b", "title": "论文乙", "citation_count": 9}])
    intent = dm.ResearchIntent(project_id=pid, topic="多模态钓鱼检测", research_stage="literature_review",
                               user_goal="发一篇 SCI", publication_goal={"type": "SCI"},
                               paper_preferences={"year_from": 2021})
    dm.save_intent(intent)
    turn = dm.prepare_turn("就按这些条件给我论文", pid)      # prepare_turn 内部已含检索
    assert turn["papers"] and turn["state"] == "SCREEN"
    assert [e.split(" → ")[-1].split("（")[0] for e in _events(pid)] == \
        ["FORMULATE", "SEARCH", "SCREEN"]


def test_feedback_after_screen_moves_to_refine(client):
    pid = _new_project(client)
    paper_memory.save_many(pid, [{"id": "a", "title": "论文甲"}])
    body = {"project_id": pid, "paper": {"id": "a", "title": "论文甲"}, "status": "favorite", "reason": ""}
    # 还没进筛选态，反馈不该把状态机硬掰到 REFINE
    assert client.post("/companion/paper/feedback", json=body).json()["state"] == "DISCOVER"
    for t in (State.CLARIFY, State.FORMULATE, State.SEARCH, State.SCREEN):
        dm.transition(pid, t)
    assert client.post("/companion/paper/feedback", json=body).json()["state"] == "REFINE"


def test_report_delivery_moves_state_to_deliver(client):
    pid = _new_project(client)
    paper_memory.save_many(pid, [{"id": "a", "title": "论文甲"}])
    for t in (State.CLARIFY, State.FORMULATE, State.SEARCH, State.SCREEN, State.READ, State.REFINE):
        dm.transition(pid, t)
    r = client.post(f"/companion/report/{pid}")
    assert r.status_code == 200 and r.json()["state"] == "DELIVER"
    assert dm.get_state(pid) is State.DELIVER


def test_report_refuses_a_paperless_project_with_an_actionable_note(client):
    """0 篇时端点必须 400 并且**说得出下一步**，状态一步都不许动。

    这条测试的由来：`g66`/`g67`/`g68` 三份现场里情景 3 都是
    `HTTP 400 /report/... → {"detail":"还没有论文，先聊到出结果再写综述"}`，
    而这条正确拒绝在仓库里躺了三轮才被读（真因是库里 0 篇，轮 106 P0 已修）。
    钉成契约，免得哪天有人把它改成静默返回空报告。
    """
    pid = _new_project(client)
    for t in (State.CLARIFY, State.FORMULATE, State.SEARCH):
        dm.transition(pid, t)
    r = client.post(f"/companion/report/{pid}")
    assert r.status_code == 400
    note = r.json()["detail"]
    assert "论文" in note and "先" in note, f"拒绝得说清下一步该干什么：{note}"
    assert dm.get_state(pid) is State.SEARCH, "被拒绝的交付不许推进状态机"
