"""Health pack: the patient must be one of the clinician's OPEN encounters (read by the gateway from
the EHR), the purpose is bound in the clinician's mandate, research needs consent on file (read by the
gateway from the consent registry), full records under treatment need step-up, emergency access is
four-eyes ("break the glass")."""
from __future__ import annotations

from collections.abc import Iterable, Mapping
from pathlib import Path

from intentplane.cedar_pdp import CedarPDP
from intentplane.core import (
    ActionContract,
    DictRegistry,
    FromMandate,
    FromRegistry,
    Pack,
    Param,
)

SECTIONS = {"summary", "labs", "meds", "notes", "full"}
PURPOSES = {"treatment", "emergency", "research"}

READ_RECORD = ActionContract(
    name="ehr.read_record", resource="patient_record", version="health-3",
    params=(
        Param("patient_id", str, critical=True, sources=(FromRegistry("open_encounters"),)),
        Param("section", str, validate=lambda s: s in SECTIONS),
        Param("purpose", str, critical=True, sources=(FromMandate("purpose"),), validate=lambda p: p in PURPOSES),
    ),
)


PDP = CedarPDP.from_file("health-policy-3", Path(__file__).with_name("health.cedar"))


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
