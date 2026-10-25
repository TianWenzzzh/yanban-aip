"""translator.py 离线单元测试（无网络、无 MCP 依赖，用 SimpleNamespace 桩模拟 Tool 对象）。"""

from types import SimpleNamespace as NS

from gateway.translator import (
    _example_for,
    _get_schema,
    _param_summary,
    tool_to_skill,
    tools_to_acs,
)


def _tool(name="search", title=None, desc=None, schema=None, annotations=None):
    return NS(
        name=name,
        title=title,
        description=desc,
        inputSchema=schema,
        input_schema=schema,  # 同给两份不影响：_get_schema 按属性优先级取
        annotations=annotations,
    )


class TestParamSummary:
    def test_empty_schema_returns_empty(self):
        assert _param_summary(None) == ""
        assert _param_summary({}) == ""
        assert _param_summary({"properties": {}}) == ""

    def test_required_optional_split_and_cn_types(self):
        s = {
            "type": "object",
            "required": ["repo_path"],
            "properties": {
                "repo_path": {"type": "string"},
                "max_count": {"type": "integer", "description": "n"},
                "flag": {"type": "boolean"},
            },
        }
        out = _param_summary(s)
        assert out.startswith("【输入要求】")
        assert "必填：repo_path（文本）" in out
        assert "可选：max_count（整数）、flag（布尔）" in out
        assert '例如 {"repo_path": ...}' in out

    def test_all_optional_no_required_line(self):
        s = {"properties": {"a": {"type": "number"}}}
        out = _param_summary(s)
        assert "必填" not in out
        assert "可选：a（数字）" in out

    def test_unknown_type_falls_back(self):
        s = {"properties": {"x": {}}}
        assert "x（任意）" in _param_summary(s)


class TestExampleFor:
    def test_type_sample_values(self):
        s = {"properties": {"q": {"type": "string"}, "n": {"type": "integer"}}}
        out = _example_for("search", s)
        assert out == "请调用工具 search：q=示例值，n=10"

    def test_caps_at_two_params(self):
        s = {"properties": {k: {"type": "string"} for k in "abc"}}
        assert _example_for("t", s).count("=") == 2

    def test_none_schema(self):
        assert _example_for("t", None) == "请调用工具 t："


class TestGetSchema:
    def test_v1_camelcase(self):
        t = NS(name="a", inputSchema={"properties": {}}, input_schema=None)
        assert _get_schema(t) == {"properties": {}}

    def test_v2_snake_case(self):
        t = NS(name="a", inputSchema=None, input_schema={"properties": {"x": {}}})
        assert _get_schema(t) == {"properties": {"x": {}}}

    def test_non_dict_ignored(self):
        t = NS(name="a", inputSchema="not-a-dict", input_schema=None)
        assert _get_schema(t) is None


class TestToolToSkill:
    def test_namespacing_and_fallbacks(self):
        skill = tool_to_skill("git", _tool(name="git_log", title=None, desc=None))
        assert skill["id"] == "git__git_log"
        assert skill["name"] == "git_log"  # 无 title 时回退 name
        assert skill["description"].startswith("MCP 工具 git_log")  # 无描述时的兜底
        assert "MCP↔AIP 网关自动封装" in skill["description"]
        assert skill["tags"] == ["git", "MCP工具"]
        assert skill["examples"] and skill["inputModes"] == ["text/plain", "application/json"]

    def test_title_and_annotations_flow_into_tags(self):
        ann = NS(title="Git Log")
        skill = tool_to_skill("git", _tool(name="git_log", title="日志", desc="查日志", annotations=ann))
        assert skill["name"] == "日志"
        assert "Git Log" in skill["tags"]
        assert skill["description"].startswith("查日志")

    def test_schema_enriches_description_and_example(self):
        schema = {"required": ["q"], "properties": {"q": {"type": "string"}}}
        skill = tool_to_skill("ns", _tool(name="s", schema=schema))
        assert "【输入要求】" in skill["description"]
        assert "q=示例值" in skill["examples"][0]


class TestToolsToAcs:
    def _acs(self):
        return tools_to_acs(
            agent_name="gw",
            description="测试网关",
            server_ns="ns",
            tools=[_tool(name="t1"), _tool(name="t2")],
            endpoint_url="http://127.0.0.1:8021/rpc",
            aic="1.2.3",
        )

    def test_required_acs_fields(self):
        acs = self._acs()
        for k in (
            "aic", "active", "lastModifiedTime", "protocolVersion", "name",
            "description", "version", "provider", "securitySchemes", "endPoints",
            "capabilities", "defaultInputModes", "defaultOutputModes", "skills",
        ):
            assert k in acs, f"缺 ACS 必填字段 {k}"
        assert acs["aic"] == "1.2.3" and acs["active"] is True

    def test_skills_mapped_per_tool(self):
        acs = self._acs()
        assert [s["id"] for s in acs["skills"]] == ["ns__t1", "ns__t2"]
        assert acs["endPoints"][0]["url"].endswith(":8021/rpc")
        assert acs["securitySchemes"]["mtls"]["type"] == "mutualTLS"
