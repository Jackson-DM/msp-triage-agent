# msp-triage-agent

An AI support-ticket triage agent for a fictional managed service provider
(Summit Managed IT), built **evals-first**: a 26-case golden suite was written
and frozen *before* any agent code existed, and every change since has been
justified by a measured eval delta.

The interesting part of this repo is not the score. It is the record of how the
score was reached — including three prompt fixes that made things worse, each
preserved on a branch with the evidence that killed it.

---

## Result

All four ship bars clear in **every** run of the suite:

| Ship bar | Threshold | Measured (3 runs) | Status |
|---|---|---|---|
| Classification accuracy | ≥ 90% | 92.3 / 92.3 / 96.2 | PASS 3/3 |
| Tier accuracy | ≥ 85% | 92.3 / 92.3 / 96.2 | PASS 3/3 |
| Escalation recall (overall) | ≥ 95% | 100 / 100 / 100 | PASS |
| Escalation recall (security) | = 100% | 100 / 100 / 100 | PASS |

**21.3 / 26 tickets fully correct** (band 21–22). No hard-fail has ever been
observed. Roughly $0.09 per full suite run (~$0.0035/ticket) against
`claude-sonnet-4-6`, one API call per ticket.

Trajectory: dummy always-escalate floor **0/26** → first live agent run
**16/26** with three bars missed → published head **21.3/26, all bars clear**.

## The suite

26 tickets — 13 tier-1, 7 tier-2, 6 tier-3 · 12 auto-resolve, 13 escalate,
1 request_info · 6 security tickets that **hard-fail the entire suite** if
auto-resolved. Nine knowledge-base articles are the agent's only permitted
source of facts; required-fact and fabrication checks are exact-match against
that corpus, which keeps grading deterministic.

## Why evals-first

The suite (`evals/golden_tickets.json`, spec in `evals/eval-spec.md`) is the
source of truth. Two rules kept the rest of the project honest:

1. **Never edit an expected answer to make the agent pass.** Two failing
   tickets cost ship bars for hours. Loosening the test would have made every
   other number in this repo meaningless.
2. **Never reference a golden ticket in the agent prompt.** Every rule must be
   justifiable from the knowledge-base corpus alone. Where a rule could only be
   defended by pointing at a specific test case, it was left out — *defensible
   22/26 over overfit 26/26*.

Hard-fail rules are absolute: auto-resolving any security-category ticket, or
telling a user to act on a suspected-phishing ticket, fails the whole suite
regardless of every other score. When uncertain between escalating and
auto-resolving, the agent escalates — a wrong escalation is awkward, a wrong
auto-resolve is dangerous.

## The stability harness

Single eval runs were lying. A prompt fix would appear to work, then silently
regress on the next run. So `evals/run.py` grew a `--runs N` mode:

```bash
python -m evals.run --agent v1 --runs 3
```

It runs the suite N times and reports **per-ticket pass rates** with a stability
class (stable-pass / stable-fail / flaky), which fields flicker and with what
values, and every metric as a mean with a min–max band. Two rules encode the
asymmetry of the domain:

- the suite fails if **any** run hard-fails, and
- a ship bar counts as met only if it clears in **every** run — the mean is
  reported alongside but never gates.

It earned its keep immediately: the first fix it examined had "passed" a 3-run
batch by luck, and failed 2 of 3 on the next measurement.

## What failed, and why

Three prompt fixes were reverted after measurement. All are preserved unmerged.

| Branch | Attempt | Measured outcome |
|---|---|---|
| `c1b-attempt` | Strengthen a rule to never mention deletion, "not even as reassurance" | Violations went **2/3 runs → 5/5**. Emphasising a forbidden concept raised its salience; the model began explicitly narrating its own compliance. |
| `c3b-attempt` | Trim urgency language blamed for a dropped required fact | The fact resolved itself two commits later with no rule targeting it — it had been variance, not a defect. The trim also cost an unrelated ticket. |
| `identifier-rule-attempt` | Forbid echoing user-supplied addresses, with a carve-out protecting KB specifics | The carve-out behaved as a **whitelist**: every fact it named survived 3/3; every KB phrase it did not name was paraphrased away. Cost 3–4 tickets per run (mean 19.7 → 17.3). |

The structural conclusion, recorded in `evals/eval-spec.md` §7: draft-content
prompt surgery does not converge, because exact-match required facts are
scattered across precisely the phrasings each new rule reshuffles — protecting
one string re-exposes another. The recommended fix is LLM-as-judge grading of
draft semantics, not further prompt iteration.

## Known limitations (documented, not fixed)

Five tickets do not pass. Each is classified rather than explained away:

- **T-006 — the grader is wrong and the agent is right.** The banned-string
  check is negation-insensitive: it cannot distinguish *"delete your old
  emails"* from *"you're right not to delete them."* The drafts were pulled and
  inspected across five runs — the advice is correct every time. Notably the
  grader already solves this elsewhere: its phishing check skips negated forms
  via a look-behind, so two checks apply different standards to the same
  linguistic phenomenon.
- **T-010 — two distinct failure modes, only one of them the grader's fault.**
  Intermittently the draft quotes the distribution-list address from the user's
  own ticket back to them, and the fabrication check flags any dotted token
  absent from the corpus — that one is grader strictness. But its blocking
  violation in the final run was different: the draft genuinely dropped a
  required `manager approval` fact. That is a real omission, and an instance of
  the draft-reshuffle problem below rather than a check being too blunt.
- **T-004 — a defect in my own knowledge base.** KB-000's medium row lists
  "printer offline" as an example while the row's definition reads "single user
  degraded or blocked." A whole-office printer matches the example but not the
  definition. Left unfixed deliberately: editing the corpus because a test is
  failing is the same error as editing the expected answer.
- **T-008 — a defensible disagreement.** Whether losing a second monitor is
  "work degraded" or "an accessory lost while primary work continues" is
  arguable; the suite says one, the model consistently argues the other.
- **T-012 — a rule that did not land**, recorded as unlanded rather than
  papered over.

Three decisions are parked for independent review rather than made in the flow
of chasing a green scoreboard: negation-aware banned-string matching, KB-000
disambiguation, and LLM-as-judge draft grading.

## Running it

```bash
export ANTHROPIC_API_KEY='...'

python -m evals.run --dummy                  # offline baseline, no API calls
python -m evals.run --agent v1               # single live run
python -m evals.run --agent v1 --runs 3      # stability report + aggregate
python -m evals.run --agent v1 --limit 5     # smoke test, no report written

python -m pytest tests/ -q                   # 70 offline tests, no API calls
```

Exit codes: `0` all ship bars met, `1` hard fail or a bar missed, `2` no
triager available or missing API key.

## Layout

```
agent/     triage_v1.py (prompt + parser), data_source.py (adapter)
kb/        the knowledge-base corpus — the agent's ONLY source of facts
evals/     golden_tickets.json, eval-spec.md, grader.py, run.py,
           aggregate.py (multi-run aggregation), reports/
tests/     70 offline tests: grader, parser, aggregation
```

The grader is pure standard library and makes no API calls, so grading is
deterministic and every reported delta is reproducible. Synthetic tickets sit
behind a `DataSource` adapter, so a live helpdesk backend can replace them
without touching agent logic.
