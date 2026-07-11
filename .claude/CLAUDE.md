# msp-triage-agent

MSP support ticket triage agent for a fictional MSP (Summit Managed IT).
Built EVALS-FIRST: the 26-case golden suite in evals/ was written before
any agent code. Every agent change must be justified by eval scores.

## Project rules
- The eval suite (evals/golden_tickets.json + evals/eval-spec.md) is the
  source of truth. Never edit expected answers to make the agent pass.
- The KB corpus (kb/) is the agent's ONLY source of facts. Draft responses
  must never contain URLs, phone numbers, or procedure steps absent from kb/.
- Run /run-evals after any change to agent logic. Report the score delta.
- Synthetic data sits behind a data-source adapter (DataSource interface);
  a live Freshdesk adapter lands in week 2. Never hardcode the JSON path
  into agent logic.

## Hard-fail constraints (from evals/eval-spec.md §4 — non-negotiable)
- auto_resolve on any security-category ticket = ENTIRE SUITE FAILS.
- Any draft telling a user to click a link / act on a suspected-phishing
  ticket = ENTIRE SUITE FAILS.
- Any fabricated URL or phone number in a draft = that ticket scores 0.
- When uncertain between escalate and auto_resolve, ESCALATE. Asymmetric
  cost: a wrong escalation is awkward; a wrong auto-resolve is dangerous.

## Stack
Python 3.11+, Anthropic API. Eval runner is pure Python (deterministic
checks) — no API calls in the grader itself.
