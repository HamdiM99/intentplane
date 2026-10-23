// Account changes (password, address): always the account holder (step-up), never automatic.

@id("permit-account-change-with-step-up")
permit (principal, action in [Action::"account.update_password", Action::"account.update_user_info"], resource)
when { context.approval_level >= 1 };
