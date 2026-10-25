"""Leader 自己的 ACS（智能体卡）——代码即唯一真源。

为什么要这个模块（现读，不是回忆）：公网登记的 `https://47.114.38.125.sslip.io/rpc` 由 caddy 转到
leader:18031（2026-10-04 只读勘察 `输出/_logs/r298_s0b_rpc_live.log`），而发卡一直是网关 A2A 面在做
（`mcp-aip-gateway/gateway/a2a_server.py:424`）——实测两条 well-known 在 8041 与经 443 **都 404**，
平台既不能自助取卡也没法刷新，手里那份还是 09-20 人工提交的 `version=1.3.0` 死快照。

口径（写死在这里，防自欺）：
  ① 这是 **Leader 的卡**：AIC 用我们自指派段（`…1.1.LDR001…`，与 `/health` 的 `leader_aic` 同源，
     `tests2/test_leader_card.py` 逐值钉这条），🔴 不复用平台分配的 `…BUPT…` literature 卡的任何内容；
  ② 只宣称 Leader 真正做的事（接研究问题→编排 Partner→汇总报告），不蹭文献检索那张卡的技能文案；
  ③ `gateway_compat.build_acs()` 的 `endPoints` 写死成 AMQP 且 url 里留着**没插值**的 `{AIC}` 占位符，
     本模块负责补成真值（公网 JSONRPC 入口 ＋ 带自身 AIC 的 AMQP 收件箱）——🔴 不把占位符发出去。
"""
from __future__ import annotations

from gateway_compat import build_acs

# 与 partners.toml 的 [leader] aic 同值；不一致会被 tests2/test_leader_card.py 的
# test_health_and_card_agree_on_the_leader_identity 判红（两处真源一旦漂就红，不是各写各的）。
LEADER_AIC = "1.2.156.3088.1.1.LDR001.LDR001.1.0002"
CARD_VERSION = "1.4.0"
# 🔴 固定值而不是 datetime.now()：一张活卡每次被取都说自己「刚刚改过」是假信息，
#    也会让「两条 well-known 逐字同卡」这种最强断言根本无法成立。改了卡内容就同轮抬这个值。
CARD_MODIFIED = "2026-10-04T16:58:00+00:00"
PUBLIC_RPC = "https://47.114.38.125.sslip.io/rpc"

_NAME = "研伴·科研协作主控智能体"
_DESC = (
    "研伴多智能体科研协作系统的 Leader：接收一个研究问题或任务，编排文献检索、实验设计、数据分析、"
    "论文写作、模拟审稿等 Partner 分工协作，汇总结构化结果与报告；对外经 AIP（JSONRPC /rpc 与 AMQP 收件箱）"
    "被调用并留审计记录。\n【输入要求】\n必填：研究问题或任务描述。\n"
    "可选：目标用途（综述/开题/投稿）、语种、文献数量上限、期望输出形态。\n"
    "缺少研究问题时返回 Task.state=\"awaiting-input\" 请对方补充。"
)
_SKILLS = [
    {
        "id": "yanban.leader.orchestrate",
        "name": "科研任务编排与汇总",
        "description": (
            "把一个研究问题拆成检索/实验设计/数据分析/写作/评审的子任务并按 AIP 派给对应 Partner，"
            "收集各 Partner 的结构化产物，产出带引用依据的汇总报告；任一子任务失败或缺输入时如实降级，"
            "不把没跑出来的东西写成结论。"
        ),
        "tags": ["多智能体编排", "科研协作", "任务分解", "汇总报告"],
        "examples": [
            "我想做『大语言模型多智能体协议互操作』的综述，帮我组织一条从检索到成稿的完整流程",
            "给你一个研究问题，请分工给文献、方法设计、数据分析和写作几个角色并给我最终报告",
        ],
        "inputModes": ["text/plain", "application/json"],
        "outputModes": ["text/markdown", "application/json"],
    },
]


def build(*, aic: str | None = None) -> dict:
    """现造一张过官方 acsSchema 的 Leader 卡（每次调用都带当前时间与当前版本）。"""
    card = build_acs(aic=aic or LEADER_AIC, name=_NAME, desc=_DESC, skills=_SKILLS, version=CARD_VERSION)
    card["lastModifiedTime"] = CARD_MODIFIED
    own = card["aic"]
    card["endPoints"] = [
        {"url": PUBLIC_RPC, "transport": "JSONRPC", "security": [{"mtls": []}]},
        {"url": f"amqps://wt.ioa.pub:5671/acps?inbox=inbox_{own}", "transport": "AMQP",
         "security": [{"mtls": []}]},
    ]
    return card
