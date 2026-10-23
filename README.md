# intentplane

Domain-independent intent control plane for LLM agents. Generalisation of
[AgenticZeroTrust](https://github.com/HamdiM99/AgenticZeroTrust): one gateway authorises
`payment.transfer`, `ehr.read_record` and `repo.merge`; only the **domain pack** (contracts, trusted
sources, policy, executor) changes.

```bash
pip install -e ".[dev]"
pytest                          # 24 tests, incl. a 6,000-proposal property fuzz
python demo.py                  # one injection, three domains, one gateway
python scripts/mutation_check.py  # neutralise each Cedar forbid: the suite must fail
```

## The trust boundary

The agent produces **one thing**: a `Proposal`, i.e. an action name and raw values.

```python
Proposal("payment.transfer", {"payee_iban": {"ref": "42"}, "amount_cents": 25_000, ...})
```

It **cannot** express an origin, an identity, a scope or a context: there is no field for them, and
an unexpected key is a contract violation. Everything that decides is resolved by the gateway:

| What | Where it comes from |
|---|---|
| Identity, scopes, mandate (task bindings, budget) | a signed token issued by the trusted side (`Authority`) |
| Provenance of each critical value | the contract declares its sources (`FromMandate`, `FromRegistry`); the gateway looks the value up **for that principal** |
| Context (sanctions, CI status, review, consent) | context providers that query systems of record |

## Pipeline (any error at any stage => DENY)

| Stage | Check |
|---|---|
| authn | token signature, expiry, revocation |
| contract | action exists in the catalog, exact parameter set, types, validators |
| provenance | every critical value equals a trusted value: a mandate claim, or a record of the principal's own registry (`{"ref": id}` is resolved by the gateway, never trusted) |
| delegation | the principal holds the action's scope |
| budget | cumulative amount and call count per mandate |
| policy | deterministic PDP, **default deny** (Cedar for the bank pack, rules for the others) |
| approval | `STEP_UP` needs the principal, `FOUR_EYES` a different human; the signature covers the operation hash, single use, expiring |
| execution | the gateway is the only component that can act (executor per action) |
| receipt | hash-chained record of the resolved intent, its provenance, the decision and the approvers |

## The property, and what checks it

> Whatever the agent proposes, an action executes only if the token is genuine and holds the scope,
> every critical value equals a trusted value for that principal, and the mandate's budget holds.

`tests/test_property.py` fuzzes 6,000 proposals over **what an attacker controls**: any action, any
keys, any values, refs (valid, foreign, unknown), forged labels (`Tagged(..., SYSTEM_OF_RECORD)`,
`{"origin": ...}`), wrong types, smuggled context keys, and replayed tokens (other users', tampered,
expired). An independent oracle, written from the world's ground truth and not from gateway code,
checks every executed action. The run exercises all outcomes (about 120 executed, 5,800 denied,
80 pending).

## v0.2: what changed, and why

v0.1 carried the trust label, the principal (with its scopes) and the context **inside the intent**,
and the gateway believed them. A compromised agent could therefore write them itself: forged
`system_of_record` labels, self-granted scopes, `payee_sanctioned=False`, `ci_status="success"`,
`consent_research=True` were all ALLOWed, and with no budget the same 999.00 payment went through
50 times. The property test checked a label the attacker chooses. Those 7 attacks are now
regression tests (`tests/test_regressions.py`), alongside:

- **default deny**: without an explicit permit, the answer is DENY (v0.1 was default-permit);
- **registries are per principal**: Alice's agent cannot pay Bob's beneficiary or read another clinician's patient;
- **mandates** bind source account, currency and (optionally) payee, with a cumulative budget and call limit;
- **escalations are enforced**: `STEP_UP` / `FOUR_EYES` return `PENDING` with an operation hash, and only a valid, matching, single-use approval by the right human executes;
- **Cedar** is back for the bank pack (`intentplane/cedar_pdp.py`), with policy mutation testing;
- **fail closed**: an exception in a validator, context provider, policy or executor is a DENY, and a failed execution consumes no budget.

## Writing a pack

A pack declares contracts, where critical values may come from, its trusted sources and policy:

```python
Param("payee_iban", str, critical=True,
      sources=(FromMandate("payee"), FromRegistry("beneficiaries")), normalize=norm_iban)
```

A critical parameter cannot accept `Free` values (enforced when the contract is built).
See `intentplane/packs/{bank,health,code}.py` (40 to 80 lines each).

## Known limits

- **Derived values**: provenance is a property of a value the gateway can look up. An amount computed
  from an untrusted invoice is not critical here; it is bounded by the mandate's budget and the policy,
  not proven. Information-flow tracking would be needed to do better.
- **Trusted sources are trusted**: a poisoned registry, mandate issuance from a manipulated UI or a
  compromised gateway are out of scope.
- **In-process, in-memory**: budgets, nonces and receipts live in memory; production needs a shared
  atomic store, separate services and asymmetric signatures. Receipts are tamper-evident, not anchored.
- **Simulated humans**: approvals are signed with keys standing in for approvers' devices.
- The text channel (what the agent says back to the user) is not covered.
