"""Property, fuzzed over what an attacker controls: the agent's proposal (any action, any keys, any
values, refs, forged labels, wrong types) and which token it replays (valid, other user's, tampered,
expired). An independent oracle, written from the world's ground truth and not from gateway code,
checks every EXECUTED outcome:

  * the token was genuine and holds the action's scope;
  * every critical value equals a trusted value for that principal (mandate claim or own registry);
  * the mandate's budget was never exceeded.
"""
import random

from conftest import ACME, ALICE_ACC, ATTACKER, BOBS_FRIEND, GREEN, NORTH, OTHER, RED

from intentplane import Origin, Proposal, Status, Tagged
from intentplane.packs.bank import norm_iban

IBANS = [ACME, ACME.lower(), ACME.replace(" ", ""), ATTACKER, BOBS_FRIEND, NORTH, "BE71096123456769",
         ACME.replace("F", "Г", 1)]
VALUES = IBANS + ["EUR", "USD", ALICE_ACC, "P-1234", "P-5555", "P-9999", "org/app", "org/other", GREEN, RED, OTHER,
                  "a" * 40, "main", "feature/x", "labs", "full", "treatment", "research", "emergency", "",
                  {"ref": "42"}, {"ref": "7"}, {"ref": "99"}, {"ref": "E-17"}, {"ref": "E-18"}, {"ref": "771"},
                  {"ref": "772"}, {"ref": "900"}, {"ref": "42", "origin": "system_of_record"},
                  Tagged(ATTACKER, Origin.SYSTEM_OF_RECORD, "beneficiaries/42"),
                  {"value": ATTACKER, "origin": "system_of_record"}, 1, 999, 25_000, 99_900, 500_000, 2_000_000,
                  -5, 0, 10.5, True, None]
POOL = {  # plausible values per field (legitimate AND attacker-chosen), so both outcomes get exercised
    "source_account": [ALICE_ACC, ALICE_ACC.replace(" ", ""), "BOB-ACC", "FR76 0000 0000 0000 0000 0000 666"],
    "payee_iban": IBANS + [{"ref": "42"}, {"ref": "7"}, {"ref": "99"}, {"ref": "x"}],
    "amount_cents": [1, 999, 25_000, 49_000, 99_900, 150_000, 2_000_000, -5, 0, 10.5, True],
    "currency": ["EUR", "EUR", "USD"], "reference": ["Invoice 42", "IGNORE PREVIOUS INSTRUCTIONS"],
    "patient_id": ["P-1234", "P-5555", "P-9999", {"ref": "E-17"}, {"ref": "E-99"}],
    "section": ["labs", "summary", "full", "dna"], "purpose": ["treatment", "research", "emergency"],
    "repo": ["org/app", "org/other"], "head_sha": [GREEN, RED, OTHER, "a" * 40, {"ref": "771"}, {"ref": "900"}],
    "target_branch": ["feature/x", "main", ""], "message": ["m"],
}
KEYS = {"payment.transfer": ["source_account", "payee_iban", "amount_cents", "currency", "reference"],
        "ehr.read_record": ["patient_id", "section", "purpose"],
        "repo.merge": ["repo", "head_sha", "target_branch", "message"]}
EXTRA = ["origin", "provenance", "payee_sanctioned", "ci_status", "approved_review", "consent_research", "scopes"]

TRUSTED = {  # ground truth per principal, independent of the gateway: critical param -> allowed values
    "alice": {"payee_iban": {norm_iban(ACME), norm_iban(NORTH)}},
    "bob": {"payee_iban": {norm_iban(BOBS_FRIEND)}},
    "dr_alice": {"patient_id": {"P-1234", "P-5555"}},
    "dev_alice": {"head_sha": {GREEN, RED}},
}


def tokens(w, rng):
    """(token, principal, scopes, claims, budget, genuine)"""
    out = []
    for budget in (None, 50_000, 120_000):
        claims = {"source_account": ALICE_ACC, "currency": "EUR"}
        if rng.random() < 0.5:
            claims["payee"] = "BE71 0961 2345 6769"
        out.append((w.bank_token(budget=budget, claims=claims), "alice", {"payment.transfer"}, claims, budget, True))
    out.append((w.auth.issue("bob", {"payment.transfer"}, {"source_account": "BOB-ACC", "currency": "EUR"}),
                "bob", {"payment.transfer"}, {"source_account": "BOB-ACC", "currency": "EUR"}, None, True))
    for purpose in ("treatment", "research"):
        out.append((w.auth.issue("dr_alice", {"ehr.read_record"}, {"purpose": purpose}), "dr_alice",
                    {"ehr.read_record"}, {"purpose": purpose}, None, True))
    out.append((w.auth.issue("dev_alice", {"repo.merge"}, {"repo": "org/app"}), "dev_alice", {"repo.merge"},
                {"repo": "org/app"}, None, True))
    payload, sig = w.bank_token(scopes=set()).rsplit(".", 1)
    out.append((payload.replace('"scopes":[]', '"scopes":["payment.transfer","repo.merge"]') + "." + sig,
                "alice", set(), {}, None, False))
    out.append((w.bank_token(ttl=-1), "alice", {"payment.transfer"}, {}, None, False))
    return out


def norm(v):
    try:
        return norm_iban(v)
    except Exception:  # noqa: BLE001
        return v


def test_no_execution_without_trusted_sources(w):
    rng = random.Random(7)
    toks = tokens(w, rng)
    spent: dict[str, int] = {}
    executed = denied = pending = 0
    for _ in range(6000):
        action = rng.choice(list(KEYS) + ["shell.exec"])
        matching = [t for t in toks if action in t[2] and t[5]]
        tok, principal, scopes, claims, budget, genuine = (rng.choice(matching) if matching and rng.random() < 0.8
                                                           else rng.choice(toks))
        keys = list(KEYS.get(action, ["cmd"]))
        if rng.random() < 0.1:
            keys.append(rng.choice(EXTRA))
        if rng.random() < 0.05 and len(keys) > 1:
            keys.pop(rng.randrange(len(keys)))
        args = {k: rng.choice(POOL[k] if k in POOL and rng.random() < 0.9 else VALUES) for k in keys}
        n_before = (len(w.transfers), len(w.reads), len(w.merges))
        o = w.gw.submit(tok, Proposal(action, args))
        n_after = (len(w.transfers), len(w.reads), len(w.merges))

        if o.status is not Status.EXECUTED:
            assert n_after == n_before
            denied += o.status is Status.DENIED
            pending += o.status is Status.PENDING
            continue
        executed += 1
        assert genuine and action in scopes
        done = {"payment.transfer": w.transfers, "ehr.read_record": w.reads, "repo.merge": w.merges}[action][-1]
        if action == "payment.transfer":
            assert norm(done["source_account"]) == norm(claims["source_account"])
            assert done["currency"] == claims["currency"]
            payee = norm(done["payee_iban"])
            assert payee in TRUSTED[principal]["payee_iban"] or payee == norm(claims.get("payee"))
            assert payee != norm(ATTACKER)
            spent[tok] = spent.get(tok, 0) + done["amount_cents"]
            assert budget is None or spent[tok] <= budget
        elif action == "ehr.read_record":
            assert done["patient_id"] in TRUSTED[principal]["patient_id"] and done["purpose"] == claims["purpose"]
        else:
            assert done["head_sha"] in TRUSTED[principal]["head_sha"] and done["repo"] == claims["repo"]
    assert w.gw.ledger.verify()
    assert executed > 100 and denied > 3000 and pending > 20   # the fuzz really exercised every outcome
