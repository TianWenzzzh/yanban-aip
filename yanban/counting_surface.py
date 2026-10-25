"""**不计数面**的申报（轮 306）：哪些入站路故意不落"被调用"账，以及为什么。

为什么要这张表：`tests2/test_counting_surface.py` 拿「每个 app 真实存在的路由」与
「`partner_base.COUNTED_ROUTES`（装了就会落账的）∪ 本文件的申报」对账，
任何一条入站路两边都没写 ⇒ 闸门红。功能迭代新开一条通道时，
"这条算不算被调用"必须有人当场决定并写下理由，而不是等两周后现读账本发现
只有 `/rpc` 一个值（轮 306 的实测起因：群组邀约与 MQ 指令两条被调用通道静默了两周多，
数不出来的东西没人来修）。

口径边界（与 `输出/scripts/wutong_call_volume.py` 的 V1 同一套）：
"被调用次数"数的是**别人让我们干了一次活**。健康探针、取卡、自家工作台 UI 的取数
都不是这件事，把它们计进去＝拿访客点击冒充受欢迎程度，比漏计更糟。
"""
from __future__ import annotations

# 精确路由 → 为什么不算"被调用"
EXACT = {
    "/health": "健康/存活探针，探针机与容器编排会反复打；计入会把 uptime 检查读成被调用",
    "/.well-known/agent-card.json": "取智能体卡＝读元数据，没有执行任何一次能力",
    "/.well-known/agent.json": "同一颗卡的别名路径，同上",
    "/debug": "内部调试读数面",
    "/stats/bench": "自家 bench 数据的只读展示面",
    "/stats/quality": "同上",
    "/amp/latest": "读自家 AMP 账本的展示面（它读账，不进账）",
    "/identity": "协议演示页",
    "/protocol/aip": "我们作为调用方主动打别人的演示入口，方向是出站",
    "/protocol/a2a": "同上",
    "/ui": "自带的工作台单页",
    "/task": "经典编排的单任务面，输入走的是自家前端，不是平台调用方",
    "/task/{task_id}": "同上（按 id 查那一条，自家前端打）",
    "/": "静态工作台目录（浏览器访客）",
    # FastAPI 自建的应用级面（每张 app 都有，不是业务入口）
    "/openapi.json": "OpenAPI 描述文件（框架自带）",
    "/docs": "Swagger UI（框架自带）",
    "/docs/oauth2-redirect": "Swagger 的 OAuth 回跳页（框架自带）",
    "/redoc": "ReDoc（框架自带）",
}

# 前缀 → 为什么不算"被调用"
PREFIX = {
    "/companion/": "2.0 认知层的自家 API（浏览器/工作台调用，70 余条）；"
                   "它不属于「被别的智能体调用」这一项，要量使用强度另有会话级尺子，"
                   "把 UI 取数计进次数＝把一次对话读成几十次被调用",
}


def declared_exact() -> set[str]:
    return set(EXACT)


def covers_prefix(path: str) -> bool:
    return any(path == p or path.startswith(p.rstrip("/") + "/") for p in PREFIX)
