// GitHub pack (autonomous coding agents). Default deny; forbid overrides permit at every approval level.
// approval_level: 0 none, 1 step-up (the operator accountable for the agent), 2 four eyes (someone else).
// Every context value is read by the gateway from GitHub or from the signed mandate, never from the agent.

@id("permit-pr-create")
permit (principal, action == Action::"pr.create", resource);

@id("permit-pr-comment")
permit (principal, action == Action::"pr.comment", resource);

// Defence in depth on free text: a title or body that looks like it carries a credential is not published,
// whoever approves. A heuristic (intentplane.textscan): encoded or split secrets pass, some legitimate text does not.
@id("forbid-text-with-secret")
forbid (principal, action in [Action::"pr.comment", Action::"pr.create"], resource)
when { context.text.secret };

// Work the agent took up on its own (an inferred task) never merges, whoever approves.
@id("forbid-merge-inferred-task")
forbid (principal, action == Action::"pr.merge", resource)
when { context.strength != "explicit" };

@id("forbid-merge-ci-not-green")
forbid (principal, action == Action::"pr.merge", resource)
when { context.pr.ci_status != "success" };

// The commit the agent names must be the PR's current head (and GitHub re-checks it at merge time).
@id("forbid-merge-stale-head")
forbid (principal, action == Action::"pr.merge", resource)
when { !context.pr.head_matches };

// A merge is never silent: at least the operator confirms the resolved operation.
@id("permit-merge-unprotected-step-up")
permit (principal, action == Action::"pr.merge", resource)
when { !context.pr.protected && context.approval_level >= 1 };

@id("permit-merge-protected-reviewed-step-up")
permit (principal, action == Action::"pr.merge", resource)
when { context.pr.protected && context.pr.approved_review && context.approval_level >= 1 };

@id("permit-merge-protected-four-eyes")
permit (principal, action == Action::"pr.merge", resource)
when { context.pr.protected && context.approval_level >= 2 };

// Publishing the task's branch. There is no force push: a commit that does not extend the branch is refused.
@id("forbid-push-not-staged")
forbid (principal, action == Action::"branch.push", resource)
when { !context.push.staged };

@id("forbid-push-not-fast-forward")
forbid (principal, action == Action::"branch.push", resource)
when { !context.push.fast_forward };

// CI that would run the task's commit (its tests, its build scripts) with a repository secret hands that secret to the
// agent's code: refused whoever approves, until the workflow keeps that job off task branches or holds no secret.
@id("forbid-push-ci-exposes-secrets")
forbid (principal, action == Action::"branch.push", resource)
when { context.push.exposes_secrets };

@id("permit-push")
permit (principal, action == Action::"branch.push", resource)
when { !context.push.touches_workflows && !context.push.touches_dependencies && !context.push.touches_instructions };

// A commit that changes CI configuration can grant itself the repository's CI secrets, and one that changes the
// project's dependencies (manifests, lockfiles, registry configuration) brings in code nobody on the team wrote, and
// one that changes the instructions agents read on every task (AGENTS.md and the like) steers every later agent:
// the operator confirms it.
@id("permit-push-ci-step-up")
permit (principal, action == Action::"branch.push", resource)
when { context.approval_level >= 1 };
