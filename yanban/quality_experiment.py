"""质量闭环对照实验（W4）：5 主题 × 三组真实 GLM 跑分，产出 docs/quality_experiment.md。

- G1 单 Agent：主题直接丢给写作 Partner（单次 AIP 调用）
- G2 多 Agent 无闭环：完整论文流水线 DAG，质量门关闭
- G3 完整闭环：同流水线，质量门开启（MAX_ROUNDS=3，审→改→再审）

评分：模拟审稿 Partner 按方法/写作/贡献打总分（X/10）+ 列幻觉声明（无依据数字/引用）；
完整度=结构头部（摘要/主要发现/结论与建议）存在性（确定性检查）。
GLM 失败重试 2 次指数退避，仍失败跳过并记录（诚实账本）。
逐主题增量写 docs/quality_experiment_data.json，全部完成后渲染 Markdown。

用法：uv run python quality_experiment.py [--topics N] [--skip-smoke]
"""

from __future__ import annotations

import asyncio
import json
import re
import sys
import time
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import httpx  # noqa: E402
from acps_sdk.aip import AipRpcClient, TaskState, TextDataItem  # noqa: E402

import quality_gate  # noqa: E402
from orchestrator import Orchestrator  # noqa: E402

ROOT = Path(__file__).resolve().parent
DATA_FILE = ROOT / "docs" / "quality_experiment_data.json"
REPORT_FILE = ROOT / "docs" / "quality_experiment.md"

TOPICS = [
    "大模型多智能体协作",
    "检索增强生成（RAG）在科研问答中的应用",
    "MCP 与 A2A 协议互操作",
    "智能体身份可信接入",
    "工具生态的动态发现与选择",
]

WRITING_ENDPOINT = "http://localhost:8034/rpc"
REVIEW_ENDPOINT = "http://localhost:8035/rpc"
LEADER_AIC = "1.2.156.3088.1.1.LDR001.LDR001.1.0002"

SCORE_PROMPT = (
    "你是严格的审稿人。对以下研究报告按 方法严谨性/写作质量/贡献 三视角打一个总分，"
    "并单独列出『幻觉声明』：文中无依据的具体数字、引用、实验结论（没有则写『无』）。"
    "输出格式固定为：\n总分: X/10\n幻觉声明:\n- …（每条一行，没有则写：无）\n\n报告：\n{report}"
)

SECTION_KEYWORDS = [("摘要",), ("发现", "结果"), ("结论", "建议")]


def completeness(report: str) -> float:
    """结构完整度：摘要/主要发现/结论三块的关键词包含（LLM 头部写法宽容）。"""
    text = report or ""
    hit = sum(1 for kws in SECTION_KEYWORDS if any(k in text for k in kws))
    return hit / len(SECTION_KEYWORDS)


async def via_aip(endpoint: str, text: str, *, timeout_s: float = 240.0) -> str:
    """通用 AIP 文本进文本出（与业务 Partner 同一协议路径）。"""
    sid, tid = f"s-{uuid.uuid4().hex[:8]}", f"t-{uuid.uuid4().hex[:8]}"
    from aip_slow import make_slow_client
    c = make_slow_client(endpoint, LEADER_AIC, timeout=timeout_s)
    try:
        t = await c.start_task(session_id=sid, task_id=tid, user_input=text)
        deadline = time.monotonic() + timeout_s
        while t.status.state in (TaskState.Accepted, TaskState.Working) and time.monotonic() < deadline:
            await asyncio.sleep(2)
            t = await c.get_task(task_id=tid, session_id=sid)
        for rescue in range(2):  # awaiting-input 代答救援（与 orchestrator 同款语义）
            if t.status.state != TaskState.AwaitingInput:
                break
            supplement = (f"Leader 代答，原任务全文：{text}\n"
                          "（缺失参数请用研究领域的合理默认值直接执行，并在结果中注明假设。）")
            if rescue == 1:
                supplement += "\n请以上方补充后的完整文本为输入直接执行。"
            t = await c.continue_task(task_id=tid, session_id=sid, user_input=supplement)
            deadline = time.monotonic() + timeout_s
            while t.status.state in (TaskState.Accepted, TaskState.Working) and time.monotonic() < deadline:
                await asyncio.sleep(2)
                t = await c.get_task(task_id=tid, session_id=sid)
        if t.status.state == TaskState.AwaitingCompletion:
            out = ""
            for p in t.products or []:
                for i in p.dataItems or []:
                    if isinstance(i, TextDataItem):
                        out += i.text
            await c.complete_task(task_id=tid, session_id=sid)
            return out
        raise RuntimeError(f"终态 {t.status.state}")
    finally:
        await c.close()


async def with_retry(coro_fn, *, retries: int = 2):
    delay = 2.0
    for attempt in range(retries + 1):
        try:
            return await coro_fn()
        except Exception as exc:  # noqa: BLE001
            if attempt == retries:
                raise
            print(f"    [retry] {attempt + 1}/{retries} {type(exc).__name__}: {str(exc)[:60]}，{delay}s 后重试", flush=True)
            await asyncio.sleep(delay)
            delay *= 2


def parse_score(review: str) -> tuple[float | None, list[str]]:
    m = re.search(r"总分[:：]\s*(\d+(?:\.\d+)?)\s*/\s*10", review)
    score = float(m.group(1)) if m else None
    halluc = []
    seg = review.split("幻觉声明")[-1] if "幻觉声明" in review else ""
    for line in seg.splitlines()[1:]:  # 跳过『幻觉声明:』标题行本身
        line = line.strip()
        if not line:
            if halluc:
                break  # 列表结束
            continue
        if line.startswith(("#", "总", "注意", "说明")):
            break  # 进入下一小节
        item = line.lstrip("-•* ").strip()
        if item and item != "无":
            halluc.append(item)
    return score, halluc[:8]





async def g1_single_agent(topic: str) -> dict:
    t0 = time.monotonic()
    report = await with_retry(lambda: via_aip(
        WRITING_ENDPOINT,
        f"为主题「{topic}」撰写一份完整研究报告，直接执行无需追问。要点：1) 领域现状与代表性工作；"
        "2) 核心技术路线与开放问题；3) 未来方向。输出必须包含『## 摘要』『## 主要发现』『## 结论与建议』三节。"))
    return {"group": "G1", "report": report, "elapsed_s": round(time.monotonic() - t0, 1)}


async def run_pipeline(topic: str, gate_on: bool) -> dict:
    quality_gate.ENABLED = gate_on
    t0 = time.monotonic()
    o = Orchestrator(ROOT / "partners.toml")
    tid = await o.submit(f"论文全流程：主题：{topic}")
    r = await o.get(tid)
    while r.status == "running":
        await asyncio.sleep(3)
        r = await o.get(tid)
    return {"group": f"{'G3' if gate_on else 'G2'}", "report": r.final_report,
            "gate": r.gate.to_dict(), "subs": [(s.partner, s.state) for s in r.subresults],
            "elapsed_s": round(time.monotonic() - t0, 1)}


async def score_report(topic: str, group: str, item: dict) -> None:
    review = await with_retry(lambda: via_aip(
        REVIEW_ENDPOINT, SCORE_PROMPT.format(report=item["report"][:3500]), timeout_s=180.0))
    score, halluc = parse_score(review)
    item["score"] = score
    item["hallucinations"] = halluc
    item["completeness"] = completeness(item["report"])
    item["score_review"] = review[:500]


async def main(topics_n: int) -> int:
    topics = TOPICS[:topics_n]
    data: dict = {"started_at": time.strftime("%Y-%m-%d %H:%M:%S"), "topics": {}}
    DATA_FILE.parent.mkdir(parents=True, exist_ok=True)
    DATA_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    for topic in topics:
        print(f"\n=== 主题：{topic} ===", flush=True)
        entry: dict = {}
        for gid, runner in (
            ("G1", lambda: g1_single_agent(topic)),
            ("G2", lambda: run_pipeline(topic, gate_on=False)),
            ("G3", lambda: run_pipeline(topic, gate_on=True)),
        ):
            try:
                item = await runner()
                await score_report(topic, gid, item)
                item["ok"] = item["score"] is not None
            except Exception as exc:  # noqa: BLE001 — 诚实账本：失败照记
                item = {"group": gid, "ok": False, "error": str(exc)[:200]}
            entry[gid] = item
            print(f"  {gid}: score={item.get('score')} complete={item.get('completeness')} "
                  f"halluc={len(item.get('hallucinations') or [])} elapsed={item.get('elapsed_s')}s", flush=True)
        data["topics"][topic] = entry
        DATA_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    # ── 汇总渲染 ──
    rows, scores = [], {"G1": [], "G2": [], "G3": []}
    hall = {"G1": [], "G2": [], "G3": []}
    comp = {"G1": [], "G2": [], "G3": []}
    for topic, entry in data["topics"].items():
        for gid in ("G1", "G2", "G3"):
            item = entry.get(gid) or {}
            if item.get("score") is not None:
                scores[gid].append(item["score"])
                hall[gid].append(len(item.get("hallucinations") or []))
                comp[gid].append(item.get("completeness") or 0.0)
        rows.append((topic, entry))

    avg = lambda xs: sum(xs) / len(xs) if xs else 0.0  # noqa: E731
    md = [
        "# 质量闭环对照实验（W4）",
        "",
        f"- 时间：{data['started_at']} ｜ 主题数：{len(data['topics'])} ｜ 全部真实 GLM 调用（glm-4-flash）",
        "- 分组：G1 单 Agent 直写 ｜ G2 多 Agent 无闭环 ｜ G3 完整闭环（审→改→再审，MAX_ROUNDS=3）",
        "- 评分：模拟审稿 Partner 三视角总分（X/10）+ 幻觉声明计数；完整度=三段结构存在性",
        "",
        "| 方法 | 平均质量分 | 幻觉声明数（合计） | 结构完整度 |",
        "|---|---|---|---|",
    ]
    label = {"G1": "单 Agent", "G2": "多 Agent 无闭环", "G3": "研伴完整系统"}
    for gid in ("G1", "G2", "G3"):
        md.append(f"| **{label[gid]}** | {avg(scores[gid]):.1f} | {sum(hall[gid])} | "
                  f"{avg(comp[gid]) * 100:.0f}% |")
    md += ["", "## 逐主题明细", ""]
    for topic, entry in rows:
        md.append(f"### {topic}")
        for gid in ("G1", "G2", "G3"):
            it = entry.get(gid) or {}
            if it.get("ok"):
                md.append(f"- {label[gid]}：{it['score']}/10，幻觉 {len(it.get('hallucinations') or [])} 条，"
                          f"完整度 {it.get('completeness', 0) * 100:.0f}%，耗时 {it.get('elapsed_s')}s")
            else:
                md.append(f"- {label[gid]}：失败——{it.get('error', '')[:120]}（诚实账本）")
        md.append("")
    md += ["---", "*数字全部为真实 GLM 调用实测；失败的组如实标注，不补数。*"]
    REPORT_FILE.write_text("\n".join(md), encoding="utf-8")
    print(f"\n实验完成 → {REPORT_FILE}")
    return 0


if __name__ == "__main__":
    n = 5
    if "--topics" in sys.argv:
        n = int(sys.argv[sys.argv.index("--topics") + 1])
    sys.exit(asyncio.run(main(n)))
