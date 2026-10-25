"""起跑前体检：四个文献源现在到底活着几个（零模型调用、每源最多一次外呼）。

为什么要它：事项二那场 A/B 判「未测」不是因为两臂等价，而是**可用对只剩 10/15** ——
容器边车/裸日志里数出 10 次 provider 熔断（OpenAlex、Semantic Scholar 各自 120→240→480→900s 连击，
arXiv 一次 406）。同一批「首问即放行」的料在上一场是 9/9 全可用，这一场 10/15
⇒ **可用对分母是供给的函数，不是料的函数**。拿这种窗口去跑一小时 A/B，
等于把预算花在测一个会被限流判死的数上。

🔴 熔断表是**进程内**的（`providers/base.py:_DOWN_UNTIL`），本探针是新进程 ⇒ 看不见 A/B 那边的冷却，
所以这一台只能测"此刻 HTTP 通不通"，不能测"那一场当时有几个源活着"。
那件事的证据是日志里那 10 行熔断，不是这里。

判据（跑前写死）：
  P1 四个源各发**一条**固定查询（同一串英文主题词），报耗时与回条数；抛错要印异常类型与原因。
  P2 「活」= 拿到 ≥1 条结果。0 条与不可用分开记（前者是"查无相关"，后者是"源挂了"——
     轮 96 定过的口径：混在一起会把限流读成相关性差）。
  P3 真实外呼次数在 **httpx 传输层**数（`AsyncClient.send`），必须 ≤4；超了就是仪器在刷配额（RC=2）。
     🔴 不用 `provider_stats()['calls']`：实测四包都发出去它仍报 0（那条判据会恒真）。
  P4 判读线与退出码语义（同一台仪器三种结论不许共用一个码）：
       RC=0 活的源 ≥2 且外发 = 每源一次（4 包）⇒ 窗口健康，起跑；
       RC=3 活的源 <2 ⇒ 窗口不健康，别拿一小时的模型调用去换一个会被供给判死的数；
       RC=5 活的源 ≥2 但外发 >4 包 ⇒ 可以起跑，但未治项 N2（429 的 HTTP 层重试）在场；
            这不是仪器坏了，也不许读成「包数没问题」。
     首版把 RC=5 那种情况判成 RC=2「读数作废」——归因错了：多出来的包是产品重试发的，不是体检发的。
"""
from __future__ import annotations

import asyncio
import pathlib
import sys
import time

sys.path.insert(0, "/app")
_HERE = pathlib.Path(__file__).resolve()
for _cand in ["/app", str(pathlib.Path.cwd()), *map(str, _HERE.parents)]:
    if (pathlib.Path(_cand) / "companion").is_dir():
        sys.path.insert(0, _cand)
        break
else:
    print("判读：找不到含 companion/ 的仓根 ⇒ 仪器没连上被测物（RC=2）")
    sys.exit(2)

import httpx                                                        # noqa: E402
from companion.literature.providers.base import provider_stats      # noqa: E402
from companion.literature.providers.crossref_arxiv import (         # noqa: E402
    ArxivProvider, CrossrefProvider)
from companion.literature.providers.openalex import OpenAlexProvider    # noqa: E402
from companion.literature.providers.semantic_scholar import (           # noqa: E402
    SemanticScholarProvider)

# P3 的计数器必须是**会红的**：首版拿 `provider_stats()['calls']`，四个源各发了一包它仍报 0
# ⇒ 那条判据恒真，等于没写（产品自己的计数器只覆盖它自己的调用路径，不覆盖探针走的这条路）。
# 所以改成在 httpx 传输层挂表：`AsyncClient.send` 是所有请求的必经口。
_SENT: list[str] = []
_SEND_ORIG = httpx.AsyncClient.send


async def _counting_send(self, request, **kw):
    _SENT.append(str(request.url).split("?")[0])
    return await _SEND_ORIG(self, request, **kw)


httpx.AsyncClient.send = _counting_send

QUERY = "phishing website detection"
LIVE_MIN = 2


async def one(prov) -> tuple[str, int]:
    name = prov.name
    try:
        t0 = time.monotonic()
        got = await prov.search(QUERY, limit=3)
    except Exception as exc:  # noqa: BLE001 —— 体检要的就是把失败形状印出来
        return f"{name:<18} 抛 {type(exc).__name__}：{str(exc)[:80]} ⇒ 不可用", 0
    n = len(got or [])
    dt = time.monotonic() - t0
    return (f"{name:<18} {dt * 1000:.0f}ms 回 {n} 条 ⇒ "
            + ("活" if n else "通着但 0 条（记不可用）")), (1 if n else 0)


def main() -> int:
    live = 0
    for cls in (CrossrefProvider, ArxivProvider, OpenAlexProvider, SemanticScholarProvider):
        text, ok = asyncio.run(one(cls()))
        live += ok
        print("  " + text)
    stats = provider_stats()
    print(f"\nP3 httpx 传输层实数外发 {len(_SENT)} 包（四源各一次的上限是 4）：" + "、".join(_SENT))
    print(f"   对照：产品自己的 `provider_stats()` 在这里报 {stats} —— 它数不到探针走的这条路，"
          "所以本判据不拿它当证据（首版拿了，于是那条线恒真）")
    overrun = len(_SENT) > 4
    print(f"P2/P4 活的源 {live} 个｜起跑下限 {LIVE_MIN} 个")
    if overrun:
        print("   🔴 包数超 4 = 未治项 N2 在场：`providers/base.py` 的 HTTP 层对 429 重试，"
              "**每撞一次限流的源要多敲 3 包**。这不是体检在刷配额（体检只发了 4 次检索），"
              "所以不许判成「读数作废」——见下方退出码语义。")
    if live < LIVE_MIN:
        print(f"判读：只有 {live} 源可用 ⇒ 现在跑 A/B，可用对会被供给判死（上一场就是这么丢的），"
              "等配额窗口再跑（RC=3，这是窗口结论，不是产品结论）")
        return 3
    if overrun:
        print(f"判读：窗口够用（{live} 源可用）可以起跑，但 N2 的重试放大在场（{len(_SENT)} 包）"
              "⇒ 单独退出码 5：允许跑，不许把这条读成仪器坏了，也不许据此说「包数没问题」（RC=5）")
        return 5
    print(f"判读：窗口健康（{live} 源可用、{len(_SENT)} 包 = 每源一次）⇒ 允许起跑 A/B（RC=0）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
