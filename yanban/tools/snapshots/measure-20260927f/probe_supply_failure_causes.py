# -*- coding: utf-8 -*-
"""轮 162 Phase 1 · 三个挂掉的源各自"为什么挂"——把服务端自己说的话取回来。

背景（不是结论）：轮 160/161 的读数里只有 crossref 活着，另外三家每次都空手：
  arXiv 406（代码注释写着"对本出口 IP 恒 406、五组 headers 全 406"）、OpenAlex 429、Semantic Scholar 429。
"供给是主导项"是轮 161 量出来的（换一个源活着，救回 11→20 句），所以这三条里只要有**一条是可修的**，
它就不该继续被当成环境事实。但"可修"是需要证据的判语，不是愿望。

Phase 1 只问三件事，不改任何产品码：
  P-a 每个源的失败**响应体与响应头**到底写了什么（服务端常把原因直接告诉你：
      是 "please provide a valid mailto"、还是 "retry after N seconds"、还是空 406 页）。
      🔴 只记状态码就等于把答案丢掉（本仓有过一次：编译器的 `note:` 行里写着确切原因）。
  P-b 是"出口 IP 被拒"还是"请求形状不被接受"？——同一个源打**多种形状**（简单式子 vs 现在产品发的
      四词 AND 带引号式子、GET 参数顺序、有无 filter/mailto），若某一形状能回 200，
      那就是形状问题，不是 IP 问题。
  P-c 换出口 IP 能不能过？——同一份矩阵在容器（生产出口）与本机（另一条出口）各跑一遍；
      两边都 406/429 ⇒ 形状/凭据问题；只有容器挂 ⇒ 才是 IP 问题。

判线（跑前写死）：
  J-1 crossref 必须回 200（**正对照**：它不活就说明这台出口根本出不去，后面所有红都归不了因）
  J-2 每条请求都要印：状态码 + Retry-After/RateLimit 类头 + 响应体前 160 字（服务端原话）
  J-3 结论按源分开写，只有看到"某一形状 200 而现状形状非 200"才允许说"形状可修"；
      只看到"全形状同码"就说"这次没能用形状区分开"，**不许**顺势写成"IP 被封"（那是另一条判据，
      要靠 P-c 的双出口对照）。
  J-4 零模型调用（不 import llm）、零写入（不碰任何库文件）、每次请求之间 sleep 不打断礼貌间隔
退出码：0=取回了能区分形状与 IP 的证据｜1=全形状同码（本轮分不出来，如实说分不出来）｜2=仪器或网络坏了
"""
from __future__ import annotations

import asyncio
import os
import sys

import httpx

os.environ.setdefault("LLM_MOCK", "1")
UA = {"User-Agent": "yanban-companion/2.0 (research assistant; contact: team@yanban.local)"}
Q = "phishing website detection"
HEADERS = ("retry-after", "x-ratelimit-limit", "x-ratelimit-remaining", "link", "server", "content-type")


def die(code: int, msg: str) -> None:
    print(f"判读：{msg}（RC={code}）")
    sys.exit(code)


async def one(client: httpx.AsyncClient, label: str, url: str, params: dict) -> tuple:
    try:
        r = await client.get(url, params=params, headers=UA, timeout=25.0)
    except Exception as exc:  # noqa: BLE001 — 网络层原因要分类印出，不许吞
        print(f"  {label:34s} 抛错 {type(exc).__name__}: {str(exc)[:90]}")
        await asyncio.sleep(1.5)
        return (label, 0)
    hs = " ".join(f"{k}={r.headers[k]}" for k in HEADERS if k in r.headers)
    body = " ".join((r.text or "").split())[:170]
    print(f"  {label:34s} {r.status_code}｜{hs[:120]}")
    print(f"      响应体：{body or '(空)'}")
    await asyncio.sleep(2.0)
    return (label, r.status_code)


async def main() -> int:
    trust = "--trust-env" in sys.argv        # 容器里直连；本机若必须走代理就加这个口子
    via = "走系统代理(trust_env=True)" if trust else "直连(trust_env=False)"
    print(f"出口：{via}｜代理 env="
          f"{'有' if any(k.lower() in {x.lower() for x in os.environ} for k in ('http_proxy', 'https_proxy')) else '无'}")
    codes = {}
    async with httpx.AsyncClient(trust_env=trust) as c:
        print("\n[J-1 正对照] crossref（产品里唯一确定活着的源）")
        _l, codes["crossref"] = await one(c, "crossref works?query", "https://api.crossref.org/works",
                                          {"query": Q, "rows": "6"})
        if codes["crossref"] != 200:
            die(2, f"正对照 crossref 就回 {codes['crossref']} ⇒ 这台出口出不去/上游挂了，"
                   "后面所有红都归不了因（别把'仪器没网'读成'源被封'）")

        print("\n[P-b] arXiv：现状形状 vs 三种不同形状（406 是形状还是 IP？）")
        cur = {"search_query": 'all:"phishing" AND all:"website" AND all:"detection"',
               "start": 0, "max_results": 6, "sortBy": "relevance"}
        codes["arxiv:产品现状四词AND"] = (await one(c, "arxiv 产品现状四词AND", "https://export.arxiv.org/api/query", cur))[1]
        codes["arxiv:单个all:词"] = (await one(c, "arxiv 单个 all: 词", "https://export.arxiv.org/api/query",
                                            {"search_query": f"all:{Q}", "max_results": 6}))[1]
        codes["arxiv:无参数"] = (await one(c, "arxiv 不带 search_query", "https://export.arxiv.org/api/query",
                                        {"max_results": 3}))[1]
        codes["arxiv:title字段检索"] = (await one(c, "arxiv ti: 字段", "https://export.arxiv.org/api/query",
                                             {"search_query": f'ti:"{Q}"', "max_results": 6}))[1]

        print("\n[P-b] OpenAlex：mailto 是真邮箱形状 vs 占位域 vs 无 mailto（429 是额度还是礼貌池没进？）")
        codes["openalex:现状占位mailto"] = (await one(c, "openalex mailto=占位.local", "https://api.openalex.org/works",
                                                 {"search": Q, "per-page": 6, "mailto": "team@yanban.local"}))[1]
        codes["openalex:无mailto"] = (await one(c, "openalex 不带 mailto", "https://api.openalex.org/works",
                                            {"search": Q, "per-page": 6}))[1]
        codes["openalex:filter+mailto"] = (await one(c, "openalex 带 filter 收窄", "https://api.openalex.org/works",
                                                {"search": Q, "per-page": 3, "mailto": "team@yanban.local",
                                                 "filter": "has_abstract:true"}))[1]

        print("\n[P-b] Semantic Scholar：现状全字段 vs 收窄 fields（429 是共享池额度还是请求太重？）")
        codes["s2:现状full"] = (await one(c, "s2 现状 full 查询", "https://api.semanticscholar.org/graph/v1/paper/search",
                                      {"query": Q, "limit": 6,
                                       "fields": "title,abstract,year,citationCount,externalIds,openAccessPdf"}))[1]
        codes["s2:nofields"] = (await one(c, "s2 不带 fields", "https://api.semanticscholar.org/graph/v1/paper/search",
                                      {"query": Q, "limit": 3}))[1]
    print("\n矩阵汇总（0=抛错）：")
    for k, v in codes.items():
        print(f"  {k:34s} {v}")
    shape_fixable = [k for k, v in codes.items()
                     if ":" in k and v == 200 and not k.startswith("crossref")]
    all_same = all(v == list(codes.values())[1] for k, v in codes.items() if not k.startswith("crossref"))
    if shape_fixable:
        print(f"判读：这些形状回 200 而同源的其他形状非 200 ⇒ **形状可修**：{shape_fixable}"
              "（下一步按 Phase 3 单变量验证，并配「关掉修复就该变差」的植入）（RC=0）")
        return 0
    if all_same:
        print("判读：每个源全形状同码 ⇒ 本轮**没能用请求形状区分开**原因；"
              "要判「IP/额度」还差 P-c 的双出口对照（同一矩阵换一条出口再跑）（RC=1）")
        return 1
    print("判读：既有非 200 又不全同码 ⇒ 情况混合，逐源看上面响应体原话，别下总结论（RC=1）")
    return 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
