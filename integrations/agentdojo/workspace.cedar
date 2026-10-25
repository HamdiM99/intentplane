// AgentDojo workspace: who may receive data is decided by provenance (addresses from the request or the
// user's existing correspondents); destroying data always needs the user.

@id("permit-send-invite-share")
permit (principal, action in [Action::"mail.send", Action::"calendar.invite", Action::"drive.share"], resource);

@id("permit-delete-with-step-up")
permit (principal, action == Action::"workspace.delete", resource)
when { context.approval_level >= 1 };
