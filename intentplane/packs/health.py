"""Health pack: patient id must come from the encounter the clinician opened;
purpose and consent are invariants; emergency access is logged four-eyes."""
from intentplane.core import Decision, Effect, Origin, Param, ActionContract, Intent, RulePDP, rule_strict_delegation

READ_RECORD = ActionContract(
    name="ehr.read_record", resource="patient_record", version="health-1",
    params=(
        Param("patient_id", str, critical=True, allowed_origins=frozenset({Origin.SYSTEM_OF_RECORD})),
        Param("section", str, validate=lambda s: s in {"summary", "labs", "meds", "notes", "full"}),
        Param("purpose", str, critical=True, allowed_origins=frozenset({Origin.USER_CHANNEL, Origin.SYSTEM_OF_RECORD}),
              validate=lambda p: p in {"treatment", "emergency", "research"}),
    ),
)

def rule_consent(i: Intent, c: ActionContract):
    if i.action == "ehr.read_record" and i.params["purpose"].value == "research" and not i.context.get("consent_research"):
        return Decision(Effect.DENY, "policy", ("no research consent on file",), "")

def rule_minimum_necessary(i: Intent, c: ActionContract):
    if i.action == "ehr.read_record" and i.params["section"].value == "full" and i.params["purpose"].value == "treatment":
        return Decision(Effect.STEP_UP, "policy", ("full record under treatment purpose needs justification",), "")

def rule_break_the_glass(i: Intent, c: ActionContract):
    if i.action == "ehr.read_record" and i.params["purpose"].value == "emergency":
        return Decision(Effect.FOUR_EYES, "policy", ("emergency access: logged, second reviewer notified",), "")

def pdp() -> RulePDP:
    return RulePDP("health-policy-1", [rule_strict_delegation, rule_consent, rule_minimum_necessary, rule_break_the_glass])

CONTRACTS = [READ_RECORD]
