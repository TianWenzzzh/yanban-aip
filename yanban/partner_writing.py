"""研伴·论文写作 Partner（8034）。要点列表 → 学术语体段落（含引用占位）。"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from llm import chat  # noqa: E402
from partner_base import create_partner_app  # noqa: E402

AIC = "1.2.156.3088.1.1.YNBAN1.WRT001.1.0000"

SYSTEM = (
    "你是学术论文写作专家。把用户给的要点扩写为学术语体段落（Markdown）："
    "逻辑连贯、术语准确、被动语态适度；引用位置用 [作者, 年份] 占位；"
    "开头给出该段落在论文中的建议位置（如 §2 相关工作）；控制在 500 字内。"
)


async def run_skill(text: str) -> str:
    return await chat(text, system=SYSTEM, temperature=0.5, max_tokens=1500)


def needs_input(text: str) -> bool:
    return len(text.strip()) < 15


app = create_partner_app(
    name="writing", aic=AIC, port=8034, run_skill=run_skill,
    needs_input=needs_input, input_hint="请提供写作要点（至少 15 字），例如：'写相关工作段落：要点1 MCP解决工具接入；要点2 A2A解决智能体协作；要点3 两者互补需桥接'。",
)
