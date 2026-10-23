"""Health pack: the patient must be one of the clinician's OPEN encounters (read by the gateway from
the EHR), the purpose is bound in the clinician's mandate, research needs consent on file (read by the
gateway from the consent registry), full records under treatment need step-up, emergency access is
four-eyes ("break the glass")."""
from __future__ import annotations

from collections.abc import Iterable, Mapping

from intentplane.core import (
    ActionContract,
    Decision,
    DictRegistry,
    Effect,
    FromMandate,
    FromRegistry,
    Pack,
    Param,
    RulePDP,
)

SECTIONS = {"summary", "labs", "meds", "notes", "full"}
PURPOSES = {"treatment", "emergency", "research"}

READ_RECORD = ActionContract(
    name="ehr.read_record", resource="patient_record", version="health-2",
    params=(
        Param("patient_id", str, critical=True, sources=(FromRegistry("open_encounters"),)),
        Param("section", str, validate=lambda s: s in SECTIONS),
        Param("purpose", str, critical=True, sources=(FromMandate("purpose"),), validate=lambda p: p in PURPOSES),
    ),
)


def permit_read(i, c):
    if i.action == "ehr.read_record":
        return Decision(Effect.ALLOW, "policy", ("permit-read-open-encounter",))


def forbid_research_without_consent(i, c):
    if i.action == "ehr.read_record" and i.params["purpose"].value == "research" and not i.context["consent_research"]:
        return Decision(Effect.DENY, "policy", ("no research consent on file",))


def step_up_minimum_necessary(i, c):
    if i.action == "ehr.read_record" and i.params["section"].value == "full" and i.params["purpose"].value == "treatment":
        return Decision(Effect.STEP_UP, "policy", ("full record under treatment purpose needs justification",))


def four_eyes_break_the_glass(i, c):
    if i.action == "ehr.read_record" and i.params["purpose"].value == "emergency":
        return Decision(Effect.FOUR_EYES, "policy", ("emergency access: second reviewer required",))


PDP = RulePDP("health-policy-2", [permit_read, forbid_research_without_consent, step_up_minimum_necessary,
                                  four_eyes_break_the_glass])


def build(open_encounters: Mapping[str, Mapping[str, str]], research_consent: Iterable[str] = ()) -> tuple[Pack, list]:
    """open_encounters: clinician -> {encounter id: patient id}. Returns the pack and the access log."""
    consent = set(research_consent)
    reads: list[dict] = []

    def execute(values) -> dict:
        reads.append(dict(values))
        return {"patient_id": values["patient_id"], "section": values["section"], "data": "<record>"}

    pack = Pack("health", [READ_RECORD], PDP,
                registries={"open_encounters": DictRegistry(open_encounters)},
                context={"consent_research": lambda a, v, m: v["patient_id"] in consent},
                executors={"ehr.read_record": execute})
    return pack, reads
