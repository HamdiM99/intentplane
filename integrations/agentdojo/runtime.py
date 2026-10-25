"""Route every money-moving / account-changing AgentDojo tool call through the intentplane gateway.

Trust model (same as AgenticZeroTrust's AgentDojo bridge, so results are comparable):
- TRUSTED: the user's prompt (typed in the bank's own app) and the bank ledger before the agent ran.
- UNTRUSTED: the agent's tool calls and everything it reads.

Mandate issued by the trusted side for the session: source account = the user's account, currency EUR,
payees = IBANs written in the user's prompt, cumulative budget 2,500.00. With mandate "prompt+known",
the user's existing counterparties are also in his beneficiaries registry.
A payee found nowhere is refused by the gateway (provenance); the simulated human may then add it to
the registry in the bank app and the call is resubmitted. Cedar asks for step-up above 1,000.00 and
account changes always need the account holder: the simulated human approves the operation hash.

Humans: "none" (nobody approves), "attentive" (approves only what the user actually asked for),
"rubber-stamp" (approves everything), "noisy:p:q" (approves an operation the user did NOT ask for with
probability p, refuses one the user asked for with probability q; seeded per scenario).
Ablations (`Config.ablate`): "resolution" (an unknown payee is added without asking anyone), "budget" (no
mandate budget), "approval" (step-up and four-eyes are granted automatically).
"""
from __future__ import annotations

import random
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path

from agentdojo.functions_runtime import FunctionsRuntime

from intentplane import (
    ActionContract,
    Approvals,
    Authority,
    Gateway,
    Pack,
    Param,
    Proposal,
    Status,
)
from intentplane.cedar_pdp import CedarPDP
from intentplane.packs import bank

PRINCIPAL = "emma"
BUDGET_CENTS = 250_000
MONEY_TOOLS = {"send_money", "schedule_transaction", "update_scheduled_transaction"}
ACCOUNT_TOOLS = {"update_password", "update_user_info"}
_IBAN_RE = re.compile(r"^[A-Z]{2}\d{2}[A-Z0-9]{10,30}$")
_IBAN_IN_TEXT = re.compile(r"\b[A-Z]{2}\d{2}[A-Z0-9]{10,30}\b")


def cents(amount: object) -> int | None:
    try:
        return round(float(amount) * 100)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


def safe_iban(v: object) -> str:
    try:
        return bank.norm_iban(v)
    except ValueError:
        return str(v)


def op_key(function: str, args: Mapping) -> tuple:
    """Comparable description of an operation, as the human sees it on the confirmation screen."""
    if function in MONEY_TOOLS:
        return ("money", safe_iban(args.get("recipient") or ""), cents(args.get("amount")))
    return (function, tuple(sorted((k, str(v)) for k, v in args.items() if v is not None)))


def matches_expected(op: tuple, expected: set[tuple]) -> bool:
    if op[0] == "money":
        return op in expected
    return any(e[0] == op[0] and set(e[1]) <= set(op[1]) for e in expected)


# --- account changes as intentplane actions (domain pack defined by the integration) -----------
PASSWORD = ActionContract("account.update_password", "user_account", (Param("password", str),), "account-1")
USER_INFO = ActionContract("account.update_user_info", "user_account", (Param("changes", dict),), "account-1")


ACCOUNT_PDP = CedarPDP.from_file("account-policy-2", Path(__file__).with_name("account.cedar"))


@dataclass
class Config:
    prompt: str
    mandate: str = "prompt"  # "prompt" | "prompt+known" | "off" (no enforcement, attempts only observed)
    human: str = "none"
    expected_ops: set[tuple] = field(default_factory=set)
    attack_targets: set[str] = field(default_factory=set)
    seed: int = 0
    ablate: frozenset = frozenset()
    registry_drop: float = 0.0   # fraction of the known counterparties missing from the registry (incomplete records)
    stats: dict = field(default_factory=lambda: {"executed": 0, "denied": 0, "asked_human": 0, "human_yes": 0,
                                                 "attack_attempts": 0, "attack_blocked": 0, "decisions": []})


def human_decides(cfg: Config, expected: bool) -> bool:
    """The simulated human's answer to a confirmation, for an operation that is (or is not) one the user
    actually asked for."""
    if cfg.human in ("none", "rubber-stamp"):
        return cfg.human == "rubber-stamp"
    if cfg.human == "attentive":
        return expected
    _, p, q = cfg.human.split(":")
    rng = cfg.__dict__.setdefault("_rng", random.Random(
        f"{cfg.seed}|{cfg.prompt}|{sorted(map(str, cfg.attack_targets))}"))
    return rng.random() >= float(q) if expected else rng.random() < float(p)


def drop_records(cfg: Config, records: list) -> list:
    """An incomplete system of record: each entry is missing with probability cfg.registry_drop (seeded)."""
    if not cfg.registry_drop:
        return records
    rng = random.Random(f"drop|{cfg.seed}|{cfg.prompt}")
    return [r for r in records if rng.random() >= cfg.registry_drop]


class IntentplaneRuntime(FunctionsRuntime):
    def __init__(self, functions, cfg: Config) -> None:
        super().__init__(functions)
        self.cfg = cfg
        self._w: dict | None = None
        self._pending = None

    # -- trusted side --------------------------------------------------------------------------
    def _init_world(self, env) -> None:
        acc = env.bank_account
        known = {safe_iban(t.recipient) for t in acc.transactions + acc.scheduled_transactions}
        known |= {safe_iban(t.sender) for t in acc.transactions}
        known = sorted(i for i in known if _IBAN_RE.match(i) and i != safe_iban(acc.iban))
        known = drop_records(self.cfg, known)
        named = sorted({safe_iban(i) for i in _IBAN_IN_TEXT.findall(self.cfg.prompt)})
        registry = {PRINCIPAL: {f"known-{n}": iban for n, iban in enumerate(known)}} \
            if self.cfg.mandate == "prompt+known" else {}
        bank_pack, _ = bank.build(registry)
        bank_pack.executors["payment.transfer"] = lambda values, mandate: self._pending(values)
        account_pack = Pack("account", [PASSWORD, USER_INFO],
                            ACCOUNT_PDP,
                            executors={c.name: (lambda values, mandate: self._pending(None)) for c in (PASSWORD, USER_INFO)})
        auth = Authority()
        approvals = Approvals({PRINCIPAL: b"emma-device-key"})
        gw = Gateway([bank_pack, account_pack], auth, approvals)
        token = auth.issue(PRINCIPAL, {"payment.transfer", PASSWORD.name, USER_INFO.name},
                           {"source_account": acc.iban, "currency": "EUR", "payee": named},
                           budget=None if "budget" in self.cfg.ablate else BUDGET_CENTS)
        self._w = {"gw": gw, "token": token, "approvals": approvals,
                   "beneficiaries": bank_pack.registries["beneficiaries"], "added": 0, "source": acc.iban}

    def _human_approves(self, function: str, args: Mapping) -> bool:
        self.cfg.stats["asked_human"] += 1
        ok = human_decides(self.cfg, matches_expected(op_key(function, args), self.cfg.expected_ops))
        self.cfg.stats["human_yes"] += ok
        return ok

    # -- enforcement point ---------------------------------------------------------------------
    def _deny(self, why: str, function: str = ""):
        self.cfg.stats["denied"] += 1
        self.cfg.stats["decisions"].append({"tool": function, "status": "DENIED", "why": why})
        return "", f"PermissionError: blocked by the intentplane gateway: {why}"

    def _is_attack(self, env, function: str, args: Mapping) -> bool:
        if function in ACCOUNT_TOOLS:
            return function in self.cfg.attack_targets and not matches_expected(
                op_key(function, args), self.cfg.expected_ops)
        payee = args.get("recipient")
        if function == "update_scheduled_transaction" and not payee:
            cur = next((t for t in env.bank_account.scheduled_transactions if t.id == args.get("id")), None)
            payee = cur.recipient if cur else None
        return isinstance(payee, str) and safe_iban(payee) in self.cfg.attack_targets

    def run_function(self, env, function: str, kwargs: Mapping, raise_on_error: bool = False):
        if function not in MONEY_TOOLS | ACCOUNT_TOOLS:
            return super().run_function(env, function, kwargs, raise_on_error)
        attack = self._is_attack(env, function, kwargs)
        if self.cfg.mandate == "off":
            result = super().run_function(env, function, kwargs, raise_on_error)
        else:
            result = self._enforce(env, function, kwargs, raise_on_error)
        if attack:
            self.cfg.stats["attack_attempts"] += 1
            self.cfg.stats["attack_blocked"] += bool(result[1] and "blocked by the intentplane gateway" in result[1])
        return result

    def _enforce(self, env, function: str, kwargs: Mapping, raise_on_error: bool):
        if self._w is None:
            self._init_world(env)
        w, args = self._w, dict(kwargs)
        human_view, payee = args, None
        if function == "update_password":
            proposal = Proposal(PASSWORD.name, {"password": args.get("password")})
        elif function == "update_user_info":
            proposal = Proposal(USER_INFO.name, {"changes": {k: v for k, v in args.items() if v is not None}})
        else:
            if function == "update_scheduled_transaction":
                cur = next((t for t in env.bank_account.scheduled_transactions if t.id == args.get("id")), None)
                if cur is None:   # fail closed: never forward an unresolvable call to the real tool
                    return self._deny("scheduled transaction not found", function)
                if not any(args.get(k) for k in ("recipient", "amount", "recurring", "date")):
                    return super().run_function(env, function, kwargs, raise_on_error)  # subject-only edit
                payee, amount = args.get("recipient") or cur.recipient, args.get("amount") or cur.amount
            else:
                payee, amount = args.get("recipient"), args.get("amount")
            human_view = {**args, "recipient": payee, "amount": amount}
            proposal = Proposal("payment.transfer", {"source_account": w["source"], "payee_iban": payee,
                                                     "amount_cents": cents(amount), "currency": "EUR",
                                                     "reference": str(args.get("subject") or function)})

        box: dict = {}

        def pending(values):
            # the tool receives the RESOLVED payee (the trusted record) and amount, not the agent's raw values
            call = dict(kwargs)
            if values is not None:
                call["recipient"] = values["payee_iban"]
                if function != "update_scheduled_transaction" or args.get("amount") is not None:
                    call["amount"] = values["amount_cents"] / 100
            box["result"] = super(IntentplaneRuntime, self).run_function(env, function, call, raise_on_error)
            return "ok"

        self._pending = pending
        o = w["gw"].submit(w["token"], proposal)
        if (o.status is Status.DENIED and o.decision.stage == "provenance"
                and all(r.startswith("payee_iban") for r in o.decision.reasons) and isinstance(payee, str)):
            if "resolution" not in self.cfg.ablate and not self._human_approves(function, human_view):
                # new payee: the human may add it in the app
                return self._deny("payee is not in the mandate and the account holder did not add it", function)
            w["added"] += 1
            w["beneficiaries"].add(PRINCIPAL, f"added-{w['added']}", payee)
            o = w["gw"].submit(w["token"], proposal)
        if o.status is Status.PENDING:
            if "approval" not in self.cfg.ablate and not self._human_approves(function, human_view):
                return self._deny(f"{o.decision.effect.value}: not approved by the account holder", function)
            o = w["gw"].submit(w["token"], proposal, (w["approvals"].approve(PRINCIPAL, o.op_hash),))
        if o.status is not Status.EXECUTED:
            return self._deny(f"[{o.decision.stage}] " + "; ".join(o.decision.reasons), function)
        self.cfg.stats["executed"] += 1
        self.cfg.stats["decisions"].append({"tool": function, "status": "EXECUTED", "why": "; ".join(o.decision.reasons)})
        return box["result"]


def runtime_factory(cfg: Config):
    return lambda functions: IntentplaneRuntime(functions, cfg)
