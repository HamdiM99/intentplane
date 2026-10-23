# intentplane

Domain-independent intent control plane for LLM agents. Generalisation of
[AgenticZeroTrust](https://github.com/HamdiM99/AgenticZeroTrust): the same core
authorises `payment.transfer`, `ehr.read_record` and `repo.merge`; only the
**domain pack** (contracts, invariants, sources of truth) changes.

```
pip install -e . && pytest        # 16 tests, incl. a 6,000-intent property fuzz
python demo.py                    # one injection, three domains
```

## What the core does (no domain knowledge inside)

| Stage | Check | Decides |
|---|---|---|
| contract | action exists in the catalog, params match the schema, nothing smuggled | DENY |
| provenance | every **critical** param carries an origin the contract allows; LLM text is never allowed | DENY |
| policy | deterministic PDP: strict delegation (universal) + pack invariants | ALLOW / STEP_UP / FOUR_EYES / DENY |
| receipt | hash-chained record: canonical intent, provenance, decision, contract + policy version | — |

A pack is ~40 lines: contracts (`Param(..., critical=True, allowed_origins=...)`)
and rules. The core never sees an IBAN, a patient id or a commit sha; it sees
typed values with origins.

## The property, and what checks it

> For any intent in any pack, if a critical parameter's origin is not in the
> contract's allowed set, the decision is never ALLOW.

`tests/test_property.py` fuzzes 2,000 random intents per pack (random values,
random origins, smuggled params, random context, random scopes) and asserts
it in both directions (no unsound ALLOW, every ALLOW has clean provenance).
This is the model-independent guarantee: it does not matter what the agent
wrote, only where the critical values came from.

## What is new vs AgenticZeroTrust

- **One core, N domains.** AZT's gateway, provenance and audit were written for
  payments. Here the catalog, provenance rule and PDP are domain-free; the bank
  pack reproduces AZT's behaviour (registry payees, cap, step-up, sanctions).
- **Provenance is per-parameter and declared in the contract**, not a global
  "known values" lookup: `patient_id` may only come from the open encounter,
  `purpose` may come from the clinician, `reference` may be anything.
- **Escalation is a policy effect** (`STEP_UP`, `FOUR_EYES`), so break-the-glass
  and protected-branch review are the same mechanism as a large payment.

## Known limits (same as AZT, kept honest)

- Provenance is a label on a value, set by the ingestion layer; derived values
  (an amount computed from an untrusted invoice) need information-flow tracking.
- `RulePDP` is a reference; the Cedar/OPA adapter is the `PDP` protocol.
- Receipts are tamper-evident, not anchored.
- The text channel (what the agent says back) is not covered here.
