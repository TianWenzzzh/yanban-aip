"""目录向量检索（W5→T5）：/tools/search?mode=vector 的语义层。

- GLM embedding-3（OpenAI 兼容 /embeddings，批量）编码聚合目录的 id+name+description
- 查询同模型编码 → 余弦相似 top-k
- 懒构建 + 进程内缓存（目录变更后下次查询重建）
- 任何失败（无 key/网络/格式）→ 调用方降级关键词检索（既有路径，语义不变）
- 边界声明：不引入本地模型下载（R6）；embedding 维度以服务返回为准（GLM embedding-3 默认 2048）

配置：WANBRIDGE_EMBEDDING_API_KEY / WANBRIDGE_EMBEDDING_BASE_URL（默认智谱 v4）/ WANBRIDGE_EMBEDDING_MODEL（默认 embedding-3）
"""

from __future__ import annotations

import math
import os
import time
from typing import Any, Callable

import httpx

DEFAULT_BASE_URL = "https://open.bigmodel.cn/api/paas/v4"
DEFAULT_MODEL = "embedding-3"


class VectorSearchError(RuntimeError):
    """向量检索不可用（调用方应降级关键词）。"""


def _config() -> tuple[str, str, str]:
    api_key = os.environ.get("WANBRIDGE_EMBEDDING_API_KEY", "").strip()
    base = os.environ.get("WANBRIDGE_EMBEDDING_BASE_URL", DEFAULT_BASE_URL).rstrip("/")
    model = os.environ.get("WANBRIDGE_EMBEDDING_MODEL", DEFAULT_MODEL)
    return api_key, base, model


async def embed_texts(texts: list[str], *, client: httpx.AsyncClient | None = None,
                      api_key: str | None = None, base_url: str | None = None,
                      model: str | None = None) -> list[list[float]]:
    """批量编码；失败抛 VectorSearchError。传入 client 时不接管生命周期。"""
    key, cfg_base, cfg_model = _config()
    key = api_key if api_key is not None else key
    base = base_url or cfg_base
    model = model or cfg_model
    if not key:
        raise VectorSearchError("未配置 WANBRIDGE_EMBEDDING_API_KEY")
    owned = client is None
    client = client or httpx.AsyncClient(timeout=30.0)
    try:
        try:
            r = await client.post(f"{base}/embeddings",
                                  headers={"Authorization": f"Bearer {key}"},
                                  json={"model": model, "input": texts})
        except httpx.HTTPError as exc:
            raise VectorSearchError(f"embedding 请求失败：{exc}") from exc
        if r.status_code != 200:
            raise VectorSearchError(f"embedding HTTP {r.status_code}: {r.text[:100]}")
        try:
            data = r.json()["data"]
            return [item["embedding"] for item in sorted(data, key=lambda x: x.get("index", 0))]
        except (ValueError, KeyError, TypeError) as exc:
            raise VectorSearchError(f"embedding 响应格式异常：{exc}") from exc
    finally:
        if owned:
            await client.aclose()


def _cosine(a: list[float], b: list[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    return dot / (na * nb) if na and nb else 0.0


class VectorIndex:
    """聚合目录的向量索引（懒构建 + 缓存；embed_fn 可注入供测试）。"""

    def __init__(self, embed_fn: Callable[..., Any] | None = None) -> None:
        self._embed_fn = embed_fn or embed_texts
        self._cache: dict[tuple, list[tuple[str, list[float]]]] = {}
        self._built_at: dict[tuple, float] = {}

    async def query(self, catalog: dict[str, tuple[Any, Any]], text: str, *,
                    limit: int = 5, client: httpx.AsyncClient | None = None) -> list[dict[str, Any]]:
        if not text.strip() or not catalog:
            return []
        entries = []
        for full_id, (_state, tool) in catalog.items():
            entries.append((full_id, str(getattr(tool, "name", "") or ""),
                            str(getattr(tool, "description", "") or "")))
        cache_key = (tuple(sorted(e[0] for e in entries)),)
        if cache_key not in self._cache:
            vectors = await self._embed_fn([f"{i} {n} {d}" for i, n, d in entries],
                                           client=client)
            if len(vectors) != len(entries):
                raise VectorSearchError(f"embedding 数量不匹配 {len(vectors)}/{len(entries)}")
            self._cache[cache_key] = [(e[0], v) for e, v in zip(entries, vectors)]
            self._built_at[cache_key] = time.monotonic()
        indexed = self._cache[cache_key]
        qvec = (await self._embed_fn([text], client=client))[0]
        scored = sorted(((fid, _cosine(qvec, vec)) for fid, vec in indexed),
                        key=lambda x: x[1], reverse=True)[:limit]
        by_id = {e[0]: e for e in entries}
        return [{"id": fid, "name": by_id[fid][1], "description": by_id[fid][2][:160],
                 "score": round(score, 4)} for fid, score in scored if score > 0]
