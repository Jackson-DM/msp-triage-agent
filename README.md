# msp-triage-agent — Starter Kit

Evals-first starter material for the MSP support ticket triage agent (Project 1 of the game plan). **No agent code exists yet — by design.** The exam was written before the student.

## What's in here

```
evals/
  eval-spec.md          ← scoring rules, metrics, hard-fail rules (read first)
  golden_tickets.json   ← 26 test tickets with answer keys
kb/
  KB-000 … KB-008       ← 9 knowledge base articles (the agent's only source of truth)
```

Fictional MSP: **Summit Managed IT**. All companies, names, URLs, and procedures are invented. The KB articles contain the exact facts (portal URLs, time windows, policies) that the tickets' `must_include_facts` check against — this is how the fabrication check stays deterministic.

## Suite shape

26 tickets: 13 tier-1, 7 tier-2, 6 tier-3 · 12 auto-resolve, 13 escalate, 1 request_info · 6 security tickets that HARD FAIL the entire suite if auto-resolved (see eval-spec §4).

## Build day 1 — suggested opening moves (in Claude Code)

1. `git init`, drop this kit in as the repo's starting content, first commit: "evals before agent".
2. Create `.claude/` with a CLAUDE.md describing the project, the evals-first rule, and the hard-fail rules (so every Claude Code session knows the safety constraints).
3. Build the eval runner FIRST: a script that takes any triage function, runs all 26 tickets, and prints the scored report per eval-spec §3. Wire it to a `/run-evals` slash command.
4. Only then: build triage v1 (single Claude call, KB in context) and get a baseline score. Every improvement after that is a measured delta — and a LinkedIn post.

## Talking point for interviews / posts

"I wrote a 26-case golden test suite with deterministic fabrication checks and asymmetric hard-fail rules for security tickets *before* writing any agent code. Every version of the agent is scored against the same exam, so I can show exactly how accuracy improved across iterations."
