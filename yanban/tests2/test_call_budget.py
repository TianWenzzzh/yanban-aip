"""调用预算（轮 307）：**别人调用我们，不该因为源被限流而拿不到回答**。

判线来自实测，不是设计偏好：
  · `acps_sdk` 的客户端把请求超时**写死 30 秒**（`aip_rpc_client.py` 里 `timeout=30.0`）；
  · 我们最坏的一趟检索实测 **88.9 秒**（`docs/UNDERMIND_BORROW.md` 未决 64，审计 durationMs=88915），
    成因是 Crossref 内部四档放松重试（每档 httpx 30s）＋ arXiv 三发退避（每发 45s）；
  · 那种日子服务端最后**答完了，调用方也已经放弃** ⇒ 一次"被调用"变成对方视角的一次失败，
    而"被调用次数"这一项要的恰恰是别人愿意再来一次。

所以：单源 15 秒（超了就明写「未在预算内返回」，另一支照发），整次能力调用 20 秒（外墙）。
这两个数**必须小于** SDK 那颗 30 秒——这条不靠人记得，由 B4 现读 SDK 源码钉住。
"""
from __future__ import annotations

import asyncio
import pathlib
import re
import sys
import time
import uuid

import pytest
from fastapi.testclient import TestClient

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import sources  # noqa: E402

SDK_CLIENT = ROOT.parent / "ACPs-community" / "acps-sdk" / "acps_sdk" / "aip" / "aip_rpc_client.py"


def _sdk_caller_timeout() -> float:
    """调用方的超时是**现读 SDK 源码**的，不是抄一个 30 在测试里（那颗数哪天变了这里会跟着变）。"""
    m = re.search(r"timeout\s*=\s*([0-9.]+)", SDK_CLIENT.read_text(encoding="utf-8", errors="replace"))
    assert m, f"没在 {SDK_CLIENT} 里读到客户端 timeout ⇒ 这条判据没连上被测物"
    return float(m.group(1))


def test_B1_slow_source_does_not_sink_the_whole_search(monkeypatch):
    """预算这件事测的是"慢那一支被收住、快那一支照发"——所以桩里的题名必须**带主题词锚**：
    🔴 轮 309 起 `multi_source_search` 接了产品的相关性尺（`filter_relevant`，标题没有同种文字的
    主题词就滤掉），题名写成"按时到的那篇"这种占位话会被滤除判成"没结果"，
    那测的就不是预算而是滤除了（本轮全量跑就是这么红的，读数在 `输出/_logs/r309_g1_full_sources.log`）。"""
    async def slow(topic, limit):
        await asyncio.sleep(5)
        return [{"title": "多智能体协作：迟到的那篇", "authors": ["某"], "year": 2026, "venue": "V",
                 "doi": "10.1/x", "citations": 0, "abstract": ""}]

    async def fast(topic, limit):
        return [{"title": "多智能体协作：按时到的那篇", "authors": ["甲"], "year": 2025, "venue": "U",
                 "doi": "10.2/y", "citations": 3, "abstract": ""}]

    monkeypatch.setattr(sources, "crossref_search", slow)
    monkeypatch.setattr(sources, "arxiv_search_raw", fast)
    t0 = time.monotonic()
    out = asyncio.run(sources.multi_source_search("多智能体协作", limit=4, budget_s=0.2))
    spent = time.monotonic() - t0
    assert "按时到的那篇" in out, out[:200]
    assert "未在 0 秒预算内返回" in out or "预算内返回" in out, out[:400]
    assert spent < 2.0, f"超预算那一支没被收住，实际花了 {spent:.1f}s"


def test_B2_all_sources_over_budget_returns_fast_instead_of_hanging(monkeypatch):
    async def slow(topic, limit):
        await asyncio.sleep(5)
        return []

    monkeypatch.setattr(sources, "crossref_search", slow)
    monkeypatch.setattr(sources, "arxiv_search_raw", slow)
    t0 = time.monotonic()
    with pytest.raises(RuntimeError):
        asyncio.run(sources.multi_source_search("多智能体协作", limit=4, budget_s=0.2))
    assert time.monotonic() - t0 < 2.0, "两路都超时却还在等，等于没预算"


def test_B3_none_budget_keeps_the_old_shape(monkeypatch):
    """`budget_s=None` 必须还是"两路都等到底"——离线仪器与批量跑靠它拿完整结果。"""
    async def slightly_slow(topic, limit):
        await asyncio.sleep(0.25)
        return [{"title": "慢但完整", "authors": ["乙"], "year": 2024, "venue": "W",
                 "doi": "10.3/z", "citations": 1, "abstract": ""}]

    monkeypatch.setattr(sources, "crossref_search", slightly_slow)
    monkeypatch.setattr(sources, "arxiv_search_raw", slightly_slow)
    out = asyncio.run(sources.multi_source_search("x", limit=4, budget_s=None))
    assert out.count("慢但完整") == 2, out[:400]


def test_B4_budgets_stay_under_the_callers_own_timeout():
    """两个预算都要**小于 SDK 客户端写死的那颗超时**，否则"我们在答，对方已经不在了"照旧发生。"""
    import literature_partner as lp
    caller = _sdk_caller_timeout()
    assert 0 < lp.SEARCH_BUDGET_S < lp.CAPABILITY_BUDGET_S < caller, (
        f"SEARCH={lp.SEARCH_BUDGET_S} CAPABILITY={lp.CAPABILITY_BUDGET_S} 调用方超时={caller}"
        " ⇒ 预算必须一层套一层且都短于调用方那侧")


def test_B8_review_generation_is_capped_small_enough_to_fit_its_budget():
    """综述那条 GLM 的**输出上限**必须小到能在 CAPABILITY_BUDGET_S 里跑完。

    为什么钉这个而不是钉 prompt 里的字数：轮 308 实测（`probe_r308_glm_timing.py`，容器内连打 3 次）
    写"控制在 300 字内"时模型照样吐 815—1116 字、要 26.9—28.5 秒 ⇒ 撞穿 20 秒预算，服务端答 failed；
    把 `max_tokens` 从 1024 压到 480 之后是 9.1—10.4 秒、259—331 字。
    生成速率 ~35—44 字/秒 ⇒ 想在 20 秒内跑完，输出上限大致不能超过 ~700 字（≈480—512 token）。
    """
    src = (ROOT / "literature_partner.py").read_text(encoding="utf-8")
    caps = [int(x) for x in re.findall(r'"max_tokens":\s*(\d+)', src)]
    assert caps, "没找到 GLM 的 max_tokens ⇒ 判据没有分母，不许默默放过"
    assert max(caps) <= 512, (
        f"GLM max_tokens={caps} 放得太高：实测生成速率 ~35—44 字/秒，"
        "超过 ~512 token 就会顶穿 20 秒预算、调用方收到 failed（见本用例 docstring 的读数）")


def test_B5_over_budget_answers_a_parseable_failed_task_and_still_counts(tmp_path, monkeypatch):
    """外墙超时要落成一条**可解析的**任务回答（state=failed ＋ 写明原因），不是崩栈也不是不回话。

    这条还顺手钉住"超时那次一样落账"：失败的调用同样是一次被调用。
    """
    import importlib.util
    monkeypatch.setenv("AMP_LOG_DIR", str(tmp_path))
    monkeypatch.setenv("LLM_MOCK", "1")
    spec = importlib.util.spec_from_file_location("lp_r307", str(ROOT / "literature_partner.py"))
    lp = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(lp)
    monkeypatch.setattr(lp, "CAPABILITY_BUDGET_S", 0.2)
    monkeypatch.setattr(lp, "SEARCH_BUDGET_S", 0.15)

    async def hang(topic, limit=10, year_range=None):
        await asyncio.sleep(5)
        return "不该到这里"

    monkeypatch.setattr(lp, "_search", hang)

    async def body():
        return await lp.on_start(_cmd("检索多智能体协作方向的文献 3 篇"), None)

    res = asyncio.run(body())
    state = res.status.state.value if hasattr(res.status.state, "value") else str(res.status.state)
    assert state == "failed", state
    text = "".join((i.text or "") for i in (res.status.dataItems or []))
    assert "未在" in text and "秒预算内完成" in text, text[:200]

    client = TestClient(lp.app)
    log = tmp_path / f"amp_access_{lp.PARTNER_NAME}.jsonl"
    before = len([x for x in log.read_text(encoding="utf-8").splitlines() if x.strip()]) if log.exists() else 0
    r = client.post("/rpc", json={"jsonrpc": "2.0", "id": "r1", "method": "rpc", "params": {"command": {
        "type": "task-command", "id": "c1", "sentAt": "2026-10-06T12:00:00Z", "senderRole": "leader",
        "senderId": "1.2.156.3088.1.1.LDR001.LDR001.1.0002", "command": "start",
        "taskId": "task-" + str(uuid.uuid4()), "sessionId": "s1",
        "dataItems": [{"type": "text", "id": "t1", "text": "检索多智能体协作方向的文献 3 篇"}]}}})
    assert r.status_code == 200, r.text
    after = len([x for x in log.read_text(encoding="utf-8").splitlines() if x.strip()])
    assert after - before == 1, f"超时那一发没落账（Δ={after - before}）⇒ 失败也算被调用这条被打断"


def _cmd(text: str):
    from acps_sdk.aip import TaskCommand, TaskCommandType, TextDataItem
    return TaskCommand(id="cmd-" + str(uuid.uuid4()), sentAt="2026-10-06T12:00:00Z",
                       senderRole="leader", senderId="1.2.156.3088.1.1.LDR001.LDR001.1.0002",
                       command=TaskCommandType.Start, taskId="task-" + str(uuid.uuid4()),
                       sessionId="s-r307", dataItems=[TextDataItem(text=text)])


def test_B6_card_advertises_the_reachable_endpoint_only():
    """卡是别人照着调的：广告的 JSONRPC 地址必须就是**哨兵实测可达**那颗，
    而且明文 http 那支不许声明 mtls（那会让守规则的调用方直接放弃这条）。

    🔴 轮 309 改过形状：轮 307 那版钉的是「JSONRPC 只能有一条＝明文那颗」，理由是那时唯一那条
    https 地址带着 `sslip.io` 域名、带该 SNI 的握手会被链路 RST（本轮 T1 三臂复测仍在）。
    现在卡片上的 TLS 条换成了 **IP 形式**、与平台 1.5.1 那张注册的卡逐字相等，且它的可达性有
    实测背书（`probe_r307_card_reach.py` 的 Agent CA + 不验主机名臂，轮 308c 公网 `TLS_PUBLIC_RC=0`）
    ⇒ 两条并存：明文给裸 HTTP 的调用方，mTLS 给 SDK 默认档。域名形式那条仍然必须判红。
    """
    import importlib.util
    spec = importlib.util.spec_from_file_location("pb_r307", str(ROOT / "partner_base.py"))
    pb = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(pb)
    import json
    acs = ROOT / "acs" / "acs-literature.json"
    aic = json.loads(acs.read_text(encoding="utf-8"))["aic"]
    card = pb.partner_card("yanban-literature", aic, acs_dir=ROOT / "acs")
    assert card, "卡取不到（acs 文件或 aic 不一致）⇒ 这条没连上被测物"
    rpcs = [e for e in card["endPoints"] if e.get("transport") == "JSONRPC"]
    assert len(rpcs) == 2, card["endPoints"]
    plain = [e for e in rpcs if e["url"].startswith("http://")]
    tls = [e for e in rpcs if e["url"].startswith("https://")]
    assert len(plain) == 1 and plain[0]["url"] == pb.PUBLIC_RPC_URL, rpcs
    assert plain[0]["url"].endswith("/rpc")
    assert plain[0].get("security") in ([], None), f"明文 http 上声明了 {plain[0].get('security')}"
    assert len(tls) == 1 and tls[0]["url"] == pb.PUBLIC_AIP_TLS_URL, rpcs
    assert {"mtls"} == {next(iter(sec)) for sec in tls[0].get("security") or []}, \
        f"TLS 那条没声明 mtls（平台注册侧要求它）：{tls[0]}"
    # 老地址（被 ICP 按域名拦的那条）不许再出现在卡上——两条都不许
    assert "sslip.io" not in str(card), "卡上还广告着被 ICP 拦掉的旧域名"


def test_B7_sources_default_budget_is_on():
    """默认值必须是"带预算"——新调用点漏传参数时也该保护外部调用方。"""
    import inspect
    sig = inspect.signature(sources.multi_source_search)
    got = sig.parameters["budget_s"].default
    assert got is not None and float(got) > 0, f"budget_s 默认又变回 None 了（{got}）"
