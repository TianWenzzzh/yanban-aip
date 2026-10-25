"""轮 119（任务 #14）：规划层到底买到了什么——同一 build、同一批料，进程内 A/B。

为什么不是"再起一次盲评"：P2 的评委两把尺（判断题、配对裁决）至今没过锚点对体检，
所以这里只用**不经评委**的量具（闸门前违规条数、接地句数、调用数、长度），全部每样本一个数。

B 臂怎么打桩（这一步设计错过一次，别再来）：线上流式路径走 `mentor.plan_prefix`（`mentor.py:985`），
同步 `ask()` 才走 `mentor.make_plan`（`mentor.py:889`）——**只打 `make_plan` 会漏掉流式那条路**，
量出来的是"两臂都带规划"的幻影对照。所以两把一起掐：`make_plan` 与 `plan_prefix` 都换成返回 None，
并由哨兵 S1 逐条核对「B 臂 `repair(plan=...)` 收到的 plan 必须为假」，有一条不是 ⇒ RC=2 整份作废。

两臂**逐情景交替**（case i 先 A 后 B）而不是"跑完 A 再跑 B"：检索供给会随 provider 熔断状态抖
（1 条 ↔ 2 条），隔二十分钟的两批料不在同一起跑线上。配对靠 case 编号，不靠顺序（有一条失败也不错位）。

预登记判据（跑前写死，跑完不许改口径）：
  S1 打桩有效性：B 臂 plan 为真的条数必须 = 0；否则 ⇒「打桩没咬到 ⇒ 读数无效」RC=2。
  S2 对照有效性：A 臂规划产出率 ≥50%（线上实测约一半）；否则 ⇒「这批料本身不产生规划」RC=2。
  S3 分母：成对（两臂都拿到真回复）的对数 ≥ PAIRS_MIN(10) 才判显著；不足 ⇒ RC=3「未测」，只印逐对原行。
  P1 主判据（唯一决策量）：两臂**闸门前违规条数**做配对符号检验（平局剔除；
     无不等对 ⇒ 显式印「不出 p 值」，那是"压根没测"，不许印成 p=1.0000）。
     A 更少且 p<0.05 ⇒ 规划层买到了合规；B 更少且 p<0.05 ⇒ 这条维度上它更差（只否证这一维）；
     判不出 ⇒ **未决**，不得读成"没差别"。
  P2 代价（只记录，不参与判定）：每臂模型调用合计、长度中位、带卡子集的接地句数。
  收尾行以「判读：」开头；聚合只在跑完整份后打印（CI 规矩）。

跑法：
  离线自检  PYTHONUTF8=1 code/yanban/.venv/Scripts/python.exe 输出/scripts/probe_plan_ab.py --selfcheck
  线上       PYTHONUTF8=1 uv run --no-project --with paramiko python 输出/scripts/run_cloud_probe.py \
               输出/scripts/probe_plan_ab.py code/yanban/docs/evidence/g98_cite_pool.json \
               --cases /tmp/g98_cite_pool.json 12
"""
from __future__ import annotations

import inspect
import json
import math
import os
import pathlib
import sys

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

import llm                                         # noqa: E402
from companion.conversation import mentor          # noqa: E402

# 轮 121：这类"走产品入口"的探针每跑一条就往生产库塞一个项目（`g101` 实测线上 754 个全是这么来的）。
# 默认接沙箱（`probe_sandbox_verify.py` 验过：沙箱真出现行、生产库五表一字未动）。
# 要复现"库里本来就带料"的状态时才 SANDBOX_DB=0 —— 那是一次**有意**写生产库。
if os.environ.get("SANDBOX_DB", "1") == "1" and "--selfcheck" not in sys.argv:
    sys.path.insert(0, str(_HERE.parent))
    from sandbox_data import isolate as _isolate     # noqa: E402
    _sb = _isolate(os.environ.get("SANDBOX_ROOT", "/tmp/probe_sandbox_plan_ab"))
    print(f"沙箱已接管：DB_PATH {_sb['before']['DB_PATH']} → {_sb['after']['DB_PATH']}"
          f"（其余三落点一并改；SANDBOX_DB=0 退回生产库）", flush=True)

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

N = next((int(a) for a in sys.argv[1:] if a.isdigit()), 12)
SELFCHECK = "--selfcheck" in sys.argv
CASES_ARG = next((sys.argv[i + 1] for i, a in enumerate(sys.argv[:-1]) if a == "--cases"), "")
PAIRS_MIN = 10
MIN_USABLE = 16      # P1'' 的可用对下限（体检：带卡子集只占 9/12 ⇒ 24 对预计 ~18 可用）
LEN_CONC_MAX = 0.90  # 「密度更高」与「更长」同侧超过这个占比 ⇒ 拒绝把结果归因给规划层
PLAN_RATE_MIN = 0.5
CITE_CITED, CITE_GROUNDED = "cited_sentences", "grounded"    # 键名只写这一处（g98 那次事故的规矩）
KEYS = list(mentor.violations("试", papers=[], history=[]).keys())
_ALLOWED = set(inspect.signature(mentor.violations).parameters)

ARM = "A"
SLOT: dict = {}                       # 当前这一条回复的槽位（case 编号），repair 里填完就清空
rows: list[dict] = []
skipped: list[str] = []
calls = {"A": 0, "B": 0}
_chat, _repair = mentor.chat, mentor.repair
_make_plan, _plan_prefix = mentor.make_plan, mentor.plan_prefix


async def spy_chat(prompt, **kw):
    calls[ARM] += 1
    return await _chat(prompt, **kw)


def measure(text: str, out: str, kw: dict) -> dict:
    arg = {k: v for k, v in (("papers", kw.get("papers") or []),
                             ("history", kw.get("history") or []),
                             ("user_text", kw.get("user_text") or "")) if k in _ALLOWED}
    if "plan" in _ALLOWED:
        arg["plan"] = kw.get("plan")
    v_pre = mentor.violations(text, **arg)
    v_post = mentor.violations(out, **arg)
    papers = kw.get("papers") or []
    ev = None
    if papers:
        from companion.literature import evidence
        ev = evidence.grounding_report(out, papers)
    return dict(arm=ARM, case=SLOT.get("case"), plan=bool(kw.get("plan")), cards=len(papers),
                viol_pre=sum(1 for k in KEYS if v_pre[k]),
                viol_post=sum(1 for k in KEYS if v_post[k]),
                fired=[k for k in KEYS if v_pre[k]], post_len=len(out),
                cited=(ev or {}).get(CITE_CITED, 0), grounded=(ev or {}).get(CITE_GROUNDED, 0))


def spy_repair(text, **kw):
    out = _repair(text, **kw)
    if not SLOT:
        return out                    # 不是本次 A/B 发起的那条（不该发生，真发生就原样放过）
    r = measure(text, out, kw)
    rows.append(r)
    print(f"[{len(rows)}] case {r['case']} 臂 {r['arm']}｜闸门前违规 {r['viol_pre']} 条"
          f"（{','.join(r['fired']) or '无'}）｜闸门后 {r['viol_post']}｜"
          f"计划={'有' if r['plan'] else '无'}｜卡 {r['cards']}｜长度 {r['post_len']}｜"
          f"接地 {r['grounded']}/{r['cited']}｜调用 A:{calls['A']} B:{calls['B']}", flush=True)
    SLOT.clear()
    return out


mentor.chat = spy_chat
mentor.repair = spy_repair


def stub_b(on: bool) -> None:
    """掐规划层：两条产出路径一起（只掐一条 = 幻影对照）。还原用存下来的原件，不 del 模块属性。"""
    if on:
        async def no_prefix(**kw):
            return None
        mentor.make_plan = lambda **kw: None
        mentor.plan_prefix = no_prefix
    else:
        mentor.make_plan = _make_plan
        mentor.plan_prefix = _plan_prefix


def sign_test(pairs: list[tuple[int, int]]) -> tuple[int, int, int, float | None]:
    """(A 更少的对数, B 更少的对数, 平局, 双侧精确 p)；无不等对 ⇒ p=None（没测，不是 p=1）。"""
    a = sum(1 for x, y in pairs if x < y)
    b = sum(1 for x, y in pairs if x > y)
    m = a + b
    if m == 0:
        return a, b, len(pairs), None
    p = min(1.0, 2.0 * sum(math.comb(m, k) for k in range(0, min(a, b) + 1)) / (2.0 ** m))
    return a, b, len(pairs) - m, p


def paired(rows: list[dict]) -> list[tuple[dict, dict]]:
    """按 case 编号配对，不靠出现顺序：中途有单条失败也不会错位。"""
    by = {"A": {}, "B": {}}
    for r in rows:
        by[r["arm"]][r["case"]] = r
    return [(by["A"][c], by["B"][c]) for c in sorted(set(by["A"]) & set(by["B"]))]


def judge(rs: list[dict]) -> int:
    a = [r for r in rs if r["arm"] == "A"]
    b = [r for r in rs if r["arm"] == "B"]
    leak = sum(1 for r in b if r["plan"])
    rate_a = (sum(1 for r in a if r["plan"]) / len(a)) if a else 0.0
    print(f"哨兵：S1 B 臂带规划的条数 = {leak}（必须 0）｜S2 A 臂规划产出率 "
          f"{sum(1 for r in a if r['plan'])}/{len(a)} = {100 * rate_a:.0f}%"
          f"（线 ≥{100 * PLAN_RATE_MIN:.0f}%）")
    if not a or not b:
        print(f"判读：A {len(a)} 条 / B {len(b)} 条 ⇒ 有一臂整条没产出，这不叫对照（RC=2）")
        return 2
    if leak:
        print(f"判读：S1 红（B 臂仍有 {leak} 条带规划）⇒ 打桩没咬到，读数无效："
              "先去查产品是不是换了规划入口，别拿这份数据下任何结论（RC=2）")
        return 2
    if rate_a < PLAN_RATE_MIN:
        print(f"判读：S2 红（A 臂规划产出率 {100 * rate_a:.0f}% < {100 * PLAN_RATE_MIN:.0f}%）⇒ "
              "这批料本身不产生规划，A/B 差里量不出规划层（RC=2，换料再来）")
        return 2
    prs = paired(rs)
    for x, y in prs:
        print(f"  · case {x['case']}：A 违规 {x['viol_pre']} vs B 违规 {y['viol_pre']}"
              f"｜A 卡 {x['cards']}/B 卡 {y['cards']}")
    for arm, rr in (("A", a), ("B", b)):
        med = sorted(r["post_len"] for r in rr)[len(rr) // 2]
        wc = [r for r in rr if r["cards"]]
        print(f"P2 代价·臂 {arm}：{len(rr)} 条｜模型调用合计 {calls[arm]}｜长度中位 {med}｜"
              f"带卡 {len(wc)} 条｜接地 {sum(r['grounded'] for r in wc)}/{sum(r['cited'] for r in wc)}")
    if len(prs) < PAIRS_MIN:
        print(f"判读：成对 {len(prs)} <{PAIRS_MIN} ⇒ **未测**，只印逐对原行，不出显著性结论（RC=3）")
        return 3
    # 副读（**不参与判定**）：旧 P1 留着，是为了让 g100 与这一轮能并排读懂
    ea, eb, tie, _ = sign_test([(x["viol_pre"], y["viol_pre"]) for x, y in prs])
    print(f"副读·闸门前违规（g100 的旧判定量）：A 更少 {ea}｜B 更少 {eb}｜平局 {tie} ⇒ 本轮不用它判")
    # P1''（轮 122 补记选定，唯一决策量）：**每百字接地句数** = 100×grounded/长度，只取两臂都带卡的对。
    # 为什么不是预登记里写的"接地率 grounded/cited"：`exp_denominator_gradient.py` 拿 g100 现成数据体检过——
    # 带卡对里 A 6/9、B 8/9 条接地率恰好 1.0，12 对只剩 2 对不等，**根本没有能显著的样本量**；
    # 除以长度之后同一批数据 0 平局。这一步只看了"有没有梯度/平局多少"，没看谁赢（挑尺子不是判案）。
    usable: list[tuple[float, float, dict, dict]] = []
    dropped: list[str] = []
    for x, y in prs:
        if not (x["cards"] and y["cards"] and x["post_len"] and y["post_len"]):
            dropped.append(f"case {x['case']}")
            continue
        usable.append((100.0 * x["grounded"] / x["post_len"], 100.0 * y["grounded"] / y["post_len"], x, y))
    print(f"P1'' 每百字接地句数：可用 {len(usable)} 对（下限 {MIN_USABLE}）"
          f"｜剔掉 {len(dropped)} 对（有臂零卡或零长度）：{'、'.join(dropped) or '无'}")
    if len(usable) < MIN_USABLE:
        print(f"判读：可用对 {len(usable)} <{MIN_USABLE} ⇒ **未测**——"
              "带卡子集太小不等于「规划层没用」，是这台仪器在这批料上看不见（RC=3）")
        return 3
    lo_a, lo_b, tie2, p = sign_test([(u0, u1) for u0, u1, _, _ in usable])
    better_a, better_b = lo_b, lo_a          # 密度越高越好：A 值更大 = A 更好（sign_test 数的是"更小"）
    print(f"P1''：A 更高 {better_a}｜B 更高 {better_b}｜平局 {tie2}｜不等 {lo_a + lo_b} 对"
          + (f"｜双侧精确 p={p:.4f}" if p is not None else "｜两臂逐对相同 ⇒ 不出 p 值（是没测到差别，不是证明了没差别）"))
    m = lo_a + lo_b
    if p is not None and m:
        conc = sum(1 for u0, u1, x, y in usable if u0 != u1 and ((u0 > u1) == (x["post_len"] > y["post_len"])))
        print(f"长度对照：{conc}/{m} = {100 * conc / m:.0f}% 的对里「密度更高」与「更长」同侧")
        if 100 * conc / m >= 100 * LEN_CONC_MAX:
            print("判读：密度差与「谁更长」几乎同向 ⇒ 这条读数**不能**归因给规划层（更可能是长度差），"
                  "按预登记记未决（RC=3）")
            return 3
    if better_a > better_b and p is not None and p < 0.05:
        print(f"判读：规划层**提高了每百字接地句数**（{better_a} vs {better_b}，p={p:.4f}）⇒ 保留它，"
              f"代价一并写进文档（A 臂合计 {calls['A']} 次调用 vs B 臂 {calls['B']} 次）")
        return 0
    if better_b > better_a and p is not None and p < 0.05:
        print(f"判读：规划层在**这一维上更低**（{better_b} vs {better_a}，p={p:.4f}）⇒ 撤它的候选成立，"
              "但只否证『每百字接地句数』这一个维度，不许外推到延迟/合规")
        return 0
    print(f"判读：判不出显著（{better_a} vs {better_b}"
          + (f"，p={p:.4f}" if p is not None else "，无不等对")
          + "）⇒ **未决**；不许读成『规划层没用』，也不许读成『有用』——这一维还没有证据（RC=3）")
    return 3


def resolve_cases(arg: str) -> str:
    for cand in (arg, f"/tmp/{os.path.basename(arg)}", str(_HERE.parent / os.path.basename(arg))):
        if cand and pathlib.Path(cand).is_file():
            return cand
    return ""


def load_cases(path: str) -> list[str]:
    try:
        data = json.loads(pathlib.Path(path).read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        print(f"用例池 {path} 读不动（{type(exc).__name__}）⇒ 不出数")
        return []
    seen: set[str] = set()
    out: list[str] = []
    for row in data:
        t = (row.get("user_text") or "").strip()
        if len(t) >= 8 and t not in seen:
            seen.add(t)
            out.append(t)
    return out


def selfcheck() -> int:
    ch: list[tuple[str, bool, str]] = []

    def chk(name, ok, note=""):
        ch.append((name, bool(ok), note))

    ea, eb, tie, p = sign_test([(1, 3), (0, 2), (2, 2), (1, 4), (0, 1)])
    # 4 对不等全 A 好 + 1 平局 ⇒ 双侧精确 p = 2·C(4,0)/2⁴ = 0.125（不是 p<0.1；上一版期望写错，被自己抓住）
    chk("A 符号检验基本算术", (ea, eb, tie) == (4, 0, 1) and p is not None and abs(p - 0.125) < 1e-9,
        f"{ea}/{eb}/tie={tie} p={p}")
    ea, eb, tie, p = sign_test([(2, 2), (0, 0)])
    chk("B 无不等对 ⇒ p=None（不许印 p=1.0000）", p is None and (ea, eb, tie) == (0, 0, 2), f"p={p}")
    ea, eb, tie, p = sign_test([(1, 2), (2, 1)])
    chk("C 完全对半分 ⇒ p 不小", p is not None and p > 0.5, f"p={p:.4f}")
    ea, eb, tie, p = sign_test([(0, 1)] * 5)
    chk("D 5 对全 A 好 ⇒ p=0.0625", p is not None and abs(p - 0.0625) < 1e-9, f"p={p}")

    import contextlib
    import io

    def row(arm, viol, case, plan=False, cards=3, cited=2, grounded=2, post_len=400):
        return dict(arm=arm, case=case, viol_pre=viol, viol_post=0, plan=plan, cards=cards,
                    fired=[], post_len=post_len, cited=cited, grounded=grounded)

    def run(rs):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = judge(rs)
        return rc, buf.getvalue()

    rc, out = run([x for c in range(1, 7) for x in (row("A", 1, c, plan=True), row("B", 3, c, plan=True))])
    chk("E S1：B 臂仍带规划 ⇒ 读数无效 RC=2", rc == 2 and "打桩没咬到" in out, f"RC={rc}")
    rc, out = run([x for c in range(1, 7) for x in (row("A", 1, c), row("B", 3, c))])
    chk("F S2：A 臂产出率 0 ⇒ RC=2（换料）", rc == 2 and "不产生规划" in out, f"RC={rc}")
    # 密度口径（P1''）下，"谁赢"由每百字接地句数决定 ⇒ 夹具必须同时钉住长度，
    # 否则 H/I 会被长度对照误判（这正是 O/P 两条要分开钉的原因）。
    rc, out = run([x for c in range(1, 21) for x in (row("A", 1, c, plan=True), row("B", 1, c))])
    chk("G 两臂逐对相同 ⇒ RC=3 且印「不出 p 值」", rc == 3 and "不出 p 值" in out, f"RC={rc}")
    rc, out = run([x for c in range(1, 21) for x in (
        row("A", 0, c, plan=True, grounded=4, cited=4, post_len=400),
        row("B", 2, c, grounded=1, cited=1, post_len=800))])
    chk("H A 每百字接地更多（且更短）⇒ 判「提高了每百字接地句数」",
        rc == 0 and "提高了每百字接地句数" in out, f"RC={rc}")
    rc, out = run([x for c in range(1, 21) for x in (
        row("A", 0, c, plan=True, grounded=1, cited=1, post_len=800),
        row("B", 0, c, grounded=4, cited=4, post_len=400))])
    chk("I B 更高 ⇒ 判「这一维更低」且写明不许外推", rc == 0 and "不许外推" in out, f"RC={rc}")
    rc, out = run([row("A", 0, c, plan=True) for c in range(1, 13)]
                  + [row("B", 2, c) for c in range(1, 4)])   # A 有 12 条，B 只回来 3 条 ⇒ 只 3 对
    chk("J 成对不足 10 ⇒ 未测（不许拿 3 对定论）",
        rc == 3 and "成对 3 <10" in out, f"RC={rc}")
    # K：B 行**整段倒序**入表（不是交错）、且 case 7 单独给一个值
    #   ⇒ 只有"按 case 配对"才可能印出 case 7 那一行；按到达顺序配对会把 A7 配到 B14（红在这里）。
    rc, out = run([row("A", 0, c, plan=True, grounded=4, cited=4, post_len=400) for c in range(1, 21)]
                  + [row("B", 5 if c == 7 else 1, c, grounded=1, cited=1, post_len=800)
                     for c in range(20, 0, -1)])
    chk("K 配对按 case 不靠顺序（B 倒序也配得对）",
        rc == 0 and "case 7：A 违规 0 vs B 违规 5" in out, f"RC={rc}")
    # O：可用对被剔到下限之下 ⇒ 只许记「未测」，不许记"规划层没用"（P1'' 独有的失效模式）
    rc, out = run([x for c in range(1, 21) for x in (
        row("A", 0, c, plan=True, grounded=4, cited=4, post_len=400),
        row("B", 0, c, grounded=1, cited=1, post_len=800, cards=0 if c <= 6 else 1))])
    chk("O 可用对被剔到 <16 ⇒ 未测（不是没差别）",
        rc == 3 and "未测" in out and "剔掉 6 对" in out, f"RC={rc}")
    # P：密度差与长度**完全同向** ⇒ 拒绝归因给规划层（轮 73/77 的胜率就是被长度反向驱动的）
    rc, out = run([x for c in range(1, 21) for x in (
        row("A", 0, c, plan=True, grounded=4, cited=4, post_len=900),
        row("B", 0, c, grounded=1, cited=1, post_len=400))])
    chk("P 密度与长度同向 100% ⇒ 拒绝归因、记未决",
        rc == 3 and "归因给规划层" in out and "100%" in out, f"RC={rc}")
    from companion.literature import evidence
    real = evidence.grounding_report("这套方法在低照度下仍然有效[1]。",
                                     [{"title": "低照度成像方法研究",
                                       "abstract": "这套方法在低照度下仍然有效" * 4}])
    chk("L 键名与不变量（直接调产品函数）",
        {CITE_CITED, CITE_GROUNDED} <= set(real) and real[CITE_GROUNDED] <= real[CITE_CITED],
        f"实得 {sorted(real)}")
    stub_b(True)
    both = mentor.make_plan is not _make_plan and mentor.plan_prefix is not _plan_prefix
    stubbed = mentor.make_plan(user_text="x", intent=None, history=[], papers=[])
    stub_b(False)
    # M：两条规划产出路径必须**一起**掐（只掐 make_plan 会漏掉线上流式那条 ⇒ 幻影对照）
    chk("M 两条规划路径一起掐掉", both and stubbed is None, f"both={both} stubbed={stubbed}")
    chk("N 打桩还得回（不 del 模块属性，否则还原成「这个函数不存在」）",
        mentor.make_plan is _make_plan and mentor.plan_prefix is _plan_prefix, "")
    bad = [n for n, ok, _ in ch if not ok]
    for name, ok, note in ch:
        print(f"[自检 {name}] {'✅' if ok else '❌'}　{note}")
    print(f"判读：{len(ch) - len(bad)}/{len(ch)} 项通过"
          + ("" if not bad else f"｜❌ 红在 {bad}"))
    return 1 if bad else 0


def main() -> int:
    global ARM
    if SELFCHECK:
        return selfcheck()
    if getattr(llm, "LLM_MOCK", False):
        print("判读：LLM_MOCK=1 ⇒ 两臂都是模板话，量不出规划层的贡献，不出数（RC=3）")
        return 3
    if "--cases" in sys.argv and not CASES_ARG:
        print("判读：--cases 后面没有值（多半被 runner 当成待上传文件吃掉了）⇒ 不退回内置情景，"
              "请传容器内路径 --cases /tmp/g98_cite_pool.json（RC=2）")
        return 2
    if not CASES_ARG:
        print("判读：没带 --cases ⇒ 拒绝出数（本探针不内置情景，免得把合成料读成线上料，RC=2）")
        return 2
    got = resolve_cases(CASES_ARG)
    if not got:
        print(f"判读：用例池 {CASES_ARG} 找不到 ⇒ 不出数（RC=2）")
        return 2
    cases = load_cases(got)[:N]
    if len(cases) < PAIRS_MIN:
        print(f"判读：池子里可用提问只有 {len(cases)} 个（<{PAIRS_MIN}）⇒ 分母不够，不出数（RC=3）")
        return 3
    from companion.conversation import dialogue_manager as dm
    print(f"用例池：去重后取 {len(cases)} 对（每对 A/B 各 1 条 = {2 * len(cases)} 条回复上限）"
          f"｜比对键集 {len(KEYS)} 道闸门", flush=True)
    for i, say in enumerate(cases, 1):
        for arm in ("A", "B"):
            ARM = arm
            SLOT.clear()
            SLOT["case"] = i
            stub_b(arm == "B")
            try:
                dm.handle_message(say, None)
            except Exception as exc:  # noqa: BLE001 — 单条抖动只丢这一条，不打死几小时的跑
                print(f"[!] case {i} 臂 {arm} 抛错被跳过：{type(exc).__name__} {str(exc)[:80]}",
                      flush=True)
                skipped.append(f"{arm}{i}")
            if SLOT:
                print(f"[!] case {i} 臂 {arm} 没走到 repair（记为失败，不进分子分母）", flush=True)
                skipped.append(f"{arm}{i}（没走到 repair）")
            SLOT.clear()
        stub_b(False)
    stub_b(False)
    if skipped:
        print(f"⚠️ 跳过 {len(skipped)} 条：{', '.join(skipped[:12])}")
    print(f"合计：{len(rows)} 条回复｜模型调用 A {calls['A']} + B {calls['B']}"
          f" = {calls['A'] + calls['B']} 次")
    return judge(rows)


if __name__ == "__main__":
    sys.exit(main())
