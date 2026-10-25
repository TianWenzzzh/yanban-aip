"""Phase B 记忆系统测试：四类 Memory + 持久化 + 项目隔离。"""
from __future__ import annotations

import os
import pathlib
import sys

os.environ["LLM_MOCK"] = "1"
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

import pytest

import companion.db as db_module
from companion.conversation import dialogue_manager
from companion.memory import episodic_memory, paper_memory, retriever, user_memory


@pytest.fixture()
def isolated_db(tmp_path, monkeypatch):
    monkeypatch.setattr(db_module, "DB_PATH", tmp_path / "test.db")
    db_module._CONN = None
    yield tmp_path / "test.db"
    db_module._CONN = None


PAPER_A = {"id": "pa1", "title": "Phishing Detection with Visual Cues", "year": 2025, "doi": "10.1000/a"}
PAPER_B = {"id": "pb1", "title": "LLM-based Malware Analysis", "year": 2024, "doi": "10.1000/b"}


def test_user_memory_roundtrip(isolated_db):
    """轮 168 起画像的键受白名单约束（user_memory.PROFILE_KEYS）。

    "任意 dict 都能存"以前成立、现在不成立，这是刻意的：项目内的 topic 之类不许变成用户属性。
    所以这里的键换成合法键，并补一条"非白名单键整份入库后是空的"。
    """
    user_memory.upsert_user("u1", {"experience": "basic", "hardware": "一张 3080"})
    assert user_memory.get_profile("u1")["hardware"] == "一张 3080"
    snap = user_memory.snapshot("u1")
    assert snap["profile"]["experience"] == "basic"
    user_memory.upsert_user("u1", {"topic": "钓鱼网站检测"})
    assert user_memory.get_profile("u1") == {}


def test_paper_feedback_written_and_listed(isolated_db):
    paper_memory.record_feedback("p1", PAPER_A, status="favorite", reason="方法匹配我的路线")
    paper_memory.record_feedback("p1", PAPER_B, status="rejected", reason="方向不相关")
    favs = paper_memory.list_papers("p1", status="favorite")
    assert len(favs) == 1 and favs[0]["title"] == PAPER_A["title"]
    allp = paper_memory.list_papers("p1")
    assert {p["status"] for p in allp} == {"favorite", "rejected"}


def test_episodic_events(isolated_db):
    episodic_memory.record("p1", "direction_change", "放弃纯 URL 路线，转向视觉+LLM")
    evts = episodic_memory.recent("p1")
    assert evts and "视觉" in evts[0]["summary"]


def test_retriever_ranks_relevant_memory(isolated_db):
    episodic_memory.record("p1", "decision", "用户决定放弃 URL 特征检测路线")
    dialogue_manager.add_message("p1", "user", "我想研究钓鱼网站检测")
    hits = retriever.retrieve("p1", "URL 特征检测路线为什么放弃", k=3)
    assert hits, "应至少命中一条相关记忆"
    assert any("URL" in (h.get("summary") or h.get("content") or "") for h in hits)


def test_projects_isolated(isolated_db):
    pid1 = dialogue_manager.create_project(user_id="u1", name="钓鱼检测")
    pid2 = dialogue_manager.create_project(user_id="u1", name="文物修复")
    assert pid1 != pid2
    dialogue_manager.handle_message("我想研究钓鱼网站检测", pid1)
    s1 = dialogue_manager.project_snapshot(pid1)
    s2 = dialogue_manager.project_snapshot(pid2)
    assert "钓鱼" in s1["intent"]["topic"]
    assert s2["intent"]["topic"] == ""  # 项目 B 不串扰项目 A


def test_project_persists_after_reopen(isolated_db):
    pid = dialogue_manager.handle_message("我想研究多智能体协作", None)["project_id"]
    db_module._CONN = None  # 模拟进程重启
    snap = dialogue_manager.project_snapshot(pid)
    assert snap["intent"] is not None
    assert len(snap["messages"]) >= 1
