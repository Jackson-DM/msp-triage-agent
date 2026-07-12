"""Baseline triager: escalates everything, drafts nothing.

Exists to prove the runner end-to-end before any agent code lands, and to
establish the floor score. Never hard-fails (no auto_resolve, no drafts).
"""

from .grader import TriageResult


class DummyTriager:
    name = "dummy_triager"

    def triage(self, ticket_input: dict) -> TriageResult:
        return TriageResult(
            category="hardware",
            priority="medium",
            tier=2,
            action="escalate",
            escalation_target="tier2_tech",
            draft_response=None,
        )
