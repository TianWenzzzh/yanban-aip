"""计数面闸门（轮 306）：别人无论从哪条路调我们，都必须**要么落账、要么被写明不落账**。

起因（现读，不是推断）：`输出/_logs/r306_call_count_chain_pre.log` 里那条 C4——线上账本
`amp_access_*.jsonl` 出现过的 route 值只有 `/rpc` 与 `/`，而在册那颗智能体其实还开着
`POST /group/rpc`（群组邀约）与群组 MQ 任务指令两条入站路。这两条在修之前一条都不落账
⇒ 平台若真走群组方式调用我们，"被调用次数"读出来是 0，而且没有任何东西会为此变红。
这道门的作用就是把"新开一条入站路却没人决定它算不算被调用"变成一次红。

口径边界见 `counting_surface.py` 的模块说明：健康探针、取卡、自家工作台 UI 的取数
**故意不计**进"被调用次数"（那会把访客点击读成受欢迎程度）。
"""
from __future__ import annotations

import importlib
import importlib.util
import json
import pathlib
import sys
import uuid

import pytest
from fastapi.testclient import TestClient

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import counting_surface  # noqa: E402
import partner_base  # noqa: E402

CALLEE_AIC = "1.2.156.3088.1.1.PTN01.PTN01.1.0001"
CALLER_AIC = "1.2.156.3088.1.1.LDR001.LDR001.1.0002"
OTHER_TEAM_AIC = "1.2.156.3088.1.BUPT.9999XXXX.AAAA1111.2222"


async def _echo_skill(text: str) -> str:
    return f"echo:{text[:40]}"


def _body(sender: str, command: str = "start", with_task_id: bool = True) -> dict:
    cmd = {"type": "task-command", "id": "c1",
           "sentAt": "2026-10-06T12:00:00Z", "senderRole": "leader",
           "senderId": sender, "command": command,
           "dataItems": [{"id": "d1", "type": "text", "text": "查一下多智能体协作"}]}
    if with_task_id:
        cmd["taskId"] = "task-" + str(uuid.uuid4())
    return {"jsonrpc": "2.0", "id": "r1", "method": "rpc", "params": {"command": cmd}}


def _records(path: pathlib.Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines() if x.strip()]


@pytest.fixture()
def partner_client(tmp_path, monkeypatch):
    monkeypatch.setattr(partner_base, "AMP_LOG_DIR", tmp_path)
    app = partner_base.create_partner_app(name="ptn", aic=CALLEE_AIC, port=8099, run_skill=_echo_skill)
    return TestClient(app), tmp_path / "amp_access_ptn.jsonl"


def _app_routes(node, prefix: str = "") -> set[tuple[str, str]]:
    """入站路由清单。

    🔴 必须递归：这版 FastAPI 的 `include_router` 不再把子路由摊平进 `app.routes`，
    而是塞一颗 `_IncludedRouter`（真身在 `.original_router.routes`）。第一版没递归时
    leader 的 55 条 `/companion/*` 整批从对账里消失——**那道"每条入站路都要有归属"的门
    会在一份残缺清单上假绿**。静态 Mount 的 path 是空串，归一成 "/"。
    """
    got: set[tuple[str, str]] = set()
    for r in getattr(node, "routes", []) or []:
        inner = getattr(r, "original_router", None)      # _IncludedRouter
        if inner is not None:
            got |= _app_routes(inner, prefix)
            continue
        p = getattr(r, "path", None)
        if p is None:
            continue
        p = (prefix + (p or "/")) or "/"
        if getattr(r, "routes", None):                   # Mount / Host：继续往下钻
            got |= _app_routes(r, p if p.endswith("/") else p + "/")
            continue
        for m in sorted(getattr(r, "methods", None) or {"GET"}):
            got.add((m, p))
    return got


def _covered(service: str, method: str, path: str) -> bool:
    counted = partner_base.COUNTED_ROUTES.get(service, set())
    if path in counted:
        return True
    if path in counting_surface.EXACT:
        return True
    return counting_surface.covers_prefix(path)


def _load_lit(tmp_path, monkeypatch):
    """独立模块名加载（`tests2/test_partner_topic_channel.py` 立过的规矩：
    不污染 sys.modules 里那份带着旧 AMP_LOG_DIR 的 literature_partner）。"""
    monkeypatch.setenv("AMP_LOG_DIR", str(tmp_path))
    monkeypatch.setenv("LLM_MOCK", "1")
    path = ROOT / "literature_partner.py"
    spec = importlib.util.spec_from_file_location("lp_under_test_r306", str(path))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ── 结构性对账：每条入站路都要有归属
@pytest.mark.parametrize("service,loader", [
    ("yanban-leader", lambda: importlib.import_module("leader").app),
    ("yanban-literature", None),          # 见下面的用例，要带 tmp env
])
def test_every_inbound_route_is_either_counted_or_declared(service, loader, tmp_path, monkeypatch):
    if loader is None:
        app = _load_lit(tmp_path, monkeypatch).app
    else:
        app = loader()
    counted_seen = set()
    uncovered = []
    for method, path in sorted(_app_routes(app)):
        if path in partner_base.COUNTED_ROUTES.get(service, set()):
            counted_seen.add(path)
        elif not _covered(service, method, path):
            uncovered.append(f"{method} {path}")
    assert not uncovered, (
        f"{service} 有入站路由既没接计数也没在 counting_surface 申报 ⇒ {uncovered}。"
        "新功能开了一条别人能打进来的路时，必须当场决定它算不算「被调用」。")
    # 计数面不许是空的：空集意味着这条对账在任何 app 上都没被测到（假绿通道）
    assert counted_seen, f"{service} 一条被计数的入站路由都没有 ⇒ 这道门在替空集合背书"


def test_cognitive_layer_is_actually_attached():
    """上面那条在 leader 上跑之前要先确认 /companion/* 真的挂上了。
    `leader.py` 对 companion 是 try/except 降级导入，没挂上时路由表会小一圈
    ⇒ 对账会在一份不完整的清单上假绿。"""
    leader = importlib.import_module("leader")
    assert getattr(leader, "_HAS_COMPANION", False) is True, "companion 层没挂上，路由面读数无效"
    paths = {p for _, p in _app_routes(leader.app)}
    assert any(p.startswith("/companion/") for p in paths), paths


def test_no_agent_app_is_on_the_bare_sdk_router():
    """SDK 的 `add_aip_rpc_router` 函数体里没有任何 emit ⇒ 谁走它，被调用就数不出来。
    产品代码里只许出现在"我们不用它"的注释与 import 里。"""
    offenders = []
    for f in sorted(ROOT.glob("*.py")):
        src = f.read_text(encoding="utf-8", errors="replace")
        for ln, line in enumerate(src.splitlines(), 1):
            code = line.split("#", 1)[0]
            if "add_aip_rpc_router(" in code and "import" not in code:
                offenders.append(f"{f.name}:{ln}")
    assert offenders == [], f"这些位置装了不会被计数的裸路由：{offenders}"


# ── 行为对账：装了计数的通道真的会落账
def test_group_invite_writes_one_record_naming_the_inviter(tmp_path, monkeypatch):
    lit = _load_lit(tmp_path, monkeypatch)
    log = tmp_path / f"amp_access_{lit.PARTNER_NAME}.jsonl"

    class _StubClient:
        def __init__(self, partner_aic):
            self.partner_aic = partner_aic

        def set_command_handler(self, fn):
            pass

        def set_task_result_handler(self, fn):
            pass

        async def join_group(self, request):
            from acps_sdk.aip import RabbitMQResponse
            from acps_sdk.aip.aip_group_model import RabbitMQResponseResult
            return RabbitMQResponse(id=request.id,
                                    result=RabbitMQResponseResult(connectionName="c", vhost="/",
                                                                   nodeName="n", queueName="q"))

    monkeypatch.setattr(lit, "GroupPartnerMqClient", _StubClient)
    invite = {"jsonrpc": "2.0", "id": "g1", "method": "group", "params": {
        "protocol": "amqp",
        "group": {"groupId": "grp-1", "leader": {"aic": OTHER_TEAM_AIC}, "partners": [{"aic": lit.PARTNER_AIC}]},
        "server": {"host": "wt.ioa.pub", "port": 5671, "vhost": "/acps"},
        "amqp": {"exchange": "x", "exchangeType": "direct", "routingKey": "k"}}}
    r = TestClient(lit.app).post("/group/rpc", json=invite)
    assert r.status_code == 200, r.text
    recs = _records(log)
    assert len(recs) == 1, f"群组邀约一条都没落账＝这条被调用路数不出来，实得 {len(recs)}"
    b = recs[0]["body"]
    assert b["request"]["route"] == "/group/rpc", b["request"]
    assert b["caller"]["aic"] == OTHER_TEAM_AIC, "邀约方身份没取到，答不出「被谁拉进群组」"


def test_group_invite_failure_still_answers_and_still_counts(tmp_path, monkeypatch):
    """连不上平台 MQ 时：调用方要拿到可解析的回答（不是崩栈的 500），账也要落。"""
    lit = _load_lit(tmp_path, monkeypatch)
    log = tmp_path / f"amp_access_{lit.PARTNER_NAME}.jsonl"

    class _BoomClient:
        def __init__(self, partner_aic):
            pass

        def set_command_handler(self, fn):
            pass

        def set_task_result_handler(self, fn):
            pass

        async def join_group(self, request):
            raise RuntimeError("MQ 连不上")

    monkeypatch.setattr(lit, "GroupPartnerMqClient", _BoomClient)
    invite = {"jsonrpc": "2.0", "id": "g2", "method": "group", "params": {
        "protocol": "amqp",
        "group": {"groupId": "grp-2", "leader": {"aic": OTHER_TEAM_AIC}, "partners": []},
        "server": {"host": "h", "port": 1, "vhost": "/"},
        "amqp": {"exchange": "x", "exchangeType": "direct", "routingKey": "k"}}}
    r = TestClient(lit.app).post("/group/rpc", json=invite)
    assert r.status_code == 200, f"群组邀约失败时不许崩栈：{r.text[:160]}"
    assert (r.json().get("error") or {}).get("code") == -32603, r.json()
    recs = _records(log)
    assert len(recs) == 1 and recs[0]["body"]["response"]["statusCode"] == 500, recs


def test_mq_task_command_writes_one_record(tmp_path, monkeypatch):
    """群组队列里下发的任务指令＝别人让我们干了一次活，必须数得出来。"""
    import asyncio
    lit = _load_lit(tmp_path, monkeypatch)
    log = tmp_path / f"amp_access_{lit.PARTNER_NAME}.jsonl"
    from acps_sdk.aip import TaskCommand, TaskCommandType

    cmd = TaskCommand(id="cmd-1", sentAt="2026-10-06T12:00:00Z", senderRole="leader",
                      senderId=OTHER_TEAM_AIC, command=TaskCommandType.Start,
                      groupId=None, taskId=None, sessionId=None)   # 早退分支：没进业务也算一次被调用
    asyncio.run(lit._on_group_task(cmd, False))
    recs = _records(log)
    assert len(recs) == 1, f"MQ 指令一条都没落账，实得 {len(recs)}"
    b = recs[0]["body"]
    assert b["request"]["route"] == "mq:group" and b["caller"]["aic"] == OTHER_TEAM_AIC, b


def test_start_without_task_id_answers_and_counts_instead_of_crashing(partner_client):
    """轮 306 外部实测 C6：缺 taskId 的 start 让 SDK 的兜底再抛一次 ⇒ 调用方收到 HTTP 500 崩栈。"""
    client, log = partner_client
    r = client.post("/rpc", json=_body(CALLER_AIC, with_task_id=False))
    assert r.status_code == 200, r.text
    res = r.json().get("result") or {}
    assert res.get("taskId"), f"没带 taskId 时服务端要补一颗并回给调用方：{r.json()}"
    assert len(_records(log)) == 1


def test_internal_failure_returns_jsonrpc_error_not_a_crash(partner_client, monkeypatch):
    """任何内部异常都换成 JSON-RPC 错误体：调用方永远拿得到可解析的回答，账照落。"""
    client, log = partner_client

    async def boom(*a, **kw):
        raise RuntimeError("通道炸了")

    monkeypatch.setattr(partner_base, "handle_rpc_request", boom)
    r = client.post("/rpc", json=_body(CALLER_AIC))
    assert r.status_code == 200, f"崩栈会把手搓的调用方挡在门外：{r.text[:160]}"
    assert (r.json().get("error") or {}).get("code") == -32603, r.json()
    recs = _records(log)
    assert len(recs) == 1 and recs[0]["body"]["response"]["statusCode"] == 500, recs
    assert recs[0]["body"]["caller"]["aic"] == CALLER_AIC


def test_counting_landing_dir_stays_on_a_shared_volume():
    """账本落在 `shared-logs` 卷上才经得起重建容器；compose 里谁掉了这条挂载，计数就会
    在一次重建之后归零而没人知道（轮 299 的 /app/data 同型事故）。"""
    import re
    text = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    # 只取 `services:` 那一段：锚点块（x-partner-build 里的 `  args:`）也是两格缩进，
    # 整本扫会把 YAML 锚点当成服务名（第一版就这么假红过一次）。
    seg = re.search(r"^services:\n((?:.*\n)*?)^\w", text, re.M)
    assert seg, "compose 解析不出 services 段，这条对账无效"
    services = re.findall(r"^  (\w[\w-]*):\n((?:^    .*\n?)+)", seg.group(1), re.M)
    assert len(services) >= 6, f"services 段只解析出 {len(services)} 颗，清单不完整⇒ 对账无效"
    missing = [name for name, body in services
               if "shared-logs:/app/logs" not in body.replace(" ", "")]
    assert missing == [], f"这些服务没把账本目录挂在共享卷上（重建即丢计数）：{missing}"
