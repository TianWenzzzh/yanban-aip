r"""参考文献的 precision 基线：列出来的每一篇，到底挂了几次角标、又真撑住了几句？

为什么要单量这一格（`METHOD_BACKLOG` 队列第 3 条 / ALCE 2305.14627）：
产品的「可核对性」报的是 recall——**这句有没有依据**（`grounding_report` 的 grounded/cited）。
它答不了另一半：**这篇该不该被引**。现在的产物把论文池前 `MAX_PAPERS` 篇一律列成参考文献，
所以"挂过角标但一句都没撑住"的那篇，和"真的撑住三句"的那篇长得一模一样——
读者以为自己在核六个来源，其实可能在核两个。轮 132/133 把 true_gap 分桶之后，这一维是产物侧剩下的
最大一处"看起来可核对、实际没区分"。

口径（全走产品函数；计数一律由 `grounding_report` 的 rows 算出，与台账同源）：
- 引用次数 = 报告正文里 `[n]` 出现的次数（`artifact._CITE` 同一条正则，不另造）；
- 支撑次数 = rows 里 `span["paper"] == 该篇下标` 的行数（grounded 与 misnumbered 都算在**真正撑住它的那篇**头上）；
- 🔴 不变量：`sum(支撑次数) == grounded + misnumbered`。不等就是复刻歪了 ⇒ RC=2（与轮 130 的
  "台账与汇总同源"是同一条规矩，只是这次量的是跨篇的边际）；
- 与上两轮对账：项目 754／报告 49／带角标句 596／true_gap 69，逐项相等才出数（不等 ⇒ 池子动过，作废）。

预登记读数（跑前写死要看哪三个数，不预设结论）：
- P1 列出来但**一次都没挂**角标的篇数（占比 / 总列出篇数）；
- P2 挂了角标却**一句都没撑住**的篇数（这才是假引用，precision 的失败形态）；
- P3 至少撑住一句的篇数。
判据（决定要不要把它做进产物）：P2 ≥ 非空池报告的 **10%** 且报告数 ≥ 20 ⇒ 值得做（有对象）；
否则记「这一维在当前池上没有对象」，按轮 113 的规矩不能记成缺陷，也就不许顺手"修"。

容器内跑：python /tmp/probe_ref_precision.py
本地自检：在 code/yanban 下 python <本文件> --local（本地无带角标报告 ⇒ 预期 RC=2 对账不过）
退出码：0=出数｜2=对账/不变量不过｜3=分母不足
"""
from __future__ import annotations

import json
import pathlib
import re
import sqlite3
import sys
import tempfile

LOCAL = "--local" in sys.argv
DB = pathlib.Path("data/yanban.db" if LOCAL else "/app/data/yanban.db")
SIDE = (pathlib.Path(tempfile.gettempdir()) / "probe_ref_precision.local.txt") if LOCAL \
    else pathlib.Path("/tmp/probe_out.txt")

PREV = {"projects": 754, "reports": 49, "cited": 596, "true_gap": 69}
FLOOR_REPORTS = 20
LINE = 0.10
CITE = re.compile(r"\[(\d{1,2})\]")

_out: list[str] = []


def emit(s: str = "") -> None:
    print(s, flush=True)
    _out.append(s)


def die(rc: int, why: str) -> None:
    emit(f"判读：{why}")
    SIDE.write_bytes(("\n".join(_out) + "\n").encode("utf-8") + b"\n")
    sys.exit(rc)


try:
    sys.path.insert(0, "." if LOCAL else "/app")
    from companion import knowledge
    from companion.artifact import _CITE
    from companion.conversation import report as report_mod
    from companion.literature import evidence
except Exception as exc:                                    # noqa: BLE001
    die(2, f"读不到被测产品模块（{type(exc).__name__}: {exc}）⇒ 连 `_CITE` 都要是产品那一条，不另造（RC=2）")

if _CITE.pattern != r"\[(\d{1,2})\]":
    die(2, f"artifact._CITE 换形了（{_CITE.pattern}）⇒ 引用次数的口径要跟它同步，先改本探针（RC=2）")

try:
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    projs = [r["id"] for r in con.execute("SELECT id FROM projects ORDER BY id")]
except (sqlite3.Error, OSError) as exc:
    die(2, f"只读连不上 {DB}（{exc}）⇒ 没连上被测物（RC=2）")


def papers_of(pid: str) -> list[dict]:
    rows = con.execute("SELECT paper_json, status, user_feedback FROM papers WHERE project_id=? "
                       "ORDER BY created_at DESC", (pid,)).fetchall()
    return [dict(json.loads(r["paper_json"]), status=r["status"], user_feedback=r["user_feedback"])
            for r in rows]


listed = never_cited = cited_no_support = supporting = 0
reports = cited = gap = 0
rows_support_sum = rows_ground_plus_mis = 0

for pid in projs:
    try:
        notes = knowledge.list_notes(pid)
    except Exception:                                       # noqa: BLE001
        continue
    reports_here = [m for m in notes if "report" in m["tags"]]
    if not reports_here:
        continue
    pool = report_mod.citable_papers(papers_of(pid))
    for m in reports_here:
        body = knowledge.note_body(pid, m["note"]) or ""
        gr = evidence.grounding_report(body, pool)
        if not gr["cited_sentences"]:
            continue
        reports += 1
        cited += gr["cited_sentences"]
        gap += gr["true_gap"]
        n_cite = {i: 0 for i in range(len(pool))}
        for num in _CITE.findall(body):
            i = int(num) - 1
            if 0 <= i < len(pool):
                n_cite[i] += 1
        n_sup = {i: 0 for i in range(len(pool))}
        for r in gr["rows"]:
            if r["status"] != "true_gap":
                rows_ground_plus_mis += 1
                p = (r["span"] or {}).get("paper")
                if p is not None and 0 <= p < len(pool):
                    n_sup[p] += 1
                    rows_support_sum += 1
            else:
                p = (r["span"] or {}).get("paper")
                if p is not None:
                    die(2, f"true_gap 行竟带支撑片段（pid {pid}）⇒ 与产品的分类同源假设矛盾（RC=2）")
        for i in range(len(pool)):
            listed += 1
            if n_cite[i] == 0:
                never_cited += 1
            elif n_sup[i] == 0:
                cited_no_support += 1
            else:
                supporting += 1
con.close()

if rows_support_sum != rows_ground_plus_mis:
    die(2, f"不变量不过：支撑次数合计 {rows_support_sum} ≠ grounded+misnumbered {rows_ground_plus_mis}"
           f"⇒ 复刻歪了，别印这份分布（RC=2）")
if (len(projs), reports, cited, gap) != (PREV["projects"], PREV["reports"], PREV["cited"], PREV["true_gap"]):
    die(2, f"与轮 132/133 对不上账（本轮 项目 {len(projs)}／报告 {reports}／带角标句 {cited}／"
           f"true_gap {gap} vs 上轮 {PREV}）⇒ 池子动过或复刻坏了，本轮作废")
emit(f"对账通过：{len(projs)} 项目／{reports} 份带角标报告／{cited} 句／true_gap {gap}"
     f"｜不变量：支撑合计 {rows_support_sum} == grounded+misnumbered {rows_ground_plus_mis} ✅")

if reports < FLOOR_REPORTS:
    die(3, f"只有 {reports} 份报告（< {FLOOR_REPORTS}）⇒ 分母不足，这一维未测")
emit(f"列出的参考文献共 {listed} 条（每份报告 ≤ MAX_PAPERS 篇）：")
emit(f"  P1 一次都没挂角标：{never_cited} 条 = {100.0 * never_cited / listed:.1f}%")
emit(f"  P2 挂了角标却一句都没撑住（假引用）：{cited_no_support} 条 = {100.0 * cited_no_support / listed:.1f}%")
emit(f"  P3 至少撑住一句：{supporting} 条 = {100.0 * supporting / listed:.1f}%")
share = 100.0 * cited_no_support / listed
emit(f"  按报告看：{reports} 份里平均每份 {cited_no_support / reports:.1f} 条假引用")
if cited_no_support >= LINE * listed:
    die(0, f"P2 = {cited_no_support}/{listed} = {share:.1f}% ≥ {LINE:.0%} ⇒ 这一维有对象，"
           f"产物该把「挂了几次角标 / 撑住几句」摆进参考文献（下一轮做进 companion/**，配测试 + 看图）")
die(0, f"P2 = {cited_no_support}/{listed} = {share:.1f}% < {LINE:.0%} ⇒ 假引用在当前池上不是普遍形态，"
       f"这一维按「未发作的机制」登记，不做进产物（做了也只是给读者多一栏噪声）")
