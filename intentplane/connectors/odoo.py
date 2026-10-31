"""Odoo as the system of record of the accounts-payable pack: an `ERPSource` over Odoo's external API (XML-RPC).

Who holds what (see docs/registry-authority.md):

  reader      read-only Odoo user: vendors, vendor bank accounts, vendor bills. Every registry lookup and every
              policy fact uses it. It cannot modify a bank account (Odoo refuses), so nothing the gateway reads can
              have been written through it.
  payer       Odoo user allowed to register payments on vendor bills; used ONLY by the executor of `invoice.pay`,
              after the gateway decided, and always paying to the bank account the gateway RESOLVED.
  bank_admin  Odoo user allowed to create vendor bank accounts; used ONLY by the executor of
              `vendor.update_bank_details`, which the policy allows after a call-back and four eyes.

The agent holds none of these credentials. A tenant of intentplane maps to one Odoo database; any other tenant
sees an empty ERP.

Odoo has no call-back log and no Verification of Payee service: call-backs are recorded by finance through
`log_callback` (a trusted-side JSON file), and VoP compares the account holder name stored in Odoo with the
vendor's name. Both are stand-ins to replace with the company's own procedure and its bank's VoP service.
"""
from __future__ import annotations

import json
import time
import xmlrpc.client
from collections.abc import Iterable
from datetime import datetime, timezone
from pathlib import Path

from intentplane.packs.bank import norm_iban


def _epoch(odoo_datetime: str) -> float:
    """Odoo stores datetimes as naive UTC strings."""
    return datetime.strptime(odoo_datetime, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc).timestamp()


class _Session:
    def __init__(self, url: str, db: str, login: str, password: str) -> None:
        self.url, self.db, self.password = url.rstrip("/"), db, password
        self.uid = xmlrpc.client.ServerProxy(f"{self.url}/xmlrpc/2/common").authenticate(db, login, password, {})
        if not self.uid:
            raise PermissionError(f"Odoo refused the credentials of {login!r}")
        self._models = xmlrpc.client.ServerProxy(f"{self.url}/xmlrpc/2/object", allow_none=True)

    def call(self, model: str, method: str, args: list, **kw):
        return self._models.execute_kw(self.db, self.uid, self.password, model, method, args, kw)


class OdooERP:
    """An `ERPSource` (intentplane.packs.accounts_payable) backed by an Odoo database."""

    def __init__(self, url: str, db: str, *, tenant: str, reader: tuple[str, str], payer: tuple[str, str],
                 bank_admin: tuple[str, str] | None = None, callbacks_file: str | Path | None = None,
                 sanctioned: Iterable[str] = ()) -> None:
        self.tenant = tenant
        self._reader = _Session(url, db, *reader)
        self._payer = _Session(url, db, *payer)
        self._bank_admin = _Session(url, db, *bank_admin) if bank_admin else None
        self._callbacks_file = Path(callbacks_file) if callbacks_file else None
        self._callbacks = (json.loads(self._callbacks_file.read_text(encoding="utf-8"))
                           if self._callbacks_file and self._callbacks_file.exists() else {})
        self.sanctioned = {norm_iban(i) for i in sanctioned}
        self.payments: list[dict] = []

    # -- trusted side ---------------------------------------------------------------------------------------
    def log_callback(self, tenant: str, vendor_id: str, new_iban: str, phone: str) -> None:
        """Finance records that it called the vendor about `new_iban` on `phone`."""
        self._callbacks[f"{tenant}|{vendor_id}|{norm_iban(new_iban)}"] = phone
        if self._callbacks_file:
            self._callbacks_file.write_text(json.dumps(self._callbacks, indent=1), encoding="utf-8")

    # -- ERPSource: reads, all through the read-only user -----------------------------------------------------
    def _mine(self, tenant: str) -> bool:
        return tenant == self.tenant

    def _current_bank(self, partner_id: int) -> dict | None:
        banks = self._reader.call("res.partner.bank", "search_read", [[["partner_id", "=", partner_id]]],
                                  fields=["acc_number", "acc_holder_name", "create_date"], order="create_date desc, id desc",
                                  limit=1)
        return banks[0] if banks else None

    def vendor(self, tenant, vendor_id):
        if not self._mine(tenant) or not str(vendor_id).isdigit():
            return None
        found = self._reader.call("res.partner", "search_read",
                                  [[["id", "=", int(vendor_id)], ["supplier_rank", ">", 0]]], fields=["name", "phone"])
        if not found:
            return None
        bank = self._current_bank(int(vendor_id))
        if bank is None:
            return None
        return {"name": found[0]["name"], "phone": found[0]["phone"] or "", "iban": norm_iban(bank["acc_number"]),
                "iban_changed_at": _epoch(bank["create_date"]), "holder": bank["acc_holder_name"] or found[0]["name"]}

    def vendor_for_iban(self, tenant, iban):
        if not self._mine(tenant):
            return None
        try:
            n = norm_iban(iban)
        except ValueError:
            return None
        banks = self._reader.call("res.partner.bank", "search_read", [[["sanitized_acc_number", "=", n]]],
                                  fields=["partner_id"])
        for b in banks:   # the account must be the vendor's CURRENT one, not an old or superseded record
            vid = str(b["partner_id"][0])
            v = self.vendor(tenant, vid)
            if v and v["iban"] == n:
                return vid
        return None

    def _bill(self, ref: str) -> dict | None:
        bills = self._reader.call("account.move", "search_read",
                                  [[["move_type", "=", "in_invoice"], ["ref", "=", ref], ["state", "=", "posted"]]],
                                  fields=["partner_id", "amount_residual", "currency_id", "payment_state"], limit=2)
        return bills[0] if len(bills) == 1 else None   # an ambiguous reference resolves to nothing

    def invoice(self, tenant, ref):
        if not self._mine(tenant) or not isinstance(ref, str):
            return None
        b = self._bill(ref)
        if b is None:
            return None
        return {"vendor_id": str(b["partner_id"][0]), "amount_cents": round(b["amount_residual"] * 100),
                "currency": b["currency_id"][1], "status": "open" if b["payment_state"] == "not_paid" else b["payment_state"]}

    def callback_phone(self, tenant, vendor_id, iban):
        return self._callbacks.get(f"{tenant}|{vendor_id}|{norm_iban(iban)}")

    def vop_match(self, tenant, vendor_name, iban):
        vid = self.vendor_for_iban(tenant, iban)
        v = self.vendor(tenant, vid) if vid else None
        return bool(v) and v["holder"].strip().casefold() == vendor_name.strip().casefold()

    def is_sanctioned(self, iban):
        return norm_iban(iban) in self.sanctioned

    def now(self) -> float:
        return time.time()

    # -- ERPSource: the two writes, made by the gateway's executors only --------------------------------------
    def record_payment(self, tenant, values):
        b = self._bill(values["invoice_ref"])
        partner_id = b["partner_id"][0]
        bank = self._reader.call("res.partner.bank", "search", [[["partner_id", "=", partner_id],
                                                                  ["sanitized_acc_number", "=", values["payee_iban"]]]])
        ctx = {"active_model": "account.move", "active_ids": [b["id"]]}
        wizard = self._payer.call("account.payment.register", "create",
                                  [{"partner_bank_id": bank[0], "amount": values["amount_cents"] / 100}], context=ctx)
        self._payer.call("account.payment.register", "action_create_payments", [[wizard]], context=ctx)
        self.payments.append({**values, "tenant": tenant})
        return f"Odoo: payment of {values['amount_cents'] / 100:.2f} {values['currency']} registered on {values['invoice_ref']}"

    def set_vendor_iban(self, tenant, vendor_id, iban):
        if self._bank_admin is None:
            raise PermissionError("no bank_admin credential: vendor bank details cannot be changed from intentplane")
        name = self.vendor(tenant, vendor_id)["name"]
        self._bank_admin.call("res.partner.bank", "create", [{"partner_id": int(vendor_id), "acc_number": iban,
                                                               "acc_holder_name": name}])
        return f"Odoo: new bank account recorded for vendor {vendor_id}"
