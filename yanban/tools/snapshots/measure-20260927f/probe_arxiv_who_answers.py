# -*- coding: utf-8 -*-
"""轮 162 Phase 1 续 · 两件事分开验，一次只动一个变量。

上一台仪器（`probe_supply_failure_causes.py`）取回的证据：
  · arXiv：四种形状**全 406、响应体空、连一条响应头都没抓到**，两条出口（本机与容器）都一样。
    一个不带任何参数的请求也能"形状错"吗？不能 ⇒ 但"服务端连 Server/Content-Type 都不给"也不像
    arXiv 自己，更像中间层。⇒ 需要把**全部响应头**取回来才知道是谁在答。
  · OpenAlex：三种形状里 `filter=has_abstract:true` 那条回 200（还带 x-ratelimit-limit=1000），
    另两条 429（Retry-After 30—37 秒，不是日额度）。但那条 200 是**固定顺序里的最后一次** ⇒
    形状与时间/顺序没分开，n=1 不算证据。

所以这一台只做两件事：
  A（arXiv）：把完整响应头与响应体取回来，并只动"谁在应答"这一个维度：
              默认 UA / 浏览器 UA / Accept:application/atom+xml / Accept:*/* / http 方案 / 完全裸 URL。
              —— 只查 headers 曾被人试过五组全 406；这次先看清**是不是 arXiv 在答**。
  B（OpenAlex）：三条形状 × 三轮，每轮**轮换出场顺序**（每条形状在各位置各出现一次），
              间隔固定 9 秒。判"形状"还是"顺序/时刻"：
                形状效应 ⇒ 同一条形状在三个位置都成/都败；
                顺序效应 ⇒ 成败跟着位置走，与形状无关。

判线（跑前写死）：
  J-1 crossref 正对照必须 200，否则整套读数作废（RC=2，网络/仪器问题，不是产品问题）。
  J-2 A 段每条都要印完整响应头（键名全列 + Server/Via/X-* 值），并据此**指名应答方**
      （arXiv 自己会带 Server: Apache/...；Cloudflare/中间层带 Via 或 cf-ray 之类）。
  J-3 B 段每条形状记 (位置 → 状态)。只有当某形状 **3/3 全成且另两条 0/3** 才允许写"形状可修"；
      任何"成 1 次败 2 次"都判为不确定（供给是时间的函数，轮 161 已量过），如实印"分不出来"。
  J-4 零模型调用、零写库；总请求数打印出来，方便事后核礼貌间隔。
退出码：0=A 或 B 至少一条取回可判结论｜1=两条都分不开（如实报，不硬判）｜2=仪器/网络坏。
"""
from __future__ import annotations

import asyncio
import sys

import httpx

UA_DEFAULT = {"User-Agent": "yanban-companion/2.0 (research assistant; contact: team@yanban.local)"}
UA_BROWSER = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                            "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"}
Q = "phishing website detection"
ARXIV = "https://export.arxiv.org/api/query"
OA = "https://api.openalex.org/works"
INTERVAL = 9.0
n_sent = [0]


async def dump(c, label, url, params, headers, show_all=False):
    n_sent[0] += 1
    try:
        r = await c.get(url, params=params, headers=headers, timeout=25.0)
    except Exception as exc:  # noqa: BLE001
        print(f"  {label:36s} 抛错 {type(exc).__name__}: {str(exc)[:80]}")
        await asyncio.sleep(2.0)
        return 0, {}
    heads = dict(r.headers)
    who = heads.get("server") or heads.get("via") or heads.get("cf-ray") or "?"
    print(f"  {label:36s} {r.status_code}｜应答方 {who}"
          f"｜头 {len(heads)} 个｜体 {len(r.text or '')} 字")
    if show_all or r.status_code >= 400:
        print("      全部响应头：" + "; ".join(f"{k}={v}" for k, v in list(heads.items())[:12])[:230])
        print("      响应体前 150：" + (" ".join((r.text or "").split())[:150] or "(空)"))
    await asyncio.sleep(2.0)
    return r.status_code, heads


async def main() -> int:
    trust = "--trust-env" in sys.argv
    print(f"出口：{'走系统代理' if trust else '直连'}｜计划请求数 6(arXiv) + 9(OpenAlex) + 1(正对照)")
    verdict = []
    async with httpx.AsyncClient(trust_env=trust, follow_redirects=True) as c:
        _code, _h = await dump(c, "crossref 正对照", "https://api.crossref.org/works",
                               {"query": Q, "rows": 3}, UA_DEFAULT)
        if _code != 200:
            print(f"判读：正对照 crossref 回 {_code} ⇒ 这台出口出不去，本次读数全部作废（RC=2）")
            return 2

        print("\n[A] arXiv：谁在答这个 406？（每条印完整响应头）")
        a = {}
        a["默认UA"] = await dump(c, "默认UA + 现状四词AND", ARXIV,
                                {"search_query": 'all:"phishing" AND all:"website"', "max_results": 3},
                                UA_DEFAULT, True)
        a["浏览器UA"] = await dump(c, "浏览器UA", ARXIV, {"search_query": "all:phishing", "max_results": 3},
                                  UA_BROWSER, True)
        a["Accept=atom"] = await dump(c, "Accept: application/atom+xml", ARXIV,
                                      {"search_query": "all:phishing", "max_results": 3},
                                      {**UA_DEFAULT, "Accept": "application/atom+xml"}, True)
        a["Accept=*/*"] = await dump(c, "Accept: */*", ARXIV, {"search_query": "all:phishing"},
                                    {**UA_DEFAULT, "Accept": "*/*"}, True)
        a["裸URL"] = await dump(c, "完全裸 URL（无参数）", ARXIV, {}, UA_DEFAULT, True)
        n_sent[0] += 1
        try:
            r = await c.get("http://export.arxiv.org/api/query", params={"search_query": "all:phishing"},
                            headers=UA_DEFAULT, timeout=25.0)
            print(f"  {'http 方案（会重定向到 https）':34s} {r.status_code}｜最终 URL {str(r.url)[:60]}")
            a["http方案"] = (r.status_code, dict(r.headers))
        except Exception as exc:  # noqa: BLE001
            print(f"  {'http 方案':36s} 抛错 {type(exc).__name__}")
            a["http方案"] = (0, {})
        ok_a = [k for k, (code, _h) in a.items() if code == 200]
        servers = {k: (h.get("server") or h.get("via") or "无 Server/Via")
                   for k, (_c, h) in a.items()}
        print(f"  A 小结：200 的形状 {ok_a or '无'}｜应答方 {sorted(set(servers.values()))}")
        if ok_a:
            verdict.append(f"arXiv 形状可修：{ok_a}")

        print(f"\n[B] OpenAlex：三条形状 × 三轮轮换顺序（间隔 {INTERVAL:.0f}s，分形状与时刻）")
        shapes = {
            "现状 search+mailto": {"search": Q, "per-page": 6, "mailto": "team@yanban.local"},
            "search 无 mailto": {"search": Q, "per-page": 6},
            "search+filter 收窄": {"search": Q, "per-page": 6, "mailto": "team@yanban.local",
                              "filter": "has_abstract:true"},
        }
        order = list(shapes)
        grid = {s: [] for s in shapes}
        for rnd in range(3):
            rot = order[rnd % 3:] + order[:rnd % 3]
            for pos, name in enumerate(rot, 1):
                code, _h = await dump(c, f"第{rnd + 1}轮 位置{pos} {name}", OA, shapes[name], UA_DEFAULT)
                await asyncio.sleep(max(0.0, INTERVAL - 2.0))
                grid[name].append((pos, code))
        print("  B 明细（位置→状态）：")
        for name, seq in grid.items():
            print(f"      {name:22s} {seq}")
        all200 = [n for n, s in grid.items() if all(code == 200 for _p, code in s)]
        allbad = [n for n, s in grid.items() if all(code != 200 for _p, code in s)]
        by_pos = {p: [code for s in grid.values() for pp, code in s if pp == p] for p in (1, 2, 3)}
        pos_clean = any(all(x == 200 for x in v) or all(x != 200 for x in v) for v in by_pos.values())
        if all200 and len(all200) < 3 and not pos_clean:
            verdict.append(f"OpenAlex 形状可修：{all200}（另两条 0/3：{allbad}）")
        elif pos_clean:
            print("  🔴 B 结论被位置带偏：某一位置的三条全同 ⇒ 这是时刻/顺序效应，不是形状效应，不判")
        else:
            print("  B 未形成「某形状 3/3、另两条 0/3」的形状分离 ⇒ 不判形状可修")

    print(f"\n实际发出请求数：{n_sent[0]}")
    if verdict:
        print("判读：" + "；".join(verdict) +
              " ⇒ 进入 Phase 3：一次只改一处，并配「关掉这个改动就该变差」的植入证明（RC=0）")
        return 0
    print("判读：A 与 B 都没能分开原因（arXiv 若始终无 Server 头则是中间层拒绝，属环境项；"
          "OpenAlex 若随时刻翻转则供给确实是时间的函数）⇒ 本轮如实报「分不出来」，不硬判（RC=1）")
    return 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
