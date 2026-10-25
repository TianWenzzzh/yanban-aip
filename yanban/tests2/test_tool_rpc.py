"""B8 验收③ 的本地这一半：命名工具经**被审计的**入口被调用，一次入站 = 一条 access 记录，
且 caller 记的是真实 senderId（不是写死的 leader 自己）。

为什么开这几条：`companion/api/tools.py` 那张表过去只是清单——SPEC 里任何一颗工具都没有一条
会被审计的入站路径（轮 197 实测：`/companion/*` 裸路由、`companion/` 全目录不写 access 日志）。
`tool_rpc` 补的就是那一小段派发层。这段代码最容易自欺的地方是「响应 200 就当成功了」，
而审计入口的全部意义在于**记录会不会落**：所以有一条专门读 jsonl 本身，不看响应。

判据形状：可派发集合由 `tools.dispatchable()` 现场算（GET + 非 internal + 返回 dict），
这里只钉「它等于我量过的那个形状」和「写类工具/未知工具/参数不对都必须 Failed」，
不在测试里抄第二份名单。
"""
from __future__ import annotations

import json
import uuid
from pathlib import Path

from acps_sdk.aip import TaskState
from acps_sdk.amp import AccessEmitter
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
import pytest

import tool_rpc
from companion.api import tools
from companion.api.router import router

AIC = "1.2.156.3088.1.BUPT.LEADER.R197TST.0001"
SENDER = "1.2.156.3088.1.9999.R197CHK.CALLER.0001"
# 轮 197 实测：对外可宣告的 GET 工具 24 颗，其中返回 dict 的 20 颗可派发
FLOOR = 15


def _client(tmp_path: Path):
    app = FastAPI()
    log = tmp_path / "amp_access_yanban-leader.jsonl"
    emitter = AccessEmitter(log, aic=AIC)
    table = tool_rpc.install(app, routes=router.routes, aic=AIC, name="yanban-leader",
                             log_dir=tmp_path, emitter=emitter, identity_binding_enabled=False)
    return TestClient(app), table, log


def _rpc(client: TestClient, text: str, sender: str = SENDER):
    # 每次调用换一个新 taskId：SDK 的 TaskManager 按 taskId 存任务，
    # 复用同一个 id 会把上一条用例留下的 products/status 当成这次的结果读到（首跑就是这么假绿的）。
    return client.post("/rpc", json={
        "jsonrpc": "2.0", "id": "r197", "method": "rpc",
        "params": {"command": {
            "type": "task-command", "id": "c1",
            "taskId": str(uuid.uuid4()),
            "sentAt": "2026-10-01T03:00:00Z", "senderRole": "partner", "senderId": sender,
            "command": "start",
            "dataItems": [{"id": "d1", "type": "text", "text": text}]}}})


def _payload(resp) -> dict:
    """从 JSON-RPC 响应里捞出那段工具结果文本（拿不到就返回空 dict，让断言去红）。"""
    body = resp.json()
    items = (((body.get("result") or {}).get("products") or [{}])[0].get("dataItems")
             or (body.get("result") or {}).get("status", {}).get("dataItems") or [])
    for it in items:
        if it.get("type") == "text" and it.get("text"):
            try:
                return json.loads(it["text"])
            except json.JSONDecodeError:
                return {"raw": it["text"]}
    return {"raw": json.dumps(body, ensure_ascii=False)[:400]}


def test_dispatchable_is_the_measured_shape_not_a_hand_list():
    """可派发集合必须=「external 里 GET 的那些」∩「端点直接返回 dict」，且不空到没人能调。
    没有这条，规则被改成一句 `return {}` 也能让下面几条全部假绿。"""
    table = tool_rpc.tools.dispatchable(router.routes)
    declared = {t["name"] for t in tools.external(router.routes) if t["method"] == "GET"}
    assert set(table) <= declared, f"派发表里出现了没被宣告的名字：{set(table) - declared}"
    assert len(table) >= FLOOR, f"可派发工具只剩 {len(table)} 颗（地板 {FLOOR}）⇒ 规则或那张表漂了"
    # 返回文件流/纯文本的那几颗必须被挡在外面（它们不是 JSON 面，别在这条入口上装能）
    for blocked in ("export_papers", "export_knowledge", "read_artifact"):
        assert blocked not in table, f"{blocked} 返回的不是 dict，不该进这条入口"


def test_named_tool_call_over_the_audited_entry_works(tmp_path):
    client, table, _log = _client(tmp_path)
    assert "list_tools" in table
    resp = _rpc(client, json.dumps({"tool": "list_tools", "args": {}}))
    assert resp.status_code == 200, resp.text
    body = resp.json()
    # 状态也要对上：只读到 products 会把「产品设了但状态没推进」那种半截结果当成成功
    state = ((body.get("result") or {}).get("status") or {}).get("state")
    assert state == TaskState.AwaitingCompletion.value, f"任务停在 {state}，没走到待完成"
    got = _payload(resp)
    names = [t["name"] for t in got.get("result", {}).get("tools", [])]
    assert "inspect_project" in names and "read_pdf_span" in names, f"工具清单没原样带回来：{str(got)[:200]}"


def test_write_tool_and_unknown_tool_are_refused_not_simulated(tmp_path):
    """写类工具（POST）与未知名字都要 Failed 并给出理由。
    没有这条，"这条入口不开放改数据"可以只是嘴上说说——表里少一颗也算它守住了。"""
    client, _table, _log = _client(tmp_path)
    post_names = sorted(t["name"] for t in tools.external(router.routes)
                        if t["method"] != "GET")
    assert post_names, "路由表里一颗非 GET 工具都没有 ⇒ 这条判据无从验证，先确认语法没漂"
    for name in (post_names[0], "这颗工具根本不存在"):
        resp = _rpc(client, json.dumps({"tool": name, "args": {}}))
        text = json.dumps(resp.json(), ensure_ascii=False)
        assert '"failed"' in text.lower(), f"{name} 没被拒：{text[:200]}"
        assert "不在这条入口的可派发集合" in text, f"{name} 拒了但没给理由：{text[:200]}"


def test_garbage_body_is_refused_per_branch_not_defaulted(tmp_path):
    """`parse_call` 的三条拒绝分支各自要被真喂一次：非 JSON / 缺 tool / args 不是对象。
    没有这条，这三处 raise 可以全是装饰——把任一条换成"默认跑 list_tools"也没人看得见。"""
    client, _table, _log = _client(tmp_path)
    cases = [
        ("这不是 JSON", "这条入口只收 JSON"),
        ('{"args": {}}', "缺 tool 字段"),
        ('{"tool": "list_tools", "args": []}', "args 要是一个对象"),
    ]
    for junk, why in cases:
        resp = _rpc(client, junk)
        text = json.dumps(resp.json(), ensure_ascii=False)
        assert '"failed"' in text.lower(), f"{junk!r} 没被拒：{text[:200]}"
        assert why in text, f"{junk!r} 拒了但没给出该分支的理由（期望含 {why!r}）：{text[:200]}"


def test_missing_required_arg_is_reported_as_such(tmp_path):
    client, _table, _log = _client(tmp_path)
    resp = _rpc(client, json.dumps({"tool": "inspect_project", "args": {}}))
    text = json.dumps(resp.json(), ensure_ascii=False)
    assert '"failed"' in text.lower() and "project_id" in text, f"参数缺失没报出必填：{text[:200]}"


def test_one_inbound_call_leaves_exactly_one_record_with_the_real_caller(tmp_path):
    """读 jsonl 本身，不看响应：一次入站 = 一条，caller = 我发出去的 senderId，callee = 本队 aic。
    这条是 B8③ 的本地半，也是「裸路由被调用数不出来」那个原事故的夹具。"""
    client, _table, log = _client(tmp_path)
    _rpc(client, json.dumps({"tool": "list_tools", "args": {}}))
    lines = [x for x in log.read_text(encoding="utf-8").splitlines() if x.strip()]
    assert len(lines) == 1, f"记录条数 {len(lines)}（期望 1）⇒ 要么没落盘，要么一次调用落了两条"
    rec = json.loads(lines[0])
    body = rec.get("body") or {}
    assert (body.get("caller") or {}).get("aic") == SENDER, f"caller 不是真实调用方：{body.get('caller')}"
    assert (body.get("callee") or {}).get("aic") == AIC, f"callee 不是本队：{body.get('callee')}"


def test_default_emitter_writes_the_named_file_under_the_team_aic(tmp_path):
    """不传 emitter 时，install 必须自己按 amp_access_<name>.jsonl 落盘、callee 记本队 aic。
    上面那条读 jsonl 的用例自带 emitter，恰好绕过这条默认分支——所以这里真喂一次调用。"""
    app = FastAPI()
    # 显式 False 而不是吃默认值：这条测的是"不传 emitter 时默认落盘分支"，
    # 而 tool_rpc.install 的默认值轮 208 起与伙伴侧对齐成 True（fail-closed）——
    # 这里不写死 False 的话，那次 /rpc 会被身份绑定拒掉，"记录条数=1"就成了给一条被拒的调用背书。
    table = tool_rpc.install(app, routes=router.routes, aic=AIC, name="r197default", log_dir=tmp_path,
                             identity_binding_enabled=False)
    assert table, "默认分支连派发表都没建起来"
    client = TestClient(app)
    _rpc(client, json.dumps({"tool": "list_tools", "args": {}}))
    log = tmp_path / "amp_access_r197default.jsonl"
    listing = sorted(p.name for p in tmp_path.iterdir())
    assert log.exists(), f"默认分支没按 amp_access_<name>.jsonl 落盘，目录里是 {listing}"
    lines = [x for x in log.read_text(encoding="utf-8").splitlines() if x.strip()]
    assert len(lines) == 1, f"默认分支记录条数 {len(lines)}（期望 1）⇒ 要么没落盘，要么一次调用落了两条"
    rec = json.loads(lines[0])
    body = rec.get("body") or {}
    assert (body.get("callee") or {}).get("aic") == AIC, f"默认分支的 callee 不是本队：{body.get('callee')}"


def test_install_refuses_to_open_the_entry_on_an_empty_table(tmp_path, monkeypatch):
    """派发表为空 = 认知层没挂上，这时必须当场拒绝启动，不许"开一条谁都不能调的入口"。
    没有这条，那行 raise 可以只是装饰：空表照样注册路由，调用方看着在线、实际颗颗都拒。"""
    monkeypatch.setattr(tools, "dispatchable", lambda routes: {})
    with pytest.raises(LookupError, match="空"):
        tool_rpc.install(FastAPI(), routes=router.routes, aic=AIC, name="r197empty", log_dir=tmp_path)


# trusted 那一支会真的把 senderId 当 AIC 校验（demo 支不校验，所以文件里那颗 9 段的 SENDER 用不了）：
# 形状照搬 `tests/test_identity_mode_unit.py:23-24` 那两颗已过 SDK 校验的 10 段 AIC。
CALLER = "1.2.156.3088.1.1.R208CK.R208CK.1.0001"
FORGED = "1.2.156.3088.1.1.R208CK.R208CK.1.9999"


def _trusted_client(tmp_path: Path, peer_aic: str | None) -> TestClient:
    """`identity_binding_enabled=True` 那一支：桩生产 mTLS 中间件，把证书解出的 AIC 落进 request.state。

    桩的形状照搬 `tests/test_identity_mode_unit.py:39` 那份已跑绿的（同一条 SDK 判据，不重新发明）。
    轮 198 复核登记的缺口就在这儿：这个文件当时只跑 False 那一支，所以「Leader 这条被审计的 /rpc
    在 trusted 下会不会拒」一直没有用例守着——上面那 11 条全绿也不覆盖它。
    """
    app = FastAPI()
    # 显式传 True，不吃默认值：这三条钉的是"这条入口认不认这个开关"，
    # 而"默认值本身是不是 fail-closed"由 test_the_two_audited_entries_share_one_fail_closed_default 单独钉
    # —— 混在一起的话，把默认值改回 False 这一枚植入会同时红四条，分不清是哪一层丢的。
    tool_rpc.install(app, routes=router.routes, aic=AIC, name="r208trusted", log_dir=tmp_path,
                     identity_binding_enabled=True)

    @app.middleware("http")
    async def stub_peer_cert(request: Request, call_next):
        request.state.peer_aic = peer_aic
        return await call_next(request)

    return TestClient(app)


def test_trusted_entry_passes_a_certified_caller(tmp_path):
    """正向对照：证书里的 AIC 与 senderId 一致 ⇒ 工具真的跑出来了。

    没有这条，"trusted 下颗颗都拒"这种植入（开关焊死成 True 却不真比对）也能让下面两条红着通过。
    """
    client = _trusted_client(tmp_path, peer_aic=CALLER)
    r = _rpc(client, json.dumps({"tool": "list_tools", "args": {}}), sender=CALLER)
    assert r.status_code == 200
    assert r.json().get("error") is None, f"认证相符的那一支被拒了：{r.json().get('error')}"
    assert _payload(r).get("tool") == "list_tools", f"说是放行了却没跑到工具：{r.json()}"


def test_trusted_entry_rejects_forged_sender(tmp_path):
    """缺口那一格：trusted 下 senderId 与证书 peer 不符 ⇒ -32009，不许降级成"看起来成功"。"""
    client = _trusted_client(tmp_path, peer_aic=CALLER)
    r = _rpc(client, json.dumps({"tool": "list_tools", "args": {}}), sender=FORGED)
    err = r.json().get("error") or {}
    assert err.get("code") == -32009, f"伪造 senderId 没被拒成 -32009，实得 {err}｜整包 {r.json()}"
    assert "does not match peer AIC" in str(err.get("data") or ""), f"码对了但理由不是身份不符：{err}"
    assert r.json().get("result") is None, f"被拒的那一支还是把 result 吐出去了：{r.json().get('result')}"


def test_trusted_entry_rejects_missing_peer_certificate(tmp_path):
    """无证书（request.state.peer_aic 缺失）是"认证缺失"，与"身份不符"分两个码：-32008。"""
    client = _trusted_client(tmp_path, peer_aic=None)
    r = _rpc(client, json.dumps({"tool": "list_tools", "args": {}}))
    err = r.json().get("error") or {}
    assert err.get("code") == -32008, f"无证书没被拒成 -32008，实得 {err}｜整包 {r.json()}"


def test_the_two_audited_entries_share_one_fail_closed_default():
    """两颗默认值必须一致且是 True：`partner_base.add_audited_rpc_router` 一直是 True，
    而 `tool_rpc.install` 过去是 False —— 今天不被坑只因 `leader.py` 那行显式传了
    `_identity_binding()`，下一个调用方漏传就会在"唯一一条被审计的入口"上静默关掉身份绑定。
    """
    import inspect

    import partner_base

    mine = inspect.signature(tool_rpc.install).parameters["identity_binding_enabled"].default
    theirs = inspect.signature(partner_base.add_audited_rpc_router).parameters[
        "identity_binding_enabled"].default
    assert mine is True, f"这条入口的默认值不是 fail-closed：{mine!r}"
    assert mine == theirs, f"两颗默认值不一致（迟早骗到下一个人）：tool_rpc={mine!r} partner_base={theirs!r}"


def test_leader_actually_exposes_the_audited_entry():
    """接线必须真的在 leader.app 上：前面几条都自己建 app，leader.py 里那行 install 没人守。
    没有这条，把这行删掉/挂到别的 app 上，本轮那份"外部能调命名工具"的读数就只活在测试里。"""
    import leader
    paths = {getattr(r, "path", "") for r in leader.app.routes}
    assert "/rpc" in paths, f"Leader 上没有那条被审计的 /rpc：{sorted(paths)}"
