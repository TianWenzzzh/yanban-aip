"""反证句的位置与落地率：「必须落在首句」这一条到底让我们损失了多少？

为什么单独量这一条：P1 的闸门四次跑（`g75`—`g78`）15 次改写调用只落地 2 次，
退回原因每次都是 `编号挂上=True｜支持 ≥1｜相反 0` —— 引是引了，`survey` 在新**首句**上
数不出反证。而 `g76b` 的地板实验说得很清楚：尺子只看词面重合，中文转述与英文原文永不相交。
所以卡点可能是"位置要求"（反证被写在第二句以后），也可能是"写法要求"（整段不带原文词元）。
**这两种病因的修法相反**：前者该放宽核对对象（改闸门语义），后者该把引文单独成句（改提示词结构）。
在量出来之前，任何一条都不许先动手 —— 已经为"猜"付过一次重试的调用成本了。

口径（全部走产品自己的函数，不复制提示词）：
- 递进模型的 facts 用 `report._counterev_facts`（与线上同一段文本）；
- 一次改写调用之后，把改写结果切成句子，分别在 **首句 / 第二句 / 前两句 / 整节** 四个口径上跑
  `evidence.survey`，看有几个口径能过闸门的验收线（`against ≥ 1 且 checked ≥ 2`）。

⚠ 只测不改：本探针不写回正文、不动报告，生产库里只会多出对话项目（与 `g75`—`g78` 同一批已知污染）。
⚠ 部署里没有 `_counterev_facts` 时立刻退出 2 —— 那是"测不了"，不是"落地率为 0"。
⚠ 一个继承来的假设：探针用 `paper_memory.list_papers(pid)` 的顺序当角标编号，与产品建报告时同源。
   `g76` 三情景里"找料重放"选出的节名与闸门自报的节名逐个相同，这个假设才敢继续用；
   若哪天两边节名开始错位，先查这里。

预登记判读（跑之前写下，跑之后不改口径）：
- 若 **前两句或整节** 的通过率比首句高 ≥ 2 倍且绝对差 ≥ 20pp → 位置要求是主卡点，
  下一轮做「核对对象从首句换成该节新增句」的 A/B（会改动尺子，读数与 `g68` 之后不可比，须另立基线）。
- 若四个口径通过率都 ≈ 0 → 位置不是卡点，是**改写本身不带原文词元** → 走「引文单独成句」那条结构路。
- 若首句通过率就很高（≥ 50%）→ 说明单次调用的方差才是主因，那 P1 该做的是**多次采样取其一**，
  而不是"失败后重试"（这两件事成本相近，但后者已被 `g78` 证伪过一次，须小心）。

容器内跑：python /tmp/probe_counterev_position.py
"""
from __future__ import annotations

import json
import re
import sys
import time
import urllib.error
import urllib.request

sys.path.insert(0, "/app")

from companion.conversation import report as R        # noqa: E402
from companion.intent.model import ResearchIntent     # noqa: E402
from companion.literature import evidence             # noqa: E402
from companion.memory import paper_memory             # noqa: E402

API = "http://127.0.0.1:8041/companion"

# 与 probe_critique_depth 同一批情景，读数才对得上 g75—g78 那四跑
CASES = [
    ("我已经读了二十来篇多智能体协作的论文，想整理成一篇综述，但不知道按什么线组织",
     "我读的多是 MARL 协作算法，导师想让我投一个 CCF-B 的会议"),
    ("我在准备毕业论文，题目是医学图像分割，实验室只有两块 3080，怕算力不够",
     "数据是公开的 BraTS 脑肿瘤 MRI，导师要求今年底前给出实验结果"),
    ("我们实验室在做光伏功率预测，我想找一个还没人做的点",
     "有两年历史数据，15 分钟采样，想赶在明年春天投一篇"),
]

SECTIONS_PER_CASE = 2      # 每个情景最多测两节：首节 + 次弱节，控制调用成本


def post(path: str, body: dict, timeout: int = 600) -> dict:
    req = urllib.request.Request(API + path, data=json.dumps(body).encode("utf-8"),
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        try:
            detail = exc.read().decode("utf-8", "ignore")[:200]
        except Exception:  # noqa: BLE001
            detail = "（读不到响应体）"
        raise RuntimeError(f"HTTP {exc.code} {path} → {detail}") from None


def sents(text: str) -> list[str]:
    """按闸门同一套切句口径切分（`_first_sentence` 用的那条正则），只留 ≥12 字的句。"""
    return [s.strip() for s in re.split(r"(?<=[。！？!?\n])", text or "") if len(s.strip()) >= 12]


def positions(new_text: str) -> dict[str, str]:
    ss = sents(new_text)
    if not ss:
        return {}
    return {"首句": ss[0], "第二句": ss[1] if len(ss) > 1 else "",
            "前两句": " ".join(ss[:2]), "整节": " ".join(ss[:6])}


def score(claim: str, papers: list[dict]) -> dict:
    sv = evidence.survey(claim, papers) if claim else {"support": 0, "against": 0, "checked": 0}
    return {"against": sv["against"], "support": sv["support"], "checked": sv["checked"],
            "pass": sv["against"] >= 1 and sv["checked"] >= 2,
            "cited": bool(re.findall(r"\[(\d{1,2})\]", claim))}


rows: list[dict] = []
skipped: list[str] = []
calls = 0          # 真正拿到改写文本的次数（调用失败不计，别让分母虚高）

if not hasattr(R, "_counterev_facts"):
    print("❌ 部署里没有 report._counterev_facts —— 探针与线上版本不匹配，"
          "这条读数无效（不是 0 落地）。先部署再跑。")
    sys.exit(2)

# 本探针**自己**调 `_section_prompt` + max_tokens=900 复刻"整节重写"那条路（g87 就是这么量的）。
# 轮 111 起产品改成"只写一句、程序插到节首"，此后跑它得到的四个口径不对应任何线上代码——
# 一个看着像读数的幻影比没数更贵。认这个哨兵：有 `_counterev_sentence_prompt` = 线上已换路。
if hasattr(R, "_counterev_sentence_prompt"):
    print("❌ 线上已是轮 111 的『只写一句 + 程序插到节首』，而本探针复刻的是**整节重写**那条旧路。"
          "读数无效（不是 0 落地）——落地率请用 probe_critique_depth.py，它读闸门自己的上报。")
    sys.exit(2)

from llm import chat, run_async                        # noqa: E402

for case_i, (say, follow) in enumerate(CASES, 1):
    t0 = time.time()
    try:
        first = post("/chat", {"user_text": say})
        pid = first["project_id"]
        post("/chat", {"project_id": pid, "user_text": follow})
        papers = paper_memory.list_papers(pid)
        rep = post(f"/report/{pid}", {})
    except Exception as exc:  # noqa: BLE001 — 一个情景失败不许毁掉整份读数
        skipped.append(f"情景 {case_i}：{type(exc).__name__} {str(exc)[:110]}")
        print(f"--- 情景 {case_i} 失败：{type(exc).__name__} {str(exc)[:110]}", flush=True)
        continue
    body = rep.get("body") or rep.get("markdown") or rep.get("text") or ""
    intent = ResearchIntent(project_id=pid, topic=first.get("topic") or say[:40])
    cands = []
    for head, text in R.split_sections(body):
        if not head or head in R.LOCKED_SECTIONS or not text.strip():
            continue
        cov = evidence.grounding_report(text, papers)["coverage"]
        cited = {int(x) for x in re.findall(r"\[(\d{1,2})\]", text)}
        cands.append((cov if cov is not None else 2.0, head, text, cited))
    cands.sort(key=lambda c: c[0])
    tried = 0
    print(f"--- {say[:18]}… pid={pid} 论文 {len(papers)} 篇｜报告 {len(body)} 字｜{time.time() - t0:.0f}s",
          flush=True)
    for _cov, head, text, cited in cands:
        if tried >= SECTIONS_PER_CASE:
            break
        material = R._limitation_hits(papers, cited)
        if not material:
            continue
        tried += 1
        prompt = R._section_prompt(intent, papers, head, text, "", R._counterev_facts(material))
        t1 = time.time()
        try:
            raw = run_async(chat(prompt, system=R.SYSTEM, max_tokens=900,
                                 temperature=0.5, timeout=120.0))
        except Exception as exc:  # noqa: BLE001
            skipped.append(f"情景 {case_i}／{head}：调用 {type(exc).__name__}")
            print(f"    「{head[:12]}」改写调用失败 {type(exc).__name__}", flush=True)
            continue
        new_text = chr(10) + R.clean_report(raw, papers) + chr(10)
        calls += 1
        cov_new = evidence.grounding_report(new_text, papers)["coverage"]
        cited_ok = any(f"[{m['idx']}]" in new_text for m in material)
        print(f"    「{head[:14]}」有料 {len(material)} 篇｜编号挂上={cited_ok}"
              f"｜接地 {_cov}→{cov_new}｜{time.time() - t1:.0f}s", flush=True)
        for name, claim in positions(new_text).items():
            if not claim:
                print(f"        {name:5s} ——（该口径没有够长的句子，测不了）", flush=True)
                continue
            sc = score(claim, papers)
            rows.append({"case": case_i, "section": head, "pos": name, "cited_ok": cited_ok,
                         "cov_new": cov_new, **sc})
            print(f"        {name:5s} 相反 {sc['against']}｜支持 {sc['support']}｜核对 {sc['checked']}"
                  f"｜过验收线={'是' if sc['pass'] else '否'}｜带角标={'是' if sc['cited'] else '否'}"
                  f"｜{claim[:34]}", flush=True)

print("\n=== 汇总（每行 = 一次改写调用 × 一个核对口径）===")
if not rows:
    print(f"一份改写都没测成（跳过 {len(skipped)} 条：{skipped}）—— 没有数可印，不是 0%")
    sys.exit(1)
print(f"改写调用 {calls} 次｜口径读数 {len(rows)} 条")
for pos in ("首句", "第二句", "前两句", "整节"):
    sub = [r for r in rows if r["pos"] == pos]
    if not sub:
        print(f"  {pos:5s}：（无读数）")
        continue
    passed = sum(1 for r in sub if r["pass"] and r["cited_ok"])
    print(f"  {pos:5s}：过验收线（含「编号挂上」）{passed}/{len(sub)} = {passed / len(sub):.0%}")
first_only = {(r["case"], r["section"]) for r in rows if r["pos"] == "首句" and r["pass"] and r["cited_ok"]}
anywhere = {(r["case"], r["section"]) for r in rows if r["pass"] and r["cited_ok"]}
print(f"\n只看首句能落地 {len(first_only)} 节；放开到任意口径能落地 {len(anywhere)} 节（改写调用 {calls} 次）")
print("判读见文件头「预登记判读」三条，别在看完数之后再改口径。")
if skipped:
    print(f"⚠ 中途失败 {len(skipped)} 条：{skipped}")
