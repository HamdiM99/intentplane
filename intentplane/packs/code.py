"""Code pack: the commit to merge must be the one CI attested; protected
branches need review; no free shell exists in the catalog at all."""
from intentplane.core import Decision, Effect, Origin, Param, ActionContract, Intent, RulePDP, rule_strict_delegation

PROTECTED = {"main", "release"}

MERGE = ActionContract(
    name="repo.merge", resource="repo", version="code-1",
    params=(
        Param("repo", str, critical=True, allowed_origins=frozenset({Origin.USER_CHANNEL, Origin.SYSTEM_OF_RECORD})),
        Param("head_sha", str, critical=True, allowed_origins=frozenset({Origin.SYSTEM_OF_RECORD}),
              validate=lambda s: len(s) == 40),
        Param("target_branch", str),
        Param("message", str),
    ),
)

def rule_protected_branch(i: Intent, c: ActionContract):
    if i.action == "repo.merge" and i.params["target_branch"].value in PROTECTED and not i.context.get("approved_review"):
        return Decision(Effect.FOUR_EYES, "policy", ("protected branch requires an approved review",), "")

def rule_ci_green(i: Intent, c: ActionContract):
    if i.action == "repo.merge" and i.context.get("ci_status") != "success":
        return Decision(Effect.DENY, "policy", ("CI not green for head_sha",), "")

def pdp() -> RulePDP:
    return RulePDP("code-policy-1", [rule_strict_delegation, rule_ci_green, rule_protected_branch])

CONTRACTS = [MERGE]
