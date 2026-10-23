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


class ERP:
    """Fictional ERP: tenant -> vendors, invoices, call-back log, plus a VoP service and a sanctions list."""

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


class _Ids:
    """Registry view: ref == value, valid if the record exists for that tenant."""

    def __init__(self, erp: ERP, table: str) -> None:
        self.erp, self.table = erp, table

    def get(self, owner, ref):
        return ref if ref in self.erp.of(owner)[self.table] else None

    def find(self, owner, value):
        return value if isinstance(value, str) and value in self.erp.of(owner)[self.table] else None


class _CurrentIbans:
    """Registry view: the CURRENT IBAN of each vendor of the tenant (ref = vendor id)."""

    def __init__(self, erp: ERP) -> None:
        self.erp = erp

    def get(self, owner, ref):
        v = self.erp.of(owner)["vendors"].get(ref)
        return v["iban"] if v else None

    def find(self, owner, value):
        try:
            n = norm_iban(value)
        except ValueError:
            return None
        return next((vid for vid, v in self.erp.of(owner)["vendors"].items() if norm_iban(v["iban"]) == n), None)


def build(erp: ERP) -> Pack:
    def invoice(values, m) -> dict:
        return erp.of(m.owner)["invoices"][values["invoice_ref"]]

    def invoice_vendor(values, m) -> dict:
        return erp.of(m.owner)["vendors"][invoice(values, m)["vendor_id"]]

    def callback_verified(a, v, m) -> bool:
        if a != UPDATE_BANK.name:
            return False
        t = erp.of(m.owner)
        phone = t["callbacks"].get((v["vendor_id"], norm_iban(v["new_iban"])))
        return phone is not None and phone == t["vendors"][v["vendor_id"]]["phone"]

    def pay_fact(fn):
        return lambda a, v, m: fn(v, m) if a == PAY.name else False

    context = {
        "callback_verified": callback_verified,
        "invoice_open": pay_fact(lambda v, m: invoice(v, m)["status"] == "open"),
        "payee_is_invoice_vendor": pay_fact(lambda v, m: norm_iban(invoice_vendor(v, m)["iban"])
                                            == norm_iban(v["payee_iban"])),
        "amount_matches_invoice": pay_fact(lambda v, m: invoice(v, m)["amount_cents"] == v["amount_cents"]
                                           and invoice(v, m)["currency"] == v["currency"]),
        "vop_match": pay_fact(lambda v, m: erp.vop.get((invoice_vendor(v, m)["name"], norm_iban(v["payee_iban"])),
                                                       False)),
        "payee_sanctioned": pay_fact(lambda v, m: norm_iban(v["payee_iban"]) in erp.sanctioned),
        "iban_age_days": lambda a, v, m: (int((erp.now() - invoice_vendor(v, m)["iban_changed_at"]) // DAY)
                                          if a == PAY.name else 0),
    }

    def update_bank(values, m) -> str:   # writes only in the mandate's own tenant
        vendor = erp.of(m.owner)["vendors"][values["vendor_id"]]
        vendor["iban"], vendor["iban_changed_at"] = norm_iban(values["new_iban"]), erp.now()
        return f"vendor {values['vendor_id']}: IBAN updated"

    def pay(values, m) -> str:
        erp.of(m.owner)["invoices"][values["invoice_ref"]]["status"] = "paid"
        erp.payments.append({**values, "tenant": m.owner})
        return f"paid {values['amount_cents'] / 100:.2f} {values['currency']} for {values['invoice_ref']}"

    return Pack("accounts_payable", [UPDATE_BANK, PAY], CedarPDP.from_file("ap-policy-1", POLICY_FILE),
                registries={"vendors": _Ids(erp, "vendors"), "invoices": _Ids(erp, "invoices"),
                            "vendor_ibans": _CurrentIbans(erp)},
                context=context, executors={UPDATE_BANK.name: update_bank, PAY.name: pay})
