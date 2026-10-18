"""Pure logic: does a file use a changed function, and what note text to show.

No database, no network. Everything here is unit-tested in tests/test_relevance.py.
Note text is always factual statements, never instructions (contract 10.3).
"""
import re
from datetime import datetime, timezone

MAX_NOTE = 600


def fact_key(fact: dict) -> str:
    """Dedupe key for a fact: file|qualname|change_type|after."""
    return "|".join(str(fact.get(k) or "") for k in ("file", "qualname", "change_type", "after"))


def find_use_line(symbol: str, content: str) -> int | None:
    """1-based line where content uses symbol (contract 10.2), else None.

    Prefers the first call `\\bsymbol\\s*\\(` (a `def symbol(` line is a definition, not a use),
    falls back to the first import `import .*\\bsymbol\\b`.
    """
    if not symbol or not content:
        return None
    s = re.escape(symbol)
    call = re.compile(rf"\b{s}\s*\(")
    define = re.compile(rf"^\s*(async\s+)?def\s+{s}\b")
    imp = re.compile(rf"import .*\b{s}\b")
    import_line = None
    for i, line in enumerate(content.splitlines(), start=1):
        if define.search(line):
            continue
        if call.search(line):
            return i
        if import_line is None and imp.search(line):
            import_line = i
    return import_line


def ago(ts: datetime | None, now: datetime | None = None) -> str:
    if ts is None:
        return "recently"
    now = now or datetime.now(timezone.utc)
    minutes = int((now - ts).total_seconds() // 60)
    if minutes < 1:
        return "just now"
    if minutes < 60:
        return f"{minutes} min ago"
    return f"{minutes // 60} h ago"


def describe_fact(fact: dict) -> str:
    """One sentence (or two) saying what changed, e.g. for the note or Slack."""
    name, file = fact.get("qualname") or fact.get("symbol"), fact.get("file")
    before, after = fact.get("before"), fact.get("after")
    kind = fact.get("change_type")
    if kind == "return_changed":
        return f"{name} in {file} changed its return value. Before: {before}. Now: {after}."
    if kind == "signature_changed":
        return f"{name} in {file} changed its signature. Before: ({before}). Now: ({after})."
    if kind == "removed":
        return f"{name} was removed from {file}."
    if kind == "added":
        return f"{name} was added to {file} with arguments ({after})."
    return f"{name} in {file} was modified."


def _clip(text: str) -> str:
    return text if len(text) <= MAX_NOTE else text[: MAX_NOTE - 3] + "..."


def _header(from_user: str, agent: str, branch: str, when: datetime | None, now: datetime | None) -> str:
    return (f"Teammate update ({from_user}, {agent} session on {branch or 'unknown branch'}, "
            f"{ago(when, now)}, not merged yet):")


def code_link_note(fact: dict, from_user: str, agent: str, branch: str, when: datetime | None,
                   target_file: str, line: int | None, now: datetime | None = None) -> str:
    symbol = fact.get("symbol")
    where = f" on line {line}" if line else ""
    return _clip(f"{_header(from_user, agent, branch, when, now)} {describe_fact(fact)} "
                 f"Your file {target_file} calls {symbol}{where}.")


def judge_note(judge_text: str, from_user: str, agent: str, branch: str,
               when: datetime | None, now: datetime | None = None) -> str:
    return _clip(f"{_header(from_user, agent, branch, when, now)} {judge_text.strip()}")


def human_note(author: str, text: str, source: str) -> str:
    via = "Slack" if source == "slack" else "the coalesce CLI"
    text = text.strip()
    if text and text[-1] not in ".!?":
        text += "."
    return _clip(f"Team note from {author} (via {via}): {text}")


def board_text(entries: list[dict], exclude_session: str | None = None) -> str:
    """Team board from the latest entry per session (contract 10.3). '' if nobody else."""
    lines = []
    for e in entries:
        if exclude_session and e.get("session_id") == exclude_session:
            continue
        task = (e.get("prompt") or "").replace("\n", " ").strip()
        if len(task) > 120:
            task = task[:117] + "..."
        line = f'- {e.get("user_name")}, {e.get("agent")}, {e.get("branch") or "unknown branch"}: "{task}".'
        if e.get("status") == "in_progress":
            line += " In progress (not yet changed)."
        files = e.get("files_changed") or []
        if files:
            line += " Files: " + ", ".join(files)
        lines.append(line)
    if not lines:
        return ""
    return "Coalesce team board (other active agent sessions):\n" + "\n".join(lines)
