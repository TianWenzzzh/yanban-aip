"""综述报告的「批判深度」基线测量：相反证据率 + 分节接地率。

为什么要先量：DeepSurvey-Bench 说现有系统最弱的是批判性分析而不是排版，我的日志里也反复写着
「缺口/批判这类节最容易写成套话」。但"批判深度"如果不落到一个机械可核对的数上，
做完就没法判断有没有变好——所以先定义口径：

- **相反证据率**：每一节取该节的首句作为论断，用 `evidence.survey()` 在项目论文库里数
  有几篇原文与它词面重合、其中有几篇重合处附近带否定/局限标记（`NEGATION_RE`）。
  至少 1 篇相反 → 该节"有相反证据"。全部节里这样的节占比就是相反证据率。
- **分节接地率**：`evidence.grounding_by_section()` 已有的 coverage 与 weakest。

两个数都**不需要 LLM 评委**（本项目实测评委对编造依据是瞎的：换位置一致率 46%、
虚构锚点只抓到 30%）。容器内跑：python /tmp/probe_critique_depth.py

⚠ survey 的"相反"只是词面+否定标记，不是蕴含判定；这个口径能比较前后，
不能当成"这节真的批判了"。
"""
from __future__ import annotations

import json
import re
import sys
import time
import urllib.error
import urllib.request

sys.path.insert(0, "/app")

from companion.literature import evidence                     # noqa: E402
from companion.memory import paper_memory                     # noqa: E402

API = "http://127.0.0.1:8041/companion"

CASES = [
    ("我已经读了二十来篇多智能体协作的论文，想整理成一篇综述，但不知道按什么线组织",
     "我读的多是 MARL 协作算法，导师想让我投一个 CCF-B 的会议"),
    ("我在准备毕业论文，题目是医学图像分割，实验室只有两块 3080，怕算力不够",
     "数据是公开的 BraTS 脑肿瘤 MRI，导师要求今年底前给出实验结果"),
    ("我们实验室在做光伏功率预测，我想找一个还没人做的点",
     "有两年历史数据，15 分钟采样，想赶在明年春天投一篇"),
]



def my_sections(body: str) -> list[tuple[str, str]]:
    """探针自己按 `## ` 切节，顺便拿到每节正文。

    `grounding_by_section` 只返回统计不返回正文，而 survey 需要该节的论断（首句）。
    不在产品函数里塞正文字段——那是为了测量去改产品接口。
    """
    out: list[tuple[str, list]] = [("（开头）", [])]
    for line in (body or "").splitlines():
        m = re.match(r"^##\s+(.+)$", line)
        if m:
            out.append((m.group(1).strip()[:40], []))
        else:
            out[-1][1].append(line)
    nl = chr(10)
    return [(h, nl.join(t).strip()) for h, t in out if nl.join(t).strip()]


def post(path: str, body: dict, timeout: int = 600) -> dict:
    req = urllib.request.Request(API + path, data=json.dumps(body).encode("utf-8"),
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        # 情景 3 上一轮只留下「HTTP 400」四个字，查不到是被哪条拒绝路径挡的；把响应体带出来
        try:
            detail = exc.read().decode("utf-8", "ignore")[:200]
        except Exception:  # noqa: BLE001
            detail = "（读不到响应体）"
        raise RuntimeError(f"HTTP {exc.code} {path} → {detail}") from None


def lead_sentence(section_body: str) -> str:
    for raw in re.split(r"(?<=[。！？\n])", section_body or ""):
        s = raw.strip().lstrip("#->* ").strip()
        if len(s) >= 12:
            return s[:160]
    return ""


def flag(rep: dict, key: str) -> str:
    """把「响应里没这个键」和「键在但是空列表」分开印。

    两者含义相反：键缺失 = 探针和线上版本不匹配，这个读数无效；空列表 = 闸门确实没动手。
    混成一个「（无）」就会把测不到说成没效果。
    """
    if key not in rep:
        return "（键缺失·读数无效）"
    v = rep[key]
    return "、".join(v) if v else "（空）"


def gate_replay(rep: dict, body: str, papers: list[dict]) -> str:
    """离线重放反证闸门的「找料」那一步，用来把 0 反证拆成 没料 / 被自己退回 / 已落地。

    为什么能重放：找料是纯函数（不叫模型），选节排序也照抄闸门，所以这里跑的是同一段代码，
    只是没走到那一次改写调用。料在、闸门却没上报改过 = 被自己的复算退回或调用抛错；
    闸门上报改了 = 落地（闸门末尾本来就用 survey 复算过 against≥1，落地必然数得出）。
    """
    from companion.conversation import report as R

    if not R._pool_has_text(papers):
        return "找料：整池无可读原文（闸门直接跳过）"
    cands = []
    for head, text in R.split_sections(body):
        if not head or head in R.LOCKED_SECTIONS or not text.strip():
            continue
        cov = evidence.grounding_report(text, papers)["coverage"]
        cited = {int(x) for x in re.findall(r"\[(\d{1,2})\]", text)}
        cands.append((cov if cov is not None else 2.0, head, cited))
    cands.sort(key=lambda c: c[0])
    tail = (f"｜闸门上报落地 {flag(rep, 'sections_counterev')}"
            f"｜闸门说明 {'；'.join(rep['counterev_note']) or '（空=落地了，无需解释）'}"
            if "counterev_note" in rep else "｜线上响应里没有 counterev_note 键（读数无效，先查部署）")
    for _cov, head, cited in cands:
        hits = R._limitation_hits(papers, cited)
        if hits:
            return (f"找料：节「{head[:12]}」有料 {len(hits)} 篇{[h['idx'] for h in hits]}"
                    + tail)
    return f"找料：{len(cands)} 节所引各篇全无局限句" + tail


rows = []
for case_i, (say, follow) in enumerate(CASES, 1):
    t0 = time.time()
    try:
        first = post("/chat", {"user_text": say})
        pid = first["project_id"]
        post("/chat", {"project_id": pid, "user_text": follow})
        papers = paper_memory.list_papers(pid)
        rep = post(f"/report/{pid}", {})
    except Exception as exc:  # noqa: BLE001 — 单个情景失败不许毁掉整份基线
        print(f"--- 情景 {case_i} 失败：{type(exc).__name__} {str(exc)[:120]}", flush=True)
        continue
    body = rep.get("body") or rep.get("markdown") or rep.get("text") or ""
    if not body:      # 拿不到正文就别把「没数据」印成「批判深度为零」
        print("    报告响应里没有正文，键有:", sorted(rep.keys())[:12], flush=True)
        continue
    print(f"--- {say[:18]}… pid={pid} 论文 {len(papers)} 篇｜报告 {len(body)} 字"
          f"｜{time.time() - t0:.0f}s"
          f"｜闸门补引用的节 {flag(rep, 'sections_cited')}", flush=True)
    for head, body_txt in my_sections(body):
        gr = evidence.grounding_report(body_txt, papers)
        claim = lead_sentence(body_txt)
        sv = evidence.survey(claim, papers) if claim else {"support": 0, "against": 0, "checked": 0}
        # 「引用论文」是程序按论文清单拼出来的，不是正文：它的首"句"是一条标题，
        # 标题与别篇摘要重合两词 + 那句恰好带否定标记，就会被数成反证（g68 的 11% 就是这么来的）。
        # 这类节照印出来对眼，但不进分子也不进分母。
        auto = head == "引用论文"
        if not auto:
            rows.append({"pid": pid, "section": head, "cov": gr.get("coverage"),
                         "cited": gr.get("cited_sentences", 0), "support": sv["support"],
                         "against": sv["against"], "checked": sv["checked"]})
        print(f"    {head[:22]:24s} 接地 {str(gr.get('coverage')):>5s}"
              f"｜引用句 {gr.get('cited_sentences', 0)}｜支持 {sv['support']}"
              f"｜相反 {sv['against']}｜核对 {sv['checked']} 篇"
              f"{'' if auto else '｜计入'}｜首句 {claim[:40]}", flush=True)
    print("    " + gate_replay(rep, body, papers), flush=True)

if not rows:
    print("=== 汇总 ===\n三个情景全部失败，一份报告都没拿到 —— 没有数可印（不是 0%）")
    sys.exit(1)
have = [r for r in rows if r["checked"]]
print("\n=== 汇总 ===")
print(f"节数 {len(rows)}（其中库里有多篇可核对的 {len(have)}）")
with_cite = sum(1 for r in rows if r["cited"] >= 1)
print(f"**带角标的节 = {with_cite}/{len(rows)} = "
      f"{with_cite / len(rows):.0%}｜角标句总数 {sum(r['cited'] for r in rows)}**"
      "　（分子分母可与上面逐行「引用句」对眼核对）")
if have:
    with_against = sum(1 for r in have if r["against"] >= 1)
    print(f"**相反证据率 = {with_against}/{len(have)} = {with_against / len(have):.0%}**")
    covs = [r["cov"] for r in have if r["cov"] is not None]
    if covs:
        print(f"分节接地率中位 {sorted(covs)[len(covs) // 2]:.2f}；"
              f"接地为 0 的节 {sum(1 for c in covs if c == 0)}/{len(covs)}")
    print(f"支持数中位 {sorted(r['support'] for r in have)[len(have) // 2]}")
print("\n判读：相反证据率就是 P2 的基线。若已经很高，P2 不必做；"
      "若接近 0，就说明报告的批判节确实是在没有反证的情况下写出来的。")
