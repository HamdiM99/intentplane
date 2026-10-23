// Bank pack. Default deny; forbid overrides permit at every approval level.
// Amounts are integer cents. approval_level: 0 none, 1 step-up (the principal), 2 four-eyes.

@id("forbid-sanctioned-payee")
forbid (principal, action == Action::"payment.transfer", resource)
when { context.payee_sanctioned };

@id("forbid-hard-cap")
forbid (principal, action == Action::"payment.transfer", resource)
when { context.params.amount_cents > 1000000 };

@id("permit-transfer")
permit (principal, action == Action::"payment.transfer", resource)
when { context.params.amount_cents <= 100000 };

@id("permit-large-transfer-with-step-up")
permit (principal, action == Action::"payment.transfer", resource)
when { context.params.amount_cents > 100000 && context.approval_level >= 1 };
