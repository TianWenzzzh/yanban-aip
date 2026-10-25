"""轮 299 契约：Leader 必须能在**登记入口那条 URL** 上自助发一张过官方 schema 的卡。

为什么要这几条（现读，不是回忆）：公网登记的 `https://47.114.38.125.sslip.io/rpc` 由 caddy 转到
leader:18031，而发卡一直是网关 A2A 面的事（`mcp-aip-gateway/gateway/a2a_server.py:424`）——
2026-10-04 实测两条 well-known 在 8041 与经 443 **都 404**，平台没法自助取/刷新卡，
登记的还是 09-20 那份 `version=1.3.0` 死快照。本组用例钉的是「入口能自己发卡，且发的是一张
真卡」：路径成对、两路逐字同卡、过 registry 官方 schema、endPoint 里不许留占位符、
AIC 必须是我们自指派段（🔴 不冒充平台分配的 BUPT 那张 literature 卡）。

判据形状（🔴 不在测试里抄第二份尺）：卡由产品函数 `leader_card.build()` 现造；
schema 用 registry-server 那份官方 jsonschema 文件；两条路径名逐字钉 `_WELL_KNOWN`（改名即红，
防「只挂一条」与「路径漂走」两类自欺）。
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import leader
import leader_card

WELL_KNOWN = ("/.well-known/agent-card.json", "/.well-known/agent.json")
SCHEMA = (Path(__file__).resolve().parents[2] / "ACPs-community"
          / "registry-server" / "app" / "agent" / "acsSchema.json")


@pytest.fixture()
def client():
    with TestClient(leader.app) as c:
        yield c


def _get(client, path):
    r = client.get(path)
    assert r.status_code == 200, f"{path} → {r.status_code}（卡路径没挂上或返回非 200）"
    return r


def test_both_well_known_paths_serve_the_identical_card(client):
    """双路径同卡：只挂一条就是半个 bug（bridge_a2a 的发现走「新版→旧版回退」）。"""
    a, b = _get(client, WELL_KNOWN[0]), _get(client, WELL_KNOWN[1])
    assert a.content == b.content, "两条 well-known 返回的卡不逐字相同"
    assert json.loads(a.content).get("aic") == leader_card.LEADER_AIC


def test_well_known_route_names_are_pinned_and_both_registered(client):
    """等价对照：路径名逐字钉住（改名即红），且 leader._WELL_KNOWN 与真实注册的路由集合一致。"""
    assert leader._WELL_KNOWN == WELL_KNOWN, "leader._WELL_KNOWN 漂了（发现侧按这两条找）"
    paths = {getattr(r, "path", None) for r in leader.app.routes}
    assert set(WELL_KNOWN) <= paths, f"实际注册的路由缺卡路径：{sorted(paths)[:8]}…"


def test_served_card_passes_the_official_registry_schema(client):
    """尺子＝registry-server 那份官方 schema **文件本身**：必需字段清单从文件里读，不在测试里抄第二份。

    🔴 不写成「有 jsonschema 才判」——venv 里没装 jsonschema，那样这条门会永远静默不跑（＝装饰）。
    这里先按官方 required 清单硬判（一定会开火），装了 jsonschema 时再补一次全量校验。
    """
    if not SCHEMA.exists():
        pytest.fail(f"官方 schema 不在场：{SCHEMA} ⇒ 这张门没有分母，不许默默放过")

    schema = json.loads(SCHEMA.read_text(encoding="utf-8"))
    required = list(schema.get("required") or [])
    assert required, "官方 schema 里没有 required 清单 ⇒ 分母为空，这一格不算验过"
    card = _get(client, WELL_KNOWN[0]).json()
    missing = [k for k in required if k not in card]
    assert not missing, f"卡缺官方必需字段：{missing}（required 共 {len(required)} 项）"
    for sk in card["skills"]:
        assert {"id", "name", "description"} <= set(sk), f"技能字段不全：{sorted(sk)[:6]}"
    try:
        from jsonschema import Draft202012Validator
    except ModuleNotFoundError:
        return  # 环境没装库：上面的 required 硬判已经开过火，不假装做了全量校验
    Draft202012Validator.check_schema(schema)
    errs = list(Draft202012Validator(schema).iter_errors(card))
    assert not errs, f"卡不过官方 schema：{errs[0].json_path} {errs[0].message[:120]}"



def test_card_endpoints_are_real_and_hold_no_placeholder(client):
    """登记的入口必须真的指得回来；`{AIC}` 这种没插值的占位符一出现就是假卡。"""
    card = _get(client, WELL_KNOWN[0]).json()
    urls = [e.get("url", "") for e in card["endPoints"]]
    assert urls, "endPoints 为空（平台拿不到任何入口）"
    joined = " ".join(urls)
    assert "{AIC}" not in joined and "{" not in joined, f"endPoint 里有未插值占位符：{urls}"
    assert any(u.startswith("https://") and u.endswith("/rpc") for u in urls), f"缺公网 JSONRPC 入口：{urls}"
    amqp = [u for u in urls if u.startswith("amqps://")]
    assert all(card["aic"] in u for u in amqp), f"AMQP 收件箱名与卡自身 aic 不一致：{amqp}"


def test_card_declares_the_self_assigned_aic_not_a_platform_one(client):
    """🔴 不冒充：Leader 卡用自指派段，绝不复用平台分配的 BUPT literature 卡内容。"""
    card = _get(client, WELL_KNOWN[0]).json()
    assert card["aic"] == leader_card.LEADER_AIC
    assert "BUPT" not in card["aic"], "自指派段里混进 BUPT（等于冒充平台分配的 AIC）"
    assert "文献检索智能体" not in card["name"], "Leader 卡冒充了 literature 那张卡"
    assert card["version"] == leader_card.CARD_VERSION
    assert card["skills"] and all(s.get("id") and s.get("name") for s in card["skills"])


def test_health_and_card_agree_on_the_leader_identity(client):
    """/health 报的 leader_aic 与卡上必须同源；不一致就是两份真源（以后必然漂）。"""
    h = _get(client, "/health").json()
    if h.get("leader_aic") is None:
        pytest.skip("lifespan 未给出 leader_aic（partners.toml 缺字段），这一格算未验")
    assert h["leader_aic"] == _get(client, WELL_KNOWN[0]).json()["aic"]
