"""Which GitHub Actions jobs would run a task's code with the repository's secrets.

Pushing a task's branch, or opening its pull request, runs the repository's workflows on the task's commit: its tests,
its build scripts, anything they import. A job that also holds a secret hands it to whatever the agent wrote. GitHub
withholds secrets only from forks; a task's branch lives in the repository itself.

This reads the workflow files of the commit to publish (the ones GitHub runs for it) and lists each job that both runs
for the task's branch and can read a secret other than `GITHUB_TOKEN`. It errs towards "exposed": a trigger it does not
model, a filter it cannot read, a file it cannot parse. A job is safe when it holds no secret, or when its `if:` keeps
it off the task's branches for each event that runs it: `!startsWith(github.ref_name, 'superagent/')` for a push,
`!startsWith(github.head_ref, 'superagent/')` for a pull request. Deployment environments do not count: whether an environment's branch rules release its secrets, and
whether a name resolves to an environment secret or a repository one, cannot be read from the file.
"""
from __future__ import annotations

import json
import re
from typing import Any

SECRET = re.compile(r"secrets\s*\.\s*(?!GITHUB_TOKEN\b)[A-Za-z_]|secrets\s*\[|toJSON\s*\(\s*secrets\s*\)", re.IGNORECASE)
RUNS_FOR_BRANCHES = ("push", "pull_request", "pull_request_target", "workflow_run", "merge_group")


def _glob(pattern: str) -> re.Pattern:
    """GitHub's filter patterns: `**` any characters, `*` any but `/`, `?` one, `+` one or more of the previous."""
    out, i = "", 0
    while i < len(pattern):
        if pattern.startswith("**", i):
            out, i = out + ".*", i + 2
        elif pattern[i] == "*":
            out, i = out + "[^/]*", i + 1
        elif pattern[i] == "?":
            out, i = out + "[^/]", i + 1
        elif pattern[i] == "+":
            out, i = out + "+", i + 1
        else:
            out, i = out + re.escape(pattern[i]), i + 1
    return re.compile(f"^{out}$")


def _matches(patterns: list, branch: str) -> bool:
    """`branches:` semantics: the last pattern that matches decides, a `!` pattern excludes."""
    hit = False
    for p in patterns:
        p = str(p)
        negated = p.startswith("!")
        if _glob(p[1:] if negated else p).match(branch):
            hit = not negated
    return hit


def _push_runs(config: Any, branch: str) -> bool:
    if not isinstance(config, dict):
        return True
    if "branches" in config:
        return _matches(list(config["branches"] or []), branch)
    if "branches-ignore" in config:
        return not _matches(list(config["branches-ignore"] or []), branch)
    # Only tag filters: branch pushes do not run it.
    return not ("tags" in config or "tags-ignore" in config)


def _events(on: Any, branch: str) -> set[str] | None:
    """The events that run the workflow on `branch`'s code; None when `on:` cannot be read (assume all of them)."""
    if isinstance(on, str):
        on = [on]
    if isinstance(on, list):
        return {str(t) for t in on} & set(RUNS_FOR_BRANCHES)
    if not isinstance(on, dict):
        return None
    # A pull request's workflows run on the task's code whatever its base; workflow_run follows such a run.
    return {str(t) for t, config in on.items() if t in RUNS_FOR_BRANCHES and (t != "push" or _push_runs(config, branch))}


def _guarded(condition: Any, prefix: str, events: set[str]) -> bool:
    """An `if:` that keeps the job off every branch under `prefix` for each event that runs it, and that an `||` cannot
    widen. A push names its branch in `github.ref_name` / `github.ref`, a pull request in `github.head_ref` (its
    `ref_name` is `<number>/merge`); `workflow_run` and `merge_group` name it elsewhere, so no guard here covers them."""
    if not isinstance(condition, str) or "||" in condition or events - {"push", "pull_request", "pull_request_target"}:
        return False
    p = re.escape(prefix)

    def negates(field: str, value: str) -> bool:
        return bool(re.search(rf"!\s*startsWith\(\s*github\.{field}\s*,\s*'{value}'\s*\)", condition))
    on_push = negates("ref_name", p) or negates("ref", f"refs/heads/{p}")
    on_pull = negates("head_ref", p)
    return (on_push or "push" not in events) and (on_pull or not events & {"pull_request", "pull_request_target"})


def exposed_jobs(files: dict[str, str], branch: str, prefix: str) -> list[str] | None:
    """`path: job` for each job that runs `branch`'s code with a secret; None when the files cannot be read."""
    try:
        import yaml
    except ImportError:
        return None
    exposed: list[str] = []
    for path, text in sorted(files.items()):
        try:
            workflow = yaml.safe_load(text)
        except yaml.YAMLError:
            return None
        if not isinstance(workflow, dict):
            return None
        # YAML 1.1 reads the key `on` as true.
        events = _events(workflow.get("on", workflow.get(True)), branch)
        if events is None:
            events = set(RUNS_FOR_BRANCHES)
        if not events:
            continue
        shared = json.dumps({k: v for k, v in workflow.items() if k in ("env", "defaults")})
        jobs = workflow.get("jobs")
        if not isinstance(jobs, dict):
            continue
        for name, job in jobs.items():
            if not isinstance(job, dict):
                return None
            holds = bool(SECRET.search(shared + json.dumps({k: v for k, v in job.items() if k != "if"}))) \
                or job.get("secrets") is not None   # a reusable workflow called with `secrets: inherit` or a map
            if holds and not _guarded(job.get("if"), prefix, events):
                exposed.append(f"{path}: {name}")
    return exposed
