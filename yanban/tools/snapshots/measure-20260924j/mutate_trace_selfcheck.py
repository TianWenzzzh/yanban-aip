"""probe_trace_vs_count 的「改坏必须红」守卫（离线，零模型调用）。

为什么单独一份：轮 117 学到的规矩——**自检自己也会假绿**。这台仪器有 19 项夹具，
但夹具只证明"当前这份代码在样例上是对的"；要证明夹具真在看着那些判据，得把每条判据
改坏一次，看它红、且红在该抓的那一项（红错条目＝等于没红）。

跑法：PYTHONUTF8=1 code/yanban/.venv/Scripts/python.exe 输出/scripts/mutate_trace_selfcheck.py
"""
from __future__ import annotations

import os
import pathlib
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]
PROBE = ROOT / "输出" / "scripts" / "probe_trace_vs_count.py"
YANBAN = ROOT / "code" / "yanban"
PY = YANBAN / ".venv" / "Scripts" / "python.exe"

# (名字, 原文锚点, 改成什么, 期望红在哪一项前缀)
MUTATIONS = [
    ("M1 归因两类互换", 'return "cn_form" if any(COUNTED(s) for s in pieces) else "short_piece"',
     'return "short_piece" if any(COUNTED(s) for s in pieces) else "cn_form"', "[自检 I"),
    ("M2 计数非 0 也拿去归因", 'if cited:\n        return "other"',
     'if cited > 5:\n        return "other"', "[自检 K"),
    ("M3 分句门槛从 8 降到 5", "len(s) >= 8", "len(s) >= 5", "[自检 J"),
    ("M4 删掉分母下限护栏", 'if st["sections"] < MIN_SECTIONS:', "if False:", "[自检 M"),
    ("M5 删掉 T3 负控制", 'if st["cited_total"] == 0 or st["sections"] == 0:', "if False:", "[自检 O"),
    ("M6 删掉结构不变量", 'if sum(st["why"].values()) != st["gap"]:', "if False:", "[自检 S"),
    ("M7 第三类不再作废读数", 'if st["why"]["other"]:', "if False:", "[自检 R"),
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
    tmp = pathlib.Path(os.environ.get("TEMP", "/tmp")) / "mutated_probe_trace_vs_count.py"
    rc0, out0 = run(PROBE)
    print(f"未变异的探针 --selfcheck → RC={rc0}｜" + out0.strip().splitlines()[-1])
    if rc0 != 0:
        print("基线就是红的，先修它（变异测试无从谈起）")
        return 2
    bad = 0
    for name, a, b, want in MUTATIONS:
        if orig.count(a) != 1:
            print(f"{name} → ❌ 锚点出现 {orig.count(a)} 次（源改过了？变异根本没打上）")
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
    print(f"判读：{len(MUTATIONS)} 条变异" + ("全部被抓到 ⇒ 这 19 项夹具真在看着这些判据。" if not bad
                                          else f"有 {bad} 条没抓到 ⇒ 夹具覆盖不足，别拿它的绿当保险。"))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
