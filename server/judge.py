"""LLM judge (contract 10.4): does a teammate's change matter to another agent's work?

Runs only in background tasks, never inside a request. Any error -> not relevant.
"""
import json
import logging
import os
from pathlib import Path

import anthropic
from dotenv import load_dotenv

from server import relevance

load_dotenv(Path(__file__).resolve().parent / ".env")

log = logging.getLogger("coalesce.judge")

MODEL = os.environ.get("COALESCE_JUDGE_MODEL") or "claude-haiku-5-5"

PROMPT = """You decide if a coding agent needs to know about a teammate's in-progress change.

Teammate's change (session {from_dev}, branch {from_branch}):
Task: {from_task}
Agent's summary: {from_last_message}
Exact changes:
{facts}

Other agent (session {to_dev}, branch {to_branch}):
Task: {to_task}
Files it is working on: {to_files}
Snippets: {snippets}

Answer with JSON only: {{"relevant": true or false, "note": "<one or two factual sentences about what changed and how it touches the other agent's work, no instructions>"}}
Only say relevant if the change could break or should change the other agent's work."""

SCHEMA = {
    "type": "object",
    "properties": {"relevant": {"type": "boolean"}, "note": {"type": "string"}},
    "required": ["relevant", "note"],
    "additionalProperties": False,
}

_client: anthropic.Anthropic | None = None


def _get_client() -> anthropic.Anthropic | None:
    global _client
    if _client is None:
        if not os.environ.get("ANTHROPIC_API_KEY"):
            return None
        _client = anthropic.Anthropic(timeout=10.0, max_retries=0)
    return _client


def build_prompt(change: dict, other: dict) -> str:
    facts = "\n".join(f"- {relevance.describe_fact(f)}" for f in change.get("facts") or []) or "- (none)"
    snippets = other.get("snippets") or "(not available)"
    return PROMPT.format(
        from_dev=change.get("from_user"), from_branch=change.get("branch"),
        from_task=change.get("task") or "(unknown)",
        from_last_message=(change.get("last_message") or "(none)")[:1500],
        facts=facts,
        to_dev=other.get("user_name"), to_branch=other.get("branch"),
        to_task=other.get("prompt") or "(unknown)",
        to_files=", ".join(other.get("files_changed") or []) or "(none yet)",
        snippets=snippets,
    )


def judge(change: dict, other: dict) -> str | None:
    """Return a factual note if `change` matters to `other`, else None. Never raises."""
    try:
        client = _get_client()
        if client is None:
            return None
        response = client.messages.create(
            model=MODEL,
            max_tokens=200,
            thinking={"type": "disabled"},
            output_config={"effort": "low",
                           "format": {"type": "json_schema", "schema": SCHEMA}},
            messages=[{"role": "user", "content": build_prompt(change, other)}],
        )
        if response.stop_reason != "end_turn":
            log.info("judge: no answer (stop_reason=%s)", response.stop_reason)
            return None
        text = next(b.text for b in response.content if b.type == "text")
        data = json.loads(text)
        note = (data.get("note") or "").strip()
        if data.get("relevant") is True and note:
            log.info("judge: relevant for %s: %s", other.get("user_name"), note)
            return note
        return None
    except Exception as e:
        log.info("judge: error, treating as not relevant: %s", type(e).__name__)
        return None
