"""轮 153：右栏「研究进度」卡要用的两个 meta 字段——放行线与缺口中文标签。

这轮改的是显示，但显示的东西必须派生自产品里那**一个**数：前端抄一份 0.52，
下次调阈值（0.62→0.52 已经调过一次，理由写在 clarification 的注释里）卡片就会当场说谎。
所以下面每条期望值都从函数签名或模块常量取，没有一条钉字面量。
"""
from __future__ import annotations

import inspect

from companion.conversation import dialogue_manager as dm
from companion.conversation import interview_agent as ia
from companion.intent import clarification as cl
from companion.intent.extractor import extract_update
from companion.intent.model import RESEARCH_STAGES, ResearchIntent

# 放行线的唯一出处＝ready_for_search 的参数默认值。改它，这几条测试跟着改；抄它，就会红。
BASE = inspect.signature(cl.ready_for_search).parameters["threshold"].default


def _intent(**kw) -> ResearchIntent:
    return ResearchIntent(project_id="p153", **kw)


def test_line_with_known_stage_is_the_parameter_default():
    it = _intent(topic="多模态钓鱼网站检测", research_stage=[s for s in RESEARCH_STAGES if s != "unknown"][0])
    assert cl.search_threshold(it) == BASE


def test_unknown_stage_raises_the_line_by_the_penalty_constant():
    it = _intent(topic="多模态钓鱼网站检测", research_stage="unknown")
    assert cl.search_threshold(it) == BASE + cl.STAGE_UNKNOWN_PENALTY
    # 抬线必须真的抬：同一份意图，阶段未知比阶段已知更难放行
    known = _intent(topic="多模态钓鱼网站检测",
                    research_stage=[s for s in RESEARCH_STAGES if s != "unknown"][0])
    assert cl.search_threshold(it) > cl.search_threshold(known)


def test_enough_facts_takes_the_line_away_entirely():
    """事实够 6 项时不该再拿线拦人——仪表要显示「信息够了」，不是画一条永远够不到的线。"""
    it = _intent()
    for msg in ("我在准备毕业论文，题目是医学图像分割，实验室只有两块 3080，怕算力不够",
                "数据是公开的 BraTS 脑肿瘤 MRI，导师要求今年底前给出实验结果；我不太会调参。"):
        it.apply_update(extract_update(msg, it))
    assert cl.enough_facts(it)
    assert cl.search_threshold(it) is None
    assert cl.ready_for_search(it)[0] is True


def test_no_topic_never_passes_whatever_the_line_says():
    it = _intent(topic="", research_stage="unknown")
    assert cl.ready_for_search(it, threshold=0.0)[0] is False


def test_threshold_override_flows_through_both_doors():
    """参数化放行线时，ready_for_search 和 search_threshold 必须给同一个答案。

    这是重构的等价性证明：原来 `threshold + 0.05` 写在函数体里，现在搬到 search_threshold。
    若哪天有人只改一处，这里会红。
    """
    it = _intent(topic="联邦学习", research_stage="unknown")
    for base in (0.0, BASE, BASE + 0.2, 1.0):
        ok, conf = cl.ready_for_search(it, threshold=base)
        assert ok == (conf >= cl.search_threshold(it, base)), f"base={base} 两扇门答案不同"


def test_every_gap_field_gets_a_chinese_label():
    """空白意图的缺口＝两张表的全部字段，一个都不许没标签。"""
    gaps = list(cl.clarification_gaps(_intent()))
    assert gaps, "一条缺口都没有 ⇒ 这条测试什么都没测"
    labels = ia.gap_labels(gaps)
    assert [x["field"] for x in labels] == gaps
    for x in labels:
        assert x["label"], f"{x['field']} 标签为空"
        assert any("一" <= ch <= "鿿" for ch in x["label"]), f"{x['field']} 的标签不是中文：{x['label']!r}"


def test_gap_fields_stay_inside_the_labelled_table():
    """卡片只显示 clarification_gaps 给的字段，而它的取值域就是 FIELD_QUESTIONS。

    这条钉的是那个**前提**：哪天有人把缺口改成从 missing_fields() 直接生成（那里还有 topic 与
    user_goal，两张表都没有中文标签），卡片会安静地冒出 `user_goal` 这样的英文路径——这里先红。
    顺带记一个实测到的产品事实（本轮不修）：user_goal 占置信度权重 2.0（model.py:89）、
    算 enough_facts 的一项（clarification.py:59），却不在 FIELD_QUESTIONS 里 ⇒ 反问永远不问它，
    它只能靠规则通道（extractor.py:221）或模型填上。
    """
    fields = list(cl.clarification_gaps(_intent()))
    covered = set(ia.CHIP_OPTIONS) | {f for f, _w, _q in cl.FIELD_QUESTIONS}
    naked = [f for f in fields if f not in covered]
    assert fields and not naked, f"这些缺口字段没有中文标签来源：{naked}"
    assert all(ia.gap_labels([f])[0]["label"] for f in fields)


def test_gap_labels_are_derived_not_copied(monkeypatch):
    """标签必须现取：改了 CHIP_OPTIONS，卡片跟着变——否则说明某处抄了第二份。"""
    field = next(f for f in ia.CHIP_OPTIONS)
    old = ia.CHIP_OPTIONS[field]
    monkeypatch.setitem(ia.CHIP_OPTIONS, field, ("临时改过的文案", old[1]))
    assert ia.gap_labels([field])[0]["label"] == "临时改过的文案"
    assert ia.gap_labels([field])[0]["field"] == field


def _turn(gaps: list[str], it: ResearchIntent) -> dict:
    return {"project_id": it.project_id, "intent": it, "gaps": gaps, "papers": [],
            "still_clarifying": bool(gaps), "state": "CLARIFY", "history": []}


def test_meta_event_carries_line_and_labels():
    it = _intent(topic="深度学习中", research_stage="unknown")
    gaps = list(cl.clarification_gaps(it))[:3]
    meta = dm.meta_event(_turn(gaps, it))
    assert meta["ready_threshold"] == cl.search_threshold(it)
    assert [x["field"] for x in meta["gap_labels"]] == gaps
    assert all(x["label"] for x in meta["gap_labels"])


def test_meta_event_labels_empty_when_no_gaps():
    """放行那一轮 gaps=[]，标签也必须空——卡片据此显示「信息够了」而不是「还差 0 项」。"""
    it = _intent(topic="深度学习中", research_stage="unknown")
    meta = dm.meta_event(_turn([], it))
    assert meta["gap_labels"] == [] and meta["gaps"] == []


def test_meta_event_carries_the_stage_table_verbatim():
    """表头那个「阶段：literature_review」要有中文来源，而来源只能是 model 里那张表。

    前端另建一份 `{exploration:"领域调研",…}` 的话，后端改词或加阶段时表头会静默失配。
    """
    it = _intent(topic="深度学习中", research_stage="unknown")
    labels = dm.meta_event(_turn([], it))["stage_labels"]
    assert labels == RESEARCH_STAGES
    assert all(any("一" <= ch <= "鿿" for ch in v) for v in labels.values())
