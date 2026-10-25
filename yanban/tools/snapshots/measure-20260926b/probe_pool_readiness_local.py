"""#25 的堵点到底卡在哪一步：外部提问池 206 条**逐条普查**（零模型调用、零网络）——槽位填到多少、放行几条。

轮 148 把壳词闸门修好之后，#25 剩下的堵点从"闸门语言形状"挪到了"这批句子里有没有槽位信息"，
但那只有一例做证据（`mice killing` 拿到 topic 之后 ready_for_search 仍是 False，confidence 0.231）。
一例不是率，所以这一轮把整池量一遍：每条走**产品自己的** `extract_update` → `apply_update` →
`compose_interview_reply` / `wants_results`，不重写任何一道判定。

判据（跑前写死）：
  Z0 认路哨兵：`dialogue_manager.prepare_context` 里那句合取必须逐字在且只在一处——
     形状变了就 RC=2（我复刻的就不再是线上那条判定了）。
  Z0b 零网络由机制保证：`llm.LLM_MOCK` 必须为真，且 httpx 的 post 装了雷；触发数必须 0。
     `PROBE_PLANT_TRIP=1` 主动点一次真请求，必须红在 Z0b（不点雷就分不清"零网络"与"雷是装饰"）。
  Z1 放行数 = 「compose 为 None 或 wants_results 为真」的条数，与长跑门槛 POOL_MIN（从
     probe_plan_ab 源码现读）比：≥ ⇒ #25 在外部池上有分母；< ⇒ 如实记未达，并给出 Z2/Z3 的成因分解。
  Z2 分解：`ready_for_search` 为真的条数、confidence 的分档直方图、缺得最多的 5 个槽位——
     用来回答"是差 topic 还是差整排槽位"，不拿这一维去顶 Z1 的班。
  Z3 控制：正对照取 g98 里线上真拿到过卡的那句中文（放行必须 True），
     负对照取一句明显该被反问的中文（放行必须 False）；两案等长同池口径，方向相反才算尺子分得开。
  收尾行以「判读：」开头；聚合只在跑完整份之后打印。
"""
from __future__ import annotations

import inspect
import json
import os
import pathlib
import sys
from collections import Counter

os.environ["LLM_MOCK"] = "1"      # 必须早于任何 `import llm`

ROOT = pathlib.Path(__file__).resolve().parents[2]
REPO = ROOT / "code" / "yanban"
EVD = REPO / "docs" / "evidence"
sys.path[:0] = [str(REPO), str(ROOT / "输出" / "scripts")]

POOL = EVD / "g132_external_questions_pool.json"
LABEL = "英语外部池 g132（轮 149 那份）"
# 轮 154：#26 的中文真人料池要用**同一组判据**，所以这里只换输入文件，不另写一台尺子
# （两把尺的口径差在轮 137 已量过线上 0/258 ⇒ 但那是"没发作"，不是"不会差"）。
for _i, _a in enumerate(sys.argv):
    if _a == "--pool" and _i + 1 < len(sys.argv):
        POOL = pathlib.Path(sys.argv[_i + 1])
    if _a == "--label" and _i + 1 < len(sys.argv):
        LABEL = sys.argv[_i + 1]
CONJUNCTION = "still_clarifying = compose_interview_reply(intent) is not None and not wants_results(user_text)"


class Trip:
    def __init__(self) -> None:
        self.net = 0

    def install(self) -> None:
        import httpx
        trip = self

        async def boom(*a, **k):
            trip.net += 1
            raise AssertionError("本仪器声称零网络，但有人真发了请求")

        httpx.AsyncClient.post = boom

    def knock(self) -> None:
        """活性探针：主动撞一次雷。不点一下，就分不清「零网络」与「雷是装饰」。"""
        import asyncio

        import httpx

        async def go():
            async with httpx.AsyncClient() as c:
                try:
                    await c.post("http://127.0.0.1:1/probe", json={})
                except Exception:  # noqa: BLE001 — 雷自己抛的也在这里吞
                    pass

        asyncio.run(go())


def gate_min() -> int:
    from probe_supply_precheck import load_gate
    ns, _ = load_gate(str(ROOT / "输出" / "scripts" / "probe_plan_ab.py"))
    return int(ns["POOL_MIN"])


def main() -> int:
    trip = Trip()
    trip.install()
    import llm
    from companion.conversation.interview_agent import compose_interview_reply, wants_results
    from companion.intent import extractor
    from companion.intent.clarification import ready_for_search
    from companion.intent.model import ResearchIntent

    src = (REPO / "companion/conversation/dialogue_manager.py").read_text(encoding="utf-8")
    if src.count(CONJUNCTION) != 1:
        print(f"Z0 认路哨兵：那句合取 {src.count(CONJUNCTION)} 处 ⇒ 复刻的形状已不是线上那份")
        print("判读：整池读数不作数，先改这条判据（RC=2）")
        return 2
    if not llm.LLM_MOCK:
        print("判读：LLM_MOCK 没生效 ⇒ 每条都会真发请求（轮 145 v1 就是这么烧掉 208 次的）（RC=2）")
        return 2
    print(f"Z0 认路哨兵：合取逐字在 prepare_context 里 1 处｜Z0b 模式：llm.LLM_MOCK={llm.LLM_MOCK}")
    thr = inspect.signature(ready_for_search).parameters["threshold"].default
    if not isinstance(thr, (int, float)):
        print(f"判读：放行阈值取不到（{thr!r}）⇒ 不写死数字（RC=2）")
        return 2

    raw = json.loads(POOL.read_text(encoding="utf-8"))
    # 料池有两种形状：g132 是裸列表，g154 是 {meta, items}（带出处账与许可说明）。
    # 取数口径不变：只吃 `user_text`，逐字、零加工。
    rows = raw["items"] if isinstance(raw, dict) and "items" in raw else raw
    texts = [p["user_text"] for p in rows]
    seen, pool = set(), []
    for t in texts:
        if t.strip() and t not in seen:
            seen.add(t)
            pool.append(t)
    print(f"池子：{LABEL}｜原始 {len(texts)} 条｜去重后 {len(pool)} 条（判据与轮 149 同一台仪器、同一口径）")

    go = ready_n = 0
    confs: list[float] = []
    missing: Counter = Counter()
    for t in pool:
        intent = ResearchIntent(project_id="probe-r149")
        intent.apply_update(extractor.extract_update(t, intent))
        still = compose_interview_reply(intent) is not None and not wants_results(t)
        go += not still
        ok, _ = ready_for_search(intent)
        ready_n += ok
        confs.append(intent.confidence)
        missing.update(intent.missing_fields())

    def verdict_of(text: str) -> tuple[bool, float, bool]:
        it = ResearchIntent(project_id="probe-r149")
        it.apply_update(extractor.extract_update(text, it))
        return (compose_interview_reply(it) is None or wants_results(text)), it.confidence, ready_for_search(it)[0]

    buckets = Counter()
    for c in confs:
        buckets["一个槽位都没填上" if c <= 0 else "<0.25" if c < .25
                else f"0.25–{thr}（差临门一脚）" if c < thr else f"≥{thr}"] += 1
    try:
        pool_min = gate_min()
    except Exception as exc:  # noqa: BLE001
        print(f"判读：POOL_MIN 读不出来（{exc}）⇒ 不猜数字（RC=2）")
        return 2

    print(f"\nZ1 放行（首问就进检索轮）：{go} / {len(pool)} 条｜长跑门槛 POOL_MIN={pool_min}（现读）")
    print(f"Z2 `ready_for_search` 为真：{ready_n} 条｜放行阈值 {thr} 由 `ready_for_search` 的默认参数现读｜"
          "confidence 直方图：" + "｜".join(f"{k}={v}" for k, v in sorted(buckets.items())))
    print(f"    缺得最多的槽位（Top5）：" + "、".join(f"{k}×{v}" for k, v in missing.most_common(5)))
    _tok = len(pool) - missing["topic"]
    print(f"    topic 非空：{_tok} / {len(pool)}"
          f"（{100.0 * _tok / max(1, len(pool)):.1f}%，规则通道口径 ⇒ 这一维是**下界**）")
    print(f"    （口径：规则通道单独走一遍，`llm_topic` 在 mock 下恒空 ⇒ topic 这一维是**下界**）")

    g98 = json.loads((EVD / "g98_cite_pool.json").read_text(encoding="utf-8"))
    pos = next((r.get("user_text") or "" for r in g98 if (r.get("user_text") or "").strip()), "")
    neg = "我刚入门，不知道做什么方向"
    p_go, p_conf, p_ready = verdict_of(pos) if pos else (False, 0.0, False)
    n_go, n_conf, _ = verdict_of(neg)
    print(f"Z3 正对照（g98 里轮 139 真拿到过卡的中文情景）放行={p_go}｜confidence={p_conf}｜ready={p_ready}")
    print(f"    正对照原文：{pos[:64]}")
    print(f"    负对照（明显该被反问的中文）放行={n_go}｜confidence={n_conf}")
    if not (p_go and not n_go):
        print("判读：两个对照没有反向 ⇒ 这把尺子分不开「会检索」与「被反问」，读数不作数（RC=2）")
        return 2

    if os.environ.get("PROBE_PLANT_TRIP"):
        trip.knock()
    print(f"\nZ0b 零网络自证：httpx post 触发={trip.net}（必须 0）"
          + ("｜🔴 植入：主动撞了一次雷（本场是负控制）" if trip.net else ""))
    if trip.net:
        print("判读：Z0b 不成立 ⇒ 读数不作数（RC=2）")
        return 2
    if go >= pool_min:
        v = (f"外部池上「首问就进检索轮」有 {go} 条 ≥ POOL_MIN={pool_min} ⇒ #25 的分母凑得出来，"
             f"不需要我代写任何一句对话（RC=0）")
    else:
        v = (f"只有 {go} 条 < POOL_MIN={pool_min} ⇒ #25 在这份外部池上**仍然没有分母**；"
             f"成因已定位到「整排槽位是空的」（见 Z2 直方图），不是壳词闸门——换料要换的是"
             f"「带着研究处境说话」的中文原话，不是再跑一场（RC=3）")
    print(f"\n判读：{v}")
    return 0 if go >= pool_min else 3


if __name__ == "__main__":
    sys.exit(main())
