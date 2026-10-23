"""A fictional world shared by the tests: one gateway, three domain packs."""
from dataclasses import dataclass

import pytest

from intentplane import Approvals, Authority, Gateway
from intentplane.packs import bank, code, health

ACME = "FR14 2004 1010 0505 0001 3M02 606"         # alice's registered beneficiary, ref "42"
BOBS_FRIEND = "GB33BUKB20201555555555"             # bob's beneficiary, ref "7" (not alice's)
NORTH = "KP12345678901234567890"                   # alice registered it, but the country is sanctioned
ATTACKER = "DE89370400440532013000"                # in nobody's registry
ALICE_ACC = "FR76 3000 6000 0112 3456 7890 189"
GREEN, RED, OTHER = "b" * 40, "c" * 40, "d" * 40   # CI-attested shas (OTHER: attested for bob only)


@dataclass
class World:
    gw: Gateway
    auth: Authority
    approvals: Approvals
    transfers: list
    reads: list
    merges: list

    def bank_token(self, principal="alice", **kw):
        claims = {"source_account": ALICE_ACC, "currency": "EUR", **kw.pop("claims", {})}
        return self.auth.issue(principal, kw.pop("scopes", {"payment.transfer"}), claims, **kw)


@pytest.fixture
def w() -> World:
    auth = Authority()
    approvals = Approvals({h: f"device-key-{h}".encode() for h in ("alice", "bob", "dr_alice", "dr_bob",
                                                                   "dev_alice", "dev_bob")})
    bank_pack, transfers = bank.build({"alice": {"42": ACME, "99": NORTH}, "bob": {"7": BOBS_FRIEND}})
    health_pack, reads = health.build({"dr_alice": {"E-17": "P-1234", "E-18": "P-5555"}}, research_consent={"P-5555"})
    code_pack, merges = code.build({"dev_alice": {"771": GREEN, "772": RED}, "dev_bob": {"900": OTHER}},
                                   ci_status={GREEN: "success", RED: "failure", OTHER: "success"})
    gw = Gateway([bank_pack, health_pack, code_pack], auth, approvals)
    return World(gw, auth, approvals, transfers, reads, merges)
