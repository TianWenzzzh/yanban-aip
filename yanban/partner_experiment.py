"""研伴·实验设计 Partner（8032）。研究问题+数据描述 → 结构化实验方案（GLM）。"""

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from llm import chat  # noqa: E402
from partner_base import create_partner_app  # noqa: E402

AIC = "1.2.156.3088.1.1.YNBAN1.EXP001.1.0000"

SYSTEM = (
    "你是严谨的实验设计专家。根据研究问题与数据条件，输出 Markdown 实验方案，必须包含："
    "## 研究假设（H1/H0）\n## 变量设计（自变量/因变量/控制变量，表格）\n## 实验分组与对照设置\n"
    "## 评估指标（主指标+次要指标，给出计算方式）\n## 样本量与统计功效说明\n## 潜在风险与缓解措施。"
    "内容必须针对用户的具体研究问题定制，不要泛泛而谈。控制在 800 字内。"
)


async def run_skill(text: str) -> str:
    return await chat(text, system=SYSTEM, temperature=0.3, max_tokens=2048)


def needs_input(text: str) -> bool:
    return len(text.strip()) < 12


app = create_partner_app(
    name="experiment", aic=AIC, port=8032, run_skill=run_skill,
    needs_input=needs_input, input_hint="请描述研究问题与可用数据条件（至少 12 字），例如：'对比 RAG 与微调在医疗问答的准确率，有 1000 条标注数据'。",
)
