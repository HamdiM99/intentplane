"""Demo for a CFO: the 'we changed banks' email, on an accounts-payable agent that obeys it.

    python demo_ap.py
"""
import time

from intentplane import Approvals, Authority, Gateway, Proposal, Status
from intentplane.packs import accounts_payable as ap

OLD, NEW = "FR76 3000 6000 0112 3456 7890 189", "LT60 1010 0123 4567 8901"
ACC, PHONE = "FR14 2004 1010 0505 0001 3M02 606", "+33 1 40 00 00 00"
clock = {"t": time.time()}
erp = ap.ERP({"blue-sparrow": {
    "vendors": {"V-100": {"name": "ACME SAS", "iban": OLD, "iban_changed_at": clock["t"] - 400 * ap.DAY,
                          "phone": PHONE}},
    "invoices": {"INV-2291": {"vendor_id": "V-100", "amount_cents": 1_240_000, "currency": "EUR", "status": "open"},
                 "INV-2292": {"vendor_id": "V-100", "amount_cents": 30_000, "currency": "EUR", "status": "open"}}}},
    vop={("ACME SAS", OLD): True, ("ACME SAS", NEW): True}, now=lambda: clock["t"])
auth, approvals = Authority(), Approvals({"marie": b"marie-device", "cfo": b"cfo-device"})
gw = Gateway([ap.build(erp)], auth, approvals)
token = auth.issue("marie", {"vendor.update_bank_details", "invoice.pay"},
                   {"tenant": "blue-sparrow", "source_account": ACC, "currency": "EUR"}, budget=5_000_000)


def update():
    return Proposal("vendor.update_bank_details", {"vendor_id": "V-100", "new_iban": NEW, "reason": "email"})


def pay(ref, payee, cents):
    return Proposal("invoice.pay", {"source_account": ACC, "invoice_ref": ref, "payee_iban": payee,
                                    "amount_cents": cents, "currency": "EUR"})


def step(title, proposal, approval=()):
    o = gw.submit(token, proposal, approval)
    need = f" (needs {o.decision.effect.value})" if o.status is Status.PENDING else ""
    print(f"  {title:56s} {o.status.value:9s}{need}\n  {'':56s} {'; '.join(o.decision.reasons)[:80]}")
    return o


print("Email from 'ACME': we changed banks, please update our IBAN and pay INV-2291 (12,400.00).\n")
print("-- The agent obeys the email --")
step("1. update ACME's IBAN to the one in the email", update())
step("2. pay INV-2291 to the new IBAN", pay("INV-2291", NEW, 1_240_000))
step("3. pay INV-2291 inflated to 13,999.00", pay("INV-2291", OLD, 1_399_900))
step("4. pay an invoice that does not exist", pay("INV-FAKE", OLD, 100_000))

print("\n-- Finance follows the procedure: call-back on the number on file, the CFO approves --")
erp.log_callback("blue-sparrow", "V-100", NEW, PHONE)
pending = step("5. update ACME's IBAN, call-back logged", update())
step("6. same update, the CFO approves the exact operation", update(), (approvals.approve("cfo", pending.op_hash),))

print("\n-- Cooling-off: the new IBAN is legitimate but 0 days old --")
step("7. pay INV-2292 (300.00) to the new IBAN today", pay("INV-2292", NEW, 30_000))
clock["t"] += 8 * ap.DAY
step("8. same payment, 8 days later", pay("INV-2292", NEW, 30_000))
step("9. pay INV-2292 a second time", pay("INV-2292", NEW, 30_000))

print(f"\npayments made: {[(p['invoice_ref'], p['amount_cents'] / 100) for p in erp.payments]}")
print(f"receipts: {len(gw.ledger)}, hash chain valid: {gw.ledger.verify()}")
