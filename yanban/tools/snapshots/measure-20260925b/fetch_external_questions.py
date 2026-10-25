"""取一批**不是我写的**科研提问（Stack Exchange 学术类站点），连出处一起落地。

为什么要外部语料：轮 112 的只读盘点把"线上=真实用户"这个前提量没了——754 个项目全是自家探针
流量，`scan_title_origin.py` 判据 7/7 条标题在自家脚本里逐字命中。轮 138 又因此发现 #25 的
可用对被池子卡住（31 条原始提问去重只剩 18 对，跑完注定「未测」）。两件事同一个根：**池子里
全是我们自己写的句子**。

三条硬约束（写在这里，也写在下面代码里）：
1. `user_text` 只放提问原文（标题），一个字的加工都不做——"不是我写的"这件事不能被我自己的手污染；
2. 每条带可核对的出处（question_id / 站点 / 永久链接 / 作者 / 发布时间 / 许可证），CC BY-SA 要求署名；
3. 拉取与判定**分开**：这个脚本碰网络，`verify_question_origin.py` 只读已落地的那份、离线判。

只读外部 API，不发帖、不注册、不带 key（额度 300 req/日/IP，这里只发个位数请求）。
"""
from __future__ import annotations

import gzip
import http.client
import io
import json
import pathlib
import sys
import time
import urllib.parse
import urllib.request

API = "https://api.stackexchange.com/2.3/questions"
# 每个 (site, tag) 一枪，pagesize 上限 100；键是站点，值是标签列表
PLAN = {
    "academia": ["research-process", "literature", "publication", "graduate-school",
                 "advisor", "research"],
    "stats": ["research-design", "modeling", "dataset", "cross-validation"],
    "or": ["paper", "research-papers", "advice"],
}
PER_TAG = 25
OUT = pathlib.Path(sys.argv[1] if len(sys.argv) > 1 else
                   "E:/AIC智能体迁移包/输出/data/external_questions.json")
# filter=withbody 太大（一页几 MB 且会淹掉标题），默认 filter 没有 title；这一串是官方
# "question 精简列" 命名 filter，字段够出处用且不带正文。
FILTER = "!nNPvSNdWme"


# 强制直连：这台机器的注册表里挂着一个代理，`urllib` 会自己去读（curl 不读），
# 走它会在 connect 阶段 10060 超时——同一时刻 `curl` 直连是通的（本轮实测）。
OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def get(params: dict[str, str]) -> list[dict]:
    url = f"{API}?{urllib.parse.urlencode(params)}"
    req = urllib.request.Request(url, headers={"User-Agent": "yanban-eval/1.0 (offline research)",
                                               "Accept-Encoding": "gzip"})
    last: Exception | None = None
    for attempt in range(3):          # 外部 API 会瞬断（本轮实测 RemoteDisconnected），整轮不白跑
        try:
            with OPENER.open(req, timeout=40) as r:
                raw = r.read()
                if r.headers.get("Content-Encoding") == "gzip" or raw[:2] == b"\x1f\x8b":
                    raw = gzip.GzipFile(fileobj=io.BytesIO(raw)).read()
            break
        except (OSError, http.client.HTTPException) as exc:
            last = exc
            print(f"  第 {attempt + 1} 次取 {params.get('site')}/{params.get('tagged')} 失败："
                  f"{type(exc).__name__}，2s 后重试", flush=True)
            time.sleep(2.0)
    else:
        raise RuntimeError(f"三次都取不动 {params}：{last}")
    data = json.loads(raw.decode("utf-8"))
    if data.get("backoff"):
        time.sleep(float(data["backoff"]) + 1.0)
    return data.get("items", [])


def main() -> int:
    rows: list[dict] = []
    seen: set[int] = set()
    for site, tags in PLAN.items():
        for tag in tags:
            items = get({"site": site, "tagged": tag, "order": "desc", "sort": "votes",
                         "pagesize": str(PER_TAG), "filter": FILTER})
            kept = dropped = 0
            for q in items:
                qid = int(q.get("question_id") or 0)
                title = (q.get("title") or "").strip()
                if not qid or qid in seen or len(title) < 8:
                    continue
                owner = q.get("owner") or {}
                # 出处缺一律不收：已注销/被封的账号会让 owner 整个消失，
                # 那样一条句子就退化成"一段没有来源的话"，署名栏空着的语料不能进池。
                if not owner.get("user_id") or not q.get("creation_date"):
                    dropped += 1
                    continue
                seen.add(qid)
                rows.append({
                    "user_text": title,                      # ← 原文，零加工
                    "question_id": qid, "site": site, "tag": tag,
                    "link": q.get("link", ""),
                    "owner_id": owner.get("user_id"),
                    "owner_name": owner.get("display_name"),
                    "created_utc": q.get("creation_date"),
                    "score": q.get("score"),
                    "license": "CC BY-SA 4.0",
                    "retrieved_utc": int(time.time()),
                })
                kept += 1
            print(f"{site}/{tag}: 取 {len(items)} 条，收 {kept} 条"
                  f"（出处不全丢 {dropped}）（累计 {len(rows)}）", flush=True)
    if not rows:
        print("判读：一条都没取到 ⇒ 网络或额度问题，不出数（RC=2）")
        return 2
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_bytes(json.dumps(rows, ensure_ascii=False, indent=1).encode("utf-8"))
    print(f"现场已存：{OUT}｜{len(rows)} 条｜不同提问 {len({r['user_text'] for r in rows})} 个")
    print("判读：外部语料已落地，判定请跑 verify_question_origin.py（这个脚本不判"
          "「是不是自家写的」）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
