"""质量闭环对照实验 v2（T1）：修正 v1 实验设计缺陷后复跑。

v1 → v2 修正点（见 docs/quality_experiment.md 诚实账本）：
1. stage 深度强制（workflows.toml：文献编号条目/写作 ≥800 字+编号引用/评审引用具体内容）
2. 门控策略三臂对照（同一份流水线原始报告差分，控制变量）：
   - GA flash 重写（=v1 现状，quality_gate.run_gate）
   - GB 意见注入不重写（审稿意见回注 Leader 聚合，修复"输入薄"根因，无独立重写调用）
   - GC glm-4-plus 重写（更强模型下场；失败降级 GA 并记录）
3. 每组 N=3 重复取均值，temperature 全链代码固定
4. 增量落盘 docs/quality_experiment_data_v2.json；报告 docs/quality_experiment_v2.md

用法：uv run python quality_experiment_v2.py [--topics N] [--repeats N]
"""

from __future__ import annotations

import asyncio
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import quality_experiment as qe  # noqa: E402
from llm import LLMError, chat  # noqa: E402
from orchestrator import Orchestrator  # noqa: E402
from quality_gate import _verdict_of, _review_via_partner  # noqa: E402

ROOT = Path(__file__).resolve().parent
DATA_FILE = ROOT / "docs" / "quality_experiment_data_v2.json"
REPORT_FILE = ROOT / "docs" / "quality_experiment_v2.md"

STRATEGY_PLUS_MODEL = "glm-4-plus"
GATE_MAX_ROUNDS = 2  # 时间截断（v2 首测单 repeat ≈2.5h，20h+ 不可行）；如实标注于报告


async def gate_a_flash_rewrite(query: str, report: str, sub_outputs: dict[str, str]) -> dict:
    """GA：flash 重写（=v1 现状 run_gate，独立重写调用）。
    注意 run_gate 读全局 ENABLED——流水线外置门控时被置 False，这里必须恢复。"""
    import quality_gate
    quality_gate.ENABLED = True
    from quality_gate import run_gate
    _saved = quality_gate.MAX_ROUNDS
    quality_gate.MAX_ROUNDS = GATE_MAX_ROUNDS
    try:
        final, gate = await run_gate(query, report)
    finally:
        quality_gate.MAX_ROUNDS = _saved
    return {"final": final, "rounds": gate.rounds, "final_verdict": gate.final_verdict,
            "strategy": "GA-flash-rewrite", "downgraded": False}


async def _reaggregate(query: str, sub_outputs: dict[str, str], opinions: str) -> str:
    """GB 核心动作：审稿意见 + 原始子任务产出 → Leader 重新聚合（不独立重写）。"""
    blocks = [f"### 子任务 {k}\n{v[:2500]}" for k, v in sub_outputs.items() if v]
    return await chat(
        f"用户原始需求：{query}\n\n各 Partner 产出：\n" + "\n\n".join(blocks) +
        f"\n\n上一版报告的审稿意见（必须逐条响应）：\n{opinions[:1500]}\n\n"
        "请按审稿意见重新整合各 Partner 产出为完整 Markdown 报告"
        "（## 摘要 → ## 主要发现 → ## 结论与建议），主要发现须编号引用子任务产出的具体内容。",
        system="你是严谨的科研报告撰写者，逐条响应审稿意见，只基于给定产出，不虚构。",
        temperature=0.3, max_tokens=3000,
    )


async def gate_b_opinion_injection(query: str, report: str, sub_outputs: dict[str, str]) -> dict:
    """GB：意见注入不重写——审稿意见回注聚合层（修根因），无独立重写。"""
    rounds, current = [], report
    for rnd in range(1, GATE_MAX_ROUNDS + 1):
        review = await _review_via_partner(f"请评审以下研究报告：\n\n{current[:3000]}")
        verdict = _verdict_of(review)
        info = {"round": rnd, "verdict": verdict, "strategy": "GB-opinion-injection"}
        if verdict not in ("major", "reject"):
            rounds.append(info)
            break
        current = await _reaggregate(query, sub_outputs, review)
        rounds.append({**info, "revised": True})
    return {"final": current, "rounds": rounds,
            "final_verdict": rounds[-1]["verdict"] if rounds else "skip",
            "strategy": "GB-opinion-injection", "downgraded": False}


async def gate_c_plus_rewrite(query: str, report: str, sub_outputs: dict[str, str]) -> dict:
    """GC：glm-4-plus 重写（run_gate 结构 + 强模型）；plus 失败降级 flash 并如实记录。"""
    rounds, current, downgraded = [], report, False
    for rnd in range(1, GATE_MAX_ROUNDS + 1):
        review = await _review_via_partner(f"请评审以下研究报告：\n\n{current[:3000]}")
        verdict = _verdict_of(review)
        info = {"round": rnd, "verdict": verdict, "strategy": "GC-plus-rewrite"}
        if verdict not in ("major", "reject"):
            rounds.append(info)
            break
        try:
            current = await chat(
                f"原始需求：{query}\n\n当前报告：\n{current[:3500]}\n\n审稿意见：\n{review[:1500]}\n\n"
                "请按审稿意见修订这份报告：修复被指出的方法/结构/贡献问题，保留正确内容，"
                "输出完整的修订版 Markdown 报告（## 摘要 → ## 主要发现 → ## 结论与建议）。",
                system="你是严谨的科研报告修订者，按意见逐条修改，不虚构数据。",
                temperature=0.3, max_tokens=3000, model=STRATEGY_PLUS_MODEL,
            )
        except LLMError as exc:
            downgraded = True
            info["downgrade_reason"] = f"plus 不可用降级 flash: {str(exc)[:80]}"
            current = await chat(
                f"原始需求：{query}\n\n当前报告：\n{current[:3500]}\n\n审稿意见：\n{review[:1500]}\n\n"
                "请按审稿意见修订这份报告，输出完整修订版 Markdown 报告。",
                system="你是严谨的科研报告修订者，按意见逐条修改，不虚构数据。",
                temperature=0.3, max_tokens=3000,
            )
        rounds.append({**info, "revised": True})
    return {"final": current, "rounds": rounds,
            "final_verdict": rounds[-1]["verdict"] if rounds else "skip",
            "strategy": "GC-plus-rewrite", "downgraded": downgraded}


async def gate_d_best_of_n(query: str, report: str, sub_outputs: dict[str, str]) -> dict:
    """GD：Best-of-N 差分选择（v6 升级4）——GA/GC 并发生成两候选，多维 rubric 评审选优。

    依据：test-time compute 换质量（best-of-N reranking）；评审用多维 rubric（judge.py）
    而非单一总分——避免长度偏差主导选择。
    """
    from judge import judge_report
    ga, gc_ = await asyncio.gather(
        gate_a_flash_rewrite(query, report, sub_outputs),
        gate_c_plus_rewrite(query, report, sub_outputs))
    fa, fc = ga["final"], gc_["final"]
    ra = await judge_report(fa)
    rc = await judge_report(fc)
    pick = "GC" if rc.total >= ra.total else "GA"
    final, chosen_r = (fc, rc) if pick == "GC" else (fa, ra)
    return {"final": final, "rounds": [{"GA_rubric": round(ra.total, 2),
                                        "GC_rubric": round(rc.total, 2), "picked": pick}],
            "final_verdict": f"best-of-2→{pick}({max(ra.total, rc.total):.1f})",
            "strategy": "GD-best-of-2", "downgraded": ga["downgraded"] and gc_["downgraded"]}


STRATEGIES = {"GA": gate_a_flash_rewrite, "GB": gate_b_opinion_injection,
              "GC": gate_c_plus_rewrite, "GD": gate_d_best_of_n}


async def run_pipeline_once(topic: str) -> dict:
    """跑一次论文流水线（门控外置），返回原始报告+子任务产出。

    关键：必须关掉 orchestrator 内置门控（quality_gate.ENABLED），否则流水线内部
    先跑一轮 flash 门控，G2 基线与三策略差分全被污染（冒烟实测踩坑）。
    """
    import quality_gate
    quality_gate.ENABLED = False
    o = Orchestrator(ROOT / "partners.toml")
    tid = await o.submit(f"论文全流程：主题：{topic}")
    r = await o.get(tid)
    while r.status == "running":
        await asyncio.sleep(3)
        r = await o.get(tid)
    sub_outputs = {s.partner: (s.output or "") for s in r.subresults}
    return {"raw_report": r.final_report, "sub_outputs": sub_outputs,
            "subs": [(s.partner, s.state) for s in r.subresults],
            "gate_meta": r.gate.to_dict()}


JUDGE_MODE = "partner"  # partner=审稿 Partner 单分数（v1 口径）| rubric=多维评审器（v6）


async def score_report_v2(item: dict) -> None:
    """v2 评分：审稿 Partner 慢（实测 25s/简单任务），超时放宽 240s、重试 3 次。

    --judge rubric 时改用多维 rubric 评审器（judge.py）：五维加权分+幻觉清单，
    去长度偏差（维度标尺显式声明不以长度论质量）。
    """
    if JUDGE_MODE == "rubric":
        from judge import judge_report
        rep = await qe.with_retry(lambda: judge_report(item["report"][:3500]), retries=2)
        d = rep.to_dict()
        item["score"] = d["total"]
        item["hallucinations"] = d["hallucinations"]
        item["completeness"] = qe.completeness(item["report"])
        item["rubric"] = d
        item["ok"] = item["score"] is not None
        return
    review = await qe.with_retry(
        lambda: qe.via_aip(qe.REVIEW_ENDPOINT,
                           qe.SCORE_PROMPT.format(report=item["report"][:3500]), timeout_s=240),
        retries=3)
    item["score"], item["hallucinations"] = qe.parse_score(review)
    item["completeness"] = qe.completeness(item["report"])
    item["score_review"] = review[:500]


async def main(topics_n: int, repeats: int, offset: int = 0, suffix: str = "") -> int:
    topics = qe.TOPICS[offset:offset + topics_n]
    data_file = DATA_FILE.with_name(DATA_FILE.stem + suffix + ".json") if suffix else DATA_FILE
    report_file = REPORT_FILE.with_name(REPORT_FILE.stem + suffix + ".md") if suffix else REPORT_FILE
    data: dict = {"started_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                  "design": {"repeats": repeats, "gate_max_rounds": GATE_MAX_ROUNDS,
                             "strategies": list(STRATEGIES), "offset": offset,
                             "notes": "同 repeat 单流水线 + 多策略差分；温度全链代码固定"},
                  "topics": {}}
    data_file.parent.mkdir(parents=True, exist_ok=True)
    data_file.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    for topic in topics:
        print(f"\n=== 主题：{topic} ===", flush=True)
        entry: dict = {"G1": [], "G2": [], "GA": [], "GB": [], "GC": [], "GD": []}
        for rep in range(1, repeats + 1):
            print(f"  [repeat {rep}] G1 单Agent…", flush=True)
            try:
                t0 = time.monotonic()
                report = await qe.with_retry(lambda: qe.via_aip(
                    qe.WRITING_ENDPOINT,
                    f"为主题「{topic}」撰写一份完整研究报告，直接执行无需追问。要点：1) 领域现状与代表性工作；"
                    "2) 核心技术路线与开放问题；3) 未来方向。输出必须包含『## 摘要』『## 主要发现』"
                    "『## 结论与建议』三节，全文不少于 600 字。"))
                item = {"group": "G1", "report": report, "elapsed_s": round(time.monotonic() - t0, 1)}
                await score_report_v2(item)
                item["ok"] = item["score"] is not None
            except Exception as exc:  # noqa: BLE001
                item = {"group": "G1", "ok": False, "error": f"{type(exc).__name__}: {str(exc)[:150]}"}
            entry["G1"].append(item)
            print(f"    G1: score={item.get('score')}", flush=True)

            print(f"  [repeat {rep}] 流水线（无门）…", flush=True)
            try:
                pipe = await qe.with_retry(lambda: run_pipeline_once(topic))
            except Exception as exc:  # noqa: BLE001
                print(f"    流水线失败：{str(exc)[:100]}", flush=True)
                for g in ("G2", "GA", "GB", "GC"):
                    entry[g].append({"group": g, "ok": False, "error": f"pipeline: {str(exc)[:120]}"})
                continue
            sub_outputs = pipe["sub_outputs"]

            # G2：原始报告（无闭环）
            item = {"group": "G2", "report": pipe["raw_report"], "subs": pipe["subs"]}
            try:
                await score_report_v2(item)
                item["ok"] = item["score"] is not None
            except Exception as exc:  # noqa: BLE001
                item["ok"], item["error"] = False, f"score: {str(exc)[:100]}"
            entry["G2"].append(item)
            print(f"    G2: score={item.get('score')}", flush=True)

            # GA/GB/GC：同一原始报告三策略并发差分（时间截断改造）
            async def run_strategy(gid: str, fn) -> dict:
                try:
                    gated = await fn(topic, pipe["raw_report"], sub_outputs)
                    item = {"group": gid, "report": gated["final"], "gate": gated["rounds"],
                            "final_verdict": gated["final_verdict"], "downgraded": gated["downgraded"]}
                    await score_report_v2(item)
                    item["ok"] = item["score"] is not None
                except Exception as exc:  # noqa: BLE001
                    item = {"group": gid, "ok": False, "error": f"{type(exc).__name__}: {str(exc)[:120]}"}
                print(f"    {gid}: score={item.get('score')} verdict={item.get('final_verdict')} "
                      f"downgraded={item.get('downgraded')}", flush=True)
                return item

            strategy_items = await asyncio.gather(
                *(run_strategy(gid, fn) for gid, fn in STRATEGIES.items()))
            for gid, item in zip(STRATEGIES, strategy_items):
                entry[gid].append(item)

        data["topics"][topic] = entry
        data_file.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    # ── 汇总渲染 ──
    def stats(gid: str) -> dict:
        scores, halls, comps, ok_n = [], [], [], 0
        for e in data["topics"].values():
            for it in e.get(gid) or []:
                if it.get("score") is not None:
                    scores.append(it["score"])
                    halls.append(len(it.get("hallucinations") or []))
                    comps.append(it.get("completeness") or 0.0)
                    ok_n += 1
        n = max(sum(len(e.get(gid) or []) for e in data["topics"].values()), 1)
        return {"avg_score": sum(scores) / len(scores) if scores else 0.0,
                "avg_halluc": sum(halls) / len(halls) if halls else 0.0,
                "avg_completeness": sum(comps) / len(comps) if comps else 0.0,
                "ok": ok_n, "total": n}

    label = {"G1": "单 Agent", "G2": "多 Agent 无闭环", "GA": "闭环·flash重写(=v1)",
            "GB": "闭环·意见注入", "GC": "闭环·plus重写", "GD": "闭环·Best-of-2选优"}
    md = [
        "# 质量闭环对照实验 v2（T1：修正设计后复跑）",
        "",
        f"- 时间：{data['started_at']} ｜ 主题 {len(data['topics'])} × 重复 {repeats} ｜ 全部真实 GLM 调用",
        "- 修正：stage 深度强制（文献编号/写作 ≥800 字+编号引用/评审引用具体内容）；"
        "三策略同一流水线原始报告差分（控制变量）",
        "",
        "| 方法 | 平均质量分 | 平均幻觉声明 | 结构完整度 | 有效/总 |",
        "|---|---|---|---|---|",
    ]
    for gid in ("G1", "G2", "GA", "GB", "GC", "GD"):
        st = stats(gid)
        md.append(f"| **{label[gid]}** | {st['avg_score']:.1f} | {st['avg_halluc']:.1f} | "
                  f"{st['avg_completeness'] * 100:.0f}% | {st['ok']}/{st['total']} |")
    md += ["", "## 逐主题明细", ""]
    for topic, e in data["topics"].items():
        md.append(f"### {topic}")
        for gid in ("G1", "G2", "GA", "GB", "GC", "GD"):
            for i, it in enumerate(e.get(gid) or [], 1):
                if it.get("ok"):
                    extra = f"，verdict={it.get('final_verdict')}" if gid in ("GA", "GB", "GC") else ""
                    dg = "（plus 降级）" if it.get("downgraded") else ""
                    md.append(f"- {label[gid]}#{i}: {it['score']}/10，幻觉 {len(it.get('hallucinations') or [])}，"
                              f"完整度 {it.get('completeness', 0) * 100:.0f}%{extra}{dg}")
                else:
                    md.append(f"- {label[gid]}#{i}: 失败——{it.get('error', '')[:120]}")
        md.append("")
    md += ["---", "*数字全部为真实 GLM 调用实测（原始数据 quality_experiment_data_v2.json），未修饰；"
           "与 v1 对比注意：v2 流水线已带 stage 深度强制，G2 基线本身较 v1 增强。*"]
    report_file.write_text("\n".join(md), encoding="utf-8")
    print(f"\n实验 v2 完成 → {report_file}")
    return 0


if __name__ == "__main__":
    n_topics, n_reps = 3, 2  # 时间截断（原计划 5×3，单 repeat 实测 ≈2.5h 不可行）
    argv = sys.argv
    if "--topics" in argv:
        n_topics = int(argv[argv.index("--topics") + 1])
    if "--repeats" in argv:
        n_reps = int(argv[argv.index("--repeats") + 1])
    if "--judge" in argv:
        JUDGE_MODE = argv[argv.index("--judge") + 1]  # noqa: F841 — main 读取模块级
    _offset = int(argv[argv.index("--offset") + 1]) if "--offset" in argv else 0
    _suffix = argv[argv.index("--suffix") + 1] if "--suffix" in argv else ""
    sys.exit(asyncio.run(main(n_topics, n_reps, _offset, _suffix)))
