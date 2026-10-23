"""intentplane.core: domain-independent intent control plane for LLM agents.

Trust boundary (the whole point of the design):

  * The agent produces ONE thing: a `Proposal` = an action name and raw argument values.
    It cannot express an origin, an identity, a scope or a context. Anything it sends is data.
  * The gateway resolves everything else itself, from trusted sources only:
      identity, scopes, mandate   <- a signed token issued by the trusted side (`Authority`)
      provenance of each value    <- the action contract says where a critical value may come
                                     from (`FromMandate`, `FromRegistry`); the gateway looks it up
      context (sanctions, CI ...) <- context providers that query systems of record
  * Then, in order: contract -> provenance -> delegation -> budget -> policy (default deny)
    -> human approval bound to the operation hash -> execution by the gateway (the only
    credential holder) -> hash-chained receipt. Any error at any stage => DENY.

Security property (tests/test_property.py): whatever the agent proposes, an action is executed
only if every critical value equals a value from a trusted source for that token, the principal
holds the scope, and the mandate's budget is respected.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import secrets
import threading
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Protocol


def _canon(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), default=str)


def _h(obj: Any) -> str:
    return hashlib.sha256(_canon(obj).encode()).hexdigest()


# --------------------------------------------------------------------------- #
# What the agent may produce
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class Proposal:
    """The only output of the agent. Values are raw JSON-like data; a registry-backed value may be
    given as {"ref": "<record id>"}. There is deliberately no field for origin, scope or context."""
    action: str
    args: Mapping[str, Any]


# --------------------------------------------------------------------------- #
# Provenance: produced by the gateway only
# --------------------------------------------------------------------------- #
class Origin(str, Enum):
    SYSTEM_OF_RECORD = "system_of_record"   # read by the gateway from a registry
    USER_CHANNEL = "user_channel"           # bound in the principal's signed mandate
    UNTRUSTED = "untrusted"                 # anything only the agent vouches for


@dataclass(frozen=True)
class Tagged:
    value: Any
    origin: Origin
    ref: str | None = None


@dataclass(frozen=True)
class FromMandate:
    """The value must equal claim `field` of the signed mandate."""
    field: str


@dataclass(frozen=True)
class FromRegistry:
    """The value is read from registry `registry` (by {"ref": id}, or found by value)."""
    registry: str


@dataclass(frozen=True)
class Free:
    """Any value, labelled UNTRUSTED. Not allowed for critical parameters."""


Source = FromMandate | FromRegistry | Free


class Registry(Protocol):
    """A system of record, always queried FOR a principal: Alice's beneficiaries are not Bob's."""
    def get(self, principal: str, ref: str) -> Any | None: ...
    def find(self, principal: str, value: Any) -> str | None: ...


class DictRegistry:
    """In-memory system of record: principal -> {ref -> value}."""

    def __init__(self, records: Mapping[str, Mapping[str, Any]], normalize: Callable[[Any], Any] | None = None):
        self._records = {p: dict(r) for p, r in records.items()}
        self._norm = normalize or (lambda v: v)

    def get(self, principal: str, ref: str) -> Any | None:
        return self._records.get(principal, {}).get(ref)

    def find(self, principal: str, value: Any) -> str | None:
        try:
            n = self._norm(value)
        except Exception:  # noqa: BLE001  (an unnormalisable value is simply not found)
            return None
        return next((r for r, v in self._records.get(principal, {}).items() if self._norm(v) == n), None)


# --------------------------------------------------------------------------- #
# Action contracts
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class Param:
    name: str
    type: type
    critical: bool = False
    sources: tuple[Source, ...] = (Free(),)
    validate: Callable[[Any], bool] | None = None
    normalize: Callable[[Any], Any] | None = None

    def __post_init__(self) -> None:
        if not self.sources:
            raise ValueError(f"{self.name}: no source")
        if self.critical and any(isinstance(s, Free) for s in self.sources):
            raise ValueError(f"{self.name}: a critical parameter cannot accept Free values")


@dataclass(frozen=True)
class ActionContract:
    name: str
    resource: str
    params: tuple[Param, ...]
    version: str = "1"
    budget_param: str | None = None   # numeric param charged against the mandate's budget


class Catalog:
    """The finite attack surface: every action the agent may name, and nothing else."""

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
# Identity: signed mandates issued by the trusted side
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class Mandate:
    principal: str
    scopes: frozenset[str]
    claims: Mapping[str, Any]
    jti: str
    exp: float
    budget: float | None = None
    max_calls: int | None = None


class Authority:
    """Issues and verifies mandate tokens. Lives on the trusted side (the bank app, the EHR, ...).
    Production: asymmetric signatures / workload identity instead of a shared HMAC key."""

    def __init__(self) -> None:
        self._key = secrets.token_bytes(32)
        self._revoked: set[str] = set()

    def issue(self, principal: str, scopes: set[str] | frozenset[str], claims: Mapping[str, Any] | None = None, *,
              budget: float | None = None, max_calls: int | None = None, ttl: float = 3600) -> str:
        payload = _canon({"sub": principal, "scopes": sorted(scopes), "claims": dict(claims or {}),
                          "budget": budget, "max_calls": max_calls, "exp": time.time() + ttl,
                          "jti": secrets.token_hex(16)})
        return payload + "." + hmac.new(self._key, payload.encode(), hashlib.sha256).hexdigest()

    def revoke(self, principal: str) -> None:
        self._revoked.add(principal)

    def verify(self, token: str) -> Mandate | None:
        try:
            payload, sig = token.rsplit(".", 1)
            good = hmac.new(self._key, payload.encode(), hashlib.sha256).hexdigest()
            if not hmac.compare_digest(sig, good):
                return None
            c = json.loads(payload)
            if c["exp"] < time.time() or c["sub"] in self._revoked:
                return None
            return Mandate(c["sub"], frozenset(c["scopes"]), c["claims"], c["jti"], c["exp"],
                           c["budget"], c["max_calls"])
        except (ValueError, KeyError, TypeError, AttributeError):
            return None


# --------------------------------------------------------------------------- #
# Resolved intent, decisions
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class ResolvedIntent:
    """Built by the gateway only: what the agent asked for, with trusted provenance and context."""
    action: str
    params: Mapping[str, Tagged]
    mandate: Mandate
    context: Mapping[str, Any]

    def values(self) -> dict[str, Any]:
        return {k: t.value for k, t in self.params.items()}

    def canonical(self) -> dict[str, Any]:
        """What a human approver is shown, and what the approval signature covers."""
        return {"action": self.action, "principal": self.mandate.principal, "mandate": self.mandate.jti,
                "params": {k: t.value for k, t in sorted(self.params.items())},
                "provenance": {k: [t.origin.value, t.ref] for k, t in sorted(self.params.items())}}

    def op_hash(self) -> str:
        return _h(self.canonical())


class Effect(str, Enum):
    ALLOW = "allow"
    STEP_UP = "step_up"        # the principal confirms with strong authentication
    FOUR_EYES = "four_eyes"    # a second, different human approves
    DENY = "deny"


_RANK = {Effect.ALLOW: 0, Effect.STEP_UP: 1, Effect.FOUR_EYES: 2, Effect.DENY: 3}


@dataclass(frozen=True)
class Decision:
    effect: Effect
    stage: str
    reasons: tuple[str, ...]
    policy_version: str = ""


class PDP(Protocol):
    version: str

    def evaluate(self, intent: ResolvedIntent, contract: ActionContract) -> Decision: ...


Rule = Callable[[ResolvedIntent, ActionContract], "Decision | None"]


class RulePDP:
    """Deterministic reference PDP, DEFAULT DENY. Rules return ALLOW (a permit), STEP_UP / FOUR_EYES
    (obligations), DENY (a forbid) or None. Any DENY wins; without at least one permit the answer is
    DENY; otherwise the strongest obligation applies."""

    def __init__(self, version: str, rules: list[Rule]) -> None:
        self.version = version
        self._rules = rules

    def evaluate(self, intent: ResolvedIntent, contract: ActionContract) -> Decision:
        decisions = [d for d in (r(intent, contract) for r in self._rules) if d is not None]
        denies = [d for d in decisions if d.effect is Effect.DENY]
        if denies:
            return Decision(Effect.DENY, "policy", tuple(r for d in denies for r in d.reasons), self.version)
        if not any(d.effect is Effect.ALLOW for d in decisions):
            return Decision(Effect.DENY, "policy", ("no policy permits this action",), self.version)
        top = max(decisions, key=lambda d: _RANK[d.effect])
        return Decision(top.effect, "policy", tuple(r for d in decisions for r in d.reasons), self.version)


# --------------------------------------------------------------------------- #
# Human approval bound to the operation hash (single-use, expiring)
# --------------------------------------------------------------------------- #
class Approvals:
    """Approvers sign op_hash|nonce|exp with a key that lives on their own device (simulated here)."""

    def __init__(self, humans: Mapping[str, bytes]) -> None:
        self._humans = dict(humans)
        self._used: set[str] = set()

    def approve(self, human: str, op_hash: str, ttl: float = 300) -> dict:
        nonce, exp = secrets.token_hex(16), time.time() + ttl
        msg = f"{op_hash}|{nonce}|{exp}".encode()
        return {"human": human, "op_hash": op_hash, "nonce": nonce, "exp": exp,
                "sig": hmac.new(self._humans[human], msg, hashlib.sha256).hexdigest()}

    def check(self, a: Mapping, op_hash: str) -> str | None:
        """The approving human if `a` is a valid, unused approval of exactly this operation."""
        try:
            key = self._humans.get(a["human"])
            msg = f"{a['op_hash']}|{a['nonce']}|{a['exp']}".encode()
            ok = (key is not None and hmac.compare_digest(a["sig"], hmac.new(key, msg, hashlib.sha256).hexdigest())
                  and a["exp"] >= time.time() and a["op_hash"] == op_hash and a["nonce"] not in self._used)
            return a["human"] if ok else None
        except (KeyError, TypeError, AttributeError):
            return None

    def consume(self, a: Mapping) -> None:
        self._used.add(a["nonce"])


# --------------------------------------------------------------------------- #
# Receipts (append-only, hash-chained)
# --------------------------------------------------------------------------- #
class Ledger:
    def __init__(self) -> None:
        self.entries: list[dict] = []

    def append(self, **data: Any) -> dict:
        body = {"seq": len(self.entries), "ts": time.time(), "data": data,
                "prev": self.entries[-1]["hash"] if self.entries else "0" * 64}
        body["hash"] = _h(body)
        self.entries.append(body)
        return body

    def verify(self) -> bool:
        prev = "0" * 64
        for e in self.entries:
            body = {k: v for k, v in e.items() if k != "hash"}
            if e["prev"] != prev or _h(body) != e["hash"]:
                return False
            prev = e["hash"]
        return True

    def __len__(self) -> int:
        return len(self.entries)


# --------------------------------------------------------------------------- #
# Domain pack
# --------------------------------------------------------------------------- #
ContextProvider = Callable[[str, Mapping[str, Any], Mandate], Any]   # (action, resolved values, mandate)
Executor = Callable[[Mapping[str, Any]], Any]


@dataclass
class Pack:
    """Everything domain-specific: contracts, policy, trusted sources, and how to execute."""
    name: str
    contracts: list[ActionContract]
    pdp: PDP
    registries: dict[str, Registry] = field(default_factory=dict)
    context: dict[str, ContextProvider] = field(default_factory=dict)
    executors: dict[str, Executor] = field(default_factory=dict)


# --------------------------------------------------------------------------- #
# Gateway (policy enforcement point)
# --------------------------------------------------------------------------- #
class Status(str, Enum):
    EXECUTED = "EXECUTED"
    PENDING = "PENDING"       # policy asks for a human: approve `op_hash` and resubmit
    DENIED = "DENIED"


@dataclass(frozen=True)
class Outcome:
    status: Status
    decision: Decision
    op_hash: str | None = None
    result: Any = None
    approvers: tuple[str, ...] = ()


class _Deny(Exception):
    def __init__(self, stage: str, *reasons: str) -> None:
        super().__init__(stage)
        self.decision = Decision(Effect.DENY, stage, reasons)


class Gateway:
    def __init__(self, packs: list[Pack], authority: Authority, approvals: Approvals | None = None,
                 ledger: Ledger | None = None) -> None:
        self.catalog = Catalog()
        self._pack_of: dict[str, Pack] = {}
        for p in packs:
            for c in p.contracts:
                self.catalog.register(c)
                self._pack_of[c.name] = p
        self.authority = authority
        self.approvals = approvals or Approvals({})
        self.ledger = ledger or Ledger()
        self._spent: dict[str, float] = {}
        self._calls: dict[str, int] = {}
        self._lock = threading.Lock()

    def submit(self, token: str, proposal: Proposal, approvals: tuple[Mapping, ...] = ()) -> Outcome:
        with self._lock:
            intent = None
            try:
                outcome, intent = self._submit(token, proposal, approvals)
            except _Deny as d:
                outcome = Outcome(Status.DENIED, d.decision)
            except Exception as e:  # noqa: BLE001  fail closed: an error never becomes an ALLOW
                outcome = Outcome(Status.DENIED, Decision(Effect.DENY, "error", (f"{type(e).__name__}: {e}",)))
            self.ledger.append(action=getattr(proposal, "action", None),
                               intent=intent.canonical() if intent else None,
                               status=outcome.status.value, stage=outcome.decision.stage,
                               reasons=list(outcome.decision.reasons), approvers=list(outcome.approvers),
                               policy_version=outcome.decision.policy_version)
            return outcome

    # -------------------------------------------------------------------- #
    def _submit(self, token: str, proposal: Proposal, approvals: tuple[Mapping, ...]):
        m = self.authority.verify(token)
        if m is None:
            raise _Deny("authn", "invalid, expired, forged or revoked token")
        if not isinstance(proposal, Proposal) or not isinstance(proposal.args, Mapping):
            raise _Deny("contract", "malformed proposal")
        contract = self.catalog.get(proposal.action)
        if contract is None:
            raise _Deny("contract", f"unknown action {proposal.action!r}")
        pack = self._pack_of[contract.name]

        expected = {p.name for p in contract.params}
        extra, missing = set(proposal.args) - expected, expected - set(proposal.args)
        if extra or missing:
            raise _Deny("contract", *([f"unexpected params {sorted(extra)}"] if extra else []),
                        *([f"missing params {sorted(missing)}"] if missing else []))

        params = {p.name: self._resolve(p, proposal.args[p.name], m, pack) for p in contract.params}

        if contract.name not in m.scopes:   # strict delegation: never more than the principal holds
            raise _Deny("delegation", f"principal {m.principal!r} lacks scope {contract.name!r}")
        charge = float(params[contract.budget_param].value) if contract.budget_param else 0.0
        if m.budget is not None and self._spent.get(m.jti, 0.0) + charge > m.budget:
            raise _Deny("budget", f"mandate budget {m.budget} would be exceeded")
        if m.max_calls is not None and self._calls.get(m.jti, 0) >= m.max_calls:
            raise _Deny("budget", f"mandate allows at most {m.max_calls} calls")

        values = {k: t.value for k, t in params.items()}
        context = {k: provider(contract.name, values, m) for k, provider in pack.context.items()}
        intent = ResolvedIntent(contract.name, params, m, context)
        decision = pack.pdp.evaluate(intent, contract)

        if decision.effect is Effect.DENY:
            return Outcome(Status.DENIED, decision), intent
        op = intent.op_hash()
        approvers: list[tuple[str, Mapping]] = []
        if decision.effect in (Effect.STEP_UP, Effect.FOUR_EYES):
            for a in approvals:
                human = self.approvals.check(a, op)
                ok = human == m.principal if decision.effect is Effect.STEP_UP else (human and human != m.principal)
                if ok:
                    approvers.append((human, a))
                    break
            if not approvers:
                return Outcome(Status.PENDING, decision, op), intent

        executor = pack.executors.get(contract.name)
        if executor is None:
            raise _Deny("execution", f"no executor for {contract.name!r}")
        result = executor(values)   # only the gateway holds the credential to act
        for _, a in approvers:
            self.approvals.consume(a)
        self._spent[m.jti] = self._spent.get(m.jti, 0.0) + charge
        self._calls[m.jti] = self._calls.get(m.jti, 0) + 1
        return Outcome(Status.EXECUTED, decision, op, result, tuple(h for h, _ in approvers)), intent

    # -------------------------------------------------------------------- #
    @staticmethod
    def _coerce(p: Param, value: Any) -> Any:
        if p.type is float and isinstance(value, int) and not isinstance(value, bool):
            value = float(value)
        if isinstance(value, bool) and p.type is not bool or not isinstance(value, p.type):
            raise _Deny("contract", f"{p.name}: expected {p.type.__name__}")
        if p.validate is not None and not p.validate(value):
            raise _Deny("contract", f"{p.name}: failed validation")
        return value

    def _resolve(self, p: Param, raw: Any, m: Mandate, pack: Pack) -> Tagged:
        """Turn a raw agent value into a Tagged value using trusted sources only."""
        norm = p.normalize or (lambda v: v)
        for src in p.sources:
            if isinstance(src, FromMandate):
                if src.field in m.claims and not isinstance(raw, Mapping):
                    claim = m.claims[src.field]
                    try:
                        same = norm(raw) == norm(claim)
                    except Exception:  # noqa: BLE001
                        same = False
                    if same:
                        return Tagged(self._coerce(p, claim), Origin.USER_CHANNEL, f"mandate:{src.field}")
            elif isinstance(src, FromRegistry):
                reg = pack.registries[src.registry]
                if isinstance(raw, Mapping):
                    ref = raw.get("ref") if set(raw) == {"ref"} and isinstance(raw.get("ref"), str) else None
                else:
                    ref = reg.find(m.principal, raw)
                record = reg.get(m.principal, ref) if ref is not None else None
                if record is not None:
                    return Tagged(self._coerce(p, record), Origin.SYSTEM_OF_RECORD, f"{src.registry}/{ref}")
            else:  # Free
                return Tagged(self._coerce(p, raw), Origin.UNTRUSTED)
        allowed = ", ".join(f"mandate:{s.field}" if isinstance(s, FromMandate) else f"registry:{s.registry}"
                            for s in p.sources)
        raise _Deny("provenance", f"{p.name}: value not found in any trusted source ({allowed})")
