"""Approval channels: how the proxy reaches a human when the agent's client cannot (or must not) confirm.

The proxy only needs the `ApprovalChannel` protocol. `LocalApprovals` is the open-source one: an approval board
and its web app, in the proxy's own event loop. By default it serves the user's own step-up only; another channel
(or `LocalApprovals` given more approvers and `four_eyes`) can serve more. Whatever the channel, the gateway checks
every approval again: signed over the exact operation, single use, by the right person.
"""
from __future__ import annotations

import asyncio
import logging
from collections.abc import Mapping
from typing import Any, Protocol

from intentplane.core import Approvals, Ledger

from .board import STEP_UP, ApprovalBoard, ApprovalRequest

log = logging.getLogger("intentplane.approvals")


class ApprovalChannel(Protocol):
    effects: frozenset[str]      # which effects it can obtain: "step_up", "four_eyes"

    def open(self, op_hash: str, intent: Mapping[str, Any], shown: Mapping[str, Any],
             effect: str) -> ApprovalRequest: ...
    async def wait(self, request_id: str, timeout: float) -> ApprovalRequest: ...
    def mark_executed(self, request_id: str) -> None: ...
    def link(self, request: ApprovalRequest) -> str: ...
    async def start(self) -> None: ...
    async def stop(self) -> None: ...


class LocalApprovals:
    """The approval board and its web app on this machine (127.0.0.1 by default)."""

    def __init__(self, approvals: Approvals, approvers: Mapping[str, str], *, host: str = "127.0.0.1",
                 port: int = 8765, public_url: str | None = None, ttl: float = 300, ledger: Ledger | None = None,
                 secure_cookies: bool = False, effects: frozenset[str] = frozenset({STEP_UP})) -> None:
        for human in approvers:
            approvals.enroll(human)
        self.board = ApprovalBoard(approvals, approvers, ttl=ttl, ledger=ledger, effects=effects)
        self.effects = effects
        self.host, self.port, self.secure_cookies = host, port, secure_cookies
        self.public_url = (public_url or f"http://{host}:{port}").rstrip("/")
        self._server = self._task = None

    def open(self, op_hash, intent, shown, effect) -> ApprovalRequest:
        return self.board.open(op_hash, intent, shown, effect)

    async def wait(self, request_id: str, timeout: float) -> ApprovalRequest:
        return await self.board.wait(request_id, timeout)

    def mark_executed(self, request_id: str) -> None:
        self.board.mark_executed(request_id)

    def link(self, request: ApprovalRequest) -> str:
        return f"{self.public_url}/requests/{request.id}"

    async def start(self) -> None:
        import uvicorn

        from .web import create_app
        self._server = uvicorn.Server(uvicorn.Config(
            create_app(self.board, secure_cookies=self.secure_cookies), host=self.host, port=self.port,
            log_config=None, access_log=False, log_level="warning"))   # logs to stderr, never stdout
        self._server.install_signal_handlers = lambda: None   # the MCP client owns this process's lifetime
        self._task = asyncio.create_task(self._server.serve())
        log.info("approval page on %s", self.public_url)

    async def stop(self) -> None:
        if self._server is not None:
            self._server.should_exit = True
            await self._task
