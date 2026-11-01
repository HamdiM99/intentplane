"""Approval requests and decisions, independent of any web framework (and tested without one)."""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import secrets
import threading
import time
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from intentplane.core import Approvals, Ledger, operation_hash

STEP_UP, FOUR_EYES = "step_up", "four_eyes"


def hash_passcode(passcode: str, salt: bytes | None = None) -> str:
    """'scrypt$<salt hex>$<hash hex>', to put in the configuration instead of the passcode."""
    salt = salt or secrets.token_bytes(16)
    digest = hashlib.scrypt(passcode.encode(), salt=salt, n=2**14, r=8, p=1)
    return f"scrypt${salt.hex()}${digest.hex()}"


def _check_passcode(passcode: str, stored: str) -> bool:
    try:
        scheme, salt, _ = stored.split("$")
        return scheme == "scrypt" and hmac.compare_digest(hash_passcode(passcode, bytes.fromhex(salt)), stored)
    except ValueError:
        return False


@dataclass
class ApprovalRequest:
    id: str
    op_hash: str
    action: str
    principal: str
    effect: str                    # step_up (the principal confirms) or four_eyes (someone else does)
    shown: Mapping[str, Any]       # the resolved operation, as the approver must see it
    intent: Mapping[str, Any]      # what the approval signs: operation_hash(intent) == op_hash
    created: float
    expires: float
    status: str = "pending"        # pending, approved, rejected, expired, executed
    approver: str | None = None
    decided: float | None = None
    grant: dict | None = field(default=None, repr=False)

    def may_decide(self, approver: str) -> bool:
        return approver == self.principal if self.effect == STEP_UP else approver != self.principal


class ApprovalBoard:
    """Open requests, authenticate approvers, sign their decisions, and let the proxy wait for them.

    `approvals` is the gateway's own `Approvals`: the board signs with the approver's key, the gateway verifies it.
    `approvers` maps each human to a passcode hash (see `hash_passcode`)."""

    def __init__(self, approvals: Approvals, approvers: Mapping[str, str], *, ttl: float = 600,
                 ledger: Ledger | None = None, effects: frozenset[str] = frozenset({STEP_UP, FOUR_EYES})) -> None:
        self.approvals = approvals
        self._passcodes = dict(approvers)
        self.effects = effects
        self.ttl = ttl
        self.ledger = ledger if ledger is not None else Ledger()
        self._requests: dict[str, ApprovalRequest] = {}
        self._waiters: dict[str, list[tuple[asyncio.AbstractEventLoop, asyncio.Event]]] = {}
        self._lock = threading.Lock()

    # -- approvers -----------------------------------------------------------------------------------------
    def authenticate(self, approver: str, passcode: str) -> bool:
        stored = self._passcodes.get(approver)
        return stored is not None and _check_passcode(passcode, stored)

    # -- requests ------------------------------------------------------------------------------------------
    def open(self, op_hash: str, intent: Mapping[str, Any], shown: Mapping[str, Any], effect: str) -> ApprovalRequest:
        """A request for this exact operation; an existing pending or approved one is reused (the agent retries).

        `intent` is the gateway's canonical resolved intent: the request is refused unless it hashes to `op_hash`,
        so what the approver is shown and what the approval signs cannot differ."""
        if effect not in self.effects:
            raise ValueError(f"this approval board does not handle {effect!r}")
        if operation_hash(intent) != op_hash:
            raise ValueError("the operation shown does not match the operation to approve")
        with self._lock:
            self._expire()
            for r in self._requests.values():
                if r.op_hash == op_hash and r.status in ("pending", "approved"):
                    return r
            now = time.time()
            r = ApprovalRequest(secrets.token_urlsafe(16), op_hash, str(intent["action"]), str(intent["principal"]),
                                effect, dict(shown), dict(intent), now, now + self.ttl)
            self._requests[r.id] = r
            self._audit("opened", r)
            return r

    def get(self, request_id: str) -> ApprovalRequest | None:
        with self._lock:
            self._expire()
            return self._requests.get(request_id)

    def pending_for(self, approver: str) -> list[ApprovalRequest]:
        with self._lock:
            self._expire()
            return [r for r in self._requests.values() if r.status == "pending" and r.may_decide(approver)]

    def history(self, limit: int = 50) -> list[ApprovalRequest]:
        with self._lock:
            return sorted((r for r in self._requests.values() if r.status != "pending"),
                          key=lambda r: r.decided or r.created, reverse=True)[:limit]

    def decide(self, request_id: str, approver: str, approve: bool) -> ApprovalRequest:
        """`approver` must already be authenticated. Raises PermissionError when this person may not decide."""
        with self._lock:
            self._expire()
            r = self._requests.get(request_id)
            if r is None or r.status != "pending":
                raise PermissionError("no such pending request")
            if not r.may_decide(approver):
                raise PermissionError("step-up is confirmed by the user themselves; four eyes needs another person")
            r.status, r.approver, r.decided = ("approved" if approve else "rejected"), approver, time.time()
            if approve:   # signed over the exact operation, single use, valid until the request expires
                r.grant = self.approvals.approve(approver, r.op_hash, ttl=r.expires - time.time())
            self._audit(r.status, r)
        self._wake(r.id)
        return r

    def mark_executed(self, request_id: str) -> None:
        with self._lock:
            r = self._requests[request_id]
            r.status, r.grant = "executed", None
            self._audit("executed", r)

    async def wait(self, request_id: str, timeout: float) -> ApprovalRequest:
        """Wait until the request is decided, or `timeout` seconds."""
        waiter = (asyncio.get_running_loop(), asyncio.Event())
        with self._lock:
            self._waiters.setdefault(request_id, []).append(waiter)
        try:
            r = self.get(request_id)
            if r is not None and r.status == "pending":
                try:
                    await asyncio.wait_for(waiter[1].wait(), timeout)
                except asyncio.TimeoutError:
                    pass
            return self.get(request_id)
        finally:
            with self._lock:
                self._waiters[request_id].remove(waiter)

    # -- internals -----------------------------------------------------------------------------------------
    def _wake(self, request_id: str) -> None:
        with self._lock:
            waiters = list(self._waiters.get(request_id, ()))
        for loop, event in waiters:   # the decision may come from another thread (the web server's)
            try:
                loop.call_soon_threadsafe(event.set)
            except RuntimeError:      # that loop is closed: nobody is waiting any more
                pass

    def _expire(self) -> None:
        now = time.time()
        for r in self._requests.values():
            if r.status in ("pending", "approved") and r.expires < now:
                r.status, r.grant = "expired", None
                self._audit("expired", r)

    def _audit(self, event: str, r: ApprovalRequest) -> None:
        self.ledger.append(event=event, request=r.id, op_hash=r.op_hash, action=r.action, principal=r.principal,
                           effect=r.effect, approver=r.approver, shown=dict(r.shown))
