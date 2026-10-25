"""读 blind_eval 的 PAIRS_JSON 转储，给出比"一个总胜率"更经得起追问的读数。

为什么需要：n=18 的总胜率噪声有 ±15pp，单看总数既证不出改善也证不出退步。
这里补三件：①按人设分组（哪一类学生我们赢、哪一类输）；②bootstrap 95% CI
（把"同一批人设重复多次"的组内相关性算进去，比按独立二项算的 Wilson 更不乐观）；
③不经评委的具体度旁证（字数、条目数、数字处数、具名术语数、≤150 字条数）。

用法：python 输出/scripts/analyze_blind_pairs.py <含 PAIRS_JSON 的文本文件> [...]
"""
from __future__ import annotations

import json
import math
import pathlib
import random
import re
import statistics
import sys

from pairwise_note import partial_note


def load(path: pathlib.Path) -> list[dict]:
    txt = path.read_text(encoding="utf-8", errors="replace")
    marker = "=== PAIRS_JSON ==="
    if marker not in txt:
        raise SystemExit(f"{path}: 没有 {marker}（这一轮可能中途崩了）")
    line = txt.split(marker)[-1].strip().splitlines()[0]
    return json.loads(line)


def wilson(k: int, n: int) -> str:
    if not n:
        return "—"
    p, z = k / n, 1.96
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return f"[{max(0, c - h):.0%}, {min(1, c + h):.0%}]"


def boot_ci(pairs: list[dict]) -> str:
    """按人设整组重抽（同一人设的多次重复不独立），2000 次。"""
    groups: dict[str, list[int]] = {}
    for p in pairs:
        groups.setdefault(p["say"], []).append(1 if p["outcome"] == "2.0 胜" else 0)
    g = list(groups.values())
    if not g:
        return "—"
    rng = random.Random(7)
    rates = []
    for _ in range(2000):
        flat = [x for _ in range(len(g)) for x in rng.choice(g)]
        rates.append(sum(flat) / len(flat))
    rates.sort()
    return f"[{rates[int(.025 * len(rates))]:.0%}, {rates[int(.975 * len(rates))]:.0%}]"


def digs(s: str) -> int:
    return len(re.findall(r"[0-9]+", s))


def terms(s: str) -> int:
    return len({t.lower() for t in re.findall(r"[A-Za-z][A-Za-z0-9\-]{2,}", s)})


def avg(pairs: list[dict], key: str, sub: bool = False) -> str:
    """字段在这代转储里根本不存在 ⇒ 说「没测」，不拿默认值算出一个 0.00 冒充读数。
    （旧转储没有 yanban_items，`p[key]` 会当场崩、`p.get(key, [])` 会印 0.00，两种都错。）"""
    have = [p for p in pairs if key in p]
    if not have:
        return f"（这一代转储没有 {key} 字段 ⇒ 不印）"
    vals = [len(p[key]) if sub else p[key] for p in have]
    tail = "" if len(have) == len(pairs) else f"（缺 {len(pairs) - len(have)} 行，按 {len(have)} 行算）"
    return f"{statistics.mean(vals):.2f}{tail}"


def report(path: pathlib.Path) -> None:
    pairs = load(path)
    wins = sum(1 for p in pairs if p["outcome"] == "2.0 胜")
    losses = sum(1 for p in pairs if p["outcome"] == "裸问胜")
    ties = sum(1 for p in pairs if p["outcome"] == "相当")
    decided = wins + losses
    print(f"\n===== {path.name} =====")
    # 文件名里的 n 是这份运行**打算**测多少对；行数不等就是部分样本，必须自己喊出来。
    pn = partial_note(path, len(pairs))
    if pn:
        print(pn)
    rate = "未测（0 局判定）" if not decided else f"{wins / decided:.0%}"
    print(f"n={len(pairs)}：2.0 胜 {wins}／裸问胜 {losses}／相当 {ties}"
          f"｜胜率 {rate} Wilson {wilson(wins, decided)}"
          f"｜按人设 bootstrap {boot_ci(pairs)}")
    if not pairs:
        print("判读：0 行转储 ⇒ 结构指标全部不印（印出来就是拿空集合冒充读数）")
        return
    a, b = [len(p["yanban"]) for p in pairs], [len(p["plain"]) for p in pairs]
    print(f"字数 2.0 均值 {statistics.mean(a):.0f}（中位 {statistics.median(a):.0f}，≤150 字 {sum(1 for x in a if x <= 150)} 条）"
          f"｜裸问 {statistics.mean(b):.0f}")
    print(f"条目数 2.0={avg(pairs, 'yanban_items', sub=True)}"
          f"｜裸问={avg(pairs, 'plain_items', sub=True)}")
    print(f"数字处数 2.0={statistics.mean(digs(p['yanban']) for p in pairs):.1f}"
          f"｜裸问={statistics.mean(digs(p['plain']) for p in pairs):.1f}"
          f"　具名术语 2.0={statistics.mean(terms(p['yanban']) for p in pairs):.1f}"
          f"｜裸问={statistics.mean(terms(p['plain']) for p in pairs):.1f}")
    print("按人设：")
    groups: dict[str, list[dict]] = {}
    for p in pairs:
        groups.setdefault(p["say"], []).append(p)
    for say, ps in groups.items():
        w = sum(1 for p in ps if p["outcome"] == "2.0 胜")
        print(f"  {w}/{len(ps)} 胜｜{say[:44]}")


def dump_losing(path: pathlib.Path, n: int) -> None:
    """把输掉的对逐条摊开：根因只能从真实文本里读出来，评委的一句"更具体"不够。"""
    pairs = [p for p in load(path) if p["outcome"] == "裸问胜"]
    rng = random.Random(11)
    rng.shuffle(pairs)
    for k, p in enumerate(pairs[:n], 1):
        print(f"\n【落败 {k}/{len(pairs)}】学生：{p['say'][:90]}")
        print(f"  2.0（{len(p['yanban'])} 字，条目 {p.get('yanban_items', '（这代转储无该字段）')}）\n    "
              + p["yanban"][:640].replace("\n", "\n    "))
        print(f"  裸问（{len(p['plain'])} 字，条目 {p.get('plain_items', '（这代转储无该字段）')}）\n    "
              + p["plain"][:640].replace("\n", "\n    "))
        print(f"  评委：{p['judge_reason']}")


def main() -> int:
    args = [a for a in sys.argv[1:] if not a.startswith("--dump")]
    dump = 0
    for a in sys.argv[1:]:
        if a.startswith("--dump"):
            dump = int(a.split("=")[1]) if "=" in a else 3
    for arg in args:
        path = pathlib.Path(arg)
        report(path)
        if dump:
            dump_losing(path, dump)
    return 0


if __name__ == "__main__":
    sys.exit(main())
