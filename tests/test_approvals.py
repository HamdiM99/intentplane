"""The local approval page: a step-up confirmed outside the agent's client.

What must hold: an approval is bound to the exact resolved operation (payee, amount and every other argument),
is single use and expires; what the page shows is what executes; the agent can neither forge a request nor
approve one; an approval of one operation never executes another. Then end to end over MCP with a client that
cannot elicit (Claude Desktop today)."""
import asyncio
import json
import re
import shutil
import socket
import sys
import threading
import time
from pathlib import Path

import pytest

from intentplane import Approvals, Ledger
from intentplane.approvals import FOUR_EYES, STEP_UP, ApprovalBoard, hash_passcode
from intentplane.core import operation_hash
from intentplane.proxy import ToolGate, load_config

EXAMPLE = Path(__file__).resolve().parents[1] / "examples" / "mcp"
ACME, LANDLORD = "FR7630006000011234567890189", "DE89370400440532013000"
PASSCODE = "emma-passcode"
HASH = hash_passcode(PASSCODE)


def intent(amount=124000, payee=ACME, **rest):
    return {"action": "payment.transfer", "principal": "emma", "mandate": "m1",
            "params": {"amount": amount, "recipient": payee, "_rest": json.dumps(rest)},
            "provenance": {"amount": ["agent", None], "recipient": ["registry", "beneficiaries/acme"],
                           "_rest": ["agent", None]}}


def shown(i):
    return {"recipient": f"{i['params']['recipient']}  (from beneficiaries/acme)", "amount": i["params"]["amount"] / 100}


def board(ttl=600, effects=frozenset({STEP_UP})):
    return ApprovalBoard(Approvals({"emma": b"e" * 32}), {"emma": HASH}, ttl=ttl, ledger=Ledger(), effects=effects)


def open_request(b, i=None):
    i = i or intent()
    return b.open(operation_hash(i), i, shown(i), STEP_UP)


# --- the board -----------------------------------------------------------------------------------------------
def test_passcodes_are_checked_against_their_hash():
    b = board()
    assert b.authenticate("emma", PASSCODE)
    assert not b.authenticate("emma", "guess") and not b.authenticate("mallory", PASSCODE)


def test_a_request_cannot_show_one_operation_and_approve_another():
    b = board()
    a, other = intent(), intent(amount=900000, payee=LANDLORD)
    with pytest.raises(ValueError):
        b.open(operation_hash(other), a, shown(a), STEP_UP)      # shows A, would sign B
    with pytest.raises(ValueError):
        b.open(operation_hash(a), {**a, "params": {**a["params"], "amount": 1}}, shown(a), STEP_UP)
    assert b._requests == {}


def test_the_approval_signs_that_operation_only_once_and_by_the_user():
    b = board()
    r = open_request(b)
    with pytest.raises(PermissionError):
        b.decide(r.id, "mallory", True)          # a step-up is the user's own confirmation
    r = b.decide(r.id, "emma", True)
    assert b.approvals.check(r.grant, r.op_hash) == "emma"
    assert b.approvals.check(r.grant, operation_hash(intent(amount=124001))) is None   # one cent more: no
    assert b.approvals.check(r.grant, operation_hash(intent(subject="other"))) is None  # any argument: no
    with pytest.raises(PermissionError):
        b.decide(r.id, "emma", True)             # decided once


def test_the_local_board_does_not_serve_four_eyes():
    with pytest.raises(ValueError):
        board().open(operation_hash(intent()), intent(), shown(intent()), FOUR_EYES)


def test_the_same_operation_reuses_its_request_and_rejections_and_expiry_are_final():
    b = board(ttl=0.05)
    r = open_request(b)
    assert open_request(b) is r                                      # the agent retried
    b.decide(r.id, "emma", False)
    late = open_request(b)
    assert late.id != r.id                                           # rejected: a new request
    time.sleep(0.1)
    assert b.get(r.id).status == "rejected"
    assert b.get(late.id).status == "expired" and b.get(late.id).grant is None
    assert [e["data"]["event"] for e in b.ledger.entries] == ["opened", "rejected", "opened", "expired"]


def test_an_approval_expires_with_its_request():
    b = board(ttl=0.2)
    r = b.decide(open_request(b).id, "emma", True)
    time.sleep(0.3)
    assert b.approvals.check(r.grant, r.op_hash) is None and b.get(r.id).status == "expired"


def test_a_waiting_call_wakes_up_when_the_user_decides_from_another_thread():
    b = board()
    r = open_request(b)

    async def wait():
        threading.Timer(0.1, b.decide, (r.id, "emma", True)).start()
        t = time.time()
        done = await b.wait(r.id, timeout=5)
        return done.status, time.time() - t

    status, waited = asyncio.run(wait())
    assert status == "approved" and waited < 2


# --- the web page --------------------------------------------------------------------------------------------
starlette = pytest.importorskip("starlette")


@pytest.fixture()
def web():
    from starlette.testclient import TestClient

    from intentplane.approvals.web import create_app
    b = board()
    return b, lambda: TestClient(create_app(b), follow_redirects=False)


def sign_in(client, passcode=PASSCODE):
    return client.post("/login", data={"approver": "emma", "passcode": passcode})


def csrf_of(html):
    return re.search(r'name="csrf" value="([^"]+)"', html).group(1)


def test_the_page_shows_the_resolved_operation_and_records_the_decision(web):
    b, client = web
    r = open_request(b)
    c = client()
    assert c.get(f"/requests/{r.id}").status_code == 303                  # signed out: to the sign-in page
    assert sign_in(c).status_code == 303
    page = c.get(f"/requests/{r.id}").text
    assert ACME in page and "from beneficiaries/acme" in page and "1240.0" in page and "Approve" in page
    assert c.post(f"/requests/{r.id}/decide", data={"decision": "approve"}).status_code == 400   # no CSRF token
    assert c.post(f"/requests/{r.id}/decide", data={"decision": "approve", "csrf": "x"}).status_code == 400
    assert b.get(r.id).status == "pending"
    c.post(f"/requests/{r.id}/decide", data={"decision": "approve", "csrf": csrf_of(page)})
    assert b.get(r.id).status == "approved" and b.get(r.id).approver == "emma"


def test_without_the_passcode_nothing_can_be_approved_and_no_request_can_be_made(web):
    b, client = web
    r = open_request(b)
    c = client()       # e.g. an agent with a shell on the same machine
    assert c.post(f"/requests/{r.id}/decide", data={"decision": "approve", "csrf": "x"}).status_code == 303
    assert c.post("/requests", data={"amount": 1}).status_code in (404, 405)   # requests only come from the gateway
    sign_in(c)
    assert "No such request" in c.get("/requests/forged-id").text
    assert b.get(r.id).status == "pending" and len(r.id) >= 20                # ids are not guessable


def test_wrong_passcodes_lock_the_approver_out(web):
    _, client = web
    c = client()
    for _ in range(5):
        assert "wrong passcode" in sign_in(c, "guess").text
    assert "Too many attempts" in sign_in(c).text                   # even the right one, for a while


# --- the proxy's gate with the local board -----------------------------------------------------------------
def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def workdir(tmp_path, wait=0, policy=None):
    work = tmp_path / "mcp"
    shutil.copytree(EXAMPLE, work, ignore=shutil.ignore_patterns("*.log", "*.jsonl"))
    if policy:
        (work / "policy.cedar").write_text(policy, encoding="utf-8")
    port = free_port()
    with open(work / "intentplane.toml", "a", encoding="utf-8") as f:
        f.write(f'\n[approvals]\npasscode = "{HASH}"\nlisten = "127.0.0.1:{port}"\nwait = {wait}\n'
                'receipts = "approvals.jsonl"\n')
    return work, port


def test_an_approval_never_executes_a_different_operation(tmp_path):
    work, _ = workdir(tmp_path)
    cfg = load_config(work / "intentplane.toml")
    gate = ToolGate(cfg)
    b = ApprovalBoard(gate.approvals, {"emma": cfg.approvals.passcode})
    calls = []

    def fake(spec, args):
        calls.append(args)
        return "ok"

    shown_op = {"recipient": "fr76 3000 6000 0112 3456 7890 189", "amount": 1240.0, "subject": "INV-2291"}
    p = gate.decide("demo.send_money", shown_op, fake)
    assert p.status == "pending" and p.effect == STEP_UP
    assert p.resolved == {"recipient": f"{ACME}  (from beneficiaries/acme)", "amount": 1240.0,
                          "other arguments": {"subject": "INV-2291"}}         # everything the approval covers
    r = b.decide(b.open(p.op_hash, p.intent, p.resolved, p.effect).id, "emma", True)
    for other in ({**shown_op, "amount": 1500.0},                             # more money
                  {**shown_op, "recipient": LANDLORD},                        # another trusted payee
                  {**shown_op, "subject": "INV-9999"}):                       # any other argument
        o = gate.decide("demo.send_money", other, fake, (r.grant,))
        assert o.status == "pending" and o.op_hash != p.op_hash
    assert calls == []
    assert gate.decide("demo.send_money", shown_op, fake, (r.grant,)).status == "executed"
    assert calls == [{"recipient": ACME, "amount": 1240.0, "subject": "INV-2291"}]
    assert gate.decide("demo.send_money", shown_op, fake, (r.grant,)).status == "pending"   # used once


def test_the_config_keeps_passcodes_hashed_and_more_approvers_need_a_channel(tmp_path):
    work, _ = workdir(tmp_path)
    toml = work / "intentplane.toml"
    text = toml.read_text(encoding="utf-8")
    toml.write_text(text.replace(HASH, "emma-passcode"), encoding="utf-8")
    with pytest.raises(ValueError, match="hash"):
        load_config(toml)
    toml.write_text(text + f'[approvals.approvers]\ncfo = "{HASH}"\n', encoding="utf-8")
    with pytest.raises(ValueError, match="channel"):
        load_config(toml)


# --- end to end over MCP, with a client that cannot elicit --------------------------------------------------
mcp = pytest.importorskip("mcp")


def approve_over_http(port, request_id, expect):
    """The user, in a browser: sign in, open the link the agent relayed, check it, approve."""
    import httpx
    with httpx.Client(base_url=f"http://127.0.0.1:{port}") as c:
        sign_in(c)
        page = c.get(f"/requests/{request_id}").text
        for text in expect:
            assert text in page, text
        c.post(f"/requests/{request_id}/decide", data={"decision": "approve", "csrf": csrf_of(page)})


def link_id(result):
    return re.search(r"/requests/([\w-]+)", result.content[0].text).group(1)


def run_client(work, steps):
    import anyio
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    async def run():
        server = StdioServerParameters(command=sys.executable, cwd=str(work),
                                       args=["-m", "intentplane.proxy.server", "--config", "intentplane.toml"])
        async with stdio_client(server) as (read, write), ClientSession(read, write) as session:   # no elicitation
            await session.initialize()
            return await steps(session)

    return anyio.run(run)


def send(session, recipient=ACME, amount=1240.0):
    return session.call_tool("send_money", {"recipient": recipient, "amount": amount})


def executed(work):
    log = work / "executed.log"
    return log.read_text(encoding="utf-8").splitlines() if log.exists() else []


def test_over_mcp_the_user_approves_the_link_and_only_that_operation_executes(tmp_path):
    work, port = workdir(tmp_path, wait=0)

    async def steps(session):
        first = await send(session)                              # 1,240.00 to ACME: needs the user
        switched = await send(session, amount=1300.0)            # the agent then asks for more...
        await asyncio.to_thread(approve_over_http, port, link_id(first), ["1240.0", ACME])
        retried_switch = await send(session, amount=1300.0)      # ...the approval of 1,240.00 does not cover it
        retried = await send(session)
        again = await send(session)                              # a second transfer needs a second approval
        return first, switched, retried_switch, retried, again

    first, switched, retried_switch, retried, again = run_client(work, steps)
    assert first.is_error and f"http://127.0.0.1:{port}/requests/" in first.content[0].text
    assert link_id(switched) != link_id(first)
    assert retried_switch.is_error and link_id(retried_switch) == link_id(switched)
    assert not retried.is_error, retried.content[0].text
    assert again.is_error and link_id(again) not in (link_id(first), link_id(switched))
    assert executed(work) == [f"TRANSFER 1240.00 EUR -> {ACME} ()"]
    events = [json.loads(line)["data"]["event"] for line in (work / "approvals.jsonl").read_text().splitlines()]
    assert events == ["opened", "opened", "approved", "executed", "opened"]


def test_over_mcp_the_call_can_wait_for_the_users_approval(tmp_path):
    work, port = workdir(tmp_path, wait=30)
    approvals = work / "approvals.jsonl"

    def approve_when_asked():
        deadline = time.monotonic() + 90                 # the proxy may take a while to start under load
        while time.monotonic() < deadline:
            if approvals.exists() and approvals.read_text().strip():
                rid = json.loads(approvals.read_text().splitlines()[0])["data"]["request"]
                return approve_over_http(port, rid, [ACME])
            time.sleep(0.05)

    user = threading.Thread(target=approve_when_asked)
    user.start()
    [res] = run_client(work, lambda session: asyncio.gather(send(session)))
    user.join()
    assert not res.is_error, res.content[0].text
    assert executed(work) == [f"TRANSFER 1240.00 EUR -> {ACME} ()"]


FOUR_EYES_POLICY = """
@id("permit-small-transfer")
permit (principal, action == Action::"payment.transfer", resource) when { context.params.amount <= 100000 };
@id("permit-large-transfer-four-eyes")
permit (principal, action == Action::"payment.transfer", resource)
when { context.params.amount > 100000 && context.approval_level >= 2 };
@id("permit-mail")
permit (principal, action == Action::"mail.send", resource);
"""


def test_over_mcp_four_eyes_is_refused_without_a_channel_that_serves_it(tmp_path):
    work, _ = workdir(tmp_path, policy=FOUR_EYES_POLICY)
    [res] = run_client(work, lambda session: asyncio.gather(send(session)))
    assert res.is_error and "no approval channel for four eyes" in res.content[0].text
    assert executed(work) == []
