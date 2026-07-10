# KB-001: Password Resets & Account Lockouts

**Lockout behavior:** Accounts lock after 5 failed attempts and automatically unlock after **15 minutes**. Users with an immediate need should not wait — direct them to self-service.

**Self-service reset (canonical fix):** https://reset.summitmit.example — requires MFA enrollment. Works for lockouts and forgotten passwords.

**Rules for support responses:**
- NEVER issue, email, or read out a temporary password.
- NEVER include a phone number in reset instructions (all resets go through the portal so they're logged).
- If the user reports they did NOT trigger the failed attempts themselves, treat as possible credential attack → escalate per KB-006.
