"""量一件事：现有四台配对读数脚本的「分母口径」在真实转储上到底差多少。

动机（先测量再改闸门）：`read_lcarm.py` / `analyze_blind_pairs.py` / `lc_winrate.py` /
`probe_citation_effect.py` 都从同一份 PAIRS_JSON 里算「胜率」，但对**平局**与**评委没判成
（outcome 缺键或空串）**这两类行的取法各不相同：
  · read_lcarm        ：平局/非胜都算进分母（=判负），空串行被 `and r[key]` 剔掉
  · analyze_blind_pairs：胜率按 wins/(wins+losses) 剔平局，但同一行的 bootstrap 把平局记 0
  · lc_winrate        ：`"2.0" in outcome`，分母 = 全部行 ⇒ 空串行被算成判负
  · probe_citation_effect：同 lc_winrate
哪个是错的？没有先验——但**同一批数据在四台脚本上印出的胜率必须是同一个数**，
否则日志里「39% / 56%」这类数根本没说自己是哪把尺量的。本脚本不修任何东西，只把
四种口径并排算出来，看差值有没有大到需要统一。

用法：python 输出/scripts/exp_tie_convention.py <转储文件> [...]
"""
from __future__ import annotations

import json
import pathlib
import sys

MARKER = "=== PAIRS_JSON ==="
WIN = "2.0 胜"


def load(path: pathlib.Path) -> list[dict]:
    txt = path.read_text(encoding="utf-8", errors="replace")
    if MARKER not in txt:
        return []
    line = txt.split(MARKER, 1)[1].strip().splitlines()[0]
    try:
        return json.loads(line)
    except json.JSONDecodeError:
        return []


def classify(rows: list[dict], key: str) -> dict:
    """四类互斥：win / loss / tie / none（评委没判成）。tie 靠标签集合认，不靠"非胜"。"""
    out = {"win": 0, "loss": 0, "tie": 0, "none": 0}
    for r in rows:
        v = str(r.get(key, ""))
        if not v:
            out["none"] += 1
        elif v == WIN:
            out["win"] += 1
        elif v.endswith("胜"):
            out["loss"] += 1
        else:
            out["tie"] += 1
    return out


def pct(k: int, n: int) -> str:
    return "—" if not n else f"{100.0 * k / n:.1f}%"


def row(label: str, c: dict) -> None:
    decided = c["win"] + c["loss"]
    print(f"  {label:<16} 行 {sum(c.values()):>3}｜胜 {c['win']:>3} 负 {c['loss']:>3} "
          f"平 {c['tie']:>3} 未判 {c['none']:>3}  "
          f"⇒ 剔平局 {pct(c['win'], decided):>6}｜含平局 {pct(c['win'], decided + c['tie']):>6}｜"
          f"lc_winrate 口径 {pct(c['win'], sum(c.values())):>6}")


def selfcheck() -> int:
    """手算样例：4 胜 2 负 1 平 1 未判 ⇒ 三个率必须分别等于手算值。"""
    fx = ([{"o": WIN}] * 4 + [{"o": "裸问胜"}] * 2 + [{"o": "相当"}] + [{"o": ""}])
    c = classify(fx, "o")
    got = (c["win"], c["loss"], c["tie"], c["none"])
    ok = got == (4, 2, 1, 1)
    r1, r2, r3 = pct(4, 6), pct(4, 7), pct(4, 8)
    ok &= r1 == "66.7%" and r2 == "57.1%" and r3 == "50.0%"
    print(f"[自检] 手算样例 4 胜 2 负 1 平 1 未判 ⇒ 计数 {got}｜三率 {r1} / {r2} / {r3}"
          f"（期望 (4,2,1,1)｜66.7% / 57.1% / 50.0%）" + (" ✅" if ok else " ❌"))
    return 0 if ok else 2


def main() -> int:
    sc = selfcheck()
    if sc:
        print("判读：仪器自检没过 ⇒ 不出数（RC=2）")
        return sc
    args = [a for a in sys.argv[1:] if not a.startswith("-")]
    if not args:
        print(__doc__)
        return 2
    worst = 0.0
    for a in args:
        p = pathlib.Path(a)
        if not p.is_file():
            print(f"  {p.name}：文件不存在 ⇒ 跳过（不当作 0 行）")
            continue
        rows = load(p)
        print(f"\n===== {p.name}（PAIRS_JSON 行 {len(rows)}）=====")
        if not rows:
            print("  没有 PAIRS_JSON 转储 ⇒ 这一份不参与结论")
            continue
        for key in ("outcome", "strong_outcome"):
            if not any(key in r for r in rows):
                print(f"  {key}：这一份没有该键 ⇒ 不比对（不是 0 行）")
                continue
            c = classify(rows, key)
            row(key, c)
            decided = c["win"] + c["loss"]
            if decided and sum(c.values()):
                worst = max(worst, abs(100.0 * c["win"] / decided
                                       - 100.0 * c["win"] / sum(c.values())))
    print(f"\n最大口径差 = {worst:.1f} 个百分点")
    print("判读：差 0 ⇒ 四把尺在这批数据上同数，不必统一（但空行仍在被某几台当判负，"
          "只是本批恰好没有）；差 >0 ⇒ 同一批数据印过不同胜率，"
          "日志里的百分数必须补写口径。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
