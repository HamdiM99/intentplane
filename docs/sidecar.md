# The sidecar: one gateway for an agent host running many tasks

`intentplane-mcp` serves one user in one session. An autonomous agent host (a "super-agent" router dispatching
tasks to concurrent workers, such as DeepSeek Harness's `dsh-superagent` prototype) needs something else: one
gateway, one mandate **per task**, issued when the host accepts the task and withdrawn when it ends.

```
host (trusted)  ── POST /v1/mandates ──────────►  sidecar  ◄── POST /v1/propose (task mandate) ──  worker tools
                ── DELETE /v1/mandates/t {hold} ─►   │
                ── POST /v1/messages/screen ────►   │   (each message the host sends: answers, notices)
                ── POST /v1/mandates/t/release ──►   ├── GitHub (the gateway's identity), approval page, receipts
host's mirror (read-only in the sidecar) ───────────►   └── the sidecar's own git repository (branch.push)
```

## The mandate of a task

The host sends what it decided, never what the agent says:

| Field | Meaning |
|---|---|
| `task` | the task id (also the key to withdraw it) |
| `strength` | `explicit`: a person asked; `inferred`: the agent took it up on its own |
| `short_name` | the task's branch is `branch_prefix + short_name`; no other branch is its own |
| `threads` | issue / PR numbers the task was accepted on (where it may comment) |
| `rules`, `requester` | recorded in the receipts |

The sidecar adds the repository, the base branch and the scopes for that strength (`[scopes]`), signs it, and
records `mandate.issued` in the hash-chained receipts. Every later decision names the mandate id, so receipts
are joined to tasks. Re-issuing a task (resume, a new thread) withdraws its previous mandate; withdrawing one
task never affects another (`Authority.revoke_token`).

**A person's withdrawal is final.** `DELETE /v1/mandates/{task}` with `{"hold": true}` (from the host, on a
person's decision, such as in an incident) withdraws the mandate and holds the task: `POST /v1/mandates` answers
409 until `POST /v1/mandates/{task}/release`. Holds are kept in `[sidecar] holds` (a JSON file) across restarts;
an unreadable file stops the sidecar. A call with a withdrawn mandate is refused at the stage `withdrawn`, which
tells a host never to issue again on its own; `authn` (an expired mandate, or one signed before a restart) still
invites one re-issue. `task.held` and `task.released` are receipts.

## What a task may do (GitHub pack)

See [`intentplane/packs/github.py`](../intentplane/packs/github.py) and its Cedar policy. In short: open a PR
only from its own branch onto the mandated base; comment only on its own threads and PRs; merge only if the task
is explicit, CI is green on the PR's current head, and a person confirms (the operator; a second person on a
protected base without an approved review). GitHub merges only the exact head the gateway resolved.

An approved review counts only from a person with write access to the repository who is not the PR's author, not a
bot, and not in `[github] not_reviewers` (list the agents' own accounts there): GitHub shows anyone's approval, and
an outsider's, or another agent's, must not turn four eyes into the operator's confirmation alone.

`branch.push` (with `[git]`): the host stages a task's commit in a bare mirror it owns, mounted read-only into the
sidecar, and proposes `{repo, branch, sha}`. The branch must be the mandate's; the sidecar fetches the commit into
its own repository (git runs only `upload-pack` in the mirror, so the mirror's configuration and hooks play no
part), checks that it fast-forwards the branch (there is no force push to ask for), and whether it changes CI
configuration (`.github/workflows/`, `.github/actions/` and other CI files): such a commit could grant itself the
repository's CI secrets, so the operator confirms it; so does a commit that changes the project's dependencies (manifests,
lockfiles, registry configuration such as `.npmrc`), which brings in code nobody on the team wrote, and so does a
commit that changes what agents read as instructions on every task (`AGENTS.md`, `CLAUDE.md`, `.cursor/rules/`, ...). The sidecar pushes
with its own identity, never forced.

Pushing the branch runs the repository's workflows on the task's code: its tests, its build scripts. GitHub withholds
secrets only from forks, and a task's branch is in the repository. So `branch.push` is refused, whoever approves
(`forbid-push-ci-exposes-secrets`), while a workflow of the commit would run on that branch with a secret other than
`GITHUB_TOKEN` (`intentplane.connectors.workflows`, which needs the `sidecar` extra's PyYAML). The repository fixes it
by keeping such jobs off task branches for each event that runs them, for example:

```yaml
jobs:
  deploy-check:
    if: ${{ !startsWith(github.ref_name, 'superagent/') && !startsWith(github.head_ref, 'superagent/') }}
```

or by not running the workflow for them (`branches-ignore: ['superagent/**']` on `push`). Unreadable workflow files,
`workflow_run` and `merge_group` triggers, and `||` in a guard count as exposed. A deployment environment does not
count as a guard: whether its rules release a secret to the branch cannot be read from the file.

Free text (titles, bodies, comments) is recorded, and refused only when it looks like it carries a credential
(`forbid-text-with-secret`, `intentplane.textscan`): a defence-in-depth heuristic, which a split or encoded secret
defeats and some legitimate text trips (hashes other than hex digests, long random identifiers).

## Messages the host sends

An agent host also talks: it answers the requester, acknowledges, posts notices, on GitHub or a chat. The host
chooses the conversation from its own records; a model wrote the text, after reading untrusted input. Before
sending, the host asks `POST /v1/messages/screen` (host secret) with `{task, source, conversation, kind, text}`:

- `{"status": "denied", "stage": "message", ...}` when the text carries what looks like a secret
  (`intentplane.textscan`, a heuristic) or when a person holds the task; the host drops the message.
- `{"status": "allowed"}` otherwise.

Each answer is a receipt (`message.screened`) holding the text's SHA-256 and length, never the text, since a
refused one may hold the secret. A host must treat no answer as "not yet": the message waits, it is never sent
unscreened.

## Deployment notes

- **GitHub identity.** A GitHub App installation token (one hour, scoped to the repository) is better than a personal
  token: put it in the file `GH_TOKEN_FILE` names and renew it there before it expires; the sidecar reads the file on
  every GitHub call and push, and refuses the call if it is empty. Give the App contents, pull requests and issues
  (write), checks and metadata (read), nothing else.

- The host's credential is `INTENTPLANE_SIDECAR_SECRET`; GitHub's is the `gh` identity of the sidecar's process.
  Both may come from files (`INTENTPLANE_SIDECAR_SECRET_FILE`, `GH_TOKEN_FILE`), such as Docker secrets. Agent
  harnesses usually strip variables named `*SECRET*` / `*TOKEN*` from the commands agents run; check yours.
- Give the agent's own `gh` a **read-only** identity, or block its write subcommands: otherwise the agent can
  bypass the gateway for anything it could already do. The gateway decides what passes through it, not what goes
  around it.
- Keep the mandate out of the model: the host's tool implementation should hold each task's token and attach it
  to `/v1/propose`, so the model never sees it.
- **Same OS user is not a boundary.** If workers run as the same user as the host and the sidecar, a worker
  with a shell reads their process environments and their files (measured in a test deployment).
  Run the sidecar in its own container with its secrets as files, and confine the workers' commands so they see
  neither the host's processes nor its credential files (for example a bubblewrap sandbox that masks those files,
  with no network but an egress proxy limited to the hosts the task needs).
- State is in memory (mandates, budgets, approvals), except holds: a restart withdraws every mandate; the host
  re-issues, except for a held task.
