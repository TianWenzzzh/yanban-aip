"""PaperProvider 抽象基类（2.0 任务书 §9）：缺 Key/失败自动降级。"""
from __future__ import annotations

import abc
import asyncio
import logging
import re
import time

import httpx

log = logging.getLogger("yanban.companion.providers")

UA = {"User-Agent": "yanban-companion/2.0 (research assistant; contact: team@yanban.local)"}

RETRY_STATUS = (500, 502, 503, 504)   # HTTP 层只替服务端抖动再敲包
THROTTLE_STATUS = (429,)              # 429 的处置见 `trip_for`：冷却时长按服务端的 Retry-After 给
PERMANENT_STATUS = (403, 406)
# 403/406 是「这台出口被这个源（准确地说是它前面的 CDN）拒了」，不是「现在太挤」：
# 实测 arXiv 六种请求形状（含**不带任何参数**的裸 URL）全 406、响应体为空、应答方是 varnish 边缘节点，
# 换 UA/Accept/方案都一样 ⇒ 换请求形状修不动，只能走永久档。
# 和 429 共用 120s 平铺冷却 = 每轮检索都白等一次必然失败的往返，所以分档。
BREAKER_STATUS = RETRY_STATUS + THROTTLE_STATUS + PERMANENT_STATUS
COOLDOWN = 120.0
MIN_RETRY_AFTER = 1.0         # 服务端要 0 秒也别立刻回头再打
MAX_COOLDOWN = 900.0        # 限流类连击后的冷却上限
LONG_COOLDOWN = 3600.0      # 永久拒绝类：一小时内别再碰它
RESULT_TTL = 6 * 3600.0     # 同一查询的文献结果一天内基本不变，缓存掉重复外呼
_DOWN_UNTIL: dict[str, float] = {}
_STRIKES: dict[str, int] = {}
_CACHE: dict[tuple[str, str, int], tuple[float, list]] = {}
_STATS = {"calls": 0, "cache_hits": 0}
# 本轮有哪些源是「失败」而不是「查无结果」。不记这个，provider 的 429/406 就被 safe_search
# 咽成空列表，产品与探针都分不清「没有相关论文」和「源全挂了」。
_OUTAGE: list[str] = []


def take_outage() -> list[str]:
    """取走并清空本轮失败源名单（引擎在每次检索前后各调一次）。"""
    out = list(_OUTAGE)
    _OUTAGE.clear()
    return out


class ProviderUnavailable(RuntimeError):
    """源明确不可用（限流/拒绝），值得熔断一段时间，别每次检索都白等它。

    `retry_after` 是服务端在 `Retry-After` 头里**自己要求**的秒数。带着它，是因为不带的话
    `trip_for` 只能按固定阶梯冷却，而阶梯对 OpenAlex 那类"15 秒后就放行"的临时限流是过度惩罚
    （实测见 `输出/scripts/probe_retry_after_window.py`）。没有这个头时它是 None，
    那种源（Semantic Scholar：四次 429、盲等 22s 仍 429）继续走阶梯。
    """

    def __init__(self, name: str, detail: str, permanent: bool = False,
                 retry_after: float | None = None):
        super().__init__(detail)
        self.name = name
        self.permanent = permanent
        self.retry_after = retry_after


def retry_seconds(headers) -> float | None:
    """只认整数秒的 `Retry-After`；HTTP-日期形态本轮不猜（真遇到再说，宁可不 honored）。"""
    raw = headers.get("retry-after") if headers is not None else None
    if not raw:
        return None
    m = re.search(r"\d+", str(raw))
    return float(m.group()) if m else None


def breaker_open(name: str) -> bool:
    return time.monotonic() < _DOWN_UNTIL.get(name, 0.0)


def trip(name: str, seconds: float = COOLDOWN) -> None:
    _DOWN_UNTIL[name] = time.monotonic() + seconds


def trip_for(name: str, exc: ProviderUnavailable) -> float:
    """冷却多久：先听服务端自己说，其次按失败性质分档。

    有 `Retry-After` 的临时限流 ⇒ 就冷却它要求的秒数（夹在 MIN/MAX 之间）。
    阶梯（120→240→480→900）留给**没给秒数**的限流：那种源的等待窗口服务端不肯承诺（S2 实测），
    翻倍是对日额度的保护。永久拒绝类一步到位。
    """
    if exc.permanent:
        trip(name, LONG_COOLDOWN)
        return LONG_COOLDOWN
    if exc.retry_after is not None:
        seconds = min(max(exc.retry_after, MIN_RETRY_AFTER), MAX_COOLDOWN)
        trip(name, seconds)
        return seconds
    _STRIKES[name] = _STRIKES.get(name, 0) + 1
    seconds = min(COOLDOWN * (2 ** (_STRIKES[name] - 1)), MAX_COOLDOWN)
    trip(name, seconds)
    return seconds


def reset_breakers() -> None:
    """测试/探针用：把熔断、连击与结果缓存一起清干净，别把上一轮的状态带进下一轮测量。"""
    _DOWN_UNTIL.clear()
    _STRIKES.clear()
    _CACHE.clear()
    _OUTAGE.clear()      # 少了这行，上一条测试留下的失败源会污染下一条（全量跑才暴露）
    _STATS.update(calls=0, cache_hits=0)


def provider_stats() -> dict:
    """外呼次数与缓存命中：轮 93 之后「有没有少撞限流」必须是个可出示的数，不是感觉。"""
    return dict(_STATS)


def _norm_key(name: str, query: str, limit: int) -> tuple[str, str, int]:
    return (name, re.sub(r"\s+", " ", (query or "").strip().lower()), limit)


class PaperProvider(abc.ABC):
    name: str = "base"

    @abc.abstractmethod
    async def search(self, query: str, limit: int = 10) -> list[dict]:
        """返回原始条目列表：title/authors/year/venue/doi/citations/abstract/url/open_access。"""

    async def safe_search(self, query: str, limit: int = 10) -> list[dict]:
        """降级封装：任何异常只记日志并返回空列表（用户无感知）。

        实测背景：arXiv 对本项目出口 IP 恒 406、Semantic Scholar 恒 429，
        不熔断的话每次检索都要为这两个必然失败的源等完超时与退避（含检索轮首字 10.2s）。
        缓存的是**外呼**：一个查询多分支/多轮重复出现时只真打一次，撞限流的次数直接下降。
        """
        if breaker_open(self.name):
            log.info("provider %s 熔断中，本轮跳过", self.name)
            return []
        key = _norm_key(self.name, query, limit)
        hit = _CACHE.get(key)
        if hit and hit[0] > time.monotonic():
            _STATS["cache_hits"] += 1
            return list(hit[1])
        try:
            found = await self.search(query, limit)
        except ProviderUnavailable as exc:
            seconds = trip_for(self.name, exc)
            _OUTAGE.append(self.name)
            log.warning("provider %s 不可用，熔断 %.0fs：%s", self.name, seconds, exc)
            return []
        except Exception as exc:  # noqa: BLE001
            _OUTAGE.append(self.name)
            log.warning("provider %s failed: %s", self.name, exc)
            return []
        _STRIKES[self.name] = 0
        _STATS["calls"] += 1
        if found:          # 空结果不缓存：多半是限流或抖动，不是「真没有」
            _CACHE[key] = (time.monotonic() + RESULT_TTL, list(found))
        return found


def _client(timeout: float = 30.0) -> httpx.AsyncClient:
    return httpx.AsyncClient(timeout=timeout, headers=UA)


async def get_json(url: str, *, params: dict | None = None, timeout: float = 30.0,
                   retries: int = 2, name: str = "") -> dict:
    """带 UA 的 GET + JSON 解析；只有 5xx 与网络抖动做指数退避重试。

    429 **不在这里重试**，但要把服务端的 `Retry-After` 原样交给熔断（`trip_for` 会照它冷却）。
    两件事分开看，别再混成一句话：
      · 「同一轮里退避再敲三次」是错的——`g166`/`g168` 实测每撞一次限流实发 3 包，且旧退避是
        秒级间隔，等于无视服务端的要求继续撞（轮 157 就是这么治掉的，包数 3→1）。
      · 「429 之后该锁多久」则要听服务端的：轮 162 实测 OpenAlex 给的是 `retry-after: 15~37`（**秒**）
        并且等够就 200；而 Semantic Scholar 不给这个头、盲等 22s 四次仍 429。
        ⇒ 有头就照它的秒数冷却，没头才回到 120s 起步翻倍的阶梯。
        （旧注释在这里写过一句"Retry-After 是日额度级的"，那句话被本轮实测否掉了。）
    """
    last = ""
    r = None
    for attempt in range(retries + 1):
        try:
            async with _client(timeout) as client:
                r = await client.get(url, params=params)
            if r.status_code == 200:
                return r.json()
            last = f"HTTP {r.status_code}"
            if r.status_code in PERMANENT_STATUS:
                break               # 拒了就别退避重试，重试只会 more 429
            if r.status_code not in RETRY_STATUS:
                break
            if r.status_code in BREAKER_STATUS and attempt == retries:
                break
        except (httpx.TimeoutException, httpx.TransportError) as exc:
            last = str(exc)
            if attempt == retries:
                raise
        await asyncio.sleep(1.5 * (2 ** attempt))
    if r is not None and r.status_code in BREAKER_STATUS:
        raise ProviderUnavailable(name or url, f"{url} 不可用：HTTP {r.status_code}",
                                  permanent=r.status_code in PERMANENT_STATUS,
                                  retry_after=retry_seconds(r.headers))
    raise RuntimeError(f"{url} 不可用：{last}")
