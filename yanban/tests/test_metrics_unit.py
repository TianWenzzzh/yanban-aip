"""诚实四指标离线单元测试（W3，无网络）：含 Codex 指出的水分场景反向用例。"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from metrics import compute_metrics, render_metrics_md  # noqa: E402


def sub(state: str, rpc: int = 2) -> dict:
    return {"state": state, "rpc_calls": rpc}


def test_all_completed_full_scores():
    tasks = [
        {"status": "completed", "plan_count": 2, "subs": [sub("completed"), sub("completed")]},
        {"status": "completed", "plan_count": 1, "subs": [sub("completed")]},
    ]
    m = compute_metrics(tasks)
    assert m["task_completion_rate"] == 1.0
    assert m["partial_completion_rate"] == 1.0
    assert m["rpc_success_rate"] == 1.0
    assert m["capability_coverage"] == 1.0


def test_codex_watered_scenario_now_honest():
    """Codex 场景：literature 失败、writing 成功 → 旧口径算 Success；新口径诚实分化。"""
    tasks = [
        {   # 文献失败、写作成功：不算 Full，只算 Partial
            "status": "completed",
            "plan_count": 2,
            "subs": [sub("failed"), sub("completed")],
        },
        {   # analysis AwaitingInput、其余成功：同上
            "status": "completed",
            "plan_count": 4,
            "subs": [sub("completed"), sub("awaiting_input"), sub("completed"), sub("completed")],
        },
    ]
    m = compute_metrics(tasks)
    assert m["task_completion_rate"] == 0.0        # 旧口径：2/2=100% ❌
    assert m["partial_completion_rate"] == 1.0     # 旧口径所谓"成功"实为 Partial
    assert 0 < m["capability_coverage"] < 1.0      # 3/6
    assert m["rpc_success_rate"] < 1.0


def test_skipped_counts_as_failure():
    tasks = [{"status": "completed", "plan_count": 3,
              "subs": [sub("completed"), sub("skipped"), sub("skipped")]}]
    m = compute_metrics(tasks)
    assert m["task_completion_rate"] == 0.0
    assert m["capability_coverage"] == pytest.approx(1 / 3)


def test_taskstate_prefix_normalized():
    tasks = [{"status": "completed", "plan_count": 1,
              "subs": [{"state": "TaskState.Completed", "rpc_calls": 3}]}]
    m = compute_metrics(tasks)
    assert m["full_completed"] == 1 and m["rpc_ok"] == 3


def test_empty_input_zero_division_safe():
    m = compute_metrics([])
    assert m["task_completion_rate"] == 0.0 and m["tasks"] == 0
    m2 = compute_metrics([{"status": "running", "plan_count": 0, "subs": []}])
    assert m2["capability_coverage"] == 0.0


def test_render_md_contains_four_metrics_and_no_single_success():
    md = render_metrics_md(compute_metrics([
        {"status": "completed", "plan_count": 2, "subs": [sub("completed"), sub("failed")]},
    ]))
    for name in ("Task Completion Rate", "Partial Completion Rate",
                 "RPC Success Rate", "Capability Coverage"):
        assert name in md
    assert "任务成功率" not in md  # 旧口径明令退场

