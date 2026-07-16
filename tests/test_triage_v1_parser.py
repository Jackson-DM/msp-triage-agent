"""Regression tests for the triage v1 response parser.

The double-object fixture is the verbatim raw response captured in
evals/reports/parse_failures/ on 2026-07-16: the model emitted a valid
JSON object, reconsidered in prose, then emitted a corrected second
object. The parser must take the last complete object — the model's
final decision — instead of failing to the safe default.
"""

import pytest

from agent.triage_v1 import parse_response

DOUBLE_OBJECT_RESPONSE = """\
{
  "category": "onboarding_offboarding",
  "priority": "medium",
  "tier": 2,
  "action": "request_info",
  "escalation_target": null,
  "draft_response": null
}

Wait — the action should be request_info because we need manager approval \
before making any access change per KB-005. Let me re-evaluate and produce \
the correct single JSON object.

{
  "category": "email",
  "priority": "medium",
  "tier": 2,
  "action": "request_info",
  "escalation_target": null,
  "draft_response": null
}
"""

SINGLE_OBJECT = (
    '{"category": "email", "priority": "medium", "tier": 1, '
    '"action": "auto_resolve", "escalation_target": null, '
    '"draft_response": "Start Outlook in safe mode with outlook.exe /safe."}'
)


class TestMultiObjectResponses:
    def test_last_object_wins_on_double_object_response(self):
        result = parse_response(DOUBLE_OBJECT_RESPONSE)
        assert result.category == "email"  # the corrected object, not the first
        assert result.action == "request_info"
        assert result.escalation_target is None

    def test_double_object_with_invalid_final_object_raises(self):
        # If the model's final decision is itself invalid, that is still a
        # parse failure — we must not silently fall back to the first object.
        # '"category": "email"' appears only in the final (corrected) object.
        bad_final = DOUBLE_OBJECT_RESPONSE.replace(
            '"category": "email"', '"category": "electronic-mail"', 1
        )
        assert bad_final != DOUBLE_OBJECT_RESPONSE  # guard against fixture drift
        with pytest.raises(ValueError):
            parse_response(bad_final)


class TestSingleObjectResponses:
    def test_plain_object_still_parses(self):
        result = parse_response(SINGLE_OBJECT)
        assert result.category == "email"
        assert result.tier == 1
        assert result.draft_response.startswith("Start Outlook")

    def test_fenced_object_still_parses(self):
        assert parse_response(f"```json\n{SINGLE_OBJECT}\n```").category == "email"

    def test_object_with_trailing_prose_parses(self):
        result = parse_response(SINGLE_OBJECT + "\n\nLet me know if you need anything else.")
        assert result.category == "email"

    def test_braces_inside_draft_string_do_not_split_parsing(self):
        tricky = SINGLE_OBJECT.replace(
            "outlook.exe /safe.", 'outlook.exe /safe. Then check {your} profile.'
        )
        result = parse_response(tricky)
        assert "{your}" in result.draft_response

    def test_no_json_raises(self):
        with pytest.raises(ValueError):
            parse_response("I could not decide on a triage for this ticket.")

    def test_malformed_json_raises(self):
        with pytest.raises(ValueError):
            parse_response('{"category": "email", "priority":')
