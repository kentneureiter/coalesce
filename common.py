"""Shared helpers for Coalesce hooks. Python stdlib only.

Rules (contract section 6.1): read JSON from stdin, print nothing but the final
JSON, fail open on any error, 3 s HTTP timeout, repo-relative paths.
"""
import json
import os
import subprocess
import sys
import urllib.request
from datetime import datetime, timezone

HTTP_TIMEOUT = 3  # seconds
CONFIG_DIR = ".coalesce"


def parse_agent(argv):
    """Read --agent claude|gemini from the command line (default claude)."""
    if "--agent" in argv:
        i = argv.index("--agent")
        if i + 1 < len(argv):
            return argv[i + 1]
    return "claude"


def read_stdin():
    raw = sys.stdin.read()
    return json.loads(raw) if raw.strip() else {}


def find_config(start):
    """Walk up from `start` looking for .coalesce/config.json. None if missing."""
    path = os.path.abspath(start or os.getcwd())
    while True:
        candidate = os.path.join(path, CONFIG_DIR, "config.json")
        if os.path.isfile(candidate):
            with open(candidate) as f:
                cfg = json.load(f)
            cfg["_root"] = path
            return cfg
        parent = os.path.dirname(path)
        if parent == path:
            return None
        path = parent


def log(cfg, message):
    """Append to .coalesce/hook.log. Never raises, never prints."""
    try:
        root = cfg["_root"] if cfg else os.getcwd()
        with open(os.path.join(root, CONFIG_DIR, "hook.log"), "a") as f:
            f.write(f"{datetime.now(timezone.utc).isoformat()} {message}\n")
    except Exception:
        pass


def git(cwd, *args):
    """Run a git command in cwd and return stdout stripped ('' on failure)."""
    try:
        out = subprocess.run(
            ["git", *args], cwd=cwd, capture_output=True, text=True, timeout=2
        )
        return out.stdout.strip() if out.returncode == 0 else ""
    except Exception:
        return ""


def session_header(payload, cfg, agent):
    """The fields every request to the server carries (contract section 8)."""
    cwd = payload.get("cwd") or os.getcwd()
    return {
        "session_id": payload.get("session_id", ""),
        "dev": cfg.get("dev", ""),
        "agent": agent,
        "branch": git(cwd, "branch", "--show-current"),  # works even before the first commit
        "cwd": cwd,
    }


def post_json(cfg, path, body):
    """POST JSON to the Coalesce server and return the parsed response."""
    url = cfg["server"].rstrip("/") + path
    req = urllib.request.Request(
        url,
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as resp:
        text = resp.read().decode()
        return json.loads(text) if text else {}


def emit_context(agent, event_name, text):
    """Print the hook output that injects `text` into the agent's context."""
    print(json.dumps({
        "hookSpecificOutput": {"hookEventName": event_name, "additionalContext": text}
    }))


def emit_nothing(agent):
    """No context: Claude prints nothing, Gemini prints {}."""
    if agent == "gemini":
        print("{}")