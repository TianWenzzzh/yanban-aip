"""研伴·数据分析 Partner（8033）。

输入：自然语言分析目标 + ```csv 代码块包裹的数据（表头+数值列）。
真实统计：纯 Python 计算描述统计与相关系数（可手算复核），GLM 只负责解读。
"""

import io
import re
import statistics
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from llm import chat  # noqa: E402
from partner_base import create_partner_app  # noqa: E402

AIC = "1.2.156.3088.1.1.YNBAN1.ANA001.1.0000"


def _extract_csv(text: str) -> str:
    m = re.search(r"```csv\s*(.*?)```", text, re.S)
    return m.group(1).strip() if m else ""


def _to_float(v: str) -> float | None:
    try:
        return float(v)
    except ValueError:
        return None


def _pearson(xs: list[float], ys: list[float]) -> float:
    n = len(xs)
    if n < 2 or n != len(ys):
        return float("nan")
    mx, my = statistics.mean(xs), statistics.mean(ys)
    cov = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    sx = (sum((x - mx) ** 2 for x in xs)) ** 0.5
    sy = (sum((y - my) ** 2 for y in ys)) ** 0.5
    return cov / (sx * sy) if sx and sy else float("nan")


def real_stats(csv_text: str) -> dict:
    """对 CSV 每个数值列做描述统计；数值列两两做皮尔逊相关（列数<=6）。"""
    rows = list(csv.reader(io.StringIO(csv_text)))
    rows = [r for r in rows if r]
    if len(rows) < 2:
        raise ValueError("CSV 至少需要表头+1 行数据")
    header = [h.strip() for h in rows[0]]
    cols: dict[str, list[float]] = {h: [] for h in header}
    for row in rows[1:]:
        for h, cell in zip(header, row):
            v = _to_float(cell.strip())
            if v is not None:
                cols[h].append(v)
    stats: dict = {}
    for h, vals in cols.items():
        if len(vals) >= 2:
            stats[h] = {
                "n": len(vals), "mean": round(statistics.mean(vals), 4),
                "stdev": round(statistics.stdev(vals), 4),
                "min": min(vals), "max": max(vals),
                "median": round(statistics.median(vals), 4),
            }
    names = [h for h in stats]
    corr = {}
    for i in range(len(names)):
        for j in range(i + 1, len(names)):
            xs = cols[names[i]]
            ys = cols[names[j]]
            n = min(len(xs), len(ys))
            r = _pearson(xs[:n], ys[:n])
            if r == r:  # 过滤 NaN
                corr[f"{names[i]}~{names[j]}"] = round(r, 4)
    return {"per_column": stats, "pearson": corr}


import csv  # noqa: E402


async def run_skill(text: str) -> str:
    csv_text = _extract_csv(text)
    if not csv_text:
        raise ValueError("输入中缺少 ```csv 代码块（表头+数据行）")
    result = real_stats(csv_text)
    import json

    interpretation = await chat(
        f"用户分析目标：{text[:400]}\n\n真实统计结果（Python 标准库计算）：\n{json.dumps(result, ensure_ascii=False, indent=1)}\n\n"
        "请基于以上数字写 200 字内分析解读：数据概况、关键发现、相关性含义、注意事项。不得编造数字。",
        system="你是数据分析专家，只引用给定数字。",
        temperature=0.2,
        max_tokens=800,
    )
    return f"## 真实统计结果\n```json\n{json.dumps(result, ensure_ascii=False, indent=1)}\n```\n## 解读\n{interpretation}"


def needs_input(text: str) -> bool:
    return "```csv" not in text


app = create_partner_app(
    name="analysis", aic=AIC, port=8033, run_skill=run_skill,
    needs_input=needs_input, input_hint="请提供 ```csv 代码块（表头+数值行）和分析目标。示例：\n```csv\nx,y\n1,2\n2,4\n```",
)
