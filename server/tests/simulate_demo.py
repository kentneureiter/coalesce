"""Replay the demo story against a running server, the way Kent's hooks would.

  s1 (Poshitha, feature/caching) changes get_user to return a dict.
  s3 (Kent, feature/checkout) goes to edit checkout.py, which calls get_user.
  Check: /relevant returns a note about get_user, exactly once.

Run (server on 8787): .venv/bin/python server/tests/simulate_demo.py [--keep] [http://localhost:8787]
Uses fresh session ids each run and deletes its rows at the end (--keep leaves them for the dashboard).
"""
import sys
import time
import uuid
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from server import db  # noqa: E402  (only for cleanup)

KEEP = "--keep" in sys.argv
args = [a for a in sys.argv[1:] if a != "--keep"]
SERVER = args[0] if args else "http://localhost:8787"
run = uuid.uuid4().hex[:6]
S1 = {"session_id": f"sim-{run}-s1", "dev": "Poshitha", "agent": "claude",
      "branch": "feature/caching", "cwd": "/demo_repo"}
S3 = {"session_id": f"sim-{run}-s3", "dev": "Kent", "agent": "claude",
      "branch": "feature/checkout", "cwd": "/demo_repo"}

USER_BEFORE = "from models import User\n\n\ndef get_user(id):\n    return User.get(id)\n"
USER_AFTER = ("from functools import lru_cache\nfrom models import User\n\n\n@lru_cache(maxsize=1024)\n"
              "def get_user(id):\n    u = User.get(id)\n    return {\"id\": u.id, \"name\": u.name, \"email\": u.email}\n")
CHECKOUT = ("from services.user import get_user\n\n\ndef checkout(user_id, cart):\n"
            "    user = get_user(user_id)\n    return {\"email\": user.email, \"total\": sum(cart)}\n")

c = httpx.Client(base_url=SERVER, timeout=10)


def post(path, body):
    t = time.perf_counter()
    r = c.post(path, json=body)
    r.raise_for_status()
    ms = (time.perf_counter() - t) * 1000
    print(f"POST {path:<10} {ms:6.0f} ms")
    return r.json()


def check(label, ok):
    print(("  PASS " if ok else "  FAIL ") + label)
    if not ok:
        raise SystemExit(1)


try:
    print(f"== simulate_demo against {SERVER} (run {run}) ==\n")

    print("1. Both agents get prompts")
    post("/task", {**S1, "prompt": "add caching to user lookups"})
    ctx = post("/task", {**S3, "prompt": "build the checkout flow"})["context"]
    check("s3's board shows s1 as in progress", "Poshitha" in ctx and "In progress (not yet changed)" in ctx)

    print("\n2. s1 finishes its turn: get_user now returns a dict")
    post("/events", {**S1, "last_message": "Added an LRU cache to get_user and changed it to return a dict.",
                     "diff": "<diff>", "files_changed": ["services/user.py"],
                     "file_versions": [{"path": "services/user.py", "before": USER_BEFORE, "after": USER_AFTER}]})
    for _ in range(50):  # /events processes in the background; wait until s1 shows as done
        if "Files: services/user.py" in c.get("/board", params={"session_id": S3["session_id"]}).json()["text"]:
            break
        time.sleep(0.2)
    board = c.get("/board", params={"session_id": S3["session_id"]}).json()["text"]
    print(board)
    check("board shows s1's changed file", "Files: services/user.py" in board)

    print("\n3. s3 is about to edit checkout.py (which calls get_user)")
    note = post("/relevant", {**S3, "file_path": "checkout.py", "content": CHECKOUT, "tool_name": "Edit"})["context"]
    print("  injected context:\n    " + (note or "(empty)"))
    check("note mentions get_user and checkout.py", "get_user" in note and "checkout.py" in note)
    check("note is from Poshitha's session", "Poshitha" in note and "feature/caching" in note)
    check("note has the AST fact (before -> now)",
          "Before: result of User.get(). Now: dict with keys email, id, name." in note)

    print("\n4. Edge cases")
    again = post("/relevant", {**S3, "file_path": "checkout.py", "content": CHECKOUT, "tool_name": "Edit"})["context"]
    check("same fact is not delivered twice", again == "")
    own = post("/relevant", {**S1, "file_path": "services/user.py", "content": USER_AFTER + "\nget_user(1)\n"})["context"]
    check("s1 gets no note about its own change", own == "")

    print("\nAll demo checks passed.")
finally:
    if not KEEP:
        like = f"sim-{run}-%"
        db._all("delete from notes where to_session like %s or from_session like %s returning id", (like, like))
        db._all("delete from entries where session_id like %s returning id", (like,))
        db.close()
