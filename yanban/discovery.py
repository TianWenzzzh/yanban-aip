"""梧桐 Discovery 客户端（ADP 协议 /acps-adp-v2/discover，W1）。

依据：ACPs-community/discovery-server/app/discovery/discovery_api.py 契约 + 官方
acps_sdk.adp.models（DiscoveryRequest/DiscoveryResult/DiscoveryAgentGroup/DiscoveryAgentSkill）。
R12 记录：模型与路由定义直接读自官方源码（2026-09-12），未凭训练记忆实现。

语义：Leader 把子任务描述发给 Discovery，取回候选（agent_skills[{aic,skill_id,ranking,memo}]）
+ acs_map（aic → 完整 ACS 文档，含 endPoints）。候选按本地关键词重排后供 Leader 选端点；
任何失败抛 DiscoveryError，调用方（Orchestrator）降级回静态 partners.toml（R18 兜底）。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

import httpx

DISCOVER_PATH = "/acps-adp-v2/discover"
DEFAULT_TIMEOUT = 10.0


class DiscoveryError(RuntimeError):
    """Discovery 不可达/协议错误（调用方应降级静态表，不中断编排）。"""


@dataclass
class DiscoveryCandidate:
    """一个被发现的能力提供者（宽容解析：字段缺失不炸）。"""

    aic: str = ""
    group: str = ""
    skill_id: str = ""
    skill_memo: str = ""
    ranking: int = 0
    acs: dict = field(default_factory=dict)
    endpoint: str = ""  # 从 ACS.endPoints[0].url 提取（即 /rpc 地址）
    agent_name: str = ""
    description: str = ""
    source: str = "discovery"


def _endpoint_from_acs(acs: dict) -> str:
    for ep in (acs.get("endPoints") or []):
        if isinstance(ep, dict) and ep.get("url"):
            return str(ep["url"])
    return ""


# 通用停用 2-gram（T2 联测实测：demo Agent 名/描述里的"智能体/服务/推荐"等
# 会在任何查询上制造伪重叠，剔除后再计分）
STOP_GRAMS = {"智能", "能体", "服务", "推荐", "系统", "用户", "提供", "支持",
              "平台", "应用", "功能", "内容", "信息", "相关", "进行"}


def _cjk_tokens(text: str) -> set[str]:
    """极简中文分词：连续汉字按 2-gram 切 + 英数按词切（本地重排用，够准且零依赖）。"""
    text = (text or "").lower()
    tokens = set(re.findall(r"[a-z0-9_]+", text))
    han = re.findall(r"[\u4e00-\u9fff]+", text)
    for seg in han:
        if len(seg) == 1:
            tokens.add(seg)
        for i in range(len(seg) - 1):
            gram = seg[i:i + 2]
            if gram not in STOP_GRAMS:
                tokens.add(gram)
    return tokens


# 最小相关性阈值（T2 联测实测：demo Agent 描述含"智能体"等通用词时单 token 重叠即误命中；
# ≥2 才视为语义相关，否则 Leader 回落静态表）
MIN_MATCH_SCORE = 2


def match_score(candidate: DiscoveryCandidate, subtask_text: str) -> int:
    """候选与子任务文本的关键词重叠度（本地重排；不依赖向量服务）。"""
    hay = _cjk_tokens(f"{candidate.skill_memo} {candidate.skill_id} "
                      f"{candidate.agent_name} {candidate.description}")
    if not hay:
        return 0
    return len(hay & _cjk_tokens(subtask_text))


def rank_candidates(candidates: list[DiscoveryCandidate], subtask_text: str) -> list[DiscoveryCandidate]:
    """本地重排：关键词重叠度降序，同分按 ranking 降序。"""
    return sorted(candidates, key=lambda c: (match_score(c, subtask_text), c.ranking), reverse=True)


async def discover(
    base_url: str,
    query: str,
    *,
    limit: int = 3,
    client: httpx.AsyncClient | None = None,
    timeout: float = DEFAULT_TIMEOUT,
) -> list[DiscoveryCandidate]:
    """ADP 发现：POST {base}/acps-adp-v2/discover → 候选列表（已本地重排）。

    任何失败（超时/HTTP 非 200/协议 error/解析异常）抛 DiscoveryError——调用方降级静态表。
    传入 client 时不接管生命周期（测试注入 MockTransport）。
    """
    owned = client is None
    client = client or httpx.AsyncClient(timeout=timeout)
    try:
        body = {"type": "explicit", "query": query, "limit": limit}
        try:
            r = await client.post(base_url.rstrip("/") + DISCOVER_PATH, json=body)
        except (httpx.TimeoutException, httpx.HTTPError) as exc:
            raise DiscoveryError(f"Discovery 请求失败：{exc}") from exc
        if r.status_code != 200:
            raise DiscoveryError(f"Discovery HTTP {r.status_code}")
        try:
            payload = r.json()
        except ValueError as exc:
            raise DiscoveryError("Discovery 响应不是合法 JSON") from exc
        if not isinstance(payload, dict):
            raise DiscoveryError("Discovery 响应结构异常")
        if payload.get("error"):
            raise DiscoveryError(f"Discovery 协议错误：{payload['error']}")
        result = payload.get("result") or {}
        if not isinstance(result, dict):
            raise DiscoveryError("Discovery result 结构异常")
        # 键名宽容（实测 discovery-server /discover 输出 camelCase：acsMap/agentSkills；
        # 官方 SDK 模型为 snake_case——双键名兼容，与万桥 bridge 的宽容原则一致）
        acs_map = result.get("acsMap") or result.get("acs_map") or {}
        candidates: list[DiscoveryCandidate] = []
        for group in result.get("agents") or []:
            group_name = str((group or {}).get("group") or "")
            skills = group.get("agentSkills") or group.get("agent_skills") or []
            for sk in skills or []:
                if not isinstance(sk, dict):
                    continue  # 宽容：混入非 dict 元素直接跳过
                aic = str(sk.get("aic") or "")
                acs = acs_map.get(aic) or acs_map.get(group_name) or {}
                if not isinstance(acs, dict):
                    acs = {}
                candidates.append(DiscoveryCandidate(
                    aic=aic,
                    group=group_name,
                    skill_id=str(sk.get("skillId") or sk.get("skill_id") or ""),
                    skill_memo=str(sk.get("memo") or ""),
                    ranking=int(sk.get("ranking") or 0),
                    acs=acs,
                    endpoint=_endpoint_from_acs(acs),
                    agent_name=str(acs.get("name") or group_name),
                    description=str(acs.get("description") or "")[:200],
                ))
        return rank_candidates(candidates, query)
    finally:
        if owned:
            await client.aclose()
