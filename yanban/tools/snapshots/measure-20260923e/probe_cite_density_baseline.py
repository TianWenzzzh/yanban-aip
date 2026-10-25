"""引用密度基线：不看评委，只数「有论文卡可引却整条不挂角标」的回复有多少。

为什么要这一台：g92 量到「带角标句 12/12 全接得住」，但那把尺的分子只数**已经挂了角标的句子**——
它答不了"该挂没挂"。而线上真实缺口恰好在这一维（平均 1.2 句/回复、4/10 条零角标）。
不先证明这一维**有梯度**就写闸门，等于给一个恒真的检查加绿。

判据口径（与产品同一套：`llm.extract_cited_indices` 的 [n]/【n】/《论文》）：
  密度 = 带角标句数 / 回复；零引用 = 该回复里一个引用痕迹都没有。
分母分两种，必须分开印，否则"池子空"会被算成"模型偷懒"：
  · 全部回复；
  · 只数**当轮论文卡非空**的回复（`papers` 长度 > 0）。

用法：python 输出/scripts/probe_cite_density_baseline.py [fixture.json ...]
不带参数时自动扫 docs/evidence/g92_citegap_fixture.json（现场夹具，离线重放，不烧云端）。
"""
from __future__ import annotations

import json
import pathlib
import re
import sys

CITE = re.compile(r"\[\d{1,2}\]|【\d{1,2}】|《")
SENT = re.compile(r"(?<=[。！？!?\n])")


def cited_sentences(text: str) -> int:
    n = 0
    for raw in SENT.split(text or ""):
        s = raw.strip()
        if len(s) >= 8 and re.search(r"\[\d{1,2}\]", s):
            n += 1
    return n


def one(rows: list[dict], tag: str) -> None:
    with_pool = [r for r in rows if r.get("papers")]
    print(f"\n=== {tag}｜夹具 {len(rows)} 条（其中有论文卡 {len(with_pool)} 条）===")
    for label, sub in (("全部回复", rows), ("只看有论文卡的", with_pool)):
        if not sub:
            print(f"{label}：0 条 —— 没测，不许印 0%")
            continue
        zero = sum(1 for r in sub if not CITE.search(r.get("reply") or ""))
        cnt = [cited_sentences(r.get("reply") or "") for r in sub]
        print(f"{label} n={len(sub)}｜零引用 {zero} = {zero / len(sub):.0%}"
              f"｜带角标句合计 {sum(cnt)}，平均 {sum(cnt) / len(sub):.2f} 条/回复"
              f"｜逐条 {cnt}")
    print("每条回复的论文卡数：", [len(r.get("papers") or []) for r in rows])


def main() -> int:
    paths = [pathlib.Path(a) for a in sys.argv[1:]]
    if not paths:
        default = pathlib.Path(__file__).resolve().parents[2] / "code" / "yanban" / \
            "docs" / "evidence" / "g92_citegap_fixture.json"
        paths = [default]
    for p in paths:
        if not p.is_file():
            print(f"缺夹具 {p}", file=sys.stderr)
            return 2
        one(json.loads(p.read_text(encoding="utf-8")), p.name)
    print("\n判读：只有「有论文卡却零引用」这一列才是闸门的靶子；"
          "池子为空是取数面问题，别拿措辞闸门去治。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
