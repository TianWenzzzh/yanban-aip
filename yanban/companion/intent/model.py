"""ResearchIntent：统一研究状态模型（2.0 任务书 §4）。"""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

Stage = Literal["exploration", "literature_review", "topic_selection",
                "method_design", "experiment", "writing", "unknown"]

RESEARCH_STAGES: dict[str, str] = {
    "exploration": "领域调研", "literature_review": "文献综述", "topic_selection": "选题确定",
    "method_design": "方法设计", "experiment": "实验验证", "writing": "论文写作",
}
LEVELS = ["none", "basic", "intermediate", "advanced"]


class UserBackground(BaseModel):
    education_level: str | None = None
    major: str | None = None
    programming_level: str | None = None
    ml_level: str | None = None
    dl_level: str | None = None
    llm_level: str | None = None
    paper_reading_level: str | None = None


class PublicationGoal(BaseModel):
    type: Literal["SCI", "CCF", "conference", "competition", "thesis", "unknown"] | None = None
    level: str | None = None
    timeline: str | None = None


class PaperPreferences(BaseModel):
    year_from: int | None = None
    year_to: int | None = None
    language: list[str] = Field(default_factory=list)
    publishers: list[str] = Field(default_factory=list)
    open_access_preferred: bool | None = None
    reproducibility_preferred: bool | None = None


class ResearchConstraints(BaseModel):
    compute: str | None = None
    dataset: str | None = None
    coding_difficulty: str | None = None
    experiment_time: str | None = None


class ResearchIntent(BaseModel):
    project_id: str
    topic: str = ""
    user_goal: str = ""
    research_stage: Stage = "unknown"
    user_background: UserBackground = Field(default_factory=UserBackground)
    publication_goal: PublicationGoal = Field(default_factory=PublicationGoal)
    paper_preferences: PaperPreferences = Field(default_factory=PaperPreferences)
    research_constraints: ResearchConstraints = Field(default_factory=ResearchConstraints)
    current_focus: list[str] = Field(default_factory=list)
    rejected_directions: list[str] = Field(default_factory=list)
    accepted_directions: list[str] = Field(default_factory=list)
    known_papers: list[str] = Field(default_factory=list)
    read_papers: list[str] = Field(default_factory=list)
    favorite_papers: list[str] = Field(default_factory=list)
    rejected_papers: list[str] = Field(default_factory=list)
    unresolved_questions: list[str] = Field(default_factory=list)
    confidence: float = 0.0
    extra: dict[str, Any] = Field(default_factory=dict)

    # ── 信息缺口与置信度 ────────────────────────────────────────────
    def missing_fields(self) -> list[str]:
        """按对检索策略的影响权重从高到低返回缺失字段。"""
        checks: list[tuple[str, bool]] = [
            ("topic", bool(self.topic)),
            ("research_stage", self.research_stage != "unknown"),
            ("user_goal", bool(self.user_goal)),
            ("publication_goal.type", self.publication_goal.type not in (None, "unknown")),
            ("paper_preferences.year_from", self.paper_preferences.year_from is not None),
            ("user_background.dl_level", self.user_background.dl_level is not None),
            ("user_background.llm_level", self.user_background.llm_level is not None),
            ("paper_preferences.open_access_preferred", self.paper_preferences.open_access_preferred is not None),
            ("research_constraints.compute", self.research_constraints.compute is not None),
        ]
        return [name for name, ok in checks if not ok]

    def recompute_confidence(self) -> float:
        """confidence = 已知权重 / 总权重（0-1）。"""
        weights: dict[str, float] = {
            "topic": 3.0, "research_stage": 2.5, "user_goal": 2.0,
            "publication_goal.type": 1.5, "paper_preferences.year_from": 1.0,
            "user_background.dl_level": 1.0, "user_background.llm_level": 0.8,
            "paper_preferences.open_access_preferred": 0.6,
            "research_constraints.compute": 0.6,
        }
        total = sum(weights.values())
        missing = set(self.missing_fields())
        got = sum(w for k, w in weights.items() if k not in missing)
        self.confidence = round(got / total, 3)
        return self.confidence

    def apply_update(self, update: dict[str, Any]) -> list[str]:
        """把提取出的增量合并进 intent；返回被更新的字段名（用于事件留痕）。"""
        changed: list[str] = []
        simple = {
            "topic": ("topic",), "user_goal": ("user_goal",), "research_stage": ("research_stage",),
            "current_focus": ("current_focus",), "rejected_directions": ("rejected_directions",),
            "accepted_directions": ("accepted_directions",), "unresolved_questions": ("unresolved_questions",),
        }
        for key, value in update.items():
            if key in simple and value:
                attr = simple[key][0]
                cur = getattr(self, attr)
                if isinstance(cur, list):
                    merged = list(dict.fromkeys([*cur, *value]) if isinstance(value, list) else list(dict.fromkeys([*cur, value])))
                    if merged != cur:
                        setattr(self, attr, merged); changed.append(attr)
                elif cur != value:
                    setattr(self, attr, value); changed.append(attr)
            elif "." in key and value is not None:
                section, _, field = key.partition(".")
                sec = getattr(self, section, None)
                if sec is not None and getattr(sec, field, None) != value:
                    setattr(sec, field, value); changed.append(f"{section}.{field}")
        self.recompute_confidence()
        return changed
