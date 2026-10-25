"""检查"带真实引用角标"这一件事本身在盲评里是加分还是减分。

动机：评委败因词频里反复出现「无编造痕迹」，而 2.0 唯一的结构性优势就是回复里内联了
真检索到的论文标题与 [n] 角标。如果带角标的那批对局胜率反而更低，那问题不在"引用得不够"，
而在**引用的写法让回复变得谨慎、割裂、字数被挤掉**——修法会完全不同（改呈现，不是加引用）。

读数：按"我方回复是否含 [n] 角标 / 是否内联论文标题"两种切法各给一次胜率与 Wilson CI，
再补一个"我方是否提到《书名号》标题"的切法。全部在既有转储上算，不花任何模型调用。

用法：python 输出/scripts/probe_citation_effect.py <含 PAIRS_JSON 的文本文件> [...]
"""
from __future__ import annotations

import json
import math
import pathlib
import re
import sys

from pairwise_note import partial_note

MARKER = "=== PAIRS_JSON ==="


def wilson(w: int, n: int, z: float = 1.96) -> tuple[float, float]:
    if n == 0:
        return (float("nan"), float("nan"))
    p = w / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return (max(0.0, c - h), min(1.0, c + h))


def load(path: pathlib.Path) -> list[dict]:
    txt = path.read_text(encoding="utf-8", errors="replace")
    if MARKER not in txt:
        raise SystemExit(f"{path}: 没有 {MARKER}")
    return json.loads(txt.split(MARKER, 1)[1].strip().splitlines()[0])


def show(label: str, rows: list[dict]) -> None:
    w = sum(1 for r in rows if "2.0" in str(r.get("outcome", "")))
    n = len(rows)
    if not n:
        print(f"   {label}：0 对")
        return
    lo, hi = wilson(w, n)
    lens = [len(str(r.get("yanban", ""))) for r in rows]
    print(f"   {label}：{w}/{n} = {100 * w / n:.0f}% [{100 * lo:.0f}%, {100 * hi:.0f}%]"
          f"｜我方长度中位 {sorted(lens)[n // 2]}")


def main() -> int:
    paths = [pathlib.Path(a) for a in sys.argv[1:]]
    if not paths:
        print(__doc__)
        return 2
    rows = []
    for p in paths:
        got = load(p)
        pn = partial_note(p, len(got))
        if pn:
            print(pn)
        rows += got
    anchor = lambda r: bool(re.search(r"\[\d{1,2}\]", str(r.get("yanban", ""))))
    title = lambda r: "《" in str(r.get("yanban", ""))
    print(f"合并 {len(rows)} 对")
    show("带 [n] 角标", [r for r in rows if anchor(r)])
    show("不带角标", [r for r in rows if not anchor(r)])
    show("带《标题》", [r for r in rows if title(r)])
    show("不带《标题》", [r for r in rows if not title(r)])
    show("两者都带", [r for r in rows if anchor(r) and title(r)])
    show("两者都不带", [r for r in rows if not anchor(r) and not title(r)])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
