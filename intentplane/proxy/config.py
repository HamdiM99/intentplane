"""Proxy configuration (TOML). Relative paths are resolved against the config file's directory.

    [mandate]                      # the session mandate, issued by the trusted side when the proxy starts
    principal = "emma"
    budget = 250000                # in the units of each tool's `budget_param` (e.g. cents)
    [mandate.claims]
    payee = ["DE89370400440532013000"]

    [registries.beneficiaries]     # a system of record: {ref: value}, read for the mandate's owner
    file = "beneficiaries.json"
    normalize = "iban"

    [[servers]]                    # downstream MCP servers (stdio)
    name = "bank"
    command = "python"
    args = ["bank_server.py"]

    [[tools]]                      # a tool the gateway decides on
    server = "bank"
    tool = "send_money"
    budget_param = "amount"
    [tools.params.recipient]
    critical = true
    sources = ["mandate:payee", "registry:beneficiaries"]
    normalize = "iban"
    [tools.params.amount]
    type = "number"
    scale = 100                    # Cedar has no floats: the policy sees round(amount * 100)

    [policy]
    file = "policy.cedar"

    [defaults]
    passthrough = ["bank.get_balance"]   # read-only tools that need no decision; anything else unlisted is DENIED
    receipts = "receipts.jsonl"
"""
from __future__ import annotations

import json
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

if sys.version_info >= (3, 11):
    import tomllib
else:  # pragma: no cover
    import tomli as tomllib

TYPES = {"string": str, "integer": int, "number": int, "boolean": bool}   # numbers are scaled to integers


@dataclass(frozen=True)
class ParamSpec:
    name: str
    type: str = "string"
    critical: bool = False
    sources: tuple[str, ...] = ()
    normalize: str | None = None
    scale: int = 1
    each: bool = False   # a list of values, every item resolved (e.g. email recipients)


@dataclass(frozen=True)
class ToolSpec:
    server: str
    tool: str
    action: str
    params: tuple[ParamSpec, ...]
    budget_param: str | None = None

    @property
    def key(self) -> str:
        return f"{self.server}.{self.tool}"


@dataclass(frozen=True)
class ServerSpec:
    name: str
    command: str
    args: tuple[str, ...] = ()
    env: dict[str, str] | None = None


@dataclass
class ProxyConfig:
    principal: str
    claims: dict[str, Any]
    budget: float | None
    registries: dict[str, tuple[dict[str, Any], str | None]]   # name -> (records, normalizer)
    servers: list[ServerSpec]
    tools: dict[str, ToolSpec]
    policy: str
    passthrough: frozenset[str] = frozenset()
    receipts: Path | None = None
    base: Path = field(default_factory=Path.cwd)


def _param(name: str, raw: dict) -> ParamSpec:
    spec = ParamSpec(name, raw.get("type", "string"), bool(raw.get("critical", False)),
                     tuple(raw.get("sources", ())), raw.get("normalize"), int(raw.get("scale", 1)),
                     bool(raw.get("each", False)))
    if spec.type not in TYPES:
        raise ValueError(f"param {name!r}: unknown type {spec.type!r}")
    if spec.critical and not spec.sources:
        raise ValueError(f"param {name!r}: a critical parameter needs at least one trusted source")
    for src in spec.sources:
        kind, _, _ = src.partition(":")
        if kind not in ("mandate", "registry"):
            raise ValueError(f"param {name!r}: source {src!r} must be 'mandate:<claim>' or 'registry:<name>'")
    return spec


def load_config(path: str | Path) -> ProxyConfig:
    path = Path(path).resolve()
    base = path.parent
    raw = tomllib.loads(path.read_text(encoding="utf-8"))
    mandate = raw.get("mandate", {})
    registries = {}
    for name, reg in raw.get("registries", {}).items():
        records = json.loads((base / reg["file"]).read_text(encoding="utf-8"))
        if not isinstance(records, dict):
            raise TypeError(f"registry {name!r}: the file must hold a JSON object {{ref: value}}")
        registries[name] = (records, reg.get("normalize"))
    # command "python" means the interpreter running the proxy, so the downstream server finds the same packages
    servers = [ServerSpec(s["name"], sys.executable if s["command"] == "python" else s["command"],
                          tuple(s.get("args", ())), s.get("env")) for s in raw.get("servers", ())]
    names = {s.name for s in servers}
    tools = {}
    for t in raw.get("tools", ()):
        if t["server"] not in names:
            raise ValueError(f"tool {t['tool']!r}: unknown server {t['server']!r}")
        spec = ToolSpec(t["server"], t["tool"], t.get("action", f"{t['server']}.{t['tool']}"),
                        tuple(_param(n, p) for n, p in t.get("params", {}).items()), t.get("budget_param"))
        if spec.budget_param and spec.budget_param not in {p.name for p in spec.params}:
            raise ValueError(f"tool {spec.key}: budget_param {spec.budget_param!r} must be a declared param")
        tools[spec.key] = spec
    policy_file = raw.get("policy", {}).get("file")
    if not policy_file:
        raise ValueError("a [policy] file is required (Cedar, default deny)")
    defaults = raw.get("defaults", {})
    receipts = defaults.get("receipts")
    return ProxyConfig(principal=mandate.get("principal", "user"), claims=dict(mandate.get("claims", {})),
                       budget=mandate.get("budget"), registries=registries, servers=servers, tools=tools,
                       policy=(base / policy_file).read_text(encoding="utf-8"),
                       passthrough=frozenset(defaults.get("passthrough", ())),
                       receipts=(base / receipts) if receipts else None, base=base)
