"""The MCP proxy server: an MCP server towards the client, an MCP client towards each downstream server.

    intentplane-mcp --config intentplane.toml

Only configured tools are exposed: gated tools (the gateway decides) and passthrough tools (read-only, no
decision). A step-up is confirmed by the user through MCP elicitation, in the client's own UI; a client that
cannot elicit gets a refusal. Four eyes cannot be granted from the same client and is refused. Nothing is
ever written to stdout except the protocol (logs go to stderr).
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
from contextlib import AsyncExitStack

from mcp import ClientSession, StdioServerParameters, types
from mcp.client.stdio import stdio_client
from mcp.server.lowlevel import Server
from mcp.server.stdio import stdio_server

from .config import ProxyConfig, load_config
from .gate import GateResult, ToolGate

log = logging.getLogger("intentplane.mcp")
CONFIRM_SCHEMA = {"type": "object", "properties": {"confirm": {"type": "boolean", "title": "Confirm this operation"}},
                  "required": ["confirm"]}


def _refusal(message: str) -> types.CallToolResult:
    return types.CallToolResult(content=[types.TextContent(type="text", text=f"Blocked by intentplane: {message}")],
                                is_error=True)


async def _confirm(ctx, key: str, args: dict) -> bool:
    """Ask the user, in the client, to confirm the exact operation. Any failure is a no."""
    text = f"intentplane: confirm {key} with\n{json.dumps(args, indent=1, ensure_ascii=False, default=str)}"
    try:
        res = await ctx.session.elicit_form(text, CONFIRM_SCHEMA, related_request_id=ctx.request_id)
    except Exception as e:  # noqa: BLE001  (client without elicitation, timeout, ...): fail closed
        log.warning("confirmation unavailable: %s", e)
        return False
    return res.action == "accept" and bool((res.content or {}).get("confirm"))


async def serve(cfg: ProxyConfig) -> None:
    gate = ToolGate(cfg)
    loop = asyncio.get_running_loop()
    async with AsyncExitStack() as stack:
        sessions: dict[str, ClientSession] = {}
        exposed: dict[str, tuple[str, str]] = {}   # exposed name -> (server, tool)
        tools: list[types.Tool] = []
        for s in cfg.servers:
            params = StdioServerParameters(command=s.command, args=list(s.args), env=s.env, cwd=str(cfg.base))
            read, write = await stack.enter_async_context(stdio_client(params))
            session = await stack.enter_async_context(ClientSession(read, write))
            await session.initialize()
            sessions[s.name] = session
            for t in (await session.list_tools()).tools:
                key = f"{s.name}.{t.name}"
                kind = gate.classify(key)
                if kind == "denied":
                    continue   # not configured: not even shown to the agent
                name = t.name if t.name not in exposed else f"{s.name}__{t.name}"
                exposed[name] = (s.name, t.name)
                note = "guarded by intentplane" if kind == "gated" else "read-only, passed through by intentplane"
                tools.append(t.model_copy(update={"name": name, "description": f"{t.description or ''} [{note}]".strip()}))
        log.info("exposing %d tools from %d servers", len(tools), len(sessions))

        async def on_list_tools(ctx, params) -> types.ListToolsResult:
            return types.ListToolsResult(tools=tools)

        async def on_call_tool(ctx, params) -> types.CallToolResult:
            if params.name not in exposed:
                return _refusal(f"unknown tool {params.name!r}")
            server, tool = exposed[params.name]
            key, args = f"{server}.{tool}", dict(params.arguments or {})
            if gate.classify(key) == "passthrough":
                return await sessions[server].call_tool(tool, args)

            def call(spec, call_args):   # runs in the gateway's thread; only the gateway reaches it
                return asyncio.run_coroutine_threadsafe(sessions[server].call_tool(tool, call_args), loop).result()

            r: GateResult = await asyncio.to_thread(gate.decide, key, args, call)
            if r.status == "pending" and r.effect == "step_up":
                if await _confirm(ctx, key, args):
                    grant = gate.approve(r.op_hash)
                    r = await asyncio.to_thread(gate.decide, key, args, call, (grant,))
                else:
                    r = GateResult("denied", "the user did not confirm the operation")
            if r.status == "executed":
                return r.result
            return _refusal(r.message)

        server = Server("intentplane", version="0.5.1",
                        instructions="Tools are guarded by intentplane: refused calls return an explanation.",
                        on_list_tools=on_list_tools, on_call_tool=on_call_tool)
        async with stdio_server() as (read, write):
            await server.run(read, write, server.create_initialization_options())


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(prog="intentplane-mcp", description=__doc__.splitlines()[0])
    ap.add_argument("--config", required=True, help="intentplane proxy configuration (TOML)")
    args = ap.parse_args(argv)
    logging.basicConfig(stream=sys.stderr, level=logging.INFO, format="%(name)s: %(message)s")
    asyncio.run(serve(load_config(args.config)))


if __name__ == "__main__":
    main()
