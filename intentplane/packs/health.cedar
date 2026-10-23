// Health pack. Default deny; forbid overrides permit at every approval level.
// approval_level: 0 none, 1 step-up (the clinician justifies), 2 four-eyes (second reviewer).

@id("forbid-research-without-consent")
forbid (principal, action == Action::"ehr.read_record", resource)
when { context.params.purpose == "research" && !context.consent_research };

@id("permit-treatment-read")
permit (principal, action == Action::"ehr.read_record", resource)
when { context.params.purpose == "treatment" && context.params.section != "full" };

@id("permit-full-record-with-step-up")
permit (principal, action == Action::"ehr.read_record", resource)
when { context.params.purpose == "treatment" && context.params.section == "full" && context.approval_level >= 1 };

@id("permit-research-read")
permit (principal, action == Action::"ehr.read_record", resource)
when { context.params.purpose == "research" && context.params.section != "full" };

@id("permit-break-the-glass-four-eyes")
permit (principal, action == Action::"ehr.read_record", resource)
when { context.params.purpose == "emergency" && context.approval_level >= 2 };
