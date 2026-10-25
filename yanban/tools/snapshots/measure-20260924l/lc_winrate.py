"""把配对盲评转储重算成"长度受控"的读数，并直接量出评委自己的长度偏置。

为什么要这个脚本：方向 B 的调研给出一手证据——LLM 评委对长回复的系统性偏好量级达 +0.24~0.44 分，
去掉长度后评委与人类 Arena 偏好的一致性从 .94 升到 .98（LC-AlpacaEval）。
而我方回复长度只有对手的 0.43—0.75 倍，所以"17% 还是 32%"这两个数都可能主要是长度读数，
不是质量读数。不先把这条混淆消掉，后面任何一轮改动的归因都不可信。

三件事：
① 原始胜率 + Wilson 95% CI；
② 长度比按分位的胜率表，以及"只保留长度可比对（0.8≤我方/对手≤1.25）"的受控胜率；
   再加一个按合并长度比分布做直接标准化（standardization）的胜率——它回答"如果两边一样长会怎样"；
③ 评委长度偏置本身：所有对局里"更长的那一条获胜"的比例（不区分哪个系统），
   以及按长度比排序的单调性。这条是评委仪器的体检，与产品无关。

用法：python 输出/scripts/lc_winrate.py <含 PAIRS_JSON 的文本文件> [...]
"""
from __future__ import annotations

import json
import math
import pathlib
import statistics
import sys

from pairwise_note import partial_note

MARKER = "=== PAIRS_JSON ==="
BANDS = ((0.0, 0.5), (0.5, 0.75), (0.75, 1.0), (1.0, 1.25), (1.25, 9.9))


def load(path: pathlib.Path) -> list[dict]:
    txt = path.read_text(encoding="utf-8", errors="replace")
    if MARKER not in txt:
        raise SystemExit(f"{path}: 没有 {MARKER}")
    return json.loads(txt.split(MARKER, 1)[1].strip().splitlines()[0])


def wilson(w: int, n: int, z: float = 1.96) -> tuple[float, float]:
    if n == 0:
        return (float("nan"), float("nan"))
    p = w / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return (max(0.0, c - h), min(1.0, c + h))


def fmt(w: int, n: int) -> str:
    if n == 0:
        return "0 对"
    lo, hi = wilson(w, n)
    return f"{w}/{n} = {100 * w / n:.0f}% [{100 * lo:.0f}%, {100 * hi:.0f}%]"


def main() -> int:
    paths = [pathlib.Path(a) for a in sys.argv[1:]]
    if not paths:
        print(__doc__)
        return 2
    rows: list[dict] = []
    for p in paths:
        got = load(p)
        pn = partial_note(p, len(got))
        if pn:
            print(pn)
        rows += got
    pairs = [(len(str(r.get("yanban", ""))), len(str(r.get("plain", ""))),
              "2.0" in str(r.get("outcome", "")), str(r.get("_src", p.name)))
             for r in rows]
    scored = [(a, b, "2.0" in str(r.get("outcome", ""))) for r in rows
              for a, b in [(len(str(r.get("yanban", ""))), len(str(r.get("plain", ""))))]]
    wins = sum(1 for _, _, w in scored if w)
    print(f"合并 {len(scored)} 对（来自 {len(paths)} 份转储）")
    print(f"① 原始胜率：{fmt(wins, len(scored))}")

    print("\n② 我方/对手长度比 → 胜率（分桶）")
    dist = {}
    for lo, hi in BANDS:
        sub = [w for a, b, w in scored if lo <= a / max(1, b) < hi]
        dist[(lo, hi)] = len(sub)
        if sub:
            print(f"   [{lo:.2f},{hi:.2f})  {fmt(sum(sub), len(sub))}")
        else:
            print(f"   [{lo:.2f},{hi:.2f})  0 对")
    matched = [w for a, b, w in scored if 0.8 <= a / max(1, b) <= 1.25]
    print(f"   长度可比对（0.80—1.25）：{fmt(sum(matched), len(matched))}")

    # 直接标准化：用"全体对局的长度比分布"当目标人群，把每桶胜率按该人群加权。
    # 因为桶权重取自同一份数据，它只消掉"我这批样本恰好短回复多"这一层，不消掉评委偏置本身。
    print("\n③ 按全体长度比分布做直接标准化的胜率（消掉样本配重，评委偏置仍在）")
    num = den = 0.0
    for key, n_bucket in dist.items():
        sub = [w for a, b, w in scored if key[0] <= a / max(1, b) < key[1]]
        if not sub:
            continue
        num += (sum(sub) / len(sub)) * n_bucket
        den += n_bucket
    if den:
        print(f"   标准化后 ≈ {100 * num / den:.0f}%（权重：{ {f'{k[0]}-{k[1]}': v for k, v in dist.items() if v} }）")

    print("\n④ 评委仪器的长度偏置体检（不区分系统：更长那条赢了多少）")
    longer_win = sum(1 for a, b, w in scored if (a >= b and w) or (a < b and not w))
    same = sum(1 for a, b, _ in scored if a == b)
    print(f"   {fmt(longer_win, len(scored) - same)} 的对局里，更长的那一条获胜")
    print(f"   我方更长的对局：{fmt(sum(1 for a, b, w in scored if a >= b and w), sum(1 for a, b, _ in scored if a >= b))}"
          f"｜我方更短的对局：{fmt(sum(1 for a, b, w in scored if a < b and w), sum(1 for a, b, _ in scored if a < b))}")
    print(f"   长度比中位 {statistics.median([a / max(1, b) for a, b, _ in scored]):.2f}"
          f"｜我方长度中位 {statistics.median([a for a, _, _ in scored]):.0f}"
          f"｜对手长度中位 {statistics.median([b for _, b, _ in scored]):.0f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
