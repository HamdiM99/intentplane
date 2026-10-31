"""The Odoo connector against a fake Odoo (no server needed): what it reads, what it refuses to resolve, and that a
payment goes to the bank account the gateway resolved, not to the one written on the bill."""
import time

import pytest

from intentplane import Approvals, Authority, Gateway, Proposal, Status
from intentplane.connectors import odoo
from intentplane.packs import accounts_payable as ap

ACME_IBAN, OTHER_IBAN, EMAIL_IBAN = "FR7630006000011234567890189", "DE89370400440532013000", "LT601010012345678901"
OLD = time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime(time.time() - 400 * 86400))


class FakeOdoo:
    """Just enough of Odoo's ORM for the connector: search / search_read with '=' and '>' domains, create, and the
    payment wizard. Records which identity made each write."""

    def __init__(self):
        self.tables = {
            "res.partner": [{"id": 7, "name": "ACME Supplies", "phone": "+33 1 40 00 00 00", "supplier_rank": 1}],
            "res.partner.bank": [
                {"id": 1, "partner_id": [7, "ACME Supplies"], "acc_number": "FR76 3000 6000 0112 3456 7890 189",
                 "sanitized_acc_number": ACME_IBAN, "acc_holder_name": "ACME Supplies", "create_date": OLD},
            ],
            "account.move": [
                {"id": 50, "move_type": "in_invoice", "ref": "INV-2291", "state": "posted", "partner_id": [7, "ACME"],
                 "amount_residual": 1240.0, "currency_id": [1, "EUR"], "payment_state": "not_paid",
                 "partner_bank_id": 99},   # the bill itself points to some other account
                {"id": 51, "move_type": "in_invoice", "ref": "DUP", "state": "posted", "partner_id": [7, "ACME"],
                 "amount_residual": 10.0, "currency_id": [1, "EUR"], "payment_state": "not_paid"},
                {"id": 52, "move_type": "in_invoice", "ref": "DUP", "state": "posted", "partner_id": [7, "ACME"],
                 "amount_residual": 10.0, "currency_id": [1, "EUR"], "payment_state": "not_paid"},
            ],
        }
        self.writes, self.wizards = [], {}

    def match(self, rec, domain):
        for field, op, value in domain:
            v = rec.get(field)
            v = v[0] if isinstance(v, list) else v
            if (op == "=" and v != value) or (op == ">" and not (v or 0) > value):
                return False
        return True

    def call(self, who, model, method, args, **kw):
        if method in ("search", "search_read"):
            rows = [r for r in self.tables[model] if self.match(r, args[0])]
            if kw.get("order", "").startswith("create_date desc"):
                rows.sort(key=lambda r: (r["create_date"], r["id"]), reverse=True)
            rows = rows[: kw["limit"]] if kw.get("limit") else rows
            return [r["id"] for r in rows] if method == "search" else rows
        self.writes.append((who, model, method))
        if model == "account.payment.register" and method == "create":
            self.wizards[len(self.wizards) + 1] = (args[0], kw["context"])
            return len(self.wizards)
        if model == "account.payment.register" and method == "action_create_payments":
            values, ctx = self.wizards[args[0][0]]
            bill = next(r for r in self.tables["account.move"] if r["id"] == ctx["active_ids"][0])
            bill["payment_state"], bill["paid_to_bank"] = "paid", values["partner_bank_id"]
            return True
        if model == "res.partner.bank" and method == "create":
            row = dict(args[0], id=len(self.tables[model]) + 10, partner_id=[args[0]["partner_id"], ""],
                       sanitized_acc_number=ap.norm_iban(args[0]["acc_number"]),
                       create_date=time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime()))
            self.tables[model].append(row)
            return row["id"]
        raise AssertionError(f"unexpected call {model}.{method}")


@pytest.fixture()
def world(monkeypatch, tmp_path):
    fake = FakeOdoo()

    class Session:
        def __init__(self, url, db, login, password):
            self.login = login

        def call(self, model, method, args, **kw):
            return fake.call(self.login, model, method, args, **kw)

    monkeypatch.setattr(odoo, "_Session", Session)
    erp = odoo.OdooERP("http://odoo", "db", tenant="acme-buyer", reader=("reader", "x"), payer=("payer", "x"),
                       bank_admin=("bank-admin", "x"), callbacks_file=tmp_path / "callbacks.json")
    return fake, erp


def test_reads_the_vendor_and_its_current_bank_account(world):
    _, erp = world
    v = erp.vendor("acme-buyer", "7")
    assert v["iban"] == ACME_IBAN and v["name"] == "ACME Supplies"
    assert (erp.now() - v["iban_changed_at"]) // ap.DAY >= 399
    assert erp.vendor_for_iban("acme-buyer", "fr76 3000 6000 0112 3456 7890 189") == "7"
    assert erp.vendor_for_iban("acme-buyer", EMAIL_IBAN) is None


def test_other_tenants_and_ambiguous_references_resolve_to_nothing(world):
    _, erp = world
    assert erp.vendor("other-company", "7") is None and erp.invoice("other-company", "INV-2291") is None
    assert erp.invoice("acme-buyer", "DUP") is None          # two bills with that reference: not trusted
    assert erp.invoice("acme-buyer", "INV-2291") == {"vendor_id": "7", "amount_cents": 124000, "currency": "EUR",
                                                     "status": "open"}


def _gateway(erp):
    auth = Authority()
    gw = Gateway([ap.build(erp)], auth, Approvals({"cfo": b"k"}))
    token = auth.issue("marie", {"invoice.pay", "vendor.update_bank_details"},
                       {"tenant": "acme-buyer", "source_account": "FR1420041010050500013M02606", "currency": "EUR"},
                       budget=5_000_000)
    return gw, token


def _pay(payee):
    return Proposal("invoice.pay", {"source_account": "FR1420041010050500013M02606", "invoice_ref": "INV-2291",
                                    "payee_iban": payee, "amount_cents": 124000, "currency": "EUR"})


def test_the_email_iban_is_refused_and_odoo_is_never_written(world):
    fake, erp = world
    gw, token = _gateway(erp)
    o = gw.submit(token, _pay(EMAIL_IBAN))
    assert o.status is Status.DENIED and o.decision.stage == "provenance" and fake.writes == []


def test_a_payment_goes_to_the_resolved_account_with_the_payer_identity_only(world):
    fake, erp = world
    gw, token = _gateway(erp)
    o = gw.submit(token, _pay("FR76 3000 6000 0112 3456 7890 189"))
    assert o.status is Status.EXECUTED
    bill = next(r for r in fake.tables["account.move"] if r["id"] == 50)
    assert bill["paid_to_bank"] == 1                      # ACME's resolved account, not the bill's own (99)
    assert {who for who, _, _ in fake.writes} == {"payer"}
    assert gw.submit(token, _pay(ACME_IBAN)).status is Status.DENIED   # paid once


def test_only_the_bank_admin_identity_can_change_bank_details_and_only_after_the_procedure(world):
    fake, erp = world
    gw, token = _gateway(erp)
    update = Proposal("vendor.update_bank_details", {"vendor_id": "7", "new_iban": EMAIL_IBAN, "reason": "email"})
    assert gw.submit(token, update).status is Status.DENIED        # no call-back on the number on file
    erp.log_callback("acme-buyer", "7", EMAIL_IBAN, "+33 1 40 00 00 00")
    pending = gw.submit(token, update)
    assert pending.status is Status.PENDING                       # four eyes
    approvals = gw.approvals
    assert gw.submit(token, update, (approvals.approve("cfo", pending.op_hash),)).status is Status.EXECUTED
    assert [w for w in fake.writes if w[1] == "res.partner.bank"] == [("bank-admin", "res.partner.bank", "create")]
    assert erp.vendor("acme-buyer", "7")["iban"] == EMAIL_IBAN     # the new, current account (cooling-off applies)
