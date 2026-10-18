"""Smoke test for server/db.py against the live Supabase database.

Run from repo root: .venv/bin/python server/tests/smoke_db.py
Uses throwaway session ids (smoketest-*) and deletes its own rows at the end.
"""
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from server import db  # noqa: E402

run = uuid.uuid4().hex[:6]
A, B = f"smoketest-{run}-a", f"smoketest-{run}-b"
fact = {"file": "services/user.py", "symbol": "get_user", "qualname": "get_user",
        "change_type": "return_changed", "before": "result of User.get()",
        "after": "dict with keys email, id, name"}


def check(label, ok):
    print(("PASS " if ok else "FAIL ") + label)
    if not ok:
        raise SystemExit(1)


try:
    db.init()
    a = db.create_entry(A, "Poshitha", "claude", "feature/caching", "add caching to user lookups")
    check("create_entry -> in_progress", a["status"] == "in_progress")
    db.create_entry(B, "Kent", "claude", "feature/checkout", "build the checkout flow")

    board = {e["session_id"]: e for e in db.board()}
    check("board shows both sessions as in_progress",
          board[A]["status"] == "in_progress" and board[B]["status"] == "in_progress")

    done = db.finish_entry(A, "Poshitha", "claude", "feature/caching",
                           "changed get_user to return a dict", "<diff>", ["services/user.py"], [fact])
    check("finish_entry updates the same row to done", done["id"] == a["id"] and done["status"] == "done")

    facts_for_b = db.active_facts(exclude_session=B)
    check("active_facts for B includes A's get_user fact",
          any(f["symbol"] == "get_user" and f["session_id"] == A for f in facts_for_b))
    check("active_facts for A excludes A's own facts",
          not any(f["session_id"] == A for f in db.active_facts(exclude_session=A)))

    key = "services/user.py|get_user|return_changed|dict with keys email, id, name"
    kw = dict(to_session=B, from_session=A, to_user="Kent", from_user="Poshitha",
              entry_id=done["id"], source="code_link", target_file="checkout.py",
              text="Teammate update: get_user now returns a dict.", dedupe_key=key)
    n1 = db.queue_note(**kw)
    n2 = db.queue_note(**kw)
    check("queue_note inserts once, duplicate is ignored", n1 is not None and n2 is None)
    check("queue_note never sends a note to its own author",
          db.queue_note(**{**kw, "to_session": A}) is None)
    check("has_note", db.has_note(B, key))

    got = db.take_queued_notes(B, "checkout.py")
    check("take_queued_notes delivers the note", len(got) == 1 and got[0]["status"] == "delivered")
    check("second take returns nothing", db.take_queued_notes(B, "checkout.py") == [])

    b2 = db.create_entry(B, "Kent", "claude", "feature/checkout", "fix the tests")
    linked = db.link_reactions(B, b2["id"])
    check("link_reactions sets reaction_entry_id", len(linked) == 1 and linked[0]["reaction_entry_id"] == b2["id"])

    check("recent_entries / recent_notes", len(db.recent_entries()) >= 3 and len(db.recent_notes()) >= 1)
    print("\nAll db.py checks passed.")
finally:
    db._all("delete from notes where to_session like %s or from_session like %s returning id",
            (f"smoketest-{run}%", f"smoketest-{run}%"))
    db._all("delete from entries where session_id like %s returning id", (f"smoketest-{run}%",))
    db.close()
