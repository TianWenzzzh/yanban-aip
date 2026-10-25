"""读 g59 双份重复的配对盲评原始记录，只报聚合值。

为什么单独做一个脚本：盲评仪器本身已被判为不可信（换位置一致率 46%、编造引用锚点只抓到 30%），
所以这份读数的价值不在"胜率是多少"，而在**同一批情景重复两次会翻转多少**——
那是给以后任何配对读数定噪声底的量。
"""
from __future__ import annotations

import json
import math
import pathlib
import sys

from pairwise_note import partial_note

f = pathlib.Path(sys.argv[1])
recs = [json.loads(l) for l in f.read_text(encoding="utf-8").splitlines()
        if "strong_outcome" in l and l.strip().startswith("[")]
print(f"重复份数 {len(recs)}，逐份 n={[len(r) for r in recs]}")
pn = partial_note(f, sum(len(r) for r in recs))
if pn:
    print(pn)


def wilson(w: int, n: int) -> str:
    """返回成品字符串：n=0 时是「—」，不是 [0%, 0%]（一个假的零宽区间会被读成「确切为 0」）。"""
    if not n:
        return "—"
    p, z = w / n, 1.96
    d = 1 + z * z / n
    c = p + z * z / (2 * n)
    m = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    return f"[{(c - m) / d:.0%}, {(c + m) / d:.0%}]"


if not recs:
    print("判读：这个文件里没有一行带 strong_outcome 的转储 ⇒ 什么都没读到（RC=2），"
          "不等于「胜率为 0」")
    sys.exit(2)


def rate(w: int, n: int) -> str:
    """分母为 0 时这个统计量必须自己说「未测」——印 0% 会被读成「一局没赢」。"""
    return "未测（0 局判定）" if not n else f"{w / n:.0%}"


def section(key: str) -> None:
    """一个键一节。键在这份转储里压根不存在 ⇒ 「没比对过」，与「比对了但全是空判定」
    是两件事，必须分开印（合并成 0% 就是假读数）。"""
    hits = [rs for rs in recs if any(key in r for r in rs)]
    if not hits:
        print(f"{key}：这些转储里根本没有该键 ⇒ 不比对（不是 0 局）")
        return
    for j, rs in enumerate(hits):
        w = sum(1 for r in rs if r.get(key) == "2.0 胜")
        l = sum(1 for r in rs if r.get(key) and r.get(key) != "2.0 胜")
        print(f"{key} 重复{j + 1}: {w} 胜 / {w + l} 判负 → 胜率 {rate(w, w + l)} "
              f"Wilson {wilson(w, w + l)}")
    tot_w = sum(1 for rs in hits for r in rs if r.get(key) == "2.0 胜")
    tot_l = sum(1 for rs in hits for r in rs if r.get(key) and r.get(key) != "2.0 胜")
    print(f"{key} 合并: {tot_w}/{tot_w + tot_l} = {rate(tot_w, tot_w + tot_l)} "
          f"Wilson {wilson(tot_w, tot_w + tot_l)}")


for k in ("outcome", "strong_outcome"):
    section(k)

print("--- 同情景序号在两次重复间的判定漂移 ---")
if len(recs) < 2:
    print(f"只有 {len(recs)} 份重复 ⇒ 翻转率未测（不是 0%，是没有第二份可对照）")
else:
    for key in ("outcome", "strong_outcome"):
        a = [r[key] for r in recs[0] if key in r]
        b = [r[key] for r in recs[1] if key in r][: len(a)]
        if not b:
            print(f"{key}：两份里都没有该键 ⇒ 不比对")
            continue
        same = sum(1 for x, y in zip(a, b) if x == y)
        flip = sum(1 for x, y in zip(a, b) if (x == "2.0 胜") != (y == "2.0 胜"))
        print(f"{key}: 完全一致 {same}/{len(b)} 局；胜负翻转 {flip} 局"
              f"（= {flip / len(b):.0%} 的判定会因为重跑一次就换方向）")

print("--- 回复长度（自查：是否又是长度在驱动判定）---")
for j, rs in enumerate(recs):
    yl = [len(r.get("yanban", "")) for r in rs]
    sl = [len(r.get("strong", "")) for r in rs]
    pl = [len(r.get("plain", "")) for r in rs]
    med = lambda v: sorted(v)[len(v) // 2] if v else 0  # noqa: E731
    print(f"重复{j + 1} 中位长度 2.0={med(yl)} 强基线={med(sl)} 裸问={med(pl)}")
lc = [(len(r.get("yanban", "")) > len(r.get("strong", "")), r["strong_outcome"])
      for rs in recs for r in rs if r.get("strong_outcome")]
longer_win = sum(1 for x, o in lc if x and o == "2.0 胜")
longer_n = sum(1 for x, o in lc if x)
shorter_win = sum(1 for x, o in lc if not x and o == "2.0 胜")
shorter_n = sum(1 for x, o in lc if not x)
print(f"对强基线：2.0 更长时胜 {longer_win}/{longer_n}；更短时胜 {shorter_win}/{shorter_n}")
