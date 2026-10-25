"""供给闸门（共用实现）：此刻四个文献源活着几个、这台仪器真发了几包。

为什么单独一个模块而不是各处再写一遍：`probe_provider_supply.py`（体检）与
`probe_gap_subquery_ab_v2.py`（A/B 长跑）必须用**同一把尺**判"现在能不能跑"。
两处各写一遍，迟早长跑印"窗口健康"而体检印"活的源 1"，两头都自称同源（本仓已为此写过
一条 `test_no_divergent_duplicate_tests` 与多次对账判据）。

判据与退出码语义由调用方决定，这里只出数：
  `supply_now()` → (逐源人话行, 活的源数, 真发包数, 每源明细)
  「活」= 那次检索拿到 ≥1 条；抛错/0 条分开记（0 条是"查无相关"，抛错是"源挂了"——轮 96 的口径）。
包数在 **httpx 传输层**数（`AsyncClient.send`）：产品自己的 `provider_stats()['calls']`
在这条路上恒 0，拿它当判据等于写了条永不发火的门（首版真这么错过一次）。
"""
from __future__ import annotations

import asyncio
import pathlib
import sys
import time

import httpx

# 本机自检与容器长跑走同一份代码：把含 companion/ 的仓根找出来插进 sys.path
# （本地 `python 输出/scripts/supply_gate.py` 时 sys.path[0] 是脚本目录，不是仓根，
#  不补这一步，自检会以 ModuleNotFoundError 收场——那是仪器坏了，不是判据红）。
_HERE = pathlib.Path(__file__).resolve()
for _cand in ["/app", "/tmp", str(pathlib.Path.cwd()), *map(str, _HERE.parents)]:
    if (pathlib.Path(_cand) / "companion").is_dir() and _cand not in sys.path:
        sys.path.insert(0, _cand)
        break

QUERY = "phishing website detection"
_SENT: list[str] = []
_SEND_ORIG = httpx.AsyncClient.send


async def _counting_send(self, request, **kw):
    _SENT.append(str(request.url).split("?")[0])
    return await _SEND_ORIG(self, request, **kw)


def install_packet_meter() -> None:
    """挂表。重复调用只挂一次（幂等），免得两台仪器各包一层。"""
    if httpx.AsyncClient.send is _counting_send:
        return
    httpx.AsyncClient.send = _counting_send


async def _probe_local_refused() -> None:
    """往本机丢弃端口 9 发一次请求：连接立刻被拒、不出这台机器，但一定经过传输层。"""
    async with httpx.AsyncClient(timeout=2.0) as c:
        await c.get("http://127.0.0.1:9/")


def providers():
    from companion.literature.providers.crossref_arxiv import (
        ArxivProvider, CrossrefProvider)
    from companion.literature.providers.openalex import OpenAlexProvider
    from companion.literature.providers.semantic_scholar import SemanticScholarProvider
    return [CrossrefProvider(), ArxivProvider(), OpenAlexProvider(), SemanticScholarProvider()]


async def _one(prov) -> tuple[str, int, str]:
    install_packet_meter()
    name = prov.name
    try:
        t0 = time.monotonic()
        got = await prov.search(QUERY, limit=3)
    except Exception as exc:  # noqa: BLE001 —— 体检要的就是把失败形状印出来
        return f"{name:<18} 抛 {type(exc).__name__}：{str(exc)[:80]} ⇒ 不可用", 0, name
    n = len(got or [])
    return (f"{name:<18} {(time.monotonic() - t0) * 1000:.0f}ms 回 {n} 条 ⇒ "
            + ("活" if n else "通着但 0 条（记不可用）")), (1 if n else 0), name


def supply_now() -> tuple[list[str], int, int, list[str]]:
    """→ (逐源行, 活的源数, 真发包数, 哪些源判活)。同步入口：内部自己起事件循环。"""
    install_packet_meter()
    _SENT.clear()
    lines, live, alive_names = [], 0, []
    for prov in providers():
        text, ok, name = asyncio.run(_one(prov))
        lines.append(text)
        live += ok
        if ok:
            alive_names.append(name)
    return lines, live, len(_SENT), alive_names


def selfcheck() -> int:
    """证明这台仪器：① 真的发了包（包数>0，不是恒 0 的假计数器）② 0 条与抛错分开记
    ③ 挂表幂等（重复 install 不套两层）。🔴 全程不碰网络：把 `providers()` 整个换成假源——
    只替一个的话，另外三个仍会真发请求，那就不叫自检而叫偷偷烧配额。"""
    ok = []

    class Alive:
        name = "fake_alive"

        async def search(self, q, limit=3):
            return [{"title": "t"}]

    class Empty:
        name = "fake_empty"

        async def search(self, q, limit=3):
            return []

    class Broken:
        name = "fake_broken"

        async def search(self, q, limit=3):
            raise RuntimeError("HTTP 429")

    real = globals()["providers"]
    globals()["providers"] = lambda: [Alive(), Empty(), Broken()]
    try:
        lines, live, packets, names = supply_now()
    finally:
        globals()["providers"] = real
    ok.append(("活的源只算真拿到条目的（三个假源里 1 个活）",
               live == 1 and names == ["fake_alive"], f"live={live} names={names}"))
    ok.append(("0 条与抛错分开记（两行两种说法，不混成'没结果'）",
               any("fake_empty" in l and "0 条" in l for l in lines)
               and any("fake_broken" in l and "不可用" in l for l in lines), ""))
    ok.append(("假源自己没发包（说明计数只在真发请求时长）", packets == 0, f"packets={packets}"))
    # 计数器到底会不会跟着真请求动：往本机丢弃端口 9 发一次（连接立刻被拒，不出机器），
    # 但 `AsyncClient.send` 必经 —— 首版这条用假源去验，假源根本不发包，于是测的是"恒 0"，
    # 把"判据永不红"当成了自检通过。
    before = len(_SENT)
    try:
        asyncio.run(_probe_local_refused())
    except Exception:  # noqa: BLE001 —— 连接被拒是预期的，要的是"包真的走了一次传输层"
        pass
    ok.append(("包数计数器跟着真请求动（不是恒 0 ⇒ 那条线会红）",
               len(_SENT) == before + 1, f"计数从 {before} 到 {len(_SENT)}"))
    before = httpx.AsyncClient.send
    install_packet_meter()
    install_packet_meter()
    ok.append(("挂表幂等：重复 install 不套两层",
               httpx.AsyncClient.send is before is _counting_send, ""))
    for name, good, note in ok:
        print(f"  {'ok ' if good else '🔴 FAIL'} {name}｜{note}")
    bad = [n for n, g, _ in ok if not g]
    print(f"自检 {len(ok) - len(bad)}/{len(ok)} 通过" + ("" if not bad else f" ⇒ 坏在 {bad}"))
    return 0 if not bad else 2


if __name__ == "__main__":
    import sys
    sys.exit(selfcheck())
