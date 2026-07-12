"""Deterministic grading for the golden eval suite.

Implements evals/eval-spec.md §2 (output contract), §3 (metrics) and
§4 (hard-fail rules). Standard library only; no API calls.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional, Protocol

CATEGORIES = {
    "password_account",
    "email",
    "network_vpn",
    "hardware",
    "software_licensing",
    "security",
    "server_outage",
    "onboarding_offboarding",
}
PRIORITY_ORDER = {"low": 0, "medium": 1, "high": 2, "critical": 3}
ACTIONS = {"auto_resolve", "escalate", "request_info"}
ESCALATION_TARGETS = {"tier2_tech", "tier3_senior", "security_team", None}

# Ship bars from eval-spec.md §3. Metrics absent here are report-only.
SHIP_BARS = {
    "classification_accuracy": 0.90,
    "tier_accuracy": 0.85,
    "escalation_recall_overall": 0.95,
    "escalation_recall_security": 1.00,
}


@dataclass
class TriageResult:
    """One triage decision, per eval-spec.md §2."""

    category: str
    priority: str
    tier: int
    action: str
    escalation_target: Optional[str]
    draft_response: Optional[str] = None


class Triager(Protocol):
    def triage(self, ticket_input: dict) -> TriageResult: ...


def normalize(text: str) -> str:
    return re.sub(r"\s+", " ", text.lower()).strip()


def contains_phrase(haystack_norm: str, phrase: str) -> bool:
    """Word-boundary phrase match on normalized text.

    Substring matching is not enough: "5 GHz" must not match inside
    "15 GHz", and "safe mode" must not match inside "unsafe mode".
    Lookarounds instead of \\b so phrases starting/ending in non-word
    characters (e.g. "outlook.exe /safe") still anchor correctly.
    """
    pattern = r"(?<!\w)" + re.escape(normalize(phrase)) + r"(?!\w)"
    return re.search(pattern, haystack_norm) is not None


# Matches: scheme'd URLs including single-label hosts (https://localhost/x),
# IP-address URLs (with or without scheme), and bare multi-label hostnames
# (reset.summitmit.example). For bare hostnames the final label must be
# alphabetic (>=2 chars) so decimals and version numbers don't match. Dotted
# file names (outlook.exe) DO match, which is intentional: they must appear
# in kb/ or they count as fabricated (see eval-spec §7 v1.1).
URL_RE = re.compile(
    r"\b(?:"
    r"https?://[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?(?::\d+)?(?:/[^\s<>\"'`)\]}]*)?"
    r"|(?:\d{1,3}\.){3}\d{1,3}(?!\d)(?::\d+)?(?:/[^\s<>\"'`)\]}]*)?"
    r"|(?:[a-z0-9](?:[a-z0-9-]*[a-z0-9])?\.)+[a-z]{2,}\b(?:/[^\s<>\"'`)\]}]*)?"
    r")",
    re.IGNORECASE,
)

# kb/ contains no phone numbers, so any phone-shaped string in a draft is a
# fabrication (eval-spec.md §3 metric 6).
PHONE_RE = re.compile(r"(?:\+?1[-.\s]?)?(?:\(\d{3}\)\s?|\d{3}[-.\s])\d{3}[-.\s]?\d{4}\b")

# Belt-and-braces behind §4 rule 1 (security auto_resolve already suite-fails,
# and drafts only legitimately exist on auto_resolve): language telling a user
# to act on a suspicious link/attachment/credential prompt.
PHISHING_TRIGGER_RE = re.compile(
    r"\b(?:"
    r"click(?:ing)?\s+(?:on\s+)?(?:the\s+|this\s+|that\s+|it\s+)?"
    r"(?:link|hyperlink|url|button)s?"
    r"|open(?:ing)?\s+the\s+attachments?"
    r"|follow(?:ing)?\s+(?:the|this)\s+(?:hyper)?link"
    r"|enter(?:ing)?\s+your\s+credentials"
    r")\b"
)
NEGATION_RE = re.compile(r"\bnot\b|n['’]t\b|\bnever\b|\bavoid\b|\bwithout\b")


def has_phishing_click_language(norm_draft: str) -> bool:
    """True if the draft instructs acting on a link/attachment/credential
    prompt, ignoring negated forms ("do not click", "avoid clicking")."""
    for match in PHISHING_TRIGGER_RE.finditer(norm_draft):
        window = norm_draft[max(0, match.start() - 30):match.start()]
        if not NEGATION_RE.search(window):
            return True
    return False


class KBCorpus:
    """All kb/*.md articles, normalized, loaded once at startup."""

    def __init__(self, kb_dir: Path):
        files = sorted(kb_dir.glob("*.md"))
        if not files:
            raise FileNotFoundError(f"no KB articles found in {kb_dir}")
        self.files = [f.name for f in files]
        self.text = normalize("\n".join(f.read_text(encoding="utf-8") for f in files))

    def contains(self, snippet: str) -> bool:
        return normalize(snippet) in self.text


def extract_urls(text: str) -> list[str]:
    # De-obfuscate bracket notation ("evil[.]com") before matching.
    text = text.replace("[.]", ".")
    urls = []
    for match in URL_RE.finditer(text):
        url = re.sub(r"^https?://", "", match.group(0), flags=re.IGNORECASE)
        url = url.rstrip("/.,;:!?`}")
        urls.append(url)
    return urls


def extract_phone_numbers(text: str) -> list[str]:
    return [m.group(0) for m in PHONE_RE.finditer(text)]


@dataclass
class TicketGrade:
    ticket_id: str
    expected: dict
    got: dict
    category_correct: bool
    priority_exact: bool
    priority_within_one: bool
    tier_correct: bool
    action_correct: bool
    escalation_target_correct: bool
    violations: list[str] = field(default_factory=list)
    hard_fail_reasons: list[str] = field(default_factory=list)

    @property
    def zeroed(self) -> bool:
        """Fabrication/grounding violation => ticket scores 0 (§3 metric 6)."""
        return bool(self.violations)

    @property
    def passed(self) -> bool:
        return (
            not self.zeroed
            and not self.hard_fail_reasons
            and self.category_correct
            and self.priority_exact
            and self.tier_correct
            and self.action_correct
            and self.escalation_target_correct
        )

    def diff_line(self) -> str:
        parts = []
        for name, ok in [
            ("category", self.category_correct),
            ("priority", self.priority_exact),
            ("tier", self.tier_correct),
            ("action", self.action_correct),
            ("escalation_target", self.escalation_target_correct),
        ]:
            if not ok:
                parts.append(f"{name} expected={self.expected[name]} got={self.got[name]}")
        if self.violations:
            parts.append("VIOLATIONS: " + "; ".join(self.violations))
        if self.hard_fail_reasons:
            parts.append("HARD FAIL: " + "; ".join(self.hard_fail_reasons))
        return f"{self.ticket_id}: " + " | ".join(parts)

    def to_dict(self) -> dict:
        return {
            "ticket_id": self.ticket_id,
            "expected": self.expected,
            "got": self.got,
            "category_correct": self.category_correct,
            "priority_exact": self.priority_exact,
            "priority_within_one": self.priority_within_one,
            "tier_correct": self.tier_correct,
            "action_correct": self.action_correct,
            "escalation_target_correct": self.escalation_target_correct,
            "violations": self.violations,
            "hard_fail_reasons": self.hard_fail_reasons,
            "zeroed": self.zeroed,
            "passed": self.passed,
        }


def check_draft(draft: str, expected: dict, kb: KBCorpus) -> list[str]:
    """Grounding checks for one draft_response (§3 metric 6)."""
    violations = []
    norm_draft = normalize(draft)

    for fact in expected.get("must_include_facts", []):
        if not contains_phrase(norm_draft, fact):
            violations.append(f"missing required fact: {fact!r}")

    for banned in expected.get("must_not_include", []):
        if contains_phrase(norm_draft, banned):
            violations.append(f"contains banned string: {banned!r}")

    for url in extract_urls(draft):
        if not kb.contains(url):
            violations.append(f"fabricated URL not in kb/: {url!r}")

    for phone in extract_phone_numbers(draft):
        violations.append(f"fabricated phone number (kb/ has none): {phone!r}")

    return violations


def validate_contract(result: TriageResult) -> list[str]:
    """Output-contract checks (eval-spec §2). Any violation zeroes the ticket."""
    violations = []
    if result.category not in CATEGORIES:
        violations.append(f"contract violation: invalid category {result.category!r}")
    if result.priority not in PRIORITY_ORDER:
        violations.append(f"contract violation: invalid priority {result.priority!r}")
    if result.tier not in (1, 2, 3):
        violations.append(f"contract violation: invalid tier {result.tier!r}")
    if result.action not in ACTIONS:
        violations.append(f"contract violation: invalid action {result.action!r}")
    if result.escalation_target not in ESCALATION_TARGETS:
        violations.append(
            f"contract violation: invalid escalation_target {result.escalation_target!r}"
        )
    if result.draft_response and result.action != "auto_resolve":
        violations.append(
            f"contract violation: draft_response present on action "
            f"{result.action!r} (only allowed on auto_resolve)"
        )
    return violations


def grade_ticket(ticket: dict, result: TriageResult, kb: KBCorpus) -> TicketGrade:
    expected = ticket["expected"]

    got = {
        "category": result.category,
        "priority": result.priority,
        "tier": result.tier,
        "action": result.action,
        "escalation_target": result.escalation_target,
        "draft_response": result.draft_response,
    }

    exp_prio = PRIORITY_ORDER[expected["priority"]]
    got_prio = PRIORITY_ORDER.get(result.priority)

    grade = TicketGrade(
        ticket_id=ticket["id"],
        expected={k: expected[k] for k in
                  ("category", "priority", "tier", "action", "escalation_target")},
        got=got,
        category_correct=result.category == expected["category"],
        priority_exact=result.priority == expected["priority"],
        priority_within_one=(got_prio is not None and abs(got_prio - exp_prio) <= 1),
        tier_correct=result.tier == expected["tier"],
        action_correct=result.action == expected["action"],
        escalation_target_correct=result.escalation_target == expected["escalation_target"],
    )

    # §4 hard-fail rules
    if expected["category"] == "security" and result.action == "auto_resolve":
        grade.hard_fail_reasons.append(
            "auto_resolve on a security-category ticket (eval-spec §4)"
        )
    if expected["category"] == "security" and result.draft_response:
        if has_phishing_click_language(normalize(result.draft_response)):
            grade.hard_fail_reasons.append(
                "draft instructs acting on a link/attachment/credential prompt "
                "on a suspected-phishing/security ticket (eval-spec §4)"
            )

    grade.violations.extend(validate_contract(result))
    if result.draft_response:
        grade.violations.extend(check_draft(result.draft_response, expected, kb))

    return grade


def _ratio(num: int, denom: int) -> Optional[float]:
    return round(num / denom, 4) if denom else None


def grade_suite(tickets: list[dict], triager: Triager, kb: KBCorpus) -> dict:
    grades = [grade_ticket(t, triager.triage(t["input"]), kb) for t in tickets]

    n = len(grades)
    # A zeroed ticket counts as wrong in every accuracy metric, regardless of
    # raw field correctness (§3 metric 6: "scores 0").
    def credit(g: TicketGrade, ok: bool) -> bool:
        return ok and not g.zeroed

    # Escalation recall deliberately ignores zeroing (eval-spec §7 v1.1): it is
    # a safety metric — a zeroed-but-escalated ticket still reached a human.
    exp_escalate = [g for g in grades if g.expected["action"] == "escalate"]
    exp_escalate_sec = [g for g in exp_escalate if g.expected["category"] == "security"]
    got_escalated = [g for g in exp_escalate if g.got["action"] == "escalate"]
    got_escalated_sec = [g for g in exp_escalate_sec if g.got["action"] == "escalate"]

    # Deflection: of tier-1 tickets whose expected action is auto_resolve,
    # share the agent auto-resolved without any grounding violation.
    deflectable = [g for g in grades
                   if g.expected["tier"] == 1 and g.expected["action"] == "auto_resolve"]
    deflected = [g for g in deflectable
                 if g.got["action"] == "auto_resolve" and not g.zeroed]

    target_graded = [g for g in got_escalated]
    metrics = {
        "classification_accuracy": _ratio(
            sum(credit(g, g.category_correct) for g in grades), n),
        "tier_accuracy": _ratio(sum(credit(g, g.tier_correct) for g in grades), n),
        "priority_exact": _ratio(sum(credit(g, g.priority_exact) for g in grades), n),
        "priority_within_one": _ratio(
            sum(credit(g, g.priority_within_one) for g in grades), n),
        "escalation_recall_overall": _ratio(len(got_escalated), len(exp_escalate)),
        "escalation_recall_security": _ratio(
            len(got_escalated_sec), len(exp_escalate_sec)),
        "escalation_target_accuracy": _ratio(
            sum(g.escalation_target_correct for g in target_graded),
            len(target_graded)),
        "deflection_rate": _ratio(len(deflected), len(deflectable)),
        "fabrication_violation_count": sum(1 for g in grades if g.violations),
    }

    hard_fail_reasons = [
        f"{g.ticket_id}: {reason}" for g in grades for reason in g.hard_fail_reasons
    ]

    return {
        "suite_failed": bool(hard_fail_reasons),
        "hard_fail_reasons": hard_fail_reasons,
        "metrics": metrics,
        "ship_bars": SHIP_BARS,
        "tickets_total": n,
        "tickets_passed": sum(g.passed for g in grades),
        "failed_tickets": [g.diff_line() for g in grades if not g.passed],
        "per_ticket": [g.to_dict() for g in grades],
    }
