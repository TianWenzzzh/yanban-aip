"""轮 118（任务 #23）：量「闸门认它有来源、计数尺数到 0 句」的节到底有多少——零模型调用。

为什么先量这个再改判据：轮 117 补记五/六证明了两把尺口径不同，而且**产品自己就踩在同一处**
（`report.py:136-150` 用 `grounding_report(...)["cited_sentences"] == 0` 挑节、又用同一个 0 采纳）。
一节如果只用 `【1】` 挂来源，会被判"没挂"⇒ 白烧一次重写，重写后若还用 `【n】` 就被整节退回。
但"该不该改"取决于**这个形状在线上真实存在多少**——老规矩：没量过就不动闸门。

这台仪器不需要论文池：`grounding_report` 里 `cited_sentences` 只由文本决定
（`evidence.py:204-208`：句子 ≥8 字且含 `[n]` 或 `《`），与 papers 无关。这条假设不是读代码读出来的就算，
`--selfcheck` 里 S5 直接拿同一句喂"空池"与"两篇假论文"两次，断言 `cited_sentences` 相等。

预登记判据（跑前写死，跑完不许改口径）：
  T0 输入自证：印 corpus 路径与份数、剔掉的份数。**corpus 是 `data/knowledge/*/*.md`，不是对话表**——
     第一版按轮 116 的经验扫 `messages` 里 `kind=deliver` 的行，线上实测 459 条里 **447 条一个 `## ` 都没有**
     （RC=2 拒判，没印出任何率）⇒ 报告正文不落 `messages`，它由 `knowledge.upsert_note` 写成
     `data/knowledge/{project_id}/*.md`（`companion/knowledge.py:3,29-38`）。这次纠正来自仪器自己印的形状，不是猜的。
  T1 主读数：分母 = 参与统计的节数（有标题、不在 LOCKED_SECTIONS、正文非空）；
     读数 = 其中「痕有 && 计数 0」的节占比。**分母 <MIN_SECTIONS(20) ⇒ 只印计数、判"未测"（RC=3）**。
  T2 归因（机械判据，两类互斥）：把 `【n】` 换成 `[n]` 再数一遍——
     数得到 ⇒ **形态差**（cn_form：对齐判据、或让计数尺也认【n】，两种改法都咬）；
     数不到 ⇒ **分句门槛**（short_piece：只有整节级的 `_CITE_TRACE` 咬得到，认【n】不咬）。
     真值里出现第三类（other）⇒ 分类没穷尽，RC=2（仪器判不了，不出数）。
  T3 负控制：全库 `cited_sentences` 合计必须 >0。等于 0 说明计数器根本没在工作（或被测物全空），
     那种"零 gap"是假的 ⇒ RC=2。分母为 0 同理。
  T4 报告级：至少有一类节的报告占多少份（改判据影响的是一篇里挑不挑这节）。
  收尾行以「判读：」开头；未测不许印成"没问题"。

跑法：
  本地自检  PYTHONUTF8=1 code/yanban/.venv/Scripts/python.exe 输出/scripts/probe_trace_vs_count.py --selfcheck
  线上读数  PYTHONUTF8=1 uv run --no-project --with paramiko python 输出/scripts/run_cloud_probe.py \\
              输出/scripts/probe_trace_vs_count.py
"""
from __future__ import annotations

import os
import pathlib
import re
import sys

sys.path.insert(0, "/app")
sys.path.insert(0, ".")
_here = pathlib.Path(__file__).resolve()
for cand in ["/app", str(pathlib.Path.cwd()), *map(str, _here.parents)]:
    if (pathlib.Path(cand) / "companion").is_dir():
        sys.path.insert(0, cand)
        break
else:
    print("判读：找不到含 companion/ 的仓根 ⇒ 仪器没连上被测物，不出数（RC=2）")
    sys.exit(2)

from companion import knowledge                        # noqa: E402
from companion.conversation import mentor, report      # noqa: E402
from companion.literature import evidence              # noqa: E402

_TEE = os.environ.get("PROBE_TEE") or (
    f"/tmp/probe_out_{os.path.basename(__file__)[:-3]}.txt" if os.name == "posix" else None)
if _TEE and "--selfcheck" not in sys.argv:
    class _Tee:
        def __init__(self, path: str) -> None:
            self.f = open(path, "w", encoding="utf-8")

        def write(self, s: str) -> int:
            self.f.write(s)
            self.f.flush()
            return sys.__stdout__.write(s)

        def flush(self) -> None:
            self.f.flush()

    sys.stdout = _Tee(_TEE)     # type: ignore[assignment]
    print(f"边车（本地管道断了就取回这份）：{_TEE}", flush=True)

MIN_SECTIONS = 20       # T1 预登记的分母下限
BRACKET_EN = re.compile(r"\[\d{1,2}\]")
# 计数尺那半边"什么样的句子才算带了角标"——与 evidence.py:206 逐字一致（自检 S6 会拿产品函数对账）
COUNTED = lambda s: len(s) >= 8 and (BRACKET_EN.search(s) or "《" in s)  # noqa: E731


def classify(text: str, cited: int) -> str:
    """痕有 / 计数 0 的节，差在哪条路径上。判法要机械、两类互斥：

    把 `【1】` 换成 `[1]` 再数一遍——数得到了 ⇒ 差的只是**形态**（`_CITE_TRACE` 或让计数尺认【n】都能咬）；
    换完还是 0 ⇒ 差的是**分句 ≥8 字那道路槛**（只有整节级的 `_CITE_TRACE` 能咬，让计数尺认【n】不咬）。
    上一版按"文本里有没有 [n]/《"分，`细节见【1】。` 这种同时满足两条，两类抢同一个样本 ⇒ 归因不确定。
    """
    if cited:
        return "other"
    conv = re.sub(r"【(\d{1,2})】", r"[\1]", text or "")
    pieces = [s.strip() for s in re.split(r"(?<=[。！？!?\n])", conv)]
    return "cn_form" if any(COUNTED(s) for s in pieces) else "short_piece"


def scan(root: pathlib.Path) -> dict:
    files = sorted(root.glob("*/*.md"))
    st = {"notes": len(files), "no_head": 0, "sections": 0,
          "gap": 0, "cited_total": 0, "reports": 0, "reports_gap": 0, "errors": 0,
          "why": {"cn_form": 0, "short_piece": 0, "other": 0}, "samples": []}
    for f in files:
        try:
            body, _tags = knowledge.strip_front(f.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001 — 单份读不动就跳过并计数，不打死整跑
            st["errors"] += 1
            continue
        if not re.search(r"^##\s", body, flags=re.M):
            st["no_head"] += 1     # 对话结论类笔记：没有 ## 就没有"节"，不是这道闸门的对象
            continue
        st["reports"] += 1
        hit_here = 0
        for head, text in report.split_sections(body):
            if not head or head in report.LOCKED_SECTIONS or not text.strip():
                continue
            try:
                cited = evidence.grounding_report(text, [])["cited_sentences"]
            except Exception:  # noqa: BLE001 — 单节失败只跳这一节
                st["errors"] += 1
                continue
            st["sections"] += 1
            st["cited_total"] += cited
            trace = bool(mentor._CITE_TRACE.search(text))
            if trace and cited == 0:
                why = classify(text, cited)
                st["why"][why] += 1
                st["gap"] += 1
                hit_here += 1
                if len(st["samples"]) < 8:
                    st["samples"].append((f.parent.name, head, why,
                                          text.strip()[:70].replace("\n", " ")))
        st["reports_gap"] += int(hit_here > 0)
    return st


def print_readout(st: dict) -> int:
    print(f"节级分母（有标题、非自动节、正文非空）：{st['sections']} 条"
          f"｜带角标句合计 {st['cited_total']} 条（T3 负控制：必须 >0）")
    print(f"归因：形态差（把【n】换成 [n] 就数得到）{st['why']['cn_form']}｜"
          f"痕迹落在 <8 字短句（换形态也数不到）{st['why']['short_piece']}"
          f"｜其它 {st['why']['other']}")
    for pid, head, why, snip in st["samples"]:
        print(f"  · {head}｜{why}｜项目 {pid[:8]}｜{snip!r}")
    if sum(st["why"].values()) != st["gap"]:
        print(f"判读：结构不可能——归因合计 {sum(st['why'].values())} ≠ gap {st['gap']} ⇒ 计数与分类分家了，"
              "这份读数作废（RC=2）")
        return 2
    if st["cited_total"] == 0 or st["sections"] == 0:
        print("判读：T3 负控制红（计数器一条句都没数到，或分母为 0）⇒ 这台仪器此刻判不了任何占比，"
              "这个 0 不是「没有口径差」")
        return 2
    if st["sections"] < MIN_SECTIONS:
        print(f"判读：分母 {st['sections']} <{MIN_SECTIONS} ⇒ **未测**，只印计数不印率"
              f"（gap {st['gap']} 条 / 报告 {st['reports_gap']}/{st['reports']} 份）")
        return 3
    rate = 100 * st["gap"] / st["sections"]
    top = max(st["why"], key=st["why"].get)
    majority = st["why"][top] * 2 > st["gap"]
    print(f"T1 主读数：痕有 && 计数 0 ⇒ {st['gap']}/{st['sections']} = {rate:.0f}%"
          f"｜报告级 {st['reports_gap']}/{st['reports']} 份至少有一节")
    if st["why"]["other"]:
        print(f"判读：有 {st['why']['other']} 条落在三类之外 ⇒ 归因没穷尽，别拿这份数选改法（RC=2）")
        return 2
    if not st["gap"]:
        print("判读：分母够而 gap=0 ⇒ 线上这条路径**没有对象**，两把尺不用对齐（#23 结为「不改」）")
        return 0
    if top == "cn_form" and majority:
        print(f"判读：口径差主要来自形态（{st['why']['cn_form']}/{st['gap']}）⇒ "
              "把 `_cite_missing_sections` 的判据对齐到 `_CITE_TRACE`、或让计数尺也认【n】，两条都咬得到")
    elif top == "short_piece" and majority:
        print(f"判读：口径差主要来自 <8 字短句（{st['why']['short_piece']}/{st['gap']}）⇒ "
              "只有整节级的 `_CITE_TRACE` 对齐咬得到；让计数尺认【n】**不咬**（那道路槛还在）")
    else:
        print("判读：两类都不占多数 ⇒ 单改一处咬不到多少，未决")
    return 0


def selfcheck() -> int:
    """手算夹具。每条都写「期望」，且改坏必须红（配套 mutate_trace_selfcheck.py）。"""
    P = {"title": "深度学习框架在实车标定中的应用研究", "abstract": "我们提出了一套标定流程" * 12}
    Q = {"title": "另一个工作的标题", "abstract": "实验结果表明性能提升" * 12}
    checks = []

    def chk(name, cond, note=""):
        checks.append((name, bool(cond), note))

    s1 = "这份研究提出了一套标定流程【1】，并在实车上验证了它的稳定性。"
    s2 = "这份研究提出了一套标定流程[1]，并在实车上验证了它的稳定性。"
    s3 = "细节见【1】。\n后面这句话很长很长，一直没有任何角标标记，直到这里仍然没有。"
    s4 = "《深度学习框架在实车标定中的应用研究》提出了流程，值得先看一遍。"
    s5 = "整节都在讲方法选择与取舍，通篇没有一处角标，也没有书名号，长度足够长而已。"
    for tag, text, want_trace, want_cited in (
            ("A 只用【n】：痕有 / 计数 0", s1, True, 0),
            ("B 用 [n]：痕有 / 计数 1", s2, True, 1),
            ("C 痕迹落在 <8 字短句", s3, True, 0),
            ("D 用《》：痕有 / 计数 1", s4, True, 1),
            ("E 无痕无计数", s5, False, 0)):
        got = evidence.grounding_report(text, [P, Q])["cited_sentences"]
        chk(tag, bool(mentor._CITE_TRACE.search(text)) == want_trace and got == want_cited,
            f"痕={want_trace} 计数={want_cited} 实测 {got}")
    # 关键假设：cited_sentences 与论文池无关（所以这台仪器不需要料）
    for tag, text in (("F papers 无关性", s1), ("G papers 无关性", s2), ("H papers 无关性", s3)):
        a = evidence.grounding_report(text, [])["cited_sentences"]
        b = evidence.grounding_report(text, [P, Q])["cited_sentences"]
        chk(tag, a == b, f"空池 {a} == 有池 {b}")
    chk("I 归因：只有【n】、换 [n] 就数得到 ⇒ cn_form", classify(s1, 0) == "cn_form", classify(s1, 0))
    chk("J 归因：换完 [n] 仍落在 <8 字短句 ⇒ short_piece",
        classify(s3, 0) == "short_piece", classify(s3, 0))
    chk("K 归因：计数非 0 时不许归成形态差", classify(s1, 1) == "other", classify(s1, 1))
    # 产品自己的函数确认 s1 真数到 0（不是我把 evidence 的口径抄错了）
    chk("L 直接调产品函数校形状",
        set(evidence.grounding_report(s1, [P, Q])) >= {"cited_sentences", "grounded",
                                                       "misnumbered", "true_gap"}, "")
    # 判据层（print_readout）也要有夹具：上面那些只验归因，率与退出码那段平时只在云端跑一次，
    # 一跑几小时——这正是轮 116/117 反复栽的"平时不执行的打印"。
    import contextlib
    import io

    def state(**kw):
        st = {"sections": 40, "cited_total": 12, "gap": 0, "reports": 10, "reports_gap": 0,
              "why": {"cn_form": 0, "short_piece": 0, "other": 0}, "samples": []}
        for k, v in kw.items():
            st["why"].update(v) if k == "why" else st.__setitem__(k, v)
        return st

    def readout(st):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            return st, print_readout(st), buf.getvalue()

    _, rc, out = readout(state(sections=5, gap=2, why={"cn_form": 2}))
    chk("M 分母 <20 ⇒ RC=3、只印计数不印率", rc == 3 and "未测" in out and "%" not in out, f"RC={rc}")
    _, rc, out = readout(state(gap=0))
    chk("N 分母够而 gap=0 ⇒ RC=0 且写「没有对象」", rc == 0 and "没有对象" in out, f"RC={rc}")
    _, rc, out = readout(state(cited_total=0, gap=3, why={"cn_form": 3}))
    chk("O 负控制：全库一句都没数到 ⇒ RC=2，不许读成零口径差", rc == 2 and "T3" in out, f"RC={rc}")
    _, rc, out = readout(state(gap=7, reports_gap=4, why={"cn_form": 6, "short_piece": 1}))
    chk("P 形态差占多数 ⇒ 判读指向「两种改法都咬」", rc == 0 and "两条都咬得到" in out, f"RC={rc}")
    _, rc, out = readout(state(gap=7, why={"cn_form": 2, "short_piece": 5}))
    chk("Q 短句门槛占多数 ⇒ 判读必须写清「认【n】不咬」", rc == 0 and "不咬" in out, f"RC={rc}")
    _, rc, out = readout(state(gap=7, why={"cn_form": 3, "short_piece": 3, "other": 1}))
    chk("R 出现第三类 ⇒ 归因没穷尽，RC=2 不出数", rc == 2 and "没穷尽" in out, f"RC={rc}")
    _, rc, out = readout(state(gap=7, why={"cn_form": 5}))
    chk("S 结构不变量：归因合计 ≠ gap ⇒ 作废 RC=2", rc == 2 and "结构不可能" in out, f"RC={rc}")

    bad = [n for n, ok, _ in checks if not ok]
    for n, ok, note in checks:
        print(f"[自检 {n}] {'✅' if ok else '❌'}　{note}")
    print(f"判读：{len(checks) - len(bad)}/{len(checks)} 项通过"
          + ("" if not bad else f"｜❌ 红在 {bad}"))
    return 1 if bad else 0


def main() -> int:
    if "--selfcheck" in sys.argv:
        return selfcheck()
    root = pathlib.Path(sys.argv[1]) if len(sys.argv) > 1 else knowledge.KB_ROOT
    if not root.is_dir():
        print(f"判读：找不到知识库目录 {root} ⇒ 这不是「没有口径差」，是仪器没连上被测物（RC=2）")
        return 2
    st = scan(root)
    print(f"库 {root}｜笔记 {st['notes']} 份｜没有 ## 分节 {st['no_head']}（对话结论类笔记，不是报告）｜"
          f"进入统计的报告 {st['reports']} 份")
    if st["notes"] == 0:
        print("判读：目录里一份笔记都没有 ⇒ 空跑，不出数（RC=2）")
        return 2
    if st["errors"]:
        print(f"⚠️ 有 {st['errors']} 节读 grounding_report 抛错被跳过（分子分母都不含它们）")
    return print_readout(st)


if __name__ == "__main__":
    sys.exit(main())
