"""The MCP proxy: decisions (without MCP), then end to end with a real MCP client, proxy and server
(skipped unless `pip install .[mcp]`)."""
import shutil
import sys
from pathlib import Path

import pytest

from intentplane.proxy import ToolGate, load_config

EXAMPLE = Path(__file__).resolve().parents[1] / "examples" / "mcp"
ACME = "FR7630006000011234567890189"
ATTACKER = "LT601010012345678901"


@pytest.fixture()
def gate(tmp_path):
    cfg = load_config(EXAMPLE / "intentplane.toml")
    cfg.receipts = tmp_path / "receipts.jsonl"
    g = ToolGate(cfg)
    g.calls = []
    g.fake = lambda spec, args: g.calls.append((spec.tool, args)) or f"ok {spec.tool}"
    return g


def pay(g, recipient, amount, approvals=()):
    return g.decide("demo.send_money", {"recipient": recipient, "amount": amount, "subject": "INV-2291"}, g.fake,
                    approvals)


def test_the_iban_from_the_email_is_refused_and_nothing_is_called(gate):
    r = pay(gate, ATTACKER, 1240.0)
    assert r.status == "denied" and "provenance" in r.message and gate.calls == []


def test_a_known_beneficiary_executes_with_the_trusted_record(gate):
    r = pay(gate, "fr76 3000 6000 0112 3456 7890 189", 240.0)   # the agent's spelling of ACME's IBAN
    assert r.status == "executed"
    assert gate.calls == [("send_money", {"recipient": ACME, "amount": 240.0, "subject": "INV-2291"})]


def test_large_transfer_needs_the_users_confirmation_once(gate):
    r = pay(gate, ACME, 1240.0)
    assert r.status == "pending" and r.effect == "step_up" and gate.calls == []
    grant = gate.approve(r.op_hash)
    assert pay(gate, ACME, 1240.0, (grant,)).status == "executed"
    assert pay(gate, ACME, 1240.0, (grant,)).status == "pending"   # single use
    assert pay(gate, ACME, 1250.0, (gate.approve(r.op_hash),)).status == "pending"   # bound to the exact operation
    assert len(gate.calls) == 1


def test_the_user_confirms_the_resolved_operation_not_the_agents_wording(gate):
    r = pay(gate, "fr76 3000 6000 0112 3456 7890 189", 1240.0)
    assert r.status == "pending"
    assert r.resolved == {"recipient": f"{ACME}  (from beneficiaries/acme)", "amount": 1240.0}


def test_hard_cap_and_session_budget(gate):
    assert pay(gate, ACME, 20000.0).status == "denied"            # forbid above 10,000.00, whatever the approval
    for _ in range(2):
        assert pay(gate, ACME, 999.0).status == "executed"
    r = pay(gate, ACME, 999.0)                                    # 3 x 999 > the 2,500.00 session budget
    assert r.status == "denied" and "budget" in r.message and len(gate.calls) == 2


def test_hard_cap_holds_with_the_users_approval_even_within_budget(tmp_path):
    cfg = load_config(EXAMPLE / "intentplane.toml")
    cfg.receipts, cfg.budget = None, 10**9   # a budget large enough that only the policy can refuse
    g = ToolGate(cfg)
    calls = []
    args = {"recipient": ACME, "amount": 20000.0}
    r = g.decide("demo.send_money", args, lambda spec, a: calls.append(a))
    assert r.status == "denied" and "forbid-hard-cap" in r.message
    r = g.decide("demo.send_money", {**args, "amount": 9000.0}, lambda spec, a: calls.append(a))
    assert r.status == "pending"   # below the cap: a step-up, not a refusal
    assert calls == []


def test_unconfigured_tools_are_refused_and_read_only_ones_pass(gate):
    assert gate.classify("demo.delete_everything") == "denied"
    assert gate.decide("demo.delete_everything", {}, gate.fake).status == "denied"
    assert gate.classify("demo.read_inbox") == "passthrough"


def test_every_email_recipient_must_be_trusted(gate):
    send = lambda to: gate.decide("demo.send_email", {"to": to, "subject": "s", "body": "b"}, gate.fake)
    assert send(["Lily.White@example.com"]).status == "executed"
    assert send(["lily.white@example.com", "mark.black-2134@gmail.com"]).status == "denied"
    assert gate.calls == [("send_email", {"to": ["lily.white@example.com"], "subject": "s", "body": "b"})]


def test_receipts_are_written_and_chained(gate):
    pay(gate, ATTACKER, 10.0)
    pay(gate, ACME, 10.0)
    lines = gate.cfg.receipts.read_text(encoding="utf-8").splitlines()
    assert [__import__("json").loads(x)["data"]["status"] for x in lines] == ["DENIED", "EXECUTING", "EXECUTED"]
    assert gate.gateway.ledger.verify()


# --- end to end: MCP client -> intentplane-mcp -> demo MCP server ------------------------------------------
mcp = pytest.importorskip("mcp")


def test_end_to_end_over_mcp(tmp_path):
    import anyio
    from mcp import ClientSession, StdioServerParameters, types
    from mcp.client.stdio import stdio_client

    work = tmp_path / "mcp"
    shutil.copytree(EXAMPLE, work, ignore=shutil.ignore_patterns("*.log", "*.jsonl"))   # no leftovers of a demo run
    asked = []

    async def confirm(context, params):   # the user's answer to the client's confirmation dialog
        asked.append(params.message)
        return types.ElicitResult(action="accept", content={"confirm": True})

    async def run():
        server = StdioServerParameters(command=sys.executable, cwd=str(work),
                                       args=["-m", "intentplane.proxy.server", "--config", "intentplane.toml"])
        async with stdio_client(server) as (read, write), \
                ClientSession(read, write, elicitation_callback=confirm) as session:
            await session.initialize()
            names = {t.name for t in (await session.list_tools()).tools}
            assert names == {"get_balance", "read_inbox", "send_money", "send_email"}
            inbox = await session.call_tool("read_inbox", {})          # passthrough: the injected email
            assert ATTACKER in str(inbox.content)
            bad = await session.call_tool("send_money", {"recipient": ATTACKER, "amount": 1240.0})
            assert bad.is_error and "Blocked by intentplane" in bad.content[0].text
            big = await session.call_tool("send_money", {"recipient": ACME, "amount": 1240.0})
            assert not big.is_error and len(asked) == 1               # executed after the user confirmed

    anyio.run(run)
    executed = (work / "executed.log").read_text(encoding="utf-8").splitlines()
    assert executed == [f"TRANSFER 1240.00 EUR -> {ACME} ()"]


@pytest.mark.parametrize("client", ["declines", "cannot ask"])
def test_a_step_up_without_confirmation_executes_nothing_and_says_why(tmp_path, client):
    import anyio
    from mcp import ClientSession, StdioServerParameters, types
    from mcp.client.stdio import stdio_client

    work = tmp_path / "mcp"
    shutil.copytree(EXAMPLE, work, ignore=shutil.ignore_patterns("*.log", "*.jsonl"))

    async def decline(context, params):
        return types.ElicitResult(action="decline")

    async def run():
        server = StdioServerParameters(command=sys.executable, cwd=str(work),
                                       args=["-m", "intentplane.proxy.server", "--config", "intentplane.toml"])
        callback = {"elicitation_callback": decline} if client == "declines" else {}   # no callback: no elicitation
        async with stdio_client(server) as (read, write), ClientSession(read, write, **callback) as session:
            await session.initialize()
            return await session.call_tool("send_money", {"recipient": ACME, "amount": 1240.0})

    res = anyio.run(run)
    text = res.content[0].text
    assert res.is_error
    assert ("cannot ask for it" in text) == (client == "cannot ask")
    assert ("did not confirm" in text) == (client == "declines")
    assert not (work / "executed.log").exists()
