# -*- coding: utf-8 -*-
r"""轮 312 第十一段·分发层的重复与乱序（队长任务书 §5 用例 12「重复请求／重复回调／乱序响应／缺会话字段」）。

以前这一格的证据都停在**函数级**（直接 `await lp.on_start(...)`）。函数级看不见两件事：
①幂等到底由谁给——`acps-sdk/acps_sdk/aip/aip_rpc_server.py:329-331` 只要我方注册了 `on_start`，
SDK 那支"同 taskId 已存在就返回现快照"的默认实现（`:79-86`）**根本不会被走到**，语义全由我方
`if task:` 那一支决定；②`on_continue` 走的是另一条腿（`literature_partner.py:638`），而它此前
**一条测试都没有**（grep 全仓 `on_continue` 在 tests/、tests2/ 里零命中）。
所以这里统一走 `handle_rpc_request` + `partner_base._task_id_tolerant`（生产注册的同一条包装路），
用假 `_search`/假 `_glm_review` 把外呼掐掉：零模型、零网络、零额度。

本轮实测（`输出/_logs/r312_p290_probe_dispatch_replay_r312.log`）量出的缺陷，D2/D3/D4 就是钉它的：
`on_continue` 里 `on_start(command, None)` 会 `TaskManager.create_task`，而 create_task 按 taskId
**原地覆盖** `_tasks[taskId]` ⇒ 原始 `start` 命令从 commandHistory 里消失、resultId 换掉；
任务已办完时补一句寒暄，整份交付（products）会被换成 awaiting-input 的空任务。
"""
from __future__ import annotations

import asyncio
import importlib.util
import pathlib
import sys
import time
import uuid

import pytest

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT))

import sources                                              # noqa: E402
import partner_base                                         # noqa: E402
from acps_sdk.aip import TextDataItem                      # noqa: E402
from acps_sdk.aip.aip_base_model import TaskCommand         # noqa: E402
from acps_sdk.aip.aip_rpc_server import (                   # noqa: E402
    CommandHandlers, TaskManager, handle_rpc_request)


def _load_lp():
    spec = importlib.util.spec_from_file_location("lp_under_test_r312d", str(ROOT / "literature_partner.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


lp = _load_lp()
CALLS: list[dict] = []
LEADER_AIC = "1.2.156.3088.1.1.LDR001.LDR001.1.0002"


async def _search_stub(topic: str, limit: int, year_range=None) -> str:
    CALLS.append({"topic": topic, "limit": limit, "year": tuple(year_range) if year_range else None})
    lo = (year_range or (2021, 2026))[0]
    rows = [{"title": f"{topic} 第{i + 1}篇", "authors": ["张三"], "year": lo + i, "venue": "Crossref",
             "doi": f"10.1000/{abs(hash(topic)) % 10000}x{i}", "citations": i} for i in range(limit)]
    return "\n".join(sources._fmt(rows, "Crossref"))


async def _review_stub(topic: str, purpose: str = "") -> str:
    return f"「{topic}」的综述提纲（桩）"


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    """每用例一份干净现场：SDK 的任务表、伙伴的会话表、调用计数都得归零，
    否则上一条用例留下的 taskId/sessionId 会让这一条的"覆盖"读数失真。"""
    TaskManager._tasks.clear()
    lp._sessions.clear()
    CALLS.clear()
    monkeypatch.setattr(lp, "_search", _search_stub)
    monkeypatch.setattr(lp, "_glm_review", _review_stub)
    yield
    TaskManager._tasks.clear()
    lp._sessions.clear()
    CALLS.clear()


def _handlers(continue_fn=None) -> CommandHandlers:
    """按生产那条路重建：CommandHandlers(...) 再过一遍 `_task_id_tolerant`。
    直接从 `lp.handlers` 拿不行——它是 import 期绑好的函数引用，测试换不了里面的腿。"""
    base = lp.handlers
    h = CommandHandlers(on_start=base.on_start, on_cancel=base.on_cancel, on_get=base.on_get,
                        on_complete=base.on_complete,
                        on_continue=continue_fn if continue_fn is not None else base.on_continue)
    return partner_base._task_id_tolerant(h)


class _Req:
    def __init__(self, payload: dict):
        self._p = payload

    async def json(self):
        return self._p


def _cmd(command: str, text: str | None = None, task_id: str | None = None,
        session_id: str | None = "sess-A") -> TaskCommand:
    kw = {"type": "task-command", "id": "c-" + uuid.uuid4().hex[:8],
          "sentAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
          "senderRole": "leader", "senderId": LEADER_AIC, "command": command}
    if task_id is not None:
        kw["taskId"] = task_id
    if session_id is not None:
        kw["sessionId"] = session_id
    if command in ("start", "continue"):
        kw["dataItems"] = [TextDataItem(text=text or "")]
    return TaskCommand(**kw)


async def _rpc(c: TaskCommand, handlers: CommandHandlers | None = None):
    return await handle_rpc_request(_Req({"jsonrpc": "2.0", "method": "rpc", "id": "r-1",
                                          "params": {"command": c.model_dump()}}),
                                    handlers or _handlers(),
                                    local_aic=None, identity_binding_enabled=False)


def _text(task) -> str:
    return (task.products[0].dataItems[0].text if task.products and task.products[0].dataItems else "")


def _cmds(task) -> list[str]:
    return [c.command.value if hasattr(c.command, "value") else str(c.command)
            for c in (task.commandHistory or [])]


# ── D1：重复请求（网关重发同一颗 Start）──────────────────────────────────────
def test_D1_replayed_start_is_idempotent_and_searches_once():
    async def go():
        tid = "t-dup-1"
        first = await _rpc(_cmd("start", "检索钓鱼网站检测的论文 3 篇", tid))
        n = len(CALLS)
        again = await _rpc(_cmd("start", "检索钓鱼网站检测的论文 3 篇", tid))
        return first, again, n

    first, again, n = asyncio.run(go())
    assert n == 1 and len(CALLS) == 1, f"重发一颗 Start 就多烧一次检索：{CALLS}"
    assert again.result.id == first.result.id, "重发返回的是另一颗任务对象（幂等没做成）"
    assert again.result.status.state == first.result.status.state == "awaiting-completion"
    assert _text(again.result).strip(), "幂等返回的快照里没有已办好的交付"


# ── D2/D3/D4：本轮实测到的缺陷（Continue 把整颗任务换了）───────────────────────
def test_D2_continue_updates_the_existing_task_instead_of_replacing_it():
    async def go():
        tid = "t-cont-2"
        await _rpc(_cmd("start", "检索多智能体协作的论文 2 篇", tid))
        before = TaskManager.get_task(tid)
        r = await _rpc(_cmd("continue", "再多给 3 篇", tid))
        return before, r, TaskManager.get_task(tid)

    before, r, after = asyncio.run(go())
    assert after is before, "Continue 之后 `TaskManager._tasks[taskId]` 换了对象＝原任务被覆盖"
    assert r.result is after, "返回的快照和库里那颗不是同一颗"
    assert _cmds(after) == ["start", "continue"], f"调用历史被重置：{_cmds(after)}"
    assert after.status.state == "awaiting-completion", after.status.state
    assert "第5篇" in _text(after), f"补充句没作用到篇数上：{_text(after)[:120]!r}"


def test_D3_get_after_continue_shows_the_whole_conversation():
    async def go():
        tid = "t-cont-3"
        await _rpc(_cmd("start", "检索图神经网络的论文 2 篇", tid))
        await _rpc(_cmd("continue", "再多给 2 篇", tid))
        return await _rpc(_cmd("get", None, tid))

    got = asyncio.run(go())
    assert got.error is None, got.error
    t = got.result
    # Get 是**只读**：`DefaultHandlers.get`（aip_rpc_server.py:88-112）只按 lastCommandSentAt 过滤，
    # 不往 commandHistory 里追加——所以这里钉的是"补充句之后历史仍然完整"，不是"Get 也留痕"。
    assert _cmds(t) == ["start", "continue"], f"平台 Get 回来的历史不完整：{_cmds(t)}"
    assert len(t.products or []) == 1, "Get 回来没有交付（任务被换成空的那支）"
    assert t.status.state == "awaiting-completion", t.status.state


def test_D4_greeting_continue_keeps_a_delivered_task_deliverable():
    """办完之后补一句寒暄：可以不给新内容，但不许把已经给出去那份清掉。"""
    async def go():
        tid = "t-cont-4"
        done = await _rpc(_cmd("start", "检索知识图谱补全的论文 3 篇", tid))
        n = len(CALLS)
        r = await _rpc(_cmd("continue", "在吗", tid))
        return done, r, n, TaskManager.get_task(tid)

    done, r, n, stored = asyncio.run(go())
    assert len(CALLS) == n, f"寒暄触发了第二次检索：{CALLS[n:]}"
    assert stored is not None and _text(stored).strip(), "已交付的清单被寒暄换掉了（products 清零）"
    assert r.result.status.state == "awaiting-completion", (
        f"寒暄把状态从 awaiting-completion 改成 {r.result.status.state.value}＝交付被撤")
    assert _text(r.result) == _text(done.result), "同一颗任务，补一句寒暄后交付内容变了"


# ── D5/D6：乱序与缺字段（协议层的拒绝形状，不许崩栈）──────────────────────────
@pytest.mark.parametrize("name,command", [("get", "get"), ("complete", "complete"),
                                          ("cancel", "cancel"), ("continue", "continue")])
def test_D5_command_arriving_before_start_gets_a_jsonrpc_error(name, command):
    async def go():
        c = _cmd(command, "再多给 3 篇" if command == "continue" else None, "t-ghost")
        return await _rpc(c)

    r = asyncio.run(go())
    assert r.error is not None and r.error.code == -32001, f"未知 taskId 的 {name} 没走拒绝支：{r}"
    assert r.error.data == {"taskId": "t-ghost"}, r.error.data


def test_D6_non_start_without_task_id_is_rejected_not_executed():
    r = asyncio.run(_rpc(_cmd("get", None, None)))
    assert r.error is not None and r.error.code == -32602, r
    assert not CALLS, "缺 taskId 还去办事了"


def test_D7_start_without_task_id_gets_one_and_completes():
    """手搓调用方的常见形状（轮 306 外部实测）：容错层必须补出 taskId，端点不许 500。"""
    r = asyncio.run(_rpc(_cmd("start", "检索联邦学习隐私保护的论文 2 篇", None)))
    assert r.error is None, r.error
    assert r.result.taskId, "没补出 taskId"
    assert r.result.status.state == "awaiting-completion", r.result.status.state.value
    assert len(CALLS) == 1, CALLS


# ── D8：两会话并发各自补条件（08×09 的交界，走分发层）─────────────────────────
def test_D8_two_sessions_following_up_stay_isolated_through_dispatch():
    async def go():
        await _rpc(_cmd("start", "检索钓鱼网站检测的论文 3 篇", "t-A", "sess-A"))
        await _rpc(_cmd("start", "检索多智能体协作的论文 2 篇", "t-B", "sess-B"))
        n = len(CALLS)
        await asyncio.gather(_rpc(_cmd("continue", "改成近三年的", "t-A", "sess-A")),
                             _rpc(_cmd("continue", "再多给 4 篇", "t-B", "sess-B")))
        return CALLS[n:], TaskManager.get_task("t-A"), TaskManager.get_task("t-B")

    follow, ta, tb = asyncio.run(go())
    a = [c for c in follow if "钓鱼" in c["topic"]]
    b = [c for c in follow if "多智能体" in c["topic"]]
    assert len(follow) == 2, f"两通补充只跑了一次：{follow}"
    assert len(a) == 1 and len(b) == 1, f"补充句串会话了：{follow}"
    assert a[0]["limit"] == 3 and a[0]["year"][1] >= time.gmtime().tm_year - 3, a[0]
    assert b[0]["limit"] == 6 and b[0]["year"] is None, b[0]
    assert _cmds(ta) == ["start", "continue"] and _cmds(tb) == ["start", "continue"], (_cmds(ta), _cmds(tb))
    assert _text(ta) and _text(tb), "并发补充之后至少一侧的交付没了"


# ── I1：植入——把 on_continue 退回"用 on_start(command, None) 造新任务"的形状────
def test_I1_implant_replacing_the_task_breaks_D2_and_D4():
    """关掉仪器：这条腿必须让 D2/D4 那两条断言反过来红，否则说明它们盯的不是"覆盖"这件事。
    这里复刻的就是**本轮实测到的那版行为**（`r312_p290` 的 J2/J6 读数），不是凭空造一个坏版本。"""
    async def buggy_on_continue(command, task):
        TaskManager.add_command_to_history(task.taskId, command)
        raw = lp._input_text(command)
        if (await lp._resolve_turn(raw, command.sessionId))["kind"] == "none":
            return task
        return await lp.on_start(command, None)          # ← 覆盖现场：create_task 按 taskId 原地换

    async def go(tid: str, followup: str):
        done = await _rpc(_cmd("start", "检索程序缺陷检测的论文 3 篇", tid), _handlers(buggy_on_continue))
        r = await _rpc(_cmd("continue", followup, tid), _handlers(buggy_on_continue))
        return done, r, TaskManager.get_task(tid)

    d2, r2, after2 = asyncio.run(go("t-imp-2", "再多给 3 篇"))
    assert after2 is not d2.result, "植入没开火：Continue 之后对象还是原来那颗，D2 就白写了"
    assert "start" not in _cmds(after2), f"植入没咬到历史：{_cmds(after2)}"

    d4, r4, after4 = asyncio.run(go("t-imp-4", "在吗"))
    assert r4.result.status.state != "awaiting-completion" or not _text(after4), (
        "植入没开火：寒暄那支在坏实现下也必须把交付弄丢")
