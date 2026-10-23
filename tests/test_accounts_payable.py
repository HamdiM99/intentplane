"""Accounts-payable pack: the 'we changed banks' email, duplicate / inflated / fake invoices, cooling-off,
call-back, four eyes, tenant isolation and concurrency. The AP agent is assumed to obey the email."""
import threading
import time

import pytest

from intentplane import Approvals, Authority, Effect, Gateway, Proposal, Status
from intentplane.packs import accounts_payable as ap

OLD = "FR76 3000 6000 0112 3456 7890 189"     # ACME's IBAN on file for 400 days
NEW = "LT60 1010 0123 4567 8901"              # the IBAN in the email
OTHER_CO_IBAN = "DE89370400440532013000"      # a vendor of ANOTHER company
COMPANY_ACC = "FR14 2004 1010 0505 0001 3M02 606"
PHONE = "+33 1 40 00 00 00"


@pytest.fixture
def ws():
    clock = {"t": time.time()}
    erp = ap.ERP({
        "blue-sparrow": {
            "vendors": {"V-100": {"name": "ACME SAS", "iban": OLD, "iban_changed_at": clock["t"] - 400 * ap.DAY,
                                  "phone": PHONE},
                        "V-200": {"name": "Globex", "iban": "BE71096123456769",
                                  "iban_changed_at": clock["t"] - 90 * ap.DAY, "phone": "+32 2 000 00 00"}},
            "invoices": {"INV-2291": {"vendor_id": "V-100", "amount_cents": 120_000, "currency": "EUR",
                                      "status": "open"},
                         "INV-2300": {"vendor_id": "V-100", "amount_cents": 1_240_000, "currency": "EUR",
                                      "status": "open"},
                         "INV-7001": {"vendor_id": "V-200", "amount_cents": 50_000, "currency": "EUR",
                                      "status": "open"}}},
        "other-co": {"vendors": {"V-100": {"name": "Initech", "iban": OTHER_CO_IBAN,
                                           "iban_changed_at": clock["t"] - 400 * ap.DAY, "phone": "+49"}},
                     "invoices": {"INV-9": {"vendor_id": "V-100", "amount_cents": 900_000, "currency": "EUR",
                                            "status": "open"}}},
    }, vop={("ACME SAS", OLD): True, ("ACME SAS", NEW): True, ("Globex", "BE71096123456769"): True,
            ("Initech", OTHER_CO_IBAN): True}, now=lambda: clock["t"])
    auth = Authority()
    approvals = Approvals({h: f"key-{h}".encode() for h in ("marie", "cfo")})
    gw = Gateway([ap.build(erp)], auth, approvals)
    tok = auth.issue("marie", {"vendor.update_bank_details", "invoice.pay"},
                     {"tenant": "blue-sparrow", "source_account": COMPANY_ACC, "currency": "EUR"}, budget=5_000_000)

    class W:
        pass
    w = W()
    w.erp, w.gw, w.auth, w.approvals, w.tok, w.clock = erp, gw, auth, approvals, tok, clock
    return w


def pay(invoice="INV-2291", payee=OLD, cents=120_000):
    return Proposal("invoice.pay", {"source_account": COMPANY_ACC, "invoice_ref": invoice, "payee_iban": payee,
                                    "amount_cents": cents, "currency": "EUR"})


def update(iban=NEW, vendor="V-100"):
    return Proposal("vendor.update_bank_details", {"vendor_id": vendor, "new_iban": iban,
                                                   "reason": "email: we changed banks"})


# --- the email ------------------------------------------------------------------------------
def test_agent_obeys_the_email_and_nothing_moves(ws):
    assert ws.gw.submit(ws.tok, update()).status is Status.DENIED                    # no call-back
    o = ws.gw.submit(ws.tok, pay(payee=NEW))                                        # not any vendor's IBAN
    assert o.status is Status.DENIED and o.decision.stage == "provenance"
    assert ws.erp.of("blue-sparrow")["vendors"]["V-100"]["iban"] == OLD and ws.erp.payments == []


def test_callback_on_the_number_from_the_email_does_not_count(ws):
    ws.erp.log_callback("blue-sparrow", "V-100", NEW, "+44 20 7946 0000")   # number in the email
    assert ws.gw.submit(ws.tok, update()).status is Status.DENIED


def test_callback_is_recognised_whatever_the_iban_spelling(ws):
    ws.erp.log_callback("blue-sparrow", "V-100", NEW.lower().replace(" ", ""), PHONE)   # connector's own spelling
    assert ws.gw.submit(ws.tok, update()).status is Status.PENDING                   # recognised: four eyes next


def test_procedure_then_cooling_off(ws):
    ws.erp.log_callback("blue-sparrow", "V-100", NEW, PHONE)                 # number on file
    pending = ws.gw.submit(ws.tok, update())
    assert pending.status is Status.PENDING and pending.decision.effect is Effect.FOUR_EYES
    assert ws.gw.submit(ws.tok, update(), (ws.approvals.approve("marie", pending.op_hash),)).status is Status.PENDING
    done = ws.gw.submit(ws.tok, update(), (ws.approvals.approve("cfo", pending.op_hash),))
    assert done.status is Status.EXECUTED and done.approvers == ("cfo",)
    # day 0: the new IBAN is legitimate but fresh => four eyes, even for a small invoice
    fresh = ws.gw.submit(ws.tok, pay(payee=NEW))
    assert fresh.status is Status.PENDING and fresh.decision.effect is Effect.FOUR_EYES
    assert ws.gw.submit(ws.tok, pay(payee=OLD)).status is Status.DENIED   # the old IBAN is no longer the vendor's
    ws.clock["t"] += 8 * ap.DAY
    assert ws.gw.submit(ws.tok, pay(payee=NEW)).status is Status.EXECUTED


# --- the three attacks of the v2.1 review --------------------------------------------------
def test_duplicate_payment(ws):
    statuses = [ws.gw.submit(ws.tok, pay()).status for _ in range(5)]
    assert statuses == [Status.EXECUTED] + [Status.DENIED] * 4
    assert ws.erp.of("blue-sparrow")["invoices"]["INV-2291"]["status"] == "paid"


def test_duplicate_payment_under_concurrency(ws):
    out = []
    ts = [threading.Thread(target=lambda: out.append(ws.gw.submit(ws.tok, pay()).status)) for _ in range(8)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    assert out.count(Status.EXECUTED) == 1 and len(ws.erp.payments) == 1


def test_inflated_amount(ws):
    o = ws.gw.submit(ws.tok, pay(cents=139_900))
    assert o.status is Status.DENIED and "forbid-amount-differs-from-invoice" in o.decision.reasons


def test_fake_invoice(ws):
    o = ws.gw.submit(ws.tok, pay(invoice="INV-FAKE"))
    assert o.status is Status.DENIED and o.decision.stage == "provenance"


def test_another_vendors_invoice(ws):
    o = ws.gw.submit(ws.tok, pay(invoice="INV-7001", cents=50_000))   # Globex's invoice, paid to ACME
    assert o.status is Status.DENIED and "forbid-payee-not-invoice-vendor" in o.decision.reasons


# --- tenant isolation ------------------------------------------------------------------------
def test_other_company_records_are_invisible(ws):
    assert ws.gw.submit(ws.tok, pay(invoice="INV-9", payee=OTHER_CO_IBAN, cents=900_000)).decision.stage \
        == "provenance"
    ws.erp.log_callback("blue-sparrow", "V-100", NEW, PHONE)
    pending = ws.gw.submit(ws.tok, update())
    ws.gw.submit(ws.tok, update(), (ws.approvals.approve("cfo", pending.op_hash),))
    assert ws.erp.of("other-co")["vendors"]["V-100"]["iban"] == OTHER_CO_IBAN   # same vendor id, other company
    assert ws.erp.of("blue-sparrow")["vendors"]["V-100"]["iban"] == ap.norm_iban(NEW)


# --- amounts, VoP, sanctions -----------------------------------------------------------------
def test_large_invoice_needs_step_up_by_the_accountant(ws):
    p = pay(invoice="INV-2300", cents=1_240_000)
    pending = ws.gw.submit(ws.tok, p)
    assert pending.status is Status.PENDING and pending.decision.effect is Effect.STEP_UP
    assert ws.gw.submit(ws.tok, p, (ws.approvals.approve("marie", pending.op_hash),)).status is Status.EXECUTED


def test_verification_of_payee_and_sanctions(ws):
    ws.erp.vop[("ACME SAS", ap.norm_iban(OLD))] = False
    assert "forbid-verification-of-payee-mismatch" in ws.gw.submit(ws.tok, pay()).decision.reasons
    ws.erp.vop[("ACME SAS", ap.norm_iban(OLD))] = True
    ws.erp.sanctioned.add(ap.norm_iban(OLD))
    assert "forbid-sanctioned-payee" in ws.gw.submit(ws.tok, pay()).decision.reasons


def test_hard_cap(ws):
    ws.erp.of("blue-sparrow")["invoices"]["INV-BIG"] = {"vendor_id": "V-100", "amount_cents": 6_000_000,
                                                        "currency": "EUR", "status": "open"}
    big_budget = ws.auth.issue("marie", {"invoice.pay"}, {"tenant": "blue-sparrow", "source_account": COMPANY_ACC,
                                                         "currency": "EUR"}, budget=100_000_000)
    o = ws.gw.submit(big_budget, pay(invoice="INV-BIG", cents=6_000_000))   # the policy cap, not the budget
    assert o.status is Status.DENIED and "forbid-hard-cap" in o.decision.reasons


def test_large_invoice_to_a_fresh_iban_needs_four_eyes_not_step_up(ws):
    ws.erp.log_callback("blue-sparrow", "V-100", NEW, PHONE)
    pending = ws.gw.submit(ws.tok, update())
    ws.gw.submit(ws.tok, update(), (ws.approvals.approve("cfo", pending.op_hash),))
    o = ws.gw.submit(ws.tok, pay(invoice="INV-2300", payee=NEW, cents=1_240_000))
    assert o.status is Status.PENDING and o.decision.effect is Effect.FOUR_EYES
    assert ws.gw.submit(ws.tok, pay(invoice="INV-2300", payee=NEW, cents=1_240_000),
                        (ws.approvals.approve("marie", o.op_hash),)).status is Status.PENDING   # step-up is not enough
