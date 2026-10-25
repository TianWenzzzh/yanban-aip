"""P5 学习者状态建模：`user_goal` 接进采集链路 + 「学生自己动手的下一步」可机检最小形状。

为什么要这条（轮 153 看图那轮的副产品实测）：`user_goal` 占置信度权重 2.0
（`model.py:recompute_confidence`）、又是 `enough_facts` 十项之一，却不在 `FIELD_QUESTIONS` 八项里
⇒ 反问永不问它，只能靠规则通道碰巧接住。学生不主动说目标，系统就一边把它当已知的分量、
一边永远不问——这是"答得更好"之外的真差距（Teaching-over-Solving 2606.16206：答对率几乎不预测教学价值）。

判据（都是派生，不钉字面量）：
  1 `user_goal` 同时出现在**三张表**里：置信度权重、enough_facts、FIELD_QUESTIONS。少一处就是"半接线"。
  2 缺口标签必须是中文（派生自 CHIP_OPTIONS 或 FIELD_QUESTIONS），不许把字段名原样印给用户。
  3 每一颗 chip 的 `say` 送进**产品自己的** `extract_update`，必须把**它自己那个字段**填上非空值
    （否则那是假按钮：点了之后反问照旧）。注意 `open_access_preferred` 的合法值是 `False`，
    所以判据是"键在且值不是 None"，用真值判断会把这条合法回答误判成没填。
  4 教学维度 `student_next_step`：正例（留下由学生自己完成的下一步）与负例（纯鼓励）各一条，
    且负例必须**判不出来**——按轮 153 的教训，把题写成"是否鼓励学生"会奖励纯鼓励话术、方向倒着走。
"""
from __future__ import annotations

import pytest

from companion.conversation.interview_agent import CHIP_OPTIONS, gap_labels
from companion.conversation.teaching import student_next_step
from companion.intent import extractor
from companion.intent.clarification import FIELD_QUESTIONS, clarification_gaps, next_questions
from companion.intent.model import ResearchIntent

FIELDS = {f for f, _w, _q in FIELD_QUESTIONS}


def test_user_goal_is_wired_into_all_three_places():
    """同一个字段在三处都该算数：置信度权重、enough_facts、反问表。"""
    it = ResearchIntent(project_id="p-goal")
    assert "user_goal" in FIELDS, "user_goal 仍不在 FIELD_QUESTIONS ⇒ 反问永远不问它"

    # ① 置信度：只补 user_goal 一项，confidence 必须真的涨（不抄 2.0 这个数）
    base_it = ResearchIntent(project_id="p-goal-b", topic="多模态钓鱼网站检测")
    with_goal = ResearchIntent(project_id="p-goal-g", topic="多模态钓鱼网站检测")
    with_goal.user_goal = "先把方向定下来"
    base = base_it.recompute_confidence()
    got = with_goal.recompute_confidence()
    assert got > base, f"填上 user_goal 后 confidence 没变（{base} → {got}）⇒ 它没进权重表"

    # ② enough_facts：它是十项之一，拿掉它应当能改变判定（行为对账，不抄清单）
    def filled(**kw):
        it = ResearchIntent(project_id="p-ef", topic="多模态钓鱼网站检测", **kw)
        it.research_stage = "exploration"
        it.publication_goal.type = "sci_journal"
        it.paper_preferences.year_from = 2022
        it.user_background.dl_level = "basic"
        it.research_constraints.compute = "laptop"
        return it
    from companion.intent.clarification import enough_facts
    with_g, without_g = filled(user_goal="先定题"), filled()
    assert enough_facts(without_g) is False and enough_facts(with_g) is True, (
        "user_goal 不再影响 enough_facts ⇒ 三处对账断了一处")


def test_gap_label_for_user_goal_is_chinese_not_the_field_name():
    labels = gap_labels(["user_goal"])
    assert len(labels) == 1
    lab = labels[0]["label"]
    assert any("\u4e00" <= ch <= "\u9fff" for ch in lab), f"标签把字段名直接印给了用户：{lab}"
    assert lab != "user_goal"


def test_next_questions_can_actually_ask_about_user_goal():
    """只给了主题时，前两问里必须能问到目标（不是只躺在表里当摆设）。

    问句文本从 FIELD_QUESTIONS 现取，测试里不抄第二份中文——改文案不该改测试。
    """
    it = ResearchIntent(project_id="p-ask", topic="多模态钓鱼网站检测")
    gaps = clarification_gaps(it)
    assert "user_goal" in gaps, f"缺 user_goal 却没进缺口表：{list(gaps)}"
    q_by_field = {f: q for f, _w, q in FIELD_QUESTIONS}
    qs = next_questions(it, max_questions=2)
    assert q_by_field["user_goal"] in qs, f"user_goal 没进前两问：{qs}"


@pytest.mark.parametrize("field", sorted(CHIP_OPTIONS))
def test_every_chip_say_fills_its_own_field(field):
    """假按钮守卫：点一颗 chip 之后，那个字段必须真的被产品自己的规则通道填上。"""
    label, opts = CHIP_OPTIONS[field]
    assert opts, f"{field} 的 chip 组是空的"
    for short, say in opts:
        it = ResearchIntent(project_id="p-chip")
        upd = extractor.extract_update(say, it)
        assert field in upd and upd[field] is not None, (
            f"{field} 的选项「{short}」原话「{say}」没填上该字段（抽出 {sorted(upd)}）"
            f"⇒ 这颗 chip 点了等于没点")


def test_positive_reply_leaves_a_student_side_next_step():
    good = ("你这种情况很常见。这周你先把 GNN 那篇综述的第三章读完，"
            "然后拿作者放出来的代码跑一遍 baseline，把结果记在表里发我。")
    got = student_next_step(good)
    assert got and "你" in got, f"正例没被认出来：{got!r}"


def test_pure_encouragement_is_not_a_next_step():
    """负例：纯鼓励话术必须判「没有」。把题写成"是否鼓励学生"会奖励这种句子。"""
    bad = "这个领域很有意思，你已经很努力了，相信自己，你一定可以的！加油！"
    assert student_next_step(bad) == "", "纯鼓励被判成了『留了下一步』⇒ 这一维在奖励空话"


def test_telling_the_student_what_you_will_do_is_not_a_next_step():
    """负例二：动作的主语是助手自己（"我这就去查"），不是学生自己动手。"""
    bad = "我这就去查近三年多模态钓鱼检测的综述，然后把对比表整理给你，稍等。"
    assert student_next_step(bad) == ""
