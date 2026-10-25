# 万桥一致性测试报告（M4）

- 运行时间：2026-09-20 01:22:13（`datetime.now()` 实测）
- 范围：`tests/conformance/`（AIP 侧 + A2A 侧，规约事实来源见 `tests/conformance/spec_tables.py`）
- 结果：**62 通过 / 0 失败 / 0 跳过**（pytest 退出码 0）
- 复跑方式：`uv run python tests/conformance/run_conformance.py`

| 用例 | 结果 | 耗时(s) |
|---|---|---|
| `test_a2a_conformance.py::TestAgentCardConformance::test_card_has_full_required_field_set` | passed | 0.000 |
| `test_a2a_conformance.py::TestAgentCardConformance::test_card_capability_flags_all_false_v0` | passed | 0.000 |
| `test_a2a_conformance.py::TestAgentCardConformance::test_card_dual_modes_and_auth` | passed | 0.000 |
| `test_a2a_conformance.py::TestAgentCardConformance::test_skill_shape_matches_agent_skill_contract` | passed | 0.000 |
| `agent-card.json]` | passed | 0.006 |
| `agent.json]` | passed | 0.024 |
| `test_a2a_conformance.py::TestWellKnownDualPath::test_both_paths_identical` | passed | 0.029 |
| `test_a2a_conformance.py::TestSevenStateConformance::test_mapping_covers_exactly_the_seven_states` | passed | 0.000 |
| `test_a2a_conformance.py::TestSevenStateConformance::test_each_state_semantics[completed-completed]` | passed | 0.000 |
| `test_a2a_conformance.py::TestSevenStateConformance::test_each_state_semantics[input-required-awaiting-completion]` | passed | 0.000 |
| `test_a2a_conformance.py::TestSevenStateConformance::test_each_state_semantics[working-working]` | passed | 0.000 |
| `test_a2a_conformance.py::TestSevenStateConformance::test_each_state_semantics[submitted-working]` | passed | 0.000 |
| `test_a2a_conformance.py::TestSevenStateConformance::test_each_state_semantics[failed-failed]` | passed | 0.000 |
| `test_a2a_conformance.py::TestSevenStateConformance::test_each_state_semantics[unknown-failed]` | passed | 0.000 |
| `test_a2a_conformance.py::TestSevenStateConformance::test_each_state_semantics[canceled-failed]` | passed | 0.000 |
| `test_a2a_conformance.py::TestSevenStateConformance::test_artifacts_extracted_on_completed_only` | passed | 0.000 |
| `test_a2a_conformance.py::TestSevenStateConformance::test_canceled_carries_dedicated_label` | passed | 0.000 |
| `test_a2a_conformance.py::TestPartTypesConformance::test_text_part` | passed | 0.000 |
| `test_a2a_conformance.py::TestPartTypesConformance::test_data_part_dual_key_names` | passed | 0.000 |
| `test_a2a_conformance.py::TestPartTypesConformance::test_file_part_placeholder` | passed | 0.000 |
| `test_a2a_conformance.py::TestPartTypesConformance::test_unknown_part_type_never_crashes` | passed | 0.000 |
| `test_a2a_conformance.py::TestPartTypesConformance::test_server_accepts_all_input_part_channels` | passed | 0.000 |
| `test_a2a_conformance.py::TestMethodsAndErrorsConformance::test_module_exports_both_method_names` | passed | 0.000 |
| `send]` | passed | 0.003 |
| `send]` | passed | 0.003 |
| `test_a2a_conformance.py::TestMethodsAndErrorsConformance::test_standard_jsonrpc_error_codes[parse_error]` | passed | 0.002 |
| `test_a2a_conformance.py::TestMethodsAndErrorsConformance::test_standard_jsonrpc_error_codes[invalid_request]` | passed | 0.002 |
| `test_a2a_conformance.py::TestMethodsAndErrorsConformance::test_standard_jsonrpc_error_codes[method_not_found]` | passed | 0.002 |
| `test_a2a_conformance.py::TestMethodsAndErrorsConformance::test_standard_jsonrpc_error_codes[invalid_params]` | passed | 0.002 |
| `test_a2a_conformance.py::TestMethodsAndErrorsConformance::test_offline_card_fetch_via_mock_transport` | passed | 0.002 |
| `test_aip_conformance.py::TestAcsSchemaConformance::test_design_record_matches_official_schema` | passed | 0.000 |
| `test_aip_conformance.py::TestAcsSchemaConformance::test_translator_acs_passes_official_schema` | passed | 0.033 |
| `test_aip_conformance.py::TestAcsSchemaConformance::test_registry_build_acs_passes_official_schema` | passed | 0.031 |
| `test_aip_conformance.py::TestAcsSchemaConformance::test_real_gen_acs_artifact_passes_official_schema` | passed | 0.032 |
| `test_aip_conformance.py::TestAcsSchemaConformance::test_agent_skill_required_fields` | passed | 0.000 |
| `test_aip_conformance.py::TestAcsSchemaConformance::test_capabilities_and_endpoint_required_fields` | passed | 0.000 |
| `test_aip_conformance.py::TestAcsSchemaConformance::test_missing_required_field_is_rejected_by_schema` | passed | 0.030 |
| `test_aip_conformance.py::TestAicConformance::test_gateway_aic_passes_official_validator` | passed | 0.002 |
| `test_aip_conformance.py::TestAicConformance::test_malformed_aic_rejected_with_message[1.2.156.3088.1.1.GATE01.1.0001-\u4ec5 9 \u6bb5]` | passed | 0.000 |
| `test_aip_conformance.py::TestAicConformance::test_malformed_aic_rejected_with_message[1.2.156.3088.1.1.MCPGW1.GATE01.1.0001.9-11 \u6bb5]` | passed | 0.000 |
| `test_aip_conformance.py::TestAicConformance::test_malformed_aic_rejected_with_message[1.2..3088.1.1.MCPGW1.GATE01.1.0001-\u7a7a\u6bb5]` | passed | 0.000 |
| `\u673a\u6784\u524d\u7f00\u4e0d\u5339\u914d]` | passed | 0.000 |
| `test_aip_conformance.py::TestAicConformance::test_malformed_aic_rejected_with_message[A.2.156.3088.1.1.MCPGW1.GATE01.1.0001-\u7b2c 1 \u7ea7\u975e\u7eaf\u6570\u5b57]` | passed | 0.000 |
| `test_aip_conformance.py::TestAicConformance::test_malformed_aic_rejected_with_message[1.2.156.3088.1.1.####.GATE01.1.0001-\u7b2c 6 \u7ea7\u975e Base36]` | passed | 0.000 |
| `test_aip_conformance.py::TestAicConformance::test_malformed_aic_rejected_with_message[-\u7a7a\u4e32]` | passed | 0.000 |
| `test_aip_conformance.py::TestStateMachineConformance::test_spec_table_matches_official_enum` | passed | 0.232 |
| `test_aip_conformance.py::TestStateMachineConformance::test_gateway_documented_flows_are_legal[flow0]` | passed | 0.000 |
| `test_aip_conformance.py::TestStateMachineConformance::test_gateway_documented_flows_are_legal[flow1]` | passed | 0.000 |
| `test_aip_conformance.py::TestStateMachineConformance::test_gateway_documented_flows_are_legal[flow2]` | passed | 0.000 |
| `test_aip_conformance.py::TestStateMachineConformance::test_gateway_documented_flows_are_legal[flow3]` | passed | 0.000 |
| `test_aip_conformance.py::TestStateMachineConformance::test_gateway_documented_flows_are_legal[flow4]` | passed | 0.000 |
| `test_aip_conformance.py::TestStateMachineConformance::test_gateway_documented_flows_are_legal[flow5]` | passed | 0.000 |
| `test_aip_conformance.py::TestStateMachineConformance::test_illegal_transitions_rejected_by_table[completed-working]` | passed | 0.000 |
| `test_aip_conformance.py::TestStateMachineConformance::test_illegal_transitions_rejected_by_table[completed-awaiting-completion]` | passed | 0.000 |
| `test_aip_conformance.py::TestStateMachineConformance::test_illegal_transitions_rejected_by_table[rejected-working]` | passed | 0.000 |
| `test_aip_conformance.py::TestStateMachineConformance::test_illegal_transitions_rejected_by_table[failed-completed]` | passed | 0.000 |
| `test_aip_conformance.py::TestStateMachineConformance::test_illegal_transitions_rejected_by_table[awaiting-completion-working]` | passed | 0.000 |
| `test_aip_conformance.py::TestStateMachineConformance::test_illegal_transitions_rejected_by_table[accepted-completed]` | passed | 0.000 |
| `test_aip_conformance.py::TestStateMachineConformance::test_bridge_only_emits_legal_aip_states` | passed | 0.000 |
| `test_aip_conformance.py::TestAwaitingInputSemantics::test_official_enum_contains_awaiting_input` | passed | 0.000 |
| `test_aip_conformance.py::TestAwaitingInputSemantics::test_wanbridge_mapping_decision_is_pinned` | passed | 0.000 |
| `test_aip_conformance.py::TestAwaitingInputSemantics::test_awaiting_input_roundtrip_is_legal` | passed | 0.000 |
