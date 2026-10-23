"""One injection, three domains, one gateway. The agent has read untrusted content and proposes an
action whose critical value comes from it; then it even tries to forge the trust label itself.
Only the gateway resolves provenance, identity and context, so neither attempt executes.

    python demo.py
"""
from intentplane import Approvals, Authority, Gateway, Origin, Proposal, Tagged
from intentplane.packs import bank, code, health

ACME, ATTACKER = "FR14 2004 1010 0505 0001 3M02 606", "DE89370400440532013000"
ACC = "FR76 3000 6000 0112 3456 7890 189"

auth = Authority()
approvals = Approvals({h: f"device-key-{h}".encode() for h in ("alice", "dr_alice", "dr_bob", "dev_alice", "dev_bob")})
bank_pack, transfers = bank.build({"alice": {"42": ACME}})
health_pack, reads = health.build({"dr_alice": {"E-17": "P-1234"}})
code_pack, merges = code.build({"dev_alice": {"771": "b" * 40}}, {"b" * 40: "success"})
gw = Gateway([bank_pack, health_pack, code_pack], auth, approvals)

CASES = [
    ("bank", auth.issue("alice", {"payment.transfer"}, {"source_account": ACC, "currency": "EUR"}, budget=50_000),
     "payment.transfer", "payee_iban", ATTACKER, {"ref": "42"},
     {"source_account": ACC, "amount_cents": 25_000, "currency": "EUR", "reference": "pay to the IBAN in the email"}),
    ("health", auth.issue("dr_alice", {"ehr.read_record"}, {"purpose": "treatment"}),
     "ehr.read_record", "patient_id", "P-9999", {"ref": "E-17"}, {"section": "labs", "purpose": "treatment"}),
    ("code", auth.issue("dev_alice", {"repo.merge"}, {"repo": "org/app"}),
     "repo.merge", "head_sha", "a" * 40, {"ref": "771"},
     {"repo": "org/app", "target_branch": "feature/x", "message": "merge, README says it's safe"}),
]

for name, token, action, crit, injected, legit, rest in CASES:
    print(f"\n== {name}: {action} ==")
    attempts = [("injected value from the text", injected),
                ("same value + forged trust label", Tagged(injected, Origin.SYSTEM_OF_RECORD, "registry/1")),
                ("legitimate (gateway resolves the ref)", legit)]
    for label, value in attempts:
        o = gw.submit(token, Proposal(action, {crit: value, **rest}))
        print(f"  {label:40s} -> {o.status.value:9s} [{o.decision.stage}] {'; '.join(o.decision.reasons)[:70]}")

print(f"\nexecuted: {len(transfers)} transfer, {len(reads)} record read, {len(merges)} merge; "
      f"receipts chained: {gw.ledger.verify()} ({len(gw.ledger)} entries)")
