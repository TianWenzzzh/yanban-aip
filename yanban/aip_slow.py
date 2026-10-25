"""慢任务 AIP 客户端工厂（T1）：SDK AipRpcClient 内部 httpx timeout=30s 硬编码
（aip_rpc_client.py:145，构造器不可配），审稿/长生成任务常超 30s → ReadTimeout。

SDK 禁改（R17）。httpx 0.28 的 AsyncHTTPTransport 无 timeout 参数（超时归 Client 层），
故用 transport 子类改写 request.extensions["timeout"]（per-request 覆盖优先于 client，
实测验证：client 写入 {'connect': 30...} 后 transport 层改写生效）。
仅实验/门控等慢链路使用；业务 Partner 链路（orchestrator）不动。
"""

from __future__ import annotations

import httpx
from acps_sdk.aip import AipRpcClient

SLOW_TIMEOUT = 300.0


class SlowTransport(httpx.AsyncHTTPTransport):
    """把所有请求的读/写超时改写为慢任务值（连接与连接池仍保持短超时）。"""

    def __init__(self, timeout: float = SLOW_TIMEOUT) -> None:
        super().__init__()
        self._slow = timeout

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        request.extensions["timeout"] = {"connect": 10.0, "read": self._slow,
                                         "write": self._slow, "pool": 10.0}
        return await super().handle_async_request(request)


def make_slow_client(endpoint: str, leader_id: str, *, timeout: float = SLOW_TIMEOUT) -> AipRpcClient:
    return AipRpcClient(partner_url=endpoint, leader_id=leader_id,
                        identity_binding_enabled=False, transport=SlowTransport(timeout))
