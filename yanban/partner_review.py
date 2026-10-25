"""研伴·模拟审稿 Partner（8035）。草稿 → 3 审稿人意见 + Meta 建议（演示核心）。"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from llm import chat  # noqa: E402
from partner_base import create_partner_app  # noqa: E402

AIC = "1.2.156.3088.1.1.YNBAN1.REV001.1.0000"

SYSTEM = (
    "你是模拟评审委员会：R1 方法审稿人（挑实验设计/统计问题）、R2 写作审稿人（挑表达/结构/规范问题）、"
    "R3 贡献审稿人（评创新性/显著性/定位）。对用户提交的草稿输出 Markdown：\n"
    "## R1 方法意见（主要问题 2 条+次要 1 条，评分 x/10）\n"
    "## R2 写作意见（同上）\n## R3 贡献意见（同上）\n"
    "## Meta 结论（接收/小修/大修/拒稿，一句话理由 + 3 条最优先修改项）。\n"
    "意见必须具体引用草稿原文短语，不得空泛。"
)


async def run_skill(text: str) -> str:
    return await chat(text, system=SYSTEM, temperature=0.4, max_tokens=2500)


def needs_input(text: str) -> bool:
    return len(text.strip()) < 50


app = create_partner_app(
    name="review", aic=AIC, port=8035, run_skill=run_skill,
    needs_input=needs_input, input_hint="请粘贴至少 50 字的草稿文本（摘要/段落/全文均可），我将给出 3 位审稿人意见与 Meta 结论。",
)
