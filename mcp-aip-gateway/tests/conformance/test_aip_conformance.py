"""AIP 侧一致性测试（从官方 schema / 官方校验器 / 规约表派生，可独立运行）。

覆盖（对照 00_全自动主提示词 M4）：
1. ACS 必填字段全集——动态派生自 fixtures/acsSchema.json，并与 DESIGN.md 记录交叉核对；
   翻译器产物 / 聚合 ACS / 真实 gen-acs.json 全部过 jsonschema 校验
2. 非法 AIC 拒绝——直接驱动官方 acps_sdk.aic.validate_aic_format
3. 状态机非法迁移——规范迁移表（spec_tables）作裁判，网关已验证流程必须合法
4. awaiting-input 语义——官方枚举含该态；万桥 v0 映射决策（A2A input-required →
   awaiting-completion，继续文本透传）被显式固化并留痕
"""

from __future__ import annotations

import json
from pathlib import Path

import jsonschema
import pytest
from types import SimpleNamespace as NS

from gateway.registry import McpRegistry
from gateway.translator import tool_to_skill, tools_to_acs

import spec_tables as spec

HERE = Path(__file__).resolve().parent
SCHEMA = json.loads((HERE / "fixtures" / "acsSchema.json").read_text(encoding="utf-8"))


def _sample_tool(name="search"):
    return NS(
        name=name, title=None, description="搜索 demo 文档",
        inputSchema={"properties": {"q": {"type": "string"}}, "required": ["q"]},
        input_schema=None, annotations=None,
    )


def _generated_acs() -> dict:
    return tools_to_acs(
        agent_name="研伴·MCP工具网关", description="聚合网关", server_ns="demo",
        tools=[_sample_tool()], endpoint_url="http://localhost:8021/rpc", aic=spec.GATEWAY_AIC,
    )


# ── 1. ACS schema 一致性 ──────────────────────────────────────────────────


class TestAcsSchemaConformance:
    def test_design_record_matches_official_schema(self):
        """DESIGN.md 记录的 14 字段全集 ↔ 官方 schema required 必须一致（漂移即报警）。"""
        assert sorted(SCHEMA["required"]) == sorted(spec.AIP_ACS_REQUIRED_FROM_DESIGN)

    def test_translator_acs_passes_official_schema(self):
        jsonschema.validate(_generated_acs(), SCHEMA)

    def test_registry_build_acs_passes_official_schema(self):
        reg = McpRegistry(
            config_path=HERE.parents[1] / "gateway" / "config.toml",
            endpoint_url="http://localhost:8021/rpc", gateway_aic=spec.GATEWAY_AIC,
        )
        jsonschema.validate(reg.build_acs(), SCHEMA)  # 未 start()，全部上游离线=空 skills 合法

    def test_real_gen_acs_artifact_passes_official_schema(self):
        gen = HERE.parents[1] / "gen-acs.json"
        if not gen.exists():
            pytest.skip("gen-acs.json 不在仓内（PoC 产物未随库分发）")
        jsonschema.validate(json.loads(gen.read_text(encoding="utf-8")), SCHEMA)

    def test_agent_skill_required_fields(self):
        skill_required = SCHEMA["$defs"]["AgentSkill"]["required"]
        assert sorted(skill_required) == sorted(["id", "name", "description", "version", "tags"])
        skill = tool_to_skill("demo", _sample_tool())
        for field in skill_required:
            assert field in skill, f"翻译器 skill 缺官方必填字段 {field}"
        # 强烈建议字段（Discovery 语义检索依赖）：examples/inputModes/outputModes
        for recommended in ("examples", "inputModes", "outputModes"):
            assert recommended in skill

    def test_capabilities_and_endpoint_required_fields(self):
        assert sorted(SCHEMA["$defs"]["AgentCapabilities"]["required"]) == sorted(
            ["streaming", "notification", "messageQueue"]
        )
        assert sorted(SCHEMA["$defs"]["AgentEndPoint"]["required"]) == sorted(["url", "transport"])
        acs = _generated_acs()
        assert set(acs["capabilities"]) >= {"streaming", "notification", "messageQueue"}
        assert set(acs["endPoints"][0]) >= {"url", "transport"}

    def test_missing_required_field_is_rejected_by_schema(self):
        bad = _generated_acs()
        del bad["securitySchemes"]  # 缺一个必填字段
        with pytest.raises(jsonschema.ValidationError):
            jsonschema.validate(bad, SCHEMA)


# ── 2. AIC 身份码一致性（官方校验器驱动） ─────────────────────────────────


class TestAicConformance:
    def test_gateway_aic_passes_official_validator(self):
        from acps_sdk.aic import validate_aic_format

        ok, err = validate_aic_format(spec.GATEWAY_AIC)
        assert ok, f"网关 AIC 未过官方校验：{err}"

    @pytest.mark.parametrize(
        "bad_aic,why",
        [
            ("1.2.156.3088.1.1.GATE01.1.0001", "仅 9 段"),
            ("1.2.156.3088.1.1.MCPGW1.GATE01.1.0001.9", "11 段"),
            ("1.2..3088.1.1.MCPGW1.GATE01.1.0001", "空段"),
            ("1.2.999.3088.1.1.MCPGW1.GATE01.1.0001", "国家/机构前缀不匹配"),
            ("A.2.156.3088.1.1.MCPGW1.GATE01.1.0001", "第 1 级非纯数字"),
            ("1.2.156.3088.1.1.####.GATE01.1.0001", "第 6 级非 Base36"),
            ("", "空串"),
        ],
    )
    def test_malformed_aic_rejected_with_message(self, bad_aic, why):
        from acps_sdk.aic import validate_aic_format

        ok, err = validate_aic_format(bad_aic)
        assert not ok, f"非法 AIC（{why}）竟通过校验"
        assert err, "拒绝时必须给错误信息"


# ── 3. 状态机一致性 ───────────────────────────────────────────────────────


class TestStateMachineConformance:
    def test_spec_table_matches_official_enum(self):
        """规约表 8 态 ↔ acps_sdk TaskState 枚举必须一一对应。"""
        from acps_sdk.aip.aip_base_model import TaskState

        assert sorted(s.value for s in TaskState) == sorted(spec.AIP_ALL_STATES)

    @pytest.mark.parametrize(
        "flow",
        [
            [None, "working", "awaiting-completion", "completed"],  # DESIGN §1.2 主流程
            [None, "working", "failed"],  # 上游调用异常
            [None, "rejected"],  # 未知工具 / 非法输入
            [None, "accepted", "working", "awaiting-completion", "completed"],  # 带 accepted 初始态
            [None, "accepted", "canceled"],  # 取消
            [None, "working", "awaiting-input", "working", "awaiting-completion", "completed"],  # 补输入循环
        ],
    )
    def test_gateway_documented_flows_are_legal(self, flow):
        for old, new in zip(flow, flow[1:]):
            assert spec.is_legal_transition(old, new), f"非法迁移 {old} → {new}"

    @pytest.mark.parametrize(
        "old,new",
        [
            ("completed", "working"),  # 终态复活
            ("completed", "awaiting-completion"),
            ("rejected", "working"),  # rejected 终态
            ("failed", "completed"),
            ("awaiting-completion", "working"),  # 完成前不许回退
            ("accepted", "completed"),  # 不可跳过执行与交付确认
        ],
    )
    def test_illegal_transitions_rejected_by_table(self, old, new):
        assert not spec.is_legal_transition(old, new), f"{old} → {new} 应为非法迁移"

    def test_bridge_only_emits_legal_aip_states(self):
        """桥映射的全部 AIP 态必须落在官方 8 态之内（7 个 A2A 态 + 未收录兜底）。"""
        from gateway.bridge_a2a import aip_state_from_a2a

        for a2a_state in spec.A2A_TASK_STATES + ["some-future-state"]:
            assert aip_state_from_a2a(a2a_state) in spec.AIP_ALL_STATES


# ── 4. awaiting-input 语义 ────────────────────────────────────────────────


class TestAwaitingInputSemantics:
    def test_official_enum_contains_awaiting_input(self):
        from acps_sdk.aip.aip_base_model import TaskState

        assert TaskState.AwaitingInput.value == "awaiting-input"

    def test_wanbridge_mapping_decision_is_pinned(self):
        """万桥 v0 决策（M1 设计 §1.3）：input-required → awaiting-completion，非 awaiting-input。

        理由（见 spec_tables 模块注释）：沿用梧桐平台已验证流程。此测试把这个决策
        固化为可执行断言——若未来切到 awaiting-input，此处失败即提醒同步更新设计文档。
        """
        from gateway.bridge_a2a import task_to_call_result

        task = {"id": "t", "status": {"state": "input-required",
                "message": {"role": "agent", "parts": [{"type": "text", "text": "请补充年份"}]}}}
        result = task_to_call_result(task)
        assert result.aip_state == "awaiting-completion"
        assert result.text == "请补充年份"  # 继续信息必须透传（Leader/用户可 continue）

    def test_awaiting_input_roundtrip_is_legal(self):
        """若 v2 切换为 awaiting-input 映射，working→awaiting-input→working 补输入循环必须合法。"""
        assert spec.is_legal_transition("working", "awaiting-input")
        assert spec.is_legal_transition("awaiting-input", "working")
