# Editions: what is open, what is enterprise

intentplane is open source (Apache 2.0) and safe on its own. An enterprise edition adds what an
**organization** needs around it. This page is the single definition of that line; any other packaging
(the MCP proxy, a sidecar, a hosted service) uses the same one.

## The rule

1. **No security feature behind a paywall.** If removing a paid feature would make the open edition less
   safe, that feature belongs in the open edition.
2. **The open edition's step-up is a production feature**, not a demo: bound to the exact resolved operation,
   single use, expiring, and tested against an agent that tries to get something else approved.
3. **Nothing is closed to stop people rebuilding it.** The approval channel is a documented interface; anyone
   can extend the local page to more approvers. The enterprise edition sells support, maintenance and
   accountability, not obfuscation.

## The line

| Open source (Apache 2.0) | Enterprise |
|---|---|
| Provenance by resolution: critical arguments resolved against the mandate and the systems of record | A second approver (four eyes) with roles and thresholds |
| Cedar policy, default deny; refusal of anything unresolved | Single sign-on (OIDC/SAML) for approvers |
| Step-up: in the client (MCP elicitation) or on the local approval page | Approvals on mobile, with notifications |
| Signed, single-use, expiring approvals over the exact operation hash | Central audit: receipts from every gateway, search, export |
| Hash-chained receipts, write-ahead `EXECUTING` receipt | Several tenants and gateways managed together |
| MCP proxy, packs, Odoo connector, the `ApprovalChannel` interface | Hosted service, support, maintenance, SLA |

Four eyes in the open edition: the gateway, the policy and the approval check are open; a request needing
four eyes is **refused** unless an approval channel that serves it is configured. Refusing is the safe default.

## The test before moving anything

Would the open edition be less safe without it? Then it stays open.
