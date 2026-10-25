"""认知层的命名工具经**被审计的**入口被外部调用：Leader 上的 `POST /rpc`。

🔴 为什么要有这一段：`companion/api/tools.py` 那张表过去只是一份**清单**——轮 197 实测，
`/companion/*` 是裸路由（`companion/api/router.py:20`、`leader.py` 直接 include_router），
`companion/` 全目录不写任何 access 日志，所以 SPEC 里任何一颗工具都没有一条"被调用数得出来"的
入站路径。§6 那条审计线只覆盖伙伴容器的 `/rpc`。这里补的就是缺的那一小段派发层：
工具名 → 端点函数，就地调用，诚实性闸门（`mentor.violations`、`report.clean_report_with_ledger`）
都还在端点函数里面，**没有绕开任何一道**。

入口只收 `{"tool": "<名字>", "args": {...}}`；可派发集合来自 `tools.dispatchable()`（GET、非
internal、返回 JSON 面），不在这里抄第二份名单。参数对不上、名字不认识、写类工具——一律 Failed
并把原因原样回给调用方，不降级成"看起来成功"。
"""
from __future__ import annotations

import inspect
import json
from collections.abc import Callable, Iterable
from pathlib import Path
from typing import Any

from acps_sdk.aip import Product, TaskCommand, TaskResult, TaskState, TextDataItem
from acps_sdk.aip.aip_rpc_server import CommandHandlers, DefaultHandlers, TaskManager
from acps_sdk.amp import AccessEmitter
from fastapi import FastAPI

from companion.api import tools
from partner_base import add_audited_rpc_router

SKILL = "yanban.cognition.tool-call"


class ToolCallError(Exception):
    """调用方给的东西不合法；文本就是回给它的理由。"""


def _text_of(command: TaskCommand) -> str:
    for item in command.dataItems or []:
        if isinstance(item, TextDataItem):
            return item.text
    return ""


def parse_call(text: str) -> tuple[str, dict[str, Any]]:
    try:
        body = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ToolCallError(
            f'这条入口只收 JSON：{{"tool": "<工具名>", "args": {{}}}}（解析失败：{exc}）') from exc
    if not isinstance(body, dict) or not isinstance(body.get("tool"), str):
        raise ToolCallError('缺 tool 字段（要字符串）')
    args = body.get("args", {})
    if not isinstance(args, dict):
        raise ToolCallError("args 要是一个对象")
    return body["tool"].strip(), args


async def call_tool(name: str, args: dict[str, Any], table: dict[str, Callable[..., Any]]) -> Any:
    fn = table.get(name)
    if fn is None:
        known = "、".join(sorted(table))
        raise ToolCallError(
            f"工具 {name!r} 不在这条入口的可派发集合里（只放 GET 且返回 JSON 的那些；"
            f"写类工具需要身份层，未开放）。可用：{known}")
    try:
        bound = inspect.signature(fn).bind(**args)
    except TypeError as exc:
        raise ToolCallError(f"工具 {name} 的参数对不上：{exc}") from exc
    out = fn(*bound.args, **bound.kwargs)
    if inspect.isawaitable(out):
        out = await out
    try:
        json.dumps(out, ensure_ascii=False)
    except TypeError as exc:
        raise ToolCallError(f"工具 {name} 的返回值不是 JSON 面：{exc}") from exc
    return out


def install(app: FastAPI, *, routes: Iterable[Any], aic: str, name: str, log_dir: Path,
            emitter: AccessEmitter | None = None,
            # 默认与 `partner_base.add_audited_rpc_router` 对齐（True＝fail-closed）：
            # 过去这里是 False，漏传参数就等于在"唯一一条被审计的入口"上静默关掉身份绑定，
            # 今天没被坑只因 leader.py 显式传了 `_identity_binding()`。两边各有一道用例钉住这颗默认值。
            identity_binding_enabled: bool = True) -> dict[str, Callable[..., Any]]:
    table = tools.dispatchable(routes)
    if not table:
        raise LookupError("可派发工具集合是空的 —— 认知层路由没挂上或那张表漂了，不在空集合上开这条入口")
    em = emitter or AccessEmitter(Path(log_dir) / f"amp_access_{name}.jsonl", aic=aic)

    async def _run(command: TaskCommand, task: TaskResult | None) -> TaskResult:
        tid = task.taskId if task else None
        if tid is None:
            created = TaskManager.create_task(command, initial_state=TaskState.Working)
            tid = created.taskId
        try:
            tool_name, args = parse_call(_text_of(command))
            result = await call_tool(tool_name, args, table)
            payload = json.dumps({"tool": tool_name, "result": result}, ensure_ascii=False)
            TaskManager.set_products(tid, [Product(id=f"{SKILL}-{tid[:8]}", name=SKILL,
                                                   dataItems=[TextDataItem(text=payload)])])
            return TaskManager.update_task_status(tid, TaskState.AwaitingCompletion,
                                                  data_items=[TextDataItem(text=payload)])
        except ToolCallError as exc:
            return TaskManager.update_task_status(tid, TaskState.Failed,
                                                  data_items=[TextDataItem(text=str(exc))])

    async def on_start(command: TaskCommand, task: TaskResult | None) -> TaskResult:
        if task is not None:
            return task
        return await _run(command, None)

    async def on_continue(command: TaskCommand, task: TaskResult) -> TaskResult:
        return await _run(command, task)

    add_audited_rpc_router(
        app, "/rpc",
        CommandHandlers(on_start=on_start, on_continue=on_continue,
                        on_get=DefaultHandlers.get, on_cancel=DefaultHandlers.cancel,
                        on_complete=DefaultHandlers.complete),
        aic=aic, name=name, emitter=em, identity_binding_enabled=identity_binding_enabled)
    return table
