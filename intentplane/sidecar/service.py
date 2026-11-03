"""The sidecar's logic, independent of HTTP (and tested without it).

One gateway serves many concurrent tasks of one agent host. The host (trusted side) asks for a mandate when it
accepts or resumes a task and withdraws it when the task ends; each task's tool calls carry that task's mandate.
The agent sees neither the host's credential nor, ideally, the mandate itself (the host's tools hold it).
"""
from __future__ import annotations

import hashlib
import json
import re
import threading
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from intentplane.approvals.board import FOUR_EYES, STEP_UP
from intentplane.connectors.git import BranchStore
from intentplane.connectors.github import GitHubAPI
from intentplane.core import Approvals, Authority, Effect, Gateway, Ledger, Proposal, Status, operation_hash
from intentplane.packs import github as github_pack
from intentplane.textscan import secret_kinds

from .config import STRENGTHS, SidecarConfig

TASK_ID = re.compile(r"^[A-Za-z0-9._:-]{1,100}$")
SHORT_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,79}$")
MESSAGE_KINDS = ("acknowledgement", "reply", "notice")
MAX_MESSAGE = 100_000


class RequestError(ValueError):
    """A malformed request from the host (HTTP 400)."""


class TaskHeld(Exception):
    """A person withdrew this task's mandate and held it: no mandate until released (HTTP 409)."""


@dataclass(frozen=True)
class Issued:
    token: str
    jti: str
    branch: str
    scopes: frozenset[str]
    expires: float


class Sidecar:
    def __init__(self, cfg: SidecarConfig, gh: GitHubAPI, ledger: Ledger | None = None, channel=None,
                 git: BranchStore | None = None) -> None:
        self.cfg = cfg
        self.authority = Authority()
        self.approvals = Approvals({})
        self.approvals.enroll(cfg.operator)
        self.ledger = ledger if ledger is not None else Ledger()
        self.gateway = Gateway([github_pack.build(gh, cfg.protected, git)], self.authority, self.approvals, self.ledger)
        self.channel = channel          # an ApprovalChannel, or None: anything needing a person is refused
        self._tasks: dict[str, Issued] = {}
        # Mandates the host withdrew (token -> task): a call with one is refused at its own stage, `withdrawn`, which
        # tells the host not to issue again. Held tasks (task -> reason) get no mandate until released; they survive a
        # restart when `cfg.holds` names a file, since a person's withdrawal must never be undone by a re-issue.
        self._withdrawn: dict[str, str] = {}
        self._held: dict[str, str] = self._load_holds()
        self._lock = threading.Lock()   # a decision and the receipt it reads back are one unit

    # -- the host's side ------------------------------------------------------------------------------------
    def issue(self, task: str, strength: str, short_name: str, threads: list | tuple = (),
              rules: list | tuple = (), requester: str | None = None) -> Issued:
        """A mandate for this task. Re-issuing (resume, a new thread) withdraws the task's previous one."""
        if not isinstance(task, str) or not TASK_ID.match(task):
            raise RequestError("task: 1-100 characters among letters, digits, . _ : -")
        if strength not in STRENGTHS:
            raise RequestError(f"strength must be one of {STRENGTHS}")
        if not isinstance(short_name, str) or not SHORT_NAME.match(short_name):
            raise RequestError("short_name: letters, digits, . _ -, starting with a letter or digit")
        if not isinstance(threads, (list, tuple)) or not all(isinstance(t, int) and not isinstance(t, bool)
                                                             and t > 0 for t in threads):
            raise RequestError("threads: a list of issue or pull request numbers")
        if not isinstance(rules, (list, tuple)) or not all(isinstance(r, str) for r in rules):
            raise RequestError("rules: a list of rule ids")
        branch = self.cfg.branch_prefix + short_name
        scopes = self.cfg.scopes[strength]
        if task in self._held:
            raise TaskHeld(f"task {task} is held ({self._held[task] or 'no reason given'}): a person must release it")
        claims = {"repo": self.cfg.repo, "base": self.cfg.base, "branch": branch, "threads": sorted(set(threads)),
                  "strength": strength, "tenant": f"{self.cfg.repo}@{branch}", "task": task,
                  "rules": list(rules), "requester": requester}
        with self._lock:
            previous = self._tasks.get(task)
            if previous is not None:
                self.authority.revoke_token(previous.jti)
            token = self.authority.issue(self.cfg.operator, scopes, claims, max_calls=self.cfg.max_calls,
                                         ttl=self.cfg.mandate_ttl)
            m = self.authority.verify(token)
            issued = Issued(token, m.jti, branch, scopes, m.exp)
            self._tasks[task] = issued
            # Receipts name mandates by id; this entry ties the id to the task (and to what it was given).
            self.ledger.append(event="mandate.issued", task=task, jti=m.jti, scopes=sorted(scopes), claims=claims,
                               replaces=previous.jti if previous else None)
        return issued

    def withdraw(self, task: str, reason: str = "", hold: bool = False) -> bool:
        """Withdraws the task's mandate. With `hold`, the task also gets no new mandate until `release`, even if it
        holds none now (a person stopping a task in an incident). False when there was nothing to withdraw or hold."""
        reason = str(reason)[:300]
        with self._lock:
            issued = self._tasks.pop(task, None)
            if issued is not None:
                self.authority.revoke_token(issued.jti)
                self._withdrawn[issued.token] = task
                self.ledger.append(event="mandate.withdrawn", task=task, jti=issued.jti, reason=reason, hold=hold)
            if hold and task not in self._held:
                self._held[task] = reason
                self._save_holds()
                self.ledger.append(event="task.held", task=task, reason=reason)
            return issued is not None or hold

    def release(self, task: str) -> bool:
        """Lets a held task receive mandates again. False when it was not held."""
        with self._lock:
            if self._held.pop(task, None) is None:
                return False
            self._save_holds()
            self.ledger.append(event="task.released", task=task)
            return True

    def held(self, task: str) -> bool:
        return task in self._held

    def screen_message(self, task: Any, source: Any, conversation: Any, kind: Any, text: Any) -> dict:
        """A message the host is about to send on a conversation (an answer, an acknowledgement, a notice). The host
        chose the conversation from its own records; the text was written by a model that read untrusted input, so
        it is refused when it carries a secret, or when a person holds its task. The receipt keeps the text's hash and
        length, never the text: a refused one may hold the secret."""
        if task is not None and (not isinstance(task, str) or not TASK_ID.match(task)):
            raise RequestError("task: 1-100 characters among letters, digits, . _ : -, or null")
        if not isinstance(source, str) or not 0 < len(source) <= 40:
            raise RequestError("source: 1-40 characters")
        if not isinstance(conversation, str) or not 0 < len(conversation) <= 300:
            raise RequestError("conversation: 1-300 characters")
        if kind not in MESSAGE_KINDS:
            raise RequestError(f"kind must be one of {MESSAGE_KINDS}")
        if not isinstance(text, str) or len(text) > MAX_MESSAGE:
            raise RequestError(f"text: a string of at most {MAX_MESSAGE} characters")
        reasons = []
        with self._lock:
            if task is not None and task in self._held:
                reasons.append(f"task {task} is held by a person ({self._held[task] or 'no reason given'})")
            kinds = secret_kinds(text)
            if kinds:
                reasons.append(f"the text carries what looks like a secret ({', '.join(sorted(set(kinds)))})")
            status = "DENIED" if reasons else "ALLOWED"
            self.ledger.append(event="message.screened", stage="message", task=task, source=source,
                               conversation=conversation, kind=kind, sha256=hashlib.sha256(text.encode()).hexdigest(),
                               length=len(text), status=status, reasons=reasons)
        if reasons:
            return {"status": "denied", "stage": "message", "reasons": reasons, "message": "[message] " + "; ".join(reasons)}
        return {"status": "allowed"}

    def _load_holds(self) -> dict[str, str]:
        path = self.cfg.holds
        if path is None or not Path(path).exists():
            return {}
        data = json.loads(Path(path).read_text(encoding="utf-8"))   # unreadable holds stop the sidecar: fail closed
        if not isinstance(data, dict) or not all(isinstance(k, str) and isinstance(v, str) for k, v in data.items()):
            raise ValueError(f"{path}: not a JSON object of task -> reason")
        return data

    def _save_holds(self) -> None:
        if self.cfg.holds is None:
            return
        path = Path(self.cfg.holds)
        temporary = path.with_name(path.name + ".tmp")
        temporary.write_text(json.dumps(self._held, indent=1, sort_keys=True), encoding="utf-8")
        temporary.replace(path)

    # -- the agent's side -----------------------------------------------------------------------------------
    def propose(self, token: str, action: Any, args: Any) -> dict:
        """One tool call. Pending operations get an approval link; retrying the same call after the person
        approved executes it (the approval is bound to that exact resolved operation)."""
        if not isinstance(args, Mapping):
            args = {"__malformed__": args}   # the gateway refuses it, and the receipt records what was sent
        proposal = Proposal(action if isinstance(action, str) else repr(action), dict(args))
        with self._lock:
            task = self._withdrawn.get(token)
            if task is not None:
                held = task in self._held
                reason = f"the mandate of task {task} was withdrawn" + (" and the task is held by a person" if held else "")
                self.ledger.append(action=proposal.action, raw=dict(proposal.args), intent=None, status="DENIED",
                                   stage="withdrawn", reasons=[reason], approvers=[], task=task, held=held)
                return {"status": "denied", "stage": "withdrawn", "reasons": [reason], "held": held,
                        "message": f"[withdrawn] {reason}: do not retry"}
            o = self.gateway.submit(token, proposal)
            if o.status is not Status.PENDING:
                return self._answer(o)
            intent = self._pending_intent(o.op_hash)
            if intent is None:   # never show a person anything but the operation to approve
                return {"status": "denied", "message": "internal error: the operation to approve could not be shown"}
            effect = STEP_UP if o.decision.effect is Effect.STEP_UP else FOUR_EYES
            if self.channel is None or effect not in self.channel.effects:
                who = "the operator" if effect == STEP_UP else "a second person"
                return {"status": "denied", "stage": "approval",
                        "message": f"needs confirmation by {who}, and no approval channel serves it here: refused"}
            request = self.channel.open(o.op_hash, intent, self._shown(intent), effect)
            if request.status == "approved" and request.grant:
                done = self.gateway.submit(token, proposal, (request.grant,))
                if done.status is Status.EXECUTED:
                    self.channel.mark_executed(request.id)
                return self._answer(done)
            if request.status in ("rejected", "expired"):
                return {"status": "denied", "stage": "approval", "message": f"the approval request was {request.status}"}
            return {"status": "pending", "effect": effect, "approval_url": self.channel.link(request),
                    "expires": request.expires, "shown": request.shown,
                    "message": ("needs confirmation by the operator" if effect == STEP_UP else
                                "needs approval by a second person (four eyes)")
                    + ": relay the link to a person, then make the same call again"}

    def _pending_intent(self, op_hash: str) -> dict | None:
        """The canonical intent of the receipt the gateway just wrote for this pending operation."""
        for entry in reversed(self.ledger.entries[-20:]):
            intent = entry["data"].get("intent")
            if entry["data"].get("status") == Status.PENDING.value and intent and operation_hash(intent) == op_hash:
                return intent
        return None

    @staticmethod
    def _answer(o) -> dict:
        if o.status is Status.EXECUTED:
            return {"status": "executed", "result": o.result, "approvers": list(o.approvers)}
        return {"status": "denied", "stage": o.decision.stage, "reasons": list(o.decision.reasons),
                "message": f"[{o.decision.stage}] " + "; ".join(o.decision.reasons)}

    @staticmethod
    def _shown(intent: Mapping[str, Any]) -> dict:
        """The resolved operation as a person must see it, with where each value comes from."""
        params, origin = intent.get("params", {}), intent.get("provenance", {})
        return {k: (f"{v}  (from {origin[k][1]})" if origin.get(k) and origin[k][1] else v) for k, v in params.items()}

    def catalog(self) -> list[str]:
        return self.gateway.catalog.names()
