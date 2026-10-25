"""多维 Rubric 评审器（v6 升级1）。

依据（调研 2026-09-12，docs/algorithm_upgrades_report.md 附来源）：
- Microsoft LLM-Rubric（ACL 2024）：多维 rubric、逐维度独立 prompt、锚定评分标尺
- Evaluating Scoring Bias in LLM-as-a-Judge（arXiv:2506.22316）：单 prompt 总分受
  评分提示自身偏差扰动；分维度 + 锚定示例是主要缓解手段
- 长度偏差（verbosity bias）：单分数 prompt 系统性偏爱长文 → 本实现记录字数并
  在维度定义中显式"以信息密度论质量，不以长度论质量"

设计：
- 5 维度（方法严谨性/内容完整性/引用真实性/结构规范/语言密度），各 0-10 分独立打分
- 总分 = 加权平均（权重可配），同时保留各维度分——修订可定向到低分维度
- 幻觉清单独立收集（引用真实性维度的副产品）
- 一次 LLM 调用输出全维度 JSON（比逐维度 N 次调用省 token；维度定义即锚定标尺）
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

from llm import LLMError, chat

DIMENSIONS = [
    ("rigor", "方法严谨性", 0.30,
     "论证链条完整、假设声明清晰、实验/检索方法可复现。10=方法可复现且假设自洽；"
     "7=方法清楚但缺验证细节；4=方法含糊；0=无方法或方法自相矛盾。"),
    ("coverage", "内容完整性", 0.25,
     "主题覆盖面与要点深度（以信息密度论质量，不以长度论质量——注水不加分）。"
     "10=覆盖全面且每点有实质内容；7=覆盖主要方面；4=仅浅层罗列；0=偏题。"),
    ("citation", "引用真实性", 0.20,
     "引用/数字有出处且可信。10=所有声明有据可查；7=少量声明缺出处；4=多数声明无出处；"
     "0=存在明显编造的引用或数据。"),
    ("structure", "结构规范", 0.15,
     "摘要/发现/结论三段齐全、层级清晰、编号引用一致。10=三段齐全且引用编号一致；"
     "7=结构齐全但引用不一致；4=缺一段或结构混乱；0=无结构。"),
    ("density", "语言密度", 0.10,
     "信息密度：删掉任何一段都会损失信息。10=每句都有信息量；7=少量冗余；"
     "4=三分之一以上为套话填充；0=通篇套话。"),
]
WEIGHTS = {k: w for k, _name, w, _anchor in DIMENSIONS}


@dataclass
class JudgeReport:
    scores: dict[str, float] = field(default_factory=dict)      # 维度 → 0-10
    total: float = 0.0                                          # 加权总分
    hallucinations: list[str] = field(default_factory=list)
    word_count: int = 0
    raw: str = ""

    def to_dict(self) -> dict:
        return {"scores": self.scores, "total": round(self.total, 2),
                "hallucinations": self.hallucinations, "word_count": self.word_count}


JUDGE_SYSTEM = (
    "你是严格的多维评审器。逐维度独立评分，各维度严格按锚定标尺取值，"
    "不得因文章长度、语气自信度或格式美观度偏移分数。只输出 JSON。"
)

JUDGE_PROMPT = (
    "对以下研究报告按 5 个维度评分（各 0-10，严格按标尺锚定）：\n{rubric}\n"
    "同时列出幻觉声明（文中无依据的具体数字/引用/结论，逐条列出；没有则空数组）。\n\n"
    '输出 JSON：{{"scores": {{"rigor": 0-10, "coverage": 0-10, "citation": 0-10, '
    '"structure": 0-10, "density": 0-10}}, "hallucinations": ["…"]}}\n\n报告：\n{report}'
)


def _rubric_text() -> str:
    return "\n".join(f"- {key}（{name}，权重 {w}）：{anchor}" for key, name, w, anchor in DIMENSIONS)


def parse_judge_output(text: str, word_count: int) -> JudgeReport:
    """宽容解析：JSON 提取 + 维度裁剪 + 缺维补零。"""
    m = re.search(r"\{.*\}", text, re.S)
    report = JudgeReport(word_count=word_count, raw=text[:400])
    if not m:
        return report
    try:
        data = json.loads(m.group(0))
    except json.JSONDecodeError:
        return report
    scores = data.get("scores") or {}
    for key, _name, _w, _anchor in DIMENSIONS:
        try:
            v = float(scores.get(key, 0))
        except (TypeError, ValueError):
            v = 0.0
        report.scores[key] = max(0.0, min(10.0, v))
    report.total = sum(report.scores[k] * WEIGHTS[k] for k, _n, w, _a in DIMENSIONS)
    hall = data.get("hallucinations")
    if isinstance(hall, list):
        report.hallucinations = [str(x)[:120] for x in hall if str(x).strip() and str(x).strip() != "无"][:8]
    return report


async def judge_report(text: str, *, temperature: float = 0.1,
                       model: str | None = None) -> JudgeReport:
    """多维评审：一次调用输出全维度。失败抛 LLMError（调用方决定降级）。"""
    word_count = len(re.sub(r"\s", "", text))
    out = await chat(
        JUDGE_PROMPT.format(rubric=_rubric_text(), report=text[:3500]),
        system=JUDGE_SYSTEM, temperature=temperature, max_tokens=800, model=model,
    )
    return parse_judge_output(out, word_count)


def weakest_dimensions(report: JudgeReport, *, threshold: float = 7.0) -> list[str]:
    """低于阈值的维度（按分升序）——Self-Refine 定向修订的输入。"""
    weak = [(k, v) for k, v in report.scores.items() if v < threshold]
    return [k for k, _ in sorted(weak, key=lambda x: x[1])]


# ── 离线测试 ──────────────────────────────────────────────────────────────

def test_parse_judge_output_full():
    raw = '{"scores": {"rigor": 8, "coverage": 7, "citation": 9, "structure": 8, "density": 6}, ' \
          '"hallucinations": ["2024 年提升 30% 无出处"]}'
    r = parse_judge_output(raw, word_count=1200)
    assert r.scores["rigor"] == 8.0 and r.scores["density"] == 6.0
    assert 6.5 < r.total < 8.5
    assert r.hallucinations == ["2024 年提升 30% 无出处"]


def test_parse_judge_output_garbage_safe():
    r = parse_judge_output("模型抽风输出无关内容", word_count=10)
    assert r.total == 0.0 and r.scores == {} and r.hallucinations == []


def test_parse_clamps_and_defaults():
    raw = '{"scores": {"rigor": 15, "coverage": "bad"}, "hallucinations": ["", "无", "编造引用X"]}'
    r = parse_judge_output(raw, word_count=100)
    assert r.scores["rigor"] == 10.0 and r.scores["coverage"] == 0.0
    assert r.hallucinations == ["编造引用X"]


def test_weakest_dimensions_ordering():
    r = parse_judge_output('{"scores": {"rigor": 6, "coverage": 8, "citation": 5, '
                           '"structure": 9, "density": 7}, "hallucinations": []}', 10)
    assert weakest_dimensions(r) == ["citation", "rigor"]  # 7.0 阈值下升序（density=7 恰好达标被排除）


if __name__ == "__main__":
    import sys
    results = [
        test_parse_judge_output_full(),
        test_parse_judge_output_garbage_safe(),
        test_parse_clamps_and_defaults(),
        test_weakest_dimensions_ordering(),
    ]
    ok = all(r is not False for r in results)  # pytest 风格 assert（None=通过）
    print("judge 离线自测:", "通过 ✅" if ok else "失败 ❌")
    sys.exit(0 if ok else 1)
