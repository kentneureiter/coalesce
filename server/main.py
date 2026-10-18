"""Coalesce server. Hooks call these endpoints; the dashboard reads Supabase directly.

Run from repo root: .venv/bin/uvicorn server.main:app --host 0.0.0.0 --port 8787

Fast paths (/task, /relevant) only touch the DB and do string matching.
Slow work (AST facts, LLM judge, Slack) runs in background tasks after the response.
"""
import logging
import uuid
from contextlib import asynccontextmanager

from fastapi import BackgroundTasks, FastAPI
from pydantic import BaseModel, ConfigDict

from server import ast_facts, db, judge, relevance, slack

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s", datefmt="%H:%M:%S")
log = logging.getLogger("coalesce")


@asynccontextmanager
async def lifespan(app: FastAPI):
    db.init()
    slack.start_listener(add_human_note)
    yield
    db.close()


app = FastAPI(title="Coalesce", lifespan=lifespan)


# ---------- request bodies ----------

class Header(BaseModel):
    """Session header every hook payload carries (contract 8)."""
    model_config = ConfigDict(extra="ignore")
    session_id: str
    dev: str = "unknown"
    agent: str = "claude"
    branch: str = ""
    cwd: str = ""


class TaskIn(Header):
    prompt: str = ""


class FileVersion(BaseModel):
    path: str
    before: str = ""
    after: str = ""


class EventIn(Header):
    last_message: str = ""
    diff: str = ""
    files_changed: list[str] = []
    file_versions: list[FileVersion] = []


class RelevantIn(Header):
    file_path: str
    content: str = ""
    tool_name: str = ""


class HumanNoteIn(BaseModel):
    author: str
    text: str
    source: str = "cli"  # slack | cli


# ---------- endpoints ----------

@app.post("/task")
def post_task(body: TaskIn, bg: BackgroundTasks):
    entry = db.create_entry(body.session_id, body.dev, body.agent, body.branch, body.prompt)
    # Notes delivered before this prompt now have their reaction: this new entry.
    db.link_reactions(body.session_id, entry["id"])
    board = relevance.board_text(db.board(), exclude_session=body.session_id)
    notes = db.take_queued_notes(body.session_id)
    context = "\n\n".join(([board] if board else []) + [n["text"] for n in notes])
    log.info("/task %s [%s] %r -> board=%s notes=%d",
             body.dev, body.branch, body.prompt[:60], bool(board), len(notes))
    bg.add_task(judge_new_prompt, body, entry)
    return {"context": context}


@app.post("/events")
def post_events(body: EventIn, bg: BackgroundTasks):
    bg.add_task(process_event, body)
    return {"ok": True}


@app.post("/relevant")
def post_relevant(body: RelevantIn, bg: BackgroundTasks):
    # Read-time code links: does the file the agent is about to edit use a changed function?
    for fact in db.active_facts(exclude_session=body.session_id):
        line = relevance.find_use_line(fact["symbol"], body.content)
        if line is None:
            continue
        text = relevance.code_link_note(fact, fact["user_name"], fact["agent"], fact["branch"],
                                        fact["completed_at"], body.file_path, line)
        db.queue_note(to_session=body.session_id, from_session=fact["session_id"],
                      to_user=body.dev, from_user=fact["user_name"], entry_id=fact["entry_id"],
                      source="code_link", target_file=body.file_path, text=text,
                      dedupe_key=relevance.fact_key(fact))
    notes = db.take_queued_notes(body.session_id, body.file_path)
    for n in notes:
        log.info("/relevant delivered note %s (%s) to %s before editing %s",
                 n["id"], n["source"], body.dev, body.file_path)
        if n["source"] in ("code_link", "llm_judge"):
            bg.add_task(slack.post_delivery, n, None)
    return {"context": "\n\n".join(n["text"] for n in notes)}


@app.post("/human_note")
def post_human_note(body: HumanNoteIn):
    return {"ok": True, "sessions": add_human_note(body.author, body.text, body.source)}


@app.get("/board")
def get_board(session_id: str | None = None):
    return {"text": relevance.board_text(db.board(), exclude_session=session_id)}


# ---------- shared logic ----------

def add_human_note(author: str, text: str, source: str) -> int:
    """Queue a human note for every active session. Also called by Slack in (step 6).
    The live DB only allows source in (code_link, llm_judge, slack), so humans use 'slack'."""
    note_text = relevance.human_note(author, text, source)
    key = f"human:{uuid.uuid4().hex}"
    count = 0
    for e in db.board():
        if db.queue_note(to_session=e["session_id"], from_session=None, to_user=e["user_name"],
                         from_user=author, entry_id=None, source="slack", target_file=None,
                         text=note_text, dedupe_key=key):
            count += 1
    log.info("human note from %s queued for %d sessions", author, count)
    return count


def process_event(body: EventIn) -> None:
    """Background: extract facts, close the entry, ask the judge about other sessions."""
    try:
        facts = []
        for fv in body.file_versions:
            facts += ast_facts.extract(fv.path, fv.before, fv.after)
        entry = db.finish_entry(body.session_id, body.dev, body.agent, body.branch,
                                body.last_message, body.diff, body.files_changed, facts)
        log.info("/events %s [%s] entry %s done, %d facts: %s", body.dev, body.branch, entry["id"],
                 len(facts), ", ".join(f"{f['qualname']} {f['change_type']}" for f in facts))
        if not facts:
            return
        change = {"from_user": body.dev, "branch": body.branch, "task": entry["prompt"],
                  "last_message": body.last_message, "facts": facts}
        for other in db.board():
            if other["session_id"] == body.session_id:
                continue
            _judge_and_queue(change, other, facts, from_session=body.session_id,
                             entry_id=entry["id"], agent=body.agent, when=entry["completed_at"])
    except Exception:
        log.exception("process_event failed")


def judge_new_prompt(body: TaskIn, entry: dict) -> None:
    """Background: judge this session's new prompt against other sessions' active facts."""
    try:
        by_session: dict[str, list[dict]] = {}
        for f in db.active_facts(exclude_session=body.session_id):
            by_session.setdefault(f["session_id"], []).append(f)
        other = {**entry, "files_changed": []}
        for from_session, facts in by_session.items():
            src = db.latest_done_entry(from_session)
            change = {"from_user": src["user_name"], "branch": src["branch"], "task": src["prompt"],
                      "last_message": src["finalised_changes"], "facts": facts}
            _judge_and_queue(change, other, facts, from_session=from_session, entry_id=src["id"],
                             agent=src["agent"], when=src["completed_at"])
    except Exception:
        log.exception("judge_new_prompt failed")


def _judge_and_queue(change: dict, other: dict, facts: list[dict], *, from_session: str,
                     entry_id: int, agent: str, when) -> None:
    """Ask the judge about facts `other` hasn't heard of yet; queue one llm_judge note if relevant."""
    pending = [f for f in facts if not db.has_note(other["session_id"], relevance.fact_key(f))]
    if not pending:
        return
    text = judge.judge({**change, "facts": pending}, other)
    if not text:
        return
    note = db.queue_note(to_session=other["session_id"], from_session=from_session,
                         to_user=other["user_name"], from_user=change["from_user"], entry_id=entry_id,
                         source="llm_judge", target_file=None,
                         text=relevance.judge_note(text, change["from_user"], agent, change["branch"], when),
                         dedupe_key=relevance.fact_key(pending[0]))
    if note:
        log.info("judge queued note %s for %s", note["id"], other["user_name"])
