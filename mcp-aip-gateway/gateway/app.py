"""万桥双面同进程入口（N4）：AIP Partner 面 + A2A 服务面共用一个 app、一个 registry。

- AIP 面：POST /rpc（gateway.partner 原生，网关 v1.1 起在役）
- A2A 面：POST / + /.well-known/agent-card.json|agent.json（§2.1/§2.2，本文件挂接）
- 能力面同源：两个协议面查同一个 McpRegistry——同一能力，两种协议，一个结果
- A2A 面审计：amp_access_a2a-<port>.jsonl（AccessEmitter，N4）；AIP 面审计归 partner.py

运行（端口 ≥8060 红线）：
    WANBRIDGE_A2A_URL=http://localhost:8060/ GATEWAY_CONFIG=... \
        uv run uvicorn --factory gateway.app:create_app --host 0.0.0.0 --port 8060

定位边界（DESIGN.md）：确定性协议翻译层——双面挂接不引入任何编排逻辑。
"""

from __future__ import annotations

import os
from pathlib import Path

from acps_sdk.amp import AccessEmitter
from fastapi import FastAPI

import gateway.partner as partner
from gateway.a2a_server import _wire_routes

DEFAULT_A2A_PORT = 8060


def create_app(*, a2a_port: int | None = None, access_emitter: AccessEmitter | None = None) -> FastAPI:
    """把 A2A 面直接 _wire_routes 到 partner.app 上并返回同一 app（路由/lifespan/registry 全复用）。

    a2a_port：仅用于审计文件名与 Card self_url 兜底；实际监听端口由 uvicorn 决定。
    WANBRIDGE_A2A_URL：外部可达地址（容器/反向代理场景），优先于本地兜底。
    access_emitter：测试注入点（tmp_path）；缺省按 AMP_LOG_DIR 构造。
    include_rpc_route=False：POST /rpc 已被 AIP 面占用（FastAPI 先注册者优先），
    A2A 走 POST /（AgentCard.url 指向 /），两协议面互不遮蔽。
    幂等：重复调用不重复挂线（测试/热重载场景安全）。
    """
    if getattr(partner.app.state, "_wanbridge_a2a_wired", False):
        return partner.app
    port = a2a_port or int(os.environ.get("A2A_PORT", str(DEFAULT_A2A_PORT)))
    self_url = os.environ.get("WANBRIDGE_A2A_URL") or f"http://localhost:{port}/"
    if access_emitter is None:
        amp_log_dir = Path(os.environ.get("AMP_LOG_DIR", "./logs"))
        amp_log_dir.mkdir(parents=True, exist_ok=True)
        access_emitter = AccessEmitter(amp_log_dir / f"amp_access_a2a-{port}.jsonl", aic=partner.GATEWAY_AIC)
    store: dict[str, dict] = {}  # A2A 内存任务表（§2.3），app 生命周期内共享
    deps = lambda: {
        "resolve": partner.registry.resolve,
        "call": partner.registry.call,
        "available_ids": lambda: list(partner.registry.all_tools()),
        "store": store,
        "catalog": lambda: partner.registry.all_tools(),  # T5：/tools/search 需要
    }
    _wire_routes(
        partner.app,
        deps,
        lambda: build_agent_card_for(partner.registry, self_url=self_url),
        access_emitter=access_emitter,
        gateway_aic=partner.GATEWAY_AIC,
        include_rpc_route=False,
    )
    partner.app.state._wanbridge_a2a_wired = True
    return partner.app


def build_agent_card_for(registry, *, self_url: str) -> dict:
    from gateway.a2a_server import build_agent_card

    return build_agent_card(registry.build_acs(), self_url=self_url)


if __name__ == "__main__":  # pragma: no cover — 本地演示入口
    import uvicorn

    uvicorn.run("gateway.app:create_app", factory=True,
                host="0.0.0.0", port=int(os.environ.get("A2A_PORT", str(DEFAULT_A2A_PORT))))
