"""Regression tests for the v0.1 design flaw: the agent could write its own trust labels, identity and
context. Each of the 7 attacks that succeeded against v0.1 must now fail, plus the AZT guarantees
(budget, hash-bound single-use approval, fail closed) that v0.1 had lost."""
from conftest import ACME, ALICE_ACC, ATTACKER, BOBS_FRIEND, GREEN, NORTH, RED

from intentplane import Effect, Origin, Proposal, RulePDP, Status, Tagged
from intentplane.core import ActionContract, Gateway, Pack, Param


def pay(payee, cents=25_000, **over):
    args = {"source_account": ALICE_ACC, "payee_iban": payee, "amount_cents": cents, "currency": "EUR",
            "reference": "Invoice 42"}
    args.update(over)
    return Proposal("payment.transfer", args)


# --- attack 1 & 2: forged provenance ---------------------------------------------------------
def test_1_attacker_iban_has_no_trusted_source(w):
    o = w.gw.submit(w.bank_token(), pay(ATTACKER))
    assert o.status is Status.DENIED and o.decision.stage == "provenance" and w.transfers == []


def test_1_agent_cannot_send_a_label(w):
    for forged in (Tagged(ATTACKER, Origin.SYSTEM_OF_RECORD, "beneficiaries/42"),
                   {"value": ATTACKER, "origin": "system_of_record"}):
        assert w.gw.submit(w.bank_token(), pay(forged)).status is Status.DENIED
    o = w.gw.submit(w.bank_token(), pay(ATTACKER, origin="system_of_record"))
    assert o.status is Status.DENIED and o.decision.stage == "contract"
    assert w.transfers == []


def test_2_ref_is_resolved_by_the_gateway_not_trusted(w):
    o = w.gw.submit(w.bank_token(), pay({"ref": "42"}))   # the gateway reads ACME from the registry
    assert o.status is Status.EXECUTED and w.transfers[0]["payee_iban"] == ACME
    for bad in ({"ref": "7"}, {"ref": "nope"}, {"ref": "42", "value": ATTACKER}):  # bob's / unknown / smuggled
        assert w.gw.submit(w.bank_token(), pay(bad)).status is Status.DENIED
    assert len(w.transfers) == 1


def test_2_other_users_beneficiary_is_not_mine(w):
    o = w.gw.submit(w.bank_token(), pay(BOBS_FRIEND))
    assert o.status is Status.DENIED and o.decision.stage == "provenance"


# --- attack 3: identity and scopes come from the signed token only ---------------------------
def test_3_scopes_come_from_the_token(w):
    o = w.gw.submit(w.bank_token(principal="bob", scopes={"ehr.read_record"}), pay({"ref": "7"}))
    assert o.status is Status.DENIED
    payload, sig = w.bank_token(scopes=set()).rsplit(".", 1)
    forged = payload.replace('"scopes":[]', '"scopes":["payment.transfer"]') + "." + sig
    o = w.gw.submit(forged, pay(ACME))
    assert o.status is Status.DENIED and o.decision.stage == "authn" and w.transfers == []


def test_3_expired_and_revoked_tokens(w):
    assert w.gw.submit(w.bank_token(ttl=-1), pay(ACME)).decision.stage == "authn"
    tok = w.bank_token()
    w.auth.revoke("alice")
    assert w.gw.submit(tok, pay(ACME)).decision.stage == "authn"


# --- attack 4: sanctions are read by the gateway ---------------------------------------------
def test_4_sanctions_come_from_the_gateway(w):
    o = w.gw.submit(w.bank_token(), pay(NORTH))            # registered, but sanctioned country
    assert o.status is Status.DENIED and "forbid-sanctioned-payee" in o.decision.reasons
    o = w.gw.submit(w.bank_token(), pay(NORTH, payee_sanctioned=False))   # there is no such field
    assert o.status is Status.DENIED and o.decision.stage == "contract" and w.transfers == []


# --- attack 5: cumulative budget per mandate --------------------------------------------------
def test_5_repeated_payments_are_capped_by_the_mandate_budget(w):
    tok = w.bank_token(budget=100_000)                     # 1,000.00 for this task
    statuses = [w.gw.submit(tok, pay(ACME, cents=99_900)).status for _ in range(50)]
    assert statuses.count(Status.EXECUTED) == 1
    assert sum(t["amount_cents"] for t in w.transfers) <= 100_000


# --- attack 6: CI status and review state are read by the gateway ----------------------------
def merge(sha, branch="main"):
    return Proposal("repo.merge", {"repo": "org/app", "head_sha": sha, "target_branch": branch, "message": "m"})


def test_6_red_ci_and_unreviewed_protected_branch(w):
    tok = w.auth.issue("dev_alice", {"repo.merge"}, {"repo": "org/app"})
    assert w.gw.submit(tok, merge(RED)).status is Status.DENIED               # CI failed, whatever the agent says
    pending = w.gw.submit(tok, merge(GREEN))
    assert pending.status is Status.PENDING and pending.decision.effect is Effect.FOUR_EYES
    self_ok = w.approvals.approve("dev_alice", pending.op_hash)
    assert w.gw.submit(tok, merge(GREEN), (self_ok,)).status is Status.PENDING  # cannot be your own 2nd pair
    peer = w.approvals.approve("dev_bob", pending.op_hash)
    done = w.gw.submit(tok, merge(GREEN), (peer,))
    assert done.status is Status.EXECUTED and done.approvers == ("dev_bob",) and len(w.merges) == 1


# --- attack 7: consent is read by the gateway ------------------------------------------------
def read(patient, section="labs"):
    return Proposal("ehr.read_record", {"patient_id": patient, "section": section, "purpose": "research"})


def test_7_research_needs_consent_on_file(w):
    tok = w.auth.issue("dr_alice", {"ehr.read_record"}, {"purpose": "research"})
    assert w.gw.submit(tok, read("P-1234")).status is Status.DENIED            # no consent on file
    assert w.gw.submit(tok, read("P-5555")).status is Status.EXECUTED          # consent on file
    assert w.gw.submit(tok, read("P-9999")).decision.stage == "provenance"     # not in an open encounter
    assert len(w.reads) == 1


# --- approvals: bound to the operation, single use, the right human ---------------------------
def test_step_up_is_bound_to_the_exact_operation_and_single_use(w):
    tok = w.bank_token(budget=1_000_000)
    pending = w.gw.submit(tok, pay(ACME, cents=500_000))
    assert pending.status is Status.PENDING and pending.decision.effect is Effect.STEP_UP
    a = w.approvals.approve("alice", pending.op_hash)
    assert w.gw.submit(tok, pay(ACME, cents=600_000), (a,)).status is Status.PENDING   # different amount
    assert w.gw.submit(tok, pay(ACME, cents=500_000), (w.approvals.approve("bob", pending.op_hash),)).status \
        is Status.PENDING                                                            # step-up is the principal
    assert w.gw.submit(tok, pay(ACME, cents=500_000), (a,)).status is Status.EXECUTED
    assert w.gw.submit(tok, pay(ACME, cents=500_000), (a,)).status is Status.PENDING  # replay


def test_hard_cap_holds_even_with_approval(w):
    tok = w.bank_token(budget=10_000_000)
    o = w.gw.submit(tok, pay(ACME, cents=2_000_000))
    assert o.status is Status.DENIED and "forbid-hard-cap" in o.decision.reasons


# --- contract and fail-closed -------------------------------------------------------------------
def test_contract_violations(w):
    tok = w.bank_token()
    for p in (Proposal("shell.exec", {"cmd": "rm -rf /"}), pay(ACME, cents=250.5), pay(ACME, cents=True),
              pay(ACME, cents=-5), pay(ACME.replace("F", "Г", 1)), pay(ACME, currency="USD"),
              Proposal("payment.transfer", {"payee_iban": ACME})):
        assert w.gw.submit(tok, p).status is Status.DENIED, p
    assert w.transfers == []


def test_fail_closed_on_errors(w):
    boom = Pack("boom", [ActionContract("x.do", "x", (Param("a", str),))],
                RulePDP("p", [lambda i, c: 1 / 0]), executors={"x.do": lambda v, m: "done"})
    gw = Gateway([boom], w.auth)
    o = gw.submit(w.auth.issue("alice", {"x.do"}), Proposal("x.do", {"a": "y"}))
    assert o.status is Status.DENIED and o.decision.stage == "error"


def test_default_deny_without_a_permit(w):
    quiet = Pack("quiet", [ActionContract("x.do", "x", (Param("a", str),))], RulePDP("p", []),
                 executors={"x.do": lambda v, m: "done"})
    gw = Gateway([quiet], w.auth)
    o = gw.submit(w.auth.issue("alice", {"x.do"}), Proposal("x.do", {"a": "y"}))
    assert o.status is Status.DENIED and "no policy permits this action" in o.decision.reasons


def test_failed_execution_does_not_consume_budget(w):
    calls = []

    def flaky(values, mandate):
        calls.append(1)
        if len(calls) == 1:
            raise RuntimeError("core banking timeout")
        return "ok"

    w.gw._pack_of["payment.transfer"].executors["payment.transfer"] = flaky
    tok = w.bank_token(budget=30_000)
    assert w.gw.submit(tok, pay(ACME, cents=25_000)).decision.stage == "error"
    assert w.gw.submit(tok, pay(ACME, cents=25_000)).status is Status.EXECUTED


def test_receipts_are_chained_and_record_trusted_provenance(w):
    w.gw.submit(w.bank_token(), pay(ATTACKER))
    w.gw.submit(w.bank_token(), pay({"ref": "42"}))
    assert w.gw.ledger.verify() and len(w.gw.ledger) == 2
    last = w.gw.ledger.entries[-1]["data"]["intent"]
    assert last["provenance"]["payee_iban"] == ["system_of_record", "beneficiaries/42"]
    assert last["provenance"]["source_account"] == ["user_channel", "mandate:source_account"]
    w.gw.ledger.entries[0]["data"]["status"] = "EXECUTED"
    assert not w.gw.ledger.verify()
