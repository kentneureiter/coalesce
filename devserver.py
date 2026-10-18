"""TEMPORARY stand-in for Poshitha's POST /task, so the prompt hook can be
tested end to end before the real server exists. Python stdlib only.

Writes each prompt into the Supabase `entries` table and returns empty context.
Delete once server/main.py implements /task.

Run:
  export SUPABASE_URL=https://<project-ref>.supabase.co
  export SUPABASE_SERVICE_KEY=<service_role or secret key>   # never commit this
  python3 scripts/dev_server.py            # listens on 0.0.0.0:8787
"""
import json
import os
import urllib.request
from http.server import BaseHTTPRequestHandler, HTTPServer

SUPABASE_URL = os.environ["SUPABASE_URL"].rstrip("/")
SUPABASE_KEY = os.environ["SUPABASE_SERVICE_KEY"]
PORT = int(os.environ.get("PORT", "8787"))


def insert_entry(row):
    headers = {
        "apikey": SUPABASE_KEY,
        "Content-Type": "application/json",
        "Prefer": "return=minimal",
    }
    if SUPABASE_KEY.startswith("eyJ"):  # legacy JWT keys also go in Authorization
        headers["Authorization"] = f"Bearer {SUPABASE_KEY}"
    req = urllib.request.Request(
        f"{SUPABASE_URL}/rest/v1/entries",
        data=json.dumps(row).encode(),
        headers=headers,
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=5) as resp:
        resp.read()


class Handler(BaseHTTPRequestHandler):
    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        body = json.loads(self.rfile.read(length) or b"{}")

        if self.path == "/task":
            row = {
                "session_id": body.get("session_id"),
                "user_name": body.get("dev") or "unknown",
                "agent": body.get("agent"),
                "branch": body.get("branch"),
                "prompt": body.get("prompt", ""),
                "status": "in_progress",
            }
            try:
                insert_entry(row)
                print(f"/task stored: {row['user_name']} {row['branch']} {row['prompt'][:60]!r}")
            except Exception as e:
                print(f"/task Supabase insert failed: {e!r}")
            self._json({"context": ""})
        else:
            self._json({"ok": True})

    def _json(self, obj):
        data = json.dumps(obj).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *args):
        pass


if __name__ == "__main__":
    print(f"dev_server on 0.0.0.0:{PORT} -> {SUPABASE_URL}")
    HTTPServer(("0.0.0.0", PORT), Handler).serve_forever()