"""研伴量化基准（bench，W3 诚实四指标版）：跑 N 条预设科研任务，输出量化指标表。

指标（metrics.py，Codex 问题④修复）：TaskCompletionRate / PartialCompletionRate /
RPCSuccessRate / CapabilityCoverage——替代旧单一"任务成功率"（旧口径下子任务失败也算成功，
10/10=100% 水分大，禁止再用）。结果写入 bench-results-<日期>.md（比赛"应用成效"素材）。
"""

import asyncio
import statistics
import sys
import time
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).parent))

from metrics import compute_metrics, render_metrics_md  # noqa: E402

LEADER = "http://localhost:8041"

TASKS = [
    "调研大模型多智能体协作的研究现状，给我摘要与方向建议",
    "检索 knowledge graph embedding 文献 3篇，并整理综述框架",
    "对比 RAG 与微调在医疗问答的准确率，有 1000 条标注数据，请设计实验方案",
    "分析这批实验结果的相关性：```csv\naccuracy,latency\n0.81,320\n0.85,410\n0.79,300\n0.88,450\n0.83,380\n```",
    "写一段相关工作：要点1 MCP统一工具接入；要点2 A2A智能体任务委派；要点3 二者互补需桥接",
    "评审这段结论：多智能体系统通过标准化协议实现协作，在两个数据集上实验显示准确率提升5%",
    "调研 MCP 与 A2A 协议互操作的研究现状，给我摘要和方向建议",
    "检索 federated learning 医疗影像 文献 2篇",
    "设计一个验证多智能体协作提升科研效率的实验，并评审这段写作：协作系统让效率提升30%",
    "分析 ```csv\nloss,epoch\n2.1,1\n1.6,2\n1.2,3\n0.9,4\n0.7,5\n``` 的收敛趋势",
]


async def run_one(client: httpx.AsyncClient, query: str) -> dict:
    t0 = time.monotonic()
    r = (await client.post(f"{LEADER}/task", json={"query": query}, timeout=20)).json()
    task_id = r["task_id"]
    for _ in range(120):
        await asyncio.sleep(3)
        d = (await client.get(f"{LEADER}/task/{task_id}", timeout=20)).json()
        if d["status"] != "running":
            break
    elapsed = time.monotonic() - t0
    subs = d.get("subresults", [])
    norm = [{"state": s["state"].replace("TaskState.", ""), "rpc_calls": s["rpc_calls"]} for s in subs]
    return {
        "query": query[:38],
        "status": d["status"],
        "plan_count": len(d.get("plan", [])),
        "subs_norm": norm,
        "subs": [(s["partner"], s["state"].replace("TaskState.", ""), s["duration_ms"]) for s in subs],
        "rpc_total": sum(s["rpc_calls"] for s in subs),
        "report_len": len(d.get("final_report", "")),
        "elapsed_s": round(elapsed, 1),
    }


async def main() -> None:
    lines = [f"# 研伴系统 量化基准结果（诚实四指标，{time.strftime('%Y-%m-%d')} 实测）\n"]
    all_subs: dict[str, list[float]] = {}
    metric_tasks: list[dict] = []
    for i, q in enumerate(TASKS, 1):
        async with httpx.AsyncClient() as client:
            try:
                r = await run_one(client, q)
            except Exception as exc:  # noqa: BLE001
                r = {"query": q[:38], "status": "exception", "plan_count": 0,
                     "subs_norm": [], "subs": [], "rpc_total": 0, "report_len": 0,
                     "elapsed_s": 0.0, "error": str(exc)[:80]}
        metric_tasks.append({"status": r["status"], "plan_count": r["plan_count"],
                             "subs": r["subs_norm"]})
        for p, st, ms in r["subs"]:
            all_subs.setdefault(p, []).append(ms)
        print(f"[{i}/{len(TASKS)}] status={r['status']} plan={r['plan_count']} subs={r['subs']} {r['elapsed_s']}s")
        lines.append(
            f"{i}. {r['query']}… → 状态={r['status']} "
            f"分解={r['plan_count']} 子任务={r['subs']} 端到端={r['elapsed_s']}s 报告={r['report_len']}字"
        )

    m = compute_metrics(metric_tasks)
    lines.append("\n## 诚实四指标（W3：替代旧单一\"任务成功率\"口径）\n")
    lines.append(render_metrics_md(m))
    lines.append(f"\n跨智能体 RPC 总次数 | {m['rpc_all']} |\n")
    for p, ms in sorted(all_subs.items()):
        lines.append(f"- {p} 调用 {len(ms)} 次：平均 {statistics.mean(ms):.0f}ms / 中位 {statistics.median(ms):.0f}ms")
    out = f"bench-results-{time.strftime('%Y%m%d')}.md"
    Path(out).write_text("\n".join(lines), encoding="utf-8")
    print("\n".join(lines[-10:]))
    print(f"\nsaved -> {out}")


if __name__ == "__main__":
    asyncio.run(main())
