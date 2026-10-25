"""研伴诚实指标（W3）：四指标替代单一"成功率"。

Codex 问题④：旧口径 success = Leader completed 且 ≥1 子任务 Completed——
文献失败、写作成功也算 Success，10/10=100% 水分大。新口径：

- TaskCompletionRate   全部子任务 completed 的任务数 / 任务总数（硬指标）
- PartialCompletionRate ≥1 个子任务 completed 的任务数 / 任务总数（软指标）
- RPCSuccessRate       completed 子任务的 RPC 数 / 全部 RPC 数
- CapabilityCoverage   completed 子任务数 / 计划子任务数（能力交付覆盖）

状态口径：completed=成功；failed/timeout/skipped=未成功（skipped=上游失败被跳过，W2 引入）。
"""

from __future__ import annotations


def _norm_state(state: str) -> str:
    return str(state or "").replace("TaskState.", "").strip().lower()


def compute_metrics(tasks: list[dict]) -> dict:
    """tasks: [{"status": str, "plan_count": int, "subs": [{"state": str, "rpc_calls": int}]}]"""
    n = len(tasks)
    full = partial = 0
    rpc_ok = rpc_all = 0
    cap_done = cap_req = 0
    for t in tasks:
        subs = t.get("subs") or []
        states = [_norm_state(s.get("state")) for s in subs]
        completed = sum(1 for x in states if x == "completed")
        plan_count = t.get("plan_count") or len(subs)
        if subs and t.get("status") == "completed" and completed == len(subs):
            full += 1
        if completed >= 1:
            partial += 1
        for s in subs:
            calls = int(s.get("rpc_calls") or 0)
            rpc_all += calls
            if _norm_state(s.get("state")) == "completed":
                rpc_ok += calls
        cap_req += plan_count
        cap_done += completed
    return {
        "tasks": n,
        "task_completion_rate": full / n if n else 0.0,
        "partial_completion_rate": partial / n if n else 0.0,
        "rpc_success_rate": rpc_ok / rpc_all if rpc_all else 0.0,
        "capability_coverage": cap_done / cap_req if cap_req else 0.0,
        "full_completed": full,
        "partial_completed": partial,
        "rpc_ok": rpc_ok,
        "rpc_all": rpc_all,
        "cap_done": cap_done,
        "cap_req": cap_req,
    }


def render_metrics_md(m: dict) -> str:
    """四指标 Markdown 表（明令：不再输出单一"成功率 100%"口径）。"""
    pct = lambda x: f"{x * 100:.0f}%"  # noqa: E731
    return (
        "| 指标 | 定义 | 数值 |\n"
        "|---|---|---|\n"
        f"| Task Completion Rate | 全部子任务 completed 的任务 / 总任务 | "
        f"{m['full_completed']}/{m['tasks']} = {pct(m['task_completion_rate'])} |\n"
        f"| Partial Completion Rate | ≥1 个子任务 completed 的任务 / 总任务 | "
        f"{m['partial_completed']}/{m['tasks']} = {pct(m['partial_completion_rate'])} |\n"
        f"| RPC Success Rate | completed 子任务 RPC / 全部 RPC | "
        f"{m['rpc_ok']}/{m['rpc_all']} = {pct(m['rpc_success_rate'])} |\n"
        f"| Capability Coverage | completed 子任务 / 计划子任务 | "
        f"{m['cap_done']}/{m['cap_req']} = {pct(m['capability_coverage'])} |"
    )
