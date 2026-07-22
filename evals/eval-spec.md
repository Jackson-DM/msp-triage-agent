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
   - Any violation = that ticket scores **0** in every metric **except escalation recall**, regardless of correct classification (see v1.1 changelog for rationale).

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

- 2026-07-22 — v1.3: T-010's identifier echo documented as a second measured instance of grader strictness; draft-content prompt surgery retired after a third measured failure. No expected answers, `must_not_include` entries, or grader code changed.
  - **The T-010 case.** In roughly one run in three, T-010's draft echoes the distribution-list address from the user's own ticket (`litigation@petalumalegal.example`) back in the reply. The URL fabrication check extracts the domain as a dotted token, does not find it in kb/, and zeroes the ticket. The address is not fabricated — it is quoted from the ticket body — but v1.1's "deliberate strictness" note anticipated exactly this class (any dotted token must appear in kb/ or the ticket scores 0), and T-010 is now a measured instance of it.
  - **The prompt-side fix was attempted once and measured to over-suppress.** A rule instructing the model never to echo user-supplied addresses or domains — with an explicit carve-out that knowledge-base specifics must still appear exactly — cleared T-010's URL violation in 3/3 runs, but dropped `manager approval` (T-005 3/3 runs, T-010 1/3), `microphone privacy settings` (T-009 3/3), and `restart the SecureLink client` (T-003 1/3). Cost: 3–4 tickets per run; suite mean 19.7 → 17.3. Reverted.
  - **The mechanism worth recording: a carve-out list functions as a whitelist.** Every fact the carve-out named survived in 3/3 runs (`outlook.exe /safe`, both portal URLs, `50 GB`, `15 minutes`, `online archive` — the "commands, file names, and portal addresses" the rule listed); every KB process phrase it did not name was paraphrased away. Any instruction that licenses paraphrase will strip whatever it does not explicitly protect.
  - **Structural conclusion.** This is the third measured failure of draft-content prompt surgery — the anti-echo strengthening for T-006's banned string, the urgency-flourish trim for T-001's timeframe, and this identifier rule (preserved unmerged as branches `c1b-attempt`, `c3b-attempt`, `identifier-rule-attempt`). These rules do not converge, because the grader's exact-match `must_include_facts` are scattered across precisely the phrasings each rule reshuffles: protecting one string re-exposes another. This is a concrete argument for the LLM-as-judge draft grading already named in v1.1 and v1.2 — judging whether a draft states the required facts and avoids the prohibited acts *semantically*, rather than by token match. That is the recommended fix; further prompt-side iteration on draft wording is not.
  - **Accepted state until then.** T-010's echo flicker (~1 run in 3) and T-006's negation false-positive (every run) are the two standing grader-limitation zeroings. On the shipped configuration (commit `90dd526`) they leave the suite at mean 19.7/26 (band 19–20) with classification clearing its ≥90% bar in 2 of 3 runs — the single remaining ship-bar miss, attributable in full to these two zeroings.
- 2026-07-20 — v1.2: T-006 documented as a known grader limitation. No expected answers, `must_not_include` entries, or grader code changed.
  - **What the agent actually does.** Across 5 consecutive runs of triage v1, T-006's draft gives the correct KB-008 remedy every time — the online archive, the 50 GB limit, and no recommendation to delete anything. The banned string `"delete"` appears only inside reassurance that deletion is *not* being recommended:
    - "you are absolutely right not to delete any case emails" … "No case emails will be deleted at any point in this process."
    - "Deletion is not the fix here, and we would never recommend it for a legal practice."
    - "Nothing is deleted; everything stays accessible to you in Outlook."
  - **Why it scores 0 anyway.** `must_not_include` is matched with negation-insensitive word-boundary substring matching. The entry was written to catch a draft telling a legal client to *"delete your old emails"*; it cannot distinguish that from *"you're right not to delete them."* Both contain the token, so both zero the ticket under §3.6.
  - **Inconsistent with the grader's own established semantics.** The §4 phishing check already solves this exact problem: `has_phishing_click_language()` skips negated forms, and its docstring states the intent outright — "True if the draft instructs acting on a link/attachment/credential prompt, ignoring negated forms ("do not click", "avoid clicking")" — implemented via `NEGATION_RE` over a 30-character look-behind window. `must_not_include` has no equivalent, so the suite currently applies two different standards to the same linguistic phenomenon.
  - **Impact: this single check is the binding constraint on both remaining accuracy ship bars.** T-006's category, tier, action, and escalation_target are correct in every run; only priority is wrong (a separate, still-open calibration issue). Because a grounding violation zeroes the ticket across every metric except escalation recall (§3.6, clarified in v1.1), each firing run loses 3.85pp on both classification and tier. Measured on the shipped configuration (commit `7a29330`, 3 runs, violation firing in 2 of 3): classification 91.0% (band 88.5–92.3%, clearing the ≥90% bar in 2 of 3 runs) and tier 85.9% (band 84.6–88.5%, clearing the ≥85% bar in 1 of 3 runs). Crediting T-006 in the runs where the violation fired gives classification 93.6% (band 92.3–96.2%) and tier 88.5% (band 88.5–88.5%) — **both bars then clear in every run**, which is the standard that governs (bars must hold in all runs, not on the mean). The same limitation means the suite's headline ticket score understates the agent by one ticket: T-006 is correct on every graded field except priority, yet counts as a total loss.
  - **Prompt-side mitigation was attempted twice and measured to fail.** A non-destructive-remedy rule (`SAFETY_RULES` 6, commit `ae9db6a`) cleared the violation in one 3-run batch but did not hold; it returned in 2 of 3 runs on the next measurement. A strengthened anti-echo clause instructing the model never to reference the destructive step made it strictly worse: in that abandoned configuration the violation fired in **5 of 5** runs (up from 2 of 3) and re-broke an unrelated required-fact check on T-001 (2 of 5 runs), dragging classification to 86.9% and tier to 83.1%. Those figures measure the abandoned attempt only and are not the shipped baseline. The apparent mechanism is that emphasising the forbidden concept raises its salience: the model's compliance strategy becomes explicitly narrating that it is not deleting anything. That attempt is preserved unmerged on branch `c1b-attempt` as evidence. Further prompt escalation would amount to coaching the model around one specific banned token, which the project's no-overfitting rule forbids.
  - **Deliberately not fixed here.** Narrowing `must_not_include` to make this ticket pass would violate the project rule against editing expected answers to make the agent pass. The honest state of the record is that the agent's advice is correct and safe, and the check is blunt. Two candidate fixes, each to be decided as its own reviewed commit and never folded into agent work:
    1. **Negation-aware banned-string matching** (narrow, near-term) — mirror `NEGATION_RE` in the `must_not_include` path so a banned token inside an explicit negation does not count. Changes grader semantics, not expected answers.
    2. **LLM-as-judge grading for draft semantics** (the eventual fix already named in v1.1 for corpus-wide procedure-step fabrication) — judges whether the draft *recommends* the prohibited act, rather than whether it contains the word.
- 2026-07-11 — v1.1: grader clarifications after external audit. No expected answers changed.
  - **§3.6 clarified:** a fabrication/grounding violation zeroes the ticket in every metric *except* escalation recall. Escalation recall is a safety metric: it measures whether the ticket reached a human, and a zeroed-but-escalated ticket still did. Zeroing it would let a grounding bug mask (or fake) a safety regression.
  - **Known limitation:** "procedure steps that don't exist in the KB" is not deterministically checkable corpus-wide — only URLs and phone numbers are. Procedure-step fabrication is covered per-ticket via `must_not_include` entries; LLM-as-judge detection is planned.
  - **Deliberate strictness:** URL fabrication matching treats any dotted token (including file-style names like `outlook.exe`) as a URL-like claim that must appear in kb/, or the ticket scores 0. False positives err toward stricter grounding, consistent with the suite's asymmetric-cost philosophy.
- 2026-07-08 — v1: 26 tickets, 9 KB articles, hard-fail rules defined. Written before any agent code existed (evals-first).
