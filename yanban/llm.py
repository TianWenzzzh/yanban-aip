"""统一 LLM 封装（OpenAI 兼容 / GLM）。

所有 GLM 调用走这里：重试 + 指数退避 + JSON 模式辅助。
key 从环境变量 GLM_API_KEY 读取（夜间任务书已约定）。
"""

from __future__ import annotations

import asyncio
import json
import os
from concurrent.futures import ThreadPoolExecutor

import httpx

GLM_API_KEY = os.environ.get("GLM_API_KEY", "")
GLM_BASE_URL = os.environ.get("GLM_BASE_URL", "https://open.bigmodel.cn/api/paas/v4")
GLM_MODEL = os.environ.get("GLM_MODEL", "glm-4-flash")

# LLM_MOCK=1：测试/离线模式——所有调用返回确定性 mock 文本，零真实 API 消耗（研伴 2.0 任务书 §1.2）
LLM_MOCK = os.environ.get("LLM_MOCK", "") == "1"

# 真实（非 MOCK）请求次数，供长时间自主运行的预算核查；MOCK 模式恒为 0
REAL_CALLS = 0


class LLMError(Exception):
    pass


def run_async(coro):
    """在同步上下文里执行协程：无运行中事件循环就直接跑，有则丢给独立线程的循环。

    dialogue_manager.handle_message 既被 FastAPI 线程池里的同步端点调用，也被同步测试调用。
    """
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)
    with ThreadPoolExecutor(max_workers=1) as ex:
        return ex.submit(asyncio.run, coro).result()


async def chat_stream(
    prompt: str,
    *,
    system: str = "",
    temperature: float = 0.4,
    max_tokens: int = 2048,
    timeout: float = 60.0,
    model: str | None = None,
):
    """流式单轮对话，逐段 yield 文本增量；失败抛 LLMError（不重试，交给调用方降级）。

    LLM_MOCK=1 时把确定性 mock 文本切片吐出，零网络请求。
    """
    global REAL_CALLS
    if LLM_MOCK:
        mock = await chat(prompt, system=system, max_tokens=max_tokens)
        for i in range(0, len(mock), 24):
            yield mock[i:i + 24]
        return
    messages = ([{"role": "system", "content": system}] if system else []) + [
        {"role": "user", "content": prompt}
    ]
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            async with client.stream(
                "POST", f"{GLM_BASE_URL}/chat/completions",
                headers={"Authorization": f"Bearer {GLM_API_KEY}"},
                json={"model": model or GLM_MODEL, "messages": messages,
                      "temperature": temperature, "max_tokens": max_tokens, "stream": True},
            ) as r:
                if r.status_code >= 400:
                    raise LLMError(f"GLM 流式调用失败：HTTP {r.status_code}")
                REAL_CALLS += 1
                async for line in r.aiter_lines():
                    if not line.startswith("data:"):
                        continue
                    payload = line[5:].strip()
                    if payload == "[DONE]":
                        break
                    try:
                        delta = json.loads(payload)["choices"][0]["delta"].get("content") or ""
                    except (ValueError, KeyError, IndexError):
                        continue
                    if delta:
                        yield delta
    except LLMError:
        raise
    except Exception as exc:  # noqa: BLE001 — 统一成 LLMError 供上层降级
        raise LLMError(f"GLM 流式调用失败：{exc}") from exc


async def chat(
    prompt: str,
    *,
    system: str = "",
    temperature: float = 0.4,
    max_tokens: int = 2048,
    retries: int = 2,
    timeout: float = 150.0,  # v5.5：GLM 长生成实测 >60s（bench 抓包 experiment/review 30016ms 全灭），60→150
    model: str | None = None,
) -> str:
    """单轮对话，返回文本。失败重试（指数退避），最终失败抛 LLMError。

    LLM_MOCK=1 时返回确定性 mock 文本（含 prompt 摘要），不发起任何网络请求。
    """
    global REAL_CALLS
    if LLM_MOCK:
        head = " ".join(prompt.split())[:80]
        return (
            f"[MOCK-LLM] system={system[:40]!r} prompt={head!r} "
            "这是一个确定性 mock 回复：用于 LLM_MOCK 测试模式，内容不代表真实模型输出。"
        )
    messages = ([{"role": "system", "content": system}] if system else []) + [
        {"role": "user", "content": prompt}
    ]
    last_exc: Exception | None = None
    for attempt in range(retries + 1):
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                r = await client.post(
                    f"{GLM_BASE_URL}/chat/completions",
                    headers={"Authorization": f"Bearer {GLM_API_KEY}"},
                    json={"model": model or GLM_MODEL, "messages": messages, "temperature": temperature, "max_tokens": max_tokens},
                )
                r.raise_for_status()
                REAL_CALLS += 1
                return r.json()["choices"][0]["message"]["content"]
        except Exception as exc:  # noqa: BLE001
            last_exc = exc
            if attempt < retries:
                await asyncio.sleep(2**attempt)
    raise LLMError(f"GLM 调用失败（{retries + 1} 次）：{last_exc}") from last_exc


async def chat_json(prompt: str, *, system: str = "", **kw) -> dict | list:
    """要求模型输出 JSON 并解析；自动剥离 ```json 围栏。解析失败抛 LLMError。

    LLM_MOCK=1 时返回确定性 mock dict。
    """
    import json
    import re

    if LLM_MOCK:
        return {"mock": True, "prompt_head": " ".join(prompt.split())[:60], "items": []}

    text = await chat(prompt, system=system, **kw)
    data = parse_json(text)
    if data is None:
        raise LLMError(f"LLM 输出不是合法 JSON：{text[:200]}")
    return data


def parse_json(text: str) -> dict | list | None:
    """从模型输出里取 JSON，按三种口径依次试。

    线上实测：模型会漏掉收尾围栏（"```json\\n{...}" 就结束），或在 JSON 前后补一句中文说明。
    原来只认"完整围栏"，漏收尾时整段文本被当成 JSON 去 loads → 抛 LLMError →
    该轮结构化通道（意图抽取 / 对比表 / TLDR）整轮作废，且用户完全看不出来。
    """
    import json
    import re

    if not text:
        return None
    raw = text.strip()
    candidates = [raw]
    fenced = re.search(r"```(?:json)?\s*(.*?)```", raw, re.S)
    if fenced:
        candidates.insert(0, fenced.group(1).strip())
    head = min((i for i in (raw.find("{"), raw.find("[")) if i >= 0), default=-1)
    tail = max(raw.rfind("}"), raw.rfind("]"))
    if 0 <= head < tail:
        candidates.append(raw[head:tail + 1])
    for cand in candidates:
        try:
            data = json.loads(cand)
        except json.JSONDecodeError:
            continue
        if isinstance(data, (dict, list)):
            return data
    return None
