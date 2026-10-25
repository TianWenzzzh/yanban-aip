"""身份绑定模式（v5 T0，R21）：demo | trusted 单一事实源。

- demo（默认）：identity_binding_enabled=False——比赛演示兜底，外部 Leader 未持证书也能调用，
  行为与 v4 及之前完全一致（R18 精神）。
- trusted（YANBAN_IDENTITY_MODE=trusted）：服务端开启身份绑定——peer AIC 来自 mTLS 客户端证书
  （acps_sdk 中间件落 request.state.peer_aic），senderId 不匹配即 -32009 拒绝。

注意：trusted 模式的完整链路还需要（a）Leader 侧真实证书（梧桐 cert issue 签发），
（b）Leader 的 senderId 使用其证书 AIC。平台令牌过期期间（见 PROGRESS 待决策 1），
先用本模块 + 桩中间件离线测试钉住接线语义；平台链恢复后无需改代码，只切环境变量。
"""

from __future__ import annotations

import os

MODE_ENV = "YANBAN_IDENTITY_MODE"
DEMO = "demo"
TRUSTED = "trusted"


def identity_mode() -> str:
    mode = os.environ.get(MODE_ENV, DEMO).strip().lower()
    return mode if mode in (DEMO, TRUSTED) else DEMO


def identity_binding_enabled() -> bool:
    """服务端身份绑定开关（create_partner_app / literature_partner 接线用）。"""
    return identity_mode() == TRUSTED
