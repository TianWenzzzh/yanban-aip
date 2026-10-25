r"""true_gap 的**形状分解**：那 69 句到底是「没有断言的版式行」还是「有断言但池子撑不住」？

为什么是这一格（轮 132 判 G1 之后留下的、挡 A/B 起跑的唯一前置）：
立项体检给出「非空池 true_gap = 69/596 = 11.6% ≥ 10%」，但日志同时登记了一条边界——
探针打的 6 条样本里 4 条长得像小标题/列表行，那是**目测定性**，不是测得的比率。
A/B 要用哪个分母由此悬着：版式行（「1. **协作与协调**」这种）二次检索救不了——它没有可核的断言，
把它算进病人数就是拿尺子噪声当疗效。所以下一步不是赶紧起跑，是把这句定性变成计数。

口径（全部走产品函数，不另造分类尺；分桶边沿只用产品常量）：
- 语料与轮 132 完全同源：同一份只读 SQL 取卡（复刻 `memory/paper_memory.py:60—71`）、
  `report.citable_papers` 建池、`evidence.grounding_report` 逐句判；
- 🔴 **与上一轮对账**：带角标句必须 == 596、true_gap 必须 == 69、报告份数 == 49、项目数 == 754；
  任一不等就 RC=2 并原样印两边数字——那说明**池子动了或复刻坏了**，本轮读数作废（同池复跑只证明确定性）；
- 形状旗标全机械、可并存（不是互斥分类，所以只报占比、不做归属判定）：
  F1 列表/编号开头 `^\s*([-*+]|\d+[.、)])\s`；F2 含 `**` 粗体；F3 含中文冒号「：」；
  F4 句长 ≥40 字；F5 含 `[n]` 角标（区别于只写《篇名》）。
  长度看的是 `rows[].sentence`，它被产品截到前 120 字（`evidence.py:230`）⇒ F4 的 40 字门槛不受截断影响。
- 夹具先自证：一行「1. **协作与协调**：X [1]。」必须同时命中 F1/F2/F3/F5，
  一行普通陈述句必须一个都不命中；命不中就是尺子坏了 ⇒ RC=2。

预登记判读（跑之前写死，跑完不改口径）：
- 令 B = F1 或 F2 命中的行。若 **true_gap 里 B 的占比 − grounded 里 B 的占比 ≥ 20pp**
  且两侧分母都 ≥ 20 句 ⇒ 判 **S1**：11.6% 里有相当一块是版式行，A/B 的分母改用「剔除 B 后的断言行」，
  且**只用剔除后的率去比 10% 判线**；若剔除后的率仍 ≥ 10% ⇒ A/B 照跑（分母按新口径重算 n），
  若降到 10% 以下 ⇒ 撤销轮 132 的 G1，改成「缺口主要是版式，转去改报告结构」。
- 若差 < 20pp，或任一侧分母 < 20 ⇒ 判 **S2**：版式行不构成本池的主要解释，
  分母维持 596/69，A/B 可直接按轮 132 的口径预登记。
- 无论 S1/S2，misnumbered 一栏只印不判（它已在轮 130 由台账接管，本轮不再动）。

容器内跑：python /tmp/probe_gap_shape.py
本地自检：在 code/yanban 下 python <本文件> --local（本地没有带角标报告 ⇒ 预期 RC=3，只当形状体检）
退出码：0=S1 或 S2 出数｜2=与轮 132 对不上账／夹具坏｜3=分母不足，未测
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
SIDE = (pathlib.Path(tempfile.gettempdir()) / "probe_gap_shape.local.txt") if LOCAL \
    else pathlib.Path("/tmp/probe_out.txt")

# 轮 132（g116 / measure-20260924p）的四个读数，逐字抄自那份边车，用于对账：
PREV = {"projects": 754, "reports": 49, "cited": 596, "true_gap": 69}
GAP_LINE = 0.10          # g92 预登记的立项线，沿用
SHAPE_PP = 20.0          # 判 S1 的百分点差
SHAPE_FLOOR = 20         # 两侧最小分母（轮 115 的规矩：率判据两侧都要有分母）
FLAGS = ("F1", "F2", "F3", "F4", "F5")

_out: list[str] = []


def emit(s: str = "") -> None:
    print(s, flush=True)
    _out.append(s)


def die(rc: int, why: str) -> None:
    emit(f"判读：{why}")
    SIDE.write_bytes(("\n".join(_out) + "\n").encode("utf-8"))
    sys.exit(rc)


F1 = re.compile(r"^\s*([-*+]|\d+[.、)])\s")


def shape(sentence: str) -> dict:
    return {"F1": bool(F1.match(sentence)), "F2": "**" in sentence, "F3": "：" in sentence,
            "F4": len(sentence) >= 40, "F5": bool(re.search(r"\[\d{1,2}\]", sentence))}


# ── 夹具：尺子自己先证一遍（正/负各一行，命中集合写死）────────────────────────
GOOD_LINE = "1. **协作与协调**：多智能体系统的通信开销随规模上升 [1]。"
PLAIN_LINE = "多智能体系统的通信开销随着参与方数量上升而明显增大。"
g_fl = shape(GOOD_LINE)
if (g_fl["F1"], g_fl["F2"], g_fl["F3"], g_fl["F5"], g_fl["F4"]) != (True, True, True, True, False):
    die(2, f"夹具正例命中集合不对（{g_fl}）⇒ 旗标实现坏了（RC=2）")   # F4 期望 False：这行不足 40 字
if any(shape(PLAIN_LINE)[k] for k in ("F1", "F2", "F3", "F5")):
    die(2, "夹具负例（普通陈述句）被误打成版式行 ⇒ 这把尺会把病人都剔掉（RC=2）")

try:
    sys.path.insert(0, "." if LOCAL else "/app")
    from companion import knowledge
    from companion.conversation import report as report_mod
    from companion.literature import evidence
except Exception as exc:                                    # noqa: BLE001
    die(2, f"读不到被测产品模块（{type(exc).__name__}: {exc}）⇒ 仪器没连上被测物（RC=2）")

try:
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    projs = [(r["id"], r["name"]) for r in con.execute("SELECT id, name FROM projects ORDER BY id")]
except (sqlite3.Error, OSError) as exc:
    die(2, f"只读连不上 {DB}（{exc}）⇒ 没连上被测物（RC=2）")


def papers_of(pid: str) -> list[dict]:
    rows = con.execute("SELECT paper_json, status, user_feedback FROM papers WHERE project_id=? "
                       "ORDER BY created_at DESC", (pid,)).fetchall()
    return [dict(json.loads(r["paper_json"]), status=r["status"], user_feedback=r["user_feedback"])
            for r in rows]


emit(f"夹具：版式行命中 {sorted(k for k, v in shape(GOOD_LINE).items() if v)}、"
     f"普通陈述句命中 {sorted(k for k, v in shape(PLAIN_LINE).items() if v) or '无'} ✅"
     f"｜轮 132 对账目标 {PREV}")

TALLY = {st: dict.fromkeys(("B", "F3", "F4", "F5"), 0)      # B = F1 ∪ F2（F1/F2 不单列，见判读）
         for st in ("grounded", "misnumbered", "true_gap")}
TOT = {st: 0 for st in TALLY}
reports = cited = 0
B_rows: list[str] = []

for pid, _name in projs:
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
        for r in gr["rows"]:
            st = r["status"]
            if st not in TALLY:
                continue
            TOT[st] += 1
            fl = shape(r["sentence"])
            if fl["F1"] or fl["F2"]:
                TALLY[st]["B"] += 1
            for k in ("F3", "F4", "F5"):
                if fl[k]:
                    TALLY[st][k] += 1
            if fl["F1"] or fl["F2"]:
                if len(B_rows) < 8:
                    B_rows.append(f"{st}｜{''.join(k for k in FLAGS if fl[k])}｜{r['sentence'][:56]}")

con.close()


def pct(n: int, d: int) -> str:
    return "未测（分母 0）" if not d else f"{100.0 * n / d:.1f}%"


if cited != PREV["cited"] or reports != PREV["reports"] or len(projs) != PREV["projects"] \
        or TOT["true_gap"] != PREV["true_gap"]:
    die(2, f"与轮 132 对不上账（本轮 项目 {len(projs)}／报告 {reports}／带角标句 {cited}／"
           f"true_gap {TOT['true_gap']} vs 上轮 {PREV}）⇒ 池子动了或复刻坏了，本轮读数作废")
emit(f"对账通过：{len(projs)} 项目／{reports} 份带角标报告／{cited} 句／true_gap {TOT['true_gap']}"
     f"——与 g116 逐项相等（同池复跑，只证明确定性）")
emit()
for st in ("grounded", "misnumbered", "true_gap"):
    n = TOT[st]
    emit(f"{st} {n} 句：" + "｜".join(
        f"{k} {TALLY[st][k]} ({pct(TALLY[st][k], n)})" for k in ("B", "F3", "F4", "F5")))

gap_b = TALLY["true_gap"]["B"]
gro_b = TALLY["grounded"]["B"]
gap_share = 100.0 * gap_b / TOT["true_gap"] if TOT["true_gap"] else None
gro_share = 100.0 * gro_b / TOT["grounded"] if TOT["grounded"] else None
assert gap_share is not None and gro_share is not None
gap_net = TOT["true_gap"] - gap_b
cited_net = cited - (TALLY["grounded"]["B"] + TALLY["misnumbered"]["B"] + gap_b)
emit()
emit(f"断言行口径（剔除 F1∪F2）：带角标句 {cited} → {cited_net}｜true_gap {TOT['true_gap']} → {gap_net}"
     f"｜率 {pct(gap_net, cited_net)}（原口径 {pct(TOT['true_gap'], cited)}）")
for s in B_rows:
    emit(f"    B 类样本：{s}")

if min(TOT["true_gap"], TOT["grounded"]) < SHAPE_FLOOR:
    die(3, f"两侧分母有一侧 < {SHAPE_FLOOR}（true_gap {TOT['true_gap']}、grounded {TOT['grounded']}）"
           f"⇒ 这一维本轮未测，S1/S2 都不许下")
diff = gap_share - gro_share
if diff >= SHAPE_PP:
    line = "照跑（分母按剔除后重算）" if gap_net and 100.0 * gap_net / max(cited_net, 1) \
        >= 100 * GAP_LINE else "撤销 G1，转去改报告结构"
    die(0, f"判 S1：true_gap 里版式行占 {gap_share:.1f}%、grounded 里占 {gro_share:.1f}%"
           f"（差 {diff:.1f}pp ≥ {SHAPE_PP:.0f}pp）⇒ A/B 分母改用剔除后的断言行"
           f"（{gap_net}/{cited_net} = {pct(gap_net, cited_net)}），据此{line}。")
die(0, f"判 S2：true_gap 里版式行占 {gap_share:.1f}%、grounded 里占 {gro_share:.1f}%"
       f"（差 {diff:.1f}pp < {SHAPE_PP:.0f}pp）⇒ 版式行不是本池缺口的主要解释，"
       f"A/B 分母维持轮 132 的 {TOT['true_gap']}/{cited}，可直接按那条口径预登记。")
