// Demo policy for the MCP proxy. Default deny; forbid overrides permit.
// context.params.amount is in cents (scale = 100 in intentplane.toml).

@id("permit-small-transfer")
permit (principal, action == Action::"payment.transfer", resource)
when { context.params.amount <= 100000 };

@id("permit-large-transfer-with-step-up")
permit (principal, action == Action::"payment.transfer", resource)
when { context.params.amount > 100000 && context.approval_level >= 1 };

@id("forbid-hard-cap")
forbid (principal, action == Action::"payment.transfer", resource)
when { context.params.amount > 1000000 };

@id("permit-mail")
permit (principal, action == Action::"mail.send", resource);
