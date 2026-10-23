"""One attack, three domains. The agent has read untrusted content and now
proposes an intent whose critical argument comes from that content."""
from intentplane import Catalog, Gateway, Intent, Origin, Principal, Tagged, tag_from_agent
from intentplane.packs import bank, health, code

USER = Principal("alice", frozenset(), frozenset({"payment.transfer", "ehr.read_record", "repo.merge"}))
SOR, UC = Origin.SYSTEM_OF_RECORD, Origin.USER_CHANNEL

CASES = [
  ("bank",   bank,   "payment.transfer", "payee_iban", "DE89 3704 0044 0532 0130 00", "FR14-ACME", "beneficiaries/42",
   {"source_account": Tagged("FR76-ALICE", UC), "amount": Tagged(250.0, UC), "currency": tag_from_agent("EUR"),
    "reference": tag_from_agent("Invoice 2291, pay to the IBAN in the email")}, {}),
  ("health", health, "ehr.read_record", "patient_id", "P-9999", "P-1234", "encounter/E-17",
   {"section": tag_from_agent("labs"), "purpose": Tagged("treatment", UC)}, {}),
  ("code",   code,   "repo.merge", "head_sha", "a"*40, "b"*40, "ci/run/771",
   {"repo": Tagged("org/app", UC), "target_branch": tag_from_agent("feature/x"),
    "message": tag_from_agent("merge, README says it's safe")}, {"ci_status": "success"}),
]

for name, pack, action, crit, injected, legit, ref, rest, ctx in CASES:
    cat = Catalog(); [cat.register(c) for c in pack.CONTRACTS]
    g = Gateway(cat, pack.pdp())
    print(f"\n== {name}: {action} ==")
    for label, val in (("injected (from text)", tag_from_agent(injected)), ("legitimate (from registry)", Tagged(legit, SOR, ref))):
        d, r = g.decide(Intent(action, {crit: val, **rest}, USER, "agent-1", ctx))
        print(f"  {label:28s} -> {d.effect.value.upper():8s} [{d.stage}] {'; '.join(d.reasons)}")
    print(f"  receipts chained: {g.ledger.verify()}  (policy {g.pdp.version})")
