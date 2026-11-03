"""Git for `branch.push`: the sidecar publishes a commit the agent host staged, with the sidecar's own identity.

Transport: the host fetches a ticket's HEAD into a bare mirror it owns, mounted read-only into the sidecar. The sidecar
fetches the commit from that mirror into ITS OWN bare repository and pushes from there. It never runs git inside the
mirror for anything but the fetch (`upload-pack`, which ignores the repository's hooks and runs no command its
configuration names), so whatever the agent left in the mirror's configuration cannot steer the push: no URL rewrite,
no hook, no credential helper. Facts are read locally, after fetching the remote branch and base, never from the agent.
"""
from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path
from typing import Protocol

from intentplane.connectors.github import token_env

SHA = re.compile(r"^[0-9a-f]{40}$")
REF = re.compile(r"^(?!.*\.\.)(?!/)[A-Za-z0-9._/-]{1,200}$")
# Paths a commit can change to alter what CI runs with the repository's secrets.
CI_PATHS = (".github/workflows/", ".github/actions/", ".gitlab-ci.yml", ".circleci/", ".buildkite/", ".travis.yml",
            "azure-pipelines.yml", "Jenkinsfile", ".drone.yml", "bitbucket-pipelines.yml")

# Files that choose what the project installs and from where: manifests, lockfiles, registry configuration. A change
# to one brings in code nobody on the team wrote, which runs at install time and at run time.
DEPENDENCY_FILES = frozenset({
    "package.json", "package-lock.json", "npm-shrinkwrap.json", "yarn.lock", "pnpm-lock.yaml", "pnpm-workspace.yaml",
    ".npmrc", ".yarnrc", ".yarnrc.yml", "bun.lockb", "requirements.txt", "constraints.txt", "pyproject.toml", "setup.py",
    "setup.cfg", "Pipfile", "Pipfile.lock", "poetry.lock", "uv.lock", "pip.conf", "go.mod", "go.sum", "Cargo.toml",
    "Cargo.lock", "Gemfile", "Gemfile.lock", "pom.xml", "build.gradle", "build.gradle.kts", "settings.gradle",
    "settings.gradle.kts", "gradle.properties", "composer.json", "composer.lock", "packages.config", "Directory.Packages.props",
    "NuGet.config", "nuget.config", "mix.exs", "mix.lock", "Package.swift", "Package.resolved", "Podfile", "Podfile.lock",
})

# Files coding agents read as instructions on every later task (dsh-superagent's workers inherit AGENTS.md): text an
# injected agent writes there would steer every agent after it, once merged.
INSTRUCTION_FILES = frozenset({"AGENTS.md", "CLAUDE.md", "GEMINI.md", ".cursorrules", ".windsurfrules", ".clinerules",
                               "copilot-instructions.md"})
INSTRUCTION_DIRS = (".cursor/rules/", ".github/instructions/", ".claude/")


class GitError(RuntimeError):
    pass


class BranchStore(Protocol):
    def stage(self, branch: str, sha: str) -> bool: ...                 # the commit is in the sidecar's repository
    def fast_forward(self, repo: str, branch: str, sha: str) -> bool: ...   # remote head is an ancestor, or no branch yet
    def changed_paths(self, repo: str, base: str, sha: str) -> list[str] | None: ...   # since the merge base; None: unknown
    def workflows(self, sha: str) -> dict[str, str] | None: ...   # GitHub Actions workflow files of the commit; None: unknown
    def push(self, repo: str, branch: str, sha: str) -> dict: ...


def _ci(path: str) -> bool:
    return any(path.startswith(c) if c.endswith("/") else path == c for c in CI_PATHS)


def touches_ci(paths: list[str] | None) -> bool:
    """Unknown counts as touching CI."""
    return paths is None or any(_ci(p) for p in paths)


def _dependency(path: str) -> bool:
    name = path.rsplit("/", 1)[-1]
    return name in DEPENDENCY_FILES or (name.startswith("requirements") and name.endswith((".txt", ".in"))) \
        or name.endswith((".csproj", ".fsproj", ".vbproj"))


def _instructions(path: str) -> bool:
    return path.rsplit("/", 1)[-1] in INSTRUCTION_FILES or any(f"/{d}" in f"/{path}" for d in INSTRUCTION_DIRS)


def touches_instructions(paths: list[str] | None) -> bool:
    """Unknown counts as touching agent instructions."""
    return paths is None or any(_instructions(p) for p in paths)


def touches_dependencies(paths: list[str] | None) -> bool:
    """Unknown counts as touching dependencies."""
    return paths is None or any(_dependency(p) for p in paths)


class LocalGit:
    """`BranchStore` over the git CLI. `remote` is a URL template (`{repo}`); GitHub through `gh auth git-credential`."""

    def __init__(self, workdir: str | Path, mirror: str | Path, remote: str = "https://github.com/{repo}.git",
                 gh: str = "gh", timeout: float = 120, token_file: str | None = None) -> None:
        self.workdir, self.mirror, self.remote, self.timeout = Path(workdir), Path(mirror), remote, timeout
        self.token_file = token_file   # read on every call, as GhCli does
        self.env = {**os.environ, "GIT_TERMINAL_PROMPT": "0", "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": os.devnull}
        for name in ("GIT_DIR", "GIT_WORK_TREE", "GIT_CONFIG_COUNT", "GIT_CONFIG_PARAMETERS", "GIT_ASKPASS", "SSH_ASKPASS"):
            self.env.pop(name, None)
        self.config = ["-c", "credential.helper=", "-c", f"credential.https://github.com.helper=!{gh} auth git-credential",
                       "-c", "core.hooksPath=/dev/null", "-c", "protocol.file.allow=always"]

    def _git(self, *args: str, check: bool = True) -> subprocess.CompletedProcess:
        if not (self.workdir / "HEAD").exists():
            subprocess.run(["git", "init", "-q", "--bare", str(self.workdir)], env=self.env, check=True,
                           capture_output=True, timeout=self.timeout)
        try:
            env = {**self.env, **token_env(self.token_file)}
        except (OSError, RuntimeError) as e:
            raise GitError(str(e)) from e
        p = subprocess.run(["git", *self.config, "--git-dir", str(self.workdir), *args], env=env, text=True,
                           capture_output=True, timeout=self.timeout, check=False)
        if check and p.returncode != 0:
            raise GitError((p.stderr or p.stdout).strip()[:500] or f"git {args[0]} failed")
        return p

    def _url(self, repo: str) -> str:
        return self.remote.format(repo=repo)

    def stage(self, branch: str, sha: str) -> bool:
        if not (REF.match(branch) and SHA.match(sha)):
            return False
        # upload-pack runs in the mirror, owned by another user: allow that one directory, nothing else.
        upload = f"git -c safe.directory={self.mirror} upload-pack"
        fetched = self._git("fetch", "-q", "--no-tags", "--no-recurse-submodules", "--upload-pack", upload,
                            str(self.mirror), f"+refs/heads/{branch}:refs/staged/{branch}", check=False)
        return fetched.returncode == 0 and self._git("rev-parse", f"refs/staged/{branch}").stdout.strip() == sha

    def fast_forward(self, repo: str, branch: str, sha: str) -> bool:
        p = self._git("ls-remote", "--heads", self._url(repo), f"refs/heads/{branch}", check=False)
        if p.returncode != 0:
            return False   # unknown is not a fast-forward
        out = p.stdout.split()
        if not out or out[0] == sha:
            return True
        # The remote head must be fetched to tell whether it is an ancestor of the commit to publish.
        return self._fetch(repo, branch) == out[0] and             self._git("merge-base", "--is-ancestor", out[0], sha, check=False).returncode == 0

    def _fetch(self, repo: str, branch: str) -> str | None:
        if self._git("fetch", "-q", "--no-tags", self._url(repo), f"+refs/heads/{branch}:refs/remote/{branch}",
                     check=False).returncode != 0:
            return None
        return self._git("rev-parse", f"refs/remote/{branch}").stdout.strip()

    def changed_paths(self, repo: str, base: str, sha: str) -> list[str] | None:
        base_sha = self._fetch(repo, base)
        if base_sha is None:
            return None
        merge_base = self._git("merge-base", base_sha, sha, check=False).stdout.strip()
        if not merge_base:
            return None
        return self._git("diff", "--name-only", "--no-renames", merge_base, sha).stdout.split()

    def workflows(self, sha: str) -> dict[str, str] | None:
        if not SHA.match(sha):
            return None
        listed = self._git("ls-tree", "-r", "-z", "--name-only", sha, "--", ".github/workflows", check=False)
        if listed.returncode != 0:
            return None
        files = {}
        for path in filter(None, listed.stdout.split("\0")):
            if path.endswith((".yml", ".yaml")):
                shown = self._git("show", f"{sha}:{path}", check=False)
                if shown.returncode != 0:
                    return None
                files[path] = shown.stdout
        return files

    def push(self, repo: str, branch: str, sha: str) -> dict:
        # No force, no `+`: a remote that moved since the decision rejects the push.
        self._git("push", "-q", "--no-verify", self._url(repo), f"{sha}:refs/heads/{branch}")
        return {"pushed": True, "branch": branch, "sha": sha}
