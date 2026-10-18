"""Prompt hook: Claude UserPromptSubmit / Gemini BeforeAgent.

Sends the dev's prompt to POST /task. If the server returns context
(team board + queued notes), injects it. Fails open: any problem -> exit 0.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import common  # noqa: E402

EVENT_NAME = {"claude": "UserPromptSubmit", "gemini": "BeforeAgent"}


def main():
    agent = common.parse_agent(sys.argv)
    cfg = None
    try:
        if os.environ.get("COALESCE_OFF") == "1":
            common.emit_nothing(agent)
            return
        payload = common.read_stdin()
        cfg = common.find_config(payload.get("cwd"))
        if cfg is None:
            common.emit_nothing(agent)
            return

        body = common.session_header(payload, cfg, agent)
        body["prompt"] = payload.get("prompt", "")
        common.log(cfg, f"on_prompt send session={body['session_id']} prompt={body['prompt'][:80]!r}")

        resp = common.post_json(cfg, "/task", body)
        context = (resp or {}).get("context", "")
        if context:
            common.emit_context(agent, EVENT_NAME[agent], context)
        else:
            common.emit_nothing(agent)
    except Exception as e:  # fail open
        common.log(cfg, f"on_prompt error: {e!r}")
        common.emit_nothing(agent)


if __name__ == "__main__":
    main()
    sys.exit(0)