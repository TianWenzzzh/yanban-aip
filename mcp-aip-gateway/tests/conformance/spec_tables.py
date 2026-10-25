"""万桥一致性套件的规约事实表（spec-as-data，测试的唯一事实来源）。

出处（R9 合规，全部可溯源）：
- AIP ACS schema：fixtures/acsSchema.json——复制自 ACPs-community/registry-server/app/agent/acsSchema.json
  （2026-09-06 快照；刷新方法：重新复制并与 DESIGN.md §1.2 实测记录核对）
- AIP 任务状态机：acps_sdk.aip.aip_base_model.TaskState（8 态枚举，2026-09-06 读源）
  + DESIGN.md §1.2/§4 实测流程（start→(working)→awaiting-completion→complete→completed；
  未知工具→rejected；调用异常→failed）
- AIP AIC：acps_sdk.aic.validate_aic_format（官方校验器：10 段 OID、前缀 1.2.156.3088、
  1-4 级纯数字、5-10 级 Base36——一致性测试直接驱动官方实现，不自行发明规则）
- A2A：docs/design_wanbridge_a2a.md 头部规范核实记录（a2a-protocol.org / agent2agent.info，
  2026-09-06 抓取）

万桥映射决策（M1 设计 §1.3，已评审固化）：A2A input-required → AIP awaiting-completion
（非 awaiting-input；理由：沿用梧桐平台已验证流程，继续信息经 message 文本透传，
Leader/用户可 continue。语义更纯的 awaiting-input 映射列为 v2 候选，见白皮书路线图）。
"""

from __future__ import annotations

# ── AIP 侧 ────────────────────────────────────────────────────────────────

# acsSchema.json 顶层必填（DESIGN.md §1.2 记录的 14 字段全集；测试同时动态派生 schema
# 的 required 与本常量比对——schema 变更即测试报警，交人工复核）
AIP_ACS_REQUIRED_FROM_DESIGN = [
    "aic", "active", "lastModifiedTime", "protocolVersion", "name", "description",
    "version", "provider", "securitySchemes", "endPoints", "capabilities",
    "defaultInputModes", "defaultOutputModes", "skills",
]

# TaskState 8 态（与 acps_sdk TaskState 枚举一一对应；测试断言两者相等）
AIP_ALL_STATES = [
    "accepted", "working", "awaiting-input", "awaiting-completion",
    "completed", "canceled", "failed", "rejected",
]

# 规范迁移表：state → 允许迁往的状态集合（空集 = 终态）。
# 依据：DESIGN.md §1.2 实测流程 + partner.py v1.2 已验证路径；awaiting-input 出自官方枚举
# （网关 v1.2 流程未使用，预留）。None 键 = 任务创建时的合法初始态。
AIP_TRANSITIONS: dict[str | None, set[str]] = {
    None: {"accepted", "working", "awaiting-completion", "rejected", "failed"},
    "accepted": {"working", "canceled", "failed"},
    "working": {"awaiting-input", "awaiting-completion", "failed", "canceled"},
    "awaiting-input": {"working", "failed", "canceled"},
    "awaiting-completion": {"completed", "failed", "canceled"},
    "completed": set(),
    "failed": set(),
    "rejected": set(),
    "canceled": set(),
}

GATEWAY_AIC = "1.2.156.3088.1.1.MCPGW1.GATE01.1.0001"

# ── A2A 侧（M1 设计文档 §1/§2 核实记录） ─────────────────────────────────

# Task 7 态全集（TaskStatus.state 的合法取值）
A2A_TASK_STATES = [
    "submitted", "working", "input-required", "completed", "canceled", "failed", "unknown",
]

# AgentCard 必备字段（§2.1 样例全集——万桥按"字段尽量全给"原则，客户端按可选解析）
A2A_CARD_REQUIRED = [
    "name", "description", "url", "version", "protocolVersion", "provider",
    "capabilities", "defaultInputModes", "defaultOutputModes", "authentication", "skills",
]

# JSON-RPC 方法名：send 双兼容（v0.2-3 tasks/send 与最新 message/send）；
# v2.0.0-alpha（R12 核实 a2a-sdk 1.1.2）新增 tasks/get、tasks/cancel
A2A_METHODS_REQUIRED = ("tasks/send", "message/send")
A2A_METHODS_FULL = A2A_METHODS_REQUIRED + ("tasks/get", "tasks/cancel", "tasks/resubscribe")

# 标准 JSON-RPC 错误码
A2A_JSONRPC_ERRORS = {
    "parse_error": -32700,
    "invalid_request": -32600,
    "method_not_found": -32601,
    "invalid_params": -32602,
}

# Part 三类型（type/kind 双键名宽容；data/json 双载荷键名宽容）
A2A_PART_TYPES = ["text", "file", "data"]


def is_legal_transition(old: str | None, new: str) -> bool:
    """AIP 状态迁移合法性判定（查 AIP_TRANSITIONS 规范表）。"""
    return new in AIP_TRANSITIONS.get(old, set())
