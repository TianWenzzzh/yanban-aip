"""轮 299 契约（第二刀）：**登记入口真正指向的是 Partner 应用**，所以发卡必须落在 partner_base 上。

现读依据（`输出/_logs/r299_d7..d8` 与主机勘察）：caddy `47.114.38.125.sslip.io` → `127.0.0.1:18031`
= `yanban-literature-1:8031`（`docker ps` 实证），而它的 `/.well-known/agent-card.json` 现在是 **404**。
⇒ 第一轮把卡加在 leader（8041）上并不解决平台取卡——那一处已当场改口。

卡的内容不另造：以**平台已登记的那张**为源（`acs/acs-literature.json` 由
`输出/证据/平台接入/E6_平台发现/discover_v13_hit_20260920.json` 的 acsMap 落盘，只把 endPoints
换成真实入口），服务端再按运行时的 aic 强制校验——🔴 aic 对不上就 503，绝不发一张身份错位的卡。

判据形状：双路径逐字同卡、官方 schema 的 required 清单硬判（venv 没装 jsonschema，
写成「有才判」＝装饰）、endPoint 不留 `{AIC}` 占位符、身份不匹配必须 503。
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import partner_base

WELL_KNOWN = ("/.well-known/agent-card.json", "/.well-known/agent.json")
ACS_DIR = Path(__file__).resolve().parents[1] / "acs"
SCHEMA = (Path(__file__).resolve().parents[2] / "ACPs-community"
          / "registry-server" / "app" / "agent" / "acsSchema.json")
# 轮 308c：身份常量**现读**（regex 读源码），不抄字面量——HTTP 身份（…1022）与 MQ 身份（…05BB）
# 从这一轮起是两颗，抄错了这条门会拿旧值去验新卡。不 import literature_partner（模块级副作用大）。
_SRC = (Path(__file__).resolve().parents[1] / "literature_partner.py").read_text(encoding="utf-8")
LIT_AIC = re.findall(r'^PARTNER_AIC = "([^"]+)"', _SRC, re.M)[0]
LIT_AIC_MQ = re.findall(r'^PARTNER_AIC_MQ = "([^"]+)"', _SRC, re.M)[0]


def _app(name: str, aic: str, inbox_aic: str | None = None) -> TestClient:
    app = FastAPI()
    partner_base.add_partner_card_routes(app, name=name, aic=aic, inbox_aic=inbox_aic)
    return TestClient(app)


def _lit_app() -> TestClient:
    """按 production 的装法：aic=HTTP 身份、inbox_aic=MQ 身份。"""
    return _app("yanban-literature", LIT_AIC, inbox_aic=LIT_AIC_MQ)


def test_both_paths_serve_the_identical_card():
    c = _lit_app()
    a, b = c.get(WELL_KNOWN[0]), c.get(WELL_KNOWN[1])
    assert a.status_code == 200 and b.status_code == 200, f"{a.status_code}/{b.status_code}"
    assert a.content == b.content, "两条 well-known 不逐字同卡（发现侧走回退，只挂一条就是半个 bug）"
    assert json.loads(a.content)["aic"] == LIT_AIC


def test_amqp_inbox_follows_the_mq_identity_not_the_http_identity():
    """轮 308c 身份拆分的第一条闸门：HTTP 身份（…1022）迁走后，卡上的 AMQP 收件箱必须仍是
    MQ 身份（…05BB）——平台侧两张已通过记录的收件箱都冻结成 inbox_05BB，跟着 HTTP 身份换
    就等于把群组那条路指到一个没人收的箱子里。"""
    assert LIT_AIC != LIT_AIC_MQ, "两条身份常量不许相等（相等说明迁移没做或抄错了）"
    card = _lit_app().get(WELL_KNOWN[0]).json()
    amqp = [e["url"] for e in card["endPoints"] if e["url"].startswith("amqps://")]
    assert amqp and all(("inbox_" + LIT_AIC_MQ) in u for u in amqp), f"AMQP 收件箱没跟 MQ 身份：{amqp}"
    assert all(("inbox_" + LIT_AIC) not in u for u in amqp), f"AMQP 收件箱错误地跟了 HTTP 身份：{amqp}"


def test_aic_mismatch_refuses_to_serve_rather_than_misidentify():
    """身份对不上宁可 503：发一张别人 aic 的卡＝对外谎报身份，比 404 更坏。"""
    c = _app("yanban-literature", "1.2.156.3088.1.9999.WRONG000.BAD001.0000")
    r = c.get(WELL_KNOWN[0])
    assert r.status_code == 503, f"错位身份本该 503，实得 {r.status_code}"


def test_served_card_carries_the_official_required_fields():
    if not SCHEMA.exists():
        pytest.fail(f"官方 schema 不在场：{SCHEMA} ⇒ 这条门没有分母，不许默默放过")
    required = list(json.loads(SCHEMA.read_text(encoding="utf-8")).get("required") or [])
    assert required, "官方 schema 没给 required 清单 ⇒ 分母为空，这一格不算验过"
    card = _lit_app().get(WELL_KNOWN[0]).json()
    missing = [k for k in required if k not in card]
    assert not missing, f"卡缺官方必需字段：{missing}（required 共 {len(required)} 项）"
    assert card["skills"] and all({"id", "name", "description"} <= set(s) for s in card["skills"])


def test_endpoints_are_real_entries_with_no_placeholder():
    urls = [e["url"] for e in _lit_app().get(WELL_KNOWN[0]).json()["endPoints"]]
    assert urls and all("{" not in u for u in urls), f"endPoint 有未插值占位符：{urls}"
    # 🔴 这条判据轮 307 改过形状：原来写死 `u.startswith("https://")`，是 443 还能用的日子立的。
    # 现在那条 https 地址是被 ICP 按域名拦的死路（未决 67 有 403 拦截页原文），把 https 钉成要求
    # 等于要求卡上广告一条打不通的地址——轮 307 改前的现读读数就是 CARD_RC=3（"卡上广告着已知死路"）。
    # 卡的职责是"照着它调得通"，所以要求换成：**http(s) 皆可，但必须是可达那颗**（可达性由
    # `输出/scripts/probe_r307_card_reach.py` 从公网逐条实测，不在这里靠字面量猜）。
    # 等未决 65 那条 AIC 形态证书挂上 443，这一支该回到 https + mtls，届时这条判据同步收紧。
    assert any(u.startswith(("http://", "https://")) and u.endswith("/rpc") for u in urls), \
        f"缺公网 JSONRPC 入口：{urls}"
    assert all(LIT_AIC_MQ in u for u in urls if u.startswith("amqps://")), \
        f"AMQP 收件箱名与 MQ 身份不一致：{urls}"


def test_every_partner_ships_its_own_card_file():
    """五颗 Partner 都必须有自己的 acs 文件，且收件箱名已插值（缺文件＝上线后 503，占位符＝假卡）。

    🔴 判据修过一次：原来写 `"{" not in json.dumps(endPoints)`——那必然为假（dict 序列化就带大括号），
    是一条**永远红的坏判据**；要查的是 URL 字符串里有没有 `{AIC}`。
    🔴 轮 308c：literature 的收件箱按 MQ 身份（…05BB）判，其余四颗仍按自身 aic 判——文件里那句
    "收件箱与自身 aic 一致" 对 literature 已不成立（HTTP 身份迁移，见上一条用例）。
    """
    for name in ("yanban-literature", "yanban-experiment", "yanban-analysis", "yanban-writing", "yanban-review"):
        f = ACS_DIR / ("acs-%s.json" % name.replace("yanban-", ""))
        assert f.exists(), f"缺卡材料：{f.name}"
        card = json.loads(f.read_text(encoding="utf-8"))
        assert card.get("aic"), f"{f.name} 没有 aic 字段"
        urls = [e.get("url", "") for e in (card.get("endPoints") or [])]
        assert urls, f"{f.name} 没有 endPoints"
        assert not any("{" in u for u in urls), f"{f.name} 的入口 URL 留了未插值占位符：{urls}"
        expect_inbox = LIT_AIC_MQ if name == "yanban-literature" else card["aic"]
        assert all(("inbox_" + expect_inbox) in u for u in urls if u.startswith("amqps://")), \
            f"{f.name} 的收件箱名与该用哪颗身份不一致：{urls}"
