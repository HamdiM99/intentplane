"""Bank pack: payee must come from the beneficiaries registry, amount is capped,
large amounts step up. The core does not know what an IBAN is."""
from intentplane.core import *  # noqa: F401,F403
from intentplane.core import Decision, Effect, Origin, Param, ActionContract, Intent, RulePDP, rule_strict_delegation

STEP_UP_ABOVE = 1_000.0
HARD_CAP = 10_000.0

TRANSFER = ActionContract(
    name="payment.transfer", resource="account", version="bank-1",
    params=(
        Param("source_account", str, critical=True, allowed_origins=frozenset({Origin.USER_CHANNEL, Origin.SYSTEM_OF_RECORD})),
        Param("payee_iban", str, critical=True, allowed_origins=frozenset({Origin.SYSTEM_OF_RECORD})),
        Param("amount", float, critical=True, allowed_origins=frozenset({Origin.USER_CHANNEL, Origin.SYSTEM_OF_RECORD}),
              validate=lambda a: a > 0),
        Param("currency", str, validate=lambda c: c in {"EUR", "USD"}),
        Param("reference", str),   # free text, not critical
    ),
)

def rule_hard_cap(i: Intent, c: ActionContract):
    if i.action == "payment.transfer" and i.params["amount"].value > HARD_CAP:
        return Decision(Effect.DENY, "policy", (f"amount above hard cap {HARD_CAP}",), "")

def rule_step_up(i: Intent, c: ActionContract):
    if i.action == "payment.transfer" and i.params["amount"].value > STEP_UP_ABOVE:
        return Decision(Effect.STEP_UP, "policy", ("amount requires strong authentication",), "")

def rule_sanctions(i: Intent, c: ActionContract):
    if i.action == "payment.transfer" and i.context.get("payee_sanctioned"):
        return Decision(Effect.DENY, "policy", ("payee on sanctions list",), "")

def pdp() -> RulePDP:
    return RulePDP("bank-policy-1", [rule_strict_delegation, rule_sanctions, rule_hard_cap, rule_step_up])

CONTRACTS = [TRANSFER]
