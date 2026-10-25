"""DAG 执行器离线单元测试（W2，无网络）：拓扑序/产物注入/失败跳过/并行保持/成环退化。"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from orchestrator import Orchestrator, SubTaskResult  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]


def make_orch() -> Orchestrator:
    return Orchestrator(ROOT / "partners.toml")


def paper_plan() -> list[dict]:
    return [
        {"key": "literature", "partner": "literature", "input": "检索文献 topic", "depends": []},
        {"key": "experiment", "partner": "experiment", "input": "设计实验 topic", "depends": ["literature"]},
        {"key": "writing", "partner": "writing", "input": "撰写论文 topic", "depends": ["literature", "experiment"]},
        {"key": "review", "partner": "review", "input": "评审草稿", "depends": ["writing"]},
    ]


def install_fake_runner(orch: Orchestrator, events: list, fail_keys: set[str] | None = None):
    """假 _run_subtask：记录收到的 input，回显上游注入痕迹；指定 key 失败。"""
    fail_keys = fail_keys or set()

    async def fake(st: dict) -> SubTaskResult:
        key = st.get("key") or st["partner"]
        events.append((key, st["input"]))
        if key in fail_keys:
            return SubTaskResult(partner=st["partner"], state="failed", error="模拟失败", stage_key=key)
        return SubTaskResult(partner=st["partner"], state="completed",
                             output=f"OUT[{key}]=基于: {st['input'][-40:]}", stage_key=key)

    orch._run_subtask = fake  # 实例属性遮蔽，_execute_plan 经 self 调用即用假体
    return orch


def test_dag_topo_order_and_product_injection():
    orch = make_orch()
    events: list = []
    install_fake_runner(orch, events)
    subs = asyncio_run(orch._execute_plan(paper_plan()))
    order = [k for k, _ in events]
    assert order.index("literature") < order.index("experiment") < order.index("writing") < order.index("review")
    # 产物注入：writing 输入含 literature 与 experiment 的产出；review 输入含 writing 产出
    writing_input = dict(events)["writing" if ("writing",) else "writing"] if False else next(i for k, i in events if k == "writing")
    assert "【上游产出——literature】" in writing_input
    assert "OUT[literature]" in writing_input
    assert "OUT[experiment]" in writing_input
    review_input = next(i for k, i in events if k == "review")
    assert "OUT[writing]" in review_input
    # 全部完成，无 skipped
    assert [s.state for s in subs] == ["completed"] * 4
    assert [s.stage_key for s in subs] == ["literature", "experiment", "writing", "review"]


def asyncio_run(coro):
    import asyncio
    return asyncio.run(coro)


def test_dag_failure_skips_downstream():
    orch = make_orch()
    events: list = []
    install_fake_runner(orch, events, fail_keys={"literature"})
    subs = asyncio_run(orch._execute_plan(paper_plan()))
    by_key = {s.stage_key: s for s in subs}
    assert by_key["literature"].state == "failed"
    for k in ("experiment", "writing", "review"):
        assert by_key[k].state == "skipped", f"{k} 应因上游失败被跳过"
        assert "失败，跳过" in by_key[k].error
    # 失败上游的下游不应被真实调用（事件里只有 literature）
    assert [k for k, _ in events] == ["literature"]


def test_llm_plan_without_deps_stays_parallel():
    orch = make_orch()
    events: list = []
    install_fake_runner(orch, events)
    plan = [{"partner": "literature", "input": "a"}, {"partner": "writing", "input": "b"}]
    subs = asyncio_run(orch._execute_plan(plan))
    assert [s.state for s in subs] == ["completed", "completed"]
    assert len(events) == 2  # 全部真实执行（并行语义保持）


def test_cycle_falls_back_to_parallel():
    orch = make_orch()
    events: list = []
    install_fake_runner(orch, events)
    plan = [
        {"key": "a", "partner": "literature", "input": "a", "depends": ["b"]},
        {"key": "b", "partner": "writing", "input": "b", "depends": ["a"]},
    ]
    subs = asyncio_run(orch._execute_plan(plan))
    assert len(events) == 2  # 成环退化并行：都执行
    assert all(s.state == "completed" for s in subs)


def test_workflow_toml_declares_dag(orch_fixture):
    plan = orch_fixture._match_workflow("请论文全流程：主题：协作")
    deps = {s.get("key"): s.get("depends") or [] for s in plan}
    assert deps["experiment"] == ["literature"]
    assert set(deps["writing"]) == {"literature", "experiment"}
    assert deps["review"] == ["writing"]


@pytest.fixture()
def orch_fixture():
    return make_orch()
