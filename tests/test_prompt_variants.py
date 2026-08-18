"""The prompt variants, and the invariants that keep them comparable.

These are offline: building a prompt is string work and makes no API calls.

Why this file exists at all. The variants are the independent variable of an
experiment, and an experiment whose independent variable quietly changes is
worse than no experiment - it produces a number that looks like evidence. Each
test below pins one property the comparison depends on.
"""

from __future__ import annotations

import re

import pytest

from agent.prompt_variants import VARIANTS, apply_pressure, strip_security_rule
from agent.triage_v1 import SAFETY_RULES


def _rules(text: str) -> list[str]:
    """The numbered rules, whitespace-normalised, without their numbers."""
    parts = re.split(r"^\d\. ", text, flags=re.M)[1:]
    return [re.sub(r"\s+", " ", p).strip() for p in parts]


def test_run_py_offers_exactly_the_variants_that_exist():
    """--rules and VARIANTS must agree.

    run.py hardcodes its choices so that --help and --dummy need no Anthropic
    SDK, which means the two lists can drift. They did: `pressure` was added
    here and the CLI rejected it, which cost a live run to discover. This test
    is that discovery moved offline.
    """
    from evals.run import build_parser

    choices = next(a.choices for a in build_parser()._actions if a.dest == "rules")
    assert set(choices) == set(VARIANTS), (
        f"--rules offers {sorted(choices)} but VARIANTS defines {sorted(VARIANTS)}"
    )


def test_every_variant_builds():
    for name, build in VARIANTS.items():
        text = build()
        assert text.strip(), f"{name} produced an empty rule set"
        assert "SAFETY RULES" in text


def test_variants_are_distinct():
    built = {name: build() for name, build in VARIANTS.items()}
    assert len(set(built.values())) == len(built), "two variants produced identical text"


def test_full_is_untouched():
    """The published v1 numbers were measured against this exact string."""
    assert VARIANTS["full"]() == SAFETY_RULES


def test_no_security_removes_one_rule_and_keeps_the_rest_verbatim():
    full, weak = _rules(SAFETY_RULES), _rules(strip_security_rule())
    assert len(weak) == len(full) - 1
    assert full[1:] == weak, "a rule other than the security rule changed"


def test_pressure_removes_two_rules_and_keeps_the_rest_verbatim():
    full, pressed = _rules(SAFETY_RULES), _rules(apply_pressure())
    assert len(pressed) == len(full) - 1  # two removed, one added
    assert full[2:] == pressed[1:], "a rule other than 1 and 2 changed under pressure"


def test_pressure_introduces_no_security_vocabulary():
    """The whole point: the drift edit must read as an efficiency note.

    If the replacement mentioned security at all it would be steering the
    result, and a reader would be right to discount the experiment.
    """
    new_rule = _rules(apply_pressure())[0].lower()
    for word in ("security", "phish", "ransom", "credential", "malware",
                 "spoof", "attack", "suspicious", "incident"):
        assert word not in new_rule, f"pressure rule mentions {word!r}"


def test_weakened_variants_keep_the_grounding_rules():
    """Only the routing changes. Draft quality rules stay, so a draft that does
    get written is still held to the standard the baseline was held to."""
    for name in ("no-security", "pressure"):
        text = VARIANTS[name]()
        assert "knowledge base below" in text, f"{name} lost the grounding rule"
        assert "irreversible" in text, f"{name} lost the no-deletion rule"
        assert "suspicious" in text, f"{name} lost the click-nothing rule"


def test_escalate_when_uncertain_survives_deletion_but_not_pressure():
    """The two variants differ in exactly this, and it is the reason `pressure`
    exists: leaving the default in place alongside 'lean toward resolving'
    would put two contradictory instructions in one prompt and measure how the
    model resolves a contradiction."""
    assert "ESCALATE" in strip_security_rule()
    assert "ESCALATE" not in apply_pressure()


def test_rules_are_numbered_consecutively_from_one():
    for name, build in VARIANTS.items():
        nums = [int(n) for n in re.findall(r"^(\d)\. ", build(), flags=re.M)]
        assert nums == list(range(1, len(nums) + 1)), f"{name} has gappy numbering: {nums}"


def test_markers_fail_loudly_if_triage_v1_is_edited():
    """The variants locate rules by quoting their opening words. If someone
    rewords SAFETY_RULES, that must raise rather than silently produce a
    different experiment under the same name."""
    with pytest.raises(AssertionError):
        strip_security_rule("SAFETY RULES:\n1. Something else entirely.\n")
    with pytest.raises(AssertionError):
        apply_pressure("SAFETY RULES:\n1. Something else entirely.\n")
