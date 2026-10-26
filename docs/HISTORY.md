# History

## v0.5.3: a clear answer when the client cannot confirm

A step-up on a client without MCP elicitation is refused with that reason (checked from the client's declared
capabilities), not as "the user did not confirm". Tool descriptions explain the guard in plain words. Found while
testing the demo in Claude Desktop, which does not support elicitation yet.

## v0.5.2: the user confirms the resolved operation

A step-up confirmation shows the resolved values and their origin (trusted record, amount in its units), not the
agent's arguments. Narrated demo client in `examples/mcp/demo_client.py`.

## v0.5.1: `intentplane-mcp` as its own package

The proxy command is now the separate PyPI package `intentplane-mcp` (it depends on `intentplane[mcp]`), so MCP
clients and the MCP Registry can run it with `uvx intentplane-mcp --config ...`. `server.json` describes it for
the official MCP Registry.

## v0.5: MCP proxy

- **`intentplane-mcp`**: the gateway as an MCP proxy between any MCP client and its MCP servers, configured in
  TOML (critical arguments, their trusted sources, the parameters the Cedar policy needs). Unlisted tools are
  hidden and refused, read-only ones can pass through, a step-up is confirmed by the user through MCP
  elicitation, receipts are appended to a JSON-lines file. Demo in `examples/mcp/`.
- **Fix**: `Gateway(..., ledger=...)` silently replaced an empty ledger by a new one (an empty `Ledger` is falsy
  through `__len__`), so a ledger passed in was never written. Found by the proxy's receipt test.

## v0.4: extended evaluation

- **Core**: list-valued critical parameters (`Param(..., each=True)`: every email recipient must resolve);
  a **write-ahead receipt** (`EXECUTING`, with the operation hash) before any execution, so nothing runs
  without a record. Writing the failure tests showed the receipt used to be written only after execution.
- **Workspace pack** for AgentDojo (email, calendar, drive): recipients, participants and shares must be in the
  request or among the user's correspondents; deletions always need the user.
- **Bridges** execute the resolved values (the trusted records), not the agent's raw ones; replays rerun with
  this change are identical.
- **Experiments**: AgentDojo's four built-in defenses as baselines, Claude Haiku 5.5, the workspace suite,
  gateway-aware adaptive attacks, a noisy approver, ablations and incomplete registries
  (`run_adaptive.py`, `run_sweep.py`, `run_registry.py`).
- **Tests**: 49 (failures of the trusted side, concurrent approval replay, workspace pack). Mutation testing
  covers the workspace policy: its deletion rule was untested until a mutant survived.

## v0.3: merge with an independent v2, accounts-payable pack

A second, independently written v2 fixed the same v0.1 flaw. v0.3 keeps the best of both:

| From v2 | From v0.2 |
|---|---|
| Cedar as the only policy engine, for every pack | registries queried per owner (v2 let Alice's agent pay Bob's beneficiary) |
| escalation decided by Cedar: smallest `approval_level` that permits | execution by the gateway only (v2 returned a decision and let the caller execute) |
| mutation testing over every policy file and approval guard | a lock around check-and-consume of budgets and nonces |
| the agent's raw message kept in each receipt | payees named by the user in the mandate; AgentDojo bridge; property oracle independent of the gateway |

The accounts-payable pack was then ported from v2.2, with two changes: records are per **tenant** (the
company named in the signed mandate), and "paid once" holds under concurrency. In v2.2 it relied on the
caller running the executor after the decision: three decisions before execution, or eight concurrent
ones, were all ALLOWed. Here the gateway checks, executes and marks the invoice paid in one critical
section. Executors receive the verified mandate, so they write only in the right tenant.

An external review then led to: the AgentDojo bridge refusing (instead of forwarding) an update of an
unknown scheduled transaction and routing `recurring` / `date` changes through the gateway; every gateway
decision recorded in the run files; `ERP.log_callback()` normalising IBANs; and a stricter mutation script
(clean baseline required, permit conditions mutated too), which exposed three missing tests, now added.

## v0.2: the gateway resolves provenance, identity and context itself

v0.1 carried the trust label, the principal (with its scopes) and the context **inside the intent**, and
the gateway believed them. A compromised agent could therefore write them itself: forged
`system_of_record` labels, self-granted scopes, `payee_sanctioned=False`, `ci_status="success"`,
`consent_research=True` were all ALLOWed, and with no budget the same 999.00 payment went through 50
times. The property test checked a label the attacker chooses. Those 7 attacks are regression tests
(`tests/test_regressions.py`), alongside:

- **default deny**: without an explicit permit, the answer is DENY (v0.1 was default-permit);
- **per-owner registries**: Alice's agent cannot pay Bob's beneficiary or read another clinician's patient;
- **mandates** bind source account, currency and (optionally) payees, with a cumulative budget and call limit;
- **escalations are enforced**: `STEP_UP` / `FOUR_EYES` return `PENDING` with an operation hash, and only a
  valid, matching, single-use approval by the right human executes;
- **fail closed**: an exception in a validator, context provider, policy or executor is a DENY, and a failed
  execution consumes no budget.

## v0.1

Generalisation of [AgenticZeroTrust](https://github.com/HamdiM99/AgenticZeroTrust) to several domains with one
core and domain packs.
