"""研伴 E2E 测试（pytest）。

前置：各服务在线（run_all_partners.py + leader.py + 网关）。服务不在线时自动 SKIP。
运行：GLM_API_KEY=... uv run pytest tests/ -v

v4 起本模块整体标记为 live（依赖在役栈+真实 GLM/arXiv，天然波动）：
默认套件跳过（uv run pytest -q），全量在线验证用 uv run pytest -m live。
"""

import asyncio
import os
import sys
import uuid
from pathlib import Path

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # T4：本地模块（aip_slow）可导入
from acps_sdk.aip import AipRpcClient, TaskState, TextDataItem

pytestmark = [pytest.mark.live]

LEADER = "http://localhost:8041"
PARTNERS = {"literature": 8031, "experiment": 8032, "analysis": 8033, "writing": 8034, "review": 8035, "gateway": 8021}


async def _online(port: int) -> bool:
    try:
        async with httpx.AsyncClient(timeout=3) as c:
            return (await c.get(f"http://127.0.0.1:{port}/health")).status_code == 200
    except Exception:
        return False


def _need(*names: str):
    def dep():
        missing = [n for n in names if not asyncio.get_event_loop().run_until_complete(_online(PARTNERS[n]))]
        if missing:
            pytest.skip(f"服务不在线: {missing}")
    return dep


async def _call_partner(port: int, text: str) -> tuple[str, str]:
    # T4 加固：SDK 内部 httpx timeout=30s 硬编码，arXiv/GLM 长生成常超 → 慢 transport（aip_slow，300s）
    from aip_slow import make_slow_client
    sid, tid = f"s-{uuid.uuid4().hex[:8]}", f"t-{uuid.uuid4().hex[:8]}"
    c = make_slow_client(f"http://localhost:{port}/rpc", "test-leader")
    try:
        t = await c.start_task(session_id=sid, task_id=tid, user_input=text)
        for _ in range(60):
            if t.status.state not in (TaskState.Accepted, TaskState.Working):
                break
            await asyncio.sleep(2)
            t = await c.get_task(task_id=tid, session_id=sid)
        if t.status.state == TaskState.AwaitingCompletion:
            out = ""
            for p in t.products or []:
                for i in p.dataItems or []:
                    if isinstance(i, TextDataItem):
                        out += i.text
            t = await c.complete_task(task_id=tid, session_id=sid)
            return str(t.status.state), out
        extra = ""
        for i in (t.status.dataItems if t.status else None) or []:
            if isinstance(i, TextDataItem):
                extra += i.text
        return str(t.status.state), extra
    finally:
        await c.close()


@pytest.mark.asyncio
async def test_literature_real_search():
    if not await _online(8031):
        pytest.skip("literature 不在线")
    # T4 加固：arXiv 外部抖动 → 整体重试 1 次
    last = ""
    for attempt in range(2):
        state, out = await _call_partner(8031, "检索 knowledge graph embedding 文献 2篇")
        last = out
        if state == "TaskState.Completed" and "arXiv" in out:
            return
    assert state == "TaskState.Completed", f"两次尝试终态 {state}: {last[:100]}"
    assert "arXiv" in last and "作者" in last


@pytest.mark.asyncio
async def test_analysis_stats_match_hand_computed():
    if not await _online(8033):
        pytest.skip("analysis 不在线")
    state, out = await _call_partner(8033, "分析\n```csv\nx,y\n1,2\n2,4\n3,6\n4,8\n5,10\n```")
    assert state == "TaskState.Completed"
    assert '"mean": 3.0' in out and '"stdev": 1.5811' in out and '"x~y": 1.0' in out


@pytest.mark.asyncio
async def test_review_awaiting_input_then_continue():
    if not await _online(8035):
        pytest.skip("review 不在线")
    sid, tid = f"s-{uuid.uuid4().hex[:8]}", f"t-{uuid.uuid4().hex[:8]}"
    from aip_slow import make_slow_client
    c = make_slow_client("http://localhost:8035/rpc", "test-leader")
    try:
        t = await c.start_task(session_id=sid, task_id=tid, user_input="短草稿")
        assert t.status.state == TaskState.AwaitingInput
        t = await c.continue_task(task_id=tid, session_id=sid, user_input="完整草稿：" * 10)
        for _ in range(90):  # T4 加固：轮询至终态而非直接断言（生成慢）
            if t.status.state not in (TaskState.Accepted, TaskState.Working):
                break
            await asyncio.sleep(2)
            t = await c.get_task(task_id=tid, session_id=sid)
        assert t.status.state == TaskState.AwaitingCompletion
        t = await c.complete_task(task_id=tid, session_id=sid)
        assert t.status.state == TaskState.Completed
    finally:
        await c.close()


@pytest.mark.asyncio
async def test_gateway_unknown_tool_rejected():
    if not await _online(8021):
        pytest.skip("gateway 不在线")
    state, out = await _call_partner(8021, '{"tool": "no__such", "arguments": {}}')
    assert state == "TaskState.Rejected" and "可用" in out


@pytest.mark.asyncio
async def test_leader_full_chain():
    if not await _online(8041):
        pytest.skip("leader 不在线")
    async with httpx.AsyncClient(timeout=15) as c:
        r = (await c.post(
            f"{LEADER}/task",
            json={"query": "完成两项独立工作：第一，检索 multi-agent 文献 2篇；第二，就『多智能体协议标准化』写一段相关工作。"},
        )).json()
    task_id = r["task_id"]
    for _ in range(90):
        await asyncio.sleep(3)
        async with httpx.AsyncClient(timeout=15) as c:
            d = (await c.get(f"{LEADER}/task/{task_id}")).json()
        if d["status"] != "running":
            break
    assert d["status"] == "completed", d.get("error")
    completed = [s for s in d["subresults"] if "Completed" in s["state"]]
    # 机制级断言：至少一个子任务真正完成 + 聚合报告实质产出。
    # 分解的子任务数受 LLM 非确定性影响（已带二次拆分重试），不作硬性断言。
    assert len(completed) >= 1
    assert len(d["final_report"]) > 300
