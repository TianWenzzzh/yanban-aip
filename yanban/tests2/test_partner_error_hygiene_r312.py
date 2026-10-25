# -*- coding: utf-8 -*-
"""轮 312 第二件（队长 §4.1「移除面向开发者的…原始异常…低层诊断」＋ §4.4「记录实际错误分类」）。

本轮自己就撞出过一条真证据：改 `_search` 签名时，一个
`TypeError: hang() got an unexpected keyword argument 'year_range'` **整句被发进了应答文本**
（`输出/_logs/r312_p4_full_after_year_fix.log`）。那正是这两节要避免的东西：
调用方拿到的是我们的内部堆栈片段，而且什么下一步动作都没说。

判据分两侧，缺一条就是半个方案：
  E1—E3 分类：网络／限流／身份／超时／内部缺陷各归各的话，**原文不外发**（金丝雀串不许出现在应答里，
      Python 异常类名也不许出现在应答里）；
  E4 正向对照：**产品自己写给调用方的那几句话必须一字不改照旧外发**（预算超时那句、"所有数据源均不可用"那句）
      —— 把消毒做成一刀切等于把有用的提示也砍了；
  E5 服务端日志里必须还看得见原始异常（不外发≠不记录，§4.4 要的是"记录实际错误分类"）；
  E7 群组 MQ 那条 `fail_task` 同样不外发原文（同一个缺陷的另一半，`literature_partner.py` 的群组分支）；
  E8—E9 白名单自己不许留泄漏面：括号里那条 `[^）]*` 能吞任意文本（本轮自查抓到的，见台账 82），
      E8 用植入的金丝雀串证明它会放行，E9 把"白名单只许字面量＋数字位"接成常驻判据；
  E10 同源腿：把两路取数都桩成不可用、让**产品自己**抛出它当时那句话，再喂回白名单——
      文案与白名单不许各自漂（台账 83；E4 那份是测试里自己抄的期望串，看不见这种漂移）。

🔴 形状上的一个坑（轮 312 实测）：本文件**不许**在模块顶层 `import literature_partner`。
它按 `AMP_LOG_DIR` 环境变量在 import 时就绑好 emitter，而 pytest 的采集发生在所有用例之前
⇒ 真实模块名一旦被顶层 import 抢下，`tests2/test_amp_access_audit.py::test_literature_partner_is_no_longer_on_the_bare_sdk_route`
会在自己的 `monkeypatch.setenv("AMP_LOG_DIR", tmp_path)` 之前就看到已绑好的模块，直接判红。
所以这里只按**唯一别名**加载副本（与 `test_partner_year_constraint_r312.py` 同形）。
"""
from __future__ import annotations

import asyncio
import importlib.util
import logging
import pathlib
import re
import sys
import time
import uuid

import httpx
import pytest

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT))

import sources                                              # noqa: E402
from acps_sdk.aip import TextDataItem                       # noqa: E402
from acps_sdk.aip.aip_base_model import TaskCommand         # noqa: E402


def _load_lp(tag: str):
    spec = importlib.util.spec_from_file_location(
        f"lp_hygiene_r312_{tag}", str(ROOT / "literature_partner.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


lp = _load_lp("main")
_failure_line = lp._failure_line

CANARY = "CANARY-r312-内部堆栈串"


def _cmd(text: str) -> TaskCommand:
    return TaskCommand(**{
        "type": "task-command", "id": "c-" + uuid.uuid4().hex[:8],
        "sentAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "senderRole": "leader", "senderId": "1.2.156.3088.1.1.LDR001.LDR001.1.0002",
        "command": "start", "taskId": "t-r312-leak-" + uuid.uuid4().hex[:6],
        "dataItems": [TextDataItem(text=text)], "sessionId": "s-r312-leak"})


def _http_error(code: int) -> httpx.HTTPStatusError:
    req = httpx.Request("GET", "https://api.crossref.org/works")
    resp = httpx.Response(code, request=req)
    return httpx.HTTPStatusError(f"{code} {CANARY}", request=req, response=resp)


def _text_of(status) -> str:
    return "".join((i.text or "") for i in (status.dataItems or []))


# ── E1—E3：各归各的分类，原文不外发 ───────────────────────────────────────────
@pytest.mark.parametrize("exc,expect", [
    (httpx.ConnectError(CANARY), "连不上上游"),
    (httpx.ReadTimeout(CANARY), "上游响应超时"),
    (_http_error(429), "限流"),
    (_http_error(403), "访问身份"),
    (_http_error(503), "上游服务异常"),
    (TypeError(f"hang() got an unexpected keyword argument '{CANARY}'"), "内部"),
])
def test_E1_each_failure_class_gets_its_own_words(exc, expect):
    got = _failure_line(exc)
    assert expect in got, f"{type(exc).__name__} 该归成「{expect}」，实际：{got!r}"
    assert CANARY not in got, f"原始异常文本外发给调用方了：{got!r}"


def test_E2_no_python_class_names_leak_into_the_answer():
    for exc in (httpx.ConnectError("x"), TypeError("y"), KeyError("z"), RuntimeError("w")):
        got = _failure_line(exc)
        assert type(exc).__name__ not in got, f"内部判定名进了用户可见结果：{got!r}"


def test_E3_failure_line_still_tells_the_caller_what_to_do_next():
    got = _failure_line(httpx.ConnectError(CANARY))
    assert any(k in got for k in ("重试", "稍后", "再发")), f"只说了坏了，没说下一步：{got!r}"
    assert "伪造" in got or "没有" in got, f"没讲清这一轮没交付结果：{got!r}"


# ── E4：正向对照——我们自己写的话必须原样发出去 ───────────────────────────────
def test_E4_product_authored_sentences_are_still_passed_through_verbatim():
    ours = "文献检索未在 20 秒预算内完成，请直接重试一次（或把主题写窄一点）"
    assert _failure_line(asyncio.TimeoutError(ours)) == ours, "预算那句被消毒过头＝砍掉了有用的提示"
    all_down = "所有数据源均不可用（可能触发限流，请约 1 分钟后重试）"
    assert _failure_line(RuntimeError(all_down)) == all_down


def test_E4b_over_budget_task_answers_the_authored_hint_not_a_generic_one(monkeypatch):
    mod = _load_lp("e4b")

    async def boom(topic: str, limit: int, year_range=None) -> str:
        raise asyncio.TimeoutError("文献检索未在 20 秒预算内完成，请直接重试一次（或把主题写窄一点）")
    monkeypatch.setattr(mod, "_search", boom)

    got = asyncio.run(mod.on_start(_cmd("检索脉冲神经网络低功耗编码的论文 3 篇"), None))
    assert "未在 20 秒预算内完成" in _text_of(got.status), _text_of(got.status)[:200]


# ── E5：不外发≠不记录 ────────────────────────────────────────────────────────
def test_E5_raw_exception_is_recorded_server_side(monkeypatch, caplog):
    mod = _load_lp("e5")

    async def boom(topic: str, limit: int, year_range=None) -> str:
        raise httpx.ConnectError(CANARY)
    monkeypatch.setattr(mod, "_search", boom)

    with caplog.at_level(logging.WARNING):
        got = asyncio.run(mod.on_start(_cmd("检索脉冲神经网络低功耗编码的论文 3 篇"), None))
    text = _text_of(got.status)
    assert CANARY not in text, f"应答里还有原始异常：{text[:200]!r}"
    joined = "\n".join(r.getMessage() + " " + (r.exc_text or "") for r in caplog.records)
    assert CANARY in joined, "外发是收住了，但服务端也没记下原始异常＝这轮故障无从归因"


# ── E6：展示层的同一件事（源不可用那一行也不许带原文） ────────────────────────
def test_E6_source_unavailable_line_drops_raw_text_but_keeps_the_budget_hint(monkeypatch):
    async def boom_cr(topic, limit):
        raise httpx.ConnectError(CANARY)

    async def ok_ax(topic, limit):
        return [{"title": "Phishing webpage detection ok", "authors": ["A"], "year": 2025,
                 "venue": "arXiv", "doi": "arXiv:2501.00001", "citations": None}]

    async def no_translate(q):
        raise AssertionError("拉丁主题不该花英译配额")

    monkeypatch.setattr(sources, "crossref_search", boom_cr)
    monkeypatch.setattr(sources, "arxiv_search_raw", ok_ax)
    monkeypatch.setattr(sources, "_english_query", no_translate)
    out = asyncio.run(sources.multi_source_search("phishing webpage detection", limit=3, budget_s=None))
    assert CANARY not in out, f"源不可用那行把原始异常带进产物了：{out[:400]!r}"
    assert "Crossref 暂不可用" in out, out[:400]
    assert any(k in out for k in ("连不上", "网络", "上游")), f"没给出这一支的成因分类：{out[:400]!r}"
    assert "Phishing webpage detection ok" in out, "一支挂了不该把另一支也吞掉"


# ── E7：群组 MQ 那条 `fail_task` 是同一个缺陷的另一半（只修 HTTP 那支＝半成品） ──
def test_E7_group_mq_failure_path_also_drops_raw_text(monkeypatch):
    mod = _load_lp("e7")

    async def boom(topic: str, limit: int, year_range=None) -> str:
        raise httpx.ConnectError(CANARY)
    monkeypatch.setattr(mod, "_search", boom)

    seen = {}

    class FakeClient:
        async def accept_task(self, task_id, session_id):
            pass

        async def start_working(self, task_id, session_id):
            pass

        async def submit_for_completion(self, task_id, session_id, products):
            seen["submitted"] = products

        async def fail_task(self, task_id, session_id, message):
            seen["failed"] = message

    monkeypatch.setitem(mod.group_clients, "g-r312", FakeClient())
    cmd = TaskCommand(**{
        "type": "task-command", "id": "c-mq", "sentAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "senderRole": "leader", "senderId": "1.2.156.3088.1.1.LDR001.LDR001.1.0002",
        "command": "start", "taskId": "t-r312-mq", "sessionId": "s-r312-mq", "groupId": "g-r312",
        "dataItems": [TextDataItem(text="检索脉冲神经网络低功耗编码的论文 3 篇")]})

    asyncio.run(mod._on_group_task_inner(cmd, True))
    assert "failed" in seen and "submitted" not in seen, f"群组这条没按预期失败：{seen}"
    assert CANARY not in seen["failed"], f"MQ 的 fail_task 还在外发原始异常：{seen['failed']!r}"
    assert "连不上" in seen["failed"], seen["failed"]


# ── E8：白名单里那条通配符就是第二个泄漏面（轮 312 自查抓到，本轮自己写的）────────
def test_E8_a_wildcard_in_the_allow_list_must_not_smuggle_raw_text():
    """`所有数据源均不可用（…）` 以前写成 `（[^）]*）`——括号里能塞任意文本。
    今天只有一处 `raise` 用它，但那道白名单是**外发口**：将来任何一处把上游原文塞进括号，
    §4.1 要挡的东西就从这个洞原样出去了。所以括号里也必须是字面量。"""
    planted = f"所有数据源均不可用（{CANARY}）"
    assert sources.outward_text(RuntimeError(planted)) == "", \
        "白名单放行了一条能吞任意文本的形状 ⇒ 原始报错可原样外发"
    got = _failure_line(RuntimeError(planted))
    assert CANARY not in got, f"应答里还有原始异常：{got!r}"
    assert "所有数据源均不可用" not in got, f"这句被当成产品原话放行了，但它带着没消毒的括号内容：{got!r}"


# ── E9：机制闸门——白名单只许「字面量＋数字位」，不许任何能吞文本的形状 ──────────
def test_E9_allow_list_entries_are_literal_apart_from_numeric_fields():
    """E8 那种洞不能只修一次：把"白名单的形状"本身接成判据。"""
    bad = []
    for pat in sources._OURS_PATTERNS:
        stripped = re.sub(r"\\d\+", "", pat)          # 唯一允许的可变位：秒数那样的数字段
        for token in (".", "*", "+", "[", "\\w", "\\s", "\\S", "\\d"):
            if token in stripped:
                bad.append(f"{pat[:24]}… 里还剩 {token!r}")
    assert not bad, "外发白名单出现能吞任意文本的形状（改成字面量，别放宽这条判据）：" + "；".join(bad)


# ── E10：同源腿——产品真抛的那句话必须逐字放行（台账 83）──────────────────────
def test_E10_the_message_sources_actually_raises_is_still_passed_through(monkeypatch):
    """E4 的期望串是测试里**自己抄的**一份：文案与白名单谁单独改一个字，E4 看不见（它测自己的抄本），
    失败方向还不坏——`outward_text` 返回空串，有用的那句静默消失。这一条不抄：把两路取数都桩成
    不可用，让产品自己抛出它当时那句话，再喂回白名单。从此这两处绑在一起，改一侧不改另一侧当场红。

    主题用拉丁文（`_needs_en_translation` 短路）⇒ 这一发不碰英译、也不花模型配额。
    """

    async def down(*_a, **_k):
        raise ConnectionError(f"{CANARY} upstream 503")

    monkeypatch.setattr(sources, "crossref_search", down)
    monkeypatch.setattr(sources, "arxiv_search_raw", down)
    try:
        asyncio.run(sources.multi_source_search("phishing website detection", limit=3))
    except RuntimeError as exc:
        msg = str(exc)
    else:
        raise AssertionError("两路都桩成不可用之后，产品没有抛「所有数据源均不可用」这句话＝这条腿在量空气")
    assert CANARY not in msg, f"产品抛出的这句话里带着上游原文：{msg!r}"
    tampered = msg.replace("1 分钟", "两分钟")
    assert tampered != msg, f"合成对照找不到可改的位（这句话变了形状，先看 E4 与白名单）：{msg!r}"
    assert sources.outward_text(RuntimeError(msg)) == msg, \
        "白名单不再放行产品自己抛出的这句话＝有用的提示静默消失；改文案要同时改白名单，别放宽这条"
    assert sources.outward_text(RuntimeError(tampered)) == "", \
        "改一个字仍然放行 ⇒ 上面那条断言恒真，白名单其实在无条件放行"
