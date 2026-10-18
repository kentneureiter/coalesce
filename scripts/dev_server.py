"""TEMPORARY stand-in for Poshitha's server so the prompt hook can be tested
end to end before server/main.py exists. Python stdlib only. Delete it once
the real server implements POST /task.

Implements (contract section 8, prompt path only):
  POST /task   upsert `sessions`, insert `tasks`, return the team board as context
  GET  /tasks  last 20 prompts, for checking that rows landed
  GET  /board  ?session_id=... the board text that session would get

Storage: SQLite at scripts/dev.db, using the contract's `sessions` and `tasks`
tables (section 9), so it matches what the real server will store.
Optional: if SUPABASE_URL and SUPABASE_SERVICE_KEY are set, each prompt is
also copied into the Supabase `entries` table (in a background thread, so
/task stays under the 500 ms budget).

Run (from the coalesce repo):
  python3 scripts/dev_server.py            # listens on 0.0.0.0:8787
"""
import json
import os
import sqlite3
import threading
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.environ.get("COALESCE_DEV_DB", os.path.join(HERE, "dev.db"))
PORT = int(os.environ.get("PORT", "8787"))
SUPABASE_URL = os.environ.get("SUPABASE_URL", "").rstrip("/")
SUPABASE_KEY = os.environ.get("SUPABASE_SERVICE_KEY", "")
ACTIVE_MINUTES = 60  # contract: active session = last_seen within 60 min

SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
  id TEXT PRIMARY KEY,
  dev TEXT NOT NULL,
  agent TEXT NOT NULL,
  branch TEXT,
  cwd TEXT,
  current_task TEXT,
  files_json TEXT DEFAULT '[]',
  last_seen TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS tasks (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  session_id TEXT NOT NULL REFERENCES sessions(id),
  prompt TEXT NOT NULL,
  created_at TEXT NOT NULL
);
"""

_lock = threading.Lock()


def now_iso():
    return datetime.now(timezone.utc).isoformat()


def connect():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    with connect() as conn:
        conn.executescript(SCHEMA)


def record_task(body):
    ts = now_iso()
    sid = body.get("session_id") or "unknown"
    prompt = body.get("prompt", "")
    with _lock:
        conn = connect()
        try:
            with conn:
                conn.execute(
                    """INSERT INTO sessions (id, dev, agent, branch, cwd, current_task, last_seen)
                       VALUES (?, ?, ?, ?, ?, ?, ?)
                       ON CONFLICT(id) DO UPDATE SET
                         dev=excluded.dev, agent=excluded.agent, branch=excluded.branch,
                         cwd=excluded.cwd, current_task=excluded.current_task,
                         last_seen=excluded.last_seen""",
                    (sid, body.get("dev") or "unknown", body.get("agent") or "claude",
                     body.get("branch"), body.get("cwd"), prompt, ts),
                )
                conn.execute(
                    "INSERT INTO tasks (session_id, prompt, created_at) VALUES (?, ?, ?)",
                    (sid, prompt, ts),
                )
        finally:
            conn.close()
    return sid


def team_board(session_id):
    """Contract 10.3 board text for every OTHER active session ('' if none)."""
    cutoff = (datetime.now(timezone.utc) - timedelta(minutes=ACTIVE_MINUTES)).isoformat()
    conn = connect()
    try:
        rows = conn.execute(
            "SELECT * FROM sessions WHERE id != ? AND last_seen >= ? ORDER BY last_seen DESC",
            (session_id, cutoff),
        ).fetchall()
    finally:
        conn.close()
    if not rows:
        return ""
    lines = ["Coalesce team board (other active agent sessions):"]
    for r in rows:
        task = (r["current_task"] or "").replace("\n", " ")
        if len(task) > 120:
            task = task[:117] + "..."
        line = f'- {r["dev"]}, {r["agent"]}, {r["branch"] or "unknown branch"}: "{task}".'
        files = json.loads(r["files_json"] or "[]")
        if files:
            line += " Files: " + ", ".join(files)
        lines.append(line)
    return "\n".join(lines)


def mirror_to_supabase(body):
    """Best-effort copy into Supabase `entries`. Never raises."""
    if not (SUPABASE_URL and SUPABASE_KEY):
        return
    row = {
        "session_id": body.get("session_id"),
        "user_name": body.get("dev") or "unknown",
        "agent": body.get("agent"),
        "branch": body.get("branch"),
        "prompt": body.get("prompt", ""),
        "status": "in_progress",
    }
    headers = {"apikey": SUPABASE_KEY, "Content-Type": "application/json",
               "Prefer": "return=minimal"}
    if SUPABASE_KEY.startswith("eyJ"):  # legacy JWT keys also go in Authorization
        headers["Authorization"] = f"Bearer {SUPABASE_KEY}"
    try:
        req = urllib.request.Request(f"{SUPABASE_URL}/rest/v1/entries",
                                     data=json.dumps(row).encode(), headers=headers,
                                     method="POST")
        with urllib.request.urlopen(req, timeout=5) as resp:
            resp.read()
    except Exception as e:
        print(f"  supabase mirror failed: {e!r}")


class Handler(BaseHTTPRequestHandler):
    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        try:
            body = json.loads(self.rfile.read(length) or b"{}")
        except json.JSONDecodeError:
            return self._json({"error": "bad json"}, 400)

        if self.path == "/task":
            sid = record_task(body)
            context = team_board(sid)
            print(f"/task {body.get('dev')} [{body.get('branch')}] "
                  f"session={sid[:8]} prompt={body.get('prompt', '')[:60]!r}"
                  f"{'  -> board injected' if context else ''}")
            threading.Thread(target=mirror_to_supabase, args=(body,), daemon=True).start()
            return self._json({"context": context})
        # /events, /relevant etc. are not implemented here yet: answer harmlessly.
        return self._json({"ok": True, "context": ""})

    def do_GET(self):
        url = urllib.parse.urlparse(self.path)
        if url.path == "/tasks":
            conn = connect()
            try:
                rows = conn.execute(
                    """SELECT t.id, t.created_at, s.dev, s.agent, s.branch, t.session_id, t.prompt
                       FROM tasks t JOIN sessions s ON s.id = t.session_id
                       ORDER BY t.id DESC LIMIT 20""").fetchall()
            finally:
                conn.close()
            return self._json([dict(r) for r in rows])
        if url.path == "/board":
            sid = urllib.parse.parse_qs(url.query).get("session_id", [""])[0]
            return self._json({"text": team_board(sid)})
        return self._json({"ok": True})

    def _json(self, obj, code=200):
        data = json.dumps(obj, indent=2).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *args):
        pass


if __name__ == "__main__":
    init_db()
    mirror = f" (+ Supabase mirror to {SUPABASE_URL})" if SUPABASE_URL and SUPABASE_KEY else ""
    print(f"dev_server on 0.0.0.0:{PORT}, sqlite {DB_PATH}{mirror}")
    ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()
