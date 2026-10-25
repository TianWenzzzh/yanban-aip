"""配对读数脚本的活性探针：空分母不许冒充 0%，部分样本不许冒充完整读数。

为什么需要（轮 123 起、轮 124 扩）：`read_lcarm.py` 与 `analyze_blind_pairs.py` 都在真实证据文件上
当场崩过 —— g58（那份没有 strong_outcome）让 read_lcarm 在「合并」那一行 ZeroDivisionError，
g36（那代还没有 yanban_items 字段）让 analyze_blind_pairs 在结构指标处 KeyError。
两个都是**印完前半截才死**：长跑读数会被切成半份，而半份最容易被人当成整份引用。
轮 124 把「部分样本」这条判断抽进 `pairwise_note.py`，四台读数脚本共用一句措辞，
于是变异改成**就地改那个公共模块** ⇒ 一次改动必须让三台同时变红，才算它们真接在同一条线上。

用法：python 输出/scripts/guard_pairwise_readers.py
退出码：0=全抓到；1=有判据失效；2=仪器自身跑不了（解释器/文件缺失）。
"""
from __future__ import annotations

import hashlib
import os
import pathlib
import subprocess
import sys

_HERE = pathlib.Path(__file__).resolve()


def _root() -> pathlib.Path:
    for p in _HERE.parents:
        if (p / "code" / "yanban").is_dir():
            return p
    return _HERE.parents[1]


ROOT = _root()
SCRIPTS = _HERE.parent
EVID = ROOT / "code" / "yanban" / "docs" / "evidence"
PY = ROOT / "code" / "yanban" / ".venv" / "Scripts" / "python.exe"
G59_LOGGED = ("39%", "29%", "34%", "56%", "65%", "60%")   # 日志里引用过的六个数
PN_TEXT = "不得当作那份预登记的完整结果"                   # 公共措辞，改它=改三台


def run(script: str, arg: str) -> tuple[int, str]:
    env = {**os.environ, "PYTHONUTF8": "1"}
    r = subprocess.run([str(PY), str(SCRIPTS / script), arg], capture_output=True,
                       text=True, encoding="utf-8", errors="replace", env=env,
                       cwd=str(ROOT))
    return r.returncode, (r.stdout or "") + (r.stderr or "")


FIXTURES = {
    "nodecision": '[{"outcome": "", "strong_outcome": "", "yanban": "甲", "strong": "乙",'
                  ' "plain": "丙", "say": "s1"}]\n',
    "onerepeat": '[{"outcome": "2.0 胜", "strong_outcome": "2.0 胜", "yanban": "甲",'
                 ' "strong": "乙", "plain": "丙", "say": "s1"},'
                 ' {"outcome": "裸问胜", "strong_outcome": "强基线胜", "yanban": "甲",'
                 ' "strong": "乙", "plain": "丙", "say": "s2"}]\n',
    "emptypairs": "=== PAIRS_JSON ===\n[]\n",
}


def fixture(name: str) -> str:
    p = SCRIPTS / f"_fx_{name}.txt"
    p.write_bytes(FIXTURES[name].encode("utf-8"))
    return str(p)


def resolve(arg: str) -> str:
    return fixture(arg[4:]) if arg.startswith("FIX:") else arg


G52 = str(EVID / "g52_blind_r42_2turn_n36.txt")

CHECKS = [
    ("A1 空分母说未测",
     ("read_lcarm.py", "FIX:nodecision"), (0,), ("未测（0 局判定）",), ("→ 胜率 0%", "= 0%")),
    ("A2 只有一份重复 ⇒ 翻转率未测",
     ("read_lcarm.py", "FIX:onerepeat"), (0,), ("翻转率未测",), ("Traceback",)),
    ("A3 真文件没这个键 ⇒ RC=2 而不是胜率为 0",
     ("read_lcarm.py", str(EVID / "g58_blind_r50_2turn_n36.txt")), (2,),
     ("什么都没读到", "不等于「胜率为 0」"), ("Traceback",)),
    ("A4 g59 回归：六个数一个不许动 + 它自己就是 35/36 的部分样本",
     ("read_lcarm.py", str(EVID / "g59_blind_lcarm_baseline_n36.txt")), (0,),
     (*G59_LOGGED, PN_TEXT), ("Traceback", "未测")),
    ("B1 旧转储缺字段 ⇒ 说不印，不印 0.00",
     ("analyze_blind_pairs.py", str(EVID / "g36_blind_before.txt")), (0,),
     ("没有 yanban_items 字段",), ("条目数 2.0=0.00", "Traceback")),
    ("B2 行数≠文件名 n ⇒ 自己喊部分样本",
     ("analyze_blind_pairs.py", G52), (0,), ("部分样本", PN_TEXT), ("Traceback",)),
    ("B3 0 行转储 ⇒ 未测而不是 0%",
     ("analyze_blind_pairs.py", "FIX:emptypairs"), (0,),
     ("未测（0 局判定）", "0 行转储"), ("胜率 0%", "Traceback")),
    ("C1 lc_winrate 也认部分样本（同一句措辞）",
     ("lc_winrate.py", G52), (0,), ("部分样本", PN_TEXT), ("Traceback",)),
    ("C2 probe_citation_effect 也认部分样本",
     ("probe_citation_effect.py", G52), (0,), ("部分样本", PN_TEXT), ("Traceback",)),
]

# (名字, 模式, 目标文件, 锚点, 打成什么, 必须因此变红的判据前缀)
MUTATIONS = [
    ("M1 空分母改回 max(1,n) 掩盖 ⇒ A1 必须红", "copy", "read_lcarm.py",
     'return "未测（0 局判定）" if not n else f"{w / n:.0%}"',
     'return f"{w / max(1, n):.0%}"', ("A1",)),
    ("M2 拆掉「只有一份重复」的护栏 ⇒ A2 必须红", "copy", "read_lcarm.py",
     "if len(recs) < 2:", "if False:", ("A2",)),
    ("M3 把公共的部分样本判断哑火 ⇒ B2/C1/C2 三台同时红", "inplace", "pairwise_note.py",
     "    if n == rows:\n        return \"\"", "    if True:\n        return \"\"",
     ("B2", "C1", "C2")),
    ("M4 缺字段改成返回 0.00 ⇒ B1 必须红", "copy", "analyze_blind_pairs.py",
     "    if not have:", '    if not have:\n        return "0.00"', ("B1",)),
]


def judge(script: str, arg: str, want_rc, must, must_not) -> tuple[bool, str]:
    path = resolve(arg)
    if not pathlib.Path(path).is_file():
        return False, f"输入不存在：{path}"
    rc, out = run(script, path)
    why = []
    if rc not in want_rc:
        why.append(f"RC={rc}（期望 {'/'.join(map(str, want_rc))}）")
    miss = [s for s in must if s not in out]
    if miss:
        why.append("缺：" + "、".join(miss))
    hit = [s for s in must_not if s in out]
    if hit:
        why.append("竟出现：" + "、".join(hit))
    return (not why), ("RC=%d" % rc if not why else "；".join(why))


def red_for(prefixes, runner_script) -> list[str]:
    red = []
    for name, (_s, arg), want_rc, must, must_not in CHECKS:
        if name[:2] in prefixes:
            ok, _ = judge(runner_script(f"{_s}"), arg, want_rc, must, must_not)
            if not ok:
                red.append(name[:2])
    return red


def main() -> int:
    if not PY.is_file():
        print(f"仪器不工作：解释器不存在 {PY}")
        return 2
    fails = 0
    for name, (script, arg), want_rc, must, must_not in CHECKS:
        ok, why = judge(script, arg, want_rc, must, must_not)
        fails += not ok
        print(f"{name} → {'✅' if ok else '❌'} {why}")
    print(f"\n判据：{len(CHECKS)} 条，红 {fails} 条")

    bad_mut = 0
    for mname, mode, script, a, b, must_red in MUTATIONS:
        src = SCRIPTS / script
        orig_b = src.read_bytes()          # 字节级留存：还原必须一字不差（含换行风格）
        orig = orig_b.decode("utf-8")
        if orig.count(a) != 1:
            print(f"{mname} → ❌ 锚点出现 {orig.count(a)} 次（变异没打上）")
            bad_mut += 1
            continue
        if mode == "copy":
            tmp = SCRIPTS / f"_mut_{script}"
            tmp.write_bytes(orig.replace(a, b, 1).encode("utf-8"))
            red = red_for(must_red, lambda s: f"_mut_{s}")
            tmp.unlink(missing_ok=True)
            src_bytes_untouched = src.read_bytes() == orig_b
        else:                               # 公共模块只能就地改：调用方按名字 import，副本无效
            src.write_bytes(orig.replace(a, b, 1).encode("utf-8"))
            red = red_for(must_red, lambda s: s)
            src.write_bytes(orig_b)
            src_bytes_untouched = src.read_bytes() == orig_b
        intact = src_bytes_untouched and hashlib.sha256(src.read_bytes()).hexdigest() \
            == hashlib.sha256(orig_b).hexdigest()
        got = set(red) >= set(must_red) and intact
        bad_mut += not got
        print(f"{mname} → {'✅' if got else '❌'} 红在 {red or '（一处都没红＝假绿）'}"
              f"{'｜源文件还原后字节不一致' if not intact else ''}")
    print(f"变异：{len(MUTATIONS)} 条，没抓到 {bad_mut} 条")
    print("判读：" + ("判据全在咬、四条变异全被抓、被守卫文件字节还原一致 ⇒ 这几种态真有人看着。"
                    if not fails and not bad_mut else "有判据失效或变异没被抓 ⇒ 别拿本探针的绿当保险。"))
    return 1 if (fails or bad_mut) else 0


if __name__ == "__main__":
    sys.exit(main())
