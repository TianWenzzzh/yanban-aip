"""不经 LLM 评委的净贡献量具：回复里**具名论文标题**断言的"外部查得到率"。

为什么换这把尺：判断题仪表与配对盲评都在锚点对检验里没过线（`g84` 2/3、`g85` 0/3、`g86` 2/3），
所以评委印的任何一个胜率都不能用来谈净贡献。但这台机器不需要评委：
「这篇论文存不存在」是可外部核对的事实，判据只有词元重合，跑一次记一次、可复现可逐条对眼。

口径（两条独立的数，不许混成一句"更好"）：
- **发不發**：每条回复里《…》式具名标题断言的条数（`claim_rate`）。认知层（真检索）结构上才有东西可写，
  等长强基线只拿走检索/记忆/意图，所以这是**机制差**而不是质量差。
- **真不真**：这些标题拿去 Crossref 查，取前 5 条命中里最好的词元重合率，≥0.75 判 FOUND（`verify_rate`）。
  这是**诚实性**读数。两条各自成立才谈得上"净贡献"，任何一条单独拿去做产品决策都是越界。

⚠ 已知限制（写在屏幕上，不许只在注释里）：
- Crossref 覆盖不全（很多 CS 论文只在 arXiv/IEEE），**查不到 ≠ 编造**；所以这个数只能比较两臂，
  不能当"真实率"的绝对值报。强基线若干脆不发具名标题，它就没有可核对对象 → 报数时必须同时报条数。
- 中文《…》（无 ≥4 个拉丁词元）判不了，单独计数，不进分子也不进分母。

自检（`--selfcheck`，不过就 exit 1 拒绝出数）：一条**逐字来自本仓论文池**的真标题必须 FOUND；
一条测试里用过的**编造标题**必须 NOT_FOUND。两条都过才允许跑真读数。

用法：
    python 输出/scripts/verify_title_claims.py --selfcheck
    python 输出/scripts/verify_title_claims.py <证据文件> [更多证据文件...]
"""
from __future__ import annotations

import json
import math
import pathlib
import re
import sys
import time
import urllib.parse
import urllib.request

HERE = pathlib.Path(__file__).resolve().parent
CACHE = HERE / "title_verify_cache.json"
STOP = {"a", "an", "the", "of", "and", "on", "in", "for", "to", "with", "via", "based",
        "using", "towards", "toward", "at", "is", "are", "from", "by"}
TITLE_RE = re.compile(r"《([^》]{6,160})》")
LATIN_RE = re.compile(r"[A-Za-z][A-Za-z0-9+'.\-]{2,}")
MATCH_MIN = 0.75
MIN_TOKENS = 4
UA = {"User-Agent": "yanban-verify/1.0 (mailto:1539274452@qq.com)"}


def norm(text: str) -> str:
    return re.sub(r"[^a-z0-9 ]", " ", (text or "").lower())


def tokens(text: str) -> set[str]:
    return {t for t in norm(text).split() if t and t not in STOP}


def classify(claim: str) -> tuple[str, str, float]:
    """→ (判定, 依据说明, 最佳重合率)。判定 ∈ {FOUND, NOT_FOUND, 判不了}。"""
    ct = tokens(claim)
    if len([t for t in re.findall(r"[A-Za-z][A-Za-z0-9+'\-]*", claim) if len(t) >= 3]) < MIN_TOKENS:
        return "判不了", f"拉丁词元 <{MIN_TOKENS}（中文/短语不判）", 0.0
    cache = json.loads(CACHE.read_text(encoding="utf-8")) if CACHE.exists() else {}
    key = norm(claim).replace("  ", " ").strip()
    if key not in cache:
        q = urllib.parse.urlencode({"query.bibliographic": claim, "rows": 5,
                                    "select": "title"})
        try:
            req = urllib.request.Request(
                f"https://api.crossref.org/works?{q}", headers=UA)
            with urllib.request.urlopen(req, timeout=30) as r:
                items = json.loads(r.read().decode("utf-8"))["message"]["items"]
            hits = [(x.get("title") or [""])[0] for x in items]
        except Exception as exc:  # noqa: BLE001 — 网络失败是"测不了"，不能记成 NOT_FOUND
            return "测不了", f"{type(exc).__name__} {str(exc)[:60]}", 0.0
        cache[key] = hits
        CACHE.write_text(json.dumps(cache, ensure_ascii=False), encoding="utf-8")
        time.sleep(0.4)
    best, best_t = 0.0, ""
    for h in cache[key]:
        ht = tokens(h)
        if not ht:
            continue
        ov = len(ct & ht) / max(1, len(ct))
        if ov > best:
            best, best_t = ov, h
    if best >= MATCH_MIN:
        return "FOUND", best_t[:80], best
    return "NOT_FOUND", f"最佳重合 {best:.2f}：{best_t[:60]}", best


def load_pairs(paths: list[str]) -> list[dict]:
    """吃证据转储里的 PAIRS_JSON 行；同一文件只算一次（glob 会重复命中）。"""
    recs: list[dict] = []
    for p in {str(pathlib.Path(x).resolve()) for x in paths}:
        for line in pathlib.Path(p).read_text(encoding="utf-8", errors="ignore").splitlines():
            s = line.strip()
            if s.startswith("[") and "strong_outcome" in s:
                recs.extend(json.loads(s))
    return recs


# ── 自检夹具：一条真标题（逐字取自本仓测试里用的真件）+ 一条编造标题 ──────────────
GOOD_TITLE = ("Graph neural networks for natural language processing: A survey")
BAD_TITLE = ("Deep transformer fusion of hyperspectral LiDAR for phishing detection "
             "in quantum networks")


def selfcheck() -> int:
    print("=== 自检：一真一假两条夹具 ===")
    bad = 0
    g, gwhy, gs = classify(GOOD_TITLE)
    print(f"  真标题 → {g}（{gs:.2f}）{gwhy}")
    if g != "FOUND":
        print("  ❌ 真标题没判成 FOUND：判据太严，聚合数会低估真实率")
        bad += 1
    b, bwhy, bs = classify(BAD_TITLE)
    print(f"  编造标题 → {b}（{bs:.2f}）{bwhy}")
    if b != "NOT_FOUND":
        print("  ❌ 编造标题没判成 NOT_FOUND：这把尺分不清真假，聚合数无意义")
        bad += 1
    print("自检 " + ("全部通过" if not bad else f"{bad} 条不通过"))
    return 1 if bad else 0


def main(argv: list[str]) -> int:
    if "--selfcheck" in argv:
        return selfcheck()
    if not argv:
        print("用法：verify_title_claims.py --selfcheck | <证据文件> [...]")
        return 2
    recs = load_pairs(argv)
    if not recs:
        print(f"没从这些文件里读到 PAIRS_JSON 记录（{argv}）—— 没有数可印，不是 0%")
        return 1
    print(f"读到配对记录 {len(recs)} 条（同一情景重复份都在内，逐条对眼见下方清单）")
    per_pair: list[tuple[int, int]] = []
    stats = {arm: {"pairs": 0, "claims": 0, "found": 0, "nf": 0, "skip": 0, "err": 0,
                   "min_found": 1.0, "long": 0}
             for arm in ("yanban", "strong")}
    lines: list[str] = []
    for i, r in enumerate(recs, 1):
        pair_claims = {"yanban": 0, "strong": 0}
        for arm in stats:
            body = r.get(arm) or ""
            stats[arm]["pairs"] += 1
            for claim in TITLE_RE.findall(body):
                pair_claims[arm] += 1
                stats[arm]["claims"] += 1
                if len(claim) > 120:      # 超长《》多半是把一整句套进了书名号，单独计数
                    stats[arm]["long"] += 1
                verdict, why, score = classify(claim)
                if verdict == "FOUND":
                    stats[arm]["found"] += 1
                    stats[arm]["min_found"] = min(stats[arm]["min_found"], score)
                elif verdict == "NOT_FOUND":
                    stats[arm]["nf"] += 1
                elif verdict == "判不了":
                    stats[arm]["skip"] += 1
                else:
                    stats[arm]["err"] += 1
                lines.append(f"  [{i:>2}] {arm:6s} {verdict:9s} 重合 {score:.2f}｜"
                             f"{len(claim):>3d} 字 {claim[:56]}"
                             f"｜{why[:52]}")
        per_pair.append((pair_claims["yanban"], pair_claims["strong"]))
    print(chr(10).join(lines) or "  （一条具名标题断言都没有）")
    print("\n=== 两条各自独立的读数 ===")
    for arm in stats:
        s = stats[arm]
        n = s["pairs"] or 1
        judged = s["found"] + s["nf"]
        print(f"{arm:7s}：标题断言 {s['claims']} 条（条均 {s['claims'] / n:.2f}，其中 >120 字 {s['long']} 条）"
              f"｜判不了 {s['skip']}｜网络失败 {s['err']}"
              f"｜可判 {judged} 条里 FOUND {s['found']} = "
              f"{(s['found'] / judged if judged else 0):.0%}"
              f"｜FOUND 里最低重合 {s['min_found']:.2f}")
    y_wins = sum(1 for a, b in per_pair if a > b)
    s_wins = sum(1 for a, b in per_pair if b > a)
    tie = len(per_pair) - y_wins - s_wins
    n_diff = y_wins + s_wins
    # 精确双边符号检验（数必须脚本自己印，不许我在屏幕上手算——本项目已犯过两次）
    p = 0.0
    if n_diff:
        k = min(y_wins, s_wins)
        tail = sum(math.comb(n_diff, i) for i in range(k + 1)) / (2 ** n_diff)
        p = min(1.0, 2 * tail)
    print(f"逐对比较（只看条数）：2.0 更多 {y_wins}｜强基线更多 {s_wins}｜并列 {tie}"
          f"　符号检验 n(不等)={n_diff} 精确双边 p={p:.4f}"
          f"（**这只说明「具名断言的条数」不同，不含好坏**）")
    print("⚠ 判据的已知失效：记错的真标题会落进 NOT_FOUND（分不出「编造」与「记错」）；"
          "而 2.0 的标题本就取自 Crossref 检索 → 这支尺子**查不出「论文存在但不支撑该论断」**。")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
