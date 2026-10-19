# Coalesce

**Live context for coding agents working in parallel.** When one agent changes a function, every other agent that depends on it finds out right before its next edit, not at the PR.

Winner of Most Innovative at Lehigh's aws hackathon.

## Why

In one of our software engineering classes this semester, we built part of our team project against the schema we'd all agreed on, but the backend didn't follow it. Nobody found out until the PR, after the work was done.

One of us ran into the same problem this past summer during a software engineering internship, this time with coding agents in the loop. She built the AI layer and the frontend while her co-intern built the backend, and everything between them ran through one JSON schema. When one side changed a field, the other side didn't find out until the PR. Because both were developing with agents, changes piled up faster than either of them could track, and every merge meant untangling differences neither had seen coming.

Coding agents make this worse. Teams now run several agents in parallel, each isolated on its own branch, and they can't see each other. One agent changes what `get_user` returns, and another keeps calling it the old way. Git only catches it when the same line is edited twice. Two correct changes that break each other stay invisible until review.

## What it does

When an agent finishes a turn, Coalesce reads the diff and pulls out exactly what changed:

```
get_user: User.get() → dict with keys email, id, name
```

When another agent, on a different branch, is about to edit a file that calls `get_user`, Coalesce puts a short factual note into that agent's context right before the edit:

> Teammate update (Poshitha, claude session on feature/caching, 2 min ago, not merged yet): get_user in services/user.py changed its return value. Before: result of User.get(). Now: dict with keys email, id, name. Your file checkout.py calls get_user on line 40.

The agent then writes `user["email"]` instead of `user.email`. Coalesce doesn't fix code, block edits or run tests. The agent does the thinking, and Coalesce gets it the right fact at the right time.

A live dashboard shows every agent session and the facts captured from each one. When a note is delivered, it animates from one agent's card to the other's.

## How it works

**Hooks** (`hooks/`) run inside Claude Code at three moments:

| Moment | Hook | Server call |
|---|---|---|
| Dev sends a prompt | `on_prompt.py` | `POST /task`: logs the task, returns the team board |
| Agent finishes a turn | `on_stop.py` | `POST /events`: sends the branch diff plus each changed file before and after |
| Agent is about to edit a file | `on_pre_edit.py` | `POST /relevant`: returns any notes for this file |

The hooks use only the Python standard library and fail silently, so a down server never breaks the agent. Set `COALESCE_OFF=1` to turn them all off.

**Server** (`server/`) is a FastAPI app:

- `ast_facts.py` compares each function's signature and return values before and after a change, using Python's `ast` module.
- `relevance.py` checks whether the file being edited calls a changed function, and writes the note text. Notes are always plain facts, never commands.
- `judge.py` runs a Claude Haiku judge in the background for changes that aren't direct calls.
- Each fact reaches each session at most once.

**Database:** Supabase Postgres. The `entries` table has one row per prompt, holding the diff, the files changed and the facts. The `notes` table has one row per fact per recipient, with its delivery status.

**Dashboard** (`dashboard/factory.html`) is a single static HTML file that reads Supabase directly and updates live through Supabase Realtime.

## Running it

**1. Start the server** (Python 3.11+):

```bash
python3 -m venv .venv && .venv/bin/pip install -r server/requirements.txt
.venv/bin/uvicorn server.main:app --host 0.0.0.0 --port 8787
```

Before starting it, create `server/.env` (it's gitignored) containing:

```
DATABASE_URL=postgresql://...        # Supabase connection string
ANTHROPIC_API_KEY=...                # optional
COALESCE_JUDGE_MODEL=claude-haiku-5-5
```

`ANTHROPIC_API_KEY` is only needed for the judge. Code-link notes work without it.

**2. Install the hooks into the repo your agents work in.** Run this once per agent folder:

```bash
python3 hooks/install.py --repo ../my_repo --name Kent --server http://localhost:8787
```

This writes `.coalesce/config.json` and merges the hooks into `.claude/settings.json`. Don't commit `.claude/` or `.coalesce/`, since they contain paths that only exist on your machine.

Add a `CLAUDE.md` to that repo telling the agent to edit files with its Edit or Write tools. Coalesce can't see edits made through shell commands yet.

**3. Open the dashboard:** `open dashboard/factory.html`. Add `?demo=1` to the URL to replay a fake story without any live data.

**4. Run two agents on different branches**, using a git worktree or a second laptop for each, and watch the dashboard. To check the server on its own, run `.venv/bin/python server/tests/simulate_demo.py`, which replays the full story against it.

To wipe all notes and entries before a demo, run `.venv/bin/python scripts/reset_demo.py`.

**Two laptops:** run the server on one laptop and use its local IP in `--server` on the other. Check the connection first with `curl http://SERVER_IP:8787/board`. Campus Wi-Fi often blocks laptop-to-laptop traffic, so a phone hotspot is the reliable fallback.

## Repo layout

```
hooks/        Claude Code (and Gemini CLI) hook scripts + install.py
server/       FastAPI app, AST facts, relevance matching, LLM judge, tests
dashboard/    Live dashboard (single HTML file)
scripts/      reset_demo.py and dev tools
docs/         Diagrams
contract.md   The build contract we used to work in parallel
```

## What's next

- Catch edits made through shell commands, not just the Edit tool.
- Support languages beyond Python.
- Finish support for other agents like Gemini CLI.
- Slack in both directions: humans post a note every agent receives, and deliveries get posted back to the channel.
- Schema contracts that warn an agent when its change breaks an interface the team agreed on, which is exactly what bit us in class.
