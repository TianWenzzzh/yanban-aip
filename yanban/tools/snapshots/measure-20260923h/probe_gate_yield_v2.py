"""闸门产量体检（升级版）：每道闸门"该拦的拦住没有"、重采样花了几次、放过去多少。

历史版本只盯 no_substance/thin_quant 两道，轮 52 之后闸门已经有七道
（fake_title / multi_question / false_memory / no_substance / no_quantity / plan_dropped / no_echo），
而且新加的 plan_dropped、no_echo 需要 plan 与 user_text 才能判定——不传就永远不触发，读数会假绿。
所以这里按 violations() 的完整键集统计，并单独报每次回复花了几次模型调用。

容器内跑：python /tmp/probe_gate_yield_v2.py [每档轮数]
离线自检（不烧模型，只验最后那段分档汇总的算术与措辞）：
    python 输出/scripts/probe_gate_yield_v2.py --selfcheck
自检本身也要有人看着 —— 变异测试在 输出/scripts/mutate_tier_selfcheck.py。
"""
from __future__ import annotations

import collections
import contextlib
import io
import json
import os
import pathlib
import sys
import tempfile

sys.path.insert(0, "/app")
sys.path.insert(0, ".")      # 也允许在 code/yanban 下本地跑 LLM_MOCK 自检

import llm                                        # noqa: E402
from companion.conversation import mentor         # noqa: E402

# 自己往容器里落一份输出：上一轮 g90 就是靠 SSH 通道回传，通道一断，一小时的重采样全部丢掉。
# 断线后用 `fetch_probe_output.py <本地路径>` 一条命令取回（它会顺便回答「到底跑完没有」）。
# 文件名带脚本名：两个探针同时跑时，后启动的那个不会把前一份截断成空。
_TEE = os.environ.get("PROBE_TEE") or (
    f"/tmp/probe_out_{os.path.basename(__file__)[:-3]}.txt" if os.name == "posix" else None)
if _TEE:
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

N = next((int(a) for a in sys.argv[1:] if a.isdigit()), 3)
SELFCHECK = "--selfcheck" in sys.argv     # 不烧模型，只验汇总打印的算术与措辞
# --cases <池子.json>：改用线上真实对话里 harvested 出来的提问当情景（g98 的靶子池）。
# 为什么需要：内置 5 个情景在 g97 只喂出 20 条带卡回复、2 次开火，代价读不出置信区间；
# 而 #19 从会话库里捞到的 31 条历史上"有卡却零引用"的提问才是闸门该治的对象。
CASES_ARG = next((sys.argv[i + 1] for i, a in enumerate(sys.argv[:-1]) if a == "--cases"), "")


def cases_preflight(argv: list[str], cases_arg: str) -> int | None:
    """`--cases` 写了却没带上值 ⇒ 拒绝出数（返回 2），不许退回内置情景。

    这个形状是 `run_cloud_probe.py` 造出来的：它会把"本地存在的文件"当数据上传并从 argv 里剔掉，
    所以 `--cases <本地路径>` 到容器里就只剩一个光杆 `--cases`。此时若默默用内置 5 情景，
    印出来的读数与 g97 那份看着一模一样，却是另一批输入 —— 归因会整个错掉。
    """
    if "--cases" in argv and not cases_arg:
        print("⚠️ --cases 后面没有值（多半是被 runner 当成待上传文件吃掉了）。"
              "不出数：请传容器内路径，例如 --cases /tmp/g98_cite_pool.json。")
        return 2
    return None

CASES = [
    ("我在准备毕业论文，题目是医学图像分割，实验室只有两块 3080，怕算力不够", "不太会调参"),
    ("我已经读了二十来篇多智能体协作的论文，想整理成综述，但不知道按什么线组织", "导师想让我投 CCF-B"),
    ("我只想把实验数据的相关性分析跑通，不确定用什么统计方法，别给我讲太多理论", "变量 12 个，样本 87 份，多数不是正态"),
    ("我第一次做科研，想做多模态钓鱼网站检测，深度学习只学过一点", "只有一台轻薄本，没有 GPU，数据自己爬"),
    # 轮 115 加的第 5 个情景：g96a 量到它 10/10 都带 8 张卡，是 `no_citation` 少见的稳定靶子；
    # 原来那 4 个里有 2 个 10/10 一张卡都没有（闸门按设计不许对空池开火），
    # 所以那批读数里这条闸门 0 次开火并不说明它没代价——只是那台仪器看不见它。
    ("我们实验室在做光伏功率预测，我想找一个还没人做的点", "只有两年历史数据，15 分钟采样"),
]

def load_cases(path: str) -> list[tuple[str, str]]:
    """把 #19 harvest 出来的会话池读成情景表：[(首句, 追问)]，追问缺省则只跑一轮。

    两件事必须显式做：① 按 user_text 去重（31 条里只有 19 个不同提问，不去重会把
    "同一个提问重复采样" 说成 "19 个情景"）；② 认不出路径就交给调用方判 RC=2 ——
    静默退回内置 5 情景会印出一份"看着像池子读数、其实不是"的数（幻影读数，轮 108 同类）。
    """
    rows = json.loads(pathlib.Path(path).read_text(encoding="utf-8"))
    seen: set[str] = set()
    out: list[tuple[str, str]] = []
    for r in rows:
        if not isinstance(r, dict):
            continue
        say = str(r.get("user_text") or "").strip()
        if not say or say in seen:
            continue
        seen.add(say)
        out.append((say, str(r.get("follow") or "").strip()))
    print(f"用例池：{len(rows)} 行 → 去重后 {len(out)} 个提问（追问 {sum(1 for _, f in out if f)} 条）"
          f"｜来源 {path}", flush=True)
    return out


def resolve_cases(path: str) -> str:
    """容器里只有 /tmp/<文件名>（run_cloud_probe 按文件名投放），所以本地绝对路径要在容器里换个家。"""
    for cand in (path, f"/tmp/{os.path.basename(path)}", os.path.basename(path)):
        if os.path.isfile(cand):
            return cand
    return ""


keys = list(mentor.violations("试", papers=[], history=[]).keys())
# 从 `grounding_report` 里取哪两个键。**g98 第一次跑就死在这里**：我照猜测写了 `cited`，
# 而产品返回的是 `cited_sentences` → 分母恒为 0、分子照常非零，印出「接地 3/0」这种结构上不可能的数。
# 所以键名只允许写在这一处，并由自检 J 拿真 `grounding_report` 的返回形状现场验。
CITE_CITED, CITE_GROUNDED = "cited_sentences", "grounded"
pre = collections.Counter()
post = collections.Counter()
plan_ok = 0
n = 0
fail_reasons = collections.Counter()
calls = 0
resampled = 0
lengths = []
cards: list[int] = []      # 每条回复当轮拿到几张论文卡：闸门开火率的分母
rows: list[dict] = []      # 每条的前置/后置长度与接地数：代价（轮 116 的 N2/N3）要看这一列

_chat = mentor.chat


async def spy_chat(prompt, **kw):
    global calls
    calls += 1
    return await _chat(prompt, **kw)


_repair = mentor.repair


def spy_repair(text, **kw):
    global n, plan_ok, resampled
    out = _repair(text, **kw)
    n += 1
    import inspect
    kw.setdefault("papers", [])
    kw.setdefault("history", [])
    kw.setdefault("user_text", "")
    allowed = set(inspect.signature(mentor.violations).parameters)   # 部署中的 build 可能还没有新参数
    arg = {k: kw[k] for k in ("papers", "history", "user_text") if k in allowed}
    if "plan" in allowed:
        arg["plan"] = kw.get("plan")
    v_pre = mentor.violations(text, **arg)
    v_post = mentor.violations(out, **arg)
    for k in keys:
        pre[k] += bool(v_pre[k])
        post[k] += bool(v_post[k])
    resampled += any(v_pre.values())
    if kw.get("plan"):
        plan_ok += 1
    else:   # 计划没产出时把 intent.extra 里的原因收上来（44% → 归因，不再靠猜）
        intent = kw.get("intent")
        why = str(getattr(intent, "extra", {}).get("plan_fail", "未记录"))[:70] if intent else "无 intent"
        fail_reasons[why] += 1
    lengths.append(len(out))
    nc = len(arg.get("papers") or [])
    cards.append(nc)
    # 轮 117（任务 #20）：代价的两个字段。轮 116 的 N2/N3 判据之所以落空，就是因为这台只印
    # 闸门后的长度、也算过接地——"判据必须绑定这台仪器真会打印的字段"，这两列就是那次的还账。
    ev = None
    if nc:
        from companion.literature import evidence
        ev = evidence.grounding_report(out, arg["papers"])
    rows.append({"n": n, "cards": nc, "pre_len": len(text or ""), "post_len": len(out),
                 "fired": bool(v_pre["no_citation"]) if "no_citation" in v_pre else False,
                 "cited": (ev or {}).get(CITE_CITED, 0), "grounded": (ev or {}).get(CITE_GROUNDED, 0)})
    # 逐样本即时打印：这支探针原来只在最后一次性输出，几小时的跑法中途看不出
    # "卡住了"还是"在跑"，也看不见失败/跳过计数（长跑前的只读体检因此无从下手）。
    # 卡数必须一起印：轮 115 的 `no_citation` 报了 0/48 开火，可没有分母就分不清
    # "闸门没出手（这批回复本来就没卡）"与"闸门坏了"——那是两回事。
    fired = ",".join(k for k in keys if v_pre[k]) or "无"
    left = ",".join(k for k in keys if v_post[k]) or "无"
    tail = f"｜接地 {rows[-1]['grounded']}/{rows[-1]['cited']}" if nc else ""
    print(f"[{n}] 长度 {len(out):>4d}（前置 {len(text or ''):>4d}）｜卡 {nc}｜闸门前 {fired}｜闸门后 {left}"
          f"{tail}｜计划={'有' if kw.get('plan') else '无'}", flush=True)
    return out


mentor.chat = spy_chat
mentor.repair = spy_repair


def _med(xs):
    s = sorted(xs)
    return s[len(s) // 2] if s else 0


def tier_report(rows: list[dict]) -> None:
    """代价分两档报（轮 117）：带卡回复里"≥200 字的实质回答"与"<200 字的过场句"分开。

    为什么要分开：g98 靶子池实测 31 条有 11 条是"我这就去查"式过场句，本来无话可引，
    混在一起量出来的"开火率/长度代价"两档掺着两种相反的东西。
    单独成函数是为了 `--selfcheck` 能拿手算样例喂它（`main()` 在 LLM_MOCK 下直接退出，
    这条打印路径没有模型就跑不到 —— 轮 90 的规矩：新指标先过手算样例自检才许出数）。
    """
    withc = [r for r in rows if r["cards"]]
    # 结构不变量：接地句数 ≤ 带角标句数。违反它只可能是"仪器读错了键名/口径"，不是模型行为——
    # g98 第一次跑就印出「接地 3/0」，正是我把 `cited` 当成键名（实为 `cited_sentences`）。
    impossible = [r for r in withc if r["grounded"] > r["cited"]]
    if impossible:
        print(f"❌ 结构不可能：{len(impossible)}/{len(withc)} 条的接地数 > 带角标句数 ⇒ "
              f"仪器与 `grounding_report` 的口径已分叉，**这份代价整体作废**（先修仪器再跑）。")
    for label, tier in (("实质回复（前置 ≥200 字）", [r for r in withc if r["pre_len"] >= 200]),
                        ("过场句（前置 <200 字）", [r for r in withc if r["pre_len"] < 200])):
        if not tier:
            print(f"代价·{label}：这一档一条都没有 ⇒ 无读数的维度，不判")
            continue
        fired_t = [r for r in tier if r["fired"]]
        cited = sum(r["cited"] for r in tier)
        grounded = sum(r["grounded"] for r in tier)
        print(f"代价·{label}：{len(tier)} 条｜no_citation 开火 {len(fired_t)}/{len(tier)} = "
              f"{100 * len(fired_t) / len(tier):.0f}%｜接地 {grounded}/{cited}"
              + ("（带角标句 0 条 ⇒ 接地率无从算，这不是 0%）" if not cited else ""))
        if not fired_t:
            print("  ⚠️ 这一档 0 次开火 ⇒ 代价未测：既不能说安全，也不能说有害。")
            continue
        pre_med, post_med = _med([r["pre_len"] for r in fired_t]), _med([r["post_len"] for r in fired_t])
        g_f = sum(r["grounded"] for r in fired_t)
        c_f = sum(r["cited"] for r in fired_t)
        print(f"  开火那 {len(fired_t)} 条才是代价所在：长度中位 {pre_med} → {post_med} 字"
              f"（{100 * (post_med / pre_med - 1):+.0f}%，线是下降不超 15%）"
              f"｜这批接地 {g_f}/{c_f}"
              + ("　⚠️ 开火数 <8 ⇒ 只作个案描述，不判显著" if len(fired_t) < 8 else ""))


def selfcheck() -> int:
    """手算样例（每个只动一个维度），验的是措辞与算术，不是模型行为：

    A 空池            → 两档各印一次「无读数」，且不许出现百分比
    B 带卡 0 次开火    → 「代价未测」，且不许出现长度中位那行（0 次开火没有代价样本）
    C 开火但角标句 0   → 「这不是 0%」（分母为零的统计量必须自己说"没测"）
    D 3 条 500→300    → 「-40%」（中位数算术）+「<8 只作个案」（小样本护栏）
    E 只有过场句       → 必须落在「过场句」档而不是「实质回复」档（切分线 200 本身要活性）
    F 卡片数 0        → 两档都无读数（`cards` 过滤器要活性：没卡的回复没有资格进代价）

    E/F 是变异测试逼出来的：只放 ≥200 字的样例时，把切分线改成 `>= 0`、把 cards 过滤删掉，
    前四项照样全绿 —— 自检覆盖不到分档条件本身，等于没测（第一轮跑出来就是这个结果）。
    G 用例池读入（g98 新增的 `--cases` 路径）：31 行里 2 个重复 + 1 条空 user_text →
    必须只剩 2 个提问，且自报的分母要说真话。
    H 用例池路径：给的路径不存在时必须返回空串（调用方据此 RC=2），不许把原路径回吐。
    I `--cases` 光杆：写了 flag 却没带上值 ⇒ 必须判 2（不许静默用内置情景）。
    J `grounding_report` 的键名与不变量：直接用产品函数跑一次，键不在或 grounded>cited 就红。
    K 结构不变量必须会喊：喂一条 grounded>cited 的假样本，`tier_report` 必须印「结构不可能」。

    J/K 的夹具就是 g98 第一次跑的现代场事故：键名写成 `cited`（实为 `cited_sentences`），
    分母恒 0、分子照常 → 印出「接地 3/0」。这种数**只错在键名上**，两档对账两边同源、照样"对上了"。
    """
    def row(**kw):
        base = {"n": 1, "cards": 8, "pre_len": 500, "post_len": 300,
                "fired": False, "cited": 3, "grounded": 2}
        base.update(kw)
        return base

    empty_sub = "实质回复（前置 ≥200 字）：这一档一条都没有"
    empty_pass = "过场句（前置 <200 字）：这一档一条都没有"
    cases = [
        ("A 空池", [], ["无读数"], ["%", "代价未测"]),
        ("B 带卡 0 次开火", [row()], ["代价未测"], ["→"]),
        ("C 开火但无角标句", [row(fired=True, cited=0, grounded=0)], ["这不是 0%", "-40%"], []),
        ("D 3 条 500→300", [row(fired=True) for _ in range(3)],
         ["-40%", "开火数 <8", "500 → 300", "开火 3/3 = 100%", "接地 6/9"], []),
        ("E 只有过场句", [row(pre_len=80, post_len=60)],
         ["过场句（前置 <200 字）：1 条", empty_sub], ["实质回复（前置 ≥200 字）：1 条"]),
        ("F 卡片数为 0", [row(cards=0)], [empty_sub, empty_pass], ["开火"]),
    ]
    bad = 0
    for name, rows, wants, forbids in cases:
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            tier_report(rows)
        got = buf.getvalue()
        missing = [w for w in wants if w not in got]
        if name.startswith("A"):
            missing += [] if got.count("无读数") == 2 else [f"「无读数」应出现 2 次，实际 {got.count('无读数')}"]
        extra = [f for f in forbids if f in got]
        ok = not missing and not extra
        bad += not ok
        print(f"[自检 {name}] {'✅' if ok else '❌ 缺 ' + str(missing) + ('／多出 ' + str(extra) if extra else '')}")
        print("  " + got.strip().replace("\n", "\n  "))
    pool = json.dumps([{"user_text": "甲"}, {"user_text": "甲"}, {"user_text": "   "},
                       {"user_text": "乙", "follow": "丙"}, "不是字典"], ensure_ascii=False)
    tmp = pathlib.Path(tempfile.gettempdir()) / "probe_cases_selfcheck.json"
    tmp.write_text(pool, encoding="utf-8")
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        got_g = load_cases(str(tmp))
    line_g = buf.getvalue()
    tmp.unlink(missing_ok=True)
    why_g = []
    if got_g != [("甲", ""), ("乙", "丙")]:
        why_g.append(f"去重/取舍结果 = {got_g}，期望 [(甲,''),(乙,'丙')]")
    if "5 行 → 去重后 2 个提问（追问 1 条）" not in line_g:
        why_g.append("自报分母没说真话（应是 5 行→2 个提问、追问 1）")
    ok_g = not why_g
    # H：`--cases` 找不到文件时必须交空串给调用方去 RC=2 —— 静默退回内置情景会印出
    # 一份"看着像池子读数、其实不是"的数。/tmp 那一档候选在 Windows 上造不出来，
    # 只能在容器里由 g98 现场验（这条限制写在这儿，不留"以为测了"的错觉）。
    here = pathlib.Path(tempfile.gettempdir()) / "probe_cases_here.json"
    here.write_text("[]", encoding="utf-8")
    ok_h = (resolve_cases(str(here)) == str(here)
            and resolve_cases("nope-not-a-pool-xyz.json") == "")
    here.unlink(missing_ok=True)
    bad += not ok_h
    # I：`--cases` 写了却没带上值 ⇒ 必须拒绝出数（runner 会把本地存在的文件当数据吃掉，
    # 这个形状就是这么来的），而正常带值/不带 --cases 两种情况都不该拦。
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        i_results = (cases_preflight(["p", "--cases"], ""),
                     cases_preflight(["p", "--cases", "x.json"], "x.json"),
                     cases_preflight(["p"], ""))
    ok_i = i_results == (2, None, None)
    bad += not ok_i
    print(f"[自检 I --cases 缺值] {'✅' if ok_i else '❌ 应为 (2, None, None)，实得 ' + str(i_results)}")
    # J：拿**产品真的那个 `grounding_report`** 校键名与不变量。
    #    夹具就是本轮的事故本身：g98 第一次跑印出「接地 3/0」，因为我把键名写成 `cited`。
    from companion.literature import evidence
    try:
        real = evidence.grounding_report("这套方法在低照度下仍然有效[1]。",
                                         [{"title": "Low-light survey",
                                           "abstract": "低照度 图像 增强 方法 在夜间 仍然 有效"}])
    except Exception as e:                                      # noqa: BLE001 — 报出来，别咽
        real = {"_调用失败": f"{type(e).__name__}: {str(e)[:70]}"}
    ok_j = (CITE_CITED in real and CITE_GROUNDED in real
            and real[CITE_GROUNDED] <= real[CITE_CITED])
    bad += not ok_j
    print(f"[自检 J grounding_report 键名] {'✅' if ok_j else '❌ 实得形状 ' + str(sorted(real))}")
    # K：结构不变量必须真的会喊（喂一条 grounded>cited 的假样本，必须印「结构不可能」）
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        tier_report([row(grounded=3, cited=0)])
    ok_k = "结构不可能" in buf.getvalue()
    bad += not ok_k
    print(f"[自检 K 不变量会喊] {'✅' if ok_k else '❌ grounded>cited 竟无人报警'}")
    print(f"[自检 H 用例池路径] {'✅' if ok_h else '❌ resolve_cases 没把「找不到」关在门外'}")
    print(f"[自检 G 用例池读入] {'✅' if ok_g else '❌ ' + str(why_g)}")
    print("  " + line_g.strip())
    bad += not ok_g
    print("判读：" + (f"{len(cases) + 5} 项都过 ⇒ 这份汇总打印的算术、措辞、分档条件与用例池读入可信，可以拿去烧模型。"
                     if not bad else f"{bad} 项不过 ⇒ 仪器自己就错，先去修它，别跑真样本。"))
    return 1 if bad else 0


def main() -> int:
    if SELFCHECK:
        return selfcheck()
    blocked = cases_preflight(sys.argv, CASES_ARG)
    if blocked:
        return blocked
    if getattr(llm, "LLM_MOCK", False):
        print("LLM_MOCK=1：闸门产量必须在真模型上测，这里不出数。")
        return 3
    from companion.conversation import dialogue_manager as dm
    cases = CASES
    if CASES_ARG:
        got = resolve_cases(CASES_ARG)
        if not got:
            print(f"用例池 {CASES_ARG} 找不到（也试过 /tmp/ 与同目录），cwd={os.getcwd()}\n"
                  f"→ 不出数：宁可退错也别把内置 5 情景的读数当成池子读数印出去。")
            return 2
        cases = load_cases(got)
        if not cases:
            print(f"用例池 {got} 读出来 0 个可用提问（缺 user_text？）→ 无靶子，不出数")
            return 2
    # 每个情景两轮（首句 + 追问），所以上限不是"N×情景"：
    # 上一版文件名写成 n24（按 6×4 算），实际样本是 48 —— 分母标错比没测更坏，这里自报。
    turns = sum(2 if f else 1 for _, f in cases)
    print(f"探针上限：{len(cases)} 情景 × {N} 轮 × 各 {turns / len(cases):.1f} 轮对话 = {N * turns} 条回复"
          f"｜比对键集 {len(keys)} 道闸门", flush=True)
    for rep in range(N):
        for say, follow in cases:
            turn = dm.handle_message(say, None)
            if follow:
                dm.handle_message(follow, turn["project_id"])
    print(f"样本：{n} 次回复，{calls} 次模型调用（平均 {calls / max(1, n):.2f} 次/回复）")
    print(f"规划阶段产出计划的比例：{plan_ok}/{n} = {100 * plan_ok / max(1, n):.0f}%")
    if fail_reasons:
        print("没产出计划的原因分布：")
        for why, cnt in fail_reasons.most_common(6):
            print(f"  {cnt:>2}× {why}")
    print(f"触发过重采样的回复：{resampled}/{n} = {100 * resampled / max(1, n):.0f}%"
          f"｜回复长度中位 {sorted(lengths)[len(lengths) // 2] if lengths else 0} 字")
    print("逐道闸门：违规率 闸门前 → 闸门后（闸门必须让违规变少，不能变多）")
    bad = 0
    for k in keys:
        a, b = pre[k], post[k]
        mark = ""
        if b > a:
            mark = "　✗ 变差"
            bad += 1
        print(f"  {k:<14} {a}/{n} = {100 * a / max(1, n):>3.0f}%  →  {b}/{n} = {100 * b / max(1, n):>3.0f}%{mark}")
    # 分母自证：`no_citation` 只对"当轮给了论文卡"的回复有资格开火，用全量分母印 0% 会被读成"不会误伤"。
    with_cards = sum(1 for c in cards if c)
    print(f"\n带论文卡的回复：{with_cards}/{n}｜空池 {n - with_cards} 条（闸门对空池按设计不开火，不是坏了）")
    fired_c = pre.get("no_citation", 0)
    if with_cards >= 8:
        print(f"闸门开火率（只在带卡回复上算）：no_citation {fired_c}/{with_cards} = "
              f"{100 * fired_c / with_cards:.0f}%　→ 分母够，这条闸门的代价可读")
    else:
        print(f"⚠️ 带卡回复只有 {with_cards} 条（<8）：no_citation {fired_c} 次开火**不能**读成"
              "「代价测过了」，也**不能**读成「不会误伤」——这台仪器仍然看不见它，预登记的判据在这里判「未测」。")
    # 结论行只判"有没有变多"，可"全部都在减少违规"这句话会把**持平**的说成减少（g97 里 bad_axes 1→1 就被
    # 这句话盖过去了）。分开报：没变多 ≠ 已修好。
    flat = [k for k in keys if pre[k] and pre[k] == post[k]]
    tier_report(rows)
    print("结论：" + (f"没有闸门让违规变多；但{flat} 前后持平——「没变多」不等于「已修好」" if not bad
                     else f"{bad} 道闸门让违规变多了，先修它别看胜率"))
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
