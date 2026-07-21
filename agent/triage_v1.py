"""Triage v1: one Anthropic API call per ticket, grounded in kb/.

The system prompt carries the triage task, the eval-spec §2 output
contract, the full KB corpus (loaded from kb/ at runtime), and the
safety rules from CLAUDE.md. Any parse failure, invalid enum, or API
error degrades to a safe default (escalate to tier2_tech) — a broken
response can never auto-resolve anything.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path

import anthropic

from evals.grader import ACTIONS, CATEGORIES, ESCALATION_TARGETS, PRIORITY_ORDER, TriageResult

MODEL = "claude-sonnet-4-6"

# USD per million tokens for claude-sonnet-4-6 (cache read ~0.1x input,
# 5-minute cache write ~1.25x input).
PRICING_USD_PER_MTOK = {
    "input_tokens": 3.00,
    "output_tokens": 15.00,
    "cache_read_input_tokens": 0.30,
    "cache_creation_input_tokens": 3.75,
}

USAGE_KEYS = list(PRICING_USD_PER_MTOK)

TASK_AND_CONTRACT = """\
You are the support-ticket triage agent for Summit Managed IT, an MSP
serving small-business clients. For each ticket you receive, decide how
it should be triaged and respond with a single JSON object — no prose,
no markdown fences — with exactly these fields:

{
  "category": one of "password_account", "email", "network_vpn", "hardware",
              "software_licensing", "security", "server_outage",
              "onboarding_offboarding",
  "priority": one of "low", "medium", "high", "critical",
  "tier": 1, 2, or 3 (integer skill level required),
  "action": one of "auto_resolve", "escalate", "request_info",
  "escalation_target": one of "tier2_tech", "tier3_senior", "security_team",
                       or null — null unless action is "escalate",
  "draft_response": string or null — the reply sent to the user; ONLY
                    when action is "auto_resolve", otherwise null
}

Field rules:
- "auto_resolve": the issue has a standard runbook fix in the knowledge
  base that the user can follow, or a process you can explain. Include a
  draft_response walking them through it.
- "escalate": the issue needs a technician. tier2_tech for standard
  technical work, tier3_senior for major outages needing senior
  engineering, security_team for anything security-related.
- "request_info": use ONLY when the ticket gives no concrete symptoms,
  errors, or specifics to reason from — you genuinely cannot tell what
  kind of problem it is. Ask targeted clarifying questions; do not guess
  a runbook. If the evidence already identifies a real problem that needs
  a technician, escalate instead — even if the user misdiagnosed the
  cause or some details are missing. Ruling out the user's own theory is
  a reason to route the ticket, not to ask for more information.
- Set priority from business impact using the priority matrix in the
  knowledge base: scope of impact, deadlines, sender seniority, data-loss
  risk. Priority and category are independent judgments — the same
  technical issue can be low or critical depending on context. Priority is
  independent of tier and action as well: the matrix's modifiers
  (executive sender, explicit same-day deadline, multiple users affected)
  raise urgency, not the skill level a fix requires. A critical-priority
  ticket whose remedy is a standard knowledge-base runbook is still tier 1
  and auto_resolve — answer it immediately and offer live help when the
  stakes are high, but do not reroute a problem you can already solve.
  This never overrides the safety rules: anything plausibly
  security-related escalates no matter how routine its fix looks.
- If a ticket contains multiple issues, triage the whole ticket by its
  most severe component.
- Triage from the symptoms and evidence in the ticket, not the user's
  own diagnosis — users often misattribute causes. When a detail in the
  ticket contradicts the user's stated cause — for example, something
  they call broken is shown to be working — trust the evidence over the
  label, and reason to the actual likely source before choosing
  category, tier, and action.
"""

SAFETY_RULES = """\
SAFETY RULES (non-negotiable, they override everything else):
1. NEVER auto_resolve any ticket that is plausibly security-related:
   phishing, suspicious emails or attachments, malware or ransomware
   indicators, credential compromise, fraud or payment-detail changes,
   spoofed or impersonated email, or unexpected system behavior after
   opening a link or attachment. These always escalate, and the target
   is security_team. Tier follows the knowledge base's severity split:
   active compromise — credentials entered, ransomware, fraud already in
   motion, or malware evidently running after the user opened or clicked
   something — is critical and tier 3. A suspected security incident with
   no such evidence, such as unwanted-software indicators like adware or a
   browser hijack, is high and tier 2. The target is security_team either
   way.
2. When uncertain between escalate and auto_resolve, ESCALATE. A wrong
   escalation is awkward; a wrong auto-resolve is dangerous.
3. Draft responses may only contain facts, URLs, and procedure steps
   that appear in the knowledge base below. Never invent a URL. Never
   include ANY phone number — the knowledge base contains none. Never
   claim an action was already performed (an install, an access change)
   when it was not.
4. Never instruct a user to click a link, open an attachment, follow a
   hyperlink, or enter credentials in connection with anything
   suspicious.
5. When action is auto_resolve, the draft must carry over the concrete
   specifics of the knowledge-base article you are relying on — the
   exact timeframes, self-service portal URLs, limits, and approval
   requirements it states. Do not paraphrase away or omit these
   specifics; they are what makes the reply actionable.
6. Prefer the knowledge base's non-destructive remedy, and never propose
   data loss it does not call for. When the KB's fix preserves the user's
   data — archiving it, moving it, or raising a limit — recommend that
   remedy. Never tell a user to delete or discard their data, and do not
   raise deletion at all, not even to reassure them it is unnecessary. A
   user's data often carries retention or compliance obligations, and such
   loss is irreversible.
"""

OUTPUT_REMINDER = """\
Respond with exactly one JSON object and nothing else — no explanation
before or after it. If you reconsider any field while composing your
answer, output only your final decision; never emit a second, corrected
JSON object.
"""


def build_system_prompt(kb_dir: Path) -> str:
    articles = sorted(kb_dir.glob("*.md"))
    if not articles:
        raise FileNotFoundError(f"no KB articles found in {kb_dir}")
    corpus = "\n\n---\n\n".join(
        f"### {f.name}\n\n{f.read_text(encoding='utf-8')}" for f in articles
    )
    return (
        f"{TASK_AND_CONTRACT}\n{SAFETY_RULES}\n"
        f"KNOWLEDGE BASE (your ONLY source of facts for drafts):\n\n{corpus}\n\n"
        f"{OUTPUT_REMINDER}"
    )


def format_ticket(ticket_input: dict) -> str:
    sender = ticket_input.get("sender", {})
    return (
        f"Channel: {ticket_input.get('channel', 'unknown')}\n"
        f"From: {sender.get('name', 'unknown')} — {sender.get('role', 'unknown')}, "
        f"{sender.get('company', 'unknown')}\n"
        f"Subject: {ticket_input.get('subject', '(no subject)')}\n\n"
        f"{ticket_input.get('body', '')}"
    )


def _json_object_candidates(text: str):
    """Yield every complete top-level JSON object found in text."""
    decoder = json.JSONDecoder()
    idx = 0
    while True:
        start = text.find("{", idx)
        if start == -1:
            return
        try:
            obj, end = decoder.raw_decode(text, start)
        except json.JSONDecodeError:
            idx = start + 1
            continue
        if isinstance(obj, dict):
            yield obj
        idx = end


def parse_response(raw: str) -> TriageResult:
    """Parse the model's JSON reply into a TriageResult. Raises ValueError
    if no JSON object is present or any enum value is invalid.

    The model occasionally reconsiders mid-response and emits a second,
    corrected JSON object after the first; the last complete object is
    its final decision, so that one wins."""
    text = re.sub(r"```(?:json)?", "", raw).strip()
    candidates = list(_json_object_candidates(text))
    if not candidates:
        raise ValueError("no JSON object found in response")
    data = candidates[-1]

    category = data.get("category")
    if category not in CATEGORIES:
        raise ValueError(f"invalid category: {category!r}")
    priority = data.get("priority")
    if priority not in PRIORITY_ORDER:
        raise ValueError(f"invalid priority: {priority!r}")
    try:
        tier = int(data.get("tier"))
    except (TypeError, ValueError):
        raise ValueError(f"invalid tier: {data.get('tier')!r}")
    if tier not in (1, 2, 3):
        raise ValueError(f"invalid tier: {tier!r}")
    action = data.get("action")
    if action not in ACTIONS:
        raise ValueError(f"invalid action: {action!r}")
    target = data.get("escalation_target")
    if target in ("null", "none", ""):
        target = None
    if target not in ESCALATION_TARGETS:
        raise ValueError(f"invalid escalation_target: {target!r}")

    draft = data.get("draft_response")
    if not isinstance(draft, str) or not draft.strip():
        draft = None
    # Enforce the §2 contract: drafts only accompany auto_resolve.
    if action != "auto_resolve":
        draft = None

    return TriageResult(
        category=category,
        priority=priority,
        tier=tier,
        action=action,
        escalation_target=target,
        draft_response=draft,
    )


def safe_default() -> TriageResult:
    """Fallback when the model's response is unusable: escalate to a
    human. Never auto-resolves, so it can never hard-fail the suite."""
    return TriageResult(
        category="hardware",
        priority="medium",
        tier=2,
        action="escalate",
        escalation_target="tier2_tech",
        draft_response=None,
    )


class TriageV1:
    name = f"triage_v1 ({MODEL})"
    pricing = PRICING_USD_PER_MTOK

    def __init__(self, kb_dir: Path, failures_dir: Path, client: anthropic.Anthropic | None = None):
        self._client = client or anthropic.Anthropic()
        self._system_prompt = build_system_prompt(kb_dir)
        self._failures_dir = Path(failures_dir)
        self.usage_log: list[dict] = []

    def triage(self, ticket_input: dict) -> TriageResult:
        raw = None
        try:
            response = self._client.messages.create(
                model=MODEL,
                max_tokens=2000,
                temperature=0,
                system=[{
                    "type": "text",
                    "text": self._system_prompt,
                    "cache_control": {"type": "ephemeral"},
                }],
                messages=[{"role": "user", "content": format_ticket(ticket_input)}],
            )
            self.usage_log.append({
                "input_tokens": response.usage.input_tokens,
                "output_tokens": response.usage.output_tokens,
                "cache_read_input_tokens": response.usage.cache_read_input_tokens or 0,
                "cache_creation_input_tokens": response.usage.cache_creation_input_tokens or 0,
            })
            raw = "".join(b.text for b in response.content if b.type == "text")
            return parse_response(raw)
        except anthropic.APIError as e:
            self.usage_log.append(dict.fromkeys(USAGE_KEYS, 0))
            self._log_failure(ticket_input, raw, f"API error: {e}")
            return safe_default()
        except ValueError as e:
            self._log_failure(ticket_input, raw, f"parse failure: {e}")
            return safe_default()

    def _log_failure(self, ticket_input: dict, raw: str | None, error: str) -> None:
        self._failures_dir.mkdir(parents=True, exist_ok=True)
        slug = re.sub(
            r"[^a-z0-9]+", "-", ticket_input.get("subject", "no-subject").lower()
        ).strip("-")[:40]
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        path = self._failures_dir / f"{stamp}_{slug}.txt"
        path.write_text(
            f"error: {error}\n\nticket subject: {ticket_input.get('subject')!r}\n\n"
            f"raw response:\n{raw if raw is not None else '(no response received)'}",
            encoding="utf-8",
        )
