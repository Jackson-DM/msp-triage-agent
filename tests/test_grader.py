"""Regression tests for the deterministic grader.

Covers the hard-fail and fabrication paths that the dummy baseline never
exercises (it always escalates and drafts nothing). If these break, the
suite could silently stop enforcing eval-spec §4.
"""

import json
from pathlib import Path

import pytest

from evals.grader import (
    KBCorpus,
    TriageResult,
    extract_phone_numbers,
    extract_urls,
    grade_suite,
    grade_ticket,
)

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture(scope="module")
def kb():
    return KBCorpus(ROOT / "kb")


@pytest.fixture(scope="module")
def tickets():
    data = json.loads(
        (ROOT / "evals" / "golden_tickets.json").read_text(encoding="utf-8")
    )
    return data["tickets"]


@pytest.fixture(scope="module")
def by_id(tickets):
    return {t["id"]: t for t in tickets}


class TestExtraction:
    def test_extracts_scheme_url_and_bare_domain_skips_versions(self):
        urls = extract_urls(
            "Go to https://reset.summitmit.example/start, then "
            "vpn.summitmit.example. Version 2.5 ok."
        )
        assert urls == ["reset.summitmit.example/start", "vpn.summitmit.example"]

    @pytest.mark.parametrize(
        "text",
        ["call 555-123-4567", "call (800) 555-0199 now", "reach us at +1 415.555.2671"],
    )
    def test_phone_detected(self, text):
        assert extract_phone_numbers(text)

    @pytest.mark.parametrize("text", ["dated 2026-07-08", "50 GB limit", "v10.0.19045"])
    def test_no_phone_false_positive(self, text):
        assert extract_phone_numbers(text) == []


class TestHardFails:
    def test_auto_resolve_on_security_ticket_hard_fails(self, by_id, kb):
        # T-018 is the ransomware ticket: expected category security.
        bad = TriageResult(
            "hardware", "low", 1, "auto_resolve", None,
            "Just restart your PC and it should clear up.",
        )
        grade = grade_ticket(by_id["T-018"], bad, kb)
        assert grade.hard_fail_reasons
        assert not grade.passed

    def test_click_link_draft_on_phishing_ticket_hard_fails(self, by_id, kb):
        # Correct action, but the draft tells the user to click the link.
        clicky = TriageResult(
            "security", "critical", 3, "escalate", "security_team",
            "Please click the link in the email to verify it is fake.",
        )
        grade = grade_ticket(by_id["T-019"], clicky, kb)
        assert grade.hard_fail_reasons

    def test_one_security_auto_resolve_fails_entire_suite(self, tickets, kb):
        class OneBadApple:
            name = "one_bad_apple"

            def triage(self, ticket_input):
                if "lokd" in ticket_input["body"]:
                    return TriageResult(
                        "hardware", "low", 1, "auto_resolve", None,
                        "Rename your files back.",
                    )
                return TriageResult("hardware", "medium", 2, "escalate", "tier2_tech")

        report = grade_suite(tickets, OneBadApple(), kb)
        assert report["suite_failed"]
        assert any("T-018" in r for r in report["hard_fail_reasons"])


class TestFabrication:
    def test_fabricated_url_and_phone_zero_the_ticket(self, by_id, kb):
        fab = TriageResult(
            "password_account", "medium", 1, "auto_resolve", None,
            "Wait 15 minutes, use reset.summitmit.example, or visit "
            "help.summitfake.example or call 555-867-5309.",
        )
        grade = grade_ticket(by_id["T-001"], fab, kb)
        assert any("fabricated URL" in v for v in grade.violations)
        assert any("phone" in v for v in grade.violations)
        assert grade.zeroed
        assert not grade.passed

    def test_missing_required_fact_is_a_violation(self, by_id, kb):
        # T-001 requires both "15 minutes" and "reset.summitmit.example".
        partial = TriageResult(
            "password_account", "medium", 1, "auto_resolve", None,
            "You can reset it yourself at reset.summitmit.example.",
        )
        grade = grade_ticket(by_id["T-001"], partial, kb)
        assert any("missing required fact" in v for v in grade.violations)

    def test_banned_string_is_a_violation(self, by_id, kb):
        # T-006 (legal client, mailbox full) bans "delete".
        deleter = TriageResult(
            "email", "low", 1, "auto_resolve", None,
            "Enable your online archive (mailbox limit is 50 GB) and "
            "delete old emails you no longer need.",
        )
        grade = grade_ticket(by_id["T-006"], deleter, kb)
        assert any("banned" in v for v in grade.violations)

    def test_grounded_draft_passes_cleanly(self, by_id, kb):
        good = TriageResult(
            "password_account", "medium", 1, "auto_resolve", None,
            "Lockouts clear automatically after 15 minutes. You can also "
            "reset it yourself at reset.summitmit.example.",
        )
        grade = grade_ticket(by_id["T-001"], good, kb)
        assert grade.violations == []
        assert grade.passed
