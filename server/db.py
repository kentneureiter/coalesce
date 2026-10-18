"""All database access for the Coalesce server. Nothing else touches Postgres.

Two tables in Supabase (live schema is the source of truth, see server/tools/dump_schema.py):
  entries: one row per prompt. in_progress on /task, done on /events, stale after 60 min idle.
  notes:   one row per (recipient session, fact). Deduped by UNIQUE (to_session, dedupe_key).
"""
import os
from pathlib import Path

import psycopg
from dotenv import load_dotenv
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from psycopg_pool import ConnectionPool

load_dotenv(Path(__file__).resolve().parent / ".env")

ACTIVE_MINUTES = 60

_pool: ConnectionPool | None = None


def init() -> None:
    """Open the connection pool. Call once on server startup."""
    global _pool
    if _pool is None:
        _pool = ConnectionPool(
            os.environ["DATABASE_URL"],
            min_size=1,
            max_size=5,
            open=True,
            # autocommit: every statement commits on its own. prepare_threshold=None
            # keeps it working through Supabase's pooler. Keepalives stop idle
            # connections from being silently dropped.
            kwargs={"autocommit": True, "row_factory": dict_row, "prepare_threshold": None,
                    "keepalives": 1, "keepalives_idle": 30, "keepalives_interval": 10,
                    "keepalives_count": 3},
        )


def close() -> None:
    global _pool
    if _pool is not None:
        _pool.close()
        _pool = None


def _all(sql: str, params=()) -> list[dict]:
    init()
    try:
        with _pool.connection() as conn:
            return conn.execute(sql, params).fetchall()
    except psycopg.OperationalError:
        # The pooled connection was dropped while idle. The pool discards it;
        # retry once on a fresh one.
        with _pool.connection() as conn:
            return conn.execute(sql, params).fetchall()


def _one(sql: str, params=()) -> dict | None:
    rows = _all(sql, params)
    return rows[0] if rows else None


# ---------- entries ----------

def create_entry(session_id: str, user_name: str, agent: str, branch: str, prompt: str) -> dict:
    """POST /task: a new prompt starts a new in_progress entry."""
    return _one(
        """insert into entries (session_id, user_name, agent, branch, prompt, status)
           values (%s, %s, %s, %s, %s, 'in_progress') returning *""",
        (session_id, user_name, agent, branch, prompt),
    )


def finish_entry(session_id: str, user_name: str, agent: str, branch: str,
                 finalised_changes: str, diff: str, files_changed: list, facts: list) -> dict:
    """POST /events: mark this session's latest in_progress entry done.

    If the session has no in_progress entry (e.g. the prompt hook didn't fire),
    insert a done entry so the change is still recorded.
    """
    row = _one(
        """update entries
              set status = 'done', finalised_changes = %s, diff = %s,
                  files_changed = %s, facts = %s, completed_at = now()
            where id = (select id from entries
                         where session_id = %s and status = 'in_progress'
                         order by created_at desc limit 1)
        returning *""",
        (finalised_changes, diff, Jsonb(files_changed), Jsonb(facts), session_id),
    )
    if row:
        return row
    return _one(
        """insert into entries (session_id, user_name, agent, branch, prompt, status,
                                finalised_changes, diff, files_changed, facts, completed_at)
           values (%s, %s, %s, %s, '', 'done', %s, %s, %s, %s, now()) returning *""",
        (session_id, user_name, agent, branch, finalised_changes, diff,
         Jsonb(files_changed), Jsonb(facts)),
    )


def mark_stale() -> None:
    """Entries with no activity for ACTIVE_MINUTES become 'stale'."""
    _all(
        f"""update entries set status = 'stale'
             where status <> 'stale'
               and coalesce(completed_at, created_at) < now() - interval '{ACTIVE_MINUTES} minutes'
         returning id"""
    )


def board() -> list[dict]:
    """Latest entry per active session (team board). Stale sessions are hidden."""
    mark_stale()
    return _all(
        """select * from (
             select distinct on (session_id) *
               from entries
              where session_id is not null
              order by session_id, created_at desc
           ) latest
           where status <> 'stale'
           order by created_at desc"""
    )


def latest_done_entry(session_id: str) -> dict | None:
    """The session's newest finished entry. Its facts are cumulative for the branch."""
    return _one(
        """select * from entries
            where session_id = %s and status = 'done'
            order by created_at desc limit 1""",
        (session_id,),
    )


def active_facts(exclude_session: str | None = None) -> list[dict]:
    """Facts from every active session's latest done entry, except exclude_session.

    Returns one dict per fact, with the source entry's info attached:
    {fact..., "entry_id", "session_id", "user_name", "agent", "branch", "completed_at"}.
    """
    rows = _all(
        f"""select distinct on (session_id)
                   id, session_id, user_name, agent, branch, facts, completed_at
              from entries
             where status = 'done' and session_id is not null
               and session_id is distinct from %s
               and completed_at >= now() - interval '{ACTIVE_MINUTES} minutes'
             order by session_id, created_at desc""",
        (exclude_session,),
    )
    out = []
    for r in rows:
        for fact in r["facts"] or []:
            out.append({**fact, "entry_id": r["id"], "session_id": r["session_id"],
                        "user_name": r["user_name"], "agent": r["agent"],
                        "branch": r["branch"], "completed_at": r["completed_at"]})
    return out


def recent_entries(limit: int = 50) -> list[dict]:
    return _all("select * from entries order by created_at desc limit %s", (limit,))


# ---------- notes ----------

def queue_note(*, to_session: str, from_session: str | None, to_user: str | None,
               from_user: str | None, entry_id: int | None, source: str,
               target_file: str | None, text: str, dedupe_key: str) -> dict | None:
    """Queue a note for one session. Returns the new row, or None if it was a
    duplicate (same to_session + dedupe_key) or addressed to its own author."""
    if from_session is not None and to_session == from_session:
        return None
    return _one(
        """insert into notes (to_session, from_session, to_user, from_user, entry_id,
                              source, target_file, text, dedupe_key)
           values (%s, %s, %s, %s, %s, %s, %s, %s, %s)
           on conflict (to_session, dedupe_key) do nothing
           returning *""",
        (to_session, from_session, to_user, from_user, entry_id,
         source, target_file, text, dedupe_key),
    )


def has_note(to_session: str, dedupe_key: str) -> bool:
    return _one(
        "select 1 as x from notes where to_session = %s and dedupe_key = %s",
        (to_session, dedupe_key),
    ) is not None


def take_queued_notes(to_session: str, target_file: str | None = None) -> list[dict]:
    """Mark every queued note for this session delivered and return them, oldest first.
    target_file (the file being edited) is recorded on notes that don't have one yet."""
    rows = _all(
        """update notes
              set status = 'delivered', delivered_at = now(),
                  target_file = coalesce(target_file, %s)
            where to_session = %s and status = 'queued'
        returning *""",
        (target_file, to_session),
    )
    return sorted(rows, key=lambda r: r["id"])


def link_reactions(to_session: str, entry_id: int) -> list[dict]:
    """The recipient's next entry after delivery is its reaction to the note."""
    return _all(
        """update notes set reaction_entry_id = %s
            where to_session = %s and status = 'delivered' and reaction_entry_id is null
        returning *""",
        (entry_id, to_session),
    )


def recent_notes(limit: int = 50) -> list[dict]:
    return _all("select * from notes order by created_at desc limit %s", (limit,))
