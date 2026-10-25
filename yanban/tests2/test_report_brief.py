# -*- coding: utf-8 -*-
"""B6 写报告前先讨论（对标 undermind U10）：动笔之前问两颗问题，答案必须真改变分节取舍。

钉的是 §4 B6 的验收 ①，加两条我自己最怕的：
① 回答与跳过两条路径都能出报告，且五节契约不破；
② 每颗选项都必须点名它要挪动哪一节（effect 里必须出现 SECTIONS 里的真节名）——
   挂一颗下游读不上的选项，等于骗用户"我控制了报告"（轮 184 那颗「排除项」的同一课）；
③ 跳过要记账，且"没问过 / 问过并跳过 / 问过并回答"三种状态互不冒充；
④ 预览与记账这两个端点一次模型调用都不发（花配额的是生成，不是问问题）。

判据走产品入口（build_prompt / build_report / HTTP 端点），不直接调私有函数。
"""
from __future__ import annotations

import os
import pathlib

os.environ["LLM_MOCK"] = "1"    # 与同目录其它测试一致：import 期定值

import pytest
from fastapi.testclient import TestClient

import companion.db as db_module
import companion.knowledge as knowledge
import llm
from companion.conversation import report, report_brief
from companion.intent.model import ResearchIntent

PAPERS = [{"id": "p1", "title": "Visual Phishing Detection with CLIP", "year": 2022,
           "abstract": "We embed URL strings with CLIP and train a classifier on 50000 pages."},
          {"id": "p2", "title": "A Survey of Multi-Agent LLM Systems", "year": 2024,
           "abstract": "This survey reviews 120 papers on multi-agent LLM protocols."}]
BOTH = {"purpose": "gap", "scope": "strict"}


@pytest.fixture()
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(db_module, "DB_PATH", tmp_path / "t.db")
    monkeypatch.setattr(knowledge, "KB_ROOT", tmp_path / "kb")
    db_module._CONN = None
    yield
    db_module._CONN = None


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(db_module, "DB_PATH", tmp_path / "test.db")
    monkeypatch.setattr(knowledge, "KB_ROOT", tmp_path / "kb")
    from companion.paper import downloader
    monkeypatch.setattr(downloader, "PDF_DIR", tmp_path / "pdf")
    db_module._CONN = None
    from leader import app
    return TestClient(app)


def intent_with(answers=None, skipped=False) -> ResearchIntent:
    it = ResearchIntent(project_id="p-brief", topic="光伏功率预测")
    if answers is not None:
        report_brief.apply_brief(it, answers)
    if skipped:
        report_brief.note_skip(it)
    return it


# ── ② 每颗选项都必须挪得动一节 ───────────────────────────────────────────
def test_every_option_names_a_real_section_it_moves():
    """effect 里必须出现 report.SECTIONS 的真节名，否则这颗选项就是装饰。"""
    assert len(report_brief.QUESTIONS) == 2, "简报固定两颗问题"
    for q in report_brief.QUESTIONS:
        assert len(q["options"]) >= 2
        for o in q["options"]:
            hit = [s for s in report.SECTIONS if s in o["effect"]]
            assert hit, f"{q['key']}={o['value']} 的 effect 没点名任何一节：{o['effect']}"


def test_two_options_of_the_same_question_cannot_say_the_same_thing():
    """同题两颗选项若 effect 逐字相同，用户点哪颗都一样——那还是装饰。"""
    for q in report_brief.QUESTIONS:
        effects = [o["effect"] for o in q["options"]]
        assert len(set(effects)) == len(effects), f"{q['key']} 有选项的 effect 撞车"


# ──  答案真的改变提示词 / 两条路径都不破五节契约 ─────────────────────────
def test_answering_changes_the_prompt_actually_sent():
    base = report.build_prompt(intent_with(), PAPERS)
    gap = report.build_prompt(intent_with(BOTH), PAPERS)
    assert gap != base, "答过问题之后提示词一个字都没变 ⇒ 这颗简报是装饰"
    assert "学生在动笔前明确说过" in gap


@pytest.mark.parametrize("answers,needle", [
    ({"purpose": "gap", "scope": "strict"}, "「研究空白与可切入方向」写成整份的重心"),
    ({"purpose": "method", "scope": "strict"}, "「方法对比」逐篇写清差异"),
    ({"purpose": "gap", "scope": "adjacent"}, "可以在「下一步建议」里点到相邻方向"),
], ids=["gap", "method", "adjacent"])
def test_each_choice_puts_its_own_effect_into_the_prompt(answers, needle):
    assert needle in report.build_prompt(intent_with(answers), PAPERS)


def test_five_section_contract_survives_both_paths(isolated):
    """验收①：回答过与跳过，两条路都要出得来一份五节齐全的报告（MOCK 下走降级骨架）。"""
    for tag, it in (("answered", intent_with(BOTH)), ("skipped", intent_with(skipped=True))):
        out = report.build_report(f"p-{tag}", it, PAPERS)
        assert out["ok"] is True, f"{tag} 路径没出报告：{out}"
        assert out["sections"][:5] == report.SECTIONS, f"{tag} 路径五节契约破了：{out['sections']}"


def test_skipping_does_not_invent_a_preference(isolated):
    """跳过之后提示词里不许出现"学生说过…"——没听见的话不能替学生说。"""
    assert report_brief.prompt_block(intent_with(skipped=True)) == ""


# ── ③ 三种状态互不冒充 ───────────────────────────────────────────────────
def test_three_states_are_distinguishable():
    never = report_brief.brief_for(intent_with())
    skipped = report_brief.brief_for(intent_with(skipped=True))
    answered = report_brief.brief_for(intent_with(BOTH))
    assert (never["recorded"], never["skipped"], never["answered"]) == (False, False, False)
    assert (skipped["recorded"], skipped["skipped"], skipped["answered"]) == (True, True, False)
    assert (answered["recorded"], answered["skipped"], answered["answered"]) == (True, False, True)


def test_answering_after_skipping_clears_the_skip_flag():
    it = intent_with(skipped=True)
    assert report_brief.apply_brief(it, BOTH) == ["purpose", "scope"]
    b = report_brief.brief_for(it)
    assert b["skipped"] is False and b["answered"] is True


def test_note_skip_is_idempotent_and_reports_whether_it_wrote():
    it = ResearchIntent(project_id="p-s", topic="t")
    assert report_brief.note_skip(it) is True
    assert report_brief.note_skip(it) is False, "已经记过还报「新记了一笔」 ⇒ 记账数会虚高"


@pytest.mark.parametrize("answers,why", [
    ({"purpose": "gap"}, "缺题要判错，不许按默认偷偷补齐"),
    ({"purpose": "sideways", "scope": "strict"}, "值不在清单里要判错"),
    ({"purpose": "gap", "scope": "strict", "extra": "x"}, "不认识的问题也要判错，别静默丢掉"),
], ids=["missing", "badvalue", "unknown"])
def test_bad_answers_are_rejected(answers, why):
    with pytest.raises(ValueError):
        report_brief.apply_brief(ResearchIntent(project_id="p-b", topic="t"), answers)


# ── ④ 端点：预览、记账、400，且一次模型调用都不发 ─────────────────────────
def test_brief_endpoints_read_write_and_reject(client, monkeypatch):
    pid = _new_project()
    got = client.get(f"/companion/report/{pid}/brief")
    assert got.status_code == 200 and len(got.json()["questions"]) == 2
    bad = client.post(f"/companion/report/{pid}/brief", json={"answers": {"purpose": "nope"}})
    assert bad.status_code == 400 and "purpose" in bad.json()["detail"]
    ok = client.post(f"/companion/report/{pid}/brief", json={"answers": BOTH})
    assert ok.status_code == 200 and ok.json()["answered"] is True
    skip = client.post(f"/companion/report/{pid}/brief", json={"skip": True})
    assert skip.status_code == 200 and skip.json()["skipped"] is True
    assert client.get("/companion/report/nope-x/brief").status_code == 404
    # 记过账之后必须重新读库还是这个值：端点只改内存里的 intent，下一轮生成就读不到了
    from companion.conversation import dialogue_manager as dm
    assert report_brief.brief_for(dm.load_intent(pid))["skipped"] is True


def test_preview_and_record_issue_zero_llm_calls(client, monkeypatch):
    """哨兵：问问题这一步不许花配额（生成才是下一步）。"""
    calls = {"n": 0}

    def _boom(*a, **k):
        calls["n"] += 1
        raise AssertionError("简报端点不许调模型")

    monkeypatch.setattr(llm, "chat", _boom)
    monkeypatch.setattr(llm, "chat_stream", _boom)
    from companion.conversation import mentor
    monkeypatch.setattr(mentor, "chat", _boom)
    pid = _new_project()
    assert client.get(f"/companion/report/{pid}/brief").status_code == 200
    assert client.post(f"/companion/report/{pid}/brief", json={"answers": BOTH}).status_code == 200
    assert client.post(f"/companion/report/{pid}/brief", json={"skip": True}).status_code == 200
    assert calls["n"] == 0


def _new_project(name: str = "简报"):
    """建一个真项目（save_intent 是 UPDATE，没有 projects 行就什么都写不进去）。"""
    from companion.conversation import dialogue_manager as dm
    pid = dm.create_project(name=name)
    it = dm.load_intent(pid)
    it.topic = "光伏功率预测"
    dm.save_intent(it)
    return pid


# ── 前端：先问再写，且跳过要看得见 ────────────────────────────────────────
SRC = pathlib.Path(__file__).resolve().parent.parent / "webui" / "src" / "pages" / "Companion.tsx"


def test_frontend_asks_before_generating():
    tsx = SRC.read_text(encoding="utf-8")
    assert "report/" in tsx and "/brief" in tsx, "前端没接简报端点"
    assert "data-testid=\"report-brief\"" in tsx, "简报卡没有可定位的 testid"
    assert "跳过，直接写" in tsx, "跳过这颗键要写明「直接写」，否则用户会以为跳过＝不写了"


def test_frontend_does_not_hardcode_the_question_list():
    """问题与选项只能从后端取：抄一份到前端，后端加一颗选项就会两边各说一套。"""
    tsx = SRC.read_text(encoding="utf-8")
    for needle in ("找还没人做的点", "把这批方法摆开比较", "只写这批论文里有的"):
        assert needle not in tsx, f"前端抄了后端的问题文案：{needle}"


def test_frontend_options_are_addressable_by_the_backends_own_value():
    """每颗选项要能被后端那个 value 点名：文案随时可改，key+value 是两边唯一的公共标识。

    轮 186 无头验收首跑就是因为 radio 没有 value、也没有按 value 出的 testid，
    "选了两颗"实际一颗都没选到，六条依赖答案的判据跟着一起红——那种红看着像产品坏了，
    其实是仪器点不着东西。
    """
    tsx = SRC.read_text(encoding="utf-8")
    assert "value={o.value}" in tsx, "radio 没有 value，外部只能按屏上文字点它"
    assert "brief-opt-${q.key}-${o.value}" in tsx, "选项没有按后端词汇表命名的 data-testid"


def test_frontend_goes_only_after_both_are_answered():
    """两颗没答完时「按这两条写」要按不动：后端对缺答是 400，可点只会给用户一次失败。"""
    tsx = SRC.read_text(encoding="utf-8")
    go = tsx.split('data-testid="brief-go"', 1)[1].split(">", 1)[0]
    assert "briefChoice" in go and "brief.questions.length" in go, \
        f"「按这两条写」的 disabled 没跟答案数挂钩：{go.strip()[:80]}"
