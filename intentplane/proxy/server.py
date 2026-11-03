"""The MCP proxy server: an MCP server towards the client, an MCP client towards each downstream server.

    intentplane-mcp --config intentplane.toml

Only configured tools are exposed: gated tools (the gateway decides) and passthrough tools (read-only, no
decision). A step-up is confirmed by the user through MCP elicitation, in the client's own UI. When the client
cannot elicit, the user can confirm it on a local approval page ([approvals]). Four eyes can never be granted
from the agent's own client: it needs an approval channel that serves it, otherwise it is refused. Nothing is ever written to
stdout except the protocol (logs go to stderr).
"""
from __future__ import annotations

import argparse
import asyncio
import importlib
import json
import logging
import sys
from contextlib import AsyncExitStack

from mcp import ClientSession, StdioServerParameters, types
from mcp.client.stdio import stdio_client
from mcp.server.lowlevel import Server
from mcp.server.stdio import stdio_server

from intentplane.approvals import ApprovalChannel, LocalApprovals
from intentplane.core import Ledger

from .config import ProxyConfig, load_config
from .gate import FileLedger, GateResult, ToolGate

log = logging.getLogger("intentplane.mcp")
GATED_NOTE = ("Protected by intentplane: the critical arguments must match trusted data (the user's mandate or "
              "records), otherwise the call is refused with the reason; some operations ask the user to confirm first.")
PASSTHROUGH_NOTE = "Read-only: intentplane passes this call through without a decision."
CONFIRM_SCHEMA = {"type": "object", "properties": {"confirm": {"type": "boolean", "title": "Confirm this operation"}},
                  "required": ["confirm"]}


def _refusal(message: str) -> types.CallToolResult:
    return types.CallToolResult(content=[types.TextContent(type="text", text=f"Blocked by intentplane: {message}")],
                                is_error=True)


NOT_CONFIRMED = "the user did not confirm the operation; nothing was executed"
CANNOT_CONFIRM = ("this operation needs the user's confirmation, but this MCP client cannot ask for it (it does "
                  "not support MCP elicitation); nothing was executed. The user can confirm it from a client that "
                  "supports elicitation, or on intentplane's local approval page once it is set up ([approvals]).")

FOUR_EYES_NO_APP = ("this operation needs a second person's approval (four eyes), which cannot be given from this "
                    "client, and no approval channel for four eyes is configured; nothing was executed.")


async def _confirm(ctx, key: str, shown: dict) -> str:
    """Ask the user, in the client, to confirm the exact RESOLVED operation: 'confirmed', 'declined', or
    'unavailable' when the client cannot ask (no elicitation). Anything but 'confirmed' executes nothing."""
    caps = getattr(ctx.session, "client_capabilities", None)
    if caps is not None and getattr(caps, "elicitation", None) is None:
        return "unavailable"   # the client said at connection time that it cannot ask the user
    text = f"intentplane: confirm {key} with\n{json.dumps(shown, indent=1, ensure_ascii=False, default=str)}"
    try:
        res = await ctx.session.elicit_form(text, CONFIRM_SCHEMA, related_request_id=ctx.request_id)
    except Exception as e:  # noqa: BLE001  (method not found, timeout, ...): fail closed
        log.warning("confirmation unavailable: %s", e)
        return "unavailable"
    return "confirmed" if res.action == "accept" and bool((res.content or {}).get("confirm")) else "declined"


def _channel(cfg: ProxyConfig, gate: ToolGate) -> ApprovalChannel | None:
    """The configured approval channel: the built-in local page (the user's own step-up), or `channel`."""
    a = cfg.approvals
    if a is None:
        return None
    if a.channel:
        module, _, factory = a.channel.partition(":")
        return getattr(importlib.import_module(module), factory)(cfg, gate)
    return LocalApprovals(gate.approvals, {cfg.principal: a.passcode}, host=a.host, port=a.port,
                          public_url=a.public_url, ttl=a.ttl, secure_cookies=a.secure_cookies,
                          ledger=FileLedger(a.receipts) if a.receipts else Ledger())


async def _via_channel(channel: ApprovalChannel, cfg: ProxyConfig, r: GateResult, decide) -> GateResult:
    """Open (or find) the approval request for this exact operation, wait as configured, and execute it once
    approved. Calling the tool again with the same arguments finds the same request; any other arguments make
    another operation, with its own request: an approval never carries over."""
    req = channel.open(r.op_hash, r.intent, r.resolved, r.effect)
    if req.status == "pending" and cfg.approvals.wait > 0:
        req = await channel.wait(req.id, cfg.approvals.wait)
    if req.status == "approved":
        done = await decide((req.grant,))
        if done.status == "executed":
            channel.mark_executed(req.id)
        return done
    if req.status == "rejected":
        return GateResult("denied", f"{req.approver} rejected this operation; nothing was executed")
    if req.status == "expired":
        return GateResult("denied", "the approval request expired; nothing was executed")
    who = "the user" if r.effect == "step_up" else "a second person (four eyes, not the user)"
    return GateResult("denied", f"waiting for approval by {who}; nothing was executed yet. Approve it at "
                                f"{channel.link(req)} , then call this tool again with the same arguments.")


async def serve(cfg: ProxyConfig) -> None:
    gate = ToolGate(cfg)
    channel = _channel(cfg, gate)
    loop = asyncio.get_running_loop()
    async with AsyncExitStack() as stack:
        if channel is not None:
            await channel.start()
            stack.push_async_callback(channel.stop)
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
                note = GATED_NOTE if kind == "gated" else PASSTHROUGH_NOTE
                description = f"{t.description or ''}\n\n{note}".strip()
                tools.append(t.model_copy(update={"name": name, "description": description}))
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

            def decide(grants=()):
                return asyncio.to_thread(gate.decide, key, args, call, grants)

            r: GateResult = await decide()
            if r.status == "pending":
                answer = "unavailable" if r.effect != "step_up" else await _confirm(ctx, key, r.resolved or args)
                if answer == "confirmed":
                    r = await decide((gate.approve(r.op_hash),))
                elif answer == "declined":
                    r = GateResult("denied", NOT_CONFIRMED)
                elif channel is not None and r.effect in channel.effects:
                    r = await _via_channel(channel, cfg, r, decide)
                else:
                    r = GateResult("denied", CANNOT_CONFIRM if r.effect == "step_up" else FOUR_EYES_NO_APP)
            if r.status == "executed":
                return r.result
            return _refusal(r.message)

        server = Server("intentplane", version="0.8.0",
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
