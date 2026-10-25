"""#26 真人料池：把 B 站公开评论区的**逐字原话**收成一份带出处的料池文件（只读、限速、不登录、不采集用户名）。

轮 150—153 的账：线上库 1223 条用户消息 100% 来自自家池 ⇒ 产品日志不能当料源；公网十条路里
只有 B 站评论区同时满足「逐字 / 中文 / 带研究处境」。上一场（轮 153 收尾）量到 308 条、
第一人称线索 31 条，但只落在 `_scratch` 里，没有出处账、没有许可说明、普查仪器吃不到 ⇒ 本轮把它做成正式料池。

判据（跑前写死，不看数再定）：
  H0 形状门：BV 必须 `BV`+10（全长 12）。任一候选长度不对 ⇒ RC=2（轮 153 的 82 条 -400 就是这么来的）。
  H1 只读自证：只用 GET `x/v2/reply` / `x/v2/reply/reply`；不 POST、不带 cookie、不读 `member` 字段（用户名）。
  H2 防空转：取回 0 条正文 ⇒ RC=2，并写明「调用失败 ≠ 没有这种话」；失败按 code 分开统计（-352/-412/-400/网络）。
  H3 出处完整：每条必须带 bv + rpid + ctime + 可点开的链接；缺任一 ⇒ 不收进池子并计数。
  H4 逐字：入库文本 == 接口返回的 `content.message`（strip 之外零加工），并留一份 sha256 供对账。
  H5 分母：打印 原始条数 / ≥12 汉字 / 第一人称研究处境线索 / 线索里带疑问或求助形状的条数，两侧都给分母。
收尾行以「判读：」开头，聚合只在跑完整份之后打印。

用法：PYTHONUTF8=1 python 输出/scripts/harvest_bili_pool.py --out code/yanban/docs/evidence/g154_bili_pool.json
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import pathlib
import re
import sys
import time
import urllib.error
import urllib.request

UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124",
      "Referer": "https://www.bilibili.com/"}
HAN = re.compile(r"[一-鿿]")
# 第一人称 + 研究处境。轮 153 的口径（比它多留「我想/我在/我们老师/求推荐」这几类，扩集只加不减，
# 且新增的每一类都在读数里单独计数，免得把"换了尺子"读成"料变多了"）
FIRST_CORE = re.compile(r"我的导师|我导师|我的课题|我的方向|我们组|我这边|刚入学|我想发|想发.{0,4}(论文|SCI|CCF|会议|期刊)"
                        r"|开题|毕设|毕业论文|组会|中期|盲审|小论文|研[一二三]|我做的|我研究")
FIRST_EXTRA = re.compile(r"我们老师|我想问|求推荐|我在做|我目前在|我想做|我的论文|我的实验|我想读研|导师让我")
ASK = re.compile(r"[?？]|怎么|如何|有没有|能不能|该不该|求|帮忙|怎么办")


def api(path: str, errs: dict) -> dict:
    url = "https://api.bilibili.com/" + path
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=30) as r:
            return json.loads(r.read().decode("utf-8", "replace"))
    except urllib.error.HTTPError as e:
        errs[f"HTTP {e.code}"] = errs.get(f"HTTP {e.code}", 0) + 1
        return {"code": -900 - int(e.code)}
    except Exception as e:  # noqa: BLE001
        key = f"{type(e).__name__}:{str(getattr(e, 'reason', e))[:40]}"
        errs[key] = errs.get(key, 0) + 1
        return {"code": -1}


def rows_of(payload: dict) -> list[dict]:
    d = (payload.get("data") or {})
    return d.get("replies") or d.get("replies_extra") or d.get("root") or []


def norm(item: dict, kind: str, bv: str, aid: int, parent) -> dict | None:
    """逐字取 `content.message`；**不读 member**（用户名一律不进上下文、不进文件）。"""
    rpid = item.get("rpid") or item.get("number")
    msg = (((item.get("content") or {}).get("message")) or "")
    if not rpid or not msg.strip():
        return None
    text = msg.strip()
    return {
        "kind": kind, "bv": bv, "aid": aid, "rpid": int(rpid), "parent": parent,
        "url": f"https://www.bilibili.com/video/{bv}/#reply{int(rpid)}",
        "ctime": int(item.get("ctime") or 0),
        "like": int(item.get("like") or 0),
        "rcount": int(item.get("rcount") or 0),
        "text": text,
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--index", default="输出/_scratch/bili_pool.json",
                    help="上一场扫出来的视频清单（bv/aid/title/reply），不重扫索引页")
    ap.add_argument("--out", default="code/yanban/docs/evidence/g154_bili_pool.json")
    ap.add_argument("--min-reply", type=int, default=20)
    ap.add_argument("--max-videos", type=int, default=0, help="0 = 全部")
    ap.add_argument("--subreply-cap", type=int, default=140, help="楼中楼最多发这么多次（会被风控）")
    ap.add_argument("--sleep", type=float, default=1.25)
    a = ap.parse_args()

    idx = json.loads(pathlib.Path(a.index).read_text(encoding="utf-8"))
    vids = [v for v in idx["videos"] if int(v.get("reply") or 0) >= a.min_reply and v.get("aid")]
    bad = [v["bv"] for v in vids if len(v["bv"]) != 12 or not re.fullmatch(r"BV1[0-9A-Za-z]{9}", v["bv"])]
    print(f"H0 形状门：候选视频 {len(vids)} 条（reply≥{a.min_reply}）｜形状不合格 {len(bad)} 条")
    if bad:
        print(f"判读：{len(bad)} 个 BV 形状不对（{bad[:3]}）⇒ 上游清单坏了，读数作废（RC=2）")
        return 2
    if a.max_videos:
        vids = vids[:a.max_videos]
    if not vids:
        print("判读：没有可用视频条目 ⇒ 没测（RC=2）")
        return 2

    errs: dict[str, int] = {}
    calls = 0
    seen: set[int] = set()
    rows: list[dict] = []
    collected_at = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    for i, v in enumerate(vids, 1):
        bv, aid, title = v["bv"], int(v["aid"]), v["title"]
        # sort=2 按点赞（未登录只回前 3 条）、sort=0 默认序 ⇒ 同一视频两个不同的窗口
        for sort, kind in (("2", "top_like"), ("0", "default")):
            for pn in (1, 2, 3) if sort == "2" else (1, 2):
                d = api(f"x/v2/reply?type=1&oid={aid}&sort={sort}&pn={pn}&ps=20", errs)
                calls += 1
                if d.get("code") != 0:
                    errs[f"code={d.get('code')}"] = errs.get(f"code={d.get('code')}", 0) + 1
                    time.sleep(a.sleep * 2.5)
                    continue
                got = 0
                for it in rows_of(d):
                    r = norm(it, kind, bv, aid, None)
                    if r and r["rpid"] not in seen:
                        seen.add(r["rpid"])
                        r["video_title"] = title
                        rows.append(r)
                        got += 1
                if not got:
                    break                     # 这一页空 ⇒ 后面的页也空（未登录分页被掐）
                time.sleep(a.sleep)
        if i % 5 == 0:
            print(f"  …已扫 {i}/{len(vids)} 条视频，累计 {len(rows)} 条逐字评论，调用 {calls} 次，"
                  f"失败 {sum(errs.values())}", flush=True)

    # 楼中楼：唯一没被分页风控掐住的通道（root= 顶层 rpid），但连打会被限流 ⇒ 限量 + 更长间隔
    sub_calls = 0
    todo = [r for r in rows if r["rcount"] > 0]
    todo.sort(key=lambda r: -r["like"])
    for r in todo[:a.subreply_cap]:
        if sub_calls >= a.subreply_cap:
            break
        d = api(f"x/v2/reply/reply?type=1&oid={r['aid']}&root={r['rpid']}&ps=20", errs)
        sub_calls += 1
        if d.get("code") != 0:
            errs[f"sub code={d.get('code')}"] = errs.get(f"sub code={d.get('code')}", 0) + 1
            time.sleep(a.sleep * 3)
            continue
        for it in rows_of(d):
            n = norm(it, "sub", r["bv"], r["aid"], r["rpid"])
            if n and n["rpid"] not in seen:
                seen.add(n["rpid"])
                n["video_title"] = r["video_title"]
                rows.append(n)
        time.sleep(a.sleep * 1.6)

    print(f"\n调用：主评论 {calls} 次 + 楼中楼 {sub_calls} 次｜逐字取回 {len(rows)} 条")
    if not rows:
        print(f"判读：一条正文都没取到（失败按码 {errs}）⇒ 通道坏了，**不能**读成「评论区没有这种话」（RC=2）")
        return 2

    long_cn = [r for r in rows if len(HAN.findall(r["text"])) >= 12]
    core = [r for r in long_cn if FIRST_CORE.search(r["text"])]
    extra_only = [r for r in long_cn if not FIRST_CORE.search(r["text"]) and FIRST_EXTRA.search(r["text"])]
    asking = [r for r in core if ASK.search(r["text"])]
    print(f"H5 分母：原始 {len(rows)}｜≥12 汉字 {len(long_cn)}｜第一人称（轮 153 原口径）{len(core)}"
          f"｜只命中扩集词 {len(extra_only)}｜原口径里带疑问/求助形状 {len(asking)}")

    pool_items, dropped = [], 0
    for r in long_cn:
        if not (r["bv"] and r["rpid"] and r["ctime"] and r["url"]):
            dropped += 1
            continue
        pool_items.append({
            "user_text": r["text"],
            "source": "bilibili_comment",
            "url": r["url"], "bvid": r["bv"], "aid": r["aid"], "rpid": r["rpid"],
            "parent": r["parent"], "kind": r["kind"],
            "comment_ts": r["ctime"], "comment_time_utc":
                dt.datetime.fromtimestamp(r["ctime"], dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ") if r["ctime"] else "",
            "like": r["like"], "video_title": r["video_title"],
            "collected_at": collected_at,
            "text_sha256": hashlib.sha256(r["text"].encode("utf-8")).hexdigest()[:16],
            "clue_core": bool(FIRST_CORE.search(r["text"])),
            "clue_extra": bool(not FIRST_CORE.search(r["text"]) and FIRST_EXTRA.search(r["text"])),
            "has_question": bool(ASK.search(r["text"])),
        })
    if dropped:
        print(f"H3 出处完整性：{dropped} 条缺出处字段 ⇒ 不收进池子")

    out = pathlib.Path(a.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    doc = {
        "meta": {
            "name": "#26 真人中文料池（B 站公开评论区）",
            "collected_at_utc": collected_at,
            "method": "只读 GET x/v2/reply(sort=2 pn1-3 / sort=0 pn1-2) + x/v2/reply/reply?root=；"
                      "未登录、无 cookie、限速 ≥1.25s、失败按 code 统计不计为「没有这种话」",
            "exclusions": "不采集用户名/头像/粉丝数（代码根本不读 `member` 字段）；不做任何写操作；"
                          "≥12 汉字才入池（噪声/表情/纯链接不算料）",
            "known_bias": "🔴 未登录时 x/v2/reply 每页只回**按赞前 3 条**（pn≥2 回 0 但 page.count 是真总数），"
                          "x/v2/reply/main 被风控 -352；楼中楼是唯一没被掐的通道但连打会 -352/-412。"
                          "⇒ 这一池系统性偏向「高赞、且发生在评论数≥20 的考研/读研经验视频下」，"
                          "不是全体中文研究者的均匀抽样，胜率/放行率一类读数必须带着这句用",
            "license": "评论正文的版权归评论者本人；B 站用户协议未授予第三方再分发权。"
                       "本池**只用于本地离线评测**：不外发、不在产品界面或报告里转载原话、不展示用户名，"
                       "每条带可点开出处便于撤回；一旦要对外发布必须整池删除。",
            "counts": {"raw_rows": len(rows), "ge12_han_in_pool": len(pool_items),
                       "clue_core": len(core), "clue_extra_only": len(extra_only),
                       "clue_with_question": len(asking)},
            "api_failures_by_code": errs,
            "calls": {"main": calls, "sub": sub_calls},
        },
        "items": pool_items,
    }
    out.write_text(json.dumps(doc, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"\n料池落盘：{out}（{len(pool_items)} 条，sha256={hashlib.sha256(out.read_bytes()).hexdigest()[:16]}）")
    print("── 逐条目视：原口径线索里带疑问/求助的（前 20）──")
    for r in asking[:20]:
        print(f"  ◾ {r['bv']} rpid={r['rpid']} like={r['like']}｜{re.sub(chr(92) + 's+', ' ', r['text'])[:120]}")
    print("── 逐条目视：只命中扩集词的（前 8，看扩集买到了什么）──")
    for r in extra_only[:8]:
        print(f"  + {r['bv']} rpid={r['rpid']}｜{re.sub(chr(92) + 's+', ' ', r['text'])[:110]}")
    print("── 逐条目视：≥12 汉字但无线索的（前 8，负样本长什么样）──")
    for r in [x for x in long_cn if not FIRST_CORE.search(x["text"]) and not FIRST_EXTRA.search(x["text"])][:8]:
        print(f"  · {r['bv']} rpid={r['rpid']}｜{re.sub(chr(92) + 's+', ' ', r['text'])[:100]}")

    fails = sum(errs.values())
    print(f"\n判读：取回 {len(rows)} 条逐字评论、入池 {len(pool_items)} 条（≥12 汉字）"
          f"｜线索 core={len(core)}（占 ≥12 汉字的 {100.0 * len(core) / max(1, len(long_cn)):.1f}%）"
          f"｜调用失败 {fails} 次按码 {errs}"
          + f"｜{'读数可用作 #26 料源（带出处账与许可说明）' if pool_items else '空池'}（RC=0）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
