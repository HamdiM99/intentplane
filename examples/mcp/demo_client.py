"""A scripted, narrated run of the demo, for people who want to see the proxy without configuring a client
(and for recording it). It plays a fully obedient agent against intentplane-mcp and the demo server.

    python examples/mcp/demo_client.py            # add --fast to skip the pauses
"""
import json
import sys
import time
from pathlib import Path

import anyio
from mcp import ClientSession, StdioServerParameters, types
from mcp.client.stdio import stdio_client

HERE = Path(__file__).resolve().parent
PAUSE = 0 if "--fast" in sys.argv else 1.6
B, DIM, RED, GREEN, YEL, END = "\033[1m", "\033[2m", "\033[31m", "\033[32m", "\033[33m", "\033[0m"
ACME = "FR76 3000 6000 0112 3456 7890 189"
ATTACKER = "LT601010012345678901"


def say(text: str = "", pause: float = 1.0) -> None:
    print(text, flush=True)
    time.sleep(PAUSE * pause)


def result(res) -> None:
    text = res.content[0].text if res.content else ""
    if res.is_error:
        say(f"   {RED}{B}REFUSED{END} {RED}{text}{END}", 1.5)
    else:
        say(f"   {GREEN}{B}EXECUTED{END} {GREEN}{text}{END}", 1.5)


async def confirm(context, params):
    say(f"   {YEL}{B}The client asks the user:{END} {YEL}{params.message.splitlines()[0]}{END}", 1.2)
    say(f"   {YEL}{json.dumps(json.loads(params.message.split(chr(10), 1)[1]))}{END}", 1.2)
    say(f"   {YEL}The user checks the operation and confirms.{END}", 1.2)
    return types.ElicitResult(action="accept", content={"confirm": True})


async def main() -> None:
    for f in ("executed.log", "receipts.jsonl"):
        (HERE / f).unlink(missing_ok=True)
    server = StdioServerParameters(command=sys.executable, cwd=str(HERE),
                                   args=["-m", "intentplane.proxy.server", "--config", "intentplane.toml"])
    say(f"{B}intentplane-mcp demo{END}  {DIM}MCP client -> intentplane-mcp -> a naive bank-and-mail MCP server{END}", 1.5)
    errlog = open(HERE / "proxy.log", "w")  # noqa: ASYNC230, SIM115  (the proxy's stderr, once per run)
    async with stdio_client(server, errlog=errlog) as (read, write), \
            ClientSession(read, write, elicitation_callback=confirm) as session:
        await session.initialize()
        tools = [t.name for t in (await session.list_tools()).tools]
        say(f"{DIM}Tools the agent sees: {', '.join(tools)}{END}\n", 1.5)

        say(f"{B}1. The agent reads the inbox.{END}")
        inbox = (await session.call_tool("read_inbox", {})).structured_content["result"][0]
        say(f"   From {inbox['from']}: {inbox['subject']}")
        say(f"   {DIM}\"{inbox['body'].splitlines()[1]}\"{END}\n", 2)

        say(f"{B}2. The agent obeys the email: it pays 1,240.00 EUR to the IBAN in the email.{END}")
        result(await session.call_tool("send_money", {"recipient": ATTACKER, "amount": 1240.0, "subject": "INV-2291"}))
        say()

        say(f"{B}3. The agent pays 240.00 EUR to ACME, the vendor's IBAN on file (spelled with spaces).{END}")
        result(await session.call_tool("send_money", {"recipient": ACME, "amount": 240.0, "subject": "INV-2290"}))
        say()

        say(f"{B}4. The agent pays the 1,240.00 EUR invoice to ACME: above 1,000.00, the user must confirm.{END}")
        result(await session.call_tool("send_money", {"recipient": ACME, "amount": 1240.0, "subject": "INV-2291"}))
        say()

    say(f"{B}What the bank server actually executed:{END}")
    for line in (HERE / "executed.log").read_text(encoding="utf-8").splitlines():
        say(f"   {line}", 0.6)
    receipts = (HERE / "receipts.jsonl").read_text(encoding="utf-8").splitlines()
    say(f"\n{DIM}{len(receipts)} hash-chained receipts in receipts.jsonl. Nothing was sent to {ATTACKER}.{END}", 1)
    say(f"\n{B}uvx intentplane-mcp --config intentplane.toml{END}   github.com/HamdiM99/intentplane", 0)


if __name__ == "__main__":
    anyio.run(main)
