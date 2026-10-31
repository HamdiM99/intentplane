"""Prepare the local Odoo for the accounts-payable demo (run once, after `docker compose ... up -d`).

Creates the database `ap_demo` with Odoo's Invoicing app and its demo data, then:
  * a vendor, "ACME Supplies", with the bank account it has used for 400 days;
  * two posted vendor bills from ACME: INV-2290 (240.00) and INV-2291 (1,240.00);
  * the three Odoo users intentplane uses (see intentplane/connectors/odoo.py): a read-only `intentplane-reader`,
    an `intentplane-payer` that can register payments, and the admin as bank administrator for this demo.
Their generated passwords are written to examples/odoo/credentials.json (git-ignored), never printed.

    python examples/odoo/setup_odoo.py
"""
import json
import secrets
import subprocess
import time
import xmlrpc.client
from pathlib import Path

URL, DB, MASTER = "http://127.0.0.1:8069", "ap_demo", "admin"
HERE = Path(__file__).resolve().parent
COMPOSE = ["docker", "compose", "-f", str(HERE / "docker-compose.yml")]
ACME_IBAN = "FR7630006000011234567890189"


def wait_for_odoo() -> None:
    for _ in range(120):
        try:
            xmlrpc.client.ServerProxy(f"{URL}/xmlrpc/2/common").version()
            return
        except (OSError, xmlrpc.client.Error):
            time.sleep(2)
    raise SystemExit("Odoo did not answer on 127.0.0.1:8069: is `docker compose up -d` running?")


def main() -> None:
    wait_for_odoo()
    dbs = xmlrpc.client.ServerProxy(f"{URL}/xmlrpc/2/db")
    admin_pw = "admin"   # Odoo's default for a database created this way; local demo only
    if DB not in dbs.list():
        print("creating database ap_demo with demo data (a few minutes)...")
        dbs.create_database(MASTER, DB, True, "en_US", admin_pw, "admin", "FR")
    common = xmlrpc.client.ServerProxy(f"{URL}/xmlrpc/2/common")
    uid = common.authenticate(DB, "admin", admin_pw, {})
    models = xmlrpc.client.ServerProxy(f"{URL}/xmlrpc/2/object", allow_none=True)

    def call(model, method, args, **kw):
        return models.execute_kw(DB, uid, admin_pw, model, method, args, kw)

    def xmlid(module, name):
        return call("ir.model.data", "search_read", [[["module", "=", module], ["name", "=", name]]],
                    fields=["res_id"])[0]["res_id"]

    if not call("ir.module.module", "search", [[["name", "=", "account"], ["state", "=", "installed"]]]):
        print("installing Invoicing (account) with demo data...")
        call("ir.module.module", "button_immediate_install", [call("ir.module.module", "search", [[["name", "=", "account"]]])])

    acme = call("res.partner", "search", [[["name", "=", "ACME Supplies"]]])
    if not acme:
        acme = [call("res.partner", "create", [{"name": "ACME Supplies", "is_company": True, "supplier_rank": 1,
                                                "phone": "+33 1 40 00 00 00", "email": "accounts@acme-supplies.example"}])]
        call("res.partner.bank", "create", [{"partner_id": acme[0], "acc_number": ACME_IBAN,
                                             "acc_holder_name": "ACME Supplies"}])
        # the account has been on file for 400 days (Odoo sets create_date itself; a demo fixture only)
        subprocess.run([*COMPOSE, "exec", "-T", "db", "psql", "-U", "odoo", "-d", DB, "-c",
                        ("UPDATE res_partner_bank SET create_date = now() - interval '400 days' "
                         f"WHERE sanitized_acc_number = '{ACME_IBAN}';")], check=True, capture_output=True)
    acme_id = acme[0]
    bank_id = call("res.partner.bank", "search", [[["partner_id", "=", acme_id]]])[0]

    for ref, amount in (("INV-2290", 240.0), ("INV-2291", 1240.0)):
        if call("account.move", "search", [[["ref", "=", ref], ["move_type", "=", "in_invoice"]]]):
            continue
        bill = call("account.move", "create", [{
            "move_type": "in_invoice", "partner_id": acme_id, "ref": ref, "partner_bank_id": bank_id,
            "invoice_date": time.strftime("%Y-%m-%d"),
            "invoice_line_ids": [[0, 0, {"name": f"ACME supplies, {ref}", "quantity": 1, "price_unit": amount,
                                         "tax_ids": [[6, 0, []]]}]]}])
        call("account.move", "action_post", [[bill]])

    creds_file = HERE / "credentials.json"
    creds = json.loads(creds_file.read_text()) if creds_file.exists() else {}
    for login, groups in (("intentplane-reader", [("base", "group_user"), ("account", "group_account_readonly")]),
                          ("intentplane-payer", [("account", "group_account_invoice")])):
        if login not in creds:
            creds[login] = secrets.token_urlsafe(18)
        found = call("res.users", "search", [[["login", "=", login]]])
        values = {"name": login, "login": login, "password": creds[login],
                  "groups_id": [[6, 0, [xmlid(m, g) for m, g in groups]]]}
        if found:
            call("res.users", "write", [found, values])
        else:
            call("res.users", "create", [values])
    creds["admin"] = admin_pw
    creds_file.write_text(json.dumps(creds, indent=1))
    print(f"ready: database {DB}, vendor ACME Supplies (id {acme_id}), bills INV-2290 and INV-2291, "
          f"users written to {creds_file.name}")


if __name__ == "__main__":
    main()
