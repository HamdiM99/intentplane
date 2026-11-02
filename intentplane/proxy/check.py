"""Check a proxy configuration before an agent uses it.

    intentplane-mcp --config intentplane.toml --check [--cases cases.json] [--no-servers]

1. Every gated tool exists on its server and every declared parameter is a real argument of it (a typo would leave
   the real argument unchecked; the proxy refuses to expose such a tool).
2. Each case of the test plan gets the expected decision. cases.json:
   [{"tool": "bank.send_money", "args": {...}, "expect": "executed" | "step_up" | "four_eyes" | "denied",
     "note": "optional"}]
3. For every case expected to go through, an attack is added for each critical argument: that argument replaced by
   a value from no trusted source must be denied.

The downstream tools are never called: the gateway decides each case with a stand-in executor, on a fresh gateway
(a fresh session budget), and writes no receipts.
"""
from __future__ import annotations

import asyncio
import json
from dataclasses import replace
from pathlib import Path

from .config import ProxyConfig
from .gate import ToolGate, schema_problems

UNTRUSTED = "intentplane-check-untrusted-value"


async def _schemas(cfg: ProxyConfig) -> dict:
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    found = {}
    for s in cfg.servers:
        params = StdioServerParameters(command=s.command, args=list(s.args), env=s.env, cwd=str(cfg.base))
        async with stdio_client(params) as (read, write), ClientSession(read, write) as session:
            await session.initialize()
            for t in (await session.list_tools()).tools:
                found[f"{s.name}.{t.name}"] = t.input_schema
    return found


def schema_report(cfg: ProxyConfig, schemas: dict) -> tuple[list[str], list[str]]:
    """(problems, tools hidden from the agent because they are not declared)."""
    problems = []
    for key, spec in cfg.tools.items():
        problems += [f"{key}: no such tool on the server"] if key not in schemas else \
            schema_problems(spec, schemas[key])
    hidden = sorted(k for k in schemas if k not in cfg.tools and k not in cfg.passthrough)
    return problems, hidden


def outcome(cfg: ProxyConfig, tool: str, args: dict) -> tuple[str, str]:
    r = ToolGate(cfg).decide(tool, args, lambda spec, call_args: "stand-in: not executed")
    return (r.effect if r.status == "pending" else r.status), r.message


def attacks(cfg: ProxyConfig, case: dict) -> list[dict]:
    spec = cfg.tools.get(case["tool"])
    if spec is None or case["expect"] == "denied":
        return []
    out = []
    for p in spec.params:
        if not p.critical or p.name not in case["args"]:
            continue
        value = case["args"][p.name]
        bad = [*value[:-1], UNTRUSTED] if p.each and isinstance(value, list) and value else UNTRUSTED
        out.append({"tool": case["tool"], "args": {**case["args"], p.name: bad}, "expect": "denied",
                    "note": f"attack: {p.name} from no trusted source"})
    return out


def run(cfg: ProxyConfig, cases: Path | None = None, *, servers: bool = True, out=print) -> int:
    """Print the report; return the number of failed checks."""
    cfg = replace(cfg, receipts=None)
    failures = 0
    if servers:
        problems, hidden = schema_report(cfg, asyncio.run(_schemas(cfg)))
        for problem in problems:
            out(f"FAIL  schema  {problem}")
        failures += len(problems)
        if hidden:
            out(f"info  hidden from the agent (not declared): {', '.join(hidden)}")
    plan = json.loads(cases.read_text(encoding="utf-8")) if cases else []
    for case in [*plan, *(a for c in plan for a in attacks(cfg, c))]:
        got, message = outcome(cfg, case["tool"], case["args"])
        ok = got == case["expect"]
        failures += not ok
        label = case.get("note") or json.dumps(case["args"], ensure_ascii=False)[:80]
        out(f"{'ok  ' if ok else 'FAIL'}  {case['tool']:<24} expect {case['expect']:<9} got {got:<9} {label}"
            + ("" if ok else f"\n      {message}"))
    out(f"\n{'all checks passed' if not failures else f'{failures} check(s) failed'}")
    return failures
