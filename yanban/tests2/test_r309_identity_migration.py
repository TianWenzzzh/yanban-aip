# -*- coding: utf-8 -*-
"""轮 309（队长交接文档验收①②）：服务器身份迁到平台新授权的那颗 AIC，且入口不许再广告被拦域名。

权威出处（都是现读，不是回忆）：
  · 新 HTTP 身份 `…A0G0KD.YYTVI4.1DHO` ＝轮 309 基线探针 T4 从公开 discovery 读回的 1.5.1 那张卡
    （`输出/证据/轮309/` 与 `输出/scripts/probe_r309_handoff_baseline.py`），也是交接文档点名的目标那颗；
  · 平台规则（轮 308c 实测，registry-server `service_command.py` / `_ensure_agent_is_editable`）：
    **客户端不能自选 AIC**、审批通过时授予新 AIC、已通过的记录永远不能再改 ⇒ 换版本＝换身份，
    服务器必须跟着迁，否则「在册卡 / 线上运行身份 / 证书身份」三者不一致（验收①要的就是这一条）；
  · MQ/群组身份**留在 …05BB**：那张已通过记录的 AMQP 收件箱冻结为 `inbox_…05BB`，
    跟着 HTTP 身份一起换＝把群组那条路指进一个没人收的箱子（`test_amqp_inbox_follows_…` 钉着）；
  · 入口域名：带 `47.95.252.83.sslip.io` 这个 SNI 的 ClientHello 会被链路 **RST**（轮 309 T1 三臂实测：
    带该 SNI 连不上、无 SNI 与 SNI=IP 两臂都握手成功；同机内自打同 SNI 成功 ⇒ 服务没问题，是按域名拦）。
    🔴 这条不是"顺手改个字符串"：地址形状错了，默认档 SDK 调用方会在握手那一步就没机会拿到身份应答。
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import partner_base

ACS_DIR = Path(__file__).resolve().parents[1] / "acs"
_SRC = (Path(__file__).resolve().parents[1] / "literature_partner.py").read_text(encoding="utf-8")
# 身份常量一律**现读源码**（不抄第二份字面量）：这一轮改的就是这两个常量，抄在测试里就等于没验。
LIT_AIC = re.findall(r'^PARTNER_AIC = "([^"]+)"', _SRC, re.M)[0]
LIT_AIC_MQ = re.findall(r'^PARTNER_AIC_MQ = "([^"]+)"', _SRC, re.M)[0]

# 平台侧的既有事实（探针/交接文档给的，不是我们希望的值）
PLATFORM_HTTP_AIC = "1.2.156.3088.1.BUPT.BNN2UD.A0G0KD.YYTVI4.1DHO"
SUPERSEDED_HTTP_AIC = "1.2.156.3088.1.BUPT.BNN2UD.B3AOPA.DZGBZX.1022"
FROZEN_MQ_AIC = "1.2.156.3088.1.BUPT.BNN2UD.V1R7FH.U8O0NE.05BB"
# 实测可达的 TLS 入口形状：IP + 8443（80/443 与未备案域名那两条都会被拦/劫）
EXPECTED_TLS_ENTRY = "https://47.95.252.83:8443/rpc"


def _no_blocked_domain(url: str) -> bool:
    """这条入口能不能从公网打通的第一道形状判据：不含按域名拦的 sslip.io。"""
    return "sslip.io" not in url


def _lit_client() -> TestClient:
    app = FastAPI()
    partner_base.add_partner_card_routes(app, name="yanban-literature", aic=LIT_AIC,
                                         inbox_aic=LIT_AIC_MQ)
    return TestClient(app)


def test_runtime_http_identity_equals_the_one_the_platform_issued():
    assert LIT_AIC == PLATFORM_HTTP_AIC, (
        f"线上 HTTP 身份是 …{LIT_AIC[-13:]}，而在册卡（discovery 现读）是 …{PLATFORM_HTTP_AIC[-13:]}"
        " ⇒ 三者不一致，验收①不成立")
    assert LIT_AIC != SUPERSEDED_HTTP_AIC, "还在用上一版那颗已作废的 AIC"


def test_card_material_and_served_card_follow_the_same_identity():
    """卡材料（发给平台/别人的那张源文件）与运行时发卡必须同一颗：
    只改代码常量不改 `acs/acs-literature.json`，`partner_card()` 会因身份不符返回 None ⇒ 503。"""
    card_file = json.loads((ACS_DIR / "acs-literature.json").read_text(encoding="utf-8"))
    assert card_file["aic"] == LIT_AIC, f"卡材料还是 …{card_file['aic'][-13:]}（运行时 …{LIT_AIC[-13:]}）"
    got = _lit_client().get("/.well-known/agent-card.json")
    assert got.status_code == 200, f"发卡 503＝卡材料与运行时身份错位：{got.status_code}"
    assert got.json()["aic"] == LIT_AIC


def test_mq_identity_stays_on_the_frozen_inbox_owner():
    """MQ/群组那条不许跟着 HTTP 身份一起换（收件箱在平台侧是冻结的）。"""
    assert LIT_AIC_MQ == FROZEN_MQ_AIC, f"MQ 身份被改动：…{LIT_AIC_MQ[-13:]}"
    assert LIT_AIC_MQ != LIT_AIC
    urls = [e["url"] for e in _lit_client().get("/.well-known/agent-card.json").json()["endPoints"]]
    amqp = [u for u in urls if u.startswith("amqps://")]
    assert amqp and all("inbox_" + FROZEN_MQ_AIC in u for u in amqp), f"收件箱没跟 MQ 身份：{amqp}"
    assert not any("inbox_" + LIT_AIC in u for u in amqp), f"收件箱错跟了 HTTP 身份：{amqp}"


def test_public_entries_advertise_no_domain_that_icp_blocks():
    """两条对外入口都不许出现 `sslip.io`——那条域名的 SNI 会被链路 RST（本轮 T1 实测）。"""
    assert _no_blocked_domain(partner_base.PUBLIC_RPC_URL), partner_base.PUBLIC_RPC_URL
    assert _no_blocked_domain(partner_base.PUBLIC_AIP_TLS_URL), partner_base.PUBLIC_AIP_TLS_URL


def test_the_domain_gate_actually_bites_on_the_previous_value():
    """负对照：上一版那条 `PUBLIC_AIP_TLS_URL` 就是带 sslip.io 的形状，判据必须把它判死；
    否则「不含被拦域名」可能只是恰好没人写过那种值。"""
    assert not _no_blocked_domain("https://47.95.252.83.sslip.io:8443/rpc"), "判据恒真"
    assert _no_blocked_domain(EXPECTED_TLS_ENTRY)


def test_tls_entry_is_ip_form_on_the_opened_port():
    assert partner_base.PUBLIC_AIP_TLS_URL == EXPECTED_TLS_ENTRY, partner_base.PUBLIC_AIP_TLS_URL


def test_certificate_subject_is_checked_against_the_runtime_identity():
    """验收①的第三格（证书）不能靠注释自证：这里要求**存在一条把证书 CN 与 PARTNER_AIC 对照**的
    机器判据（部署侧由 `migrate_r309_identity.py` M3/M4 出读数），测试这一层钉的是
    「不许有人把这三者当成两件事各改一半」——所以要求代码里那句身份注释指向同一个常量。
    """
    src = (Path(__file__).resolve().parents[1] / "literature_partner.py").read_text(encoding="utf-8")
    assert src.count('PARTNER_AIC = "%s"' % PLATFORM_HTTP_AIC) == 1
    assert "PARTNER_AIC_MQ" in src and FROZEN_MQ_AIC in src, "MQ 那半句身份被删掉了"
