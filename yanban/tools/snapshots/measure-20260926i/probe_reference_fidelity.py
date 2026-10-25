"""B：报告末尾「引用论文」里列出的论文，有多少**一次都没被挂过角标**（零模型调用、只读）。

为什么先量这个再谈收窄：`report.MAX_PAPERS` 决定一份综述末尾列多少篇参考文献。
列得多 → 读者看到一堆没被用过的来源；列得少 → 把"读者该知道有这篇"也一起砍掉。
这个取舍上一轮只登记成"结构事实"，没量过负担有多大——**没有负担数字就讨论阈值等于猜**。

🔴 这条读数只回答"列出的东西有没有被挂上"，**不回答"正文的话对不对"**（后者由接地尺与 `g164`
的轮转判别力管）。收窄产物长度的效果必须另做 A/B；本场只出分母与负担。

判据（跑前写死，跑完不许改）：
  F1 主读数 = 合并「被列出却一次都没挂角标」/「被列出」（所有报告加总，不是逐份取平均）。
     「挂过」= 正文里出现过 [n]，n 是该篇在清单里的序号。
  F2 分母两侧都写：报告数、被列出论文总数、其中被挂过的、每份列出数中位。
     被列出总数 <40 ⇒ RC=3 只作定性（拿 8 篇去卡 10%/25% 的线＝写了条证伪不了的判据）。
  F3 与产品对账：`evidence.grounding_report(正文, 清单)["cited_sentences"]` 是我解析出的标记数的**下界**
     （一句可挂多个标记）⇒ 必须 `标记数 ≥ 带角标句数`，反了说明我解析漏了角标（RC=2）。
  F4 只读自证：DB 的 (mtime,size) 与 KB 里 .md 数跑前跑后逐位相同；`mode=ro` 连不上直接崩（RC=2）。
  F5 植入（判据必须会动）：`--plant-hung` 强制"全挂" ⇒ 负担率必须 0.0%；
     `--plant-none` 强制"全不挂" ⇒ 负担率必须 100.0%。两种植入都不改数字 ⇒ 计数器是装饰（RC=2）。
  F6 决策线（按 F1 合并率）：≤10% ⇒ **不收窄**，阈值维持现值（RC=0）；
     ≥25% ⇒ **收窄候选成立**，另开一场量"短报告掉不掉质量"（RC=1）；10%–25% ⇒ 记未决（RC=1）。
  F7 分层如实报：给了 `--since <epoch>` 就按 mtime 分「之后 / 之前」两桶各印份数；
     「之后」为 0 时，结论只属于**之前**那份分布，必须这么写，不许假装分层成立。
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

from companion.literature import evidence          # noqa: E402

KB = pathlib.Path("/app/data/knowledge")
DB = pathlib.Path("/app/data/yanban.db")
REPORTS_MAX = int(next((a for a in sys.argv[1:] if a.isdigit()), 40))
MIN_LISTED = 40
LOW_LINE, HIGH_LINE = 10.0, 25.0
PLANT = "hung" if "--plant-hung" in sys.argv else ("none" if "--plant-none" in sys.argv else "")
SINCE = int(sys.argv[sys.argv.index("--since") + 1]) if "--since" in sys.argv else 0
CITE_RE = re.compile(r"\[\[(?:论文)[:：](.+?)\]\]")
MARK_RE = re.compile(r"\[(\d{1,2})\]")


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


def reports() -> list[dict]:
    """[{pid, name, mtime, listed[标题按序], body}]；body 已剔掉程序拼的「引用论文」节。"""
    out: list[dict] = []
    dirs = sorted((d for d in KB.iterdir() if d.is_dir()), key=lambda p: -p.stat().st_mtime) \
        if KB.is_dir() else []
    for d in dirs:
        for f in sorted(d.glob("*.md")):
            text = f.read_text(encoding="utf-8", errors="replace")
            if "## 引用论文" not in text:
                continue
            cut = text.rfind("## 引用论文")
            listed = [t.strip() for t in CITE_RE.findall(text[cut:])]
            if listed:
                out.append({"pid": d.name, "name": f.name, "mtime": f.stat().st_mtime,
                            "listed": listed, "body": text[:cut]})
            break
        if len(out) >= REPORTS_MAX:
            break
    return out


def burden(rs: list[dict]) -> tuple[float, int, int, list[tuple[int, int]]]:
    """(合并负担率%，被列出总数，被挂过总数，逐份 (列出, 挂过))。"""
    total = hung = 0
    per = []
    for r in rs:
        n = len(r["listed"])
        used = len({int(m) for m in MARK_RE.findall(r["body"])} & set(range(1, n + 1)))
        if PLANT == "hung":
            used = n
        elif PLANT == "none":
            used = 0
        total += n
        hung += used
        per.append((n, used))
    return (100.0 * (total - hung) / total if total else 0.0), total, hung, per


def main() -> int:
    if not KB.is_dir():
        print(f"判读：KB 目录 {KB} 不存在 ⇒ 扫错了地方（RC=2）")
        return 2
    st0 = DB.stat()
    kb0 = sum(1 for _ in KB.rglob("*.md"))
    rs = reports()
    plant = {"hung": "｜🔴 植入：强制全挂", "none": "｜🔴 植入：强制全不挂"}.get(PLANT, "")
    print(f"扫到带「引用论文」节的报告 {len(rs)} 份（上限 {REPORTS_MAX}）{plant}")
    if not rs:
        print("判读：一份都没扫到 ⇒ 判据或路径坏了（RC=2）")
        return 2

    marker_total = cited_total = unres = 0
    for r in rs:
        by_title = db_papers(r["pid"])
        pool = [by_title[t] for t in r["listed"] if t in by_title]
        unres += len(r["listed"]) - len(pool)
        marker_total += len(MARK_RE.findall(r["body"]))
        if pool:
            cited_total += evidence.grounding_report(r["body"], pool)["cited_sentences"]

    st1 = DB.stat()
    kb1 = sum(1 for _ in KB.rglob("*.md"))
    rate, total, hung, per = burden(rs)
    listed_med = sorted(x[0] for x in per)[len(per) // 2]
    print("\n逐份（报告｜列出｜被挂过｜一次没挂）：")
    for r, (n, h) in zip(rs, per):
        print(f"  {r['name'][:34]}｜{n}｜{h}｜{n - h}")
    print(f"\nF4 只读自证：DB (mtime,size) {st0} → {st1}｜KB .md {kb0} → {kb1}")
    if (st0.st_mtime_ns, st0.st_size) != (st1.st_mtime_ns, st1.st_size) or kb0 != kb1:
        print("判读：这台仪器不该有任何写操作 ⇒ 读数作废（RC=2）")
        return 2
    print(f"F3 对账：解析出的角标标记 {marker_total} 处 vs 产品尺子认得的带角标句 {cited_total} 句"
          f"（要求标记 ≥ 句）")
    if marker_total < cited_total:
        print("判读：标记数 < 带角标句数 ⇒ 我漏解析了角标，负担率不作数（RC=2）")
        return 2
    if unres:
        print(f"  （清单标题回查不到原文的条目 {unres} 条：不影响 F1，只影响顺手算支撑的那一路）")
    if SINCE:
        after = sum(1 for r in rs if r["mtime"] >= SINCE)
        print(f"F7 分层（切点 epoch={SINCE}）：上线之后 {after} 份｜之前 {len(rs) - after} 份"
              + ("｜🔴 「之后」为 0 ⇒ 本场结论只属于上线**前**那份分布" if not after else ""))
    print(f"\nF1/F2 合并负担率 {rate:.1f}%（被列出 {total} 篇，其中被挂过 {hung} 篇）"
          f"｜报告 {len(rs)} 份｜每份列出中位 {listed_med}")
    if total < MIN_LISTED:
        print(f"判读：被列出总数 {total} <{MIN_LISTED} ⇒ 只作定性，不印决策（RC=3）")
        return 3
    if PLANT == "hung":
        print("判读：" + ("植入（强制全挂）后负担率 0.0% ⇒ 计数器跟着动（RC=2，本场是负控制）"
                         if abs(rate) < 1e-9 else
                         f"植入（强制全挂）后负担率仍 {rate:.1f}% ⇒ 计数器不认植入，是装饰（RC=2）"))
        return 2
    if PLANT == "none":
        print("判读：" + ("植入（强制全不挂）后负担率 100.0% ⇒ 计数器会红（RC=2，本场是负控制）"
                         if abs(rate - 100.0) < 1e-9 else
                         f"植入（强制全不挂）后负担率只有 {rate:.1f}% ⇒ 计数器坏了（RC=2）"))
        return 2
    if rate <= LOW_LINE:
        print(f"判读：负担率 {rate:.1f}% ≤{LOW_LINE:.0f}% ⇒ **不收窄**："
              "「列出但不挂」不是这套产物的主要毛病，`MAX_PAPERS` 维持现值（RC=0）")
        return 0
    print(f"判读：负担率 {rate:.1f}% ≥{HIGH_LINE:.0f}%（或落在 10–25% 的未决带）⇒ "
          "阈值这事**不能只凭本场数字定**：要改就得另开一场量「短报告掉不掉质量」（RC=1）")
    return 1


if __name__ == "__main__":
    sys.exit(main())
