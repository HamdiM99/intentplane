"""python -m intentplane.sidecar --config intentplane-sidecar.toml

Reads the host's credential from INTENTPLANE_SIDECAR_SECRET and GitHub's from the environment `gh` uses (GH_TOKEN):
both are the sidecar's, never the agent's. Each may come from a file instead (`*_FILE`, such as a Docker secret);
GH_TOKEN_FILE is read again on every GitHub call, so a short-lived token (a GitHub App installation token, one hour)
can be renewed in place by something else, without a restart.
"""
from __future__ import annotations

import argparse
import logging
import os

import uvicorn

from intentplane.approvals import LocalApprovals
from intentplane.approvals.board import FOUR_EYES, STEP_UP
from intentplane.connectors.git import LocalGit
from intentplane.connectors.github import GhCli
from intentplane.proxy.gate import FileLedger

from .app import create_app
from .config import load
from .service import Sidecar


def _secret(name: str) -> str:
    """`<name>_FILE` (such as a Docker secret) when set, else `<name>`, else empty."""
    path = os.environ.get(f"{name}_FILE")
    if path:
        with open(path, encoding="utf-8") as f:
            return f.read().strip()
    return os.environ.get(name, "")


def main() -> None:
    ap = argparse.ArgumentParser(prog="python -m intentplane.sidecar")
    ap.add_argument("--config", required=True)
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    secret = _secret("INTENTPLANE_SIDECAR_SECRET")
    if len(secret) < 32:
        raise SystemExit("set INTENTPLANE_SIDECAR_SECRET or INTENTPLANE_SIDECAR_SECRET_FILE (at least 32 characters) for the host")
    token_file = os.environ.get("GH_TOKEN_FILE") or None
    if token_file:
        _secret("GH_TOKEN")   # readable now, or no start
    cfg = load(args.config)
    git = LocalGit(cfg.git_workdir, cfg.git_mirror, cfg.git_remote, cfg.gh, token_file=token_file) if cfg.git_mirror else None
    github = GhCli(cfg.gh, not_reviewers=frozenset(cfg.not_reviewers), token_file=token_file)
    sidecar = Sidecar(cfg, github, FileLedger(cfg.receipts) if cfg.receipts else None, git=git)
    if cfg.approvals is not None:
        if cfg.operator not in cfg.approvals.approvers:
            logging.getLogger("intentplane.sidecar").warning("[approvals.approvers] has no passcode for the operator %r: step-ups will be refused",
                            cfg.operator)
        others = set(cfg.approvals.approvers) - {cfg.operator}
        effects = frozenset({STEP_UP} | ({FOUR_EYES} if others else set()))
        sidecar.channel = LocalApprovals(sidecar.approvals, cfg.approvals.approvers, host=cfg.approvals.host,
                                         port=cfg.approvals.port, public_url=cfg.approvals.public_url,
                                         ttl=cfg.approvals.ttl, effects=effects)   # its own audit log
    uvicorn.run(create_app(sidecar, secret), host=cfg.host, port=cfg.port, log_level="warning")


if __name__ == "__main__":
    main()
