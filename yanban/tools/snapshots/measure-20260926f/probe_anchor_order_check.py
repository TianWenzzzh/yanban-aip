"""判据不是"编号看着对"而是"换个编号会不会掉分"：线上报告的依据池，顺序是不是真的（零模型调用）。

起因：事项一把 `renumber_citations` 接进报告路径后，`g160` 那个「改前 85.1% / 改后 92.0%」
全靠**按报告自己「## 引用论文」的顺序重建依据池**。如果这个顺序与正文 [n] 的真实编号错位，
那把尺会把对的读成错的、也会把错的读成对的，+6.9pp 就是垃圾数。
"同意率"这条路试过走不通：这 12 份报告里**没有一句**是「《标题》+ 单角标」的形态
（`probe_anchor_order_check` 首版量到可判句 0/12），所以只能换成对**判别力**的直接检验。

做法：同一份正文、同一批论文，只把论文的**顺序**整体轮转一格（`pool[1:]+pool[:1]`），
再用同一把 `grounding_report` 复算。顺序真 ⇒ 轮转后"被引那篇撑得住本句"的比率必须明显掉；
轮转后不掉 ⇒ 要么我这台仪器取错了池子，要么这把尺根本不区分"引的是哪一篇"
（后者更糟：它意味着历史所有 coverage 读数都只测了"有没有字面重合"，不是"引用对不对"）。

判据（跑前写死）：
  A1 结构底线：正文里出现的每个角标都必须 ≤ 该报告「引用论文」的条数；越界 ⇒ 报数并 RC=2
     （越界编号本该被 `strip_dangling_citations` 剪掉，出现在落盘正文里就是产品缺陷）。
  A2 主判据：**对位接地率 ≥ 2 × 轮转接地率**，且对位分母 ≥20 句 ⇒ 顺序同源成立（RC=0）。
  A3 分母不足（带角标句 <20）⇒ RC=3 只作定性，不印倍数（拿 6 句定"≥2 倍"是条证伪不了的线）。
  A4 轮转后不掉（<2 倍）⇒ RC=2：这条改动的前后数作废，且**历史所有 coverage 读数的口径**要重写。
  A5 只读自证：DB (mtime,size) 与 KB .md 数逐位不变；`mode=ro` 连不上直接崩（不写不猜）。
"""
from __future__ import annotations

import json
import pathlib
import re
import sqlite3
import sys

sys.path.insert(0, "/app")
_HERE = pathlib.Path(__file__).resolve()
for _cand in ["/app", str(pathlib.Path.cwd()), *map(str, _HERE.parents)]:
    if (pathlib.Path(_cand) / "companion").is_dir():
        sys.path.insert(0, _cand)
        break
else:
    print("判读：找不到含 companion/ 的仓根 ⇒ 仪器没连上被测物（RC=2）")
    sys.exit(2)

from companion.literature import evidence        # noqa: E402

KB = pathlib.Path("/app/data/knowledge")
DB = pathlib.Path("/app/data/yanban.db")
REPORTS_MAX = int(next((a for a in sys.argv[1:] if a.isdigit()), 12))
CITE_RE = re.compile(r"\[\[(?:论文)[:：](.+?)\]\]")
MIN_CITED = 20
RATIO_LINE = 2.0


def db_papers(pid: str) -> dict[str, dict]:
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    try:
        rows = con.execute(
            "SELECT paper_json FROM papers WHERE project_id=? ORDER BY created_at DESC",
            (pid,)).fetchall()
    finally:
        con.close()
    out: dict[str, dict] = {}
    for (raw,) in rows:
        try:
            p = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if isinstance(p, dict) and p.get("title") and p["title"] not in out:
            out[p["title"]] = p
    return out


def main() -> int:
    if not KB.is_dir():
        print(f"判读：KB 目录 {KB} 不存在（RC=2）")
        return 2
    st0 = DB.stat()
    kb0 = sum(1 for _ in KB.rglob("*.md"))
    rows = []
    for d in sorted((p for p in KB.iterdir() if p.is_dir()), key=lambda p: -p.stat().st_mtime):
        for f in sorted(d.glob("*.md")):
            text = f.read_text(encoding="utf-8", errors="replace")
            if "## 引用论文" in text:
                rows.append((d.name, f.name, text))
                break
        if len(rows) >= REPORTS_MAX:
            break
    print(f"扫到带「引用论文」节的报告 {len(rows)} 份")
    if not rows:
        print("判读：一份都没扫到 ⇒ 路径或判据坏了（RC=2）")
        return 2

    cited = ground_al = ground_rot = 0
    over: list[str] = []
    per = []
    for pid, name, text in rows:
        block = text[text.rfind("## 引用论文"):]
        links = [t.strip() for t in CITE_RE.findall(block)]
        body = text[:text.rfind("## 引用论文")]
        by_title = db_papers(pid)
        pool = [by_title[t] for t in links if t in by_title]
        if len(pool) < 2 or len(pool) != len(links):
            print(f"  跳过 {pid[:10]} {name[:20]}：链接 {len(links)} 条、回查到原文 {len(pool)} 篇")
            continue
        for m in re.finditer(r"\[(\d{1,2})\]", body):
            if int(m.group(1)) > len(links):
                over.append(f"{pid[:10]}｜{name[:18]}｜[{m.group(1)}] > 引用清单 {len(links)} 条")
        rot = pool[1:] + pool[:1]
        a = evidence.grounding_report(body, pool)
        b = evidence.grounding_report(body, rot)
        cited += a["cited_sentences"]
        ground_al += a["grounded"]
        ground_rot += b["grounded"]
        per.append((pid[:10], name[:18], len(pool), a["cited_sentences"],
                    a["grounded"], b["grounded"]))

    st1 = DB.stat()
    kb1 = sum(1 for _ in KB.rglob("*.md"))
    print("\n逐份（pid｜文件｜池｜带角标句｜对位接地｜轮转接地）：")
    for r in per:
        print("  " + "｜".join(str(x) for x in r))
    print(f"\nA5 只读自证：DB {(st0.st_mtime_ns, st0.st_size)} → {(st1.st_mtime_ns, st1.st_size)}"
          f"｜KB .md {kb0} → {kb1}")
    if (st0.st_mtime_ns, st0.st_size) != (st1.st_mtime_ns, st1.st_size) or kb0 != kb1:
        print("判读：这台仪器不该有任何写操作 ⇒ 读数作废（RC=2）")
        return 2
    print(f"A1 越界角标 {len(over)} 处" + ("".join("\n  🔴 " + o for o in over[:6]) if over else ""))
    if over:
        print("判读：落盘正文里存在越界编号 ⇒ 那是产品缺陷，本判据的前提交不成立（RC=2）")
        return 2
    if cited < MIN_CITED:
        print(f"判读：带角标句 {cited} <{MIN_CITED} ⇒ 只作定性，不印倍数（RC=3）")
        return 3
    ra, rb = ground_al / cited, ground_rot / cited
    ratio = ra / rb if rb else float("inf")
    print(f"A2 对位接地率 {100 * ra:.1f}%（{ground_al}/{cited}）"
          f"｜轮转后 {100 * rb:.1f}%（{ground_rot}/{cited}）｜倍数 {ratio:.2f}"
          + ("（轮转后为 0 ⇒ 尺子完全靠编号，倍数记为 ∞）" if not rb else ""))
    if ratio >= RATIO_LINE:
        print(f"判读：≥{RATIO_LINE} 倍 ⇒ 「## 引用论文」的顺序与正文 [n] 同源，"
              "g160 那台仪器的依据池重建成立，接地尺也确有判别力（RC=0）")
        return 0
    print(f"判读：轮转后只掉到 {100 * rb:.1f}%（<{RATIO_LINE} 倍）⇒ 要么池子顺序取错了，"
          "要么这把尺不区分「引的是哪一篇」。g160 的 +6.9pp 与历史所有 coverage 读数都要重看（RC=2）")
    return 2


if __name__ == "__main__":
    sys.exit(main())
