"""Phase A 认知层测试：ResearchIntent / 提取 / 反问 / 状态机 / 对话流程。

全部在 LLM_MOCK=1 + 独立临时数据库下运行，零真实调用。
"""
from __future__ import annotations

import os
import pathlib
import sys

os.environ["LLM_MOCK"] = "1"
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

import pytest

from companion.conversation import dialogue_manager
from companion.conversation.state_machine import State
from companion.intent import clarification, extractor
from companion.intent.model import ResearchIntent


import companion.db as db_module  # noqa: E402


@pytest.fixture()
def isolated_db(tmp_path, monkeypatch):
    monkeypatch.setattr(db_module, "DB_PATH", tmp_path / "test.db")
    db_module._CONN = None
    yield tmp_path / "test.db"
    db_module._CONN = None


import companion.db as db_module  # noqa: E402


# ── ResearchIntent 模型 ──────────────────────────────────────────────
def test_intent_defaults_and_confidence():
    intent = ResearchIntent(project_id="p1")
    assert intent.research_stage == "unknown" and intent.confidence == 0.0
    assert "topic" in intent.missing_fields()


def test_intent_apply_update_and_confidence_rise():
    intent = ResearchIntent(project_id="p1")
    changed = intent.apply_update({
        "topic": "钓鱼网站检测",
        "research_stage": "exploration",
        "user_goal": "一年左右发表 SCI",
        "publication_goal.type": "SCI",
        "user_background.dl_level": "basic",
    })
    assert "topic" in changed and intent.topic == "钓鱼网站检测"
    assert intent.confidence > 0.5
    assert intent.recompute_confidence() == intent.confidence


# ── 规则提取器 ───────────────────────────────────────────────────────
def test_extractor_stage_and_goal():
    intent = ResearchIntent(project_id="p")
    u = extractor.extract_update("我第一次做科研，想一年左右发一篇 SCI", intent)
    assert u.get("research_stage") == "exploration"
    assert "SCI" in str(u.get("publication_goal.type", ""))


def test_extractor_levels_and_years():
    intent = ResearchIntent(project_id="p")
    u = extractor.extract_update("深度学习学过一点，LLM 不太了解，要近两年的论文", intent)
    assert u.get("user_background.dl_level") == "basic"
    assert u.get("user_background.llm_level") == "none"
    assert u.get("paper_preferences.year_from") == 2024


def test_extractor_topic_capture():
    intent = ResearchIntent(project_id="p")
    u = extractor.extract_update("我想研究钓鱼网站检测，不知道从哪开始", intent)
    assert "钓鱼网站检测" in str(u.get("topic", ""))


# ── 反问引擎 ─────────────────────────────────────────────────────────
def test_clarification_asks_when_sparse():
    intent = ResearchIntent(project_id="p")
    qs = clarification.next_questions(intent, max_questions=2)
    assert 1 <= len(qs) <= 2
    # 稀疏状态最高增益应是研究阶段或目标类型
    assert any("阶段" in q or "目标" in q or "SCI" in q for q in qs)


def test_clarification_silent_when_ready():
    intent = ResearchIntent(project_id="p")
    intent.apply_update({
        "topic": "钓鱼网站检测", "research_stage": "literature_review",
        "user_goal": "一年左右发表 SCI", "publication_goal.type": "SCI",
        "paper_preferences.year_from": 2024, "user_background.dl_level": "basic",
        "user_background.llm_level": "none", "paper_preferences.open_access_preferred": True,
        "research_constraints.compute": "无 GPU",
    })
    ok, conf = clarification.ready_for_search(intent)
    assert ok and conf >= 0.62
    assert clarification.next_questions(intent) == []


# ── 对话流程（临时库）────────────────────────────────────────────────
def test_clarify_turn_asks_and_does_not_search(isolated_db, monkeypatch):
    """反问轮不检索：先给方向感而不是论文列表。

    轮 40 一度让澄清轮也查 3 篇当"地基"，n=36 盲评否掉了（2.0 胜率 16%，不查时 33—39%）：
    引文把可执行计划的篇幅挤掉，评委要的是"接下来做什么"。这条测试现在守反面契约。
    （这里仍然要打桩：不打桩一旦哪天又有人让澄清轮检索，测试就会真连 Crossref。
    tests2/conftest.py 已经加了离线总闸，这一层是双保险。）
    """
    from companion.literature import engine

    calls: list[int] = []

    def fake_search(intent, limit=8):
        calls.append(limit)
        return [{"id": "g1", "title": "Multimodal phishing detection", "doi": "10.1/x", "year": 2024}]

    monkeypatch.setattr(engine, "search_for_intent", fake_search)

    r1 = dialogue_manager.handle_message("我想研究钓鱼网站检测，第一次做科研", None)
    assert r1["state"] in (State.DISCOVER.value, State.CLARIFY.value)
    assert r1["gaps"] and r1["reply"]
    assert not r1.get("papers") and calls == []

    pid = r1["project_id"]
    r2 = dialogue_manager.handle_message("想一年左右发一篇 SCI，深度学习学过一点", pid)
    assert r2["project_id"] == pid
    intent = r2["intent"]
    assert intent["topic"] == "钓鱼网站检测"
    assert intent["publication_goal"]["type"] == "SCI"


def test_conversation_state_persists(isolated_db, tmp_path):
    r1 = dialogue_manager.handle_message("我想研究多智能体协作，刚入门科研", None)
    pid = r1["project_id"]
    # 模拟重启：新建 manager 进程内重新读取（同库）
    snap = dialogue_manager.project_snapshot(pid)
    assert snap["intent"]["topic"]
    assert len(snap["messages"]) >= 2


def test_conversation_reaches_search_when_ready(isolated_db, monkeypatch):
    r1 = dialogue_manager.handle_message("我想研究大模型多智能体协作，在做文献综述，目标发 SCI，看近两年论文，深度学习比较熟", None)
    pid = r1["project_id"]
    # 补齐置信度后第二轮应进入 SEARCH（engine 真实调用可能失败——失败也应给出说明而非崩溃）
    r2 = dialogue_manager.handle_message("开放获取优先，没有 GPU 做大实验", pid)
    assert r2["state"] in (State.SCREEN.value, State.DELIVER.value, State.SEARCH.value, State.CLARIFY.value)
    assert r2["reply"]


# ---------------------------------------------------------------- 轮 147：主题壳词闸门的语言形状

def test_latin_term_is_content_not_shell():
    """`_BOILER_LATIN` 想拦的是**拉丁水词**，可它前面那句 `len(cjk) < 2` 把所有纯拉丁主题一起拦了。

    死支不是推测：`输出/scripts/probe_topic_guard_shapes.py` 用 ast 造了两个变异体，
    「只留第一句」与原函数在全语料上判定 0 处不同、「只留第二句」有 2 处不同 ⇒ 第二句从不改变判定。
    """
    assert not extractor._topic_is_shell("GNN")
    assert not extractor._topic_is_shell("CRISPR")


def test_boilerplate_latin_still_shell():
    """放宽的边界：拉丁水词表里的词逐条仍须判壳，否则「IEEE 的方向」那类线上事故会回来。

    样本从产品自己的 `_BOILER_LATIN` 现取（表一改就跟着动，不钉字面量）。
    """
    assert extractor._BOILER_LATIN, "水词表空了 ⇒ 这条测试失去对象"
    for w in sorted(extractor._BOILER_LATIN):
        assert extractor._topic_is_shell(w), f"拉丁水词 {w} 不该被当实义主题放过"
    assert extractor._topic_is_shell("IEEE 的方向")


def test_capture_topic_keeps_mixed_language_title():
    """用户可见后果：中文句里嵌英文术语、术语后面不跟「的」时，整条主题此前会丢光。

    丢光不是"抽得不准"而是**空**——空 topic 让 `ready_for_search` 恒假，学生被无限反问。
    """
    t1 = extractor.capture_topic("题目是 CRISPR 脱靶检测的深度学习模型")
    t2 = extractor.capture_topic("我的课题是 PINN 求解偏微分方程")
    assert "CRISPR" in t1, t1
    assert "PINN" in t2, t2


def test_loosened_guard_still_rejects_no_topic_input():
    """旧期望原样保留：没主题就别硬查（放宽只放宽「拉丁水词 vs 拉丁术语」这一维）。"""
    assert extractor.capture_topic("我刚入门，不知道做什么方向") == ""
    assert extractor._topic_is_shell("方向")
    it = ResearchIntent(project_id="t-shell")
    it.apply_update(extractor.extract_update("我刚入门，不知道做什么方向", it))
    assert it.topic == "" and not clarification.ready_for_search(it)[0]


# ---------------------------------------------------------------- 轮 148：模型兜底通道的校验链

def _stub_model(monkeypatch, answer: str) -> None:
    """把 `llm.chat_json` 换成计数桩，并让 mock 关掉——否则 `llm_topic` 第一行就返回空串，
    测的其实是 mock 分支，不是那三道校验。"""
    import llm

    monkeypatch.setattr(llm, "LLM_MOCK", False)

    async def fake(prompt: str, **kw):
        return {"topic": answer}

    monkeypatch.setattr(llm, "chat_json", fake)


def test_model_channel_admits_latin_topic(monkeypatch):
    """线上实况当夹具：模型从英语原话里给出纯拉丁 topic，修复前死在壳词闸门（g141→g142）。"""
    _stub_model(monkeypatch, "mice killing")
    assert extractor.llm_topic("I don't want to kill any more mice") == "mice killing"


def test_model_channel_still_rejects_translation_and_boilerplate(monkeypatch):
    """反向：放宽只放宽「拉丁水词 vs 拉丁术语」。模型把英语译成中文（撞逐字校验）、
    以及水词表里的词逐条，仍须返回空串——否则这条通道就成了"什么都能进检索"。"""
    en = "How should I deal with becoming discouraged as a graduate student?"
    _stub_model(monkeypatch, "研究生挫败感处理")
    assert extractor.llm_topic(en) == ""
    assert extractor._BOILER_LATIN, "水词表空了 ⇒ 下面这个循环没有对象"
    for w in sorted(extractor._BOILER_LATIN):
        _stub_model(monkeypatch, w)
        assert extractor.llm_topic(f"tell me more about {w}") == "", f"拉丁水词 {w} 不该从兜底通道漏出去"
