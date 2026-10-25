"""质量闭环（Quality Gate）：聚合报告 → 模拟审稿 → 不合格自动修订。

对应创新点 4 的"评审迭代闭环"（Coscientist 式生成→审→改）：
1. 把 Leader 聚合报告交给模拟审稿 Partner（AIP 调用，与普通 Partner 同一协议）
2. 解析 Meta 结论：大修/拒稿 → 触发一轮修订（GLM 按意见重写）
3. 返回 gate 元数据（评审意见/是否修订/轮次），随任务结果透出
"""

from __future__ import annotations

import os
import uuid
from dataclasses import dataclass, field

from acps_sdk.aip import AipRpcClient, TaskState, TextDataItem

from llm import chat

REVIEW_PARTNER = os.environ.get("YANBAN_REVIEW_ENDPOINT", "http://localhost:8035/rpc")
LEADER_AIC = "1.2.156.3088.1.1.LDR001.LDR001.1.0002"
ENABLED = os.environ.get("YANBAN_QUALITY_GATE", "1") == "1"
MAX_ROUNDS = 3  # v4(W2)：审一次改一次完 → 修订后再次审稿，最多 3 轮


@dataclass
class GateReport:
    enabled: bool = ENABLED
    rounds: list[dict] = field(default_factory=list)  # [{review_summary, verdict, revised}]
    final_verdict: str = ""

    def to_dict(self) -> dict:
        return {"enabled": self.enabled, "rounds": self.rounds, "final_verdict": self.final_verdict}


async def _review_via_partner(text: str) -> str:
    """经 AIP 协议调模拟审稿 Partner（与业务 Partner 同一协议路径）。"""
    sid, tid = f"s-{uuid.uuid4().hex[:8]}", f"t-{uuid.uuid4().hex[:8]}"
    from aip_slow import make_slow_client
    c = make_slow_client(REVIEW_PARTNER, LEADER_AIC)
    try:
        t = await c.start_task(session_id=sid, task_id=tid, user_input=text)
        import asyncio

        for _ in range(45):
            if t.status.state not in (TaskState.Accepted, TaskState.Working):
                break
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
        extra = ""
        for i in (t.status.dataItems if t.status else None) or []:
            if isinstance(i, TextDataItem):
                extra += i.text
        raise RuntimeError(f"审稿 Partner 终态 {t.status.state}: {extra[:120]}")
    finally:
        await c.close()


def _verdict_of(review_text: str) -> str:
    """从 Meta 结论提取判定：reject/major/minor/accept。"""
    import re

    seg = review_text.split("Meta")[-1] if "Meta" in review_text else review_text[-400:]
    if re.search(r"拒稿", seg):
        return "reject"
    if re.search(r"大修", seg):
        return "major"
    if re.search(r"小修", seg):
        return "minor"
    if re.search(r"接收", seg):
        return "accept"
    return "minor"


async def run_gate(query: str, report: str) -> tuple[str, GateReport]:
    """对报告跑质量闭环，返回 (最终报告, gate 元数据)。Partner 不可用时静默降级。"""
    gate = GateReport()
    print(f"[qgate] called enabled={ENABLED} report_len={len(report)}", flush=True)
    if not ENABLED:
        gate.final_verdict = "disabled"
        return report, gate

    current = report
    for rnd in range(1, MAX_ROUNDS + 1):
        try:
            review = await _review_via_partner(f"请评审以下研究报告：\n\n{current[:3000]}")
        except Exception as exc:  # noqa: BLE001
            gate.final_verdict = f"reviewer-unavailable: {str(exc)[:80]}"
            return current, gate

        verdict = _verdict_of(review)
        need_revise = verdict in ("major", "reject")
        round_info = {"round": rnd, "verdict": verdict, "revised": False, "review_summary": review[:600]}
        if need_revise:
            current = await chat(
                f"原始需求：{query}\n\n当前报告：\n{current[:3500]}\n\n审稿意见：\n{review[:1500]}\n\n"
                "请按审稿意见修订这份报告：修复被指出的方法/结构/贡献问题，保留正确内容，"
                "输出完整的修订版 Markdown 报告（结构：## 摘要 → ## 主要发现 → ## 结论与建议）。"
                "在开头加一行『> 修订说明：按审稿意见修改了…』。",
                system="你是严谨的科研报告修订者，按意见逐条修改，不虚构数据。",
                temperature=0.3,
                max_tokens=3000,
            )
            round_info["revised"] = True
        gate.rounds.append(round_info)
        gate.final_verdict = verdict
        if not need_revise:
            break
    return current, gate
