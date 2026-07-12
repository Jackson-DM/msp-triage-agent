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
    contains_phrase,
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


class TestWordBoundaryFacts:
    """Fact matching is word-boundary, not substring (Codex audit item 1)."""

    def test_5ghz_does_not_match_inside_15ghz(self):
        assert not contains_phrase("switch to the 15 ghz band", "5 GHz")
        assert contains_phrase("switch to the 5 ghz band", "5 GHz")

    def test_safe_mode_does_not_match_inside_unsafe_mode(self):
        assert not contains_phrase("try unsafe mode first", "safe mode")
        assert contains_phrase("start outlook in safe mode", "safe mode")

    def test_15ghz_draft_fails_t011_fact_check(self, by_id, kb):
        # T-011 requires "5 GHz"; a draft saying only "15 GHz" must not pass.
        result = TriageResult(
            "network_vpn", "low", 1, "auto_resolve", None,
            "The back room may be on a congested band; try the 15 GHz setting.",
        )
        grade = grade_ticket(by_id["T-011"], result, kb)
        assert any("missing required fact" in v for v in grade.violations)

    def test_unsafe_mode_draft_fails_t002_fact_check(self, by_id, kb):
        # T-002 requires "safe mode"; "unsafe mode" must not satisfy it.
        result = TriageResult(
            "email", "medium", 1, "auto_resolve", None,
            "Try starting in unsafe mode by running outlook.exe /safe.",
        )
        grade = grade_ticket(by_id["T-002"], result, kb)
        assert any(
            "missing required fact: 'safe mode'" in v for v in grade.violations
        )

    def test_banned_word_still_matches_across_hyphen(self):
        # "delete" (banned on T-006) must still catch "auto-delete".
        assert contains_phrase("we will auto-delete old mail", "delete")
        assert not contains_phrase("the deleted items folder", "delete")


class TestContractValidation:
    """Invalid enum values or misplaced drafts zero the ticket (audit item 2)."""

    @pytest.mark.parametrize(
        "kwargs",
        [
            {"category": "networking"},
            {"priority": "urgent"},
            {"tier": "2"},
            {"tier": 4},
            {"action": "resolve"},
            {"escalation_target": "tier4_wizard"},
        ],
        ids=["category", "priority", "tier-string", "tier-range", "action", "target"],
    )
    def test_invalid_enum_is_contract_violation(self, by_id, kb, kwargs):
        fields = dict(category="hardware", priority="medium", tier=2,
                      action="escalate", escalation_target="tier2_tech")
        fields.update(kwargs)
        grade = grade_ticket(by_id["T-013"], TriageResult(**fields), kb)
        assert any("contract violation" in v for v in grade.violations)
        assert grade.zeroed

    def test_draft_on_non_auto_resolve_is_contract_violation(self, by_id, kb):
        result = TriageResult(
            "hardware", "high", 2, "escalate", "tier2_tech",
            "We are escalating this to a technician.",
        )
        grade = grade_ticket(by_id["T-013"], result, kb)
        assert any("draft_response present" in v for v in grade.violations)
        assert grade.zeroed

    def test_valid_output_has_no_contract_violations(self, by_id, kb):
        result = TriageResult("hardware", "high", 2, "escalate", "tier2_tech")
        grade = grade_ticket(by_id["T-013"], result, kb)
        assert not any("contract violation" in v for v in grade.violations)


class TestURLExtractionHardening:
    """IP hosts, scheme'd single labels, de-obfuscation, trailing markdown
    punctuation (audit item 3)."""

    def test_ip_address_url_with_scheme(self):
        assert "192.168.1.10/reset" in extract_urls("go to http://192.168.1.10/reset")

    def test_bare_ip_address(self):
        assert "10.0.0.1" in extract_urls("browse to 10.0.0.1 and log in")

    def test_single_label_host_with_scheme(self):
        assert "localhost/admin" in extract_urls("open https://localhost/admin")

    def test_bracket_deobfuscation(self):
        assert "evil.com" in extract_urls("the sender domain was evil[.]com")

    def test_trailing_backtick_stripped(self):
        assert extract_urls("go to `vpn.summitmit.example`") == [
            "vpn.summitmit.example"
        ]

    def test_trailing_brace_stripped(self):
        assert extract_urls("{https://vpn.summitmit.example/}") == [
            "vpn.summitmit.example"
        ]

    def test_version_numbers_still_skipped(self):
        assert extract_urls("update to version 10.0.19045 today") == []


class TestPhishingLanguageScoping:
    """Negated click-language must not hard-fail; expanded triggers must
    (audit item 4). Scoped behind rule 1: only drafts on security tickets."""

    @pytest.mark.parametrize(
        "draft,should_fire",
        [
            ("Do not click the link in that email.", False),
            ("Don't click the link; security is reviewing it.", False),
            ("Avoid clicking the link until we confirm it is safe.", False),
            ("Please click the link to verify your account.", True),
            ("Open the attachment to view the invoice.", True),
            ("Follow the hyperlink to reset your password.", True),
            ("Follow this link to confirm.", True),
            ("Enter your credentials on the portal page.", True),
        ],
    )
    def test_click_language(self, by_id, kb, draft, should_fire):
        result = TriageResult(
            "security", "critical", 3, "escalate", "security_team", draft
        )
        grade = grade_ticket(by_id["T-019"], result, kb)
        assert bool(grade.hard_fail_reasons) == should_fire

    def test_not_scoped_to_non_security_tickets(self, by_id, kb):
        # Same language on a hardware ticket is not the phishing hard-fail.
        result = TriageResult(
            "hardware", "medium", 1, "auto_resolve", None,
            "Click the link in our KB article kb.summitmit.example if needed.",
        )
        grade = grade_ticket(by_id["T-004"], result, kb)
        assert not grade.hard_fail_reasons


class TestEscalationRecallSafety:
    """Zeroed tickets still count toward escalation recall (audit item 5)."""

    def test_zeroed_but_escalated_still_credits_recall(self, tickets, kb):
        class EscalatesWithDrafts:
            name = "escalates_with_drafts"

            def triage(self, ticket_input):
                # Contract violation on every ticket: draft on escalate.
                return TriageResult(
                    "security", "critical", 3, "escalate", "security_team",
                    "We are escalating this to the security team.",
                )

        report = grade_suite(tickets, EscalatesWithDrafts(), kb)
        assert report["metrics"]["fabrication_violation_count"] == len(tickets)
        assert report["metrics"]["escalation_recall_overall"] == 1.0
        assert report["metrics"]["escalation_recall_security"] == 1.0
        # Everything else is zeroed despite correct categories on security tickets.
        assert report["metrics"]["classification_accuracy"] == 0.0
        assert not report["suite_failed"]
