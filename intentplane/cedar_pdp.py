"""Cedar as the policy decision point.

Cedar decides with default deny and forbid-overrides-permit. Escalation lives in the policy too: the
request is evaluated at `context.approval_level` 0 (none), 1 (step-up: the principal confirms) and
2 (four-eyes: a second human), and the decision is the SMALLEST level at which Cedar permits. If no
level permits, the answer is DENY with the forbids that applied. The request Cedar sees is built by
the gateway from the resolved intent, never by the agent:

    principal  Principal::"<principal>"  { scopes }
    action     Action::"<contract name>"
    resource   Resource::"<contract resource>"
    context    { params: {...values}, origin: {...origins}, approval_level, <context providers...> }
"""
from __future__ import annotations

import re
from pathlib import Path

import cedarpy

from .core import ActionContract, Decision, Effect, ResolvedIntent

LEVELS = ((0, Effect.ALLOW), (1, Effect.STEP_UP), (2, Effect.FOUR_EYES))


def _cedar_value(v):
    """Cedar has no null and no float: drop None, refuse floats (use integer minor units)."""
    if isinstance(v, float):
        raise TypeError("Cedar has no float type: use integer units (e.g. cents)")
    if isinstance(v, dict):
        return {k: _cedar_value(x) for k, x in v.items() if x is not None}
    return v


class CedarPDP:
    def __init__(self, version: str, policies: str) -> None:
        self.version = version
        self.policies = policies
        self._ids = re.findall(r'@id\("([^"]+)"\)', policies)

    @classmethod
    def from_file(cls, version: str, path: Path) -> CedarPDP:
        return cls(version, Path(path).read_text(encoding="utf-8"))

    def _name(self, pid: str) -> str:
        m = re.fullmatch(r"policy(\d+)", pid)
        return self._ids[int(m.group(1))] if m and int(m.group(1)) < len(self._ids) else pid

    def evaluate(self, intent: ResolvedIntent, contract: ActionContract) -> Decision:
        base = _cedar_value({"params": intent.values(),
                             "origin": {k: t.origin.value for k, t in intent.params.items()},
                             **dict(intent.context)})
        entities = [{"uid": {"type": "Principal", "id": intent.mandate.principal},
                     "attrs": {"scopes": sorted(intent.mandate.scopes)}, "parents": []},
                    {"uid": {"type": "Resource", "id": contract.resource}, "attrs": {}, "parents": []}]
        request = {"principal": f'Principal::"{intent.mandate.principal}"', "action": f'Action::"{contract.name}"',
                   "resource": f'Resource::"{contract.resource}"'}
        names: tuple[str, ...] = ()
        for level, effect in LEVELS:
            res = cedarpy.is_authorized({**request, "context": {**base, "approval_level": level}},
                                        self.policies, entities)
            if res.diagnostics.errors:   # evaluation error => fail closed
                return Decision(Effect.DENY, "policy", ("policy evaluation error",), self.version)
            names = tuple(sorted(self._name(p) for p in res.diagnostics.reasons))
            if res.allowed:
                return Decision(effect, "policy", names, self.version)
        # Not permitted even with four-eyes: report the forbids that applied (determining policies at level 2).
        return Decision(Effect.DENY, "policy", names or ("no policy permits this action",), self.version)
