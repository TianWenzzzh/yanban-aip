"""P3 缺口→子查询 A/B · **第二场（新预登记）**：把可用对分母从 9 抬到 >12。

同一批真人料、同一 build、进程内两臂，只换「补检索开不开」这一个开关。
臂 A = 把 `engine.gap_subqueries` 打桩成恒空（不发补检索）；臂 B = 产品默认（发）。
两臂跑的是**同一串用户话**：第一句逐字来自 g154 真人料池（B 站评论，带 bvid/rpid），
第二句逐字来自产品自己的 `CHIP_OPTIONS[*].say` ⇒ 不是本轮新造的题。

为什么要单独开这一场（不是回头改上一场的判据）：
`g157` 那份 9 对读数被 S5 判成"探索性"，而它的损耗归因已经查清了 ——
`g161` 按 `kind` 交叉表：首问即放行 9 对 ⇒ **9 对可用（100%）**；点一颗 chip 后放行 15 对 ⇒
**0 对可用（0%）**。这个维度在**处理前**就可判定，所以换抽样框是合法的扩分母手段。
本场只取 `--only first` 那 15 条（`select_ab_cases.py` 在同一判据下重跑得到的全量）。

判据（跑前写死，跑完不许改）：
  S1 操纵真生效：臂 A 的 `extra["subquery"]` 必须一条都没有；臂 B 在检索轮里必须**有**。
     两边都没有 ⇒ 打桩没咬到 / 这批料根本不触发，读数无效（RC=2），不许读成"补检索没差别"。
  S2 可用对 = 两臂都带卡的对数；**MIN_USABLE_PAIRS=13（就是「>12」那条线）**，
     不足 ⇒ 判「未测」（RC=3）并把逐条损耗原因表印出来（没走到检索轮 / 抛错 / 没走到 repair）。
  S3 🔴 统计口径改掉上一场的一处错：上一场印的「A 合计 7000.0」是**把逐对比率乘 1000 再相加**，
     那个"合计"根本不是率。本场三档都按**合并池**报（Σ分子 / Σ分母），逐对方向只做符号检验，
     并对「合并差值」做配对 bootstrap 95% CI（重抽对子 2000 次，种子固定）。
  S4 延迟与模型调用数一起报（补检索的代价是每轮 4 次源调用 + 更长的池子）。
  S5 样本量定性：>12 对才许写"预登记检验"；本场 13—15 对 ⇒ 分辨力有限，CI 含 0 就记「未证明」，
     不许把"点估计 B 更高"读成有效。
  S6 教学维度只报数、不做判据（`teaching.student_next_step`）——它不是这条改动的靶子。
  S7 通过线（三条同时成立才算"留下"）：
     ① recall（接地句/带角标句）合并差值 B−A 的 95% CI 下界 > 0；
     ② 两档 precision（挂过/列出、撑住/挂过）合并差值的 CI 上界都 ≥ −2.0pp（不显著摊薄）；
     ③ S1 哨兵绿。
     触发 ①反号（recall CI 上界 < 0）或任一 precision CI 上界 < −2.0pp ⇒ 判"不留下"（RC=1）；
     其余 ⇒ 判"未证明"（RC=5，独立退出码：既不是通过也不是被否，别与 1 混）。
  S8 单样本 try/except 已经在了；可用对 < 本场尝试对数（计划 15）时**打印差额与逐条归因**，
     并把"通过/不留下"这两类结论降级成 RC=4（薄样本拒绝充当那场预登记结果，轮 106 的规矩）；
     可用对 < 13 直接 RC=3（未测）。

跑法（容器里才有 GLM 密钥与文献源；detached + waiter，约 15 对 × 两臂 ≈ 1 小时）：
  MSYS_NO_PATHCONV=1 PYTHONUTF8=1 /d/anaconda/python.exe 输出/scripts/run_cloud_probe.py \\
     <本文件绝对路径> <g161_ab_cases_first.json 绝对路径> --cases /tmp/g161_ab_cases_first.json 15 --detach
"""
from __future__ import annotations

import inspect
import json
import math
import os
import pathlib
import random
import re
import sys
import time

sys.path.insert(0, "/app")
sys.path.insert(0, ".")
_HERE = pathlib.Path(__file__).resolve()
for _cand in ["/app", str(pathlib.Path.cwd()), *map(str, _HERE.parents)]:
    if (pathlib.Path(_cand) / "companion").is_dir():
        sys.path.insert(0, _cand)
        break
else:
    print("判读：找不到含 companion/ 的仓根 ⇒ 仪器没连上被测物，不出数（RC=2）")
    sys.exit(2)

if os.environ.get("SANDBOX_DB", "1") == "1":
    sys.path.insert(0, str(_HERE.parent))
    from sandbox_data import isolate as _isolate      # noqa: E402
    _sb = _isolate(os.environ.get("SANDBOX_ROOT", "/tmp/probe_sandbox_gapsub"))
    print(f"沙箱已接管：DB_PATH {_sb['before']['DB_PATH']} → {_sb['after']['DB_PATH']}", flush=True)

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

import llm                                          # noqa: E402
from companion.conversation import mentor           # noqa: E402
from companion.conversation.teaching import student_next_step   # noqa: E402
from companion.literature import engine             # noqa: E402

MIN_USABLE_PAIRS = 13        # S2：就是「把分母抬到 >12」那条线
EXPLORE_MAX = 12             # ≤12 对只许写探索性读数
PLANNED = 15                 # S8：本场计划尝试的对数（--only first 全量），不足就印差额
BOOT_ITERS = 2000            # S3：配对 bootstrap 重抽次数
SEED = 20260926              # 固定种子：同一份读数必须能逐位复算
PREC_FLOOR_PP = 2.0          # S7②：precision 允许的最大摊薄（百分点，CI 上界口径）
CASES_ARG = next((sys.argv[i + 1] for i, a in enumerate(sys.argv[:-1]) if a == "--cases"), "")
N = next((int(a) for a in sys.argv[1:] if a.isdigit()), PLANNED)   # 缺省就是预登记的那 15 对

ARM = "B"
SLOT: dict = {}
rows: list[dict] = []
search_log: list[dict] = []
skipped: list[str] = []
calls = {"A": 0, "B": 0}
latency = {"A": [], "B": []}
_chat, _repair = mentor.chat, mentor.repair
_gapsub = engine.gap_subqueries
_search = engine.search_for_intent
_ALLOWED = set(inspect.signature(mentor.violations).parameters)


async def spy_chat(prompt, **kw):
    calls[ARM] += 1
    return await _chat(prompt, **kw)


def spy_search(intent, limit: int = 8) -> list[dict]:
    out = _search(intent, limit=limit)
    search_log.append({"arm": ARM, "case": SLOT.get("case"), "cards": len(out),
                       "subquery": dict((intent.extra or {}).get("subquery") or {})})
    return out


def stub_a(on: bool) -> None:
    """臂 A：把缺口→子查询打桩成恒空。还原用存下来的原件，不 del 模块属性。"""
    engine.gap_subqueries = (lambda gaps, queries: []) if on else _gapsub


def measure(text: str, out: str, kw: dict) -> dict:
    papers = kw.get("papers") or []
    arg = {k: v for k, v in (("papers", papers), ("history", kw.get("history") or []),
                             ("user_text", kw.get("user_text") or "")) if k in _ALLOWED}
    if "plan" in _ALLOWED:
        arg["plan"] = kw.get("plan")
    ev = None
    if papers:
        from companion.literature import evidence
        ev = evidence.grounding_report(out, papers)
    ev = ev or {}
    sup = ev.get("supports") or []
    # 🔴 键名必须取自产品自己的返回：`grounding_report` 给的是 `cited_sentences`。
    # 上一场这里读 `cited` 拿到恒 0，于是印出「带角标句 0｜接地 3」这种自相矛盾的数——
    # 人眼能看出来，但那时已经烧了 6 次调用。所以把恒等式做成**判定**而不是靠人眼。
    cited = ev.get("cited_sentences")
    grounded = ev.get("grounded", 0)
    bad = []
    if papers and cited is None:
        bad.append("grounding_report 没返回 cited_sentences ⇒ 尺子接错了，别继续跑")
    if cited is not None and grounded > cited:
        bad.append(f"接地 {grounded} > 带角标句 {cited} ⇒ 不可能成立")
    if len(sup) != len(papers):
        bad.append(f"supports 长度 {len(sup)} ≠ 卡数 {len(papers)}")
    cited_idx = {i for r in (ev.get("rows") or []) for i in (r.get("cited") or [])}
    hung = len(cited_idx & set(range(1, len(papers) + 1)))
    supported = sum(1 for i, n in enumerate(sup, 1) if n > 0 and i in cited_idx)
    if supported > hung:
        bad.append(f"撑住 {supported} 篇 > 挂过 {hung} 篇 ⇒ 不可能成立")
    if bad:
        raise KeyError("；".join(bad))
    led = next((s["subquery"] for s in reversed(search_log)
                if s["arm"] == ARM and s["case"] == SLOT.get("case")), {})
    return dict(arm=ARM, case=SLOT.get("case"), cards=len(papers),
                # S2 的损耗归因要用它分家：cards=0 且 searched=False 是**没走到检索轮**（CLARIFY 循环），
                # searched=True 而 cards=0 才是"查了但没结果 / provider 全挂"（轮 96 的口径）
                searched=any(s["arm"] == ARM and s["case"] == SLOT.get("case") for s in search_log),
                cited=cited or 0, grounded=grounded,
                listed=len(papers), hung=hung, supported=supported,
                sub=bool(led), sub_added=int(led.get("added") or 0),
                sub_gap=str(led.get("gap") or ""),
                next_step=bool(student_next_step(out)), post_len=len(out))


def spy_repair(text, **kw):
    out = _repair(text, **kw)
    if not SLOT:
        return out
    try:
        r = measure(text, out, kw)
    except KeyError as exc:      # S0 恒等式不成立 ⇒ 尺子接错了，立刻停，别再烧调用
        print(f"\n判读：S0 恒等式红在 case {SLOT.get('case')} 臂 {ARM}：{exc}\n"
              "⇒ 这台仪器的取数键与产品返回不一致，整场读数作废（RC=2）", flush=True)
        sys.stdout.flush()
        os._exit(2)
    rows.append(r)
    print(f"[{len(rows)}] case {r['case']} 臂 {r['arm']}｜卡 {r['cards']}｜带角标句 {r['cited']}"
          f"｜接地 {r['grounded']}｜挂过 {r['hung']}/{r['listed']} 篇｜撑住 {r['supported']}"
          f"｜补检索={'有' if r['sub'] else '无'}(+{r['sub_added']} 篇 {r['sub_gap']})"
          f"｜学生下一步={'有' if r['next_step'] else '无'}｜长度 {r['post_len']}"
          f"｜调用 A:{calls['A']} B:{calls['B']}", flush=True)
    SLOT.clear()
    return out


mentor.chat = spy_chat
mentor.repair = spy_repair
engine.search_for_intent = spy_search


def binom_p(k_b: int, k_a: int) -> str:
    """双侧精确符号检验 p；没有不等对就说「没测」，不印 p=1.0000 冒充结论。"""
    n = k_b + k_a
    if n == 0:
        return "未测（0 局判定）"
    lo = min(k_a, k_b)
    p = min(1.0, 2.0 * sum(math.comb(n, i) for i in range(0, lo + 1)) / (2.0 ** n))
    return f"{p:.4f}"


def paired(rs: list[dict]) -> list[tuple[dict, dict]]:
    by = {"A": {}, "B": {}}
    for r in rs:
        by[r["arm"]][r["case"]] = r
    return [(by["A"][c], by["B"][c]) for c in sorted(set(by["A"]) & set(by["B"]))]


def rate(rs: list[dict], num: str, den: str) -> tuple[float | None, int, int]:
    """合并池率 Σ分子/Σ分母 —— 上一场把逐对比率乘 1000 相加当成「合计」，那不是率。"""
    n = sum(r[num] for r in rs)
    d = sum(r[den] for r in rs)
    return (n / d if d else None), n, d


def dim(pairs: list[tuple[dict, dict]], name: str, num: str, den: str) -> dict:
    """一档指标：合并率 + 合并差值的配对 bootstrap 95% CI + 逐对方向符号检验。

    🔴 分母为 0 时（整批回复一条角标都没挂）率是 None：印「没测」并把 lo/hi 交回 None，
    让 `decide` 去判未证明 —— 上一版这里直接 `f"{pp:+.1f}"` 会在跑完一小时后崩在收尾。
    """
    ra, na, da = rate([x for x, _ in pairs], num, den)
    rb, nb, db = rate([y for _, y in pairs], num, den)
    diffs = [(y[num] / y[den] - x[num] / x[den]) for x, y in pairs if x[den] and y[den]]
    b_better = sum(1 for v in diffs if v > 0)
    a_better = sum(1 for v in diffs if v < 0)
    rng = random.Random(SEED)
    boot: list[float] = []
    for _ in range(BOOT_ITERS):
        s = [pairs[rng.randrange(len(pairs))] for _ in pairs]
        va, _, _ = rate([x for x, _ in s], num, den)
        vb, _, _ = rate([y for _, y in s], num, den)
        if va is not None and vb is not None:
            boot.append(vb - va)
    boot.sort()
    lo = boot[int(0.025 * len(boot))] if boot else None
    hi = boot[int(0.975 * len(boot))] if boot else None
    pp = (rb - ra) * 100 if (ra is not None and rb is not None) else None
    tail = (f"差 {pp:+.1f}pp [{100 * lo:+.1f},{100 * hi:+.1f}]"
            if pp is not None and lo is not None and hi is not None else
            "🔴 这一档没测出来（分母为 0 或全部对子缺该维度）")
    print(f"  {name:<22} A {100 * (ra or 0):.1f}%（{na}/{da}）｜B {100 * (rb or 0):.1f}%（{nb}/{db}）"
          f"｜{tail}｜逐对 B更好 {b_better}｜A更好 {a_better}"
          f"｜平 {len(diffs) - b_better - a_better}｜符号检验 p={binom_p(b_better, a_better)}")
    return {"name": name, "pp": pp, "lo": None if lo is None else 100 * lo,
            "hi": None if hi is None else 100 * hi}


def decide(n_pairs: int, attempted: int, r_rec: dict, r_pre: dict, r_sup: dict) -> tuple[int, str]:
    """S7 通过线做成纯函数，好让 `--selfcheck` 能在起跑前证明它真的会分方向。"""
    dims = (r_rec, r_pre, r_sup)
    if any(d["hi"] is None for d in dims):
        missing = "、".join(d["name"] for d in dims if d["hi"] is None)
        return 5, f"判读：{missing} 没测出来 ⇒ 不下「留下」也不下「不留下」（RC=5）"
    hurt = [d for d in dims if d["hi"] < -PREC_FLOOR_PP]
    demote = n_pairs < attempted
    if hurt:
        rc, text = 1, (f"判读：{'、'.join(d['name'] for d in hurt)} 的 CI 上界都在 "
                       f"−{PREC_FLOOR_PP}pp 以下 ⇒ **不留下**这条改动（RC=1）")
    elif r_rec["lo"] > 0.0:
        rc, text = 0, (f"判读：recall 合并差 {r_rec['pp']:+.1f}pp 的 95% CI 下界 "
                       f"{r_rec['lo']:+.1f}pp > 0，两档 precision 未被显著摊薄 ⇒ **留下**（RC=0）；"
                       "代价见 S4 与调用数")
    else:
        rc = 5
        text = (f"判读：CI 含 0（recall {r_rec['lo']:+.1f}→{r_rec['hi']:+.1f}pp）⇒ **未证明**（RC=5）。"
                "这既不是「补检索有用」也不是「补检索有害」，下一轮别拿点估计替它说话")
    if demote and rc in (0, 1):
        return 4, (f"判读：本场只跑成 {n_pairs}/{attempted} 对 ⇒ 按 S8 拒绝充当那份预登记结果，"
                   f"上面那句只算倾向（RC=4）")
    return rc, text


def attrition(cases: list[dict], rs: list[dict], skipped: list[str]) -> list[str]:
    """把"没进分母"的对逐条归因成三种类别（互斥），别让它变成一个笼统的 0。

    类别取自 g161 那份交叉表暴露的形状：`点一颗 chip 后放行` 的料 15 对全部 0 卡，
    上一场只印了「可用对 9」，看不出是"没走到检索轮"还是"查了没结果"——
    而这两件事的下一步动作完全相反（前者是抽样框/状态机问题，后者是 provider 可用性问题）。
    """
    by = {"A": {}, "B": {}}
    for r in rs:
        by[r["arm"]][r["case"]] = r
    lost = set()
    for x in skipped:
        m = re.match(r"[AB](\d+)", str(x))
        if m:
            lost.add(int(m.group(1)))
    out = []
    for i in range(1, len(cases) + 1):
        kind = str(cases[i - 1].get("kind") or "?")
        ra, rb = by["A"].get(i), by["B"].get(i)
        if ra is None or rb is None or i in lost:
            out.append(f"case {i}（{kind}）：缺样——抛错或没走到 repair")
        elif ra["cards"] >= 1 and rb["cards"] >= 1:
            continue
        elif not (ra["searched"] or rb["searched"]):
            out.append(f"case {i}（{kind}）：两臂都没走到检索轮（按设计 CLARIFY 不检索）")
        elif not (ra["searched"] and rb["searched"]):
            out.append(f"case {i}（{kind}）：只有一臂走到检索轮 A searched={ra['searched']}"
                       f" B searched={rb['searched']}")
        else:
            out.append(f"case {i}（{kind}）：走到检索轮但 0 卡（A {ra['cards']}｜B {rb['cards']}）")
    return out


def resolve_cases(arg: str) -> str:
    for cand in (arg, f"/tmp/{os.path.basename(arg)}", str(_HERE.parent / os.path.basename(arg))):
        if cand and pathlib.Path(cand).is_file():
            return cand
    return ""


def main() -> int:
    global ARM
    if getattr(llm, "LLM_MOCK", False):
        print("判读：LLM_MOCK=1 ⇒ 两臂都是模板话，量不出补检索的贡献，不出数（RC=3）")
        return 3
    got = resolve_cases(CASES_ARG)
    if not got:
        print(f"判读：用例池 {CASES_ARG!r} 找不到 ⇒ 拒绝出数（不内置情景，免得把合成料读成线上料，RC=2）")
        return 2
    doc = json.loads(pathlib.Path(got).read_text(encoding="utf-8"))
    cases = (doc["cases"] if isinstance(doc, dict) else doc)[:N]
    print(f"用例：{len(cases)} 对｜来源 {doc['meta']['pool'] if isinstance(doc, dict) else '?'}"
          f"｜两臂各 1 条回复 ⇒ 上限 {2 * len(cases)} 条", flush=True)
    kinds = sorted({str(c.get("kind")) for c in cases})
    print(f"抽样框哨兵：{len(cases)} 对｜kind 集合 {kinds}", flush=True)
    if len(cases) != PLANNED or kinds != ["首问即放行"]:
        print(f"判读：本场预登记的抽样框是「{PLANNED} 对 · 首问即放行」，拿到 {len(cases)} 对 {kinds}"
              "⇒ 这不是那一场，拒绝出数（换框就是换母体，RC=2）")
        return 2
    from companion.conversation import dialogue_manager as dm

    for i, c in enumerate(cases, 1):
        for arm in ("A", "B"):
            ARM = arm
            stub_a(arm == "A")
            SLOT.clear()
            SLOT["case"] = i
            t0 = time.monotonic()
            pid = None
            try:
                for turn in c["turns"]:
                    r = dm.handle_message(turn, pid)
                    pid = r.get("project_id") or pid
            except Exception as exc:  # noqa: BLE001 — 单条抖动只丢这一条
                print(f"[!] case {i} 臂 {arm} 抛错被跳过：{type(exc).__name__} {str(exc)[:80]}", flush=True)
                skipped.append(f"{arm}{i}")
                continue
            latency[arm].append(time.monotonic() - t0)
            if SLOT:
                print(f"[!] case {i} 臂 {arm} 没走到 repair（记为失败，不进分子分母）", flush=True)
                skipped.append(f"{arm}{i}（没走到 repair）")
            SLOT.clear()
    stub_a(False)
    engine.gap_subqueries = _gapsub

    a = [r for r in rows if r["arm"] == "A"]
    b = [r for r in rows if r["arm"] == "B"]
    sub_a = [s for s in search_log if s["arm"] == "A" and s["subquery"]]
    sub_b = [s for s in search_log if s["arm"] == "B" and s["subquery"]]
    print(f"\n合计 {len(rows)} 条回复（A {len(a)} / B {len(b)}）｜跳过 {len(skipped)} 条 {skipped[:8]}"
          f"｜模型调用 A {calls['A']} + B {calls['B']} = {calls['A'] + calls['B']} 次")
    med = lambda v: (sorted(v)[len(v) // 2] if v else 0.0)
    print(f"S4 延迟（整串对话，秒）：A 中位 {med(latency['A']):.1f}｜B 中位 {med(latency['B']):.1f}"
          f"｜A 最大 {max(latency['A'] or [0]):.1f}｜B 最大 {max(latency['B'] or [0]):.1f}")
    print(f"S1 操纵自证：臂 A 带 subquery 的检索轮 {len(sub_a)} 次（必须 0）"
          f"｜臂 B 带 subquery 的检索轮 {len(sub_b)} 次（必须 >0）")
    if not a or not b:
        print("判读：有一臂整条没产出 ⇒ 这不叫对照（RC=2）")
        return 2
    if sub_a:
        print(f"判读：S1 红（臂 A 仍有 {len(sub_a)} 次补检索）⇒ 打桩没咬到，读数无效（RC=2）")
        return 2
    if not sub_b:
        print("判读：S1 红（臂 B 一次补检索都没发）⇒ 这批料不触发缺口→子查询，"
              "读数无效：既不能说有用，也不能说没用（RC=2）")
        return 2

    pairs = [(ra, rb) for ra, rb in paired(rows) if ra["cards"] >= 1 and rb["cards"] >= 1]
    print(f"\nS2 可用对（两臂都带卡）：{len(pairs)} 对｜本场尝试 {len(cases)} 对｜下限 {MIN_USABLE_PAIRS}")
    if len(pairs) < len(cases):
        print(f"  差额 {len(cases) - len(pairs)} 对，逐条归因（类别互斥）：")
        for line in attrition(cases, rows, skipped):
            print("   " + line)
    if len(pairs) < MIN_USABLE_PAIRS:
        print(f"判读：可用对 {len(pairs)} <{MIN_USABLE_PAIRS} ⇒ 本轮判不了留下还是收窄，"
              f"按预登记记**未测**（RC=3）。这不是两臂等价。")
        return 3

    print(f"\nS3 分开报（ALCE 的两半；合并池率 + 配对 bootstrap {BOOT_ITERS} 次、种子 {SEED}）：")
    r_rec = dim(pairs, "recall 接地/带角标句", "grounded", "cited")
    r_pre = dim(pairs, "precision 挂过/列出", "hung", "listed")
    r_sup = dim(pairs, "precision 撑住/挂过", "supported", "hung")
    d_cards = sum(x["cards"] for x, _ in pairs)
    d_cards_b = sum(y["cards"] for _, y in pairs)
    d_cited = sum(x["cited"] for x, _ in pairs)
    d_cited_b = sum(y["cited"] for _, y in pairs)
    print(f"  （呈现层，不做判据）相关篇数合计 A {d_cards}｜B {d_cards_b}；"
          f"带角标句合计 A {d_cited}｜B {d_cited_b}")
    ns_a = sum(1 for x, _ in pairs if x["next_step"])
    ns_b = sum(1 for _, y in pairs if y["next_step"])
    print(f"  S6 学生下一步（只报数，不做判据）：A {ns_a}/{len(pairs)}｜B {ns_b}/{len(pairs)}")

    tag = "预登记检验" if len(pairs) > EXPLORE_MAX else "探索性读数"
    print(f"\n样本量定性：{len(pairs)} 对 ⇒ **{tag}**｜通过线 S7 = recall CI 下界 >0 "
          f"且两档 precision CI 上界 ≥−{PREC_FLOOR_PP}pp 且 S1 绿")
    rc, text = decide(len(pairs), len(cases), r_rec, r_pre, r_sup)
    print(text)
    return rc



def selfcheck() -> int:
    """起跑前证明这台仪器会分方向、分母为 0 不崩、损耗归因分得开（手算期望值）。

    规矩：任何自造指标先过手算样例（本会话已经第 5 次栽在指标定义上）。
    四份合成数据都是**手算得出期望**的，不是"跑出来什么就是什么"。
    """
    ok = bad = 0

    def chk(name: str, cond: bool, extra: str = "") -> None:
        nonlocal ok, bad
        print(f"  {'ok ' if cond else '🔴 FAIL'} {name} {extra}")
        ok, bad = (ok + 1, bad) if cond else (ok, bad + 1)

    def row(arm: str, case: int, cards: int, cited: int, grounded: int,
            hung: int, supported: int, searched: bool = True) -> dict:
        return dict(arm=arm, case=case, cards=cards, cited=cited, grounded=grounded,
                    listed=cards, hung=hung, supported=supported, searched=searched,
                    sub=bool(arm == "B"), sub_added=4 if arm == "B" else 0,
                    sub_gap="user_goal", next_step=True, post_len=500)

    # C1：B 每对都把「接地/带角标」从 1/2 抬到 2/2，两档 precision 不降 ⇒ 该判「留下」
    pairs = [(row("A", i, 4, 2, 1, 1, 1), row("B", i, 4, 2, 2, 2, 2)) for i in range(1, 14)]
    d1 = dim(pairs, "recall 接地/带角标句", "grounded", "cited")
    d2 = dim(pairs, "precision 挂过/列出", "hung", "listed")
    d3 = dim(pairs, "precision 撑住/挂过", "supported", "hung")
    chk("C1 recall 合并率 A 50% B 100%", (round(d1["pp"], 1)) == 50.0, f"实得 {d1['pp']}")
    rc, _ = decide(13, 13, d1, d2, d3)
    chk("C1 判留下（RC=0）", rc == 0, f"实得 {rc}")
    rc2, _ = decide(12, 13, d1, d2, d3)
    chk("C1 少一对 ⇒ 降级 RC=4（薄样本不充当预登记结果）", rc2 == 4, f"实得 {rc2}")

    # C2：B 把 precision 砸烂（挂过/列出 100%→25%）⇒ 该判「不留下」
    p2 = [(row("A", i, 4, 2, 1, 4, 1), row("B", i, 4, 2, 1, 1, 1)) for i in range(1, 14)]
    e1 = dim(p2, "recall 接地/带角标句", "grounded", "cited")
    e2 = dim(p2, "precision 挂过/列出", "hung", "listed")
    e3 = dim(p2, "precision 撑住/挂过", "supported", "hung")
    chk("C2 precision 合并差 = -75pp", round(e2["pp"], 1) == -75.0, f"实得 {e2['pp']}")
    rc3, _ = decide(13, 13, e1, e2, e3)
    chk("C2 判不留下（RC=1）", rc3 == 1, f"实得 {rc3}")

    # C3：两臂逐字相同 ⇒ CI 是 [0,0]，点估计 0 ⇒ 只能判「未证明」，不许读成"通过"
    p3 = [(row("A", i, 4, 2, 1, 2, 1), row("B", i, 4, 2, 1, 2, 1)) for i in range(1, 14)]
    f1 = dim(p3, "recall 接地/带角标句", "grounded", "cited")
    f2 = dim(p3, "precision 挂过/列出", "hung", "listed")
    f3 = dim(p3, "precision 撑住/挂过", "supported", "hung")
    chk("C3 CI 恰好 [0,0]", (f1["lo"], f1["hi"]) == (0.0, 0.0), f"实得 {f1['lo']},{f1['hi']}")
    rc4, _ = decide(13, 13, f1, f2, f3)
    chk("C3 判未证明（RC=5）", rc4 == 5, f"实得 {rc4}")

    # C4：整批一条角标都没挂 ⇒ 率为 None、CI 算不出，`dim` 不许崩、`decide` 不许判通过
    p4 = [(row("A", i, 4, 0, 0, 0, 0), row("B", i, 4, 0, 0, 0, 0)) for i in range(1, 14)]
    try:
        g1 = dim(p4, "recall 接地/带角标句", "grounded", "cited")
        g2 = dim(p4, "precision 挂过/列出", "hung", "listed")
        g3 = dim(p4, "precision 撑住/挂过", "supported", "hung")
        crashed = ""
    except Exception as exc:  # noqa: BLE001
        crashed = f"{type(exc).__name__}: {exc}"
        g1 = g2 = g3 = {"name": "", "pp": None, "lo": None, "hi": None}
    chk("C4 分母为 0 不崩", not crashed, crashed)
    rc5, _ = decide(13, 13, g1, g2, g3)
    chk("C5 分母为 0 判未证明（RC=5，不许读成通过）", rc5 == 5, f"实得 {rc5}")

    # C6：损耗归因四类要分得开（可用对不进表），且类别互斥
    cases = [{"kind": "首问即放行"}, {"kind": "首问即放行"}, {"kind": "首问即放行"},
             {"kind": "点一颗 chip 后放行"}]
    rows4 = [row("A", 1, 4, 2, 1, 2, 1), row("B", 1, 4, 2, 2, 2, 2),
             row("A", 2, 0, 0, 0, 0, 0, searched=False), row("B", 2, 0, 0, 0, 0, 0, searched=False),
             row("A", 3, 0, 0, 0, 0, 0), row("B", 3, 5, 1, 1, 1, 1),
             row("A", 4, 0, 0, 0, 0, 0, searched=False)]
    lines = attrition(cases, rows4, ["B4（没走到 repair）"])
    chk("C6 三条归因（可用对 case 1 不列）", len(lines) == 3, str(lines))
    chk("C6 case 2 认成「没走到检索轮」", any("case 2" in l and "没走到检索轮" in l for l in lines))
    chk("C6 case 3 认成「走到检索轮但 0 卡」",
        any("case 3" in l and "0 卡" in l for l in lines))
    chk("C6 case 4 认成「缺样」", any("case 4" in l and "缺样" in l for l in lines))

    # C7：同一份数据两次必须逐位相同（bootstrap 有种子；否则一切 CI 都是噪声表演）
    chk("C7 bootstrap 可复现", dim(pairs, "x", "grounded", "cited")["lo"] == d1["lo"])
    print(f"自检 {ok}/{ok + bad} 通过" + ("" if not bad else f" ⇒ 🔴 仪器自己坏了，不许起跑（RC=2）"))
    return 0 if not bad else 2


if __name__ == "__main__":
    if "--selfcheck" in sys.argv:
        sys.exit(selfcheck())
    sys.exit(main())
