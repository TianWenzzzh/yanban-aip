"""通用 MCP 上游冒烟探针（生态矩阵 M5 / 基准 M6 共用的验证工具）。

用法：
    # stdio 上游
    uv run python bench/smoke_mcp_server.py --ns fetch -- uvx mcp-server-fetch
    # streamable-http 上游
    uv run python bench/smoke_mcp_server.py --ns httpdemo --http http://127.0.0.1:8022/mcp
    # 可选工具调用（冒烟加强）
    uv run python bench/smoke_mcp_server.py --ns time --call get_current_time \
        --args '{"timezone": "Asia/Shanghai"}' -- uvx mcp-server-time

输出单行 JSON 摘要（ok/tools/tool_call），退出码 0=成功 1=失败——供矩阵表格直接引用。
注意：stdio 的 anyio 上下文必须由单一任务持有（DESIGN.md 坑位 #2），本脚本 asyncio.run 天然满足。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time


async def _smoke_stdio(command: list[str], call_tool: str | None, call_args: dict) -> dict:
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    params = StdioServerParameters(command=command[0], args=command[1:], env=None)
    async with stdio_client(params) as (read, write), ClientSession(read, write) as session:
        await session.initialize()
        listed = await session.list_tools()
        result = {"ok": True, "tools": [{"name": t.name, "description": (t.description or "")[:80]} for t in listed.tools]}
        if call_tool:
            t0 = time.monotonic()
            res = await session.call_tool(call_tool, call_args)
            texts = [c.text for c in (res.content or []) if hasattr(c, "text")]
            result["tool_call"] = {"tool": call_tool, "ms": int((time.monotonic() - t0) * 1000),
                                   "texts": [t[:200] for t in texts], "isError": bool(getattr(res, "isError", getattr(res, "is_error", False)))}
        return result


async def _smoke_http(url: str, call_tool: str | None, call_args: dict) -> dict:
    from mcp import ClientSession
    from mcp.client.streamable_http import streamable_http_client

    async with streamable_http_client(url) as ctx:
        read, write = ctx[0], ctx[1]
        async with ClientSession(read, write) as session:
            await session.initialize()
            listed = await session.list_tools()
            result = {"ok": True, "tools": [{"name": t.name, "description": (t.description or "")[:80]} for t in listed.tools]}
            if call_tool:
                t0 = time.monotonic()
                res = await session.call_tool(call_tool, call_args)
                texts = [c.text for c in (res.content or []) if hasattr(c, "text")]
                result["tool_call"] = {"tool": call_tool, "ms": int((time.monotonic() - t0) * 1000),
                                       "texts": [t[:200] for t in texts], "isError": bool(getattr(res, "isError", getattr(res, "is_error", False)))}
            return result


def main() -> int:
    parser = argparse.ArgumentParser(description="MCP 上游冒烟探针")
    parser.add_argument("--ns", required=True, help="命名空间标签（仅用于输出）")
    parser.add_argument("--http", help="streamable-http 端点 URL（不给则走 stdio）")
    parser.add_argument("--call", help="可选：冒烟加强，实际调用一个工具")
    parser.add_argument("--args", default="{}", help="--call 的参数 JSON")
    parser.add_argument("--timeout", type=float, default=90.0, help="整体硬超时秒数（防挂死）")
    parser.add_argument("command", nargs=argparse.REMAINDER, help="-- 之后为 stdio 启动命令")
    ns = parser.parse_args()

    command = [c for c in ns.command if c != "--"]
    call_args = json.loads(ns.args)
    t0 = time.monotonic()
    try:
        if ns.http:
            coro = _smoke_http(ns.http, ns.call, call_args)
        else:
            if not command:
                print(json.dumps({"ns": ns.ns, "ok": False, "error": "stdio 模式需要 -- 命令"}), flush=True)
                return 1
            coro = _smoke_stdio(command, ns.call, call_args)
        result = asyncio.run(asyncio.wait_for(coro, timeout=ns.timeout))
    except (asyncio.TimeoutError, TimeoutError):
        print(json.dumps({"ns": ns.ns, "ok": False, "error": f"整体超时（>{ns.timeout:g}s）"}), flush=True)
        return 1
    except BaseException as exc:  # noqa: BLE001 — anyio ExceptionGroup 解包取根因
        while isinstance(exc, BaseExceptionGroup) and exc.exceptions:
            exc = exc.exceptions[0]
        print(json.dumps({"ns": ns.ns, "ok": False, "error": f"{type(exc).__name__}: {exc}"}), flush=True)
        return 1
    result["ns"] = ns.ns
    result["total_ms"] = int((time.monotonic() - t0) * 1000)
    print(json.dumps(result, ensure_ascii=False), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
