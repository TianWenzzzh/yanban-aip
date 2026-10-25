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
# 两档的名字只写一遍（上面那一条）：判据里再抄一份字面量，改档名时判分器会安静地永远判"没这档"
SUB, TRA = TIERS[0][0], TIERS[1][0]
MIN_N = 8
# g98 的身份：轮 117 预登记写死 19 个提问 × 4 轮 = 76 条。换池子必须同时改这两个数，
# 否则 Q0 会把"跑错情景集"当成一次正常读数放过去。
EXPECT_CASES, EXPECT_REPLIES = 19, 76
# 下面几条只出现在探针**收尾**的汇总里。g98 的判据（轮 117 预登记 Q0—Q6）就是按这些行写的，
# 所以判分也由这台机器做，不许我事后拿逐样本行手算——部分样本手算已错过两次。
POOL = re.compile(r"^用例池：(\d+) 行 → 去重后 (\d+) 个提问")
CAP = re.compile(r"^探针上限：(\d+) 情景 × (\d+) 轮.*? = (\d+) 条回复")
MED = re.compile(r"长度中位 (?P<pre>\d+) → (?P<post>\d+) 字")
CALLS = re.compile(r"样本：(?P<n>\d+) 次回复，(?P<calls>\d+) 次模型调用（平均 (?P<avg>[\d.]+) 次/回复）")


def one(path: pathlib.Path, stat: dict | None = None) -> int:
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
    # 探针遇到空档印的是「这一档一条都没有 ⇒ 无读数的维度，不判」，不匹配 TIER 那条正则。
    # 不认这一行，"两边都说这档是 0" 这种**真的对上**就会被算成"没比对过"（g98 第一判就这么被我拒掉的）。
    own_empty = {l.split("：")[0][3:] for l in lines
                 if l.startswith("代价·") and "这一档一条都没有" in l}
    rec = 0
    if not cost:
        print("这批行里没有「前置」列或没有带卡样本 ⇒ 两档代价无从还原")
    else:
        for label, keep in TIERS:
            tier = [r for r in cost if keep(int(r["pre"]))]
            if not tier:
                both_empty = label in own_empty
                rec += int(both_empty)
                print(f"还原·{label}：0 条"
                      + ("｜探针也说这一档一条都没有 ⇒ ✅ 对上了（两边都空，这档确实没有对象）"
                         if both_empty else "（文件里没有这档的汇总行，无从对账）"))
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
            rec += int(same)
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
    got = next((m for l in lines if (m := CALLS.search(l))), None)
    print(f"这份含收尾那行 ⇒ 调用次数判得了（Q6/R4）：{got.group(0)}" if got else
          "⚠️ 这份**不含**模型调用次数：那一列只在收尾的合计里，判不了 R4。")
    if stat is not None:
        stat["reconciled"] = rec
    return bad


def judge(path: pathlib.Path, recon: str) -> int:
    """按轮 117 **跑前写死**的 Q0—Q6 判这份 g98，结论由机器印，不由我事后手算。

    `recon` 由调用方给出：`ok`（两档都比对过且一致）/ `mismatch`（比对过但对不上）/
    `not-comparable`（旧行格式没有「前置」列，或这批没有带卡样本 ⇒ 压根没比）。
    **"没比对"绝不能印成"比对通过"** —— Q5 唯一容易被糊过去的就是这里。
    只读探针收尾真会打印的行（Q0 池子自证、Q1/Q3 两档开火率、Q2 长度中位、Q4 变差、Q6 调用数）；
    任何一档分母 <8 一律记「未测」——两侧都要最小分母是轮 106 立的规矩。
    """
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    print(f"\n=== 判据 Q0—Q6（预登记于轮 117，跑完不许改口径）｜{path.name}")
    tiers: dict[str, dict] = {}
    cur = None
    for l in lines:
        m = TIER.match(l)
        if m:
            cur = tiers.setdefault(m.group("label"), {})
            cur.update(rows=int(m.group("rows")), fired=int(m.group("fired")),
                       total=int(m.group("total")), cited=int(m.group("cited")))
            continue
        if l.startswith("代价·") and "这一档一条都没有" in l:
            cur = tiers.setdefault(l.split("：")[0][3:], {"rows": 0, "fired": 0, "total": 0, "cited": 0})
            continue
        m = MED.search(l)
        if m and cur is not None:
            cur["pre_med"], cur["post_med"] = int(m.group("pre")), int(m.group("post"))
    pool, cap = next((m for l in lines if (m := POOL.search(l))), None), \
        next((m for l in lines if (m := CAP.search(l))), None)
    calls = next((m for l in lines if (m := CALLS.search(l))), None)

    # Q5 先判：对账不过 ⇒ 后面的数都是幻影，一条都不许引用
    if recon == "mismatch":
        print("Q5 对账 ❌：两档还原与探针自印汇总对不上 ⇒ 这份**作废**，Q0—Q4/Q6 不判"
              "（判了就是把两套口径的均值当结论）")
        return 2
    if recon != "ok":
        print("Q5 对账 ⚠️：两档压根没比过（轮 117 之前的行没有「前置」列，或这批没有带卡样本）⇒ 不判。"
              "「看不见」不是「对上了」")
        return 2
    if any("❌ 结构不可能" in l for l in lines):
        print("Q5 结构不变量 ❌（接地数 > 带角标句数）⇒ 仪器与产品口径已分叉，这份**作废**")
        return 2
    print("Q5 对账 ✅（两档都逐条还原过，且与探针自印汇总一致）")

    ok0 = bool(pool and cap and int(pool.group(2)) == EXPECT_CASES and int(cap.group(3)) == EXPECT_REPLIES)
    print(f"Q0 输入自证 {'✅' if ok0 else '❌'}：池子 {pool and pool.group(0)}｜上限 {cap and cap.group(0)}"
          + ("" if ok0 else f" ⇒ 不是预登记的 g98（应当 {EXPECT_CASES} 个提问、{EXPECT_REPLIES} 条回复），不判"))
    if not ok0:
        return 2

    # **两把尺的口径差**（读代码读出来的，不是猜的）：闸门用 `_CITE_TRACE = \[\d{1,2}\]|【\d{1,2}】|《`
    # （mentor.py:802），而 `grounding_report` 只数"句子里有 `[n]` 或《》且该句 ≥8 字"。
    # 于是"计数尺读到 0 句带角标"完全可能对应"闸门认为已经挂了来源"（如整条只用【1】【2】）。
    # Q3 的前提是"这一档没有可治对象"，拿计数尺的 0 去当那个 0 就会**收窄错方向**。
    rows = [m.groupdict() for m in (LINE.match(l) for l in lines) if m]
    blind = [r for r in rows if r["cards"] and int(r["cards"]) and not int(r["cited"] or 0)
             and "no_citation" not in (r["before"] or "")]
    print(f"口径核对：卡>0、计数尺读到 0 句、闸门却没开火 ⇒ {len(blind)}/{len(rows)} 条 ｜ "
          + ("两把尺一致 ⇒ Q3 可以拿「带角标句 0 条」当证据" if not blind else
             "**两把尺口径不一致** ⇒ Q3 不许据它下「没有可治对象」"))

    verdict = None
    worse = [l for l in lines if "✗ 变差" in l]
    if worse:                                   # Q4：任何一道闸门改坏 ⇒ 先修它，别谈收益
        print(f"Q4 回退触发 ❌：{len(worse)} 行「✗ 变差」⇒ 先修闸门，本轮不判留下/收窄")
        verdict = "先修闸门（Q4）"
    real, trans = tiers.get(SUB), tiers.get(TRA)
    got2 = got3 = False           # 两档各是否"读得出结论"——两侧都要最小分母（轮 106）
    if real is None or trans is None:
        missing = "、".join(n for n, t in ((SUB, real), (TRA, trans)) if t is None)
        print(f"文件里缺汇总行：{missing} ⇒ 这台仪器没跑到收尾，或探针换了措辞（判分器要先修）")
    else:
        t = real
        head = (f"Q1/Q2 实质回复档：{t['total']} 条｜开火 {t['fired']}/{t['total']} = "
                f"{100 * t['fired'] / t['total']:.0f}%｜带角标句 {t['cited']} 条")
        if t["total"] < MIN_N:
            print(head + f" ⇒ 分母 <{MIN_N} ⇒ **未测**（不写「没有代价」，也不写「不会误伤」）")
        elif t["fired"] < MIN_N:
            print(head + f" ⇒ 开火 {t['fired']} 条 <{MIN_N} ⇒ 按预登记**只作个案描述、不判显著**")
        elif t.get("post_med") is None:
            print(head + " ⇒ 没有「开火那 N 条」那行 ⇒ Q2 未测")
        else:
            got2 = True
            ok2 = t["post_med"] >= t["pre_med"] * 0.85
            print(head + f"｜长度中位 {t['pre_med']} → {t['post_med']} ⇒ Q2 下降不超 15% "
                  + ("✅" if ok2 else "❌"))
            if not ok2:
                verdict = verdict or "回退（Q2 代价超线）"
        t = trans
        rate = 100 * t["fired"] / t["total"] if t["total"] else 0.0
        head = (f"Q1/Q3 过场句档：{t['total']} 条｜开火 {t['fired']}/{t['total']} = {rate:.0f}%"
                f"｜带角标句 {t['cited']} 条")
        if t["total"] == 0:
            print(head + " ⇒ 这一档 **0 条**：池子里带卡的回复全在 ≥200 字那档 ⇒ "
                  "「闸门在过场句上误伤」这一风险本轮**无从发作**（是计数层面的上界，不等于「不会误伤」）")
        elif t["total"] < MIN_N:
            print(head + f" ⇒ 分母 <{MIN_N} ⇒ **未测**")
        else:
            got3 = True
            mis = rate > 50 and t["cited"] == 0
            if mis and blind:
                print(head + f" ⇒ Q3 **不判**：这一档确有 {len(blind)} 条"
                      "「计数尺读到 0 句、闸门却按自己的宽口径认已挂来源」⇒「没有可治对象」这个前提不成立"
                      "（先让两台尺同口径，再来判收窄）")
                got3 = False
            else:
                print(head + f" ⇒ Q3 {'闸门在这档没有可治对象 ⇒ 该收窄到 ≥200 字' if mis else '未见系统性误伤'}")
                if mis:
                    verdict = "收窄到 ≥200 字（Q3）"
    if calls:
        print(f"Q6 成本：{calls.group('calls')} 次调用 / {calls.group('n')} 条回复 = "
              f"{calls.group('avg')} 次每回复（预登记未给 Q6 设线 ⇒ 只记录，不参与判定）")
    else:
        print("Q6 成本 ❌：没有收尾那行 ⇒ 中途被杀，这一维判不了")
    if verdict is None and got2 and got3:
        verdict = "留下（Q2 通过、Q3 未触发收窄）"
    print("判读：" + (verdict if verdict else
                     "Q2/Q3 至少一档停在「未测」或「只作个案」⇒ 本轮判不了留下还是收窄"
                     "（这不是通过，也不是「没有代价」）"))
    return 0


def main() -> int:
    paths = [pathlib.Path(a) for a in sys.argv[1:] if not a.startswith("--")]
    if not paths:
        print("用法：parse_gate_yield_lines.py [--judge] <gate_yield 的 stdout/边车文件> ...", file=sys.stderr)
        return 2
    if "--judge" in sys.argv and len(paths) > 1:
        print("--judge 一次只判一份（判据是预登记给某一次跑法的，多份会互相顶包）", file=sys.stderr)
        return 2
    bad = 0
    for p in paths:
        if not p.is_file():
            print(f"缺文件 {p}", file=sys.stderr)
            bad = 1
            continue
        stat: dict = {}
        rc = one(p, stat)
        bad |= rc
        if "--judge" in sys.argv:
            rec = stat.get("reconciled", 0)
            recon = "mismatch" if rc == 1 else ("ok" if rc == 0 and rec == len(TIERS) else "not-comparable")
            bad |= judge(p, recon)
    return bad


if __name__ == "__main__":
    sys.exit(main())
