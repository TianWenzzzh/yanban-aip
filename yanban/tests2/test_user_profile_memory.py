"""跨项目稳定画像（轮 168）：写入是机械派生的，读取要带出处，删除是真删除。

这一层以前"有表有函数、零生产写入点"——画像恒为 {}，用户级记忆只是一句承诺。
用例盯的是三类会静默回退的事：① 拿"没说"当"说过"（空值入库）；
② 把本项目刚说的话包装成"跨项目记忆"（假出处）；③ 删除只做了软删。
"""
import os
import pathlib

os.environ["LLM_MOCK"] = "1"

import pytest

import companion.db as db_module
from companion.conversation import dialogue_manager, mentor
from companion.intent import extractor
from companion.intent.model import ResearchIntent
from companion.memory import user_memory


@pytest.fixture()
def isolated_db(tmp_path, monkeypatch):
    monkeypatch.setattr(db_module, "DB_PATH", tmp_path / "test.db")
    db_module._CONN = None
    yield
    db_module._CONN = None


def _intent(pid="p1", text="我第一次做科研，想做钓鱼检测，深度学习只学过一点，只有轻薄本，"
                             "三个月内要出一篇 SCI；文献想看近五年的"):
    it = ResearchIntent(project_id=pid)
    it.apply_update(extractor.extract_update(text, it))
    return it


# ── 写入侧 ───────────────────────────────────────────────────────────
def test_facts_are_derived_only_from_present_fields():
    facts = user_memory.facts_from_intent(_intent())
    assert set(facts) <= set(user_memory.PROFILE_KEYS)
    assert facts.get("hardware"), f"约束里明明写了轻薄本，却没进画像：{facts}"
    empty = ResearchIntent(project_id="p0")
    assert user_memory.facts_from_intent(empty) == {}      # 没说过 ⇒ 一个字都不收


def test_learn_merges_and_keeps_provenance(isolated_db):
    it = _intent()
    first = user_memory.learn("u1", user_memory.facts_from_intent(it), "pa")
    assert first and set(first) <= set(user_memory.PROFILE_KEYS)
    rec = user_memory.profile_records("u1")
    assert rec[first[0]]["project"] == "pa" and rec[first[0]]["at"]
    again = user_memory.learn("u1", user_memory.facts_from_intent(it), "pa")
    assert again == []                                      # 同值不重写时间戳
    assert user_memory.profile_records("u1")[first[0]]["at"] == rec[first[0]]["at"]


def test_learn_rejects_keys_outside_the_whitelist(isolated_db):
    changed = user_memory.learn("u1", {"topic": "钓鱼检测", "experience": "basic"}, "pa")
    assert changed == ["experience"]
    assert "topic" not in user_memory.get_profile("u1")     # 项目内的东西不许变成用户属性


def test_upsert_user_stays_flat_for_callers(isolated_db):
    user_memory.upsert_user("u2", {"hardware": "一张 3080", "junk": "x"})
    assert user_memory.get_profile("u2") == {"hardware": "一张 3080"}


def test_forget_is_a_hard_delete(isolated_db):
    user_memory.learn("u1", {"hardware": "轻薄本"}, "pa")
    assert user_memory.forget("u1", "hardware") is True
    assert "hardware" not in user_memory.profile_records("u1")
    assert user_memory.forget("u1", "hardware") is False


# ── 读取侧 ───────────────────────────────────────────────────────────
def test_block_declares_source_and_hides_same_project_facts(isolated_db):
    user_memory.learn("u1", {"hardware": "轻薄本", "timeline": "三个月"}, "pa")
    block = user_memory.profile_block("u1", except_project="pb")
    assert "其它项目" in block and "轻薄本" in block and "机器与数据" in block
    assert user_memory.profile_block("u1", except_project="pa") == ""   # 本项目说的不算跨项目记忆
    assert user_memory.profile_block("nobody") == ""


def test_both_prompt_builders_carry_the_profile():
    it = _intent()
    for fn in (mentor.build_prompt, mentor.plan_prompt):
        out = fn(user_text="下一步做什么", intent=it, profile_ctx="【画像】机器：轻薄本")
        assert "轻薄本" in out
        assert "画像" not in fn(user_text="下一步做什么", intent=it)


# ── 端到端：新项目的第二轮真的看得见另一个项目学到的事 ────────────────
def test_second_project_sees_the_profile(monkeypatch, isolated_db):
    prompts = []

    async def fake_chat(prompt, **kw):
        prompts.append(prompt)
        return ("建议你先精读 5 篇近两年的综述，把方法谱系画成一张图。可以先拿公开数据集跑一遍"
                "基线，两周能看到初步结果。第三步用 3 张数据集各跑 1 次对照，1 周内出对比表。"
                "你导师更看重期刊还是会议？")

    async def fake_stream(prompt, **kw):
        prompts.append(prompt)
        yield "建议你先精读 5 篇近两年的综述，把方法谱系画成一张图。"

    monkeypatch.setattr(mentor, "chat", fake_chat)
    monkeypatch.setattr(mentor, "chat_stream", fake_stream)
    monkeypatch.setattr(mentor, "plan_prefix", lambda **kw: _fake_plan())
    monkeypatch.setattr(dialogue_manager, "run_search", lambda turn, no_search=False: turn)   # 本轮不测检索

    def _fake_plan():
        return None

    pid_a = dialogue_manager.create_project(user_id="u9", name="项目 A")
    dialogue_manager.handle_message("我第一次做科研，深度学习只学过一点，只有一台轻薄本，三个月内要出 SCI", pid_a)
    assert user_memory.get_profile("u9"), "第一轮之后画像还是空的 ⇒ 写入没接上生产路径"

    pid_b = dialogue_manager.create_project(user_id="u9", name="项目 B")
    out = dialogue_manager.handle_message("换个题目，帮我看看接下来怎么排", pid_b)
    assert any("轻薄本" in p or "机器与数据" in p for p in prompts[1:]), \
        "新项目里模型看不见另一个项目学到的画像"
    assert out["profile"].get("hardware")


def test_profile_endpoints_read_and_forget(client_isolated):
    from companion.memory import user_memory
    user_memory.learn("u3", {"experience": "basic"}, "pa")
    r = client_isolated.get("/companion/users/u3/profile")
    assert r.status_code == 200 and r.json()["profile"]["experience"] == "basic"
    assert r.json()["labels"]["experience"] == "基础"
    assert client_isolated.post("/companion/users/u3/profile/forget",
                                json={"key": "topic"}).status_code == 400
    ok = client_isolated.post("/companion/users/u3/profile/forget", json={"key": "experience"})
    assert ok.status_code == 200 and ok.json()["removed"] is True
    assert client_isolated.get("/companion/users/u3/profile").json()["profile"] == {}


def test_project_snapshot_carries_user_id(isolated_db):
    """画像是用户级的：前端要读它就得从项目快照里知道归属，而不是另开一条按项目查画像的端点。"""
    pid = dialogue_manager.create_project(user_id="u7", name="归属测试")
    assert dialogue_manager.project_snapshot(pid)["user_id"] == "u7"


def test_memory_panel_shows_profile_with_backend_labels():
    """面板不许自己抄一份中文标签：键名与标签都来自 /users/{uid}/profile。"""
    panel = (pathlib.Path(__file__).resolve().parents[1] / "webui" / "src"
             / "pages" / "MemoryPanel.tsx").read_text(encoding="utf-8")
    assert "/users/${userId}/profile" in panel and "profileLabels[k]" in panel
    # 「阶段」这个词面板里本来就有（事件种类 KIND_LABEL 里 state_change 也叫"阶段"），
    # 那是另一件事的标签，不是画像标签的抄本；其余四个只可能来自后端。
    for key, label in user_memory.PROFILE_LABELS.items():
        if label == "阶段":
            continue
        assert label not in panel, f"前端抄了一份画像标签「{label}」"
    assert "profile/forget" in panel and "别记了" in panel
    assert "profileKeys.length > 0" in panel          # 没有画像就整块不渲染，不摆空卡
    assert 'data-testid="profile-card"' in panel


def test_profile_provenance_does_not_claim_cross_project_for_itself():
    """整块顶一句「这些来自你的其它项目」，对本项目刚学到的那几条是假的——
    画像按 (user, key) 存、每条带 project 出处，本项目学到的也在同一张表里。
    出处逐条说（本项目/来自 X），才是有一句都不骗人的写法。"""
    panel = (pathlib.Path(__file__).resolve().parents[1] / "webui" / "src"
             / "pages" / "MemoryPanel.tsx").read_text(encoding="utf-8")
    assert "这些来自你的其它项目" not in panel
    assert "profileRecords[k].project === projectId" in panel
    assert "本项目里学的" in panel


@pytest.fixture()
def client_isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(db_module, "DB_PATH", tmp_path / "prof.db")
    db_module._CONN = None
    from fastapi.testclient import TestClient
    from leader import app
    return TestClient(app)
