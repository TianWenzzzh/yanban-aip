# -*- coding: utf-8 -*-
"""O3/O4：补检索的「作用面」与「材料天花板」——零模型调用、零外发检索，只读线上库。

两份读数：
  O3 作用面：真缺证据的句子（挂了编号、把整池扫一遍也接不住）占带角标句多少。
     这是 P3 唯一能发挥作用的地方——挂错编号那类由 `renumber_citations` 在写盘前治掉，
     已接住的更不需要补检索。
  O4 天花板：把**整张 papers 表**（这个项目群历史上引用过的全部论文，本轮实测 248 篇）当补充池，
     看能救回其中几句。口径要说准：它是**库内天花板**，不是「文献世界天花板」——
     一次真子查询完全可能带回库里没有的论文，所以这个数回答的是「材料已经在手能救回多少」。

判线（跑前写死，不看数再定）：
  O1 池子必须从库里取**带可核对文本**的论文（`evidence.sources` 非空），编号按 `## 引用论文`
     块的篇名反查；对不上（缺篇/同名两篇/<3 篇）整份剔除并印原因。
     🔴 为什么不许用产物 markdown 里的清单当池子：那里只有篇名、没有摘要，
     尺子看不见正文 ⇒ "缺证据"是假的。本仪器把两种口径**都算一遍**并印出差值（O6），
     让"口径造成的差"是量出来的，不是嘴上说的。
  O2 接线自检：判据必须是产品那把 `evidence.supplement_value`（本文件不重写判据），
     且合成正对照要全救回、错配池（含只会说套话的那篇）必须救回 0；不过 ⇒ RC=2 且不出数。
  O3 分母两侧：Σ带角标句 ≥ MIN_CITED 才印率；真缺证据 < MIN_GAP ⇒ O4 记「未测」不补假；
     缺证据率 < EMPTY_FACE_PP ⇒ 判「P3 的作用面近乎为空」（RC=1，可落进产品决策）。
  O4a 旋转对照：换给**下一份报告**那份材料。⚠️ 本轮实测：库里只有 3 个主题
     （光伏功率预测 / 医学图像分割 / 多智能体协作），换给下一份 ≈ 同一批材料，
     救回数与天花板逐份相同 ⇒ 旋转对照在这里先天测不出东西，只印数、不判线。
  O4b 跨领域对照（真正的判线）：换成「与该报告自己的池子词面共享 < MIN_TOKENS 个词」的那批论文
     （同量材料、错配领域）⇒ 救回必须**严格低于**天花板，否则 RC=2
     （那意味着这把尺对任何材料都说「能救」，读数作废）。
  O5 只读自证：DB 的 (mtime_ns, size) 与 KB 的 *.md 计数跑前后逐条相同；全程 `mode=ro`。
退出码：0=两份读数齐｜1=作用面近乎为空｜2=仪器或尺子坏｜3=分母不足未测。

用法（真读数只能在容器里跑：本机没有 /app/data）：
  MSYS_NO_PATHCONV=1 PYTHONUTF8=1 /d/anaconda/python.exe 输出/scripts/run_cloud_probe.py \
    E:/AIC智能体迁移包/输出/scripts/probe_rescuable_oracle.py 12
本机只能验接线（不碰库）：
  PYTHONUTF8=1 code/yanban/.venv/Scripts/python.exe 输出/scripts/probe_rescuable_oracle.py --selfcheck
未做（登记，不偷做）：真子查询能买到什么（O5 之后那一步）要拿每份报告当时的 intent 与缺口
  重构查询式并发真检索 ⇒ 得先现读 `research_intents` 表结构、从产品函数取词面，单开一轮。
"""
from __future__ import annotations

import json
import os
import pathlib
import random
import re
import sqlite3
import sys

os.environ.setdefault("LLM_MOCK", "1")
HERE = pathlib.Path(__file__).resolve()
CANDS = ["/app", str(HERE.parent), os.getcwd()]
# 容器里本文件被传到 /tmp，parents 只有两级 ⇒ 不许用 parents[2] 这种下标（第一版就在这崩在容器里）
CANDS += [str(p / "code" / "yanban") for p in HERE.parents]
for cand in CANDS:
    if (pathlib.Path(cand) / "companion").is_dir():
        sys.path.insert(0, cand)
        break
else:
    print("判读：找不到含 companion 的目录 ⇒ 不知道要 import 哪份产品码（RC=2）")
    sys.exit(2)

KB = pathlib.Path("/app/data/knowledge")
DB = pathlib.Path("/app/data/yanban.db")
CITE_RE = re.compile(r"\[\[(?:论文)[:：](.+?)\]\]")
SELFCHK_ONLY = "--selfcheck" in sys.argv
# 候选仓根：容器里本文件在 /tmp（parents 短，索引会越界），本机在 输出/scripts 下
CANDS = ["/app", str(HERE.parent), os.getcwd()] + [str(p / "code" / "yanban") for p in HERE.parents]
REPORTS_MAX = int(next((a for a in sys.argv[1:] if a.isdigit()), 12))
MIN_REPORTS = 6         # 少于 6 份，cluster bootstrap 就是拿噪声当样本
MIN_CITED = 60          # O3：印率的分母下限
MIN_GAP = 8             # O4：可救比例的分母下限（轮 158 的教训：薄分母等于写一条证伪不了的判据）
EMPTY_FACE_PP = 2.0     # O3 判线：缺证据率低于此 ⇒ 作用面近乎为空
BOOT_ITERS = 2000
SEED = 20260927


def die(code: int, msg: str) -> None:
    print(f"判读：{msg}（RC={code}）")
    sys.exit(code)


def db_state() -> tuple:
    st = DB.stat()
    return (st.st_mtime_ns, st.st_size, sum(1 for _ in KB.rglob("*.md")))


def rows_sql(sql: str, args: tuple = ()) -> list:
    """唯一出口：一律 `mode=ro`；打不开就 RC=2，绝不"顺手"用可写连接。"""
    try:
        con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    except sqlite3.Error as exc:
        die(2, f"只读连接打不开（{exc}）⇒ 不退回写连接")
    try:
        return con.execute(sql, args).fetchall()
    finally:
        con.close()


def schema() -> dict:
    """表与列现读，不照记忆写 SQL。"""
    out = {}
    for (name,) in rows_sql("SELECT name FROM sqlite_master WHERE type='table'"):
        out[name] = [r[1] for r in rows_sql(f"PRAGMA table_info({name})")]
    return out


def papers_of(pid: str) -> dict:
    out: dict[str, dict] = {}
    for (raw,) in rows_sql(
            "SELECT paper_json FROM papers WHERE project_id=? ORDER BY created_at DESC", (pid,)):
        try:
            p = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if isinstance(p, dict) and p.get("title") and p["title"] not in out:
            out[p["title"]] = p
    return out


def all_papers() -> dict:
    """O4 的天花板材料 = 整张 papers 表（按篇名去重，保留第一次出现的那份）。"""
    out: dict[str, dict] = {}
    for (raw,) in rows_sql("SELECT paper_json FROM papers ORDER BY created_at DESC"):
        try:
            p = json.loads(raw)
        except json.JSONDecodeError:
            continue
        t = p.get("title") if isinstance(p, dict) else None
        if t and t not in out:
            out[t] = p
    return out


def load():
    """逐份报告：正文 + 它自己在库里的可引用池（带摘要）+ 同份的"只有篇名"口径池（O6 用）。"""
    from companion.literature import evidence
    reports, skipped = [], []
    dirs = sorted((p for p in KB.iterdir() if p.is_dir()), key=lambda p: -p.stat().st_mtime)
    for d in dirs:
        if len(reports) >= REPORTS_MAX:
            break
        hit = text = None
        for f in sorted(d.glob("*.md")):
            t = f.read_text(encoding="utf-8", errors="replace")
            if "## 引用论文" in t:
                hit, text = f, t
                break
        if hit is None:
            continue
        cut = text.rfind("## 引用论文")
        body, block = text[:cut], text[cut:]
        links = [x.strip() for x in CITE_RE.findall(block)]
        tag = f"{d.name[:10]}/{hit.name[:18]}"
        if not links:
            skipped.append((tag, "引用块解析不出篇名"))
            continue
        if len(links) != len(set(links)):
            skipped.append((tag, "同名两篇 ⇒ 编号反查不唯一"))
            continue
        by_title = papers_of(d.name)
        pool = [by_title[t] for t in links if t in by_title]
        if len(pool) != len(links):
            skipped.append((tag, f"链接 {len(links)} 条、库内查得 {len(pool)} 篇"))
            continue
        if len(pool) < 3:
            skipped.append((tag, f"池子只有 {len(pool)} 篇（<3，rare 防线失效）"))
            continue
        thin = [p["title"][:20] for p in pool if not evidence.sources(p)]
        if thin:
            skipped.append((tag, f"{len(thin)} 篇拿不到可核对文本（O1）"))
            continue
        reports.append({"file": tag, "body": body, "pool": pool,
                        "titles_only": [{"title": p.get("title", ""), "abstract": ""}
                                        for p in pool]})
    return reports, skipped


def selfcheck() -> int:
    """O2：先证明这把尺会分方向，再拿它量线上产物。全合成、零网络、零模型调用。"""
    from companion.literature import evidence
    from companion.literature.evidence import supplement_value

    def paper(t, a):
        return {"title": t, "abstract": a, "year": 2024, "doi": ""}

    boiler = "the proposed deep learning framework experimental results show improvement"
    pool = [paper("Alpha graph clustering", f"alpha graph clustering groups nodes. {boiler}"),
            paper("Beta certificate lifetime", f"beta certificate lifetime predicts fraud. {boiler}"),
            paper("Gamma dialog colours", f"gamma warning dialog colours wording. {boiler}"),
            paper("Delta cache timing", f"delta cache timing leaks patterns. {boiler}")]
    # 🔴 那两句必须避开主池全部词面：只要沾上主池某篇的两个稀有词，`_classify` 就判
    #    「挂错编号」（池内别处接得住）而不是「真缺证据」，于是分母少一——第一版就在这判了个假红。
    extra = [paper("Eps drift in vendors",
                   "eps drift across quorum vendors exposes storefront ledger gaps at 0.41 quartile"),
             paper("Zeta quench factor",
                   "zeta quenching factors for nuclear recoils in liquid argon time projection")]
    junk = [paper("Crop rotation soil", "crop rotation increases soil organic carbon in field trials"),
            paper("Retail price elasticity", "online retail price elasticity from six million tx"),
            paper("Deep learning boilerplate", boiler)]
    t = ("综述。\n\neps drift across quorum vendors exposes storefront ledger gaps at 0.41 "
         "quartile[1]\nzeta quenching factors for nuclear recoils in liquid argon time "
         "projection[2]\n")
    checks = []
    good = supplement_value(t, pool, extra)
    checks.append(("R-a 真摘录必须全救回", good["true_gap"] == 2 and good["rescuable"] == 2, good))
    bad = supplement_value(t, pool, junk)
    checks.append(("R-b 错配池（含只会说套话的那篇）必须救回 0",
                   bad["true_gap"] == 2 and bad["rescuable"] == 0, bad))
    empty = supplement_value("综述。\n\nbeta certificate lifetime predicts fraud[2]\n", pool, extra)
    checks.append(("R-c 已接住 ⇒ 分母 0 报 None",
                   empty["true_gap"] == 0 and empty["rescue_rate_of_gap"] is None, empty))
    checks.append(("R-d 本仪器用的是产品那把尺",
                   supplement_value is getattr(evidence, "supplement_value", None),
                   getattr(evidence, "supplement_value", None)))
    fail = [n for n, ok, _ in checks if not ok]
    for n, ok, blob in checks:
        print(f"  {'✅' if ok else '🔴'} {n}" + ("" if ok else f"｜{str(blob)[:170]}"))
    if fail:
        die(2, f"O2 接线自检 {len(fail)}/{len(checks)} 条不成立 ⇒ 尺子坏了，不出读数")
    print(f"O2 接线自检 {len(checks)}/{len(checks)} 通过（含「错配池必须 0」与「用产品那把尺」）")
    return 0


def cluster_ci(rows: list, num: str, den: str) -> tuple | None:
    """按报告整块重抽：句内相关，逐句重抽会把 12 份报告当成 200 份独立样本，CI 会假窄。"""
    files = sorted({r["file"] for r in rows})
    byfile = {f: [r for r in rows if r["file"] == f] for f in files}
    rng = random.Random(SEED)
    stats = []
    for _ in range(BOOT_ITERS):
        picked = []
        for f in (files[rng.randrange(len(files))] for _ in files):
            picked += byfile[f]
        d = sum(r[den] for r in picked)
        if d:
            stats.append(100.0 * sum(r[num] for r in picked) / d)
    if len(stats) < BOOT_ITERS // 2:
        return None
    stats.sort()
    return stats[int(0.025 * len(stats))], stats[min(int(0.975 * len(stats)), len(stats) - 1)]


def rate_block(tag: str, rows: list, num: str, den: str) -> None:
    n, d = sum(r[num] for r in rows), sum(r[den] for r in rows)
    ci = cluster_ci(rows, num, den)
    tail = f"｜95% CI（按报告整块重抽 {BOOT_ITERS} 次，种子 {SEED}）[{ci[0]:+.1f},{ci[1]:+.1f}]pp" if ci \
        else "｜CI 没算出来（有效重抽不足）"
    print(f"{tag} {n}/{d} = {100.0 * n / d if d else 0:.1f}pp{tail}")


def main() -> int:
    rc = selfcheck()
    if rc:
        return rc
    if SELFCHK_ONLY:
        print("判读：只验了接线（--selfcheck），没碰库也没发检索 ⇒ 没有读数（RC=0 仅指接线通过）")
        return 0
    import llm
    if not getattr(llm, "LLM_MOCK", False):
        die(2, "LLM_MOCK 没生效 ⇒ 本仪器声称零模型调用，做不到就别出数")
    if not KB.is_dir() or not DB.is_file():
        die(2, f"容器路径不对（KB={KB.is_dir()} DB={DB.is_file()}）⇒ 本机跑不了真读数，"
               "请用 run_cloud_probe 送进容器（或只跑 --selfcheck）")
    st0 = db_state()
    sh = schema()
    if "papers" not in sh or "paper_json" not in sh.get("papers", []):
        die(2, f"表结构里没有 papers.paper_json（拿到 {list(sh)[:8]}…）⇒ 现读结果与取数不符")
    reports, skipped = load()
    print(f"\nO1 报告：上限 {REPORTS_MAX} → 可用 {len(reports)} 份｜剔除 {len(skipped)} 份")
    for t, why in skipped[:12]:
        print(f"    跳过 {t}：{why}")
    if len(reports) < MIN_REPORTS:
        die(3, f"可用报告 {len(reports)} < {MIN_REPORTS} ⇒ 不测")
    from companion.literature.evidence import (MIN_TOKENS, _tokens, sources, supplement_value)
    universe = all_papers()
    usable_universe = {t: p for t, p in universe.items() if t}
    print(f"O4 天花板材料：整张 papers 表 {len(universe)} 篇（去重后 {len(usable_universe)}）")
    rows = []
    for i, rp in enumerate(reports):
        mine = {(p.get("title") or "").strip() for p in rp["pool"]}
        extra = [p for t, p in usable_universe.items() if t and t not in mine]
        nxt = reports[(i + 1) % len(reports)]
        nxt_mine = {(p.get("title") or "").strip() for p in nxt["pool"]}
        rotated = [p for t, p in usable_universe.items() if t and t not in nxt_mine]
        # O4b 跨领域对照：与本报告自己的池子词面几乎不相交的那批（同量材料、错配领域）
        own: set = set()
        for q in rp["pool"]:
            own |= _tokens(q.get("title") or "")
            for _lab, _b in sources(q):
                own |= _tokens(_b)
        foreign = [q for tt, q in usable_universe.items()
                   if tt not in mine and len(_tokens(tt) & own) < MIN_TOKENS]
        s = supplement_value(rp["body"], rp["pool"], extra)
        r = supplement_value(rp["body"], rp["pool"], rotated)
        f = supplement_value(rp["body"], rp["pool"], foreign)
        thin = supplement_value(rp["body"], rp["titles_only"], [])   # O6：产物口径（无摘要）
        rows.append({"file": rp["file"], "pool_n": len(rp["pool"]), "extra_n": s["extra_n"],
                     "cited": s["cited_sentences"], "grounded": s["grounded"],
                     "misnumbered": s["misnumbered"], "gap": s["true_gap"],
                     "rescued": s["rescuable"], "rot_rescued": r["rescuable"],
                     "foreign_rescued": f["rescuable"], "foreign_n": len(foreign),
                     "thin_gap": thin["true_gap"]})
        print(f"  {rp['file'][:30]:30s} 池 {rows[-1]['pool_n']:2d} 天花板 {rows[-1]['extra_n']:4d}"
              f" 句 {rows[-1]['cited']:3d} 接地 {rows[-1]['grounded']:3d}"
              f" 挂错 {rows[-1]['misnumbered']:3d} 缺证据 {rows[-1]['gap']:3d}"
              f"（无摘要口径 {rows[-1]['thin_gap']:3d}） 天花板可救 {rows[-1]['rescued']:3d}"
              f" 旋转 {rows[-1]['rot_rescued']:3d}"
              f" 跨领域可救 {rows[-1]['foreign_rescued']:3d}" f"（跨领域候选 {rows[-1]['foreign_n']:4d}）")
    cited = sum(r["cited"] for r in rows)
    gap = sum(r["gap"] for r in rows)
    mis = sum(r["misnumbered"] for r in rows)
    thin = sum(r["thin_gap"] for r in rows)
    resc = sum(r["rescued"] for r in rows)
    rot = sum(r["rot_rescued"] for r in rows)
    forn = sum(r["foreign_rescued"] for r in rows)
    if cited < MIN_CITED:
        print(f"\n合计：报告 {len(rows)} 份｜带角标句 {cited}｜缺证据 {gap}")
        die(3, f"带角标句 {cited} < {MIN_CITED} ⇒ 分母不足，只印计数不印比例")
    print(f"\nO3 作用面（分母 = 带角标句 {cited}）")
    print(f"  接地 {sum(r['grounded'] for r in rows)} 句｜挂错编号 {mis} 句｜真缺证据 {gap} 句")
    print(f"  缺证据率 {100.0 * gap / cited:.1f}pp｜挂错编号率 {100.0 * mis / cited:.1f}pp")
    print(f"O6 口径差（同一批正文、同一把尺）：池子带摘要时缺证据 {gap} 句，"
          f"换成产物里那种「只有篇名」的清单时 {thin} 句 ⇒ 差 {thin - gap} 句")
    st1 = db_state()
    if st1 != st0:
        die(2, "读数期间线上库动了 ⇒ 只读自证失败，读数作废")
    print("O5 只读自证：DB (mtime,size) 与 KB *.md 计数前后相同 ⇒ 全程 mode=ro、零写入")
    if gap < MIN_GAP:
        if 100.0 * gap / cited < EMPTY_FACE_PP:
            print(f"判读：真缺证据只有 {gap} 句（率 {100.0 * gap / cited:.1f}pp < {EMPTY_FACE_PP}pp，"
                  f"且 < 分母下限 {MIN_GAP}）⇒ O4 天花板没有可算的分母，记「未测」不补假。"
                  "**P3 补检索的作用面近乎为空**：线上报告的引用毛病是「挂错编号」，"
                  "而那由 `renumber_citations` 在写盘前治掉（RC=1）")
            return 1
        die(3, f"真缺证据 {gap} 句 < {MIN_GAP} ⇒ O4 未测")
    print(f"\nO4 天花板：救回 {resc}/{gap} = {100.0 * resc / gap:.1f}% of gap")
    rate_block("   上限贡献（救回/带角标句）：", rows, "rescued", "cited")
    print(f"O4a 旋转对照：换给下一份报告 ⇒ 救回 {resc} → {rot}"
          "（库里只有 3 个主题，先天不分辨 ⇒ 只印数，不判线）")
    print(f"O4b 跨领域对照：同量材料换成词面不相交的那批 ⇒ 救回 {resc} → {forn}")
    rate_block("   跨领域对照的率（分母同为带角标句）：", rows, "foreign_rescued", "cited")
    if forn >= resc:
        die(2, f"跨领域对照救回 {forn} 没低于天花板 {resc} ⇒ 这把尺对任何材料都说「能救」，读数作废")
    print("判读：O3/O4 齐。O4 是**库内材料的天花板**（真子查询可能带回库里没有的论文，"
          "也可能一篇带不回）；这是上限不是效果，不许写进任何效果口径（RC=0）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
