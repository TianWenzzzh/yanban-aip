"""Clarification Engine：clarification_score = 缺失程度 × 对检索策略的影响权重。

每轮只产出 1-2 个信息增益最大的自然问题——不做调查问卷。
"""
from __future__ import annotations

from .model import ResearchIntent

# 字段 → (权重, 自然问题模板)。权重=该信息对检索策略的影响程度。
FIELD_QUESTIONS: list[tuple[str, float, str]] = [
    ("research_stage", 2.5, "你现在处于哪个阶段：刚想了解这个领域，还是已经准备定题、做实验，或者开始写论文了？"),
    # user_goal 占置信度权重 2.0、也是 enough_facts 的十项之一，却从没进过这张表
    # ⇒ 反问永远不问它，只能靠规则通道碰巧接住（轮 153 看图那轮量到的副产品）。
    ("user_goal", 2.0, "这次研究你最想先拿到什么：定一个做得动的方向、完成毕业论文、投出去一篇，还是参加比赛？"),
    ("publication_goal.type", 2.0, "你的目标大概是什么类型？比如发 SCI、投会议、毕业论文，或者参加比赛？"),
    ("paper_preferences.year_from", 1.6, "文献有年限偏好吗？比如近两年、近五年的论文？"),
    ("user_background.dl_level", 1.5, "深度学习这块你什么水平？跑过模型吗，还是刚开始学？"),
    ("user_background.llm_level", 1.2, "大模型（LLM）方向你了解多少？"),
    ("paper_preferences.open_access_preferred", 1.0, "希望论文尽量能直接下载全文（开放获取）吗？"),
    ("research_constraints.compute", 1.0, "你的实验条件如何？有 GPU 吗，还是只能轻量级实验？"),
    ("user_background.paper_reading_level", 0.9, "你读英文论文的经验多吗？需要我多解释一些术语吗？"),
]


def clarification_gaps(intent: ResearchIntent) -> dict[str, float]:
    """返回缺失字段的 clarification_score（降序）。分数 = 权重 × (1 - confidence)。"""
    scores: dict[str, float] = {}
    missing = set(intent.missing_fields())
    for field, weight, _question in FIELD_QUESTIONS:
        if field in missing or (field == "user_background.paper_reading_level"
                                and not intent.user_background.paper_reading_level):
            scores[field] = round(weight * (1.0 - intent.confidence * 0.5), 3)
    return dict(sorted(scores.items(), key=lambda kv: -kv[1]))


def next_questions(intent: ResearchIntent, max_questions: int = 2) -> list[str]:
    """返回当前信息增益最大的 1-2 个自然问题；信息足够进入检索时返回 []。"""
    ready, _conf = ready_for_search(intent)
    if ready:
        return []
    gaps = clarification_gaps(intent)
    questions: list[str] = []
    for field, _score in gaps.items():
        for f, w, q in FIELD_QUESTIONS:
            if f == field:
                questions.append(q)
                break
        if len(questions) >= max_questions:
            break
    return questions


def enough_facts(intent) -> bool:
    """学生已经给出的事实够多了：继续追问就是查户口，不是导师。

    两轮口径实测到的形状（probe_two_turn.py）：那位说"医学图像分割、毕业论文、年底前要结果、
    公开 BraTS 数据集、不太会调参"的学生，第二轮仍然只收到一句"你深度学习水平如何？"——
    既没给计划也没触发检索，把 2.0 真正区别于裸问的那段（拿文献、对比表说话）整个跳过了。
    """
    b, p, c, pref = (intent.user_background, intent.publication_goal,
                     intent.research_constraints, intent.paper_preferences)
    facts = [bool(intent.topic), bool(intent.user_goal), bool(b.dl_level), bool(b.paper_reading_level),
             bool(c.compute), bool(c.dataset), p.type not in (None, "unknown"),
             bool(p.timeline), bool(pref.year_from), bool(c.experiment_time)]
    return sum(1 for f in facts if f) >= 6


# 放行线的唯一出处：字面量只许出现在这里，右栏仪表要显示这条线也只能取它（经 meta 事件），
# 前端再抄一份 0.52 就是下一次「文档说 0.52、代码是别的数」的事故形状。
SEARCH_THRESHOLD = 0.52
STAGE_UNKNOWN_PENALTY = 0.05


def search_threshold(intent: ResearchIntent, base: float = SEARCH_THRESHOLD) -> float | None:
    """这份意图**当前**实际适用的放行线；None = 事实已经够了，不看线。

    分三档不是设计洁癖：没提阶段时要把线抬高一档（下面 ready_for_search 的注释记着实测原因），
    而事实够 6 项时根本不该拿线拦人。仪表若只画一条固定线，就会在「够了」的情况下画出一条假门槛。
    """
    if enough_facts(intent):
        return None
    return base + (STAGE_UNKNOWN_PENALTY if intent.research_stage == "unknown" else 0.0)


def ready_for_search(intent: ResearchIntent, threshold: float = SEARCH_THRESHOLD) -> tuple[bool, float]:
    """信息是否足够进入 FORMULATE/SEARCH。

    阈值 0.62→0.52：实测五轮真实输入下置信度只到 0.538，用户会被困在反问里出不来内容
    （任务书 P0-3「反问像查户口」的量化根因）。
    """
    c = intent.recompute_confidence()
    # 主题必须已知；「阶段未知」只在置信度还不够时当硬闸门——
    # 实测有用户把目标/年限/水平/算力/OA 全说了（conf 0.592）却因未提阶段被连着只问不查，
    # 那是「查户口」而非导师行为；信息已经够多时先给一轮结果，阶段留给 chip 补。
    if not intent.topic:
        return False, c
    line = search_threshold(intent, threshold)
    if line is None:
        return True, c
    return c >= line, c
