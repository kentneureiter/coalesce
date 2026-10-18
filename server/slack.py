"""STUB until step 6. Real version: contract 10.5 (webhook out, Bolt socket mode in)."""
import logging

log = logging.getLogger("coalesce.slack")


def post_delivery(note: dict, fact: dict | None) -> None:
    """Fire and forget: tell Slack a note was delivered. Stub: log only."""
    log.info("slack (stub) would post: note %s -> %s", note.get("id"), note.get("to_session"))


def start_listener(on_message) -> None:
    """Start the Slack socket-mode listener in a thread. Stub: does nothing."""
    return None
