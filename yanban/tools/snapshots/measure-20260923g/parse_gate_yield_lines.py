"""从 gate_yield 的**逐样本行**还原出汇总——用于中途被杀的长跑。

为什么要有这一台：`probe_gate_yield_v2.py` 的合计（含"平均几次模型调用"）只在收尾那一刻印，
中途杀掉就等于整轮读数作废（轮 115 我亲手把 384 条上限的跑法在第 58 条上停了）。
但逐样本行里已经有：长度、闸门前开火的键集、闸门后开火的键集、有没有计划。
除了模型调用次数，R1/R2/R3 都能从这些行还原出来。

用法：python 输出/scripts/parse_gate_yield_lines.py <stdout 或边车文件> [对拍文件...]
它会显式声明"调用次数无法还原"，不许把这份当成完整的 g96b。
"""
from __future__ import annotations

import pathlib
import re
import statistics
import sys

LINE = re.compile(r"^\[(\d+)\] 长度\s*(\d+)｜(?:卡\s*(\d+)｜)?闸门前 ([^｜]+)｜闸门后 ([^｜]+)｜计划=(\S+)")


def one(path: pathlib.Path) -> int:
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    rows = [m.groups() for m in (LINE.match(l) for l in lines) if m]
    print(f"\n=== {path.name}｜还原出 {len(rows)} 条样本（文件共 {len(lines)} 行）")
    if not rows:
        print("一条都没还原出来 —— 先确认这真是 gate_yield 的输出，别看下面空的 0 当读数")
        return 2
    n = len(rows)
    idx = [int(r[0]) for r in rows]
    if idx != list(range(idx[0], idx[0] + n)):
        print(f"⚠️ 序号不连续：{idx[0]}…{idx[-1]} 中间缺号 → 这份只能当部分样本")
    else:
        print(f"序号连续 {idx[0]}…{idx[-1]}")
    pre: dict[str, int] = {}
    post: dict[str, int] = {}
    for _, _, _, a, b, _ in rows:
        for k in (x.strip() for x in a.split(",")):
            if k and k != "无":
                pre[k] = pre.get(k, 0) + 1
        for k in (x.strip() for x in b.split(",")):
            if k and k != "无":
                post[k] = post.get(k, 0) + 1
    lengths = [int(r[1]) for r in rows]
    print(f"长度中位 {statistics.median(lengths):.0f} 字｜最长 {max(lengths)}｜"
          f"超过 1200 的 {sum(1 for x in lengths if x > 1200)}/{n}")
    print(f"闸门后仍带违规的回复：{sum(1 for r in rows if r[4] != '无')}/{n} = "
          f"{100 * sum(1 for r in rows if r[4] != '无') / n:.0f}%")
    # 卡数只有新版探针才印（旧证据文件里那一组是 None）：印得出就报分母，印不出就明说看不见
    got_cards = [int(r[2]) for r in rows if r[2] is not None]
    if got_cards:
        wc = sum(1 for c in got_cards if c)
        f = pre.get("no_citation", 0)
        print(f"带论文卡的回复：{wc}/{len(got_cards)}｜no_citation 开火 {f}"
              + (f" = {100 * f / wc:.0f}%（分母够）" if wc >= 8 else
                 f"（分母 {wc} < 8：**读不出代价**，也别读成「不会误伤」）"))
    else:
        print("这批行里没有卡数字段（探针加该列之前跑的）⇒ 带卡分母未知，"
              "`no_citation` 的 0 只能读成「看不见」，不能读成「没代价」")
    print("逐道闸门（闸门前 → 闸门后，率按还原出的分母算）：")
    for k in sorted(set(pre) | set(post)):
        a, b = pre.get(k, 0), post.get(k, 0)
        mark = "　✗ 变差" if b > a else ""
        print(f"  {k:<14} {a}/{n} = {100 * a / n:>3.0f}%  →  {b}/{n} = {100 * b / n:>3.0f}%{mark}")
    for k in ("too_long", "no_citation", "no_substance", "fake_title"):
        if k not in pre and k not in post:
            print(f"  {k:<14} 0/{n} =   0%  →  0/{n} =   0%　（一次没开火，不是没测）")
    print("⚠️ 这份**不含**模型调用次数：那一列只在收尾的合计里，判不了 R4。")
    return 0


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
