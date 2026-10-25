"""true_gap 的归因体检：「池子里没货」还是「有货没引到」？——零模型调用、只读、不建项目。

为什么单独量这一条（队列第 2 格 FAIR-RAG 2510.22344 的立项前置）：
g92 定的线是「no_evidence / 带角标句 ≥ 10% 才立项针对单句的二次检索」，量出来 0% → 不立项；
轮 115 在更大样本上顺手复算得 14/62 = 22.6% → 过线。但**同一把尺在两个池子上量的**，
而轮 115 那批的 stderr 里 arXiv 406 / Semantic Scholar 429 全程熔断、供给只剩 OpenAlex。
刚读产品代码发现一个更要紧的形状（companion/literature/evidence.py:203—209）：
`_classify` 先看被引那几篇，接不住再扫整池；**池子为空时两轮都是空循环，直接落 true_gap**。
所以「true_gap」把两种处置相反的失败混在一起了：
  A 供给为空（当轮 0 篇卡）→ 二次检索发给同一批熔断的 provider，变不出摘要；
  B 池子非空、整池也撑不住这句 → 才是真正的证据缺口，FAIR-RAG 的子查询有病人；
  C 池子非空、但撑着这句的卡被 `citable_papers` 截断/剔掉了 → 那是选单问题，改选单不改检索。
旧结论（g92「没有可治对象」与轮 115「22.6% 过线」）都没把这三类分开。本轮只做这一件分离。

口径（全部走产品自己的函数，不另造分类尺）：
- 论文池 = `report.citable_papers(paper_memory.list_papers(pid))`，与产物端点同一条表达式；
- 逐句结局 = `evidence.grounding_report(body, 池子)`，四类计数由产品自己的 rows 算出；
- 只对落 true_gap 的句子做第二次分类：把**同项目未经 MAX_PAPERS 截断**的全池再喂一次
  （只做"放大池子能否找到依据"这一件事，判据本身仍是产品函数）；
- 报告正文取 `knowledge.note_body`（轮 113 实测：正文在 data/knowledge/*/*.md，不在 messages.deliver）。

预登记判读（跑之前写死，跑完不许改口径）：
- 分桶边沿只用产品常量：空池 = 0 篇；非空池 = 1..MAX_PAPERS-1 篇；满池 = MAX_PAPERS 篇（被截断过）。
- 最小分母（轮 115 的教训：拿 n=6 定 ≤10% 的线 = 写了条永远证伪不了的判据）：
  非空池桶（含满池）带角标句 ≥ 60 句才允许下「≥10% / <10%」的结论（60 句时 1 句 = 1.7pp，
  判线 10pp 的六分之一，抖动不至于翻盘）；空池桶 ≥ 20 句才允许说「空池占多数」。不足印「未测」并 RC=3。
- G1 立项：非空池桶 true_gap ≥ 10%，**且**其中「放大到全项目池仍查无依据」的那部分也 ≥ 非空池桶带角标句的 10%
      ⇒ 真缺口稳定存在，FAIR-RAG 式「只为缺口句发子查询」有病人，下一轮做它的 A/B。
- G2 改选单：非空池桶 true_gap ≥ 10%，但放大后大多数能找到依据 ⇒ 病因是截断/剔无标题，不立项二次检索。
- G3 病因在供给：非空池桶 < 10% 而空池桶 ≥ 10% ⇒ 缺口全在「当轮没卡」上，同一批熔断的 provider
      二次检索变不出摘要 ⇒ 不立项，改去看供给熔断本身（轮 113 已量到摘要只走一路出水）。
- 认路哨兵：三个产品函数任一 import 失败、`grounding_report` 少键、或正/负对照任一不按要求落
      ⇒ RC=2（测不了，不是产品的错）。只读 SQL 与 `list_papers` 的数量对不上 ⇒ RC=2。

容器内跑：python /tmp/probe_gap_attribution.py
本地自检：在 code/yanban 下 python /e/AIC智能体迁移包/输出/scripts/probe_gap_attribution.py --local
退出码：0=分桶完成且判据可下结论｜1=判据指向不立项（G3）｜2=仪器/尺子坏了｜3=分母不足，未测
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
# 本地自检的边车绝不落在仓里（会把工作树弄脏）；容器里沿用长跑边车的固定路径。
SIDE = (pathlib.Path(tempfile.gettempdir()) / "probe_gap_attribution.local.txt") if LOCAL \
    else pathlib.Path("/tmp/probe_out.txt")

# 判线的分母地板：见文件头预登记段落。1 句在 60 句的桶里 = 1.7pp < 判线 10pp 的三分之一。
FLOOR_NONEMPTY = 60
FLOOR_EMPTY = 20
# g92 预登记的立项线，沿用不改（改它就是换测试）。
GAP_LINE = 0.10

_out: list[str] = []


def emit(s: str = "") -> None:
    print(s, flush=True)
    _out.append(s)


def die(rc: int, why: str) -> None:
    emit(f"判读：{why}")
    SIDE.write_text("\n".join(_out) + "\n", encoding="utf-8")
    sys.exit(rc)


try:
    sys.path.insert(0, "." if LOCAL else "/app")
    from companion import knowledge
    from companion.conversation import report as report_mod
    from companion.literature import evidence
    from companion.memory import paper_memory
except Exception as exc:                                    # noqa: BLE001
    die(2, f"读不到被测产品模块（{type(exc).__name__}: {exc}）⇒ 仪器没连上被测物，不出数（RC=2）")

MAXP = getattr(report_mod, "MAX_PAPERS", None)
if not isinstance(MAXP, int) or MAXP < 2:
    die(2, f"读不到产品常量 MAX_PAPERS（拿到 {MAXP!r}）⇒ 不猜分桶边沿（RC=2）")
emit(f"论文池 = report.citable_papers(paper_memory.list_papers(pid))｜MAX_PAPERS = {MAXP}"
     f"｜判线 = 带角标句的 {GAP_LINE:.0%}（g92 预登记值，未改）")

# ── 尺子的正/负对照（先证明这把尺会红，再拿它去量）───────────────────────────
# 两句对两句：分类器按句计量，长度不进分母，所以这里只要求**句数相等**（各自两句话、都带 [1]）。
# ⚠ 夹具本身就是量出来的：grounding_report 的断句只认「。！？」与换行（evidence.py:224 的字符类里没有
#   半角点号），且角标必须落在句内 ⇒ 要写成「…[1]。」。写成「…。[1]」时角标会跟着**下一句**走
#   （归因错位），段末那种甚至只剩「[1]。」一句、不到 8 字被丢掉。同一条性质对真报告成立。
CTRL_PAPER = {"title": "Diffusion Policy 控制实验",
              "abstract": "transformer based diffusion policy achieves 91 percent success on "
                          "real robot manipulation tasks. each task uses thirty demonstrations "
                          "collected during two hours of teleoperation.",
              "tldr": "diffusion policy beats baselines", "source": ["crossref"], "year": 2024}
GOOD = ("transformer based diffusion policy achieves 91 percent success on real robot "
        "manipulation tasks[1]。 each task uses thirty demonstrations collected during two "
        "hours of teleoperation[1]。")
BAD = ("今天中午食堂有红烧肉，我们饭后再去把下周的排期表填一下[1]。"
       "这份名单里的三号作者我上周才在地铁上碰见过一次，跟他聊了半小时[1]。")
ctrl = evidence.grounding_report(GOOD, [CTRL_PAPER])
ctrl_bad = evidence.grounding_report(BAD, [CTRL_PAPER])
need = {"cited_sentences", "grounded", "misnumbered", "true_gap", "rows"}
if not need <= set(ctrl):
    die(2, f"grounding_report 返回缺键（有 {sorted(set(ctrl) - need)} 缺）⇒ 尺子形状变了（RC=2）")
if ctrl["grounded"] != ctrl["cited_sentences"] or ctrl["true_gap"]:
    die(2, f"正对照未全判 grounded（{ctrl['grounded']}/{ctrl['cited_sentences']}，true_gap {ctrl['true_gap']}）"
           f"⇒ 逐字抄来的原文都接不住，这把尺量谁都是缺口（RC=2）")
if ctrl_bad["true_gap"] != ctrl_bad["cited_sentences"]:
    die(2, f"负对照未全判 true_gap（{ctrl_bad['true_gap']}/{ctrl_bad['cited_sentences']}）"
           f"⇒ 毫不相干的句子也能找到依据，这把尺量谁都没缺口（RC=2）")
if ctrl["cited_sentences"] != 2 or ctrl_bad["cited_sentences"] != 2:
    die(2, f"正/负对照的句数不是各 2 句（{ctrl['cited_sentences']} vs {ctrl_bad['cited_sentences']}）"
           f"⇒ 断句形状变了，两侧分母不可比（RC=2）")
emit(f"尺子自检：逐字抄原文的 {ctrl['cited_sentences']} 句全判 grounded、"
     f"不相干的 {ctrl_bad['cited_sentences']} 句全判 true_gap ✅")

# ── 只读连库：取项目清单，并与 paper_memory 对账（认路哨兵）──────────────────
try:
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
except sqlite3.Error as exc:
    die(2, f"只读连不上 {DB}（{exc}）⇒ 没连上被测物（RC=2）")
try:
    con.row_factory = sqlite3.Row
    projs = [(r["id"], r["name"]) for r in con.execute("SELECT id, name FROM projects ORDER BY id")]
except sqlite3.Error as exc:
    die(2, f"线上库结构读不动（{exc}）⇒ 仪器没连上被测物（RC=2）")

if not projs:
    die(3, "库里 0 个项目 ⇒ 没有可分桶的对象（未测，RC=3）")

# 论文卡**只从只读连接取**，SQL 逐字复刻 companion/memory/paper_memory.py:60—71 的 list_papers；
# 不直接调它——db.get_conn()（companion/db.py:108—112）会开写连接并 executescript(SCHEMA)，
# 那等于宣称"只读"却去碰线上库。复刻就要对账：抽样的项目上比过，之后每份报告再自证一次。
def papers_of(pid: str) -> list[dict]:
    rows = con.execute("SELECT paper_json, status, user_feedback FROM papers WHERE project_id=? "
                       "ORDER BY created_at DESC", (pid,)).fetchall()
    return [dict(json.loads(r["paper_json"]), status=r["status"], user_feedback=r["user_feedback"])
            for r in rows]


samp = projs[:: max(1, len(projs) // 5)][:5]
for pid, _ in samp:
    n_sql = con.execute("SELECT COUNT(*) c FROM papers WHERE project_id=?", (pid,)).fetchone()["c"]
    n_mem = len(paper_memory.list_papers(pid))
    if n_sql != n_mem:
        die(2, f"只读 SQL 与 paper_memory.list_papers 数量不符（{n_sql} vs {n_mem}，pid {pid}）"
               f"⇒ 复刻的那条查询不是产品用的那条，别往下算（RC=2）")
emit(f"只读对账：抽 {len(samp)} 个项目，只读 SQL 与产品的 list_papers 逐个相等 ✅｜库内项目 {len(projs)} 个")


def buckets() -> dict:
    return {"cited": 0, "grounded": 0, "misnumbered": 0, "true_gap": 0, "widen_found": 0}


B = {"empty": buckets(), "thin": buckets(), "full": buckets()}
notes_seen = reports_seen = 0
# 顺带量尺子自己的地板：断句只认「。！？」⇒ 写成「…。[1]」的角标会掉到下一段，整句不进分母。
# 预登记：脱落数 / 附着数 ≥ 10% 就记「可核对性分母系统性少算」，本轮只登记不改尺子。
DETACH = ATTACH = 0
oor_rows: list[str] = []                            # 角标越界（编号 > 池子大小）的样本句
gap_samples: list[str] = []


def tag_of(npool: int) -> str:
    if npool == 0:
        return "empty"
    return "full" if npool >= MAXP else "thin"


for pid, _name in projs:
    try:
        papers_all = papers_of(pid)
        notes = knowledge.list_notes(pid)
    except Exception:                                               # noqa: BLE001
        continue
    reports = [m for m in notes if "report" in m["tags"]]
    if not reports:
        continue
    pool = report_mod.citable_papers(papers_all)
    widened = [p for p in papers_all if p.get("title")]
    npool = len(pool)
    for m in reports:
        notes_seen += 1
        body = knowledge.note_body(pid, m["note"]) or ""
        DETACH += len(re.findall(r"[。！？!?\n]\s*\[\d{1,2}\]", body))
        ATTACH += len(re.findall(r"\[\d{1,2}\]\s*[。！？!?\n]", body))
        gr = evidence.grounding_report(body, pool)
        if not gr["cited_sentences"]:
            continue
        reports_seen += 1
        b = B[tag_of(npool)]
        for k in ("cited", "grounded", "misnumbered", "true_gap"):
            v = gr["cited_sentences"] if k == "cited" else gr[k]
            b[k] += v
        for r in gr["rows"]:
            idxs = [int(x) for x in re.findall(r"\[(\d{1,2})\]", r["sentence"])]
            if any(i > npool for i in idxs):
                oor_rows.append(f"{pid}｜池 {npool}｜角标 {max(idxs)}｜{r['sentence'][:60]}")
            if r["status"] != "true_gap":
                continue
            if len(gap_samples) < 6:
                gap_samples.append(f"{pid}｜池 {npool}｜{r['sentence'][:60]}")
            # 只有这一句走第二次分类：把池子放大到「同项目全量有标题卡」再判一次
            if widened and evidence.grounding_report(r["sentence"], widened)["grounded"]:
                b["widen_found"] += 1

tot = {k: sum(b[k] for b in B.values()) for k in B["empty"]}
ne = {k: B["thin"][k] + B["full"][k] for k in B["empty"]}
emit()
emit(f"笔记 {notes_seen} 份｜其中带角标句 > 0 的报告 {reports_seen} 份｜带角标句合计 {tot['cited']}"
     f"｜接地 {tot['grounded']}｜挂错编号 {tot['misnumbered']}｜true_gap {tot['true_gap']}")
for key, label in (("empty", f"空池（0 篇卡）"), ("thin", f"非空未满（1—{MAXP - 1} 篇）"),
                   ("full", f"满池（{MAXP} 篇，被 citable_papers 截断过）")):
    b = B[key]
    rate = b["true_gap"] / b["cited"] if b["cited"] else None
    emit(f"  {label}：带角标句 {b['cited']}｜true_gap {b['true_gap']}"
         f"{'（未测：分母 0）' if rate is None else f' = {rate:.1%}'}"
         f"｜放大到全项目池后能找到依据的 {b['widen_found']}")
emit(f"  非空池合并：带角标句 {ne['cited']}｜true_gap {ne['true_gap']}"
     f"{'（未测）' if not ne['cited'] else f' = {ne['true_gap'] / ne['cited']:.1%}'}"
     f"｜其中放大后仍查无依据的 {ne['true_gap'] - ne['widen_found']}")
emit(f"  角标越界（编号 > 当轮池子大小）的句子 {len(oor_rows)} 处")
_det = f"{DETACH / ATTACH:.1%}" if ATTACH else "未测（附着数为 0）"
emit(f"  角标形态：句末附着「[n]。」{ATTACH} 处｜句号之后脱落「。[n]」{DETACH} 处（脱落/附着 = {_det}）")
if ATTACH and DETACH / ATTACH >= GAP_LINE:
    emit(f"  尺子地板（预登记）：脱落/附着 ≥ {GAP_LINE:.0%} ⇒ 逐句归因会错位到下一句，"
         f"记为「可核对性的分母与归因系统性偏差」，列进下一轮修法（本轮不改尺子）")
elif ATTACH:
    emit(f"  尺子地板（预登记）：脱落/附着 < {GAP_LINE:.0%} ⇒ 断句口径在本池上不构成归因问题")
for s in oor_rows[:4]:
    emit(f"    · {s}")
for s in gap_samples:
    emit(f"    true_gap 样本：{s}")

# ── 判读（只用上面印出来的数，按预登记分支走）───────────────────────────────
ne_rate = ne["true_gap"] / ne["cited"] if ne["cited"] else None
ne_true_after_widen = ne["true_gap"] - ne["widen_found"]
e_rate = B["empty"]["true_gap"] / B["empty"]["cited"] if B["empty"]["cited"] else None
if ne["cited"] < FLOOR_NONEMPTY:
    die(3, f"非空池桶只有 {ne['cited']} 句带角标（< {FLOOR_NONEMPTY}）⇒ 分母不足，"
           f"这一维本轮未测（不是「缺口不成立」，也不是「成立」）。要出数得先攒够非空池的报告，"
           f"或把判线连同分母一起重新预登记。空池桶 {B['empty']['cited']} 句同样只当背景。")
assert ne_rate is not None
if ne_rate >= GAP_LINE and ne_true_after_widen / ne["cited"] >= GAP_LINE:
    die(0, f"非空池 true_gap = {ne_rate:.1%} ≥ {GAP_LINE:.0%}，且放大池后仍查无依据的占 "
           f"{ne_true_after_widen / ne['cited']:.1%}（≥ 判线）⇒ 判 G1：真缺口稳定存在，"
           f"FAIR-RAG 式「只为缺口句发子查询」有病人，下一轮给它做 A/B（本轮不下效果结论）。")
if ne_rate >= GAP_LINE and ne["widen_found"]:
    die(0, f"非空池 true_gap = {ne_rate:.1%} ≥ {GAP_LINE:.0%}，但其中 {ne['widen_found']} 句"
           f"把池子放大到同项目全量就能找到依据 ⇒ 判 G2：病因是选单截断/剔无标题，"
           f"改选单，不立项二次检索。")
if e_rate is not None and e_rate >= GAP_LINE and B["empty"]["cited"] >= FLOOR_EMPTY:
    die(1, f"非空池 true_gap = {ne_rate:.1%} < {GAP_LINE:.0%}，而空池桶 = {e_rate:.1%}"
           f"（{B['empty']['cited']} 句 ≥ {FLOOR_EMPTY}）⇒ 判 G3：缺口全部落在「当轮没卡」上，"
           f"同一批熔断的 provider 再检索也变不出摘要 ⇒ 不立项，转去看供给熔断。")
die(1, f"非空池 true_gap = {ne_rate:.1%} < {GAP_LINE:.0%}，空池桶 {B['empty']['cited']} 句"
       f"（分母 < {FLOOR_EMPTY}，只当背景不据此归因）⇒ 判 G3 但归因粒度受限："
       f"二次检索在现有池子上没有病人，不立项。")
