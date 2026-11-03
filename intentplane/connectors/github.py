"""GitHub as the system of record of the github pack: pull requests, their head commit, CI and reviews.

`GitHubAPI` is the small surface the pack needs. `GhCli` implements it with the `gh` command line, run by the
gateway with the gateway's own GitHub identity (`GH_TOKEN` in the sidecar's environment). The agent never holds
that identity: its own `gh`, if it has one, should be read-only (see docs/sidecar.md).

Every fact the policy uses is read here, never taken from the agent: the PR's real head branch, base branch and
head commit, the CI result for that commit, the review state.
"""
from __future__ import annotations

import json
import os
import subprocess
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Protocol

OK_CONCLUSIONS = frozenset({"success", "neutral", "skipped"})
# Repository permissions whose approval GitHub counts towards a protected branch's required reviews.
WRITE_PERMISSIONS = frozenset({"admin", "maintain", "write"})


@dataclass(frozen=True)
class PullRequest:
    number: int
    state: str          # open, closed
    head_ref: str       # the branch
    head_repo: str      # owner/name of the head repository (a fork has another one)
    head_sha: str
    base_ref: str


class GitHubAPI(Protocol):
    def pull(self, repo: str, number: int) -> PullRequest | None: ...
    def pulls_for_branch(self, repo: str, branch: str) -> list[PullRequest]: ...
    def ci_status(self, repo: str, sha: str) -> str: ...          # success, failure, pending, unknown
    def approved(self, repo: str, number: int) -> bool: ...
    def create_pull(self, repo: str, head: str, base: str, title: str, body: str) -> dict: ...
    def comment(self, repo: str, number: int, body: str) -> dict: ...
    def merge(self, repo: str, number: int, sha: str, method: str) -> dict: ...


def token_env(token_file: str | None) -> dict[str, str]:
    """`GH_TOKEN` read now from `token_file`, or nothing. An unreadable or empty file fails the call: never another identity."""
    if not token_file:
        return {}
    with open(token_file, encoding="utf-8") as f:
        token = f.read().strip()
    if not token:
        raise RuntimeError(f"{token_file} holds no token")
    return {"GH_TOKEN": token}


def combine_ci(check_runs: Sequence[Mapping[str, Any]], statuses: Sequence[Mapping[str, Any]]) -> str:
    """One verdict for a commit. No check and no status at all is `unknown`, never success."""
    if not check_runs and not statuses:
        return "unknown"
    if any(r.get("status") != "completed" for r in check_runs) or any(s.get("state") == "pending" for s in statuses):
        return "pending"
    if all(r.get("conclusion") in OK_CONCLUSIONS for r in check_runs) and all(s.get("state") == "success"
                                                                            for s in statuses):
        return "success"
    return "failure"


def review_approved(reviews: Sequence[Mapping[str, Any]],
                    counts: Callable[[str, Mapping[str, Any]], bool] = lambda who, review: True) -> bool:
    """Approved when, keeping each reviewer's latest decisive review, none requests changes and at least one
    approval `counts` (a person who may approve: see `GhCli.approved`). Comments do not count either way; a request
    for changes blocks whoever made it."""
    latest: dict[str, Mapping[str, Any]] = {}
    for r in reviews:   # GitHub returns them oldest first
        state, who = r.get("state"), (r.get("user") or {}).get("login")
        if who and state in ("APPROVED", "CHANGES_REQUESTED", "DISMISSED"):
            latest[who] = r
    states = {r.get("state") for r in latest.values()}
    return "CHANGES_REQUESTED" not in states and any(
        r.get("state") == "APPROVED" and counts(who, r) for who, r in latest.items())


def _pull(d: Mapping[str, Any]) -> PullRequest:
    head, base = d["head"], d["base"]
    return PullRequest(int(d["number"]), str(d["state"]), str(head["ref"]),
                       str((head.get("repo") or {}).get("full_name", "")), str(head["sha"]), str(base["ref"]))


class GhCli:
    """`gh api` as the transport. `gh` reads its identity from GH_TOKEN (or its own login): the sidecar's."""

    def __init__(self, gh: str = "gh", env: Mapping[str, str] | None = None, timeout: float = 30,
                 not_reviewers: frozenset[str] = frozenset(), token_file: str | None = None) -> None:
        self.gh, self.timeout = gh, timeout
        # A file read on every call: a short-lived token (a GitHub App's, one hour) that something else renews in place.
        self.token_file = token_file
        # Accounts whose approval never counts: the agents' own (another agent's approval is no person's review).
        self.not_reviewers = frozenset(n.lower() for n in not_reviewers)
        self.env = {**os.environ, **(env or {}), "GH_PROMPT_DISABLED": "1"}

    def _api(self, path: str, method: str = "GET", body: Mapping[str, Any] | None = None) -> Any:
        cmd = [self.gh, "api", "-X", method, "-H", "Accept: application/vnd.github+json", path]
        if body is not None:
            cmd += ["--input", "-"]
        p = subprocess.run(cmd, input=json.dumps(body) if body is not None else None, capture_output=True,
                           text=True, env={**self.env, **token_env(self.token_file)}, timeout=self.timeout, check=False)
        if p.returncode != 0:
            raise RuntimeError(f"gh api {method} {path} failed: {p.stderr.strip()[:300]}")
        return json.loads(p.stdout) if p.stdout.strip() else {}

    def pull(self, repo: str, number: int) -> PullRequest | None:
        try:
            return _pull(self._api(f"repos/{repo}/pulls/{int(number)}"))
        except RuntimeError:
            return None

    def pulls_for_branch(self, repo: str, branch: str) -> list[PullRequest]:
        owner = repo.split("/", 1)[0]
        return [_pull(d) for d in self._api(f"repos/{repo}/pulls?state=open&head={owner}:{branch}")]

    def ci_status(self, repo: str, sha: str) -> str:
        runs = self._api(f"repos/{repo}/commits/{sha}/check-runs?per_page=100").get("check_runs", [])
        statuses = self._api(f"repos/{repo}/commits/{sha}/status").get("statuses", [])
        return combine_ci(runs, statuses)

    def approved(self, repo: str, number: int) -> bool:
        """A review counts as GitHub counts it for a protected branch, and stricter: a person with write access to the
        repository, who is not the PR's author, not a bot, and not one of the agents' accounts. GitHub shows anyone's
        approval on a public repository; this never lets an outsider's, or the read-only worker's, stand in for a
        reviewer. A permission that cannot be read does not count."""
        author = ((self._api(f"repos/{repo}/pulls/{int(number)}").get("user")) or {}).get("login", "")

        def counts(who: str, review: Mapping[str, Any]) -> bool:
            if who.lower() in self.not_reviewers or who.lower() == author.lower() \
                    or (review.get("user") or {}).get("type") == "Bot":
                return False
            try:
                permission = self._api(f"repos/{repo}/collaborators/{who}/permission").get("permission")
            except RuntimeError:
                return False
            return permission in WRITE_PERMISSIONS
        return review_approved(self._api(f"repos/{repo}/pulls/{int(number)}/reviews?per_page=100"), counts)

    def create_pull(self, repo: str, head: str, base: str, title: str, body: str) -> dict:
        d = self._api(f"repos/{repo}/pulls", "POST", {"head": head, "base": base, "title": title, "body": body})
        return {"number": d.get("number"), "url": d.get("html_url")}

    def comment(self, repo: str, number: int, body: str) -> dict:
        d = self._api(f"repos/{repo}/issues/{int(number)}/comments", "POST", {"body": body})
        return {"id": d.get("id"), "url": d.get("html_url")}

    def merge(self, repo: str, number: int, sha: str, method: str) -> dict:
        # `sha`: GitHub refuses the merge if the head moved since the gateway resolved it.
        d = self._api(f"repos/{repo}/pulls/{int(number)}/merge", "PUT", {"sha": sha, "merge_method": method})
        return {"merged": bool(d.get("merged")), "sha": d.get("sha")}
