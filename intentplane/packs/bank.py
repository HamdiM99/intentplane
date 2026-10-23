"""Bank pack (the AgenticZeroTrust behaviour on the generic core).

payee: the one the user named in the mandate, or one of HIS registered beneficiaries.
source account and currency: bound in the mandate. Amount: chosen by the agent, but charged
against the mandate's cumulative budget and checked by Cedar (step-up above 1,000.00, hard cap
10,000.00, see bank.cedar). Sanctions come from the gateway's own list, never from the agent.
Amounts are integer cents (Cedar has no float).
"""
from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from pathlib import Path

from intentplane.cedar_pdp import CedarPDP
from intentplane.core import ActionContract, DictRegistry, FromMandate, FromRegistry, Pack, Param


def norm_iban(v: object) -> str:
    if not isinstance(v, str) or not v.isascii():   # look-alike characters are not folded: they stay different
        raise ValueError("not an ASCII IBAN")
    return re.sub(r"\s+", "", v).upper()


_IBAN = re.compile(r"^[A-Z]{2}\d{2}[A-Z0-9]{10,30}$")


def is_iban(v: object) -> bool:
    """Structural IBAN check (country, check digits, BBAN). Applied to the RESOLVED value, so even a record
    a human added by mistake ("Apple") is refused."""
    try:
        return bool(_IBAN.match(norm_iban(v)))
    except ValueError:
        return False


TRANSFER = ActionContract(
    name="payment.transfer", resource="account", version="bank-3", budget_param="amount_cents",
    params=(
        Param("source_account", str, critical=True, sources=(FromMandate("source_account"),), normalize=norm_iban,
              validate=is_iban),
        Param("payee_iban", str, critical=True, sources=(FromMandate("payee"), FromRegistry("beneficiaries")),
              normalize=norm_iban, validate=is_iban),
        Param("amount_cents", int, validate=lambda a: a > 0),
        Param("currency", str, critical=True, sources=(FromMandate("currency"),)),
        Param("reference", str),
    ),
)

POLICY_FILE = Path(__file__).with_name("bank.cedar")


def build(beneficiaries: Mapping[str, Mapping[str, str]], sanctioned: Iterable[str] = (),
          sanctioned_countries: Iterable[str] = ("KP", "IR")) -> tuple[Pack, list]:
    """beneficiaries: principal -> {ref: IBAN}. Returns the pack and the (mock) core-banking ledger."""
    blocked = {norm_iban(i) for i in sanctioned}
    countries = set(sanctioned_countries)
    ledger: list[dict] = []

    def is_sanctioned(action, values, mandate) -> bool:
        iban = norm_iban(values["payee_iban"])
        return iban in blocked or iban[:2] in countries

    def execute(values, mandate) -> str:
        ledger.append(dict(values))
        return f"transfer-{len(ledger)}"

    pack = Pack("bank", [TRANSFER], CedarPDP.from_file("bank-policy-3", POLICY_FILE),
                registries={"beneficiaries": DictRegistry(beneficiaries, normalize=norm_iban)},
                context={"payee_sanctioned": is_sanctioned},
                executors={"payment.transfer": execute})
    return pack, ledger

