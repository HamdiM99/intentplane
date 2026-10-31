"""Accounts-payable pack: the "we changed banks" fraud, on an AP agent that reads vendor emails.

Ported from an independent v2.2 design. Systems of record, all per tenant (the company named in the
signed mandate), read by the gateway, never written by the agent:

  vendors    : vendor id -> {name, iban, iban_changed_at, phone}        (ERP vendor master)
  invoices   : invoice ref -> {vendor_id, amount_cents, currency, status} (ERP, after PO/receipt/invoice match)
  callbacks  : (vendor id, new IBAN) -> phone number the finance team called
  vop        : Verification of Payee: (vendor name, IBAN) -> holder name matches

Actions:
  vendor.update_bank_details  needs a call-back on the phone number already on file, then four eyes.
  invoice.pay                 the invoice must exist and be open, the payee must be the CURRENT IBAN of the
                              invoice's vendor, the amount must equal the invoice; a freshly changed IBAN
                              (< 7 days) needs four eyes; large invoices need step-up; VoP and sanctions apply.

Execution happens inside the gateway (under its lock): an invoice is marked paid in the same critical
section that authorised it, so it cannot be paid twice, even by concurrent requests.
"""
from __future__ import annotations

import time
from collections.abc import Callable, Iterable, Mapping
from pathlib import Path
from typing import Protocol

from intentplane.cedar_pdp import CedarPDP
from intentplane.core import ActionContract, FromMandate, FromRegistry, Pack, Param
from intentplane.packs.bank import is_iban, norm_iban

DAY = 86_400.0
POLICY_FILE = Path(__file__).with_name("accounts_payable.cedar")

UPDATE_BANK = ActionContract(
    name="vendor.update_bank_details", resource="vendor", version="ap-1",
    params=(
        Param("vendor_id", str, critical=True, sources=(FromRegistry("vendors"),)),
        Param("new_iban", str, validate=is_iban),   # untrusted by nature: it comes from the request itself
        Param("reason", str),
    ),
)

PAY = ActionContract(
    name="invoice.pay", resource="vendor", version="ap-1", budget_param="amount_cents",
    params=(
        Param("source_account", str, critical=True, sources=(FromMandate("source_account"),), normalize=norm_iban,
              validate=is_iban),
        Param("invoice_ref", str, critical=True, sources=(FromRegistry("invoices"),)),
        Param("payee_iban", str, critical=True, sources=(FromRegistry("vendor_ibans"),), normalize=norm_iban,
              validate=is_iban),
        Param("amount_cents", int, validate=lambda a: a > 0),
        Param("currency", str, critical=True, sources=(FromMandate("currency"),)),
    ),
)


class ERPSource(Protocol):
    """The ERP as the gateway sees it: read access to the systems of record, for one tenant at a time, plus the two
    writes the gateway makes AFTER a decision (the agent never holds these credentials). Every lookup is keyed by
    the mandate's tenant and by an id or a value the gateway resolves; nothing is taken on the agent's word.

    Implementations: `ERP` (in memory, for tests and demos) and `intentplane.connectors.odoo.OdooERP`."""

    def vendor(self, tenant: str, vendor_id: str) -> Mapping | None:
        """{name, iban, iban_changed_at (epoch seconds), phone}, or None if not a vendor of that tenant."""

    def vendor_for_iban(self, tenant: str, iban: str) -> str | None:
        """The vendor of that tenant whose CURRENT bank account is `iban` (normalised), or None."""

    def invoice(self, tenant: str, ref: str) -> Mapping | None:
        """{vendor_id, amount_cents, currency, status ('open' or anything else)}, or None."""

    def callback_phone(self, tenant: str, vendor_id: str, iban: str) -> str | None:
        """The phone number on which finance confirmed that `iban` belongs to the vendor, if it did."""

    def vop_match(self, tenant: str, vendor_name: str, iban: str) -> bool:
        """Verification of Payee: does the account holder of `iban` match the vendor's name?"""

    def is_sanctioned(self, iban: str) -> bool: ...

    def now(self) -> float: ...

    def record_payment(self, tenant: str, values: Mapping) -> str:
        """Executor of invoice.pay: pay the invoice and mark it paid (called by the gateway, under its lock)."""

    def set_vendor_iban(self, tenant: str, vendor_id: str, iban: str) -> str:
        """Executor of vendor.update_bank_details (called by the gateway, after call-back and four eyes)."""


class ERP:
    """Fictional ERP, in memory: tenant -> vendors, invoices, call-back log, plus a VoP service and a sanctions list.
    An `ERPSource`."""

    def __init__(self, tenants: Mapping[str, Mapping[str, dict]], vop: Mapping[tuple[str, str], bool] | None = None,
                 sanctioned: Iterable[str] = (), now: Callable[[], float] = time.time) -> None:
        self.t = {name: {"vendors": dict(d.get("vendors", {})), "invoices": dict(d.get("invoices", {})),
                         "callbacks": dict(d.get("callbacks", {}))} for name, d in tenants.items()}
        self.vop = {(n, norm_iban(i)): ok for (n, i), ok in (vop or {}).items()}
        self.sanctioned = {norm_iban(i) for i in sanctioned}
        self.now = now
        self.payments: list[dict] = []

    def of(self, tenant: str) -> dict:
        return self.t.get(tenant, {"vendors": {}, "invoices": {}, "callbacks": {}})

    def log_callback(self, tenant: str, vendor_id: str, new_iban: str, phone: str) -> None:
        """Finance records that it called the vendor about `new_iban` on `phone` (trusted side only). The IBAN is
        normalised here, so a connector cannot store a spelling the gateway would not recognise."""
        self.t[tenant]["callbacks"][(vendor_id, norm_iban(new_iban))] = phone

    # -- ERPSource ------------------------------------------------------------------------------------------
    def vendor(self, tenant, vendor_id):
        return self.of(tenant)["vendors"].get(vendor_id)

    def vendor_for_iban(self, tenant, iban):
        try:
            n = norm_iban(iban)
        except ValueError:
            return None
        return next((vid for vid, v in self.of(tenant)["vendors"].items() if norm_iban(v["iban"]) == n), None)

    def invoice(self, tenant, ref):
        return self.of(tenant)["invoices"].get(ref)

    def callback_phone(self, tenant, vendor_id, iban):
        return self.of(tenant)["callbacks"].get((vendor_id, norm_iban(iban)))

    def vop_match(self, tenant, vendor_name, iban):
        return self.vop.get((vendor_name, norm_iban(iban)), False)

    def is_sanctioned(self, iban):
        return norm_iban(iban) in self.sanctioned

    def record_payment(self, tenant, values):
        self.of(tenant)["invoices"][values["invoice_ref"]]["status"] = "paid"
        self.payments.append({**values, "tenant": tenant})
        return f"paid {values['amount_cents'] / 100:.2f} {values['currency']} for {values['invoice_ref']}"

    def set_vendor_iban(self, tenant, vendor_id, iban):
        vendor = self.of(tenant)["vendors"][vendor_id]
        vendor["iban"], vendor["iban_changed_at"] = norm_iban(iban), self.now()
        return f"vendor {vendor_id}: IBAN updated"


class _Vendors:
    """Registry view: a vendor id of the tenant (ref == value)."""

    def __init__(self, erp: ERPSource) -> None:
        self.erp = erp

    def get(self, owner, ref):
        return ref if isinstance(ref, str) and self.erp.vendor(owner, ref) else None

    def find(self, owner, value):
        return self.get(owner, value)


class _Invoices:
    """Registry view: an invoice ref of the tenant (ref == value)."""

    def __init__(self, erp: ERPSource) -> None:
        self.erp = erp

    def get(self, owner, ref):
        return ref if isinstance(ref, str) and self.erp.invoice(owner, ref) else None

    def find(self, owner, value):
        return self.get(owner, value)


class _CurrentIbans:
    """Registry view: the CURRENT IBAN of each vendor of the tenant (ref = vendor id)."""

    def __init__(self, erp: ERPSource) -> None:
        self.erp = erp

    def get(self, owner, ref):
        v = self.erp.vendor(owner, ref)
        return v["iban"] if v else None

    def find(self, owner, value):
        return self.erp.vendor_for_iban(owner, value)


def build(erp: ERPSource) -> Pack:
    def invoice(values, m) -> Mapping:
        return erp.invoice(m.owner, values["invoice_ref"])

    def invoice_vendor(values, m) -> Mapping:
        return erp.vendor(m.owner, invoice(values, m)["vendor_id"])

    def callback_verified(a, v, m) -> bool:
        if a != UPDATE_BANK.name:
            return False
        phone = erp.callback_phone(m.owner, v["vendor_id"], v["new_iban"])
        return phone is not None and phone == erp.vendor(m.owner, v["vendor_id"])["phone"]

    def pay_fact(fn):
        return lambda a, v, m: fn(v, m) if a == PAY.name else False

    context = {
        "callback_verified": callback_verified,
        "invoice_open": pay_fact(lambda v, m: invoice(v, m)["status"] == "open"),
        "payee_is_invoice_vendor": pay_fact(lambda v, m: norm_iban(invoice_vendor(v, m)["iban"])
                                            == norm_iban(v["payee_iban"])),
        "amount_matches_invoice": pay_fact(lambda v, m: invoice(v, m)["amount_cents"] == v["amount_cents"]
                                           and invoice(v, m)["currency"] == v["currency"]),
        "vop_match": pay_fact(lambda v, m: erp.vop_match(m.owner, invoice_vendor(v, m)["name"], v["payee_iban"])),
        "payee_sanctioned": pay_fact(lambda v, m: erp.is_sanctioned(v["payee_iban"])),
        "iban_age_days": lambda a, v, m: (int((erp.now() - invoice_vendor(v, m)["iban_changed_at"]) // DAY)
                                          if a == PAY.name else 0),
    }

    def update_bank(values, m) -> str:   # writes only in the mandate's own tenant
        return erp.set_vendor_iban(m.owner, values["vendor_id"], values["new_iban"])

    def pay(values, m) -> str:
        return erp.record_payment(m.owner, values)

    return Pack("accounts_payable", [UPDATE_BANK, PAY], CedarPDP.from_file("ap-policy-1", POLICY_FILE),
                registries={"vendors": _Vendors(erp), "invoices": _Invoices(erp), "vendor_ibans": _CurrentIbans(erp)},
                context=context, executors={UPDATE_BANK.name: update_bank, PAY.name: pay})
