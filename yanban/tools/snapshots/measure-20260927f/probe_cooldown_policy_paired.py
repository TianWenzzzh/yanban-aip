# -*- coding: utf-8 -*-
"""轮 162 · 冷却策略的前后数：**同一份服务端应答，两种策略各自重放**。

为什么不能"分两遍跑再比"：这次改的是**跨轮冷却时长**（120s 起步翻倍 → 照服务端 `Retry-After` 的秒数）。
探针一起来熔断器是空的，单轮体检量不出差别；而分两个时刻跑长轮，服务端状态本来就不同
（轮 161 已量到"供给是时间的函数"），那是一次假配对。
🔴 还有一个更隐蔽的圈套：采集时**不许**问熔断器。如果采集器自己按新策略跳过某些轮，
   那这份轨迹就是新策略 produced 的，再拿它去"对比"旧策略就成了自我确认。
   ⇒ 采集阶段每轮都真打每个源（不看任何锁），只记"服务端这轮说了什么"；策略只在重放阶段参与。

重放方式：对每种策略各自模拟锁表（哪些轮该允许出网），然后数
   `允许出网的轮数` 与 `其中服务端回了 200 的轮数`。
   后者就是"这一策略下，本轮检索真能拿到该源结果的轮数"。两侧看的是同一批应答 ⇒ 差值只来自策略。

判线（跑前写死）：
  W-1 crossref 是正对照，也是"策略不该伤害好源"的对照：两种策略下它的"可出结果轮数"必须相等且=全轮。
      它自己回不满 ⇒ 出口或上游不稳，本次配对作废（RC=2）。
  W-2 采集阶段每轮真打，不查 breaker（源码里就断言一次：采集循环里没有 breaker_open 调用）。
  W-3 只有当 `新策略可出结果轮数 − 旧策略 ≥ 2`（任一变了的源上）才允许说"这次改动有收益"；
      相等 ⇒ 说"本窗口里两种策略没有可测差别"（不许说"改动没用"，也不许说"有用"）。
  W-4 零模型调用、零写库。已知偏置：采集比任何一个真实策略都更"莽"（每轮必打），
      所以服务端 429 会比产品实际遇到的更多 ⇒ 这个偏置**偏向高估新策略的收益**，
      因此 W-3 的门槛定在 2 轮，且结论要连着这句偏置一起引用。
退出码：0=新策略多放行 ≥2 轮｜1=差别不足（收益未证实）｜2=仪器/网络坏。
"""
from __future__ import annotations

import asyncio
import os
import pathlib
import re
import sys

os.environ.setdefault("LLM_MOCK", "1")
import httpx

HERE = pathlib.Path(__file__).resolve()
for _c in ("/app", str(HERE.parent), os.getcwd(),
           *[str(p / "code" / "yanban") for p in HERE.parents]):
    if (pathlib.Path(_c) / "companion").is_dir():
        sys.path.insert(0, _c)
        break
else:
    print("判读：找不到含 companion 的目录 ⇒ 拿不到产品自己的熔断器（RC=2）")
    sys.exit(2)

from companion.literature.providers.base import (  # noqa: E402
    COOLDOWN, MAX_COOLDOWN, retry_seconds)

ROUNDS = 10
GAP = 20.0
Q = "phishing website detection"
UA = {"User-Agent": "yanban-companion/2.0 (research assistant; contact: team@yanban.local)"}
SOURCES = {
    "openalex": ("https://api.openalex.org/works",
                 {"search": Q, "per-page": 5, "mailto": "team@yanban.local"}),
    "s2": ("https://api.semanticscholar.org/graph/v1/paper/search", {"query": Q, "limit": 5}),
    "arxiv": ("https://export.arxiv.org/api/query", {"search_query": "all:phishing", "max_results": 5}),
    "crossref": ("https://api.crossref.org/works", {"query": Q, "rows": 5}),
}


def old_seconds(strikes: int) -> float:
    """轮 157 那一版的限流档：120s 起步、逐次翻倍、封顶 900s（完全不看 Retry-After）。"""
    return min(COOLDOWN * (2 ** max(1, strikes) - 1), MAX_COOLDOWN)


def replay(seq: list, policy: str, src: str) -> tuple:
    """seq = [(状态码, retry_after秒)] 逐轮；返回 (允许出网的轮数, 其中 200 的轮数, 锁住的轮数)。"""
    locked_until, strikes = -1.0, 0
    allowed = good = locked = 0
    for i, (code, ra) in enumerate(seq):
        t = i * GAP
        if t < locked_until:
            locked += 1
            continue
        allowed += 1
        if code == 200:
            good += 1
            strikes = 0
        elif code == 429:
            if policy == "new" and ra is not None:
                locked_until = t + min(max(ra, 1.0), MAX_COOLDOWN)
            else:
                strikes += 1
                locked_until = t + old_seconds(strikes)
        else:                                  # 406/403/抛错：两侧同按永久档
            locked_until = t + 3600.0
    return allowed, good, locked


def assert_no_breaker_in_collect() -> None:
    """W-2：采集阶段查锁 = 自我确认。这条在源码上钉一次。"""
    src = HERE.read_text(encoding="utf-8")
    body = src[src.index("async def collect"):src.index("async def main")]
    if re.search(r"breaker_open|trip_for", body):
        print("判读：采集循环里出现了 breaker_open/trip_for ⇒ 轨迹被新策略污染，本次配对作废（RC=2）")
        sys.exit(2)


async def collect(trust: bool) -> dict:
    """每轮真打每个源，只记服务端说了什么——不看任何锁。"""
    trace = {n: [] for n in SOURCES}
    async with httpx.AsyncClient(trust_env=trust, timeout=25.0) as c:
        for r in range(ROUNDS):
            line = []
            for name, (url, params) in SOURCES.items():
                try:
                    resp = await c.get(url, params=params, headers=UA)
                    trace[name].append((resp.status_code, retry_seconds(resp.headers)))
                    line.append(f"{name}={resp.status_code}")
                except Exception as exc:  # noqa: BLE001
                    trace[name].append((f"err:{type(exc).__name__}", None))
                    line.append(f"{name}=抛错")
                await asyncio.sleep(1.0)
            print(f"  第{r + 1:2d}/{ROUNDS}轮 " + " ".join(line))
            if r < ROUNDS - 1:
                await asyncio.sleep(GAP)
    return trace


async def main() -> int:
    trust = "--trust-env" in sys.argv
    assert_no_breaker_in_collect()
    print(f"出口：{'走系统代理' if trust else '直连'}｜{ROUNDS} 轮 × {len(SOURCES)} 源，"
          f"轮间隔 {GAP:.0f}s（采集阶段不查任何锁）")
    trace = await collect(trust)
    print("\n每源应答分布（含服务端给的秒数）：")
    for name in SOURCES:
        codes = [c for c, _r in trace[name]]
        ras = sorted({r for _c, r in trace[name] if r is not None})
        print(f"  {name:10s} {codes}｜Retry-After 出现过 {ras or '从未'}")
    cr = [c for c, _ in trace["crossref"]]
    if not all(c == 200 for c in cr):
        print(f"判读：W-1 正对照 crossref 没全 200（{cr}）⇒ 出口/上游不稳，本次不配对（RC=2）")
        return 2
    print("\n同一份应答、两种策略重放（可出结果轮数 = 允许出网 ∧ 服务端回 200）：")
    best_gain = 0
    for name in SOURCES:
        a_n, g_n, l_n = replay(trace[name], "new", name)
        a_o, g_o, l_o = replay(trace[name], "old", name)
        gain = g_n - g_o
        best_gain = max(best_gain, gain)
        tail = "" if name != "crossref" else "  ← 正对照，两侧必须相等"
        print(f"  {name:10s} 新：放行 {a_n:2d} 轮 / 出结果 {g_n:2d} 轮（锁住 {l_n}）｜"
              f"旧：放行 {a_o:2d} 轮 / 出结果 {g_o:2d} 轮（锁住 {l_o}）｜差 {gain:+d} 轮{tail}")
    if best_gain >= 2:
        print(f"判读：新策略在同一批服务端应答下多出结果 {best_gain} 轮（≥2）⇒ 改动有收益（RC=0）。"
              "连着 W-4 的偏置一起引用：采集比真实策略更莽，429 偏多，这个数会**偏高估**。")
        return 0
    print("判读：两种策略的可出结果轮数差别 <2 ⇒ 这个窗口里**没有可测的配对差别**；"
          "既不据此说改动有用，也不说它没用（收益未证实，RC=1）")
    return 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
