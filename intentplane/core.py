"""intentplane.core — domain-independent intent control plane.

Generalises AgenticZeroTrust: the agent never acts, it emits a typed Intent
against an ActionContract. The gateway (PEP) checks, in order:

  1. contract   : the action exists and the parameters match its schema
  2. provenance : every *critical* parameter carries an origin label allowed
                  by the contract; an LLM-generated value is never allowed
  3. policy     : a deterministic PDP (no LLM) decides ALLOW / STEP_UP / DENY
  4. receipt    : a hash-chained record of the decision

Security property (checked in tests/test_property.py):
  for any intent, if any critical parameter has provenance UNTRUSTED,
  the decision is never ALLOW, whatever the rest of the intent says.
"""
from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Mapping, Protocol


# --------------------------------------------------------------------------- #
# Provenance
# --------------------------------------------------------------------------- #
class Origin(str, Enum):
    """Who produced a value. Set by the ingestion layer, never by the agent."""
    SYSTEM_OF_RECORD = "system_of_record"   # beneficiaries registry, EHR, CI
    USER_CHANNEL = "user_channel"           # typed/confirmed by the principal
    DERIVED = "derived"                     # computed by trusted code
    UNTRUSTED = "untrusted"                 # LLM text, emails, files, web


@dataclass(frozen=True)
class Tagged:
    """A value with its origin and an optional pointer to the source record."""
    value: Any
    origin: Origin
    ref: str | None = None  # e.g. "beneficiaries/42", "encounter/E-17"

    @staticmethod
    def untrusted(value: Any) -> "Tagged":
        return Tagged(value, Origin.UNTRUSTED)


def tag_from_agent(value: Any) -> Tagged:
    """Anything the agent writes itself is untrusted by construction."""
    return Tagged(value, Origin.UNTRUSTED)


# --------------------------------------------------------------------------- #
# Action contracts
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class Param:
    name: str
    type: type
    critical: bool = False
    allowed_origins: frozenset[Origin] = frozenset({Origin.SYSTEM_OF_RECORD})
    validate: Callable[[Any], bool] | None = None


@dataclass(frozen=True)
class ActionContract:
    name: str                      # "payment.transfer", "ehr.read_record"
    resource: str                  # "account", "patient_record", "repo"
    params: tuple[Param, ...]
    version: str = "1"

    def param(self, name: str) -> Param | None:
        return next((p for p in self.params if p.name == name), None)


class Catalog:
    """The finite, auditable attack surface: every action the agent may name."""

    def __init__(self) -> None:
        self._contracts: dict[str, ActionContract] = {}

    def register(self, c: ActionContract) -> None:
        if c.name in self._contracts:
            raise ValueError(f"duplicate contract {c.name}")
        self._contracts[c.name] = c

    def get(self, name: str) -> ActionContract | None:
        return self._contracts.get(name)

    def names(self) -> list[str]:
        return sorted(self._contracts)


# --------------------------------------------------------------------------- #
# Intent, principal, decision
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class Principal:
    id: str
    roles: frozenset[str]
    scopes: frozenset[str]       # what the human is allowed to do
    # On-behalf-of: the agent inherits exactly these, never more.


@dataclass(frozen=True)
class Intent:
    action: str
    params: Mapping[str, Tagged]
    on_behalf_of: Principal
    agent_id: str
    context: Mapping[str, Any] = field(default_factory=dict)

    def canonical(self) -> dict[str, Any]:
        """What a human would be shown. Produced by the gateway, not the LLM."""
        return {
            "action": self.action,
            "on_behalf_of": self.on_behalf_of.id,
            "params": {k: v.value for k, v in sorted(self.params.items())},
            "provenance": {k: v.origin.value for k, v in sorted(self.params.items())},
        }


class Effect(str, Enum):
    ALLOW = "allow"
    STEP_UP = "step_up"        # strong auth by the principal
    FOUR_EYES = "four_eyes"    # second human
    DENY = "deny"


@dataclass(frozen=True)
class Decision:
    effect: Effect
    stage: str                  # which layer decided
    reasons: tuple[str, ...]
    policy_version: str


# --------------------------------------------------------------------------- #
# Policy decision point (pluggable: Cedar/OPA adapters implement this)
# --------------------------------------------------------------------------- #
class PDP(Protocol):
    version: str

    def evaluate(self, intent: Intent, contract: ActionContract) -> Decision: ...


Rule = Callable[[Intent, ActionContract], Decision | None]


class RulePDP:
    """Deterministic reference PDP. Forbid-style rules first, then obligations
    (step-up), then permit. First DENY wins; highest escalation wins."""

    def __init__(self, version: str, rules: list[Rule]) -> None:
        self.version = version
        self._rules = rules

    def evaluate(self, intent: Intent, contract: ActionContract) -> Decision:
        worst: Decision | None = None
        order = {Effect.ALLOW: 0, Effect.STEP_UP: 1, Effect.FOUR_EYES: 2, Effect.DENY: 3}
        reasons: list[str] = []
        for rule in self._rules:
            d = rule(intent, contract)
            if d is None:
                continue
            reasons.extend(d.reasons)
            if worst is None or order[d.effect] > order[worst.effect]:
                worst = d
            if d.effect is Effect.DENY:
                break
        if worst is None:
            return Decision(Effect.ALLOW, "policy", ("default-permit-within-contract",), self.version)
        return Decision(worst.effect, "policy", tuple(reasons), self.version)


# --------------------------------------------------------------------------- #
# Universal rules (domain-independent, part of the core)
# --------------------------------------------------------------------------- #
def rule_strict_delegation(intent: Intent, contract: ActionContract) -> Decision | None:
    """The agent never exceeds the principal's own scopes."""
    if intent.action not in intent.on_behalf_of.scopes:
        return Decision(Effect.DENY, "policy", (f"principal lacks scope {intent.action}",), "")
    return None


# --------------------------------------------------------------------------- #
# Receipts (append-only, hash-chained)
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class Receipt:
    seq: int
    ts: float
    intent: dict[str, Any]
    decision: dict[str, Any]
    contract_version: str
    policy_version: str
    prev_hash: str
    hash: str


class Ledger:
    def __init__(self) -> None:
        self._entries: list[Receipt] = []

    def append(self, intent: Intent, decision: Decision, contract: ActionContract | None) -> Receipt:
        prev = self._entries[-1].hash if self._entries else "0" * 64
        body = {
            "seq": len(self._entries),
            "ts": time.time(),
            "intent": intent.canonical(),
            "decision": {"effect": decision.effect.value, "stage": decision.stage,
                         "reasons": list(decision.reasons)},
            "contract_version": contract.version if contract else "none",
            "policy_version": decision.policy_version,
            "prev_hash": prev,
        }
        h = hashlib.sha256(json.dumps(body, sort_keys=True, default=str).encode()).hexdigest()
        r = Receipt(hash=h, **body)
        self._entries.append(r)
        return r

    def verify(self) -> bool:
        prev = "0" * 64
        for r in self._entries:
            if r.prev_hash != prev:
                return False
            body = {k: getattr(r, k) for k in ("seq", "ts", "intent", "decision",
                                                 "contract_version", "policy_version", "prev_hash")}
            if hashlib.sha256(json.dumps(body, sort_keys=True, default=str).encode()).hexdigest() != r.hash:
                return False
            prev = r.hash
        return True

    def __len__(self) -> int:
        return len(self._entries)


# --------------------------------------------------------------------------- #
# Gateway (policy enforcement point)
# --------------------------------------------------------------------------- #
class Gateway:
    def __init__(self, catalog: Catalog, pdp: PDP, ledger: Ledger | None = None) -> None:
        self.catalog = catalog
        self.pdp = pdp
        self.ledger = ledger or Ledger()

    def decide(self, intent: Intent) -> tuple[Decision, Receipt]:
        contract = self.catalog.get(intent.action)
        d = self._check_contract(intent, contract)
        if d is None:
            d = self._check_provenance(intent, contract)  # type: ignore[arg-type]
        if d is None:
            d = self.pdp.evaluate(intent, contract)        # type: ignore[arg-type]
        receipt = self.ledger.append(intent, d, contract)
        return d, receipt

    # -- layer 1: contract ------------------------------------------------- #
    def _check_contract(self, intent: Intent, contract: ActionContract | None) -> Decision | None:
        if contract is None:
            return Decision(Effect.DENY, "contract", (f"unknown action {intent.action}",), self.pdp.version)
        reasons = []
        expected = {p.name for p in contract.params}
        extra = set(intent.params) - expected
        missing = expected - set(intent.params)
        if extra:
            reasons.append(f"unexpected params {sorted(extra)}")
        if missing:
            reasons.append(f"missing params {sorted(missing)}")
        for p in contract.params:
            t = intent.params.get(p.name)
            if t is None:
                continue
            if not isinstance(t.value, p.type):
                reasons.append(f"{p.name}: expected {p.type.__name__}")
            elif p.validate and not p.validate(t.value):
                reasons.append(f"{p.name}: failed validation")
        if reasons:
            return Decision(Effect.DENY, "contract", tuple(reasons), self.pdp.version)
        return None

    # -- layer 2: provenance ----------------------------------------------- #
    def _check_provenance(self, intent: Intent, contract: ActionContract) -> Decision | None:
        reasons = []
        for p in contract.params:
            if not p.critical:
                continue
            t = intent.params[p.name]
            if t.origin not in p.allowed_origins:
                reasons.append(f"{p.name}: origin {t.origin.value} not in "
                               f"{sorted(o.value for o in p.allowed_origins)}")
        if reasons:
            return Decision(Effect.DENY, "provenance", tuple(reasons), self.pdp.version)
        return None
