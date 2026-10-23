// Accounts-payable pack. Default deny; forbid overrides permit at every approval level.
// Amounts are integer cents. approval_level: 0 none, 1 step-up (the accountant), 2 four-eyes (e.g. the CFO).
// The vendor master is itself a protected resource: changing it is an intent, and a freshly changed
// IBAN is less trusted than a stable one. The ERP invoice is the source of truth for vendor and amount.

// --- vendor.update_bank_details: only after a call-back on the phone number the ERP already knew,
//     and always with a second human.
@id("forbid-bank-change-without-callback")
forbid (principal, action == Action::"vendor.update_bank_details", resource)
unless { context.callback_verified };

@id("permit-bank-change-four-eyes")
permit (principal, action == Action::"vendor.update_bank_details", resource)
when { context.approval_level >= 2 };

// --- invoice.pay
@id("forbid-invoice-not-open")
forbid (principal, action == Action::"invoice.pay", resource)
unless { context.invoice_open };

@id("forbid-payee-not-invoice-vendor")
forbid (principal, action == Action::"invoice.pay", resource)
unless { context.payee_is_invoice_vendor };

@id("forbid-amount-differs-from-invoice")
forbid (principal, action == Action::"invoice.pay", resource)
unless { context.amount_matches_invoice };

@id("forbid-verification-of-payee-mismatch")
forbid (principal, action == Action::"invoice.pay", resource)
unless { context.vop_match };

@id("forbid-sanctioned-payee")
forbid (principal, action == Action::"invoice.pay", resource)
when { context.payee_sanctioned };

@id("forbid-hard-cap")
forbid (principal, action == Action::"invoice.pay", resource)
when { context.params.amount_cents > 5000000 };

// stable IBAN (changed 7+ days ago): up to 5,000.00 automatic, above that the accountant confirms
@id("permit-pay-stable-iban")
permit (principal, action == Action::"invoice.pay", resource)
when { context.iban_age_days >= 7 && context.params.amount_cents <= 500000 };

@id("permit-pay-stable-iban-large-with-step-up")
permit (principal, action == Action::"invoice.pay", resource)
when { context.iban_age_days >= 7 && context.params.amount_cents > 500000 && context.approval_level >= 1 };

// cooling-off: IBAN changed less than 7 days ago => four eyes, whatever the amount
@id("permit-pay-fresh-iban-four-eyes")
permit (principal, action == Action::"invoice.pay", resource)
when { context.iban_age_days < 7 && context.approval_level >= 2 };
