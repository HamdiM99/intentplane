"""Sidecar configuration (TOML). Relative paths are resolved against the config file's directory.

    [sidecar]
    listen = "127.0.0.1:8770"
    operator = "mustapha"          # the person accountable for the agent: every mandate's principal, confirms step-ups
    branch_prefix = "superagent/"  # a task's branch is <prefix><short name>; nothing else is its branch
    mandate_ttl = 28800            # seconds; the host re-issues on resume
    max_calls = 50                 # executed actions per mandate
    receipts = "receipts.jsonl"
    holds = "holds.json"           # tasks a person withdrew and held; kept across restarts

    [github]
    repo = "owner/name"
    base = "main"                  # the branch the agent's PRs target
    protected = ["main", "release"]
    not_reviewers = ["agent-bot", "worker-reader"]   # the agents' accounts: their approval never counts as a review

    [scopes]                       # what a task may do, by how it was accepted
    explicit = ["pr.create", "pr.comment", "pr.merge", "branch.push"]
    inferred = ["pr.create", "pr.comment", "branch.push"]

    [git]                          # optional; enables branch.push
    mirror = "/mirror"             # the host's bare mirror, mounted read-only: where the host stages a task's commit
    workdir = "git"                # the sidecar's own bare repository, which it pushes from

    [approvals]                    # optional; without it, anything needing a person is refused
    listen = "127.0.0.1:8771"
    ttl = 900
    [approvals.approvers]          # passcode hashes: python -m intentplane.approvals hash-passcode
    mustapha = "scrypt$..."        # the operator: step-up
    reviewer = "scrypt$..."        # anyone else: four eyes

The host's credential is never in this file: it is read from the environment variable INTENTPLANE_SIDECAR_SECRET
(a name agent harnesses strip from the environment of the commands their agents run).
"""
from __future__ import annotations

import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

if sys.version_info >= (3, 11):
    import tomllib
else:  # pragma: no cover
    import tomli as tomllib

STRENGTHS = ("explicit", "inferred")
REPO = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
BRANCH_PREFIX = re.compile(r"^[A-Za-z0-9._/-]{0,60}$")


@dataclass(frozen=True)
class ApprovalsSpec:
    approvers: dict[str, str]
    host: str = "127.0.0.1"
    port: int = 8771
    public_url: str = "http://127.0.0.1:8771"
    ttl: float = 900


@dataclass
class SidecarConfig:
    operator: str
    repo: str
    base: str = "main"
    protected: tuple[str, ...] = ("main", "master", "release")
    scopes: dict[str, frozenset[str]] = field(default_factory=lambda: {
        "explicit": frozenset({"pr.create", "pr.comment", "pr.merge", "branch.push"}),
        "inferred": frozenset({"pr.create", "pr.comment", "branch.push"})})
    branch_prefix: str = "superagent/"
    mandate_ttl: float = 8 * 3600
    max_calls: int | None = 50
    host: str = "127.0.0.1"
    port: int = 8770
    receipts: Path | None = None
    holds: Path | None = None
    gh: str = "gh"
    not_reviewers: tuple[str, ...] = ()
    approvals: ApprovalsSpec | None = None
    git_mirror: Path | None = None
    git_workdir: Path | None = None
    git_remote: str = "https://github.com/{repo}.git"

    def __post_init__(self) -> None:
        if not REPO.match(self.repo):
            raise ValueError(f"github.repo must be owner/name, got {self.repo!r}")
        if not BRANCH_PREFIX.match(self.branch_prefix):
            raise ValueError("sidecar.branch_prefix: letters, digits, . _ - / only")
        if set(self.scopes) != set(STRENGTHS):
            raise ValueError(f"[scopes] needs exactly {STRENGTHS}")
        if "pr.merge" in self.scopes["inferred"]:
            raise ValueError("an inferred task cannot be given pr.merge (the policy forbids it anyway)")


def _hostport(value: str, default_port: int) -> tuple[str, int]:
    host, _, port = value.rpartition(":")
    return (host or "127.0.0.1"), int(port or default_port)


def load(path: str | Path) -> SidecarConfig:
    path = Path(path)
    raw = tomllib.loads(path.read_text(encoding="utf-8"))
    side, gh, scopes = raw.get("sidecar", {}), raw.get("github", {}), raw.get("scopes")
    host, port = _hostport(side.get("listen", "127.0.0.1:8770"), 8770)
    approvals = None
    if "approvals" in raw:
        a = raw["approvals"]
        ahost, aport = _hostport(a.get("listen", "127.0.0.1:8771"), 8771)
        approvals = ApprovalsSpec(dict(a.get("approvers", {})), ahost, aport,
                                  a.get("public_url", f"http://{ahost}:{aport}"), float(a.get("ttl", 900)))
    cfg = SidecarConfig(
        operator=side["operator"], repo=gh["repo"], base=gh.get("base", "main"),
        protected=tuple(gh.get("protected", ("main", "master", "release"))),
        branch_prefix=side.get("branch_prefix", "superagent/"),
        mandate_ttl=float(side.get("mandate_ttl", 8 * 3600)), max_calls=side.get("max_calls", 50),
        host=host, port=port, gh=gh.get("cli", "gh"), not_reviewers=tuple(gh.get("not_reviewers", ())),
        receipts=(path.parent / side["receipts"]) if side.get("receipts") else None,
        holds=(path.parent / side["holds"]) if side.get("holds") else None, approvals=approvals,
        git_mirror=Path(raw["git"]["mirror"]) if raw.get("git", {}).get("mirror") else None,
        git_workdir=(path.parent / raw["git"].get("workdir", "git")) if raw.get("git", {}).get("mirror") else None)
    if scopes is not None:
        cfg.scopes = {k: frozenset(scopes.get(k, ())) for k in STRENGTHS}
        cfg.__post_init__()
    return cfg
