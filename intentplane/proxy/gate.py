"""The decision logic of the MCP proxy, independent of MCP (and tested without it).

Each configured tool becomes an action contract: its critical parameters are resolved against the mandate's
claims or a registry, the parameters the policy needs are typed (numbers scaled to integers, Cedar has no
float), and every other argument travels as one free JSON string, `_rest`, recorded in the receipt but never
used to decide. The downstream tool receives the agent's arguments with every critical one replaced by its
resolved, trusted value.
"""
from __future__ import annotations

import json
import secrets
import threading
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from intentplane.cedar_pdp import CedarPDP
from intentplane.core import (
    ActionContract,
    Approvals,
    Authority,
    DictRegistry,
    Effect,
    FromMandate,
    FromRegistry,
    Gateway,
    Ledger,
    Outcome,
    Pack,
    Param,
    Proposal,
    Status,
)
from intentplane.packs.bank import norm_iban

from .config import TYPES, ParamSpec, ProxyConfig, ToolSpec

NORMALIZERS: dict[str, Callable[[Any], Any]] = {
    "iban": norm_iban,
    "email": lambda v: str(v).strip().lower(),
    "lower": lambda v: str(v).strip().lower(),
}
REST = "_rest"


class FileLedger(Ledger):
    """The hash-chained receipts, also appended to a JSON-lines file."""

    def __init__(self, path) -> None:
        super().__init__()
        self.path = path

    def append(self, **data: Any) -> dict:
        entry = super().append(**data)
        with open(self.path, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, default=str) + "\n")
        return entry


@dataclass(frozen=True)
class GateResult:
    status: str               # "executed", "denied", "pending" (needs an approval), "passthrough"
    message: str
    result: Any = None
    op_hash: str | None = None
    effect: str | None = None


def _normalizer(name: str | None):
    if name is None:
        return None
    if name not in NORMALIZERS:
        raise ValueError(f"unknown normalizer {name!r} (known: {sorted(NORMALIZERS)})")
    return NORMALIZERS[name]


def _contract_param(p: ParamSpec) -> Param:
    if not p.critical:
        return Param(p.name, TYPES[p.type])
    sources = tuple(FromMandate(s.split(":", 1)[1]) if s.startswith("mandate:") else FromRegistry(s.split(":", 1)[1])
                    for s in p.sources)
    return Param(p.name, TYPES[p.type], critical=True, sources=sources, normalize=_normalizer(p.normalize),
                 each=p.each)


class ToolGate:
    def __init__(self, cfg: ProxyConfig) -> None:
        self.cfg = cfg
        registries = {name: DictRegistry({cfg.principal: records}, _normalizer(norm))
                      for name, (records, norm) in cfg.registries.items()}
        contracts = [ActionContract(t.action, t.server, (*map(_contract_param, t.params), Param(REST, str)),
                                    "mcp-1", t.budget_param) for t in cfg.tools.values()]
        self._by_action = {t.action: t for t in cfg.tools.values()}
        self._call: Callable[[ToolSpec, dict], Any] | None = None
        self._args: dict = {}
        self._lock = threading.Lock()   # one decision at a time: the executor reads the current call
        pack = Pack("mcp", contracts, CedarPDP("mcp-policy", cfg.policy), registries=registries,
                    executors={c.name: self._execute for c in contracts})
        self.authority = Authority()
        self.approvals = Approvals({cfg.principal: secrets.token_bytes(32)})
        self.gateway = Gateway([pack], self.authority, self.approvals,
                               FileLedger(cfg.receipts) if cfg.receipts else Ledger())
        self.token = self.authority.issue(cfg.principal, {c.name for c in contracts}, cfg.claims,
                                          budget=cfg.budget, ttl=12 * 3600)

    # ------------------------------------------------------------------
    def classify(self, key: str) -> str:
        """'gated' (configured), 'passthrough' (declared read-only) or 'denied' (anything else)."""
        if key in self.cfg.tools:
            return "gated"
        return "passthrough" if key in self.cfg.passthrough else "denied"

    def _proposal(self, spec: ToolSpec, args: Mapping[str, Any]) -> Proposal:
        values: dict[str, Any] = {}
        for p in spec.params:
            raw = args.get(p.name)
            if p.type == "number" and isinstance(raw, (int, float)) and not isinstance(raw, bool):
                raw = round(raw * p.scale)
            values[p.name] = raw
        declared = {p.name for p in spec.params}
        values[REST] = json.dumps({k: v for k, v in args.items() if k not in declared}, sort_keys=True, default=str)
        return Proposal(spec.action, values)

    def _execute(self, values: Mapping[str, Any], mandate) -> Any:
        spec = self._by_action[self._current_action]
        call_args = dict(self._args)
        for p in spec.params:
            if p.critical:
                call_args[p.name] = values[p.name]   # the trusted record, not the agent's spelling of it
        return self._call(spec, call_args)

    def decide(self, key: str, args: Mapping[str, Any], call: Callable[[ToolSpec, dict], Any],
               approvals: tuple[Mapping, ...] = ()) -> GateResult:
        """Submit one tool call. `call(spec, args)` performs the downstream call; only the gateway invokes it."""
        kind = self.classify(key)
        if kind == "denied":
            return GateResult("denied", f"tool {key!r} is not configured in intentplane: refused by default")
        if kind == "passthrough":
            return GateResult("passthrough", "read-only tool, no decision needed")
        spec = self.cfg.tools[key]
        with self._lock:
            self._call, self._args, self._current_action = call, dict(args), spec.action
            o: Outcome = self.gateway.submit(self.token, self._proposal(spec, args), approvals)
        if o.status is Status.EXECUTED:
            return GateResult("executed", "; ".join(o.decision.reasons), o.result, o.op_hash)
        if o.status is Status.PENDING:
            who = "you" if o.decision.effect is Effect.STEP_UP else "a second person (four eyes)"
            return GateResult("pending", f"needs confirmation by {who}", None, o.op_hash, o.decision.effect.value)
        return GateResult("denied", f"[{o.decision.stage}] " + "; ".join(o.decision.reasons))

    def approve(self, op_hash: str) -> Mapping:
        """The user's own approval (step-up), signed over the exact operation hash."""
        return self.approvals.approve(self.cfg.principal, op_hash)
