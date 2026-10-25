"""事项一的前后数：把 `renumber_citations` 回放到线上真报告上，机检通过率动了几pp。

零模型调用、零落库。读的是容器里既有的 `data/knowledge/{pid}/*.md`（轮 123 定过：报告正文在
这里，不在 `messages.deliver`）与生产库 `papers.paper_json`（列名猜错会静默返回 []，
于是"没有依据池"会被读成"接地率 0%"——轮 154 的教训，逐字沿用那段读法）。

🔴 编号必须由报告自己反推，不能拿 DB 顺序凑：`grounding_report` 认的是**报告里那个 [n]**，
而 n = 生成当时 `citable_papers` 的列表位置。所以依据池按报告末尾「## 引用论文」里
`[[论文：标题]]` 的**出现顺序**重建（那是程序自己写的，与 `papers_block` 同源），
再用标题回查 DB 取原文。任一条对不上 ⇒ 该份报告整个剔出分母（宁可少样本，不要错位样本）。
同时只测正文：「引用论文」节是程序拼的，它的首"句"是一条论文标题，会被接地尺数成句子
（轮 106 已经栽过一次：g68 的 11% 就是这个假高度）。

判据（跑前写死，跑完不许改）：
  R0 零真调用：`llm.REAL_CALLS` 增量必须为 0（这条链全是纯函数；一旦 >0 说明被测物偷偷调模型）。
  R1 只读自证：DB 的 (mtime,size) 与 KB 里 .md 数跑前跑后逐位相同；不同 ⇒ 读数作废（RC=2）。
  R2 建表哨兵：容器里的 `report.py` 必须有 `clean_report_with_ledger`（事项一那次改动）。
     没有 ⇒ 这份"改后"读数与线上将跑的不是同一套代码（RC=2）。
  R3 分母两侧都写：可用报告数 / 被剔报告数（含剔除原因）/ 带角标句 / 未接地句 / 改指处数。
     带角标句 <20 ⇒ 只作定性，不印率、不下结论（RC=3）。
  R4 主判据 = 机检通过率 grounded/cited 的合并前后差：≥+5pp ⇒ 记「有效」；
     <5pp 或 >0 份报告变差 ⇒ 记「未证明」，如实落档（RC=1），不改判据再来一次。
  R5 负控（--plant-off，RC=2）：关掉改指、把同一份文本用尺子量两遍 ⇒ 两遍必须逐位相同。
     若不同 ⇒ 尺子本身不确定，本场与历史所有接地读数一起作废。
  R6 生成路径复量：零模型调用直接调容器里的 `report._finalize`（KB_ROOT 改指 /tmp，不碰生产库），
     必须交出 `citations_renumbered=1` 且落盘正文里的角标已被改指 ⇒ 回放数的不是"函数会这样"，
     而是"产品出口就是这样"。不符 ⇒ RC=2。
"""
from __future__ import annotations

import json
import pathlib
import re
import sqlite3
import sys

sys.path.insert(0, "/app")
sys.path.insert(0, ".")
_HERE = pathlib.Path(__file__).resolve()
for _cand in ["/app", str(pathlib.Path.cwd()), *map(str, _HERE.parents)]:
    if (pathlib.Path(_cand) / "companion").is_dir():
        sys.path.insert(0, _cand)
        break
else:
    print("判读：找不到含 companion/ 的仓根 ⇒ 仪器没连上被测物（RC=2）")
    sys.exit(2)

import llm                                                        # noqa: E402
from companion.conversation import mentor                         # noqa: E402
from companion.conversation import report as report_module        # noqa: E402
from companion.literature import evidence                         # noqa: E402

KB_CANDIDATES = [pathlib.Path(p) for p in
                 ("/app/data/knowledge", "/app/companion/data/knowledge", "data/knowledge")]
DB_CANDIDATES = [pathlib.Path(p) for p in
                 ("/app/data/yanban.db", "/app/companion/data/yanban.db", "data/yanban.db")]


def _pick(paths: list[pathlib.Path]) -> pathlib.Path:
    for p in paths:
        if p.is_file() or (p.is_dir() and any(p.rglob("*.md"))):
            return p
    return paths[0]


KB = _pick(KB_CANDIDATES)
DB = _pick(DB_CANDIDATES)
REPORTS_MAX = int(next((a for a in sys.argv[1:] if a.isdigit()), 12))
PLANT_OFF = "--plant-off" in sys.argv
MIN_CITED = 20           # 分母下限（R3）：低于此只作定性
MIN_GAIN_PP = 5.0        # 主判据（R4）：合并通过率的提升门槛，单位百分点
CITE_RE = re.compile(r"\[\[(?:论文)[:：](.+?)\]\]")


def db_stamp() -> tuple:
    st = DB.stat()
    return (st.st_mtime_ns, st.st_size)


def db_papers(pid: str) -> dict[str, dict]:
    """标题 → 论文记录（同名取最新一条）。"""
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    try:
        rows = con.execute(
            "SELECT paper_json FROM papers WHERE project_id=? ORDER BY created_at DESC",
            (pid,)).fetchall()
    except sqlite3.Error as exc:
        print(f"  读论文失败 {pid}：{exc}")
        return {}
    finally:
        con.close()
    out: dict[str, dict] = {}
    for (raw,) in rows:
        try:
            p = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if isinstance(p, dict) and p.get("title") and p["title"] not in out:
            out[p["title"]] = p
    return out


def split_body(text: str) -> str:
    """去掉程序自动拼的「## 引用论文」节，只留正文。"""
    idx = text.rfind("## 引用论文")
    return (text[:idx] if idx > 0 else text).strip()


# R7：接地尺按「。！？\n」切句，所以写在句号**后面**的角标会自成一段不足 8 字的碎片，
# 那句等于没挂引用 —— 分子分母两边都看不见它。线上到底有多少这种标记必须数出来，
# 否则"带角标句 174"会被当成"报告里一共有这么多引用"。
TRAILING_RE = re.compile(r"[。！？!?\n]\s*\[\d{1,2}\]")


def marker_shape(text: str) -> tuple[int, int]:
    """(全部角标标记数, 紧跟在句末标点之后的标记数)。"""
    return (len(re.findall(r"\[\d{1,2}\]", text)), len(TRAILING_RE.findall(text)))


def reports() -> list[tuple[str, str, str]]:
    out = []
    dirs = sorted((d for d in KB.iterdir() if d.is_dir()), key=lambda p: -p.stat().st_mtime) \
        if KB.is_dir() else []
    for d in dirs:
        for f in sorted(d.glob("*.md")):
            text = f.read_text(encoding="utf-8", errors="replace")
            if re.search(r"\[\d{1,2}\]", split_body(text)):
                out.append((d.name, f.name, text))
                break                       # 一个项目取一份，避免同项目多份互相抄
        if len(out) >= REPORTS_MAX:
            break
    return out


def pool_for(text: str, by_title: dict[str, dict]) -> tuple[list[dict], list[str]]:
    """按报告自己的「引用论文」顺序重建依据池；返回 (池, 对不上的标题)。"""
    link_block = text[text.rfind("## 引用论文"):] if "## 引用论文" in text else ""
    titles = [t.strip() for t in CITE_RE.findall(link_block)]
    pool, missing = [], []
    for t in titles:
        rec = by_title.get(t)
        if rec is None:               # 标题被程序改名过 / 论文行已被清理
            missing.append(t)
            continue
        pool.append(rec)
    return (pool, missing) if not missing else ([], missing)


def arm(text: str, papers: list[dict], *, renumber: bool) -> tuple[dict, int]:
    """同一把尺量一次；renumber=True 时先走事项一那道清洗（正文已清洗过，闸门近乎幂等）。"""
    if renumber:
        out, moved = report_module.clean_report_with_ledger(text, papers)
    else:
        out, moved = mentor.strip_dangling_citations(
            mentor.clean(mentor.strip_unverified(text, papers)), papers), 0
    return evidence.grounding_report(out, papers), moved


def live_path() -> tuple[bool, str]:
    """R6 线上生成路径复量：不烧模型，直接拿线上那份 `_finalize` 跑一次收尾。

    回放测的是"这道闸放在真报告上值几 pp"，这一条测的是"这个数真的从产品出口出来"。
    `KB_ROOT` 改指 /tmp ⇒ 生产知识库一个字节都不动（`upsert_note` 只写文件，已读码确认）。
    合成池的两篇摘要都短于 80 字 ⇒ `_pool_has_text` 为假，补引用/反证两道闸门不开工，
    所以这条断言是单因子的；R0 的真调用增量会当场证明"没偷偷叫模型"。
    """
    import companion.knowledge as knowledge
    from companion.intent.model import ResearchIntent

    pool = [{"title": "Visual Phishing Detection with CLIP",
             "abstract": "We embed URL strings with CLIP and train a classifier on 50000 pages."},
            {"title": "A Survey of Multi-Agent LLM Systems",
             "abstract": "This survey reviews 120 papers on multi-agent LLM protocols."}]
    # 每节各写一句**不同**的话，只有一句挂错角标 ⇒ 记账必须恰好是 1（首版我让五节复读同一句，
    # 线上产品如实报了 5 处，红的是我的期望值而不是产品：改指按标记计数，不按节计数）。
    bodies = ["这篇用 CLIP 把 URL strings 编码后训练分类器，跑了 50000 个页面 [2]。",
              "两篇的差别在数据面：CLIP 那篇讲的是 URL strings 的编码方式 [1]。",
              "多智能体那一侧给的是一个数量级的对照：120 papers [2]。",
              "没有一篇报告过跨站点迁移的效果，这一句不挂角标。",
              "下一步把这批 50000 个页面的做法迁移到中文语料 [1]。"]
    raw = "\n\n".join(f"## {s}\n{bodies[i]}\n" for i, s in enumerate(report_module.SECTIONS))
    keep = knowledge.KB_ROOT
    knowledge.KB_ROOT = pathlib.Path("/tmp/probe_renumber_vault")
    title = "综述：探针自检"
    try:
        out = report_module._finalize("probeRn6", ResearchIntent(project_id="probeRn6",
                                                                 topic="钓鱼网站检测"),
                                      title, pool, raw, False, False)
        # 文件名由产品自己的 _slug 算（"："是全角，不在它剔除的字符类里 —— 别照猜写死）
        landed = (knowledge.KB_ROOT / "probeRn6" / f"{knowledge._slug(title)}.md").read_text(
            encoding="utf-8")
    finally:
        knowledge.KB_ROOT = keep
    rep = evidence.grounding_report(split_body(landed), pool)
    ok = (out.get("citations_renumbered") == 1
          and "这篇用 CLIP 把 URL strings 编码后训练分类器，跑了 50000 个页面 [1]。" in landed
          and (rep["cited_sentences"], rep["grounded"], rep["misnumbered"], rep["true_gap"])
          == (4, 3, 0, 1))
    return ok, (f"citations_renumbered={out.get('citations_renumbered')}"
                f"｜落盘逐句 带角标 {rep['cited_sentences']} 接地 {rep['grounded']} "
                f"错号 {rep['misnumbered']} 真空 {rep['true_gap']}"
                f"｜首句 {[l for l in landed.splitlines() if l.startswith('这篇用')][:1]}")



def main() -> int:
    if not KB.is_dir():
        print(f"判读：KB 目录 {KB} 不存在 ⇒ 扫错了地方，不是「没有报告」（RC=2）")
        return 2
    if not hasattr(report_module, "clean_report_with_ledger"):
        print("判读：容器里的 report.py 没有 clean_report_with_ledger ⇒ 事项一没上线，"
              "这份读数不属于将跑的 build（RC=2）")
        return 2
    stamp0, kb0, calls0 = db_stamp(), sum(1 for _ in KB.rglob("*.md")), int(getattr(llm, "REAL_CALLS", 0))
    rs = reports()
    print(f"扫到带角标的报告 {len(rs)} 份（上限 {REPORTS_MAX}）"
          f"{'｜🔴 植入：关掉改指，两遍都只量尺子' if PLANT_OFF else ''}")
    if not rs:
        print("判读：一份带角标的报告都没扫到 ⇒ 判据或路径坏了（RC=2）")
        return 2
    ok6, why6 = live_path()
    print(f"R6 线上生成路径复量（零模型调用，直接调容器里那份 `_finalize`）：{why6}")
    if not ok6:
        print("判读：产品出口没交出 citations_renumbered 或落盘正文没被改指 ⇒ 上线的那份不是这套行为（RC=2）")
        return 2
    print("  R6 判定：✅ 记账字段与落盘正文都对（生产知识库未参与：KB_ROOT 已改指 /tmp）")


    rows, skipped = [], []
    for pid, name, text in rs:
        body = split_body(text)
        pool, missing = pool_for(text, db_papers(pid))
        if len(pool) < 2:
            skipped.append((pid[:10], name[:22],
                             f"池不足 2 篇（可对上 {len(pool)}，对不上 {len(missing)}）"))
            continue
        if missing:
            skipped.append((pid[:10], name[:22], f"{len(missing)} 条引用标题回查不到原文"))
            continue
        b, _ = arm(body, pool, renumber=False)
        a, moved = arm(body, pool, renumber=PLANT_OFF is False)
        c, _ = arm(body, pool, renumber=False)      # 同臂复算：尺子必须逐位相同（R5）
        m_all, m_trail = marker_shape(body)
        if (c["cited_sentences"], c["grounded"], c["misnumbered"]) != \
           (b["cited_sentences"], b["grounded"], b["misnumbered"]):
            print(f"  🔴 尺子不确定：{pid[:10]} {name[:22]} 同臂复算 {b} → {c}")
        rows.append({"pid": pid[:10], "name": name[:22], "pool": len(pool), "moved": moved,
                     "m_all": m_all, "m_trail": m_trail,
                     "b_cited": b["cited_sentences"], "b_g": b["grounded"], "b_mis": b["misnumbered"],
                     "b_gap": b["true_gap"], "a_cited": a["cited_sentences"], "a_g": a["grounded"],
                     "a_mis": a["misnumbered"], "a_gap": a["true_gap"]})

    stamp1, kb1, calls = db_stamp(), sum(1 for _ in KB.rglob("*.md")), int(getattr(llm, "REAL_CALLS", 0)) - calls0
    print("\n逐份（pid｜文件｜池｜改指处数｜标记总数｜句点后标记 → 带角标句 地/错/空 → 地/错/空）：")
    for r in rows:
        print(f"  {r['pid']}｜{r['name']}｜{r['pool']}｜{r['moved']}｜{r['m_all']}｜{r['m_trail']}｜"
              f"{r['b_cited']} {r['b_g']}/{r['b_mis']}/{r['b_gap']} → {r['a_g']}/{r['a_mis']}/{r['a_gap']}")
    if skipped:
        print("剔出分母（原因）：")
        for s in skipped:
            print("  " + "｜".join(str(x) for x in s))

    T = {k: sum(r[k] for r in rows) for k in
         ("pool", "moved", "m_all", "m_trail", "b_cited", "b_g", "b_mis", "b_gap",
          "a_cited", "a_g", "a_mis", "a_gap")}
    print(f"\nR0 真模型调用增量 {calls} 次（必须 0）")
    print(f"R1 只读自证：DB (mtime,size) {stamp0} → {stamp1}｜KB .md {kb0} → {kb1}")
    if stamp0 != stamp1 or kb0 != kb1:
        print("判读：生产库或 KB 被碰过 ⇒ 这台仪器不该有任何写操作，读数作废（RC=2）")
        return 2
    if calls:
        print("判读：接地尺子里冒出了真模型调用 ⇒ 被测物不是纯函数，本场读数不可复现（RC=2）")
        return 2
    print(f"R2 建表哨兵：容器 report.py 含 clean_report_with_ledger ⇒ 线上 build 与本读数同源")
    print(f"R3 分母：可用报告 {len(rows)}｜剔除 {len(skipped)}｜带角标句 {T['b_cited']}（复算 {T['a_cited']}）"
          f"｜改前 接地 {T['b_g']} 错号 {T['b_mis']} 真空 {T['b_gap']}"
          f"｜改后 接地 {T['a_g']} 错号 {T['a_mis']} 真空 {T['a_gap']}｜改指 {T['moved']} 处")
    if not rows or T["b_cited"] < MIN_CITED:
        print(f"判读：可用报告 {len(rows)} 份、带角标句 {T['b_cited']} <{MIN_CITED} ⇒ "
              "只作定性，不印率、不下结论（RC=3）")
        return 3
    print(f"R7 标记形状：正文里角标共 {T['m_all']} 处，其中紧跟句末标点之后的 {T['m_trail']} 处；"
          f"尺子认得的带角标句 {T['b_cited']} 句 ⇒ "
          f"{'🔴 有角标挂在句末标点之后，那些句子既不进分子也不进分母（尺子的盲区，如实报）' if T['m_trail'] else '没有角标落在句点之后'}")
    if T["b_cited"] != T["a_cited"]:
        print("判读：清洗前后带角标句数变了 ⇒ 改指之外还动了句子，归因不是单因子（RC=2）")
        return 2
    rb, ra = T["b_g"] / T["b_cited"], T["a_g"] / T["b_cited"]
    pp = 100 * (ra - rb)
    worse = [r["pid"] for r in rows if r["a_g"] < r["b_g"]]
    print(f"R4 机检通过率（合并，分母 = 带角标句 {T['b_cited']}）：{100 * rb:.1f}% → {100 * ra:.1f}%"
          f"（{pp:+.1f}pp）｜变差的报告 {len(worse)} 份 {worse[:4]}")
    if PLANT_OFF:
        print("判读：" + (f"植入场（关掉改指）通过率差 {pp:+.1f}pp、改指 {T['moved']} 处 ⇒ 尺子或清洗不确定，"
                         "本场与历史接地读数一起作废" if (abs(pp) > 1e-9 or T["moved"]) else
                         "植入场两遍逐位相同、改指 0 处 ⇒ 尺子确定，主判据的差异只可能来自改指这一步"
                         "（RC=2，本场是负控制）"))
        return 2
    if not T["moved"]:
        print("判读：线上真实报告里没有一处角标可改 ⇒ 事项一在真实分布上未证明有效，"
              "记「未证明」，不改判据再来一次（RC=1）")
        return 1
    if pp >= MIN_GAIN_PP and not worse:
        print(f"判读：达标 —— 改指 {T['moved']} 处、通过率 +{pp:.1f}pp ≥ {MIN_GAIN_PP}pp 且无一份变差（RC=0）")
        return 0
    print(f"判读：未达预登记线（{pp:+.1f}pp vs 门槛 +{MIN_GAIN_PP}pp，变差 {len(worse)} 份）⇒ "
          "记「未证明」并落档，不改判据再来一次（RC=1）")
    return 1


if __name__ == "__main__":
    sys.exit(main())
