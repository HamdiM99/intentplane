// Code pack. Default deny; forbid overrides permit at every approval level.
// approval_level: 0 none, 1 step-up (the developer), 2 four-eyes (a reviewer).

@id("forbid-red-ci")
forbid (principal, action == Action::"repo.merge", resource)
when { context.ci_status != "success" };

@id("permit-merge-unprotected-branch")
permit (principal, action == Action::"repo.merge", resource)
unless { ["main", "release"].contains(context.params.target_branch) };

@id("permit-merge-protected-branch-reviewed")
permit (principal, action == Action::"repo.merge", resource)
when { ["main", "release"].contains(context.params.target_branch) && context.approved_review };

@id("permit-merge-protected-branch-four-eyes")
permit (principal, action == Action::"repo.merge", resource)
when { ["main", "release"].contains(context.params.target_branch) && context.approval_level >= 2 };
