# -*- coding: utf-8 -*-
"""轮 160 · 真子查询的漏斗：补检索**实际**能带回几篇、其中几篇真能救回句子。

上一轮（轮 159）量到的是**库内天花板**：把整张 papers 表喂给量具，救回 24/27 句 ⇒ 上限贡献 6.6pp。
天花板只回答「材料在手有没有用」。这一轮回答另一半：**机制够不够得着那些材料**——
走产品自己的触发面与筛选件，真发一次子查询（只撞文献 API，不撞模型），数五级漏斗：
    报告 → 有可触发缺口 → 发出子查询 → 四源带回 → 过相关性(主轮那把尺) → 剔掉池内已有 → [:上限] → 救回几句
reach = 救回(机制) / 救回(天花板)，两个数同一次跑出来、同一把尺、同一个分母，不分口径。
**天花板的口径 = 本场「够得着的材料全集」= 线上 papers 表 ∪ 本轮机制真带回来的那几篇**（按池内已有剔除）。
为什么不照轮 159 那样"只问库里有没有"：首跑实测到一份光伏报告「机制救 1 句 / 只问全库救 0 句」——
子查询从 API 拿回的论文根本不在库里，那不是上界而是**另一堆材料**，拿它当分母算出来的不是比例。
"只问全库"那一栏照样印出来（与轮 159 的 24 句可比），只是不参与 reach。

判线（跑前写死，不看数再定）：
  Q0 接线自检（全合成、零网络）：
     R-a 真摘录必须全救回；R-b 错配池必须救回 0；
     R-c **拿报告自己的池子当补充材料 ⇒ 救回必须为 0** —— 这条钉住 `true_gap` 的定义（整池都接不住），
         少了它，任何「多给几篇就说救回来了」的假象都拦不住；
     R-d 判据件必须全部来自产品（`gap_subqueries`/`topic_tokens`/`filter_relevant`/`_dedup`/
         `_normalize`/`supplement_value`/`grounding_report`），逐个查 `__module__` 是否以 `companion.`
         开头——本文件重写一把同名尺子就红；上限 `MAX_SUBQUERY_PAPERS` 直接 import，不抄字面量；
     R-e 零模型调用的雷必须**会响**：把 `llm.chat`/`chat_stream`/`chat_json` 换成「一碰就抛」，
         主动碰一次必须抛；埋不到 3 个或不响 ⇒ RC=2（仪器自己没证明，没资格说零调用）。
  Q1 只读自证（**这一条在本轮改过口径，改在看到读数之后，方向是收紧**）：
     判据 = ① `papers`/`research_intents`/`projects`/`messages` 四张表的「行数 + max(rowid) +
     逐行 sha256」跑前跑后逐项相同（内容级，别的连接一提交就躲不过；表名写错 ⇒ 直接 RC=2，
     不许咽成一条恒定相同的错误串）；② `mode=ro` 连接真试写一次必须被拒（比时间戳强：
     写不进的通道不需要靠文件时间戳猜）；③ KB *.md 计数相同。
     原写法「yanban.db 与 -wal/-shm 的 (mtime,size) 三连相同」首跑就把一场完整读数判成 RC=2，
     实测凶手是判据本身：`输出/scripts/probe_wal_who_touches.py` 在容器里静置 20 秒、期间
     **一个 sqlite 连接都不开**，`-shm` 照样自己动（主库与 -wal 一字节没变），而同一个进程里
     拿副本做一次真 UPDATE、内容指纹立刻变 ⇒ 时间戳测的是「有没有别的连接活着」，
     常驻服务永远活着，那条判据从写下那天起就不可能被满足。三个文件的大小仍逐行印出来当旁证。
     SQL 一律 `mode=ro`；本文件**不许 import companion.db**（那个连接工厂会开写连接并跑
     `executescript`），跑前 grep 自己的源码断言一次。
     ⚠️ 故意不走 `search_for_intent_async` 端到端：它下游的 `_intent_feedback` 会经产品连接读库，
     与本仪器的只读口径冲突 ⇒ 走的是它内部那几件同样的产品函数（Q0/R-d 就是这条的自证），
     这是选择不是疏忽。同理每个源**串行**发（并行只是为了数不出包数归属，不改每源带回什么）。
  Q2 N2 没回归：用 `supply_gate` 的传输层挂表按源数包，**任何「没带回东西」的源必须 ≤1 包**
     （>1 ⇒ 失败后又在 HTTP 层连敲 ⇒ RC=2 读数作废）。
  Q3 分母两侧：报告 ≥ MIN_REPORTS、Σ带角标句 ≥ MIN_CITED、Σ真缺证据 ≥ MIN_GAP ⇒ 否则 RC=3 未测。
     Q3b（**本轮看到读数后补的，方向是收紧**）：线上 24 份报告其实只有 3 个不同主题，
     「独立子查询」= 去重后的子查询条数 ⇒ `reach ≥ REACH_OK` 只有在 distinct ≥ MIN_DISTINCT(=6)
     时才允许判 RC=0；distinct 不足时最多判到 RC=5（未决定）。理由：分母是"句"，但机制侧的
     独立输入只有 3 条，50% 这个数可能是 3 次调用的运气，不该被读成"扩母体值得一试"的证据。
  Q4 恒等式：每份报告都验 `rescued ≤ rescued_nocap ≤ rescued_ceiling ≤ gap`、
     `added ≤ MAX_SUBQUERY_PAPERS`、`fetched ≥ relevant ≥ fresh ≥ added`、
     `supplement_value` 与 `grounding_report` 的 gap 三者相同；
     违例 ⇒ RC=2（读数作废，不「就地修正」）。
     另印两笔**只对账不判定**的拆账：去掉 4 篇上限能救几句（`rescued_nocap - rescued`＝上限的代价）、
     天花板再去掉本轮带回（`rescued_ceiling - rescued_nocap`＝词面与相关门槛的代价）。
     reach 一律用**带上限**的 `rescued` 算——那才是产品真做得到的事，放宽上限是另一次改动。
  Q5 判读线（阈值由轮 158/159 的数派生，不是拍的）：
     天花板 6.6pp ⇒ reach=0.5 时机制上界 ≈ 3.3pp，正好落在「判 3pp 需 ~360 对」那条线上 ⇒ reach ≥ 0.5
     才谈得上「跑更大的场跑得动」；reach < 1/3（≈2.2pp）时连 120 对（半宽 4.6pp）都判不动 ⇒ 该去修检索侧
     而不是扩母体；中间 ⇒ 未决定。
     ⇒ reach ≥ 0.5 ⇒ RC=0；1/3 ≤ reach < 0.5 ⇒ RC=5 未决定；reach < 1/3 ⇒ RC=1；零触发 ⇒ RC=1（另印原因）
已知限制（跑前就写明，不是看完数补的）：
  · `_english_query` 在 `LLM_MOCK` 下原样返回中文式（llm 不可碰），所以中文主题这几份走的是产品的
    **不翻译分支**——线上真跑时是翻译过的。对这几行，「带回」是下界，逐行印 `译` 列标明。
  · 熔断是有状态的：arXiv(406)/Semantic Scholar(429) 一旦冷掉，后面的行就是 0 包 0 篇。这是线上同款
    行为，逐行印「活的源」，不当成 bug 修。

退出码：0=机制够得着｜1=机制够不着（产品问题）｜2=仪器坏｜3=分母不足｜5=落在中间带，不足以下判断。

用法（必须在容器里跑：要真文献源 + 线上库）：
  MSYS_NO_PATHCONV=1 PYTHONUTF8=1 /d/anaconda/python.exe 输出/scripts/run_cloud_probe.py \\
    E:/AIC智能体迁移包/输出/scripts/probe_subquery_funnel.py 24
  本机验接线：PYTHONUTF8=1 code/yanban/.venv/Scripts/python.exe 输出/scripts/probe_subquery_funnel.py --selfcheck
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

sys.path.insert(0, str(HERE.parent))          # supply_gate（传输层挂表，与体检同一把尺）

KB = pathlib.Path("/app/data/knowledge")
DB = pathlib.Path("/app/data/yanban.db")
CITE_RE = re.compile(r"\[\[(?:论文)[:：](.+?)\]\]")
SELFCHK_ONLY = "--selfcheck" in sys.argv
REPORTS_MAX = int(next((a for a in sys.argv[1:] if a.isdigit()), 24))
MIN_REPORTS = 6
MIN_CITED = 60
MIN_GAP = 8
MIN_DISTINCT = 6               # Q3b：独立子查询条数下限（低于它就不许判"够得着"）
REACH_OK = 0.5                 # Q5：够得着天花板的一半 ⇒ 值得跑更大的场
REACH_NO = 1.0 / 3.0           # Q5：连 1/3 都不到 ⇒ 判为机制问题
LIMIT = 8                      # 与产品默认 top 一致（engine 里 per = max(limit, 6)）
HOSTS = {"crossref": "api.crossref.org", "arxiv": "export.arxiv.org",
         "openalex": "api.openalex.org", "semantic_scholar": "api.semanticscholar.org"}
MAX_PAPERS_SEEN = [0]
# R-d 的两个方向都要查：① 本文件不许出现这些判据件的**本地同名定义**（换成"探针自己再写一把尺"
# 是最常见的自欺路径，光查 import 对象的 __module__ 看不见它）；② 真正调用的那几个对象必须来自
# companion.* 模块（把 engine.filter_relevant 悄悄换成别的包里的同名件也会被这条逮住）。
RULER_NAMES = frozenset({"supplement_value", "grounding_report", "_classify", "_cited",
                         "best_spans", "rare_tokens", "topic_tokens", "filter_relevant",
                         "gap_subqueries", "_dedup", "_normalize", "_query_from_intent"})


def die(code: int, msg: str) -> None:
    print(f"判读：{msg}（RC={code}）")
    sys.exit(code)


TABLES = ("papers", "research_intents", "projects", "messages")


def assert_tables_exist() -> None:
    """指纹尺扫的表必须真的存在——写错表名会让那一项变成一条恒定相同的错误串（假自证）。"""
    have = {r[0] for r in rows_sql("SELECT name FROM sqlite_master WHERE type='table'")}
    missing = [t for t in TABLES if t not in have]
    if missing:
        die(2, f"判据里的表不在库中：{missing}（实有 {sorted(have)}）⇒ 指纹尺在空气上开火")


def content_state() -> tuple:
    """Q1 的判据：内容级指纹（四张表逐行摘要）+ KB *.md 计数。

    为什么不是文件时间戳三连（轮 160 首跑就红在错误的那一项上）：
    `输出/scripts/probe_wal_who_touches.py` 在容器里实测——静置 20 秒、期间本仪器**一个 sqlite
    连接都不开**，`yanban.db-shm` 照样自己动（mtime 变、大小不变），主库与 -wal 一字节没动；
    拿副本做一次真 UPDATE，行集指纹立刻变（负控制成立）。⇒ 时间戳测的是
    「有没有别的连接活着」，常驻服务永远活着，那条判据从写下那天起就不可能被满足；
    而"有没有人写了我要读的那四张表"只能问内容。时间戳仍在下面 `stamp_note()` 里如实印出来当旁证。
    """
    assert_tables_exist()
    h = {}
    for t in TABLES:
        rows = rows_sql(f"SELECT rowid, * FROM {t} ORDER BY rowid")
        h[t] = (len(rows), rows[-1][0] if rows else 0,
                int(hashlib.sha256(repr(rows).encode("utf-8", "replace")).hexdigest()[:12], 16))
    return tuple(sorted(h.items())) + (("KB_md", (sum(1 for _ in KB.rglob("*.md")), 0, "")),)


def stamp_note() -> str:
    """把三个文件的时间戳印出来当**旁证**（不参与判定，理由见 content_state 的 docstring）。"""
    out = []
    for suf in ("", "-wal", "-shm"):
        p = pathlib.Path(str(DB) + suf)
        out.append(f"{p.name}:{'缺' if not p.exists() else p.stat().st_size}")
    return " ".join(out)


def assert_ro_channel() -> None:
    """Q1 的第三条腿：证明我的连接工厂真的写不进（写不进比"时间戳没动"强）。"""
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    try:
        con.execute("CREATE TABLE __tripwire__(x)")
    except sqlite3.Error as exc:
        if "readonly" not in str(exc).lower():
            die(2, f"只读连接报错但不是「readonly」（{exc}）⇒ 通道状况未知")
        return
    finally:
        con.close()
    die(2, "mode=ro 的连接居然建了表 ⇒ 名义只读、实际可写，本仪器的只读口径不成立")


def assert_no_db_import() -> None:
    """Q1：本文件不许 import companion.db（它的连接工厂会开写连接并 executescript）。"""
    src = HERE.read_text(encoding="utf-8")
    hits = [l.strip()[:60] for l in src.splitlines()
            if re.search(r"^\s*(from|import)\s+companion\.db\b", l)]
    if hits:
        die(2, f"本仪器 import 了 companion.db（{hits}）⇒ 只读口径不成立")


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


def install_llm_tripwire() -> list:
    """Q0/R-e：把所有模型入口换成「一碰就抛」，返回被埋的名字（不足 3 个 ⇒ 雷不响，RC=2）。"""
    import llm
    buried = []
    for name in ("chat", "chat_stream", "chat_json"):
        if callable(getattr(llm, name, None)):
            buried.append(name)

            async def boom(*a, **k):
                raise AssertionError("本仪器声称零模型调用，但有人真调了模型")

            setattr(llm, name, boom)
    return buried


def load_reports():
    """报告 + 它自己的可引用池（带摘要）+ 它当时的 intent（用于触发面与子查询词面）。"""
    from companion.literature import evidence
    from companion.intent.model import ResearchIntent
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
        by_title = papers_of(d.name)
        pool = [by_title[t] for t in links if t in by_title]
        if len(pool) != len(links) or len(pool) < 3:
            skipped.append((tag, f"链接 {len(links)} 条、库内查得 {len(pool)} 篇"))
            continue
        if not all(evidence.sources(p) for p in pool):
            skipped.append((tag, "有篇目拿不到可核对文本"))
            continue
        row = rows_sql("SELECT intent_json FROM research_intents WHERE project_id=?", (d.name,))
        if not row:
            skipped.append((tag, "库里没有该项目的 intent ⇒ 还原不出触发面"))
            continue
        # 用产品自己的重构件（dialogue_manager.load_intent 同一行写法）；读不出就分开记原因，
        # 别把「schema 漂了」混进「没有 intent」——那是两种完全不同的病。
        try:
            intent = ResearchIntent(**json.loads(row[0][0]))
        except (json.JSONDecodeError, ValueError) as exc:
            skipped.append((tag, f"intent 读不出：{type(exc).__name__}｜{str(exc)[:80]}"))
            continue
        reports.append({"file": tag, "body": body, "pool": pool, "intent": intent})
    return reports, skipped


def providers():
    """产品在那条路径上实例化的就是这四家，顺序与 `search_for_intent_async` 一致。"""
    from companion.literature.providers.crossref_arxiv import ArxivProvider, CrossrefProvider
    from companion.literature.providers.openalex import OpenAlexProvider
    from companion.literature.providers.semantic_scholar import SemanticScholarProvider
    return [CrossrefProvider(), ArxivProvider(), OpenAlexProvider(), SemanticScholarProvider()]


async def funnel_once(rp, ceiling_universe, pkt_bad):
    """一份报告的完整漏斗。所有判断件都取自产品，这里只做扇出与计数。"""
    from companion.intent.clarification import clarification_gaps
    from companion.literature import evidence, engine
    import supply_gate

    intent = rp["intent"]
    gaps = [g for g in clarification_gaps(intent) if g in engine.GAP_FACETS]
    base = engine._query_from_intent(intent)
    out = {"file": rp["file"], "gaps": len(gaps), "sub": "", "fetched": 0, "relevant": 0,
           "fresh": 0, "added": 0, "added_new": 0, "gap": 0, "rescued": 0, "rescued_lib": 0,
           "rescued_nocap": 0, "rescued_ceiling": 0, "live": "—", "titles": [],
           "topic": (getattr(intent, "topic", "") or "")[:24],
           "cjk": any("\u4e00" <= ch <= "\u9fff" for ch in base)}
    g0 = evidence.grounding_report(rp["body"], rp["pool"])
    out["cited"] = g0["cited_sentences"]
    out["gap"] = g0["true_gap"]
    if not gaps or not base:
        return out
    subs = engine.gap_subqueries(gaps, [base])
    if not subs:
        return out
    field, sub_q = subs[0]
    out["sub"] = f"{field}←{sub_q[:30]}"
    raw: list[dict] = []
    live = []
    for p in providers():                       # 串行：包数归属要能落到具体那一家
        before = len(supply_gate._SENT)
        items = await p.safe_search(sub_q, max(LIMIT, 6))
        pk = supply_gate._SENT[before:]
        if items:
            live.append(p.name)
        elif any(HOSTS[p.name] in u for u in pk):
            if len([u for u in pk if HOSTS[p.name] in u]) > 1:      # Q2：空手回来还连敲
                pkt_bad.append((rp["file"], p.name, len(pk)))
        raw += [engine._normalize(r, p.name) for r in (items or [])]
    out["live"] = ",".join(live) or "—"
    fetched = engine._dedup(raw)
    tokens = engine.topic_tokens([base], getattr(intent, "topic", "") or "")
    relevant = engine.filter_relevant(fetched, tokens)
    # 产品的剔除条件是 `p["id"] not in have`，而 id 每轮新铸 ⇒ 这条恒真（已登记的 N6，本轮不动）。
    # 这里照抄产品语义当作**主读数**，另按篇名算一份「真新增」，让 N6 的代价在同一行里可见。
    mine_ids = {p.get("id") for p in rp["pool"]}
    fresh = [p for p in relevant if p.get("id") not in mine_ids]
    added = fresh[:engine.MAX_SUBQUERY_PAPERS]
    my_titles = {(p.get("title") or "").strip() for p in rp["pool"]}
    out.update(fetched=len(fetched), relevant=len(relevant), fresh=len(fresh), added=len(added),
               added_new=sum(1 for p in added if (p.get("title") or "").strip() not in my_titles),
               titles=[(p.get("title") or "")[:26] for p in added])
    s_mech = evidence.supplement_value(rp["body"], rp["pool"], added)
    # 天花板必须是机制材料的**超集**，否则 `reach = 机制/天花板` 不是比例（首跑实测到有一行
    # 机制救 1 句而"只问全库"救 0 句——本轮从 API 带回来的那几篇根本不在 papers 表里。
    # 所以天花板取「全库 ∪ 本轮过相关的带回」：它是这一场**够得着的材料全集**。"只问全库"
    # 另列一栏与轮 159 的 24 句可比。改这条是在看到读数之后，方向是收紧判据
    # （补 `rescued ≤ rescued_nocap ≤ rescued_ceiling ≤ gap` 的恒等式，Q4），不是把红改成绿。
    lib_extra = [p for t, p in ceiling_universe.items() if t not in my_titles]
    s_lib = evidence.supplement_value(rp["body"], rp["pool"], lib_extra)
    s_ceil = evidence.supplement_value(rp["body"], rp["pool"], lib_extra + fresh)
    # 拆开两笔账：`MAX_SUBQUERY_PAPERS` 这道上限吃掉几句、检索词面/相关门槛吃掉几句。
    # 没有这一列，reach=44% 只有一句"机制不够"，分不清该改上限还是该改查询。
    s_nocap = evidence.supplement_value(rp["body"], rp["pool"], fresh)
    out["rescued"], out["rescued_ceiling"] = s_mech["rescuable"], s_ceil["rescuable"]
    out["rescued_lib"], out["rescued_nocap"] = s_lib["rescuable"], s_nocap["rescuable"]
    # Q0/R-c：拿它自己的池子当补充材料 ⇒ 必须一句也「救」不回来
    own = evidence.supplement_value(rp["body"], rp["pool"], rp["pool"])
    if own["rescuable"] != 0:
        die(2, f"{rp['file']}：把自己的池子当补充材料竟判出救回 {own['rescuable']} 句 ⇒ "
               "`true_gap` 定义已变，量具与判据不再同源")
    # Q4 恒等式（一篇论文可以撑住好几句，所以 `rescued` 与 `added` 之间**没有**大小关系，不写）
    if not (out["rescued"] <= out["rescued_nocap"] <= out["rescued_ceiling"] <= out["gap"]
            and out["added"] <= engine.MAX_SUBQUERY_PAPERS
            and out["fetched"] >= out["relevant"] >= out["fresh"] >= out["added"]
            and s_mech["true_gap"] == s_ceil["true_gap"] == out["gap"]):
        die(2, f"{rp['file']}：漏斗恒等式破了 {out}")
    return out


def selfcheck() -> int:
    """Q0：接线自检全合成、零网络、零模型调用。"""
    from companion.literature import engine
    from companion.literature.evidence import grounding_report, supplement_value

    rd_bad = []
    shadow = sorted({n for n in re.findall(r"^\s*def (\w+)", HERE.read_text(encoding="utf-8"),
                                           re.M) if n in RULER_NAMES})
    if shadow:
        rd_bad.append(f"本文件重写了判据件 {shadow}")
    for nm, fn in (("gap_subqueries", engine.gap_subqueries), ("topic_tokens", engine.topic_tokens),
                   ("filter_relevant", engine.filter_relevant), ("_dedup", engine._dedup),
                   ("_normalize", engine._normalize), ("_query_from_intent",
                                                        engine._query_from_intent),
                   ("supplement_value", supplement_value), ("grounding_report", grounding_report)):
        if not getattr(fn, "__module__", "").startswith("companion."):
            rd_bad.append(f"{nm}={fn.__module__}")
    MAX_PAPERS_SEEN[0] = engine.MAX_SUBQUERY_PAPERS

    def paper(t, a):
        return {"title": t, "abstract": a, "year": 2024, "doi": ""}

    boiler = "the proposed deep learning framework experimental results show improvement"
    pool = [paper("Alpha graph clustering", f"alpha graph clustering groups nodes. {boiler}"),
            paper("Beta certificate lifetime", f"beta certificate lifetime predicts fraud. {boiler}"),
            paper("Gamma dialog colours", f"gamma warning dialog colours wording. {boiler}"),
            paper("Delta cache timing", f"delta cache timing leaks patterns. {boiler}")]
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
    a = supplement_value(t, pool, extra)
    checks.append(("R-a 真摘录必须全救回", a["true_gap"] == 2 and a["rescuable"] == 2, a))
    b = supplement_value(t, pool, junk)
    checks.append(("R-b 错配池必须救回 0", b["true_gap"] == 2 and b["rescuable"] == 0, b))
    c = supplement_value(t, pool, pool)
    checks.append(("R-c 拿主池自己当补充材料必须 0（true_gap 的定义）",
                   c["true_gap"] == 2 and c["rescuable"] == 0, c))
    checks.append(("R-d 八件判据全部来自产品模块（不是本文件重写）", not rd_bad, rd_bad))
    fail = [n for n, ok, _ in checks if not ok]
    for n, ok, blob in checks:
        print(f"  {'✅' if ok else '🔴'} {n}" + ("" if ok else f"｜{str(blob)[:150]}"))
    if fail:
        die(2, f"Q0 接线自检 {len(fail)}/{len(checks)} 不成立 ⇒ 不出读数")
    buried = install_llm_tripwire()
    if len(buried) < 3:
        die(2, f"R-e 只埋到 {len(buried)} 个模型入口（{buried}）⇒ 雷不响，零调用无从证明")
    import llm
    try:
        asyncio.run(llm.chat("ping"))
        fired = False
    except AssertionError:
        fired = True
    print(f"  {'✅' if fired else '🔴'} R-e 零模型调用的雷会响（埋了 {len(buried)} 个入口：{buried}）")
    if not fired:
        die(2, "R-e 不响 ⇒ 本仪器没法声称零模型调用")
    print(f"Q0 接线自检 {len(checks)} 条 + R-e 雷响 = 全通过")
    return 0


def main() -> int:
    if selfcheck() != 0:
        return 2
    if SELFCHK_ONLY:
        print("判读：只验了接线（--selfcheck），没碰库也没发检索 ⇒ 没有读数"
              "（RC=0 仅指接线通过，不是效果）")
        return 0
    import llm
    if not getattr(llm, "LLM_MOCK", False):
        die(2, "LLM_MOCK 没生效 ⇒ 本仪器无法声称零模型调用")
    assert_no_db_import()
    if not KB.is_dir() or not DB.is_file():
        die(2, f"容器路径不对（KB={KB.is_dir()} DB={DB.is_file()}）⇒ 本机跑不了真读数")
    from companion.literature import engine
    from companion.literature.providers.base import provider_stats, reset_breakers
    import supply_gate

    st0 = content_state()
    print(f"Q1 起跑状态：内容指纹 {len(st0)} 项｜文件旁证 {stamp_note()}\n")
    assert_ro_channel()
    reset_breakers()
    supply_gate.install_packet_meter()
    supply_gate._SENT.clear()
    reports, skipped = load_reports()
    print(f"\n报告：上限 {REPORTS_MAX} → 可用 {len(reports)} 份｜剔除 {len(skipped)} 份")
    for tag, why in skipped[:12]:
        print(f"    跳过 {tag}：{why}")
    if len(reports) < MIN_REPORTS:
        die(3, f"可用报告 {len(reports)} < {MIN_REPORTS} ⇒ 不测")
    universe = all_papers()
    print(f"天花板材料（同一次跑、同一把尺的对照）：整张 papers 表去重 {len(universe)} 篇\n")
    print(f"  {'报告':30s} {'译':2s} {'缺口':>4s} {'带回':>4s} {'相关':>4s} {'入库':>4s}"
          f" {'真新':>4s} {'缺证据':>6s} {'机制救':>7s} {'去上限救':>7s} {'天花板救':>8s}"
          f" {'仅全库':>6s}  活的源")
    pkt_bad: list = []
    rows = []
    for rp in reports:
        r = asyncio.run(funnel_once(rp, universe, pkt_bad))
        rows.append(r)
        print(f"  {r['file'][:30]:30s} {'中' if r['cjk'] else '英':2s} {r['gaps']:4d} "
              f"{r['fetched']:4d} {r['relevant']:4d} {r['added']:4d} "
              f"{r['added_new']:4d} {r['gap']:6d} {r['rescued']:7d} {r['rescued_nocap']:7d} "
              f"{r['rescued_ceiling']:8d} {r['rescued_lib']:6d}  {r['live']}")
        if r["sub"]:
            print(f"      主题「{r['topic']}」｜子查询 {r['sub']}｜入库 "
                  f"{('、'.join(r['titles']) or '—')[:104]}")
    cited = sum(r["cited"] for r in rows)
    gap = sum(r["gap"] for r in rows)
    fired = sum(1 for r in rows if r["sub"])
    distinct_sub = len({r["sub"] for r in rows if r["sub"]})
    added = sum(r["added"] for r in rows)
    added_new = sum(r["added_new"] for r in rows)
    mech = sum(r["rescued"] for r in rows)
    nocap = sum(r["rescued_nocap"] for r in rows)
    ceil = sum(r["rescued_ceiling"] for r in rows)
    ceil_lib = sum(r["rescued_lib"] for r in rows)
    print(f"\n触发面：{len(rows)} 份里 {sum(1 for r in rows if r['gaps'])} 份有词表内的可触发缺口，"
          f"{fired} 份真发出了子查询——但**独立子查询只有 {distinct_sub} 条**"
          f"（{fired} 份触发摊在 {distinct_sub} 个主题上，平均每主题 "
          f"{fired / distinct_sub:.1f} 份同题产物）")
    print(f"漏斗：四源带回 {sum(r['fetched'] for r in rows)} 篇 → 过相关 "
          f"{sum(r['relevant'] for r in rows)} → 过 id 剔除 {sum(r['fresh'] for r in rows)}"
          f" → 上限内入库 {added} 篇（每份上限 {engine.MAX_SUBQUERY_PAPERS}，"
          f"其中按篇名看是**真新增** {added_new} 篇）")
    print(f"救回：机制 {mech} 句｜天花板（全库∪本轮过相关带回）{ceil} 句｜只问全库 {ceil_lib} 句"
          f"（分母同为真缺证据 {gap} 句）")
    print(f"分母：带角标句 {cited} 句｜缺证据率 {100.0 * gap / cited:.1f}pp"
          f"｜换成占带角标句的比例：机制 {100.0 * mech / cited:.1f}pp"
          f" vs 天花板 {100.0 * ceil / cited:.1f}pp（轮 159 的 6.6pp 就是这个口径）")
    print(f"账拆开算：上限 {engine.MAX_SUBQUERY_PAPERS} 篇这道闸吃掉 {nocap - mech} 句"
          f"（去掉上限能救 {nocap} 句）｜检索词面 + 相关门槛再吃掉 {ceil - nocap} 句"
          f"（材料够得着但这一场没带回）｜reach 用**带上限**的 {mech} 算，不用 {nocap}")
    for f, name, n in pkt_bad:
        print(f"  🔴 Q2 {f} 的 {name} 空手回来却发了 {n} 包 ⇒ N2 回归")
    if pkt_bad:
        die(2, f"Q2：{len(pkt_bad)} 处「失败源连敲」⇒ N2 已回归，本轮读数作废")
    stats = provider_stats()
    print(f"外呼：传输层共 {len(supply_gate._SENT)} 包｜provider_stats={stats}"
          "（缓存命中算 0 包，这是产品同款行为）")
    if cited < MIN_CITED or gap < MIN_GAP:
        die(3, f"分母不足（带角标句 {cited}<{MIN_CITED} 或缺证据 {gap}<{MIN_GAP}）"
               "⇒ 只印计数不印 reach")
    if not fired:
        print("判读：零触发 ⇒ 没有任何一行的缺口落进产品词表，机制根本没动（RC=1）")
        return 1
    if ceil == 0:
        print("判读：天花板这一场救回 0 句 ⇒ reach 无定义；机制侧只能定性"
              "（缺证据句存在、全库材料也接不住）（RC=1）")
        return 1
    reach = mech / ceil
    print(f"reach = 机制 / 天花板 = {mech}/{ceil} = {100.0 * reach:.0f}%")
    st1 = content_state()
    if st1 != st0:
        diff = [(k, a, b) for (k, a), (_k, b) in zip(st0, st1) if a != b]
        die(2, f"读数期间线上四张表的内容指纹变了 ⇒ 有并发写在提交，本场读数作废：{diff}")
    print(f"Q1 只读自证：{'/'.join(TABLES)} 四张表的行数、max(rowid) 与逐行摘要跑前跑后逐项相同"
          f" + KB *.md 计数相同 + mode=ro 连接试写被拒 ⇒ 零写入"
          f"（文件旁证 {stamp_note()}；-wal/-shm 的时间戳不参与判定，理由见 content_state）")
    if reach >= REACH_OK and distinct_sub < MIN_DISTINCT:
        print(f"判读：reach={100.0 * reach:.0f}% 看着够，但**独立子查询只有 {distinct_sub} 条**"
              f"（<{MIN_DISTINCT}）⇒ Q3b 拦下这次升判：{len(rows)} 份报告其实是 {distinct_sub} 个主题"
              "× 若干份同题产物，这个比例没资格判成「够得着」。要判它，得先有跨领域的题目"
              "（扩母体要扩在**主题**上，不是扩在同一主题的重复份数上）（RC=5）")
        return 5
    if reach >= REACH_OK:
        print(f"判读：机制够得到天花板的 {100.0 * reach:.0f}%（≥{100.0 * REACH_OK:.0f}%），"
              f"独立子查询 {distinct_sub} 条 ≥{MIN_DISTINCT} ⇒ 想判这条改动，缺的是**分母**不是材料；"
              "扩母体值得一试（RC=0）")
        return 0
    if reach < REACH_NO:
        print(f"判读：机制只够到天花板的 {100.0 * reach:.0f}%（<{100.0 * REACH_NO:.0f}%）"
              "⇒ 真效应上界被压到 2pp 量级，扩母体也判不动；该修的是**检索侧**"
              "（看漏斗哪一级掉得多：触发面 / 相关门槛 / 上限 / N6 的空转）（RC=1）")
        return 1
    print(f"判读：reach={100.0 * reach:.0f}% 落在 [{100.0 * REACH_NO:.0f}%,"
          f"{100.0 * REACH_OK:.0f}%) 中间带 ⇒ 不足以下任何一侧判断，别硬判（RC=5）")
    return 5


if __name__ == "__main__":
    sys.exit(main())
