# Who has authority over the registries

intentplane decides on an agent's action by resolving each critical value against a trusted source: the
signed mandate, or a registry such as the vendor master. The guarantee is therefore exactly as strong as the
control over those registries. If an agent, or anything the agent can reach, could write a vendor's bank account
in the ERP, an attacker would target the ERP and the gateway would faithfully pay the attacker. This page states
the rules the deployment must follow, and how the Odoo connector applies them.

## Rules

1. **The agent cannot write a registry.** It holds no ERP credential at all. The tools it sees are those the
   proxy exposes; none of them writes a vendor, a bank account or a contact. An MCP server that can (for example a
   generic ERP server) must not be exposed to the agent, or only with its write tools left undeclared, which
   hides and refuses them.
2. **The gateway reads with a read-only identity.** Registry lookups and policy facts use an ERP user that the
   ERP itself prevents from writing master data. A bug or a compromise of the read path cannot change what the
   gateway will trust next.
3. **Each write the gateway makes uses its own, narrow identity**, and only in the executor of an action the
   policy has allowed:
   - paying a bill: a user that can register payments, never one that can edit bank accounts; the payment goes to
     the bank account the gateway *resolved*, not to the one written on the bill;
   - changing a vendor's bank details: a separate user, used only by `vendor.update_bank_details`, which the
     policy allows after a call-back on the number already on file and a second approver.
4. **Every other change to a registry happens outside the agent's path**: in the ERP's own interface, by a
   person, under the company's own controls (Odoo's access rights, its approval of bank account changes where
   enabled, its audit log).
5. **A new value is never trusted at once.** A bank account changed less than 7 days ago makes every payment to
   it need four eyes (accounts-payable policy).

## How the Odoo connector applies them

| Identity | Odoo rights | Used by | Can change a bank account? |
|---|---|---|---|
| `intentplane-reader` | Internal user, Accounting read-only | every registry lookup and policy fact | no (Odoo refuses; checked by `examples/odoo/demo_odoo.py`) |
| `intentplane-payer` | Billing | executor of `invoice.pay` only | no (Odoo refuses to modify or create one; checked by the demo) |
| bank administrator | can create vendor bank accounts | executor of `vendor.update_bank_details` only | yes, by design, after call-back and four eyes |

The agent holds none of them. In the demo the bank administrator is Odoo's admin, for simplicity; in production
it is a dedicated user, and its credential sits with the gateway only.

**Checked on Odoo 17 Community.** With these groups, Odoo itself refuses both the reader and the payer when they
try to modify a vendor bank account or add one (`examples/odoo/demo_odoo.py` tries all four). Access rights
differ between Odoo versions and customised installations: run the same check on yours before going live, and
keep it in your acceptance tests.

**Stand-ins in the demo.** Odoo has no call-back log and no Verification of Payee service: the connector records
call-backs in a file on the trusted side, and compares the account holder name stored in Odoo with the vendor's
name. Replace both with the company's procedure and its bank's VoP service.
