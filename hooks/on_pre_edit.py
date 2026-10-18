"""Pre-edit hook: Claude PreToolUse (Edit|Write|MultiEdit) / Gemini BeforeTool (write_file|replace).

Sends the file the agent is about to edit (current content + the new text) to
POST /relevant and injects any notes it returns. Never sets permissionDecision.
Fails open: any problem -> exit 0.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import common  # noqa: E402

EVENT_NAME = {"claude": "PreToolUse", "gemini": "BeforeTool"}
CONTENT_CAP = 200_000


def new_text(tool_input):
    """What the agent is about to write: Write/write_file content, Edit/replace new_string, MultiEdit edits."""
    parts = [tool_input.get("content"), tool_input.get("new_string")]
    parts += [e.get("new_string") for e in tool_input.get("edits") or [] if isinstance(e, dict)]
    return "\n".join(p for p in parts if isinstance(p, str) and p)


def main():
    agent = common.parse_agent(sys.argv)
    cfg = None
    try:
        if os.environ.get("COALESCE_OFF") == "1":
            common.emit_nothing(agent)
            return
        payload = common.read_stdin()
        cfg = common.find_config(payload.get("cwd"))
        tool_input = payload.get("tool_input") or {}
        file_path = tool_input.get("file_path")
        if cfg is None or not file_path:
            common.emit_nothing(agent)
            return

        cwd = payload.get("cwd") or os.getcwd()
        root = common.git(cwd, "rev-parse", "--show-toplevel") or cfg["_root"]
        abs_path = os.path.realpath(file_path if os.path.isabs(file_path) else os.path.join(cwd, file_path))
        rel_path = os.path.relpath(abs_path, os.path.realpath(root))

        try:
            with open(abs_path, encoding="utf-8", errors="replace") as f:
                current = f.read(CONTENT_CAP)
        except OSError:
            current = ""  # new file

        body = common.session_header(payload, cfg, agent)
        body.update({
            "file_path": rel_path,
            "content": (current + "\n" + new_text(tool_input))[:CONTENT_CAP],
            "tool_name": payload.get("tool_name", ""),
        })
        common.log(cfg, f"on_pre_edit send session={body['session_id']} file={rel_path}")

        resp = common.post_json(cfg, "/relevant", body)
        context = (resp or {}).get("context", "")
        if context:
            common.log(cfg, f"on_pre_edit injected {len(context)} chars before {rel_path}")
            common.emit_context(agent, EVENT_NAME[agent], context)
        else:
            common.emit_nothing(agent)
    except Exception as e:  # fail open
        common.log(cfg, f"on_pre_edit error: {e!r}")
        common.emit_nothing(agent)


if __name__ == "__main__":
    main()
    sys.exit(0)
