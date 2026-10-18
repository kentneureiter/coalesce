"""Stop hook: Claude Stop (async) / Gemini AfterAgent.

Sends what this branch changed (diff, files, before/after of .py files) to
POST /events. Never returns context or a decision (that makes the agent loop).
Fails open: any problem -> exit 0.
"""
import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import common  # noqa: E402

DIFF_CAP = 100_000   # bytes, contract 6.4
FILE_CAP = 50_000    # bytes per file version
IGNORED_DIRS = (".claude/", ".gemini/", ".coalesce/")  # agent/Coalesce config, not code changes


def git_raw(cwd, *args):
    """Like common.git but keeps the output exactly (file contents need their whitespace)."""
    try:
        out = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, timeout=5)
        return out.stdout if out.returncode == 0 else ""
    except Exception:
        return ""


def base_commit(root, base_branch):
    """merge-base of HEAD and the base branch (local, then origin/), else HEAD."""
    for ref in (base_branch, f"origin/{base_branch}"):
        base = common.git(root, "merge-base", "HEAD", ref)
        if base:
            return base
    return "HEAD"


def read_file(path):
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            return f.read(FILE_CAP)
    except OSError:
        return ""  # deleted


def main():
    agent = common.parse_agent(sys.argv)
    cfg = None
    try:
        if os.environ.get("COALESCE_OFF") == "1":
            return
        payload = common.read_stdin()
        cfg = common.find_config(payload.get("cwd"))
        if cfg is None:
            return
        cwd = payload.get("cwd") or os.getcwd()
        root = common.git(cwd, "rev-parse", "--show-toplevel")
        if not root:
            return

        base = base_commit(root, cfg.get("base_branch", "main"))
        tracked = [p for p in common.git(root, "diff", "--name-only", base).splitlines() if p]
        untracked = [p for p in common.git(root, "ls-files", "--others", "--exclude-standard").splitlines() if p]
        files_changed = sorted(p for p in set(tracked) | set(untracked)
                               if not p.startswith(IGNORED_DIRS))

        file_versions = []
        for path in files_changed:
            if path.endswith(".py"):
                before = "" if path in untracked else git_raw(root, "show", f"{base}:{path}")[:FILE_CAP]
                file_versions.append({"path": path, "before": before,
                                      "after": read_file(os.path.join(root, path))})

        body = common.session_header(payload, cfg, agent)
        body.update({
            "last_message": payload.get("last_assistant_message") or payload.get("prompt_response") or "",
            "diff": git_raw(root, "diff", base)[:DIFF_CAP],
            "files_changed": files_changed,
            "file_versions": file_versions,
        })
        common.log(cfg, f"on_stop send session={body['session_id']} files={files_changed}")
        common.post_json(cfg, "/events", body)
    except Exception as e:  # fail open
        common.log(cfg, f"on_stop error: {e!r}")
    finally:
        common.emit_nothing(agent)  # Claude: nothing. Gemini: {}. Never context or decision.


if __name__ == "__main__":
    main()
    sys.exit(0)
