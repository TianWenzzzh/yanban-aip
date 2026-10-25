"""MCP Tool → ACS Skill 翻译器（网关核心之一）。

设计对应（研究结论）：
- MCP Tool.name           → skill.id 的尾段（带服务命名空间前缀防冲突，参照 mcp-gateway）
- MCP Tool.title/name     → skill.name
- MCP Tool.description + inputSchema 参数摘要 → skill.description（生成【输入要求】段，
  与梧桐已过审 demo 的技能描述风格一致，便于 Discovery 语义检索命中）
- MCP Tool.inputSchema    → skill.examples（按参数类型生成示例调用）
- 固定 inputModes/outputModes：AIP 侧统一走 JSON 参数 + 文本/JSON 产出
"""

from __future__ import annotations

from typing import Any

NS_SEPARATOR = "__"

_TYPE_HINT_CN = {
    "string": "文本",
    "integer": "整数",
    "number": "数字",
    "boolean": "布尔",
    "array": "列表",
    "object": "对象",
}


def _param_summary(input_schema: dict[str, Any] | None) -> str:
    """把 MCP inputSchema 翻译成中文参数说明（必填/可选分组）。"""
    if not input_schema:
        return ""
    props = input_schema.get("properties") or {}
    required = set(input_schema.get("required") or [])
    if not props:
        return ""
    lines = ["【输入要求】"]
    req = [n for n in props if n in required]
    opt = [n for n in props if n not in required]
    if req:
        lines.append("必填：" + "、".join(
            f"{n}（{_TYPE_HINT_CN.get((props[n] or {}).get('type', ''), '任意')}）" for n in req
        ))
    if opt:
        lines.append("可选：" + "、".join(
            f"{n}（{_TYPE_HINT_CN.get((props[n] or {}).get('type', ''), '任意')}）" for n in opt
        ))
    lines.append('参数以 JSON 对象提供，例如 {"' + (req[0] if req else next(iter(props))) + '": ...}。')
    return "\n".join(lines)


def _example_for(tool_name: str, input_schema: dict[str, Any] | None) -> str:
    """按 inputSchema 生成一条自然语言示例调用（进 skill.examples，供 Discovery 检索）。"""
    props = input_schema.get("properties") or {} if input_schema else {}
    sample: dict[str, Any] = {}
    for name, spec in props.items():
        t = (spec or {}).get("type", "string")
        sample[name] = {"string": "示例值", "integer": 10, "number": 1.5, "boolean": True}.get(t, "示例值")
        if len(sample) >= 2:
            break
    args = "，".join(f"{k}={v}" for k, v in sample.items())
    return f"请调用工具 {tool_name}：{args}"


def _get_schema(tool: Any) -> dict[str, Any] | None:
    """兼容 MCP SDK v1（camelCase）与 v2（snake_case）的 inputSchema 取值。"""
    for attr in ("inputSchema", "input_schema"):
        v = getattr(tool, attr, None)
        if isinstance(v, dict):
            return v
    return None


def tool_to_skill(server_ns: str, tool: Any) -> dict[str, Any]:
    """MCP Tool → ACS skill 对象（符合 acsSchema.json 的 AgentSkill 必填项）。"""
    name = tool.name
    desc = (tool.description or f"MCP 工具 {name}").strip()
    schema = _get_schema(tool)
    summary = _param_summary(schema)
    description = desc + ("\n" + summary if summary else "")
    description += "\n本能力由 MCP↔AIP 网关自动封装（无需修改原 MCP 工具）。"

    annotations = getattr(tool, "annotations", None)
    tags = [server_ns, "MCP工具"]
    if annotations and getattr(annotations, "title", None):
        tags.append(str(annotations.title))

    return {
        "id": f"{server_ns}{NS_SEPARATOR}{name}",
        "name": str(getattr(tool, "title", None) or name),
        "description": description,
        "version": "1.0.0",
        "tags": tags,
        "examples": [_example_for(name, schema)],
        "inputModes": ["text/plain", "application/json"],
        "outputModes": ["text/plain", "application/json"],
    }


def tools_to_acs(
    *,
    agent_name: str,
    description: str,
    server_ns: str,
    tools: list[Any],
    endpoint_url: str,
    aic: str = "",
) -> dict[str, Any]:
    """工具清单 → 完整 ACS 文档（可直接提交梧桐注册）。"""
    import datetime as _dt

    return {
        "aic": aic,
        "active": True,
        "lastModifiedTime": _dt.datetime.now(_dt.timezone.utc).isoformat(),
        "protocolVersion": "02.02",
        "name": agent_name,
        "description": description,
        "version": "1.0.0",
        "provider": {"organization": "研伴科研协作团队", "department": "", "url": "", "license": ""},
        "securitySchemes": {"mtls": {"type": "mutualTLS", "description": "智能体间mTLS双向认证"}},
        "endPoints": [
            {
                "url": endpoint_url,
                "transport": "JSONRPC",
                "security": [{"mtls": []}],
            }
        ],
        "capabilities": {"streaming": False, "notification": False, "messageQueue": []},
        "defaultInputModes": ["text/plain", "application/json"],
        "defaultOutputModes": ["text/plain", "application/json"],
        "skills": [tool_to_skill(server_ns, t) for t in tools],
    }
