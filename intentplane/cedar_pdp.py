"""Cedar as the policy decision point (optional dependency: `pip install cedarpy`).

Cedar decides allow / deny with default deny and forbid-overrides-permit. Escalations are expressed
as ordinary permit policies whose @id starts with "step-up" or "four-eyes": when such a policy is
among the determining permits, the effect becomes STEP_UP / FOUR_EYES. The request Cedar sees is
built by the gateway from the resolved intent, never by the agent:

    principal  Principal::"<principal>"  { scopes }
    action     Action::"<contract name>"
    resource   Resource::"<contract resource>"
    context    { params: {...values}, origin: {...origins}, <context providers...> }
"""
from __future__ import annotations

import re

import cedarpy

from .core import ActionContract, Decision, Effect, ResolvedIntent


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

    def _name(self, pid: str) -> str:
        m = re.fullmatch(r"policy(\d+)", pid)
        return self._ids[int(m.group(1))] if m and int(m.group(1)) < len(self._ids) else pid

    def evaluate(self, intent: ResolvedIntent, contract: ActionContract) -> Decision:
        context = _cedar_value({"params": intent.values(),
                                "origin": {k: t.origin.value for k, t in intent.params.items()},
                                **dict(intent.context)})
        principal = {"type": "Principal", "id": intent.mandate.principal}
        entities = [{"uid": principal, "attrs": {"scopes": sorted(intent.mandate.scopes)}, "parents": []},
                    {"uid": {"type": "Resource", "id": contract.resource}, "attrs": {}, "parents": []}]
        request = {"principal": f'Principal::"{intent.mandate.principal}"', "action": f'Action::"{contract.name}"',
                   "resource": f'Resource::"{contract.resource}"', "context": context}
        res = cedarpy.is_authorized(request, self.policies, entities)
        if res.diagnostics.errors:   # evaluation error => fail closed
            return Decision(Effect.DENY, "policy", ("policy evaluation error",), self.version)
        names = tuple(sorted(self._name(p) for p in res.diagnostics.reasons))
        if not res.allowed:
            return Decision(Effect.DENY, "policy", names or ("no policy permits this action",), self.version)
        if any(n.startswith("four-eyes") for n in names):
            return Decision(Effect.FOUR_EYES, "policy", names, self.version)
        if any(n.startswith("step-up") for n in names):
            return Decision(Effect.STEP_UP, "policy", names, self.version)
        return Decision(Effect.ALLOW, "policy", names, self.version)
