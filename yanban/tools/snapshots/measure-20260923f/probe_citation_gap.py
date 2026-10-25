"""引用「接得住吗」分层体检：错编号 vs 真缺证据（**逐句**归因 + 与产品对账）。

动机（一轮实测留下的问号）：只报一个"接地率 70%"会把两种处置相反的失败混在一起——
  A. 这句在本轮**别的**论文卡里能找到依据 → 只是角标挂错了篇，重编号就能修，检索不用再跑；
  B. 这句在**池里任何一篇**里都找不到 → 才是真的证据缺口，需要针对这句话二次检索。
产品自己的 `evidence.grounding_report` 早就把这两类分开了（misnumbered / true_gap），
所以本探针**不另造一把尺**：它补的是产品给不了的两件事——
① 逐句印出到底是哪一句挂错了篇（产品只给合计，合计没法拿去改机制）；
② 每份回复当场与产品合计**对账**，对不上就 RC=2 作废（尺复刻歪了却印出一张像模像样的分布表，
   是这类"复刻产品逻辑"的探针最容易犯的错，见轮 112 的认路哨兵）。

容器内跑：python /tmp/probe_citation_gap.py [每情景轮数]
本地自检：python probe_citation_gap.py --selfcheck（不需要云端、不需要 GLM）
退出码：0=可对账且读数可读｜1=自检未过｜2=与产品对不上账（仪器不工作，不是产品变差）
走真链路（/companion/chat），只看回复与当轮论文卡，不碰模型内部。
"""
from __future__ import annotations

import json
import os
import re
import sys
import time
import urllib.request

sys.path.insert(0, "/app")
sys.path.insert(0, ".")      # 自检不该依赖云端：在 code/yanban 下也能本地跑

from companion.literature import evidence   # noqa: E402

# 容器里另存一份输出：本地 SSH 管道只是镜像，它断了不代表探针断了（g90 整份读数就是这么丢的）。
# 断线后用 `fetch_probe_output.py <本地路径>` 取回，并由「有没有汇总」判跑没跑完。
_TEE = os.environ.get("PROBE_TEE") or (
    f"/tmp/probe_out_{os.path.basename(__file__)[:-3]}.txt" if os.name == "posix" else None)
if _TEE:
    class _Tee:
        def __init__(self, path: str) -> None:
            self.f = open(path, "w", encoding="utf-8")

        def write(self, s: str) -> int:
            self.f.write(s)
            self.f.flush()
            return sys.__stdout__.write(s)

        def flush(self) -> None:
            self.f.flush()

    sys.stdout = _Tee(_TEE)     # type: ignore[assignment]
    print(f"边车（本地管道断了就取回这份）：{_TEE}", flush=True)

API = "http://127.0.0.1:8041/companion"

CASES = [
    ("我在准备毕业论文，题目是医学图像分割，实验室只有两块 3080，怕算力不够", "不太会调参，数据是公开的"),
    ("我已经读了二十来篇多智能体协作的论文，想整理成综述，但不知道按什么线组织", "导师想让我投 CCF-B"),
    ("我第一次做科研，想做多模态钓鱼网站检测，深度学习只学过一点", "只有一台轻薄本，没有 GPU"),
    ("我们实验室在做光伏功率预测，我想找一个还没人做的点", "有两年历史数据，15 分钟采样"),
    ("导师让我把强化学习用在多机器人调度上，两周给他一个可行性判断", "仿真环境是 ROS，没有实车"),
]


def post(path: str, body: dict) -> dict:
    req = urllib.request.Request(API + path, data=json.dumps(body).encode("utf-8"),
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=300) as r:
        return json.loads(r.read().decode("utf-8"))


def cited_sentences(text: str) -> list[tuple[str, list[int]]]:
    """带角标的句子 + 该句引用的论文序号（0 基）。"""
    out = []
    for raw in re.split(r"(?<=[。！？!?\n])", text or ""):
        s = raw.strip()
        if len(s) < 8:
            continue
        idxs = [int(m) - 1 for m in re.findall(r"\[(\d{1,2})\]", s)]
        if not idxs and "《" not in s:
            continue
        out.append((s, [i for i in idxs if 0 <= i]))
    return out


def classify(sent: str, papers: list[dict], idxs: list[int], rare) -> str:
    """grounded_cited=依据确在被引篇｜misnumbered=依据在池里别处｜no_evidence=整池都没有。

    🔴 必须与产品那把尺同口径：`grounding_report` 传的是 `rare=rare_tokens(papers)`，
    这里若漏传 `rare`，套话重合（"实验结果表明性能提升"）会被判成"接得住"，
    探针于是系统性地比产品乐观 —— 同一份产物印出两个"接地率"就是 g68/今天的 22% 那种事故。
    所以本函数只负责**逐句**给类别（产品只给合计），合计由 `--crosscheck` 那一步对账。
    """
    pool = [papers[i] for i in idxs if 0 <= i < len(papers)] or list(papers)
    if any(evidence.best_spans(sent, p, rare=rare) for p in pool):
        return "grounded_cited"
    if any(evidence.best_spans(sent, p, rare=rare) for p in papers):
        return "misnumbered"
    return "no_evidence"


BOILER = "In this paper we propose a deep learning framework and experimental results show " \
         "performance improvement on the benchmark dataset."
FIX_PAPERS = [
    {"title": "Zebra finch telescope calibration",
     "abstract": "The zebra finch telescope uses a quartz mirror for calibration, but the quartz "
                 "mirror scratches easily. " + BOILER},
    {"title": "Aurora magnetometer campaign",
     "abstract": "The aurora magnetometer campaign recorded daylight spectra. " + BOILER},
    {"title": "Cooperative quota scheduling",
     "abstract": "Cooperative quota scheduling balances the magnetometer load across arrays. " + BOILER},
]
# 每条 = (句子, 期望类别, 为什么是期望值)
FIXTURES = [
    ("石英镜面确实容易划伤（quartz mirror scratches easily）[1]", "grounded_cited",
     "稀有词全在第 1 篇原文里"),
    ("日光谱记录依赖极光磁强计（aurora magnetometer daylight spectra）[1]", "misnumbered",
     "依据只在第 2 篇，角标挂错篇"),
    ("该方法的贝叶斯先验靠人工设定（bayesian prior handcrafted）[1]", "no_evidence",
     "整池都没有这些词"),
    ("实验结果表明性能有提升（experimental results show performance improvement）[1]",
     "no_evidence", "只与三篇共有的套话重合，`rare_tokens` 一律丢掉"),
]


def selfcheck() -> int:
    """手算夹具：三篇里必须 ≥3 篇，否则 `rare_tokens` 整条关掉、四类全判成"接得住"（轮 112 踩过）。"""
    if len(FIX_PAPERS) < 3:
        print("❌ 夹具池不足 3 篇，`rare_tokens` 会返回 None —— 这把尺当场失效")
        return 2
    rare = evidence.rare_tokens(FIX_PAPERS)
    print(f"=== --selfcheck（手算夹具：{len(FIXTURES)} 句 / {len(FIX_PAPERS)} 篇）===")
    bad = 0
    for sent, want, why in FIXTURES:
        got = classify(sent, FIX_PAPERS, [int(m) - 1 for m in re.findall(r"\[(\d{1,2})\]", sent)], rare)
        ok = got == want
        bad += 0 if ok else 1
        print(f"  {'✅' if ok else '❌'} 期望 {want:14s} 实得 {got:14s}｜{why}｜{sent[:44]}")
    body = "\n".join(s for s, _, _ in FIXTURES)
    g = evidence.grounding_report(body, FIX_PAPERS)
    mine = {"grounded_cited": 0, "misnumbered": 0, "no_evidence": 0}
    for sent, want, _ in FIXTURES:
        mine[classify(sent, FIX_PAPERS,
                      [int(m) - 1 for m in re.findall(r"\[(\d{1,2})\]", sent)], rare)] += 1
    same = (g["grounded"] == mine["grounded_cited"] and g["misnumbered"] == mine["misnumbered"]
            and g["true_gap"] == mine["no_evidence"])
    print(f"  {'✅' if same else '❌'} 与产品 `grounding_report` 同口径"
          f"｜产品 {g['grounded']}/{g['misnumbered']}/{g['true_gap']}"
          f"＝探针 {mine['grounded_cited']}/{mine['misnumbered']}/{mine['no_evidence']}")
    bad += 0 if same else 1
    # 负对照：把 rare 摘掉（旧口径）必须让第 4 句"假接住"，否则说明这条差值压根没被夹具测到
    legacy = classify(FIXTURES[3][0], FIX_PAPERS, [0], None)
    caught = legacy == "grounded_cited"
    print(f"  {'✅' if caught else '❌'} 负对照：摘掉 `rare`（旧口径）后套话句被判成 {legacy}"
          f"　——这就是要挡的假绿形状，夹具能复现它，上面那条 no_evidence 才是测到了真东西")
    bad += 0 if caught else 1
    print(f"自检 {6 - bad}/6 通过")
    return 1 if bad else 0


def main() -> int:
    if "--selfcheck" in sys.argv:
        return selfcheck()
    reps = int(sys.argv[1]) if len(sys.argv) > 1 else 1
    # 现场另存一份（默认 /tmp，本地复盘用 CITEGAP_FILE 换路径）：
    # 新机制（错挂角标重编号）要能离线测试，不然每次改动都得再烧一轮云端调用。
    fixture_path = os.environ.get("CITEGAP_FILE") or "/tmp/citegap.json"
    fixture: list[dict] = []
    tally = {"grounded_cited": 0, "misnumbered": 0, "no_evidence": 0}
    disagree = 0
    per_reply: list[tuple[int, int]] = []
    stats_rows: list[tuple[int, int, int]] = []
    made: list[str] = []
    for say, extra in CASES:
        for _ in range(reps):
            t0 = time.time()
            first = post("/chat", {"user_text": say})
            pid = first["project_id"]
            made.append(pid)
            second = post("/chat", {"project_id": pid, "user_text": extra})
            reply, papers = second.get("reply") or "", second.get("papers") or []
            # 外呼/缓存计数**不在这里读**：探针是走 HTTP 打给 uvicorn 进程的，
            # provider_stats() 在探针自己的进程里恒为 0（上一版就印出了一排假的「外呼 0 次」）。
            # 想看真实外呼，去容器日志数 `provider .* 不可用` / `熔断中，本轮跳过`。
            stats_rows.append((len(papers), str(second.get("state") or ""), int(time.time() - t0)))
            secs = cited_sentences(reply)
            rare = evidence.rare_tokens(papers)
            kinds = [classify(s, papers, i, rare) for s, i in secs]
            for k in kinds:
                tally[k] += 1
            # 与产品自己的合计对账：两份不一致 = 我把尺复刻歪了，这份分布表不能读（RC=2）。
            g = evidence.grounding_report(reply, papers)
            mine = {"grounded": kinds.count("grounded_cited"),
                    "misnumbered": kinds.count("misnumbered"), "true_gap": kinds.count("no_evidence")}
            off = (mine["grounded"] != g["grounded"] or mine["misnumbered"] != g["misnumbered"]
                   or mine["true_gap"] != g["true_gap"])
            disagree += 1 if off else 0
            per_reply.append((len(secs), kinds.count("grounded_cited")))
            fixture.append({"say": say, "reply": reply, "kinds": kinds,
                            "cited": [s for s, _ in secs],
                            "idxs": [i for _, i in secs],
                            "product": {k: g[k] for k in ("cited_sentences", "grounded",
                                                          "misnumbered", "true_gap")},
                            "papers": [{"title": str(p.get("title") or "")[:200],
                                        "abstract": str(p.get("abstract") or "")[:900]}
                                       for p in papers]})
            print(f"--- {say[:20]}…（{time.time() - t0:.0f}s）论文 {len(papers)} 篇｜带角标句 {len(secs)}"
                  f"｜产品 {g['grounded']}/{g['misnumbered']}/{g['true_gap']}"
                  f"｜探针 {mine['grounded']}/{mine['misnumbered']}/{mine['true_gap']}"
                  f"{'　❌两份不一致' if off else ''}", flush=True)
            for (s, i), k in zip(secs, kinds):
                if k != "grounded_cited":
                    print(f"    [{k}] {s[:110]}", flush=True)
    tot = sum(tally.values())
    print("\n=== 汇总（按带角标句计）===")
    for k, v in tally.items():
        print(f"{k:16s} {v:3d}  {v / tot:.0%}" if tot else k)
    fixable = tally["misnumbered"]
    real_gap = tally["no_evidence"]
    cited = tally["grounded_cited"] + fixable + real_gap
    print(f"引用句接地率 {tally['grounded_cited']}/{cited} = "
          f"{tally['grounded_cited'] / cited:.0%}" if cited else "本轮没有带角标引用句")
    if fixable or real_gap:
        print(f"未接地里：改编号可修 {fixable} ｜ 需要二次检索 {real_gap} "
              f"→ 主要处置应偏向{'重编号（便宜的排序/标注修复）' if fixable > real_gap else '补检索（真缺证据）'}")
    n_repl = len(per_reply)
    clean = sum(1 for c, g in per_reply if c and c == g)
    print(f"每条回复平均带角标句 {sum(c for c, _ in per_reply) / max(1, n_repl):.1f}；"
          f"全部接得住的回复 {clean}/{n_repl} = {clean / max(1, n_repl):.0%}")
    print("本探针新建的项目 id（跑完可清理）：" + ", ".join(made))
    with open(fixture_path, "w", encoding="utf-8") as f:
        json.dump(fixture, f, ensure_ascii=False)
    print(f"现场已存 {fixture_path}（{len(fixture)} 条）")
    if disagree:
        print(f"❌ {disagree}/{len(per_reply)} 份回复里，探针的逐句分类与产品 `grounding_report` "
              "对不上账 —— 尺复刻歪了，这份分布表不能读（别把它读成「缺证据的句子变多了」）")
        return 2
    print("✅ 逐句分类与产品合计份份对得上账（同口径），上面的分布可读")
    return 0


if __name__ == "__main__":
    sys.exit(main())
