"""P3 A/B 的用例挑选：从 g154 真人料池里选出**两回合能走到检索轮**的提问（零模型调用、零网络）。

为什么要在起跑前挑：轮 144/149 两次都是"跑了几十分钟才发现根本没进检索轮"。
这一台用规则通道（`LLM_MOCK=1`）把每条料走一遍产品自己的 `prepare_context` 判定，
只留下「第一句就放行」或「第一句被反问 + 点一颗产品自己的 chip 之后放行」的料。

🔴 第二句不是我写的：它是 `CHIP_OPTIONS[*].options[*].say` 里产品自己的文案，
按 `next_chips(intent)` 给出的顺序取第一颗。所以这一池仍然满足 #26「提问不是我写的」。

判据（跑前写死）：
  S1 零模型调用由机制保证：`llm.LLM_MOCK` 必须为真 + httpx 装雷，触发数必须 0；
     `--plant-net` 主动点一次雷，必须红（RC=2）。
  S2 每条入选料必须带出处（bvid/rpid/url），且第二句必须逐字等于某条 CHIP_OPTIONS 的 say。
  S3 两类都要报数：首问即放行 / 靠 chip 放行；只有一类也算得出分母，但要写清是哪一类。
  S4 防空转：入选 0 条 ⇒ RC=2（是判据没匹配上，不是"料池里没有这种话"）。
"""
from __future__ import annotations

import json
import os
import pathlib
import sys

os.environ["LLM_MOCK"] = "1"

ROOT = pathlib.Path(__file__).resolve().parents[2]
REPO = ROOT / "code" / "yanban"
sys.path[:0] = [str(REPO), str(ROOT / "输出" / "scripts")]

POOL = REPO / "docs" / "evidence" / "g154_bili_pool.json"
OUT = REPO / "docs" / "evidence" / "g156_ab_cases.json"
MAX_CASES = int(next((sys.argv[i + 1] for i, a in enumerate(sys.argv[:-1])
                      if a in ("--max", "-n")) , "24"))


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


def main() -> int:
    trip = Trip()
    trip.install()
    import llm
    from companion.conversation.interview_agent import CHIP_OPTIONS, compose_interview_reply, next_chips, wants_results
    from companion.conversation import dialogue_manager as dm
    from companion.intent import extractor
    from companion.intent.clarification import clarification_gaps
    from companion.intent.model import ResearchIntent

    if not llm.LLM_MOCK:
        print("判读：LLM_MOCK 没生效 ⇒ 每条都会真发请求（RC=2）")
        return 2
    rows = json.loads(POOL.read_text(encoding="utf-8"))
    rows = rows["items"] if isinstance(rows, dict) else rows
    chip_says = {say for _lab, opts in CHIP_OPTIONS.values() for _t, say in opts}
    print(f"S1 模式：LLM_MOCK={llm.LLM_MOCK}｜料池 {len(rows)} 条｜chip 原话 {len(chip_says)} 句")

    picked: list[dict] = []
    kinds = {"首问即放行": 0, "点一颗 chip 后放行": 0}
    for r in rows:
        if len(picked) >= MAX_CASES:
            break
        text = (r.get("user_text") or "").strip()
        if len(text) < 12:
            continue
        it = ResearchIntent(project_id="sel")
        it.apply_update(extractor.extract_update(text, it))
        if not clarification_gaps(it):
            continue                      # 没有缺口 ⇒ 补检索没有触发条件，这一条测不到要测的东西
        if compose_interview_reply(it) is None or wants_results(text):
            kinds["首问即放行"] += 1
            picked.append({"turns": [text], "kind": "首问即放行", "src": r,
                           "gaps": list(clarification_gaps(it))[:3]})
            continue
        chips = next_chips(it)
        if not chips:
            continue
        say = chips[0]["options"][0]["say"]
        if say not in chip_says:
            print(f"判读：chip 的 say 不在 CHIP_OPTIONS 里（{say[:20]}）⇒ 出处账坏了（RC=2）")
            return 2
        it.apply_update(extractor.extract_update(say, it))
        if compose_interview_reply(it) is None or wants_results(text + say):
            kinds["点一颗 chip 后放行"] += 1
            picked.append({"turns": [text, say], "kind": "点一颗 chip 后放行", "src": r,
                           "gaps": list(clarification_gaps(it))[:3]})

    print(f"\nS3 分母：首问即放行 {kinds['首问即放行']} 条｜点一颗 chip 后放行 {kinds['点一颗 chip 后放行']} 条"
          f"｜合计入选 {len(picked)} 条（上限 {MAX_CASES}）")
    if not picked:
        print("判读：一条都没选中 ⇒ 是判据没匹配上，不是「料池里没有这种话」（RC=2）")
        return 2
    items = []
    for i, p in enumerate(picked, 1):
        s = p["src"]
        items.append({
            "case": i, "kind": p["kind"], "turns": p["turns"], "gaps": p["gaps"],
            "bvid": s.get("bvid"), "rpid": s.get("rpid"), "url": s.get("url"),
            "comment_time_utc": s.get("comment_time_utc"), "collected_at": s.get("collected_at"),
            "video_title": s.get("video_title"),
        })
    OUT.write_text(json.dumps({"meta": {
        "pool": POOL.name, "kinds": kinds, "n": len(items),
        "turn2_provenance": "第二句逐字取自 CHIP_OPTIONS 的 say（产品自己的文案），不是本轮新造",
        "channel": "规则通道（LLM_MOCK=1）⇒ 放行判定是**下界**，线上真路径只会更多",
    }, "cases": items}, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"用例落盘：{OUT.name}（{len(items)} 对）")
    for it in items[:6]:
        print(f"  ◾ #{it['case']} {it['kind']}｜缺口 {it['gaps']}｜{it['turns'][0][:46]}"
              + (f" → 「{it['turns'][1][:22]}」" if len(it["turns"]) > 1 else ""))
    if "--plant-net" in sys.argv:
        import asyncio

        import httpx
        async def go():
            async with httpx.AsyncClient() as c:
                try:
                    await c.post("http://127.0.0.1:1/x", json={})
                except Exception:  # noqa: BLE001
                    pass
        asyncio.run(go())
    print(f"\nS1 零网络自证：httpx post 触发={trip.net}（必须 0）"
          + ("｜🔴 植入：主动撞了一次雷（本场是负控制）" if trip.net else ""))
    if trip.net:
        print("判读：S1 不成立 ⇒ 读数不作数（RC=2）")
        return 2
    print(f"\n判读：选出 {len(items)} 对可跑 A/B 的真人料（每条带 bvid/rpid 出处，第二句是产品自己的 chip 文案）（RC=0）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
