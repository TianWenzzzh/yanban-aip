"""轮 106 P0：「产品静默吞掉检索结果」必须变成可观测的事。

一律走 prepare_context / run_search / prepare_turn 这类**产品入口**，不直接调私有函数——
轮 104 的线上 500 就是因为所有测试都在私有函数上跑，产品路径上那行漏传参数的调用
从没被执行过，230 条测试全绿放行。
"""
import os

os.environ["LLM_MOCK"] = "1"  # 与同目录其它测试一致：import 期定值

import pytest

import companion.db as db_module
import companion.knowledge as knowledge
from companion.conversation import dialogue_manager as dm
from companion.intent import extractor
from companion.intent.model import ResearchIntent
from companion.literature import engine
from companion.memory import paper_memory

# g69b 里那份「产品 0 篇、同进程直调引擎 8 篇」的原话，一字不改地钉成回归门
SAY = "我们实验室在做光伏功率预测，我想找一个还没人做的点"
FOLLOW = "有两年历史数据，15 分钟采样，想赶在明年春天投一篇"


@pytest.fixture()
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(db_module, "DB_PATH", tmp_path / "t.db")
    monkeypatch.setattr(knowledge, "KB_ROOT", tmp_path / "kb")
    db_module._CONN = None
    yield
    db_module._CONN = None


def _papers(n: int) -> list[dict]:
    return [{"id": f"p{i}", "title": f"Photovoltaic power forecasting study {i}"} for i in range(n)]


def test_scenario3_first_turn_clears_the_gate_and_stores(isolated, monkeypatch):
    """根因回归门：第一句就把主题和目标说清了，过去一个字都没接住 → 闸门永远不放行。"""
    monkeypatch.setattr(engine, "search_for_intent", lambda intent, limit=8: _papers(2))
    turn = dm.prepare_turn(SAY, None)
    assert turn["intent"].research_stage == "topic_selection"
    assert turn["intent"].user_goal
    assert not turn["still_clarifying"], f"闸门不该再拦：{turn['intent'].extra.get('search_missing')}"
    assert len(turn["papers"]) == 2
    extra = dm.load_intent(turn["project_id"]).extra        # 必须从库里读回来
    assert (extra["search_engine_returned"], extra["search_stored"]) == (2, 2)
    assert len(paper_memory.list_papers(turn["project_id"])) == 2


def test_repeated_search_reports_dedup_instead_of_lying(isolated, monkeypatch):
    """第二次引擎又给 2 篇、库里一篇没多——stored 必须是 0。

    以前 save_many 直接 return len(papers)，returned 和 stored 永远相等，
    去重吃掉多少在产品上完全看不出来。
    """
    monkeypatch.setattr(engine, "search_for_intent", lambda intent, limit=8: _papers(2))
    pid = dm.prepare_turn(SAY, None)["project_id"]
    dm.prepare_turn(FOLLOW, pid)
    extra = dm.load_intent(pid).extra
    assert (extra["search_engine_returned"], extra["search_stored"]) == (2, 0)
    assert len(paper_memory.list_papers(pid)) == 2


def test_clarifying_turn_leaves_a_trace(isolated, monkeypatch):
    """不查不是错，不查却什么都不留才是错。走真实闸门判成反问轮，读数要留得下来。"""
    calls: list[int] = []
    monkeypatch.setattr(engine, "search_for_intent", lambda *a, **k: calls.append(1) or [])
    turn = dm.prepare_context("在吗，我有件事想请教", None)
    assert turn["still_clarifying"], "这条样本必须真的被判成反问轮，否则测试是空转"
    turn = dm.run_search(turn)
    assert calls == [], "反问轮不许发检索（轮 40 的结论）"
    extra = dm.load_intent(turn["project_id"]).extra
    assert extra["search_skipped"] == "clarifying"
    assert extra["search_missing"], "要留下缺哪几项，否则定位不了为什么没查"
    assert isinstance(extra["search_confidence"], float)


def test_search_failure_records_type_and_message(isolated, monkeypatch):
    def boom(intent, limit=8):
        raise RuntimeError("HTTP 429 Too Many Requests")

    monkeypatch.setattr(engine, "search_for_intent", boom)
    turn = dm.prepare_turn(SAY, None)
    assert turn["papers"] == []
    extra = dm.load_intent(turn["project_id"]).extra
    assert "429" in extra["search_error"]
    assert extra["search_exc_type"] == "RuntimeError"
    assert (extra["search_engine_returned"], extra["search_stored"]) == (0, 0)


def test_save_many_counts_rows_actually_inserted(isolated):
    pid = dm.create_project(name="计数")
    papers = _papers(3)
    assert paper_memory.save_many(pid, papers) == 3
    assert paper_memory.save_many(pid, papers) == 0
    assert paper_memory.save_many(pid, papers + [{"id": "x", "title": "一篇没见过的"}]) == 1


def test_rule_channel_catches_what_the_student_actually_said():
    """抽取覆盖面：这两句话里每个事实都是学生说过的，不新增任何推断。"""
    u1 = extractor.extract_update(SAY, ResearchIntent(project_id="u1", topic="光伏功率预测"))
    assert u1["research_stage"] == "topic_selection"
    assert u1["user_goal"]
    u2 = extractor.extract_update(FOLLOW, ResearchIntent(project_id="u2", topic="光伏功率预测"))
    assert u2["research_constraints.dataset"] == "已有多年历史数据"
    assert u2["research_constraints.experiment_time"] == "一年内"


def test_new_patterns_do_not_fire_on_unrelated_chatter():
    """负向：没说出口的事不许被新模式猜出来，否则 conf 会变成凭空涨。"""
    for line in ("我今天在食堂排队，想到晚上还要交作业",
                 "实验室的空调坏了，数据先放一放",
                 "这本书的第三章没人引用过吧"):
        u = extractor.extract_update(line, ResearchIntent(project_id="n", topic=""))
        assert "user_goal" not in u, line
        assert u.get("research_stage") != "topic_selection", line
        assert "research_constraints.dataset" not in u, line
        assert "research_constraints.experiment_time" not in u, line
