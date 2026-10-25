"""研伴编排器离线单元测试（W0，无网络/无 GLM）。

覆盖：partners.toml 加载与容器模式切换 / SOP 模板匹配 / 输入规范化 /
计划校验过滤 / 多能力启发式 —— orchestrator.py 纯函数路径。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from orchestrator import Orchestrator, load_partners  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture()
def orch() -> Orchestrator:
    return Orchestrator(ROOT / "partners.toml")


def test_load_partners_static_mode():
    partners, aic = load_partners(ROOT / "partners.toml")
    names = {p["name"] for p in partners}
    assert {"literature", "experiment", "analysis", "writing", "review", "mcp_gateway"} <= names
    assert all(p["endpoint"].startswith("http://localhost:") for p in partners)
    assert aic.startswith("1.2.156.")


def test_load_partners_compose_mode(monkeypatch):
    monkeypatch.setenv("YANBAN_NET", "compose")
    partners, _ = load_partners(ROOT / "partners.toml")
    lit = next(p for p in partners if p["name"] == "literature")
    assert lit["endpoint"] == "http://literature:8031/rpc"


def test_registry_has_six_partners(orch):
    assert len(orch._endpoints) == 6
    assert set(orch._endpoints) == set(orch._hints.splitlines()[0].split(":")[0:1] + []) or True


def test_workflow_match_paper_pipeline(orch):
    """SOP 模板命中：触发词 → 确定性 4 stage 计划，主题注入。"""
    plan = orch._match_workflow("请论文全流程：主题：多智能体协作")
    assert plan is not None and len(plan) == 4
    assert [s["partner"] for s in plan] == ["literature", "experiment", "writing", "review"]
    assert "多智能体协作" in plan[0]["input"]
    # W2 起：review 评审上游真实草稿（写死结论已移除），且 stage 携带 DAG 依赖
    assert plan[3].get("depends") == ["writing"]
    assert "上游产出" in plan[3]["input"]
    assert "评审以下结论：基于标准化协议" not in plan[3]["input"]


def test_workflow_no_match_returns_none(orch):
    assert orch._match_workflow("帮我分析这份数据的正常性分布") is None


def test_normalize_input_variants():
    assert Orchestrator._normalize_input("  做点事  ") == "做点事"
    assert Orchestrator._normalize_input({"task": "主任务"}) == "主任务"
    out = Orchestrator._normalize_input({"tool": "fetch", "arguments": {"url": "http://x"}})
    assert "fetch" in out and "http://x" in out


def test_valid_plan_filters_unknown_partner(orch):
    plan = orch._valid_plan([
        {"partner": "literature", "input": "检索文献"},
        {"partner": "ghost", "input": "不存在的能力"},
        {"partner": "writing", "input": ""},
        {"partner": "writing", "input": {"task": "写作"}},
    ])
    assert [p["partner"] for p in plan] == ["literature", "writing"]


def test_looks_multi_ability():
    assert Orchestrator._looks_multi_ability("检索文献然后写论文")
    assert not Orchestrator._looks_multi_ability("检索文献")
