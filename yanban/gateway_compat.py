"""ACS 构建公共函数（gen_acs.py 与未来注册流程共用）。"""


def build_acs(*, aic: str, name: str, desc: str, skills: list[dict], version: str = "1.0.0") -> dict:
    import datetime as _dt

    return {
        "aic": aic,
        "active": True,
        "lastModifiedTime": _dt.datetime.now(_dt.timezone.utc).isoformat(),
        "protocolVersion": "02.02",
        "name": name,
        "description": desc,
        "version": version,
        "provider": {"organization": "研伴科研协作团队", "department": "", "url": "", "license": ""},
        "securitySchemes": {"mtls": {"type": "mutualTLS", "description": "智能体间mTLS双向认证"}},
        # 🔴 轮 299 修：这里的 `{AIC}` 从来没被插值，四份 acs-*.json 因此带着字面占位符出厂
        #    （tests2/test_partner_card_routes.py 第 5 条抓到的就是它）。收件箱名必须是这张卡自己的 aic。
        "endPoints": [{"url": "amqps://wt.ioa.pub:5671/acps?inbox=inbox_" + aic, "transport": "AMQP",
                       "security": [{"mtls": []}]}],
        "capabilities": {"streaming": False, "notification": False, "messageQueue": ["rabbitmq:>=4.2"]},
        "defaultInputModes": ["text/plain", "application/json"],
        "defaultOutputModes": ["text/plain", "text/markdown", "application/json"],
        "skills": [
            {**s, "version": s.get("version", "1.0.0"), "examples": s.get("examples", []),
             "inputModes": s.get("inputModes", ["text/plain"]),
             "outputModes": s.get("outputModes", ["text/plain", "text/markdown"])}
            for s in skills
        ],
    }
