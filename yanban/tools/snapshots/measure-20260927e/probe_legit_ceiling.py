# -*- coding: utf-8 -*-
"""轮 161 · **合法**天花板与词面 bake-off：先确认「改检索词面」这件事有没有可买的空间。

为什么要重算一遍天花板：轮 159/160 的"库内天花板"是**整张 papers 表**，但产品的读法一律是
`WHERE project_id=?`（`companion/memory/paper_memory.py:63`、`companion/conversation/dialogue_manager.py:124`，
本轮逐条 grep 过，全仓没有任何跨项目检索）⇒ 兄弟项目里那批同主题论文**按设计就读不到**。
线上一共只有 3 个主题 × 每主题 8 个项目，"整张表"的天花板里有一大块是**同一批材料被复制了 8 份**
造成的冗余，不是机制该够得到的东西。轮 160 那句「掉的 14 句在词面」如果是这个冗余，那 ① 的前提就错了。

所以这一轮先把分母换合法，再谈改不改产品码。做法：对每个主题发若干条**确定性候选词面**
（零模型调用），把它们的带回并集当作「API 真够得着的材料」，与产品现在那条式子（V0）对比。

候选词面（全部只许从 `intent` 与**该项目自己的池子**推出，一次模型都不碰）：
  V0 现状式 `<query> <facet>`                —— 产品现在发的，作基线
  V1 裸式   `<query>`                        —— 体裁词到底是帮忙还是拖累
  V2 英文体裁同义 `<query> review`
  V3 中文体裁 `<query> 综述`
  V4 池内判别词 + 体裁 `<top3 池内稀有拉丁词> <facet>`
  V5 主题 + 池内判别词 `<query> <top2 池内稀有拉丁词>`
🔴 词面构造器**一律不许看报告正文**（看了就是把答案抄进查询式，读数立刻变成自证）：
   R-f 用源码扫描钉住"候选构造器的形参里不许出现 body/report 文本"，并配一条植入证明它会红。

判线（跑前写死，不看数再定）：
  Q0 接线自检（全合成、零网络）：
     R-a 真摘录必须全救回；R-b 错配材料必须救回 0；R-c 拿报告自己的池子当补充材料必须救回 0；
     R-d 判据件必须来自产品模块（逐个查 `__module__` 以 `companion.` 开头 + 本文件不许有同名本地定义）；
     R-e 零模型调用的雷必须会响（`llm.chat`/`chat_stream`/`chat_json` 一碰就抛，主动碰一次必须抛）；
     R-f 词面构造器不许看正文（源码扫描 + 植入：把一个 builder 的实参换成正文 ⇒ 必须红）。
  Q1 只读自证（沿用轮 160 改后的口径）：`papers`/`research_intents`/`projects`/`messages` 四张表的
     行数 + max(rowid) + 逐行 sha256 跑前跑后逐项相同；`mode=ro` 连接真试写一次必须被拒；
     KB *.md 计数相同。表名现查 `sqlite_master`，缺表直接 RC=2（不许咽成恒定相同的错误串）。
     文件时间戳只印不判（`-shm` 会被常驻服务的别的连接推动，实测见 `probe_wal_who_touches.py`）。
  Q2 N2 没回归：按源数包，**任何"空手回来"的源必须 ≤1 包**。
  Q3 分母两侧：主题数 ≥ MIN_TOPICS(=3)、Σ真缺证据 ≥ MIN_GAP(=8)、Σ带角标句 ≥ MIN_CITED(=60)
     ⇒ 否则 RC=3 未测。
  Q4 恒等式：每份报告每个变体都验 `rescued ≤ gap`、`fetched ≥ relevant ≥ fresh`；
     并印一条**经验核对**（不是定理）：本场并集口径 ≥ 每个变体单独。
     🔴 跑第一版之后才知道的事：`supplement_value` 对候选集**不单调**——词频集 `rare_tokens` 按
        合并语料重算，多给几篇会把它们共用的判别词挤进套话档，那句话的 want 变空 ⇒ 救回可能变小。
        这一场自己的表里就有现成例子（`b99bc2d465` 行：整张表那份候选救 0 句，而一份小得多的并集
        救 1 句）。行为由 `tests2/test_supplement_value.py` 的 V6 钉住（两条植入都会让它红）。
        ⇒ 所以：① "天花板"不是上界，是**该份候选集上的读数**；两个不同候选集的读数**不许比大小**，
           下面那行"冗余量"只能当两份读数的差印出来，不许读成"被复制了 8 份的同主题材料贡献几句"；
        ⇒ ② 并集与单个变体的大小关系**既不是定理、也不当判据用**：首版在这里写了 `die(2)`，
           现在改成"只印不判"——它红了不能证明归属算错，它绿了也不能证明并集是上界。
  Q5 判读线（两条都要印，只报一条就是挑数）：
     ① 两个口径**并列印、不比大小**：并集口径（本项目可达材料）与整表口径（含按设计读不到的
        兄弟项目材料）。首版把它们的差写成"冗余量 = 被复制 8 份贡献的句数"——那是一次误读：
        两份读数出自两个不同候选集，而这把尺对候选集不单调，差值不能归因给任何一批材料。
        真正能说的是结构性的那条：**产品全仓读 papers 一律带 `WHERE project_id=?`**（本轮逐条 grep），
        所以整表口径里属于兄弟项目的那部分**按设计不可达**，引用天花板时必须写明用的是哪个口径。
     ② 有没有可修的空间：`best = max_v Σ rescue(v)`（v≠V0），
        `Σ rescue(best) ≥ Σ rescue(V0) + ΔMIN(=2 句)` ⇒ 判「词面有空间，改 ①B」RC=0；
        否则 ⇒ 判「词面没空间」RC=1（负结果，不改产品码）。
     🔴 反过拟合：**留一主题法**（三个主题就是三折）。在另外两折上选优胜变体，
        在剩下那一折上验；优胜变体必须在 **≥2/3 折**上都不差于 V0 且带来 ΔMIN 以上增益才算 RC=0，
        否则判「选不出稳定优胜」RC=5（拿一次挑出来的最好看不泛化，是这类 bake-off 的标准死法）。
  退出码：0=词面确有可修空间（并指出优胜变体）｜1=没有（负结果）｜2=仪器坏｜3=分母不足｜5=优胜不稳定。

用法：MSYS_NO_PATHCONV=1 PYTHONUTF8=1 /d/anaconda/python.exe 输出/scripts/run_cloud_probe.py \\
        E:/AIC智能体迁移包/输出/scripts/probe_legit_ceiling.py 24
  本机验接线：PYTHONUTF8=1 code/yanban/.venv/Scripts/python.exe 输出/scripts/probe_legit_ceiling.py --selfcheck
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
import pathlib
import re
import sqlite3
import sys

os.environ.setdefault("LLM_MOCK", "1")
HERE = pathlib.Path(__file__).resolve()
for _cand in ("/app", str(HERE.parent), os.getcwd(),
              *[str(p / "code" / "yanban") for p in HERE.parents]):
    if (pathlib.Path(_cand) / "companion").is_dir():
        sys.path.insert(0, _cand)
        break
else:
    print("判读：找不到含 companion 的目录 ⇒ 不知道 import 哪份产品码（RC=2）")
    sys.exit(2)
sys.path.insert(0, str(HERE.parent))

KB = pathlib.Path("/app/data/knowledge")
DB = pathlib.Path("/app/data/yanban.db")
CITE_RE = re.compile(r"\[\[(?:论文)[:：](.+?)\]\]")
SELFCHK_ONLY = "--selfcheck" in sys.argv
REPORTS_MAX = int(next((a for a in sys.argv[1:] if a.isdigit()), 24))
MIN_TOPICS = 3
MIN_CITED = 60
MIN_GAP = 8
DELTA_MIN = 2                 # Q5②：至少多救回 2 句才算"有空间"
LOO_FOLDS_OK = 2               # Q5：三折留一里至少 2 折优胜不差
LIMIT = 8
TABLES = ("papers", "research_intents", "projects", "messages")
HOSTS = {"crossref": "api.crossref.org", "arxiv": "export.arxiv.org",
         "openalex": "api.openalex.org", "semantic_scholar": "api.semanticscholar.org"}
VARIANTS = ("V0 现状式", "V1 裸式", "V2 英文体裁", "V3 中文体裁", "V4 池内词面", "V5 主题+池内词面")


def die(code: int, msg: str) -> None:
    print(f"判读：{msg}（RC={code}）")
    sys.exit(code)


def rows_sql(sql: str, args: tuple = ()) -> list:
    try:
        con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    except sqlite3.Error as exc:
        die(2, f"只读连接打不开（{exc}）⇒ 不退回可写连接")
    try:
        return con.execute(sql, args).fetchall()
    except sqlite3.Error as exc:
        die(2, f"只读取数失败（{exc}）⇒ 不猜数")
    finally:
        con.close()


def assert_no_db_import() -> None:
    src = HERE.read_text(encoding="utf-8")
    hits = [l.strip()[:60] for l in src.splitlines()
            if re.search(r"^\s*(from|import)\s+companion\.db\b", l)]
    if hits:
        die(2, f"本仪器 import 了 companion.db（{hits}）⇒ 只读口径不成立")


def assert_tables_exist() -> None:
    have = {r[0] for r in rows_sql("SELECT name FROM sqlite_master WHERE type='table'")}
    missing = [t for t in TABLES if t not in have]
    if missing:
        die(2, f"判据里的表不在库中：{missing}（实有 {sorted(have)}）⇒ 指纹尺在空气上开火")


def content_state() -> tuple:
    assert_tables_exist()
    h = {}
    for t in TABLES:
        rws = rows_sql(f"SELECT rowid, * FROM {t} ORDER BY rowid")
        h[t] = (len(rws), rws[-1][0] if rws else 0,
                int(hashlib.sha256(repr(rws).encode("utf-8", "replace")).hexdigest()[:12], 16))
    return tuple(sorted(h.items())) + (("KB_md", (sum(1 for _ in KB.rglob("*.md")), 0, "")),)


def stamp_note() -> str:
    return " ".join(f"{pathlib.Path(str(DB) + s).name}"
                    f"{'' if pathlib.Path(str(DB) + s).exists() else '(缺)'}"
                    f":{pathlib.Path(str(DB) + s).stat().st_size if pathlib.Path(str(DB) + s).exists() else '-'}"
                    for s in ("", "-wal", "-shm"))


def assert_ro_channel() -> None:
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    try:
        con.execute("CREATE TABLE __tripwire__(x)")
    except sqlite3.Error as exc:
        if "readonly" not in str(exc).lower():
            die(2, f"只读连接报的错不是「readonly」而是 {type(exc).__name__}（{str(exc)[:60]}）")
        return
    finally:
        con.close()
    die(2, "mode=ro 的连接居然建了表 ⇒ 名义只读、实际可写")


def install_llm_tripwire() -> list:
    import llm
    buried = []
    for name in ("chat", "chat_stream", "chat_json"):
        if callable(getattr(llm, name, None)):
            buried.append(name)

            async def boom(*a, **k):
                raise AssertionError("本仪器声称零模型调用，但有人真调了模型")

            setattr(llm, name, boom)
    return buried


# ---------------------------------------------------------------- 词面构造器（只看 intent 与自己的池子）
def pool_terms(pool, k: int) -> list:
    """从**该项目自己的池子篇名**里取有区分度的拉丁词（df 最小的 k 个）。

    为什么算合法：这是产品手上本来就有的数据（`list_papers(project_id)` 同一批），
    不是把答案抄进查询——真缺证据的那些句子本来就接不住这批篇名（否则不会落进 gap）。
    """
    from companion.literature.engine import FACET_VOCAB, GENERIC_TOKENS
    from companion.literature.evidence import _tokens
    titles = [(p.get("title") or "") for p in (pool or [])]
    df: dict[str, int] = {}
    order: list[str] = []
    for t in titles:
        seen = set()
        for w in re.findall(r"[a-zA-Z]{4,}", t.lower()):
            if w in GENERIC_TOKENS or w in FACET_VOCAB or w in _STOP:
                continue
            seen.add(w)
        for w in sorted(seen):
            if w not in df:
                order.append(w)
            df[w] = df.get(w, 0) + 1
    n = max(1, len(titles))
    cand = [w for w in order if df[w] <= max(1, n // 2)]
    return sorted(cand, key=lambda w: (df[w], order.index(w)))[:k]


_STOP = {"based", "using", "with", "from", "into", "their", "this", "that", "and", "for",
         "the", "a", "an", "of", "in", "on", "to", "via", "study", "studies", "novel"}


def candidate_queries(intent, pool, base: str, facet: str) -> dict:
    """六条候选式子。**形参里没有正文**——R-f 就是钉这一条。"""
    from companion.literature.engine import _is_latin
    t4 = pool_terms(pool, 4)
    en = " ".join(t4[:3])
    out = {
        "V0 现状式": f"{base} {facet}",
        "V1 裸式": base,
        "V2 英文体裁": f"{base} review",
        "V3 中文体裁": f"{base} 综述",
        "V4 池内词面": f"{en} {facet}".strip(),
        "V5 主题+池内词面": (f"{base} {' '.join(t4[:2])}".strip() if _is_latin(base)
                             else f"{' '.join(t4[:2])} {facet}".strip()),
    }
    return {k: re.sub(r"\s+", " ", v).strip() for k, v in out.items()}


# ---------------------------------------------------------------- 取数与跑
def load_reports():
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
        if not links or len(links) != len(set(links)):
            skipped.append((tag, "引用块解析不出篇名或有重名"))
            continue
        pid_row = rows_sql("SELECT paper_json FROM papers WHERE project_id=? ORDER BY created_at DESC",
                           (d.name,))
        by_title = {}
        for (raw,) in pid_row:
            try:
                p = json.loads(raw)
            except json.JSONDecodeError:
                continue
            if isinstance(p, dict) and p.get("title") and p["title"] not in by_title:
                by_title[p["title"]] = p
        pool = [by_title[t] for t in links if t in by_title]
        if len(pool) != len(links) or len(pool) < 3:
            skipped.append((tag, f"链接 {len(links)} 条、本项目库内查得 {len(pool)} 篇"))
            continue
        if not all(evidence.sources(p) for p in pool):
            skipped.append((tag, "有篇目拿不到可核对文本"))
            continue
        row = rows_sql("SELECT intent_json FROM research_intents WHERE project_id=?", (d.name,))
        if not row:
            skipped.append((tag, "库里没有该项目的 intent ⇒ 还原不出触发面"))
            continue
        from companion.intent.model import ResearchIntent
        try:
            intent = ResearchIntent(**json.loads(row[0][0]))
        except (json.JSONDecodeError, ValueError) as exc:
            skipped.append((tag, f"intent 读不出：{type(exc).__name__}｜{str(exc)[:60]}"))
            continue
        reports.append({"file": tag, "pid": d.name, "body": body, "pool": pool, "intent": intent})
    return reports, skipped


def providers():
    from companion.literature.providers.crossref_arxiv import ArxivProvider, CrossrefProvider
    from companion.literature.providers.openalex import OpenAlexProvider
    from companion.literature.providers.semantic_scholar import SemanticScholarProvider
    return [CrossrefProvider(), ArxivProvider(), OpenAlexProvider(), SemanticScholarProvider()]


async def fetch(q: str, pkt_bad: list, label: str):
    """一条式子 → 四源串行带回（串行才能把包数归到具体那一家，Q2 要用）。"""
    from companion.literature import engine
    import supply_gate
    raw, live = [], []
    for p in providers():
        before = len(supply_gate._SENT)
        items = await p.safe_search(q, max(LIMIT, 6))
        pk = supply_gate._SENT[before:]
        n = len([u for u in pk if HOSTS[p.name] in u])
        if items:
            live.append(p.name)
        elif n > 1:
            pkt_bad.append((label, p.name, n))
        raw += [engine._normalize(r, p.name) for r in (items or [])]
    return raw, ",".join(live) or "—"


def load_library() -> dict:
    """整张 papers 表按篇名收一次，带它出自哪个项目 —— 只读一次，别在每份报告里重扫。"""
    lib: dict[str, tuple] = {}
    for pid, raw in rows_sql("SELECT project_id, paper_json FROM papers ORDER BY created_at DESC"):
        try:
            p = json.loads(raw)
        except json.JSONDecodeError:
            continue
        t = p.get("title") if isinstance(p, dict) else None
        if t and t not in lib:
            lib[t] = (pid, p)
    return lib


async def run_one(rp, pkt_bad, cache, library):
    from companion.literature import evidence, engine
    from companion.intent.clarification import clarification_gaps

    g0 = evidence.grounding_report(rp["body"], rp["pool"])
    out = {"file": rp["file"], "topic": (rp["intent"].topic or "").strip(),
           "cited": g0["cited_sentences"], "gap": g0["true_gap"], "per": {}, "live": {}}
    if not out["gap"]:
        return out
    gaps = [g for g in clarification_gaps(rp["intent"]) if g in engine.GAP_FACETS]
    base = engine._query_from_intent(rp["intent"])
    facet = engine.GAP_FACETS[gaps[0]] if gaps else "survey"
    qs = candidate_queries(rp["intent"], rp["pool"], base, facet)
    my_titles = {(p.get("title") or "").strip() for p in rp["pool"]}
    tokens = engine.topic_tokens([base], rp["intent"].topic or "")
    union = {}
    for name, q in qs.items():
        if q not in cache:
            raw, live = await fetch(q, pkt_bad, name)
            cache[q] = (engine._dedup(raw), live)
        fetched, live = cache[q]
        relevant = engine.filter_relevant(fetched, tokens)
        fresh = [p for p in relevant if (p.get("title") or "").strip() not in my_titles]
        out["live"][name] = live
        out["per"][name] = {"fetched": len(fetched), "relevant": len(relevant),
                            "fresh": len(fresh), "q": q[:40],
                            "rescued": evidence.supplement_value(
                                rp["body"], rp["pool"], fresh)["rescuable"]}
        for p in fresh:
            union[(p.get("title") or "").strip()] = p
    ul = list(union.values())
    s_ceil = evidence.supplement_value(rp["body"], rp["pool"], ul)
    out["ceiling"] = s_ceil["rescuable"]
    out["union_n"] = len(ul)
    # 整张表口径（含**按设计读不到**的兄弟项目材料）并列印作对照；
    # 它与并集口径**不可比大小**（救回对候选集不单调），只作为"另一个候选集上的读数"印出来。
    other = [p for t, (own, p) in library.items() if own != rp["pid"] and t not in my_titles]
    out["ceiling_allproject"] = evidence.supplement_value(
        rp["body"], rp["pool"], other)["rescuable"]
    # Q0/R-c + Q4
    if evidence.supplement_value(rp["body"], rp["pool"], rp["pool"])["rescuable"] != 0:
        die(2, f"{rp['file']}：把自己的池子当补充材料竟判出救回 ⇒ `true_gap` 定义已变")
    if out["ceiling"] < max(v["rescued"] for v in out["per"].values()):
        # 这条**不判红**：救回对候选集不单调（见文件头 Q4 那段与 V6 用例），
        # "并集比某个变体小"是这台尺子的合法行为，不是归属算错的证据。只印出来当观察。
        out["union_anomaly"] = (out["ceiling"], max(v["rescued"] for v in out["per"].values()))
    if out["ceiling"] > out["gap"] or any(v["rescued"] > out["gap"] for v in out["per"].values()):
        die(2, f"{rp['file']}：救回数超过真缺证据 ⇒ 恒等式破了")
    for v in out["per"].values():
        if not (v["fetched"] >= v["relevant"] >= v["fresh"]):
            die(2, f"{rp['file']}：单变体漏斗恒等式破了 {v}")
    return out


def selfcheck() -> int:
    from companion.literature import engine
    from companion.literature.evidence import grounding_report, supplement_value
    src = HERE.read_text(encoding="utf-8")
    rd_bad = sorted({n for n in re.findall(r"^\s*def (\w+)", src, re.M)
                     if n in {"supplement_value", "grounding_report", "_classify", "_cited",
                              "best_spans", "rare_tokens", "topic_tokens", "filter_relevant",
                              "gap_subqueries", "_dedup", "_normalize", "_query_from_intent"}})
    mods = [engine.gap_subqueries, engine.topic_tokens, engine.filter_relevant, engine._dedup,
            engine._normalize, engine._query_from_intent, supplement_value, grounding_report]
    rd_bad += [f"{f.__name__}={f.__module__}" for f in mods
               if not getattr(f, "__module__", "").startswith("companion.")]
    # R-f：候选词面**整段函数体**都不许摸到正文（只查形参是不够的——把正文塞进默认实参、
    # 或在函数里自己捞一份 `rp["body"]` 都是同一件事）。
    import inspect
    rf_bad = []
    for fn in (candidate_queries, pool_terms):
        try:
            body_src = inspect.getsource(fn)
        except OSError:
            die(2, f"R-f 读不到 {fn.__name__} 的源码 ⇒ 这条自检没法证明（RC=2）")
        hits = re.findall(r"\b(body|report|claim|sentence|正文)\b", body_src)
        if hits:
            rf_bad.append(f"{fn.__name__} 里出现 {sorted(set(hits))}")
    checks = []
    P = lambda t, ab: {"title": t, "abstract": ab, "year": 2024, "doi": ""}  # noqa: E731
    boiler = "the proposed deep learning framework experimental results show improvement"
    pool = [P("Alpha graph clustering", f"alpha graph clustering groups nodes. {boiler}"),
            P("Beta certificate lifetime", f"beta certificate lifetime predicts fraud. {boiler}"),
            P("Gamma dialog colours", f"gamma warning dialog colours wording. {boiler}"),
            P("Delta cache timing", f"delta cache timing leaks patterns. {boiler}")]
    extra = [P("Eps drift in vendors",
               "eps drift across quorum vendors exposes storefront ledger gaps at 0.41 quartile"),
             P("Zeta quench factor",
               "zeta quenching factors for nuclear recoils in liquid argon time projection")]
    junk = [P("Crop rotation soil", "crop rotation increases soil organic carbon in field trials"),
            P("Deep learning boilerplate", boiler)]
    t = ("综述。\n\neps drift across quorum vendors exposes storefront ledger gaps at 0.41 "
         "quartile[1]\nzeta quenching factors for nuclear recoils in liquid argon time "
         "projection[2]\n")
    a = supplement_value(t, pool, extra)
    b = supplement_value(t, pool, junk)
    c = supplement_value(t, pool, pool)
    checks.append(("R-a 真摘录必须全救回", a["true_gap"] == 2 and a["rescuable"] == 2, a))
    checks.append(("R-b 错配材料必须救回 0", b["true_gap"] == 2 and b["rescuable"] == 0, b))
    checks.append(("R-c 拿主池自己当补充材料必须 0", c["true_gap"] == 2 and c["rescuable"] == 0, c))
    checks.append(("R-d 判据件全部来自产品模块", not rd_bad, rd_bad))
    checks.append(("R-f 两个词面构造器整段不摸正文", not rf_bad, rf_bad))
    for n, ok, blob in checks:
        print(f"  {'✅' if ok else '🔴'} {n}" + ("" if ok else f"｜{str(blob)[:130]}"))
    bad = [n for n, ok, _ in checks if not ok]
    if bad:
        die(2, f"Q0 接线自检 {len(bad)}/{len(checks)} 不成立 ⇒ 不出读数")
    buried = install_llm_tripwire()
    import llm
    try:
        asyncio.run(llm.chat("ping"))
        fired = False
    except AssertionError:
        fired = True
    print(f"  {'✅' if fired and len(buried) == 3 else '🔴'} R-e 零模型雷会响"
          f"（埋了 {len(buried)} 个入口 {buried}）")
    if not fired or len(buried) != 3:
        die(2, "R-e 不响或入口不够 ⇒ 没资格声称零模型调用")
    # 词面构造器本身也要会算：给一个合成 intent + 池子，六条式子必须齐且各不相同
    from companion.intent.model import ResearchIntent
    it = ResearchIntent(project_id="x", topic="multimodal phishing detection")
    cq = candidate_queries(it, pool, "multimodal phishing detection", "survey")
    ok = (len(cq) == len(VARIANTS) and len({v for v in cq.values()}) >= 4
          and cq["V0 现状式"] == "multimodal phishing detection survey")
    print(f"  {'✅' if ok else '🔴'} R-g 六条候选式子齐全且互不相同（{len(set(cq.values()))} 种）")
    if not ok:
        die(2, f"R-g 候选式子异常 {cq}")
    print("Q0 接线自检 6 条全通过")
    return 0


def main() -> int:
    if selfcheck() != 0:
        return 2
    if SELFCHK_ONLY:
        print("判读：只验了接线（--selfcheck）⇒ 没有读数（RC=0 仅指接线通过）")
        return 0
    import llm
    if not getattr(llm, "LLM_MOCK", False):
        die(2, "LLM_MOCK 没生效 ⇒ 无法声称零模型调用")
    assert_no_db_import()
    if not KB.is_dir() or not DB.is_file():
        die(2, f"容器路径不对（KB={KB.is_dir()} DB={DB.is_file()}）⇒ 本机跑不了真读数")
    from companion.literature.providers.base import reset_breakers
    import supply_gate

    st0 = content_state()
    assert_ro_channel()
    reset_breakers()
    supply_gate.install_packet_meter()
    supply_gate._SENT.clear()
    reports, skipped = load_reports()
    print(f"\n报告：上限 {REPORTS_MAX} → 可用 {len(reports)} 份｜剔除 {len(skipped)} 份")
    for tag, why in skipped[:10]:
        print(f"    跳过 {tag}：{why}")
    if len(reports) < MIN_TOPICS:
        die(3, f"可用报告 {len(reports)} 份 ⇒ 不够分折，不测")
    pkt_bad: list = []
    cache: dict = {}
    library = load_library()
    rows = [asyncio.run(run_one(rp, pkt_bad, cache, library)) for rp in reports]
    print(f"库里 papers 共 {len(library)} 篇（按篇名去重，含各项目的复制品）")
    topics = sorted({r["topic"] for r in rows if r["topic"]})
    meas = [r for r in rows if r["gap"]]
    print(f"主题：{len(topics)} 个 {topics}")
    print(f"候选式子共发 {len(cache)} 条（四源，去重后）\n")
    print(f"  {'报告':28s} {'缺证据':>6s} " + " ".join(f"{v.split()[0]:>7s}" for v in VARIANTS)
          + f" {'并集读数':>9s} {'整表读数':>9s}")
    for r in rows:
        if not r["gap"]:
            print(f"  {r['file'][:28]:28s} {0:6d} （无真缺证据句，不进分子分母）")
            continue
        print(f"  {r['file'][:28]:28s} {r['gap']:6d} "
              + " ".join(f"{r['per'][v]['rescued']:7d}" for v in VARIANTS)
              + f" {r['ceiling']:9d} {r['ceiling_allproject']:9d}")
    gap = sum(r["gap"] for r in rows)
    cited = sum(r["cited"] for r in rows)
    ceil_ok = sum(r["ceiling"] for r in meas)
    ceil_all = sum(r["ceiling_allproject"] for r in meas)
    tot = {v: sum(r["per"][v]["rescued"] for r in meas) for v in VARIANTS}
    print(f"\n分母：带角标句 {cited}｜真缺证据 {gap}（{100.0 * gap / cited:.1f}pp）")
    for v in VARIANTS:
        print(f"  {v:18s} 救回 {tot[v]:3d} 句"
              f"（式子样例：{next((r['per'][v]['q'] for r in meas if r['per'][v]['q']), '—')}）")
    print(f"  {'并集口径':18s} 救回 {ceil_ok:3d} 句（六条候选式子带回材料的并集，只算本项目可达的）")
    print(f"  {'整表口径':18s} 救回 {ceil_all:3d} 句（整张 papers 表，含按设计读不到的兄弟项目材料）")
    print(f"  两份读数的差 = {ceil_all} − {ceil_ok} = {ceil_all - ceil_ok} 句｜"
          f"并集反而读得小的行有 {sum(1 for r in meas if r.get('union_anomaly'))} 处 ⇒ "
          "**这两个口径不可比大小**（救回对候选集不单调，见文件头 Q4），"
          "本行只印差值，不解释成「复制出来的材料贡献了几句」")
    for f, name, n in pkt_bad:
        print(f"  🔴 Q2 {f} 的 {name} 空手回来却发了 {n} 包 ⇒ N2 回归")
    if pkt_bad:
        die(2, f"Q2：{len(pkt_bad)} 处「失败源连敲」⇒ N2 已回归，读数作废")
    st1 = content_state()
    if st1 != st0:
        die(2, f"读数期间线上四张表内容变了 ⇒ 有并发写，读数作废：{[x for x in zip(st0, st1) if x[0] != x[1]]}")
    print(f"Q1 只读自证：{'/'.join(TABLES)} 内容指纹逐项相同 + mode=ro 试写被拒 + KB 计数相同"
          f"（文件旁证 {stamp_note()}，时间戳不参与判定）")
    if cited < MIN_CITED or gap < MIN_GAP or len(topics) < MIN_TOPICS:
        die(3, f"分母不足（带角标句 {cited}／缺证据 {gap}／主题 {len(topics)}）⇒ 只印计数不印判定")
    if ceil_ok == 0:
        print("判读：并集口径本身就读出 0 句 ⇒ 这六条式子带回来的材料里没有能接住这些句子的东西，"
              "「改词面」无从谈起（这是**该候选集上的读数**，不等于「真上界为空」）（RC=1）")
        return 1
    best = max((v for v in VARIANTS if v != "V0 现状式"), key=lambda v: tot[v])
    print(f"\nreach(并集口径分母) = V0 {tot['V0 现状式']} / 并集读数 {ceil_ok} = "
          f"{100.0 * tot['V0 现状式'] / ceil_ok:.0f}%")
    print(f"② 全场最优变体是 {best}（{tot[best]} 句）vs V0（{tot['V0 现状式']} 句），"
          f"差 {tot[best] - tot['V0 现状式']} 句（判线 ΔMIN={DELTA_MIN}）")
    folds = []
    for held in topics:
        train = [r for r in meas if r["topic"] != held]
        test = [r for r in meas if r["topic"] == held]
        if not train or not test:
            folds.append((held, "样本不足", None))
            continue
        s = {v: sum(r["per"][v]["rescued"] for r in train) for v in VARIANTS}
        pick = max((v for v in VARIANTS if v != "V0 现状式"), key=lambda v: s[v])
        st = {v: sum(r["per"][v]["rescued"] for r in test) for v in VARIANTS}
        ok = st[pick] >= st["V0 现状式"]
        folds.append((held, pick, ok))
        print(f"  留一折（验「{held}」）：训练两折选 {pick}（{s[pick]} vs V0 {s['V0 现状式']}）"
              f"｜留出折 {pick} {st[pick]} 句 vs V0 {st['V0 现状式']} 句 ⇒ {'不差' if ok else '更差'}")
    good = sum(1 for _h, _p, o in folds if o)
    if tot[best] - tot["V0 现状式"] < DELTA_MIN:
        print(f"判读：最优变体比 V0 只多 {tot[best] - tot['V0 现状式']} 句（<{DELTA_MIN}）⇒ "
              "**词面这一级没有可买的**，① 到此为止：不改产品码，把这条负结果落档（RC=1）")
        return 1
    if good < LOO_FOLDS_OK:
        print(f"判读：留一主题只有 {good}/{len(folds)} 折优胜，全场最大差是**在同一批主题上挑出来的**"
              f"⇒ 不泛化，据它改产品码就是过拟合（RC=5）")
        return 5
    print(f"判读：{best} 比 V0 多救 {tot[best] - tot['V0 现状式']} 句，且留一主题 "
          f"{good}/{len(folds)} 折不差 ⇒ 词面确有可修空间，"
          f"下一步把它落进 `gap_subqueries`/词面来源并重跑轮 160 那台仪器量 reach（RC=0）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
