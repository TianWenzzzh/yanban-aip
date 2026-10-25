"""离线判：**这批提问是不是我们写的**，以及够不够撑起 #25 那场 A/B。零网络、零模型调用。

三件事一起判，缺一不可：
1. **出处完整**——每条都要有 question_id / 永久链接（链接里必须含同一个 id）/ 作者 / 发布时间 / 许可证；
   CC BY-SA 要求署名，署名栏空着就是"我们抄了一段没有来源的话"。
2. **不是自家写的**——复用 `scan_title_origin.py` 的逐字扫描（同一台仪器，不另造一把尺子）。
   🔴 溯源池必须跳过被扫的语料文件本身，否则语料躺在 `docs/evidence/*.json` 里会被自己读成"自家情景"，
   0 命中永远拿不到（这条规矩来自轮 112 那次"用被审文档自证"的事故）。
3. **正对照**——把 `scan_title_origin.TITLES`（已知全是自家写死的情景）喂同一把尺子，
   必须 7/7 命中。没有这一步，"外部语料 0 命中"完全可能是"扫描器根本没在扫"。

需要的池子大小**从 `probe_plan_ab.py` 现读**（`ast` 解析 `MIN_USABLE`/`USABLE_RATE` 两个常量后算），
不在这里抄第二份 46——抄的那份不会跟着上游改。
"""
from __future__ import annotations

import ast
import json
import pathlib
import sys

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from scan_title_origin import find_hits          # noqa: E402  同一把尺子，不复制逻辑

CORPUS = HERE.parent / "data" / "external_questions.json"
POOL_OUT = HERE.parents[1] / "code" / "yanban" / "docs" / "evidence" / "g132_external_questions_pool.json"
PROBE = HERE / "probe_plan_ab.py"
MIN_LEN = 8                                      # 与 probe_plan_ab.load_cases 同一条门槛


def pool_min_from_probe(path: pathlib.Path) -> int:
    """从 A/B 仪器里现读 `POOL_MIN = ceil(MIN_USABLE / USABLE_RATE)`，读不到就抛错（不许拍脑袋给数）。"""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    consts: dict[str, ast.stmt] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and len(node.targets) == 1 \
                and isinstance(node.targets[0], ast.Name):
            consts[node.targets[0].id] = node.value

    def ev(n):
        if isinstance(n, ast.Constant):
            return n.value
        if isinstance(n, ast.BinOp) and isinstance(n.op, ast.Div):
            return ev(n.left) / ev(n.right)
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id == "min":
            return min(ev(a) for a in n.args)
        if isinstance(n, ast.Name) and n.id in consts:
            return ev(consts[n.id])
        raise ValueError(f"读不动的形状 {type(n).__name__}")

    import math
    return math.ceil(ev(consts["MIN_USABLE"]) / ev(consts["USABLE_RATE"]))


def check_provenance(rows: list[dict]) -> list[str]:
    bad = []
    for r in rows:
        qid, link = r.get("question_id"), r.get("link", "")
        site = r.get("site", "")
        probs = []
        if not qid:
            probs.append("无 question_id")
        if f"://{site}." not in link or f"/questions/{qid}" not in link:
            probs.append(f"链接与 id/站点不自洽（{link[:40]}）")
        if not r.get("owner_id") or not r.get("created_utc"):
            probs.append("缺作者或发布时间")
        if "BY" not in (r.get("license") or ""):
            probs.append("缺许可证")
        if len((r.get("user_text") or "").strip()) < MIN_LEN:
            probs.append(f"提问短于 {MIN_LEN} 字")
        if probs:
            bad.append(f"{qid}: {'、'.join(probs)}")
    return bad


def main() -> int:
    if not CORPUS.is_file():
        print(f"判读：语料文件不存在（{CORPUS}）⇒ 先跑 fetch_external_questions.py（RC=2）")
        return 2
    rows = json.loads(CORPUS.read_text(encoding="utf-8"))
    if not rows:
        print("判读：语料 0 条 ⇒ 不出数（RC=2）")
        return 2

    bad = check_provenance(rows)
    print(f"① 出处完整：{len(rows) - len(bad)}/{len(rows)} 条合格"
          + ("" if not bad else f"｜不合格 {bad[:3]}"))

    texts = sorted({r["user_text"] for r in rows})
    hits, n_files = find_hits(texts, skip=[POOL_OUT, CORPUS])
    self_hit = [t for t, w in hits.items() if w]
    print(f"② 逐字命中自家代码树（扫 {n_files} 个文件，已跳过被扫语料自身）："
          f"{len(self_hit)}/{len(texts)} 条命中"
          + (f"｜例：{self_hit[:2]}" if self_hit else ""))

    control, _ = find_hits(__import__("scan_title_origin").TITLES)
    c_hit = sum(1 for w in control.values() if w)
    print(f"③ 正对照（已知自家情景必须被同一把尺子抓到）：{c_hit}/{len(control)} 条命中")

    need = pool_min_from_probe(PROBE)
    print(f"④ 池子：不同提问 {len(texts)} 条（门槛 {need} 对，从 probe_plan_ab.py 现读）")

    problems = []
    if bad:
        problems.append(f"{len(bad)} 条出处不全")
    if self_hit:
        problems.append(f"{len(self_hit)} 条在自家代码里逐字命中")
    if c_hit != len(control):
        problems.append("正对照没全命中 ⇒ 这把尺子不可信，本轮 0 命中不算数")
    if len(texts) < need:
        problems.append(f"去重后 {len(texts)} < 门槛 {need}")
    if problems:
        print("判读：" + "；".join(problems) + f" ⇒ 这批语料不能顶替自家池（RC=3）")
        return 3

    POOL_OUT.parent.mkdir(parents=True, exist_ok=True)
    pool, seen = [], set()
    for r in sorted(rows, key=lambda x: -(x.get("score") or 0)):
        t = r["user_text"]
        if t in seen:
            continue
        seen.add(t)
        pool.append({"user_text": t, "question_id": r["question_id"], "site": r["site"],
                     "link": r["link"], "owner_name": r["owner_name"], "license": r["license"]})
    POOL_OUT.write_bytes(json.dumps(pool, ensure_ascii=False, indent=1).encode("utf-8"))
    print(f"判读：{len(pool)} 条外部提问通过三关（出处齐、自家树 0 命中、正对照 {c_hit}/{len(control)} "
          f"抓得到），池子 ≥{need} ⇒ 可以顶替 g98 自家池跑 #25（现场已存：{POOL_OUT.name}）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
