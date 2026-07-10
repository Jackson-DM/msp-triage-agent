# Golden Eval Suite Specification — msp-triage-agent

*The exam we wrote before the agent existed. Every version of the agent takes this same test; scores are tracked over time in the README.*

---

## 1. What this is (plain language)

A set of 26 fake-but-realistic MSP support tickets where the correct answer is already decided. The agent reads each ticket's `input` and produces a triage decision; we compare it against `expected`. This turns "it seems to work" into a defensible number.

The fictional MSP is **Summit Managed IT**, serving five fictional client companies (Hartley & Vance Accounting, Redwood Dental Group, Bayline Logistics, Cormorant Real Estate, Petaluma Legal Partners). All names, URLs, and procedures are invented.

## 2. What the agent must output per ticket

| Field | Allowed values | Meaning |
|---|---|---|
| `category` | `password_account`, `email`, `network_vpn`, `hardware`, `software_licensing`, `security`, `server_outage`, `onboarding_offboarding` | What kind of problem this is |
| `priority` | `low`, `medium`, `high`, `critical` | How urgent |
| `tier` | `1`, `2`, `3` | Skill level required |
| `action` | `auto_resolve`, `escalate`, `request_info` | What the agent does next |
| `escalation_target` | `tier2_tech`, `tier3_senior`, `security_team`, or `null` | Who gets it if escalated |
| `draft_response` | text (only when `action = auto_resolve`) | The reply sent to the user, grounded in the KB |

## 3. Scoring metrics

1. **Classification accuracy** — % of tickets with correct `category`. Ship bar: ≥ 90%.
2. **Tier accuracy** — % with correct `tier`. Ship bar: ≥ 85%.
3. **Priority accuracy** — exact match %, plus "within one level" as a softer secondary metric.
4. **Escalation recall** — of tickets whose expected action is `escalate`, % the agent actually escalated. **Ship bar: 100% for security-category tickets, ≥ 95% overall.** Missing an escalation is far worse than an unnecessary one.
5. **Deflection rate** — % of tier-1 tickets correctly auto-resolved. This is the business number ("deflected X% of tier-1 volume").
6. **Fabrication check (deterministic)** — for every `draft_response`:
   - Every string in `must_include_facts` must appear (exact or normalized match).
   - No string in `must_not_include` may appear.
   - No URLs, phone numbers, or procedure steps that don't exist in the KB corpus (checked programmatically against the kb/ folder).
   - Any violation = that ticket scores **0**, regardless of correct classification.

## 4. Hard-fail rules (asymmetric safety)

The bar-service rule: cutting someone off unnecessarily is awkward; over-serving is dangerous. Same asymmetry here.

- **Any `auto_resolve` on a ticket whose expected category is `security` = automatic suite FAIL.** No partial credit, no averaging it away. The suite result is marked FAILED until fixed.
- Any `draft_response` that instructs a user to click a link/take an action on a suspected-phishing ticket = automatic suite FAIL.
- Any fabricated URL or phone number in any draft = hard fail for that ticket (see metric 6).

## 5. What the tricky tickets probe

| Ticket(s) | Capability tested |
|---|---|
| T-020 | Vague input → must `request_info`, not guess |
| T-021 | Urgency bump: identical problem, executive sender → priority changes, category doesn't |
| T-022, T-024, T-026 | Disguised security issues that read like tier 1 — the hard-fail zone |
| T-023 | Two problems in one ticket; the dangerous one must win |
| T-025 | User misdiagnoses their own problem; agent must triage the symptoms, not the user's theory |

## 6. Running the suite (target workflow)

`/run-evals` (custom Claude Code slash command) → runs every ticket through the agent → writes a scored report to `evals/reports/` with per-metric results, per-ticket diffs, and a pass/fail banner. A GitHub Action runs the suite on every push to main. Score history lives in the README as a table — visible proof of iteration.

## 7. Change log

- 2026-07-08 — v1: 26 tickets, 8 KB articles, hard-fail rules defined. Written before any agent code existed (evals-first).
