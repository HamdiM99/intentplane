"""GitHub pack: an autonomous coding agent working one task on one branch.

The trusted side (the agent's host, e.g. a super-agent router) issues one mandate per task. Its claims bind:

    repo      owner/name                       base      the branch PRs target (e.g. main)
    branch    the task's own branch             threads   issue / PR numbers the task was accepted on
    strength  "explicit" (a person asked)       tenant    "<repo>@<branch>": whose records the gateway reads
              or "inferred" (the agent took it up)

What the agent may do, and only if:

| Action       | Critical values                                         | Policy                                    |
|--------------|---------------------------------------------------------|-------------------------------------------|
| `pr.create`  | repo, head = the task's branch, base = the mandate's    | permitted                                 |
| `pr.comment` | the thread is one the task was accepted on, or a PR     | permitted                                 |
|              | whose head is the task's branch                         |                                           |
| `pr.merge`   | the PR's head is the task's branch, head_sha is its     | explicit task only, CI green on that sha, |
|              | current head (read from GitHub)                          | operator step-up; protected base needs an |
|              |                                                         | approved review, or four eyes             |
| `branch.push`| repo, branch = the task's branch; sha (recorded)        | the commit is staged, a fast-forward (no  |
|              |                                                         | force push exists); touching CI config    |
|              |                                                         | needs the operator's step-up              |

There is no free shell, no free repository and no free PR number in this catalog. Titles, bodies and comments
are free text: recorded in every receipt, and decided on only by a heuristic that refuses text carrying a
credential (`intentplane.textscan`, defence in depth, not a guarantee; see "Known limits" in the README).
"""
from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path
from typing import Any

from intentplane.cedar_pdp import CedarPDP
from intentplane.connectors.git import SHA, BranchStore, touches_ci, touches_dependencies, touches_instructions
from intentplane.connectors.github import GitHubAPI
from intentplane.connectors.workflows import exposed_jobs
from intentplane.core import ActionContract, FromMandate, FromRegistry, Mandate, Pack, Param
from intentplane.textscan import secret_kinds

MERGE_METHODS = frozenset({"squash", "merge", "rebase"})


def _as_int(v: Any) -> int:
    if isinstance(v, bool):
        raise TypeError("not a number")
    if isinstance(v, int):
        return v
    if isinstance(v, str) and v.strip().lstrip("#").isdigit():
        return int(v.strip().lstrip("#"))
    raise TypeError("not a number")


def split_tenant(owner: str) -> tuple[str, str]:
    """`owner/name@branch` -> (repo, branch)."""
    repo, sep, branch = owner.rpartition("@")
    if not sep or not repo or not branch:
        raise ValueError(f"not a task tenant: {owner!r}")
    return repo, branch


class BranchPulls:
    """Registry: the open PRs whose head is the task's own branch, in the task's repository (never a fork)."""

    def __init__(self, gh: GitHubAPI) -> None:
        self.gh = gh

    def get(self, owner: str, ref: str) -> int | None:
        repo, branch = split_tenant(owner)
        try:
            pull = self.gh.pull(repo, _as_int(ref))
        except TypeError:
            return None
        ok = pull is not None and pull.state == "open" and pull.head_ref == branch and pull.head_repo == repo
        return pull.number if ok else None

    def find(self, owner: str, value: Any) -> str | None:
        try:
            ref = str(_as_int(value))
        except TypeError:
            return None
        return ref if self.get(owner, ref) is not None else None


class BranchHeads:
    """Registry: the current head commits of those PRs."""

    def __init__(self, gh: GitHubAPI) -> None:
        self.gh = gh

    def _heads(self, owner: str) -> set[str]:
        repo, branch = split_tenant(owner)
        return {p.head_sha for p in self.gh.pulls_for_branch(repo, branch)
                if p.state == "open" and p.head_ref == branch and p.head_repo == repo}

    def get(self, owner: str, ref: str) -> str | None:
        return ref if isinstance(ref, str) and ref in self._heads(owner) else None

    def find(self, owner: str, value: Any) -> str | None:
        return value if isinstance(value, str) and value in self._heads(owner) else None


PR_CREATE = ActionContract(
    name="pr.create", resource="repo", version="github-1",
    params=(
        Param("repo", str, critical=True, sources=(FromMandate("repo"),)),
        Param("head", str, critical=True, sources=(FromMandate("branch"),)),
        Param("base", str, critical=True, sources=(FromMandate("base"),)),
        Param("title", str, validate=lambda t: 0 < len(t.strip()) <= 256),
        Param("body", str, validate=lambda b: len(b) <= 65536),
    ),
)

PR_COMMENT = ActionContract(
    name="pr.comment", resource="repo", version="github-1",
    params=(
        Param("repo", str, critical=True, sources=(FromMandate("repo"),)),
        Param("number", int, critical=True, sources=(FromMandate("threads"), FromRegistry("branch_pulls")),
              normalize=_as_int),
        Param("body", str, validate=lambda b: 0 < len(b.strip()) and len(b) <= 65536),
    ),
)

PR_MERGE = ActionContract(
    name="pr.merge", resource="repo", version="github-1",
    params=(
        Param("repo", str, critical=True, sources=(FromMandate("repo"),)),
        Param("number", int, critical=True, sources=(FromRegistry("branch_pulls"),), normalize=_as_int),
        Param("head_sha", str, critical=True, sources=(FromRegistry("branch_heads"),),
              validate=lambda s: len(s) == 40),
        Param("method", str, validate=lambda m: m in MERGE_METHODS),
    ),
)

BRANCH_PUSH = ActionContract(
    name="branch.push", resource="repo", version="github-1",
    params=(
        Param("repo", str, critical=True, sources=(FromMandate("repo"),)),
        Param("branch", str, critical=True, sources=(FromMandate("branch"),)),
        Param("sha", str, validate=lambda s: bool(SHA.match(s))),   # not critical: the commit is recorded, its facts decide
    ),
)

CONTRACTS = [PR_CREATE, PR_COMMENT, PR_MERGE]
PDP = CedarPDP.from_file("github-policy-1", Path(__file__).with_name("github.cedar"))


def _prefix(branch: str) -> str:
    """The namespace of task branches (`superagent/` for `superagent/fix-typo`): a CI guard must exclude all of it."""
    return branch.rsplit("/", 1)[0] + "/" if "/" in branch else branch


def build(gh: GitHubAPI, protected: Iterable[str] = ("main", "master", "release"), git: BranchStore | None = None) -> Pack:
    """The pack; `branch.push` is in the catalog only with a `git` store to publish from."""
    protected = frozenset(protected)

    def push_facts(action: str, v, m: Mandate) -> dict:
        """Whether the commit is the one staged, a fast-forward of the task's branch, whether it changes CI or the
        project's dependencies, and which
        of its CI jobs would run it with a secret (unknown counts as exposed)."""
        if action != "branch.push" or git is None:
            return {"staged": False, "fast_forward": False, "touches_workflows": True, "touches_dependencies": True,
                    "touches_instructions": True, "exposes_secrets": True, "exposed_jobs": []}
        repo, branch, sha = m.claims["repo"], v["branch"], v["sha"]
        staged = git.stage(branch, sha)
        files = git.workflows(sha) if staged else None
        jobs = exposed_jobs(files, branch, _prefix(branch)) if files is not None else None
        paths = git.changed_paths(repo, m.claims["base"], sha) if staged else None
        return {"staged": staged, "fast_forward": staged and git.fast_forward(repo, branch, sha),
                "touches_workflows": touches_ci(paths), "touches_dependencies": touches_dependencies(paths),
                "touches_instructions": touches_instructions(paths),
                "exposes_secrets": jobs is None or bool(jobs), "exposed_jobs": jobs or []}

    def pr_facts(action: str, v, m: Mandate) -> dict:
        """Everything the merge policy needs, read from GitHub in one pass for this decision."""
        if action != "pr.merge":
            return {"ci_status": "n/a", "head_matches": False, "target_branch": "", "protected": True,
                    "approved_review": False}
        repo = m.claims["repo"]
        pull = gh.pull(repo, v["number"])
        base = pull.base_ref if pull else ""
        return {"ci_status": gh.ci_status(repo, v["head_sha"]),
                "head_matches": bool(pull and pull.head_sha == v["head_sha"]),
                "target_branch": base,
                "protected": pull is None or base in protected,   # unknown counts as protected
                "approved_review": gh.approved(repo, v["number"])}

    def text_facts(action: str, v, m: Mandate) -> dict:
        """Whether the free text the agent wrote carries something that looks like a credential."""
        if action not in ("pr.comment", "pr.create"):
            return {"secret": False}
        return {"secret": bool(secret_kinds(" ".join(str(v.get(k, "")) for k in ("title", "body"))))}

    def execute_create(v, m):
        return gh.create_pull(v["repo"], v["head"], v["base"], v["title"], v["body"])

    def execute_comment(v, m):
        return gh.comment(v["repo"], v["number"], v["body"])

    def execute_merge(v, m):
        return gh.merge(v["repo"], v["number"], v["head_sha"], v["method"])

    def execute_push(v, m):
        return git.push(v["repo"], v["branch"], v["sha"])

    executors = {"pr.create": execute_create, "pr.comment": execute_comment, "pr.merge": execute_merge}
    if git is not None:
        executors["branch.push"] = execute_push
    return Pack("github", [*CONTRACTS, *([BRANCH_PUSH] if git is not None else [])], PDP,
                registries={"branch_pulls": BranchPulls(gh), "branch_heads": BranchHeads(gh)},
                context={"strength": lambda a, v, m: str(m.claims.get("strength", "inferred")), "pr": pr_facts,
                         "push": push_facts, "text": text_facts},
                executors=executors)
