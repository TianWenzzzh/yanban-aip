"""probe_plan_ab 的「改坏必须红」守卫（离线，零模型调用）。

轮 117 立的规矩：自检自己也会假绿——13/14 项过只说明"当前这份代码在样例上对"，
要证明这些样例真在看着判据，得把每条判据改坏一次，看它红、**且红在该抓的那一项**。

跑法：PYTHONUTF8=1 code/yanban/.venv/Scripts/python.exe 输出/scripts/mutate_plan_ab_selfcheck.py
"""
from __future__ import annotations

import os
import pathlib
import subprocess
import sys

def _root() -> pathlib.Path:
    """往上找那个**装着 code/yanban 的**目录，而不是固定层数。

    为什么不写死 parents[2]：这份守卫会被冻进 `code/yanban/tools/snapshots/measure-*/`，
    从那里数两层数到 `code/yanban/tools` 去了，解释器就"不存在"、守卫 RC=2 ——
    一份只能待在工作副本旁边的守卫，验不了快照（冻结当场实测到的）。"""
    here = pathlib.Path(__file__).resolve()
    for anc in here.parents:
        if (anc / "code" / "yanban").is_dir():
            return anc
    return here.parents[2]


ROOT = _root()
# 默认打工作副本，但快照目录里那一份才是产出读数的字节（工作副本在跑完后还会漂）。
# 为什么必须能换目标：钉进仓的是快照份，若守卫只能打工作副本，则"8/8 抓到"这句话
# 对快照份从来没成立过 —— 那正是 measure-20260923d 记的同一种假保险。
_cli = [sys.argv[i + 1] for i, v in enumerate(sys.argv) if v == "--probe"]
PROBE = pathlib.Path(_cli[0]).resolve() if _cli else ROOT / "输出" / "scripts" / "probe_plan_ab.py"
YANBAN = ROOT / "code" / "yanban"
PY = YANBAN / ".venv" / "Scripts" / "python.exe"

MUTATIONS = [
    ("M1 平局算成 A 更好（分母被灌水）", "a = sum(1 for x, y in pairs if x < y)",
     "a = sum(1 for x, y in pairs if x <= y)", "[自检 A"),
    ("M2 双侧检验忘了乘 2", "p = min(1.0, 2.0 * sum(", "p = min(1.0, 1.0 * sum(", "[自检 D"),
    ("M3 无不等对印成 p=1.0（会被读成『证明了没差别』）",
     "return a, b, len(pairs), None", "return a, b, len(pairs), 1.0", "[自检 B"),
    ("M4 删掉 S1 打桩哨兵", "if leak:", "if False and leak:", "[自检 E"),
    ("M5 删掉 S2 对照哨兵", "if rate_a < PLAN_RATE_MIN:", "if False:", "[自检 F"),
    ("M6 配对退回按到达顺序（有跳过就错位）",
     'return [(by["A"][c], by["B"][c]) for c in sorted(set(by["A"]) & set(by["B"]))]',
     'return list(zip([r for r in rows if r["arm"] == "A"], '
     '[r for r in rows if r["arm"] == "B"]))', "[自检 K"),
    ("M7 只掐 make_plan、漏掉流式那条（幻影对照）",
     "        mentor.plan_prefix = no_prefix", "        pass  # 故意漏", "[自检 M"),
    ("M8 删掉成对数下限护栏", "if len(prs) < PAIRS_MIN:", "if False:", "[自检 J"),
]


def run(path: pathlib.Path) -> tuple[int, str]:
    env = {**os.environ, "PYTHONUTF8": "1", "LLM_MOCK": "1"}
    r = subprocess.run([str(PY), str(path), "--selfcheck"], capture_output=True, text=True,
                       encoding="utf-8", errors="replace", cwd=str(YANBAN), env=env)
    return r.returncode, r.stdout + r.stderr


def main() -> int:
    if not PROBE.is_file() or not PY.is_file():
        print(f"仪器不工作：探针存在={PROBE.is_file()}／解释器存在={PY.is_file()}")
        return 2
    orig = PROBE.read_text(encoding="utf-8")
    tmp = pathlib.Path(os.environ.get("TEMP", "/tmp")) / "mutated_probe_plan_ab.py"
    rc0, out0 = run(PROBE)
    print(f"未变异的探针 --selfcheck → RC={rc0}｜" + out0.strip().splitlines()[-1])
    if rc0 != 0:
        print("基线就是红的，先修它（变异测试无从谈起）")
        return 2
    bad = 0
    for name, a, b, want in MUTATIONS:
        if orig.count(a) != 1:
            print(f"{name} → ❌ 锚点出现 {orig.count(a)} 次（源改过了？变异没打上）")
            bad += 1
            continue
        tmp.write_text(orig.replace(a, b, 1), encoding="utf-8")
        rc, out = run(tmp)
        red = [ln.strip() for ln in out.splitlines() if ln.startswith("[自检") and "❌" in ln]
        right = any(ln.startswith(want) for ln in red)
        ok = rc == 1 and red and right
        bad += not ok
        print(f"{name} → RC={rc}｜红在：{'; '.join(red) or '（一处都没红）'}｜"
              + (f"✅ {want}" if ok else f"❌ 假绿或红错条目（期望 {want}）"))
    tmp.unlink(missing_ok=True)
    print(f"判读：{len(MUTATIONS)} 条变异" + ("全部被抓到 ⇒ 这些夹具真在看着对应判据。" if not bad
                                          else f"有 {bad} 条没抓到 ⇒ 覆盖不足，别拿它的绿当保险。"))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
