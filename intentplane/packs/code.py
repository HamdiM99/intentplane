"""Code pack: the repository is bound in the developer's mandate, the commit must be one CI attested
for that developer's repos (read by the gateway), CI status and review state are read by the gateway
from the CI and code-review systems, protected branches without an approved review need a second
human. No free shell exists in the catalog at all."""
from __future__ import annotations

from collections.abc import Iterable, Mapping

from intentplane.core import (
    ActionContract,
    Decision,
    DictRegistry,
    Effect,
    FromMandate,
    FromRegistry,
    Pack,
    Param,
    RulePDP,
)

PROTECTED = {"main", "release"}

MERGE = ActionContract(
    name="repo.merge", resource="repo", version="code-2",
    params=(
        Param("repo", str, critical=True, sources=(FromMandate("repo"),)),
        Param("head_sha", str, critical=True, sources=(FromRegistry("ci_attested"),), validate=lambda s: len(s) == 40),
        Param("target_branch", str, validate=lambda b: bool(b)),
        Param("message", str),
    ),
)


def permit_merge(i, c):
    if i.action == "repo.merge":
        return Decision(Effect.ALLOW, "policy", ("permit-merge",))


def forbid_red_ci(i, c):
    if i.action == "repo.merge" and i.context["ci_status"] != "success":
        return Decision(Effect.DENY, "policy", ("CI not green for head_sha",))


def four_eyes_protected_branch(i, c):
    if i.action == "repo.merge" and i.params["target_branch"].value in PROTECTED and not i.context["approved_review"]:
        return Decision(Effect.FOUR_EYES, "policy", ("protected branch requires an approved review",))


PDP = RulePDP("code-policy-2", [permit_merge, forbid_red_ci, four_eyes_protected_branch])


def build(ci_attested: Mapping[str, Mapping[str, str]], ci_status: Mapping[str, str],
          approved_reviews: Iterable[str] = ()) -> tuple[Pack, list]:
    """ci_attested: developer -> {CI run id: sha}; ci_status: sha -> status. Returns the pack and the merge log."""
    approved = set(approved_reviews)
    merges: list[dict] = []

    def execute(values) -> str:
        merges.append(dict(values))
        return f"merged {values['head_sha'][:7]} into {values['target_branch']}"

    pack = Pack("code", [MERGE], PDP,
                registries={"ci_attested": DictRegistry(ci_attested)},
                context={"ci_status": lambda a, v, m: ci_status.get(v["head_sha"], "unknown"),
                         "approved_review": lambda a, v, m: v["head_sha"] in approved},
                executors={"repo.merge": execute})
    return pack, merges
