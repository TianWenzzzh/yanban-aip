# 替学生宣布完成的硬闸门（轮 170）。起因是 g176 大样本 A/B：注入台账的 B 臂 4/32 句冒领，
# 关掉注入的 A 臂 0/32 —— 提示词里明明写着"不要替学生宣布某项已经完成"。
import os

os.environ["LLM_MOCK"] = "1"

import pytest

import companion.db as db_module
from companion.conversation import commitments, dialogue_manager, mentor
from companion.intent.model import ResearchIntent

PLAN = {"situation": "三个月内开题", "artifact": "一张对比表",
        "actions": [{"do": "跑通 XGBoost 基线", "with": "12k 条 URL", "dose": "2 周",
                     "check": "准确率到 90%"},
                    {"do": "画方法谱系图", "with": "5 篇综述", "dose": "3 天",
                     "check": "每条线有代表方法"}]}


@pytest.fixture()
def isolated_db(tmp_path, monkeypatch):
    monkeypatch.setattr(db_module, "DB_PATH", tmp_path / "fc.db")
    db_module._CONN = None
    yield
    db_module._CONN = None


def test_detector_fires_only_on_declarative_claims(isolated_db):
    commitments.record("p1", PLAN)
    items = commitments.open_items("p1")
    assert len(items) == 2
    assert commitments.premature_claims("你已经把 XGBoost 基线做完了，下一步是写初稿。", items) == 1
    assert commitments.premature_claims("上次那条 XGBoost 基线跑通了吗？", items) == 0
    assert commitments.premature_claims("谱系图画完了吗，还是还卡在数据上？", items) == 0
    assert commitments.premature_claims("建议你先精读 5 篇综述，两周出初步结果。", items) == 0
    assert commitments.premature_claims("你已经把 XGBoost 基线做完了。", []) == 0


def test_violation_only_when_open_items_are_passed(isolated_db):
    commitments.record("p1", PLAN)
    items = commitments.open_items("p1")
    text = "你已经把 XGBoost 基线做完了，接下来写初稿。"
    assert mentor.violations(text, papers=[], history=[], user_text="下一步")["false_completion"] is False
    assert mentor.violations(text, papers=[], history=[], user_text="下一步",
                             open_items=items)["false_completion"] is True


def test_repair_resamples_and_names_the_violation(isolated_db, monkeypatch):
    commitments.record("p1", PLAN)
    items = commitments.open_items("p1")
    prompts = []

    async def fake_chat(prompt, **kw):
        prompts.append(prompt)
        if len(prompts) == 1:
            return "你已经把 XGBoost 基线做完了，接下来一周写初稿，两周出结果，覆盖 5 条线。"
        return ("上次那条 XGBoost 基线跑通了吗？如果通了，下一步用 3 张公开数据集各跑 1 次对照，"
                "1 周内出同一口径的对比表。")

    monkeypatch.setattr(mentor, "chat", fake_chat)
    out = mentor.repair("你已经把 XGBoost 基线做完了，接下来一周写初稿，两周出结果，覆盖 5 条线。",
                        user_text="我回来了", intent=ResearchIntent(project_id="p1"),
                        history=[{"role": "user", "content": "上次你说让我跑基线"}],
                        papers=[], open_items=items)
    assert len(prompts) >= 2 and any("尚未回报完成" in p for p in prompts[1:])
    assert commitments.premature_claims(out, items) == 0


def test_stream_path_feeds_the_gate(isolated_db, monkeypatch):
    """闸门必须在线上那条路上拿到未结项清单，否则又是一条"只在测试里生效"的判据。"""
    seen = {}
    real_violations = mentor.violations

    def spy(text, **kw):
        seen["open_items"] = kw.get("open_items")
        return real_violations(text, **kw)

    commitments.record("parity-fc", PLAN)
    monkeypatch.setattr(mentor, "violations", spy)
    intent = ResearchIntent(project_id="parity-fc")
    turn = {"project_id": "parity-fc", "intent": intent, "history": [], "papers": [], "gaps": [],
            "memory_items": [], "changed": [], "state": "DELIVER", "still_clarifying": False}
    kwargs = dialogue_manager._mentor_kwargs(turn, "我回来了")
    assert kwargs["open_items"] and len(kwargs["open_items"]) == 2
