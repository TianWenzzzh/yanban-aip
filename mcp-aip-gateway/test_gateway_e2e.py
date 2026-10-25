"""经 AIP 协议调用 MCP 工具的验证客户端（PoC 收尾）。"""

import asyncio
import json
import uuid

from acps_sdk.aip import AipRpcClient, TaskState, TextDataItem

GATEWAY_RPC = "http://localhost:8021/rpc"


async def call_tool_via_aip(tool: str, arguments: dict) -> None:
    session_id = f"session-{uuid.uuid4()}"
    task_id = f"task-{uuid.uuid4()}"
    client = AipRpcClient(partner_url=GATEWAY_RPC, leader_id="1.2.156.3088.1.1.LDR001.LDR001.1.0002", identity_binding_enabled=False)
    try:
        task = await client.start_task(
            session_id=session_id,
            task_id=task_id,
            user_input=json.dumps({"tool": tool, "arguments": arguments}, ensure_ascii=False),
        )
        print(f"[{tool}] start -> {task.status.state}")
        while task.status.state in (TaskState.Accepted, TaskState.Working):
            await asyncio.sleep(1)
            task = await client.get_task(task_id=task_id, session_id=session_id)
        if task.status.state == TaskState.AwaitingCompletion:
            for p in task.products or []:
                for item in p.dataItems or []:
                    if isinstance(item, TextDataItem):
                        print(f"[{tool}] MCP 结果: {item.text[:200]}")
            task = await client.complete_task(task_id=task_id, session_id=session_id)
            print(f"[{tool}] complete -> {task.status.state}")
        else:
            for item in task.status.dataItems or []:
                if isinstance(item, TextDataItem):
                    print(f"[{tool}] 失败: {item.text[:150]}")
    finally:
        await client.close()


async def main() -> None:
    await call_tool_via_aip("literature_search", {"topic": "大模型多智能体协作", "limit": 3})
    await call_tool_via_aip("summarize_paper", {"title": "Agent Lab", "abstract": "多智能体系统通过任务分解与协同执行提升科研效率，本文提出一种结构化契约流水线方法。", "max_words": 50})
    await call_tool_via_aip("not_exist_tool", {})


if __name__ == "__main__":
    asyncio.run(main())
