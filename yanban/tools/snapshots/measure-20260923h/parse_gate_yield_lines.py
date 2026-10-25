"""从 gate_yield 的**逐样本行**还原出汇总——用于中途被杀的长跑。

为什么要有这一台：`probe_gate_yield_v2.py` 的合计（含"平均几次模型调用"）只在收尾那一刻印，
中途杀掉就等于整轮读数作废（轮 115 我亲手把 384 条上限的跑法在第 58 条上停了）。
但逐样本行里已经有：长度、闸门前开火的键集、闸门后开火的键集、有没有计划。
除了模型调用次数，R1/R2/R3 都能从这些行还原出来。

用法：python 输出/scripts/parse_gate_yield_lines.py <stdout 或边车文件> [对拍文件...]
它会显式声明"调用次数无法还原"，不许把这份当成完整的 g96b。

轮 117：探针的逐样本行多了「（前置 N）」与「｜接地 g/c」两列，正则改成命名字段并**按两档还原代价**；
同时和文件里探针自己印的「代价·…」汇总行对账 —— 复刻一份汇总而不比对，就是幻影读数的入口。
"""
from __future__ import annotations

import pathlib
import re
import statistics
import sys

LINE = re.compile(r"^\[(?P<idx>\d+)\] 长度\s*(?P<post>\d+)(?:（前置\s*(?P<pre>\d+)）)?"
                  r"｜(?:卡\s*(?P<cards>\d+)｜)?闸门前 (?P<before>[^｜]+)｜闸门后 (?P<after>[^｜]+)"
                  r"(?:｜接地 (?P<grounded>\d+)/(?P<cited>\d+))?｜计划=(?P<plan>\S+)")
TIER = re.compile(r"^代价·(?P<label>[^：]+)：(?P<rows>\d+) 条｜no_citation 开火 (?P<fired>\d+)/(?P<total>\d+)"
                  r"(?: = \d+%)?｜接地 (?P<grounded>\d+)/(?P<cited>\d+)")
TIERS = (("实质回复（前置 ≥200 字）", lambda p: p >= 200),
         ("过场句（前置 <200 字）", lambda p: p < 200))


def one(path: pathlib.Path) -> int:
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    rows = [m.groupdict() for m in (LINE.match(l) for l in lines) if m]
    print(f"\n=== {path.name}｜还原出 {len(rows)} 条样本（文件共 {len(lines)} 行）")
    if not rows:
        print("一条都没还原出来 —— 先确认这真是 gate_yield 的输出，别看下面空的 0 当读数")
        return 2
    idx = [int(r["idx"]) for r in rows]
    bad = 0
    if idx != list(range(idx[0], idx[0] + len(rows))):
        print(f"⚠️ 序号不连续：{idx[0]}…{idx[-1]} 中间缺号 → 这份只能当部分样本")
    else:
        print(f"序号连续 {idx[0]}…{idx[-1]}")
    pre: dict[str, int] = {}
    post: dict[str, int] = {}
    for r in rows:
        for k in (x.strip() for x in r["before"].split(",")):
            if k and k != "无":
                pre[k] = pre.get(k, 0) + 1
        for k in (x.strip() for x in r["after"].split(",")):
            if k and k != "无":
                post[k] = post.get(k, 0) + 1
    n = len(rows)
    lengths = [int(r["post"]) for r in rows]
    print(f"长度中位 {statistics.median(lengths):.0f} 字｜最长 {max(lengths)}｜"
          f"超过 1200 的 {sum(1 for x in lengths if x > 1200)}/{n}")
    print(f"闸门后仍带违规的回复：{sum(1 for r in rows if r['after'] != '无')}/{n} = "
          f"{100 * sum(1 for r in rows if r['after'] != '无') / n:.0f}%")
    # 卡数只有新版探针才印（旧证据文件里那一列是 None）：印得出就报分母，印不出就明说看不见
    typed = [r for r in rows if r["cards"] is not None]
    if typed:
        wc = sum(1 for r in typed if int(r["cards"]))
        f = pre.get("no_citation", 0)
        print(f"带论文卡的回复：{wc}/{len(typed)}｜no_citation 开火 {f}"
              + (f" = {100 * f / wc:.0f}%（分母够）" if wc >= 8 else
                 f"（分母 {wc} < 8：**读不出代价**，也别读成「不会误伤」）"))
    else:
        print("这批行里没有卡数字段（探针加该列之前跑的）⇒ 带卡分母未知，"
              "`no_citation` 的 0 只能读成「看不见」，不能读成「没代价」")
    # 两档代价：与探针的 tier_report 同口径（有卡 + 按「前置」长度切档）。
    # 关键是下面那次对账：这台还原器和探针自己的汇总不一致就得作废，别让两份数各说一遍。
    cost = [r for r in typed if r["pre"] is not None and int(r["cards"])]
    own_lines = {m.group("label"): m.groupdict() for l in lines if (m := TIER.match(l))}
    if not cost:
        print("这批行里没有「前置」列或没有带卡样本 ⇒ 两档代价无从还原")
    else:
        for label, keep in TIERS:
            tier = [r for r in cost if keep(int(r["pre"]))]
            if not tier:
                print(f"还原·{label}：0 条 ⇒ 无读数")
                continue
            fired_t = [r for r in tier if "no_citation" in r["before"]]
            g = sum(int(r["grounded"] or 0) for r in tier)
            c = sum(int(r["cited"] or 0) for r in tier)
            got = f"{len(tier)} 条｜开火 {len(fired_t)}｜接地 {g}/{c}"
            own = own_lines.get(label)
            if not own:
                print(f"还原·{label}：{got}（文件里没有这档的汇总行，无从对账）")
                continue
            same = (int(own["rows"]) == len(tier) and int(own["fired"]) == len(fired_t)
                    and int(own["grounded"]) == g and int(own["cited"]) == c)
            bad |= not same
            print(f"还原·{label}：{got}｜探针汇总 {own['rows']} 条｜开火 {own['fired']}"
                  f"｜接地 {own['grounded']}/{own['cited']} → "
                  + ("✅ 对上了" if same else "❌ 对不上：这台还原器与探针口径已分叉，这份数作废"))
    print("逐道闸门（闸门前 → 闸门后，率按还原出的分母算）：")
    for k in sorted(set(pre) | set(post)):
        a, b = pre.get(k, 0), post.get(k, 0)
        mark = "　✗ 变差" if b > a else ""
        print(f"  {k:<14} {a}/{n} = {100 * a / n:>3.0f}%  →  {b}/{n} = {100 * b / n:>3.0f}%{mark}")
    for k in ("too_long", "no_citation", "no_substance", "fake_title"):
        if k not in pre and k not in post:
            print(f"  {k:<14} 0/{n} =   0%  →  0/{n} =   0%　（一次没开火，不是没测）")
    print("⚠️ 这份**不含**模型调用次数：那一列只在收尾的合计里，判不了 R4。")
    return bad


def main() -> int:
    paths = [pathlib.Path(a) for a in sys.argv[1:] if not a.startswith("--")]
    if not paths:
        print("用法：parse_gate_yield_lines.py <gate_yield 的 stdout/边车文件> ...", file=sys.stderr)
        return 2
    bad = 0
    for p in paths:
        if not p.is_file():
            print(f"缺文件 {p}", file=sys.stderr)
            bad = 1
            continue
        bad |= one(p)
    return bad


if __name__ == "__main__":
    sys.exit(main())
