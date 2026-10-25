# -*- coding: utf-8 -*-
"""轮 162 Phase 3 · 单变量实验：服务端报的 Retry-After 到底兑不兑现？

为什么是这一条假设（前面几台仪器取回的事实，不是猜测）：
  · `providers/base.py:43` 的 docstring 写着「429 的 Retry-After 是**日额度级**的，退避并不会放行」，
    并且 `ProviderUnavailable` 根本没带这个头 ⇒ `trip_for()` 只能按固定阶梯冷却 120→240→480→900 秒。
  · 但 `probe_supply_failure_causes.py` 在两条出口都取回 `retry-after=30..37`（**秒**）+
    服务端原话「temporarily rate-limited … Please retry in 33s」，
    Semantic Scholar 的原话是「Please wait and try again or apply for a key」。
  · `probe_arxiv_who_answers.py` 又看到 OpenAlex 三条形状 × 三个位置**全 200** ⇒ 它是会自己好的。
⇒ 假设 H：OpenAlex/S2 的 429 是**几十秒级**的临时限流；服务端说的秒数兑现，
   那么"等它说的秒数再打一次"就该放行，而现在这套 120 秒起步、逐次翻倍的阶梯是在**过度惩罚**，
   在一个长驻服务里会把一个其实活着的源锁死 15 分钟（这就是轮 161 量到的"供给主导"里可修的那一块）。

这条实验一次只测一个变量：**等 Retry-After 之后那一次请求成不成**。
  R-1 每个源最多 4 次机会：打一次；若 429 就读 Retry-After（没有头就记 "无头"），
      等 max(秒数, 5) + 2 秒，再打一次；把 (第几次, 状态, 等了多久) 全程记下来。
  R-2 crossref 正对照必须 200（否则这台仪器的网络就是坏的，任何红都归不了因）。
  R-3 判线（跑前写死，不许看完数再定）：
        某源出现过 429 且**等待后至少一半转 200** ⇒ 判「Retry-After 兑现 ⇒ 阶梯是过度惩罚」，
          可以进 Phase 4 改成"按服务端秒数冷却/一次有据重试"；
        某源出现过 429 且**等待后仍然全 429** ⇒ 判「服务端说的秒数不兑现 ⇒ 现有长冷却不是错」，
          只改那句 docstring（它把秒级说成了日级），不动行为；
        某源一次 429 都没出现 ⇒ 判「本轮没病可测」，**不许**据此说它好或坏（RC=3 未测），
        这条尤其重要：上一台仪器就是撞在"全 200 时无从归因"上。
退出码：0=取到"兑现"的证据（可以动产品码）｜1=取到"不兑现"的证据（只改文档）｜3=本轮没触发 429，未测｜2=仪器/网络坏
"""
from __future__ import annotations

import asyncio
import re
import sys
import time

import httpx

UA = {"User-Agent": "yanban-companion/2.0 (research assistant; contact: team@yanban.local)"}
Q = "phishing website detection"
ROUNDS = 4
SOURCES = {
    "openalex": ("https://api.openalex.org/works", {"search": Q, "per-page": 6,
                                                "mailto": "team@yanban.local"}),
    "s2": ("https://api.semanticscholar.org/graph/v1/paper/search", {"query": Q, "limit": 6}),
}


def parse_retry(headers) -> tuple:
    ra = headers.get("retry-after")
    if not ra:
        return (None, "无 Retry-After 头")
    m = re.search(r"\d+", ra)
    return ((int(m.group()), "秒") if m else (None, f"读不出数字:{ra[:20]}"))


async def main() -> int:
    trust = "--trust-env" in sys.argv
    print(f"出口：{'走系统代理' if trust else '直连'}｜每源最多 {ROUNDS} 次机会，等待时长由服务端决定")
    async with httpx.AsyncClient(trust_env=trust, timeout=30.0) as c:
        try:
            r0 = await c.get("https://api.crossref.org/works", params={"query": Q, "rows": 3}, headers=UA)
        except Exception as exc:  # noqa: BLE001
            print(f"判读：正对照 crossref 抛错 {type(exc).__name__} ⇒ 网络/仪器坏了（RC=2）")
            return 2
        if r0.status_code != 200:
            print(f"判读：正对照 crossref 回 {r0.status_code} ⇒ 这台出口出不去，读数作废（RC=2）")
            return 2
        print(f"R-2 正对照 crossref 200 OK\n")

        any_429, verdicts = False, []
        for name, (url, params) in SOURCES.items():
            print(f"[{name}]")
            seq, hits429, rescued = [], 0, 0
            for i in range(ROUNDS):
                t0 = time.monotonic()
                r = await c.get(url, params=params, headers=UA)
                wait = 0.0
                note = ""
                if r.status_code == 429:
                    hits429 += 1
                    sec, why = parse_retry(r.headers)
                    wait = max(sec or 20.0, 5.0) + 2.0
                    note = f"{why}→等 {wait:.0f}s"
                    await asyncio.sleep(wait)
                    r2 = await c.get(url, params=params, headers=UA)
                    dt = time.monotonic() - t0
                    seq.append((i + 1, 429, r2.status_code, round(wait, 1), round(dt, 1)))
                    print(f"    第{i + 1}次 429 → 等待 {wait:.0f}s 后 {r2.status_code}"
                          f"（本轮总耗时 {dt:.1f}s）{note}")
                    if r2.status_code == 200:
                        rescued += 1
                    await asyncio.sleep(3.0)
                else:
                    dt = time.monotonic() - t0
                    seq.append((i + 1, r.status_code, None, 0.0, round(dt, 1)))
                    print(f"    第{i + 1}次 {r.status_code}（无等待，{dt:.1f}s）")
                    await asyncio.sleep(3.0)
                if seq[-1][1] == 200 and len(seq) >= 3:
                    break
            if hits429 == 0:
                verdicts.append((name, "未触发 429", 0, 0))
                print(f"  ⇒ 本轮这个源一次都没 429 ⇒ 无从归因（不写好也不写坏）")
            else:
                any_429 = True
                rate = rescued / hits429
                verdicts.append((name, "兑现" if rate >= 0.5 else "不兑现", hits429, rescued))
                print(f"  ⇒ 429 共 {hits429} 次，按服务端秒数等待后转 200 的有 {rescued} 次"
                      f"（{100.0 * rate:.0f}%）")
    print("\n汇总：")
    for name, kind, h, s in verdicts:
        print(f"  {name:10s} {h} 次 429，等待后放行 {s} 次 ⇒ {kind}")
    if not any_429:
        print("判读：两个源本轮都没触发 429 ⇒ 这个实验没有可测对象，**不许**读成"
              "「阶梯是对的」或「源没问题」；供给宽窗里跑不出这条结论（RC=3 未测）")
        return 3
    good = [n for n, k, _h, _s in verdicts if k == "兑现"]
    if good:
        print(f"判读：{'、'.join(good)} 的 Retry-After 兑现 ⇒ 现有 120s 起步、逐次翻倍的阶梯是过度惩罚，"
              "进 Phase 4：把服务端秒数接进冷却（一次有据重试或按秒数冷却），"
              "并配「关掉这个改动就该变差」的植入（RC=0）")
        return 0
    print("判读：出现过 429 且按服务端秒数等待后仍不放行 ⇒ 阶梯不是错，但 `base.py:43` 那句"
          "「Retry-After 是日额度级」与实测的 30—37 秒不符，只改文档不改行为（RC=1）")
    return 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
