"""Property: an intent whose critical parameter has UNTRUSTED provenance is
never ALLOWed, for any action in any pack, whatever the other values are.
Random fuzz over 3 packs x 2000 intents. Also: every ALLOW has all critical
params from an allowed origin (soundness in the other direction)."""
import random
from intentplane import Catalog, Effect, Gateway, Intent, Origin, Principal, Tagged
from intentplane.packs import bank, health, code

ALL = frozenset({"payment.transfer", "ehr.read_record", "repo.merge"})
SAMPLES = {str: ["x", "FR14-ACME", "P-1", "main", "EUR", "treatment", "emergency", "research", "full", "labs", "a" * 40],
           float: [-1.0, 0.0, 10.0, 999.0, 1001.0, 9999.0, 10001.0, 1e9]}

def random_intent(contract, rng):
    params = {}
    for p in contract.params:
        origin = rng.choice(sorted(p.allowed_origins)) if rng.random() < 0.8 else rng.choice(list(Origin))
        params[p.name] = Tagged(rng.choice(SAMPLES[p.type]), origin)
    if rng.random() < 0.1:   # sometimes smuggle a parameter
        params["extra"] = Tagged("x", Origin.UNTRUSTED)
    ctx = {k: rng.choice([True, False, "success", "failure", None])
           for k in ("payee_sanctioned", "consent_research", "ci_status", "approved_review")}
    scopes = ALL if rng.random() < 0.8 else frozenset()
    return Intent(contract.name, params, Principal("u", frozenset(), scopes), "agent", ctx)

def test_untrusted_critical_never_allowed():
    rng = random.Random(7)
    n_untrusted = n_allow = 0
    for pack in (bank, health, code):
        cat = Catalog()
        for c in pack.CONTRACTS:
            cat.register(c)
        g = Gateway(cat, pack.pdp())
        for _ in range(2000):
            c = pack.CONTRACTS[0]
            i = random_intent(c, rng)
            d, _ = g.decide(i)
            bad = any(i.params[p.name].origin not in p.allowed_origins for p in c.params if p.critical)
            if bad:
                n_untrusted += 1
                assert d.effect is not Effect.ALLOW, (i.canonical(), d)
            if d.effect is Effect.ALLOW:
                n_allow += 1
                assert not bad
        assert g.ledger.verify()
    assert n_untrusted > 1000 and n_allow > 30   # the fuzz actually exercised both sides
