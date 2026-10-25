"""Self-Refine 式迭代修订（v6 升级2）。

依据：Self-Refine（Madaan et al. 2023）/ Reflexion 的"生成→自评→定向修订"循环，
叠加两项工程化停机准则（调研结论：盲目多轮会振荡甚至退化——v1 实验 5 题中 3 题修订变差）：
- 分数轨迹：每轮多维评审（judge.py），总分入轨迹
- 平台期停机：连续 plateau_rounds 轮提升 < min_gain 即停（防振荡）
- 定向修订：只把低于阈值的维度作为修订指令（weakest_dimensions），不做盲目全文重写
- 达标即停：total ≥ target 立即返回（省 token）
"""

from __future__ import annotations

from dataclasses import dataclass, field

from judge import JudgeReport, judge_report, weakest_dimensions
from llm import chat

REVISE_SYSTEM = "你是严谨的科研报告修订者。只修订被点名的维度，其余内容保持原样，不虚构数据。"


def _revision_prompt(report_text: str, weak: list[str], dim_names: dict[str, str],
                     opinions: str = "") -> str:
    dims = "、".join(f"{dim_names.get(k, k)}({k})" for k in weak)
    return (
        f"以下报告的评审结果指出这些维度不达标：{dims}。\n"
        f"{'评审意见参考：' + opinions[:1200] + chr(10) if opinions else ''}"
        f"请定向修订报告，重点提升 {dims}，其余维度内容尽量保留。\n"
        f"输出完整修订版 Markdown 报告。\n\n报告：\n{report_text[:3500]}"
    )


@dataclass
class RefineResult:
    final: str
    trajectory: list[dict] = field(default_factory=list)   # 每轮 JudgeReport.to_dict()
    rounds: int = 0
    stop_reason: str = ""  # target_reached | max_rounds | plateau | no_weak | error
    improved: float = 0.0  # 末轮 - 首轮总分

    def to_dict(self) -> dict:
        return {"rounds": self.rounds, "stop_reason": self.stop_reason,
                "improved": round(self.improved, 2), "trajectory": self.trajectory}


async def run_selfrefine(
    query: str,
    report: str,
    *,
    max_rounds: int = 3,
    target: float = 8.5,
    weak_threshold: float = 7.0,
    plateau_rounds: int = 2,
    min_gain: float = 0.3,
    model: str | None = None,
    opinions: str = "",
) -> tuple[str, RefineResult]:
    """生成→自评→定向修订循环。返回 (最终报告, 过程元数据)。首轮评审失败则原样返回。"""
    dim_names = {k: name for k, name, _w, _a in
                 __import__("judge").DIMENSIONS}
    current = report
    result = RefineResult(final=current)
    totals: list[float] = []

    try:
        for rnd in range(1, max_rounds + 1):
            rep: JudgeReport = await judge_report(current, model=model)
            result.trajectory.append(rep.to_dict())
            result.rounds = rnd
            totals.append(rep.total)

            if rep.total >= target:
                result.stop_reason = "target_reached"
                break
            weak = weakest_dimensions(rep, threshold=weak_threshold)
            if not weak:
                result.stop_reason = "no_weak"
                break
            if rnd >= plateau_rounds and (totals[-1] - totals[-plateau_rounds]) < min_gain \
                    and len(totals) >= plateau_rounds and max(totals[-plateau_rounds:]) < target:
                result.stop_reason = "plateau"
                break

            current = await chat(
                _revision_prompt(current, weak, dim_names, opinions),
                system=REVISE_SYSTEM, temperature=0.3, max_tokens=3000, model=model,
            )
        else:
            result.stop_reason = "max_rounds"

        result.final = current
        if len(totals) >= 2:
            result.improved = totals[-1] - totals[0]
        elif len(totals) == 1:
            result.improved = 0.0
    except Exception as exc:  # noqa: BLE001 — 评审/修订失败：原样返回+如实标注
        result.stop_reason = f"error: {type(exc).__name__}: {str(exc)[:80]}"
        result.final = report if result.rounds == 0 else current
    return result.final, result


# ── 离线测试（假评审序列驱动停机逻辑，无网络）──────────────────────────────

def _offline_selftest() -> bool:
    """离线自测：停机逻辑（无网络）。返回全过与否。"""
    ok = True

    seq = iter([(9.0, []), (9.0, [])])

    async def fake_judge(text, **kw):
        total, _ = next(seq)
        r = JudgeReport()
        r.total = total
        return r

    async def fake_chat(*a, **kw):
        return "修订版"

    import asyncio as _aio

    saved_j, saved_c = globals()["judge_report"], globals()["chat"]
    try:
        globals()["judge_report"] = fake_judge
        final, res = _aio.run(run_selfrefine("q", "r", max_rounds=3))
        ok &= res.stop_reason == "target_reached" and res.rounds == 1

        seq2 = iter([(6.0, []), (6.2, []), (6.3, []), (6.3, [])])

        async def fake_judge2(text, **kw):
            total, _ = next(seq2)
            r = JudgeReport()
            r.total = total
            r.scores = {"rigor": total, "coverage": total}  # 低分维度存在 → 走修订分支
            return r

        globals()["judge_report"] = fake_judge2
        globals()["chat"] = fake_chat
        final, res = _aio.run(run_selfrefine("q", "r", max_rounds=5, plateau_rounds=2))
        ok &= res.stop_reason == "plateau"

        async def boom(text, **kw):
            raise RuntimeError("x")
        globals()["judge_report"] = boom
        final, res = _aio.run(run_selfrefine("q", "原文", max_rounds=2))
        ok &= final == "原文" and res.stop_reason.startswith("error")
    finally:
        globals()["judge_report"], globals()["chat"] = saved_j, saved_c
    return bool(ok)
def test_error_returns_original(monkeypatch):
    async def fake_judge(text, **kw):
        raise RuntimeError("评审挂了")

    import refine
    monkeypatch.setattr(refine, "judge_report", fake_judge)
    final, res = asyncio_run(refine.run_selfrefine("q", "原文", max_rounds=2))
    assert final == "原文" and res.stop_reason.startswith("error")


if __name__ == "__main__":
    import sys
    print("refine 离线自测:", "通过 ✅" if _offline_selftest() else "失败 ❌")
    sys.exit(0 if _offline_selftest() else 1)
