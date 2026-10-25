"""#25 零卡的归因：读产品**自己写下的**检索诊断，定位卡在哪一层被丢掉（零模型调用、只读）。

现场：同一份外部提问池、同一个容器、同一天——
  供给体检（绕过产品，直接 `search_for_intent(topic=提问, limit=8)`）判 单臂带卡 69/70；
  A/B（走 `dm.handle_message`）判 140 条回复 卡 0。
两个读数都真，差别只在中间那段产品代码。所以不猜，读 `run_search` 无条件落库的那几项
（`companion/conversation/dialogue_manager.py` 的 `_diagnose`：`search_skipped` /
`search_engine_returned` / `search_stored` / `search_error`）。

判读（跑前写死，三选一，互斥）：
  D1 最新时段以 `skipped=clarifying` 为主 ⇒ **反问轮不检索**（产品按设计反问）；A/B 每条都是
     一个新项目的首问 ⇒ 全部落在反问轮，零卡是路径性质，不是供给、也不是规划层。
  D2 以 `returned=0` 为主 ⇒ 检索真跑了但拿回 0 篇 ⇒ 与体检的 69/70 矛盾 ⇒ 去看意图抽取后的
     topic 被改写成什么（脚本会印样例），瓶颈在意图层。
  D3 以「无诊断」为主 ⇒ 流式入口根本没走到 `run_search` ⇒ 是 A/B 这台仪器没把产品跑到检索那步，
     读数作废，与产品无关。
只读方式：`sqlite3 file:...?mode=ro`；取不到库或零行 ⇒ RC=2，不把"读不到"当成 0。
"""
from __future__ import annotations

import collections
import json
import pathlib
import sqlite3
import sys

DB_DEFAULT = "/tmp/probe_sandbox_plan_ab/yanban.db"


def main() -> int:
    db = pathlib.Path(sys.argv[1] if len(sys.argv) > 1 else DB_DEFAULT)
    if not db.is_file():
        print(f"判读：沙箱库 {db} 不存在 ⇒ 那份读数没落在这里，先确认 SANDBOX_ROOT（RC=2）")
        return 2
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=5)
    try:
        rows = con.execute(
            "SELECT i.project_id, i.intent_json, substr(p.created_at,1,13) AS hr "
            "FROM research_intents i LEFT JOIN projects p ON p.id=i.project_id").fetchall()
    except sqlite3.Error as exc:
        print(f"判读：读不动（{type(exc).__name__} {str(exc)[:80]}）⇒ 不出结论（RC=2）")
        return 2
    print(f"库 {db}｜intent 行 {len(rows)}")
    if not rows:
        print("判读：零行 ⇒ 这台仪器没东西可读，别把空表读成「没有跳过检索」（RC=2）")
        return 2

    by_hr: dict[str, collections.Counter] = collections.defaultdict(collections.Counter)
    miss: dict[str, list] = collections.defaultdict(list)
    topics: dict[str, list] = collections.defaultdict(list)
    for pid, js, hr in rows:
        try:
            d = json.loads(js)
        except (TypeError, ValueError):
            by_hr[hr or "?"]["intent_json 坏"] += 1
            continue
        ex = d.get("extra") or {}
        if "search_skipped" in ex:
            key = f"skipped={ex['search_skipped']}"
            miss[key].append((ex.get("search_missing") or [])[:3])
        elif "search_engine_returned" in ex:
            n = ex["search_engine_returned"]
            key = f"returned={n}" if n else "returned=0"
        else:
            key = "无诊断"
        by_hr[hr or "?"][key] += 1
        topics[hr or "?"].append(str(d.get("topic") or "")[:40])

    for hr in sorted(by_hr):
        tot = sum(by_hr[hr].values())
        top = by_hr[hr].most_common()
        print(f"{hr}（{tot} 条）｜" + "｜".join(f"{k}×{v}" for k, v in top))
        if by_hr[hr].get("skipped=clarifying") and topics[hr]:
            print(f"    该时段前 3 条抽取后的 topic：{topics[hr][:3]}")

    last = max((h for h in by_hr if h != "?"), key=lambda h: (sum(by_hr[h].values()), h))
    cnt = by_hr[last]
    n_skipped = cnt.get("skipped=clarifying", 0)
    n_zero = sum(v for k, v in cnt.items() if k == "returned=0")
    n_none = cnt.get("无诊断", 0)
    tot = sum(cnt.values())
    line = f"最新时段 {last} 共 {tot} 条：clarifying {n_skipped}、returned=0 {n_zero}、无诊断 {n_none}"
    if n_skipped and n_skipped >= n_zero and n_skipped >= n_none:
        print(f"判读：D1 ⇒ {line} ⇒ 零卡的直接机制是**反问轮不检索**（产品按设计反问，"
              "而 A/B 每条都是新项目首问）。P1'' 在这批料上没有分母，与规划层无关（RC=0）")
        return 0
    if n_zero and n_zero >= n_none:
        print(f"判读：D2 ⇒ {line} ⇒ 检索跑了但拿回 0 篇，与体检的 69/70 矛盾 ⇒ "
              "下一步查意图抽取后的 topic（上面已印样例）（RC=0）")
        return 0
    print(f"判读：D3 ⇒ {line} ⇒ 这些回复根本没经过 `run_search` ⇒ "
          "A/B 那 140 条的「卡 0」是仪器没跑到产品那一步，读数作废（RC=3）")
    return 3


if __name__ == "__main__":
    sys.exit(main())
