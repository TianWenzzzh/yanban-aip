"""#25 起跑前的**供给体检**：只读、零模型调用，回答「今天这批池子撑不撑得起一场会出判定的长跑」。

为什么要这一步（轮 131 的前科）：同一份 A/B 探针跑了三小时，回来是「可用对 8 <16 ⇒ 未测（RC=3）」，
逐对原行显示一半对里有一臂零卡——根因不是规划层，是当天 provider 熔断（arXiv HTTP 406、
Semantic Scholar 429）。供给是**随时间抖的**（同一主题熔断状态一变，料就从 2 条变 1 条），
隔几天再盲跑一次大概率只是再花三小时换一个同样的「未测」⇒ 先用几分钟量供给，够才起跑。

判据（跑前写死）：
  C1 起跑算术（在**任何检索之前**做）：池子按 `probe_plan_ab.load_cases` 去重、按 N 截断后的条数
     送进 `probe_plan_ab.pool_shortfall` —— 与长跑启动时**同一个函数、同一份字节**。它拒绝就原样
     印它那句话并 RC=3。上一版体检自己钉着 `N_CASES = 24`、阈值由启动方抠成命令行参数，
     两头都会与长跑漂移（实际起跑用的是外部提问池、N=70）。
  C0 仪器可信：控制查询（一条注定有料的英文主题）首尾各跑一次，任一次 0 篇 ⇒ RC=2 ——
     这一批用例的 0 不能读成「这个主题没料」。首尾之差同时就是这轮供给漂移的读数。
  C2 供给上界：单臂 `cards ≥ 1` 的条数记作 hit。可用对要求**两臂都带卡** ⊆ 单臂带卡
     ⇒ 可用对 ≤ hit（并集界，不假设两臂独立）。hit < MIN_USABLE ⇒ RC=3「今天的供给连上界都不够」。
  观察量（不参与判定）：单臂带卡率 hit/条数、逐 provider 卡数合计、首尾漂移。
     ⚠️ 单臂率与 `USABLE_RATE`（沙箱实测的**两臂都带卡**率）是两个口径，不许互相换算，
     也不许印成「凑得出多少对可用」——上一版印的就是那句。

阈值不抄第二份：`MIN_USABLE / USABLE_RATE / POOL_MIN / load_cases / pool_shortfall` 用 ast 从
`probe_plan_ab.py` 的**源码**里抠（只取这几个顶层节点，**不 import 整个脚本**——它在 import 期就
`sys.stdout = _Tee(...)`、按 `sys.argv` 里的数字反推 N、还真调一次 `mentor.violations`，
一起 import 会把体检日志拐进长跑边车、并把体检的命令行当成长跑的参数）。
抠不全（改名或删函数）⇒ RC=2，并印所读字节的 sha256，让记录能对上当时读的是哪一份代码。

跑法（容器内；🔴 必须等 #25 长跑**结束之后**——体检本身要打几十次检索，会扰动 provider 熔断状态，
而熔断状态一变，长跑的供给读数就换了料）：
  PYTHONUTF8=1 uv run --no-project --python 3.12 --with paramiko \
    MSYS_NO_PATHCONV=1 python /e/AIC智能体迁移包/输出/scripts/run_cloud_probe.py \
    /e/AIC智能体迁移包/输出/scripts/probe_supply_precheck.py \
    <本地 pool.json> /tmp/pool.json /tmp/probe_plan_ab.py <N> \
    /e/AIC智能体迁移包/输出/scripts/sandbox_data.py
  位置参数 1 = 池子（容器内路径；本地那份由 runner 一起上传）；
  位置参数 2 = 闸门文件：用容器 /tmp 里那份**已经在跑长跑的**字节，别再上传本地副本去覆盖它——
    覆盖之后再「从容器取字节冻结仪器」就钉了一份从没跑过的代码，这个坑已经踩过三次。
    要体检工作副本，就等长跑结束后先重新同步再跑。
本地自检（不碰容器、不碰检索，只验「抠阈值」这根管子会不会咬人）：
  PYTHONUTF8=1 python 输出/scripts/probe_supply_precheck.py --selfcheck
本机查分母预算（C1 那一步，零检索；后面对几个 N 就印几行）：
  PYTHONUTF8=1 python 输出/scripts/probe_supply_precheck.py --budget <pool.json> \
      输出/scripts/probe_plan_ab.py 70 24 18
"""
from __future__ import annotations

import ast
import hashlib
import json
import math
import pathlib
import sys
import tempfile
import time

WANT = {"MIN_USABLE", "USABLE_RATE", "POOL_MIN", "load_cases", "pool_shortfall"}
CONTROL = "large language model multi-agent collaboration survey"


def load_gate(path: str) -> tuple[dict, str]:
    """从 probe_plan_ab.py 的源码里抠出阈值与两个函数 ⇒ (命名空间, sha256 前 12 位)。"""
    raw = pathlib.Path(path).read_bytes()
    keep = []
    for node in ast.parse(raw.decode("utf-8"), filename=path).body:
        if isinstance(node, ast.Assign) and any(
                isinstance(t, ast.Name) and t.id in WANT for t in node.targets):
            keep.append(node)
        elif isinstance(node, ast.FunctionDef) and node.name in WANT:
            keep.append(node)
    ns: dict = {"json": json, "math": math, "pathlib": pathlib}
    mod = ast.fix_missing_locations(ast.Module(body=keep, type_ignores=[]))
    exec(compile(mod, path, "exec"), ns)  # noqa: S102 读的是本仓自己的仪器源码，不是外部输入
    missing = sorted(WANT - set(ns))
    if missing:
        raise KeyError(f"{path} 里抠不到 {missing}（改名或删了？阈值不同源就不能作数）")
    return ns, hashlib.sha256(raw).hexdigest()[:12]


def selfcheck() -> int:
    ch: list[tuple[str, bool, str]] = []

    def chk(name, ok, note=""):
        ch.append((name, bool(ok), note))

    src = pathlib.Path(__file__).with_name("probe_plan_ab.py")
    ns, digest = load_gate(str(src))
    chk("G1 抠全 5 个名字并印字节指纹", len(digest) == 12, digest)
    chk("G2 起跑算术会拒绝小池子（否则 C1 是恒真）",
        "RC=3" in ns["pool_shortfall"](18), f"18 对 ⇒ 预计 {18 * ns['USABLE_RATE']:.1f} 对")
    chk("G3 起跑算术会放行大池子（否则 G2 是恒假）",
        ns["pool_shortfall"](ns["POOL_MIN"]) == "", f"POOL_MIN={ns['POOL_MIN']}")
    with tempfile.TemporaryDirectory() as d:
        pool = pathlib.Path(d) / "pool.json"
        pool.write_text(json.dumps([{"user_text": "abcdefgh"}, {"user_text": "abcdefgh"},
                                    {"user_text": "太短"}, {"user_text": ""}]), encoding="utf-8")
        chk("G4 池子解析与长跑同一个 load_cases（去重＋剔短＋剔空）",
            ns["load_cases"](str(pool)) == ["abcdefgh"], f"4 行 ⇒ {ns['load_cases'](str(pool))}")
        bad = pathlib.Path(d) / "mangled.py"
        bad.write_text(src.read_text(encoding="utf-8").replace(
            "def pool_shortfall(", "def renamed_gate("), encoding="utf-8")
        try:
            load_gate(str(bad))
            chk("G5 闸门函数被改名时必须炸（负对照）", False, "没炸 ⇒ 这根管子恒绿")
        except KeyError as exc:
            chk("G5 闸门函数被改名时必须炸（负对照）", "pool_shortfall" in str(exc), str(exc)[:66])
    for name, ok, note in ch:
        print(f"[{'OK  ' if ok else 'FAIL'}] {name}" + (f"｜{note}" if note else ""))
    red = [n for n, ok, _ in ch if not ok]
    print(f"判读：本地自检 {len(ch)} 项 ⇒ "
          + ("全过（RC=0）" if not red else f"红在 {red}（RC=2）"))
    return 0 if not red else 2


def main() -> int:
    if "--selfcheck" in sys.argv:
        return selfcheck()
    budget = "--budget" in sys.argv
    pos = [a for a in sys.argv[1:] if not a.startswith("-")]
    if len(pos) < 2:
        print(__doc__)
        return 2
    pool_path, gate_path = pos[0], pos[1]
    if not gate_path.endswith(".py"):
        print(f"判读：第 2 个位置参数要的是**闸门文件路径**，拿到的是 {gate_path}"
              "⇒ 阈值已改为从 probe_plan_ab.py 现读，旧的 <MIN_USABLE> 命令行作废（RC=2）")
        return 2
    try:
        gate, digest = load_gate(gate_path)
    except (OSError, SyntaxError, KeyError, ValueError) as exc:
        print(f"判读：从 {gate_path} 抠阈值失败（{type(exc).__name__}：{str(exc)[:90]}）"
              "⇒ 体检与长跑不同源，这份结论不作数（RC=2）")
        return 2

    n = next((int(a) for a in pos if a.isdigit()), 0)
    cases_all = gate["load_cases"](pool_path)
    cases = cases_all[:n] if n else cases_all
    print(f"闸门字节 sha256[:12]={digest}｜MIN_USABLE={gate['MIN_USABLE']} "
          f"两臂带卡率={100 * gate['USABLE_RATE']:.0f}% POOL_MIN={gate['POOL_MIN']}"
          f"｜去重后可用提问 {len(cases_all)} 条，本轮取前 {len(cases)} 条（与 A/B 同序）")

    if budget:      # 只查分母预算，零检索零模型调用 ⇒ 本机就能跑，不起容器
        for k in [int(a) for a in pos[2:] if a.isdigit()] or [len(cases_all)]:
            short = gate["pool_shortfall"](k)
            print(f"池子取 {k:3d} 对｜预计可用 {k * gate['USABLE_RATE']:5.1f} 对 ⇒ "
                  + ("放行" if not short else short.split("⇒")[-1].strip()))
        return 0

    if (short := gate["pool_shortfall"](len(cases))):
        print(short, flush=True)
        return 3
    print(f"C1 起跑算术：{len(cases)} 对 × 历史两臂带卡率 {100 * gate['USABLE_RATE']:.0f}% "
          f"= 预计可用 {len(cases) * gate['USABLE_RATE']:.1f} 对 ≥{gate['MIN_USABLE']} ⇒ 放行，接着量今天的供给")

    sys.path[:0] = ["/app", "/tmp", "."]
    import sandbox_data                                   # noqa: E402 由启动方一起 docker cp 到 /tmp
    from companion.intent.model import ResearchIntent      # noqa: E402
    from companion.literature import engine                # noqa: E402

    def one(topic: str) -> tuple[int, dict, int]:
        t0 = time.time()
        papers = engine.search_for_intent(ResearchIntent(project_id="supply_precheck", topic=topic),
                                          limit=8)       # 与 dialogue_manager.py:217 同一个 limit
        src: dict[str, int] = {}
        for p in papers:
            k = str(p.get("source") or "?")
            src[k] = src.get(k, 0) + 1
        return len(papers), src, int((time.time() - t0) * 1000)

    marks = sandbox_data.isolate("/tmp/probe_supply_precheck")
    print(f"沙箱已接管落点：{list(marks)}")
    n0, s0, ms0 = one(CONTROL)
    print(f"C0 控制查询（首）：{n0} 篇｜{s0}｜{ms0}ms")
    hit = 0
    by_src: dict[str, int] = {}
    for i, q in enumerate(cases, 1):
        cnt, src, ms = one(q[:120])
        hit += cnt >= 1
        for k, v in src.items():
            by_src[k] = by_src.get(k, 0) + v
        print(f"case {i:3d}｜卡 {cnt}｜{src}｜{ms}ms｜{q[:26]}", flush=True)
    n1, s1, ms1 = one(CONTROL)
    print(f"C0 控制查询（尾）：{n1} 篇｜{s1}｜{ms1}ms")

    if not n0 or not n1:
        print(f"判读：控制查询首 {n0} 篇 / 尾 {n1} 篇 ⇒ **仪器或 provider 不可信**，"
              f"这 {len(cases)} 条里的 0 不能读成「这个主题没料」（RC=2）")
        return 2
    drift = "供给在这轮里没掉" if n1 >= n0 else f"供给在这轮里掉了 {n0}→{n1} 篇"
    obs = (f"单臂带卡 {hit}/{len(cases)}（{100 * hit / len(cases):.0f}%，**上界口径**：可用对要两臂都带卡"
           f"⇒ 至多 {hit} 对）｜按历史两臂率预计 {len(cases) * gate['USABLE_RATE']:.1f} 对｜{drift}"
           f"｜provider 卡数合计 {by_src}")
    if hit < gate["MIN_USABLE"]:
        print(f"判读：{obs} ⇒ 今天的供给连**上界**都够不到 MIN_USABLE={gate['MIN_USABLE']}，"
              "别押三小时（RC=3）")
        return 3
    print(f"判读：{obs} ⇒ 起跑算术放行、今日供给上界 ≥{gate['MIN_USABLE']} ⇒ 可以起 #25 的长跑；"
          "但「够不够出判定」以长跑自己那行判读为准，本体检不替它作数（RC=0）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
