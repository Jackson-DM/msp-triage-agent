---
description: Run the full 26-ticket golden eval suite and report scores
allowed-tools: Bash(python *), Read, Glob
---

Run the eval suite: `python -m evals.run`

Then:
1. If any HARD FAIL fired, report that first, in its own line, before
   any metrics.
2. Report each metric vs its ship bar from evals/eval-spec.md §3.
3. Report the delta vs the previous report in evals/reports/ (if one
   exists).
4. List failed ticket IDs with one-line diffs (expected vs got).
5. Do not modify any code. Hand analysis to the eval-judge subagent.
