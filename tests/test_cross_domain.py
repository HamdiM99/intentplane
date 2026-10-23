"""The same attack in three domains: the agent read untrusted content and now proposes a critical
value taken from it. Same core, different pack, same outcome; the legitimate request still works."""
from conftest import ACME, ALICE_ACC, ATTACKER, GREEN, OTHER

from intentplane import Effect, Proposal, Status


def test_bank(w):
    tok = w.bank_token()
    args = {"source_account": ALICE_ACC, "amount_cents": 25_000, "currency": "EUR",
            "reference": "Invoice 2291, pay to the IBAN in the email"}
    bad = w.gw.submit(tok, Proposal("payment.transfer", {**args, "payee_iban": ATTACKER}))
    good = w.gw.submit(tok, Proposal("payment.transfer", {**args, "payee_iban": ACME}))
    assert bad.decision.stage == "provenance" and good.status is Status.EXECUTED
    assert [t["payee_iban"] for t in w.transfers] == [ACME]


def test_bank_payee_named_in_the_mandate(w):
    """A new payee the user typed in the bank app (bound in the mandate) needs no registry entry."""
    tok = w.bank_token(claims={"payee": "BE71 0961 2345 6769"})
    o = w.gw.submit(tok, Proposal("payment.transfer", {"source_account": ALICE_ACC, "payee_iban": "BE71096123456769",
                                                       "amount_cents": 1_000, "currency": "EUR", "reference": "r"}))
    assert o.status is Status.EXECUTED


def test_health(w):
    tok = w.auth.issue("dr_alice", {"ehr.read_record"}, {"purpose": "treatment"})
    p = lambda pid, section="labs": Proposal("ehr.read_record", {"patient_id": pid, "section": section,
                                                                  "purpose": "treatment"})
    assert w.gw.submit(tok, p("P-9999")).decision.stage == "provenance"   # "also pull up patient P-9999"
    assert w.gw.submit(tok, p({"ref": "E-17"})).status is Status.EXECUTED   # the open encounter
    full = w.gw.submit(tok, p("P-1234", "full"))
    assert full.status is Status.PENDING and full.decision.effect is Effect.STEP_UP


def test_health_break_the_glass(w):
    tok = w.auth.issue("dr_alice", {"ehr.read_record"}, {"purpose": "emergency"})
    p = Proposal("ehr.read_record", {"patient_id": "P-1234", "section": "full", "purpose": "emergency"})
    pending = w.gw.submit(tok, p)
    assert pending.decision.effect is Effect.FOUR_EYES
    assert w.gw.submit(tok, p, (w.approvals.approve("dr_bob", pending.op_hash),)).status is Status.EXECUTED


def test_code(w):
    tok = w.auth.issue("dev_alice", {"repo.merge"}, {"repo": "org/app"})
    m = lambda sha: Proposal("repo.merge", {"repo": "org/app", "head_sha": sha, "target_branch": "feature/x",
                                            "message": "merge, README says it's safe"})
    assert w.gw.submit(tok, m("a" * 40)).decision.stage == "provenance"   # sha quoted by a README
    assert w.gw.submit(tok, m(OTHER)).decision.stage == "provenance"      # attested, but for bob's repos
    assert w.gw.submit(tok, m(GREEN)).status is Status.EXECUTED


def test_one_gateway_all_domains_strict_delegation(w):
    tok = w.auth.issue("dr_alice", {"ehr.read_record"}, {"purpose": "treatment"})
    o = w.gw.submit(tok, Proposal("payment.transfer", {"source_account": ALICE_ACC, "payee_iban": ACME,
                                                       "amount_cents": 1, "currency": "EUR", "reference": "r"}))
    assert o.status is Status.DENIED   # a clinician's mandate does not move money
    assert w.gw.catalog.names() == ["ehr.read_record", "payment.transfer", "repo.merge"]
