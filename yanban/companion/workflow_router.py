"""自适应工作流路由（2.0 迭代新增）：按用户输入分流到最佳执行路径。

类型判定（规则优先 + 可选 LLM 兜底）：
- quick_answer   快问快答：概念解释、比较、常识——直接 LLM 回答，不起编排
- search         检索型：找论文/文献——Companion 认知层检索
- survey         综述型：领域综述/现状梳理——检索偏 survey/review + 结构化输出
- experiment     实验设计型：设计实验/方案——既有多智能体工作流（experiment Partner）
- writing        写作型：写一段/综述章节——既有多智能体工作流（writing Partner）
- interview      信息不足：走反问（认知层）
"""
from __future__ import annotations

import re

RULES: list[tuple[str, list[str]]] = [
    ("experiment", [r"设计.*实验", r"实验(方案|设计|步骤)", r"提出.*方法", r"复现方案", r"做.*实验验证"]),
    ("writing", [r"写(一|这|一封)?(段|篇|章|节)?(相关)?工作", r"撰写|起草|润色|论文的.*部分", r"帮我写"]),
    ("survey", [r"综述|研究现状|领域地图|梳理.*方向|文献综述|梳理发展"]),
    ("search", [r"检索|查找|找.{0,4}论文|文献|有哪些.*(论文|文献)|帮我找"]),
    ("quick_answer", [r"什么是|为什么|区别|对比一下|解释|介绍|原理", r"怎么样|如何选择"]),
]


def classify(message: str) -> str:
    """返回工作流类型。命中顺序即优先级；未命中默认 search（Companion 主流程）。"""
    text = (message or "").strip()
    if not text:
        return "search"
    for wtype, patterns in RULES:
        for pat in patterns:
            if re.search(pat, text):
                return wtype
    # 兜底启发：问句且很短 → 快答；含"文献/论文/研究" → 检索
    if text.endswith("？") or text.endswith("?") or len(text) < 14:
        return "quick_answer"
    return "search"


def route_plan(message: str, confidence: float, topic_known: bool) -> dict:
    """综合判定：结合认知层置信度决定最终路由。

    返回 {workflow, reason, use_companion, use_orchestrator}
    """
    wtype = classify(message)
    plan = {
        "workflow": wtype,
        "reason": "",
        "use_companion": True,
        "use_orchestrator": False,
    }
    if wtype == "interview" or (confidence < 0.62 and not topic_known and wtype in ("search", "survey")):
        plan.update(workflow="interview", reason="信息不足：先反问补齐 ResearchIntent 再行动")
    elif wtype in ("experiment", "writing"):
        plan.update(use_orchestrator=True,
                    reason=f"该请求适合既有多智能体工作流（{wtype} Partner 承担）")
    elif wtype == "quick_answer":
        plan.update(use_companion=True, reason="快问快答：直接由研伴回答，无需编排")
    elif wtype == "survey":
        plan["reason"] = "综述型：检索将偏 survey/review 并输出结构化梳理"
    else:
        plan["reason"] = "检索型：Companion 认知层多源检索"
    return plan
