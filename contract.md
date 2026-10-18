# Coalesce: Build Contract

Paste this whole file into every agent's first prompt. If something here is wrong or missing, tell your teammate before changing it. Nobody changes this file alone.

Working name: **Coalesce**. Team: **Poshitha** (server) and **Kent** (agent side + dashboard). Due 10 am.

---

## 1. What we're building (read this first)

Teams now run many coding agents in parallel, each isolated in its own branch or worktree. Isolation stops agents from overwriting each other's files, but it also makes them blind to each other. A change in one session silently breaks another session's code, and nobody finds out until the PR.

Coalesce gives every agent session live context about what other sessions are changing, right before it matters.

- When an agent finishes a turn, a hook sends the change to the Coalesce server.
- The server pulls out exactly what changed (Python AST facts) and decides which other sessions it affects.
- When another agent is about to edit a file that uses the changed code, a hook asks the server and injects a short factual note into that agent's context.
- The agent does the thinking. We don't fix code, block edits, or run tests. We get the right info to the right agent at the right time.
- Humans see it all on a live dashboard and in Slack, and can post notes in Slack that every agent receives.

**The unit is an agent session, not a person.** One dev can run 3 sessions. Every session both produces updates and receives them.

Origin story (for the pitch): in our CSE 216 sprint, one teammate built against the agreed schema and the backend didn't follow it. Nobody found out until the PR, after all the work was done.

---

## 2. Ownership

| Folder | Owner | What |
|---|---|---|
| `server/` | Poshitha | FastAPI app, SQLite, AST facts, relevance, LLM judge, Slack out + in, WebSocket |
| `hooks/` | Kent | Hook scripts for Claude Code and Gemini CLI |
| `cli/` | Kent | `coalesce` command: `init`, `server`, `status`, `note` |
| `dashboard/` | Kent | Single page served by the server |
| `scripts/fake_events.py` | Kent | Fake WebSocket feed so the dashboard can be built before the server works |
| `demo_repo/` | Kent | The repo the agents work on during the demo |
| `contract.md` | Both | This file |

Rule: never edit another person's folder. One agent per folder at a time.

---

## 3. Stack

- Python 3.11+
- Server: `fastapi`, `uvicorn`, `anthropic`, `slack_bolt`, `httpx`. SQLite via stdlib `sqlite3`. DB file: `server/coalesce.db`.
- Hooks: **Python stdlib only** (`json`, `sys`, `subprocess`, `urllib.request`). No pip installs, so they run anywhere.
- Dashboard: one `index.html` + `app.js`, vanilla JS, no build step. Served by FastAPI at `GET /`.
- Server port: **8787**. Fake events port: **8788**.

Env vars (server side, in `server/.env`, never committed):

```
ANTHROPIC_API_KEY=
COALESCE_JUDGE_MODEL=claude-haiku-5-5
SLACK_WEBHOOK_URL=
SLACK_BOT_TOKEN=
SLACK_APP_TOKEN=
SLACK_CHANNEL_ID=
```

---

## 4. Repo layout

```
coalesce/
  contract.md
  pyproject.toml              # console script: coalesce = cli.coalesce:main
  server/
    main.py                   # app, endpoints, WS /live, serves dashboard/
    db.py                     # schema + helpers
    ast_facts.py              # facts from before/after file content
    relevance.py              # code-link match + judge orchestration + note text
    judge.py                  # Anthropic call
    slack.py                  # webhook out + Bolt socket-mode in
  hooks/
    common.py                 # config, git helpers, POST with timeout, fail open
    on_prompt.py
    on_stop.py
    on_pre_edit.py
  cli/
    coalesce.py
    templates/
      claude_settings.json
      gemini_settings.json
      claude_command_coalesce.md
      gemini_command_coalesce.toml
  dashboard/
    index.html
    app.js
  scripts/
    fake_events.py
  demo_repo/                  # its own git repo, see section 12
```

---

## 5. Config on each machine

`coalesce init` (run inside the repo the agents work on) writes `.coalesce/config.json` and adds `.coalesce/` to `.gitignore`:

```json
{
  "dev": "Poshitha",
  "server": "http://192.168.1.20:8787",
  "base_branch": "main",
  "coalesce_home": "/Users/poshitha/coalesce"
}
```

Hooks find it by walking up from `cwd`. If missing, hooks do nothing.

**Kill switch:** if env var `COALESCE_OFF=1` is set, every hook exits 0 with no output. Needed for the 5 vs 5 proof runs.

---

## 6. Hooks (Kent)

### 6.1 Rules every hook follows

1. Read JSON from stdin.
2. Never print anything to stdout except the final JSON. No debug prints. Log to `.coalesce/hook.log` instead.
3. **Fail open.** Any exception, timeout, or server down: exit 0. Claude: print nothing. Gemini: print `{}`.
4. HTTP timeout: 3 seconds.
5. Paths sent to the server are **repo-relative** (strip `git rev-parse --show-toplevel`).
6. Branch: `git rev-parse --abbrev-ref HEAD` in `cwd`.
7. Every hook takes `--agent claude` or `--agent gemini` so it knows which input and output shape to use.
8. Injected text is always **facts, not commands**. The server writes it; hooks pass it through untouched.

### 6.2 Which hook fires when

| Moment | Claude Code | Gemini CLI | Script | Server call |
|---|---|---|---|---|
| Dev sends a prompt | `UserPromptSubmit` | `BeforeAgent` | `on_prompt.py` | `POST /task` |
| Agent finishes a turn | `Stop` (async) | `AfterAgent` | `on_stop.py` | `POST /events` |
| Agent about to edit a file | `PreToolUse`, matcher `Edit\|Write\|MultiEdit` | `BeforeTool`, matcher `write_file\|replace` | `on_pre_edit.py` | `POST /relevant` |

### 6.3 Input fields we read

| Field | Claude Code | Gemini CLI |
|---|---|---|
| session id | `session_id` | `session_id` |
| working dir | `cwd` | `cwd` |
| prompt | `prompt` (UserPromptSubmit) | `prompt` (BeforeAgent) |
| agent's last message | `last_assistant_message` (Stop) | `prompt_response` (AfterAgent) |
| tool name | `tool_name` | `tool_name` |
| file being edited | `tool_input.file_path` | `tool_input.file_path` |

**Kent: first thing, log one raw stdin sample from each Gemini hook to `.coalesce/hook.log` and confirm these field names.** Gemini's are the least certain.

### 6.4 What each script does

**on_prompt.py**
1. Build payload, `POST /task`.
2. Response `{"context": "..."}`. If non-empty, output it as additional context (6.5). If empty, output nothing / `{}`.

**on_stop.py**
1. In `cwd`: `base = git merge-base HEAD <base_branch>`.
2. `diff = git diff <base>` (committed + uncommitted changes on this branch). Cap at 100 KB.
3. `files_changed = git diff --name-only <base>`.
4. For each changed `.py` file: `before = git show <base>:<path>` (empty string if new), `after` = current file on disk (empty string if deleted). Cap 50 KB per file.
5. `POST /events`. Ignore the response.
6. Output nothing (Claude) / `{}` (Gemini). **Never return context or `decision` from this hook.** It makes the agent loop.

**on_pre_edit.py**
1. Get file path from `tool_input.file_path`, make repo-relative.
2. Read the file's current content from disk (empty string if it doesn't exist yet). For `Write`, also append `tool_input.content`. For `Edit`, append `tool_input.new_string`. So the server sees what the agent is about to write.
3. `POST /relevant`.
4. Response `{"context": "..."}`. If non-empty, output as additional context. Never set `permissionDecision`.

### 6.5 Output shapes

Claude, prompt hook with context:
```json
{"hookSpecificOutput": {"hookEventName": "UserPromptSubmit", "additionalContext": "<text>"}}
```

Claude, pre-edit hook with context:
```json
{"hookSpecificOutput": {"hookEventName": "PreToolUse", "additionalContext": "<text>"}}
```

Gemini, prompt hook with context:
```json
{"hookSpecificOutput": {"hookEventName": "BeforeAgent", "additionalContext": "<text>"}}
```

Gemini, pre-edit hook with context:
```json
{"hookSpecificOutput": {"hookEventName": "BeforeTool", "additionalContext": "<text>"}}
```

No context: Claude prints nothing, Gemini prints `{}`. Always exit 0.

### 6.6 Agent config files written by `coalesce init`

`.claude/settings.json` (merge into existing, don't overwrite other keys):
```json
{
  "hooks": {
    "UserPromptSubmit": [
      {"hooks": [{"type": "command", "command": "python3 <coalesce_home>/hooks/on_prompt.py --agent claude", "timeout": 5}]}
    ],
    "PreToolUse": [
      {"matcher": "Edit|Write|MultiEdit",
       "hooks": [{"type": "command", "command": "python3 <coalesce_home>/hooks/on_pre_edit.py --agent claude", "timeout": 5}]}
    ],
    "Stop": [
      {"hooks": [{"type": "command", "command": "python3 <coalesce_home>/hooks/on_stop.py --agent claude", "async": true, "timeout": 30}]}
    ]
  }
}
```

`.gemini/settings.json` (Gemini hooks are experimental and must be enabled; Kent: confirm the exact enable flags and whether `timeout` is in ms):
```json
{
  "hooks": {
    "BeforeAgent": [
      {"matcher": "*", "hooks": [{"name": "coalesce-prompt", "type": "command", "command": "python3 <coalesce_home>/hooks/on_prompt.py --agent gemini", "timeout": 5000}]}
    ],
    "BeforeTool": [
      {"matcher": "write_file|replace", "hooks": [{"name": "coalesce-pre-edit", "type": "command", "command": "python3 <coalesce_home>/hooks/on_pre_edit.py --agent gemini", "timeout": 5000}]}
    ],
    "AfterAgent": [
      {"matcher": "*", "hooks": [{"name": "coalesce-stop", "type": "command", "command": "python3 <coalesce_home>/hooks/on_stop.py --agent gemini", "timeout": 10000}]}
    ]
  }
}
```

---

## 7. CLI (Kent)

Installed with `pip install -e .` from `coalesce/`.

| Command | Does |
|---|---|
| `coalesce init --name NAME --server URL [--base main]` | Run inside the demo repo. Writes `.coalesce/config.json`, merges hook configs, writes slash command files, gitignores `.coalesce/` |
| `coalesce server` | `uvicorn server.main:app --host 0.0.0.0 --port 8787` |
| `coalesce status` | `GET /board`, prints the text |
| `coalesce note "text"` | `POST /human_note` with `author` = config `dev` |

Slash commands written by `coalesce init`:

`.claude/commands/coalesce.md`
```
---
description: Coalesce team board, or send a note to every agent
allowed-tools: Bash(coalesce:*)
---
!`coalesce $ARGUMENTS`

Show the output above to the user exactly as it is.
```

`.gemini/commands/coalesce.toml` (Kent: verify syntax)
```toml
description = "Coalesce team board, or send a note to every agent"
prompt = """
!{coalesce {{args}}}

Show the output above to the user exactly as it is.
"""
```

Usage: `/coalesce status`, `/coalesce note "we're switching to dicts for user data"`.

---

## 8. Server API (Poshitha)

All bodies are JSON. All timestamps ISO 8601 UTC. Every hook payload includes the **session header**:

```json
{"session_id": "abc123", "dev": "Poshitha", "agent": "claude", "branch": "feature/caching", "cwd": "/Users/poshitha/demo_repo"}
```

### `POST /task`
Request: session header + `{"prompt": "add caching to user lookups"}`
Server: upsert `sessions` (set `current_task`, `last_seen`), insert `tasks`, broadcast `session_update`.
Response: `{"context": "<team board + any queued notes for this session, or empty>"}`. Queued notes included here are marked delivered (`via: "prompt"`).
**Must respond in under 500 ms. No LLM calls in the request itself.**

After responding, in a background task (Kent's idea, made non-blocking): run the LLM judge with **this session's new prompt** against every active fact from other sessions that this session doesn't already have a note for. Relevant results become queued `llm_judge` notes, delivered on this session's next edit (`/relevant`) or next prompt. This covers sessions that start after a change was made. If the agent edits before the judge finishes, the code-link text search in `/relevant` still catches direct uses.

### `POST /events`
Request: session header +
```json
{
  "last_message": "I added an LRU cache to get_user and changed it to return a dict...",
  "diff": "<git diff text>",
  "files_changed": ["services/user.py"],
  "file_versions": [{"path": "services/user.py", "before": "<old>", "after": "<new>"}]
}
```
Server: respond `{"ok": true}` immediately, then in a background task:
1. Insert `events`, update `sessions.files_json` and `last_seen`, store `after` content in `file_snapshots`.
2. Link reactions: if this session got notes delivered since its previous event, insert `reactions` rows for them with this `last_message`. Broadcast `reaction`.
3. Run `ast_facts` on each `file_versions` entry. Replace this session's facts with the new set (diff is cumulative). Broadcast each **new** fact.
4. Write-time code links: for each other active session, if any new fact's `symbol` appears in that session's `file_snapshots`, queue a `code_link` note.
5. LLM judge: for each other active session that got no code-link note for these facts, ask the judge. If relevant, queue an `llm_judge` note.
6. Broadcast `event` and each `note_queued`.

### `POST /relevant`
Request: session header + `{"file_path": "checkout.py", "content": "<current file + what the agent is about to write>", "tool_name": "Edit"}`
Server (**DB and string matching only, no LLM, under 300 ms**):
1. Update `file_snapshots` for this session + path.
2. Read-time code links: for every active fact from **other** sessions, if `symbol` appears in `content`, ensure a `code_link` note exists for this session (dedupe key, see 9).
3. Collect all `queued` notes for this session, mark `delivered` (`via: "pre_edit"`, `file`), broadcast `note_delivered`, post each to Slack out.
4. Response: `{"context": "<notes joined by blank lines, or empty>"}`.

### `POST /human_note`
Request: `{"author": "Poshitha", "text": "we're switching to dicts for user data", "source": "slack"}` (`source` is `slack` or `cli`)
Server: queue a `human` note for every active session, broadcast `human_note`.
Response: `{"ok": true, "sessions": 3}`

### `GET /board`
Response: `{"text": "<team board text>"}` covering all active sessions.

### `GET /state`
Dashboard initial load. Response:
```json
{"sessions": [ ...session objects... ], "facts": [ ...last 50... ], "notes": [ ...last 50, newest first... ]}
```

### `WS /live`
Server pushes messages from section 11. Clients never send anything.

### `GET /`
Serves `dashboard/index.html` and static files from `dashboard/`.

**Active session** = `last_seen` within the last 60 minutes.

---

## 9. Database (Poshitha)

SQLite, `server/coalesce.db`. Created on startup if missing.

```sql
CREATE TABLE sessions (
  id TEXT PRIMARY KEY,           -- agent's session_id
  dev TEXT NOT NULL,
  agent TEXT NOT NULL,           -- claude | gemini
  branch TEXT,
  cwd TEXT,
  current_task TEXT,
  files_json TEXT DEFAULT '[]',  -- files changed on this branch
  last_seen TEXT NOT NULL
);

CREATE TABLE tasks (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  session_id TEXT NOT NULL REFERENCES sessions(id),
  prompt TEXT NOT NULL,
  created_at TEXT NOT NULL
);

CREATE TABLE events (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  session_id TEXT NOT NULL REFERENCES sessions(id),
  last_message TEXT,
  diff TEXT,
  files_json TEXT DEFAULT '[]',
  created_at TEXT NOT NULL
);

CREATE TABLE facts (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  event_id INTEGER NOT NULL REFERENCES events(id),
  session_id TEXT NOT NULL REFERENCES sessions(id),
  file TEXT NOT NULL,
  symbol TEXT NOT NULL,          -- bare name, e.g. get_user (what we grep for)
  qualname TEXT NOT NULL,        -- e.g. UserService.get_user
  change_type TEXT NOT NULL,     -- added | removed | signature_changed | return_changed
  before TEXT,
  after TEXT,
  fact_key TEXT NOT NULL,        -- file|qualname|change_type|after
  active INTEGER NOT NULL DEFAULT 1,
  created_at TEXT NOT NULL
);

CREATE TABLE file_snapshots (
  session_id TEXT NOT NULL REFERENCES sessions(id),
  path TEXT NOT NULL,
  content TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  PRIMARY KEY (session_id, path)
);

CREATE TABLE notes (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  to_session TEXT NOT NULL REFERENCES sessions(id),
  from_session TEXT REFERENCES sessions(id),  -- NULL for human notes
  author TEXT,                                -- dev name or Slack user
  source TEXT NOT NULL,          -- code_link | llm_judge | human
  fact_id INTEGER REFERENCES facts(id),
  target_file TEXT,
  text TEXT NOT NULL,
  dedupe_key TEXT NOT NULL,      -- code_link/llm_judge: fact_key; human: 'human:'||id of message
  status TEXT NOT NULL DEFAULT 'queued',      -- queued | delivered
  via TEXT,                      -- pre_edit | prompt
  created_at TEXT NOT NULL,
  delivered_at TEXT,
  UNIQUE (to_session, dedupe_key)
);

CREATE TABLE reactions (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  note_id INTEGER NOT NULL REFERENCES notes(id),
  session_id TEXT NOT NULL REFERENCES sessions(id),
  message TEXT NOT NULL,
  created_at TEXT NOT NULL
);
```

Rules:
- When a session's new event replaces its facts, set old facts `active = 0`. Only active facts create notes.
- A session never gets notes about its own facts.
- `UNIQUE (to_session, dedupe_key)` means each fact reaches each session at most once. Use `INSERT OR IGNORE`.

---

## 10. Logic details (Poshitha)

### 10.1 AST facts (`ast_facts.py`)

`extract(path, before, after) -> list[Fact]` for `.py` files only. Skip the file if either side fails to parse.

1. Parse both with `ast.parse`.
2. Collect every top-level function and every method in top-level classes. Key = qualname (`get_user`, `UserService.get_user`).
3. For each function record:
   - `sig`: argument list as source text via `ast.unparse(node.args)` plus return annotation if present.
   - `returns`: sorted set of return shapes from every `return` in the body (not nested functions):
     - `ast.Dict` with constant keys → `dict with keys id, name, email`
     - `ast.Call` → `result of <ast.unparse(func)>()` e.g. `result of User.get()`
     - `ast.List` / `ast.Tuple` → `list` / `tuple`
     - `ast.Constant(None)` or bare `return` → `None`
     - anything else → `ast.unparse(value)` truncated to 60 chars
4. Compare:
   - only in after → `added`, `after` = sig
   - only in before → `removed`, `before` = sig
   - sig differs → `signature_changed`, `before`/`after` = sigs
   - returns differ → `return_changed`, `before`/`after` = returns joined with ` or `
5. `symbol` = last part of qualname.

Expected for the demo:
```json
{"file": "services/user.py", "symbol": "get_user", "qualname": "get_user",
 "change_type": "return_changed",
 "before": "result of User.get()", "after": "dict with keys email, id, name"}
```

### 10.2 Code-link match

`symbol` appears in content as a whole word followed by `(` or `.`: regex `\b<symbol>\s*\(`  OR import `import .*\b<symbol>\b`. Record the first matching line number for the note.

### 10.3 Note text (`relevance.py`)

Facts only. No "you must", no "instruction", no all-caps. Under 600 characters.

Code link:
```
Teammate update (Poshitha, claude session on feature/caching, 4 min ago, not merged yet): get_user in services/user.py changed its return value. Before: result of User.get(). Now: dict with keys email, id, name. Your file checkout.py calls get_user on line 42.
```

LLM judge:
```
Teammate update (Kent, gemini session on feature/orders, 2 min ago, not merged yet): <judge note>
```

Human:
```
Team note from Poshitha (via Slack): we're switching to dicts for user data.
```

Team board (top of every prompt context):
```
Coalesce team board (other active agent sessions):
- Kent, gemini, feature/checkout: "build the checkout flow". Files: checkout.py
- Poshitha, claude, feature/orders: "add order history". Files: orders.py
```

### 10.4 LLM judge (`judge.py`)

Model `COALESCE_JUDGE_MODEL`, `max_tokens` 200, 10 s timeout, runs only in background tasks, never inside a request. It runs at two moments:

| Moment | Judges | Against |
|---|---|---|
| A change arrives (`/events`) | The new facts | Every other active session's task + files |
| A session sends a prompt (`/task`) | That session's new prompt | Every active fact from other sessions |

Same function both times. Dedupe with the `notes` unique key so nobody gets the same fact twice. On any error, return not relevant.

Prompt:
```
You decide if a coding agent needs to know about a teammate's in-progress change.

Teammate's change (session {from_dev}, branch {from_branch}):
Task: {from_task}
Agent's summary: {from_last_message}
Exact changes: {facts as bullet list}

Other agent (session {to_dev}, branch {to_branch}):
Task: {to_task}
Files it is working on: {to_files}
Snippets: {first 40 lines of each file snapshot, max 3 files}

Answer with JSON only: {"relevant": true or false, "note": "<one or two factual sentences about what changed and how it touches the other agent's work, no instructions>"}
Only say relevant if the change could break or should change the other agent's work.
```

### 10.5 Slack (`slack.py`)

**Out:** on every `note_delivered` where source is `code_link` or `llm_judge`, POST to `SLACK_WEBHOOK_URL`:
```
:link: *Poshitha's agent* (feature/caching) changed `get_user`: now returns dict with keys email, id, name.
Told *Kent's agent* (feature/checkout) right before it edited `checkout.py`.
```
Slack failures never break the request. Fire and forget.

**In:** Bolt app in socket mode, started in a background thread on server startup. For every message in `SLACK_CHANNEL_ID` that isn't from a bot, call the same logic as `POST /human_note` with `author` = Slack display name, `source` = `slack`. React to the message with :white_check_mark: so humans see it landed.

---

## 11. WebSocket messages (`WS /live`)

Every message has `type` and `ts`.

```json
{"type": "session_update", "ts": "...", "session_id": "s1", "dev": "Poshitha", "agent": "claude", "branch": "feature/caching", "task": "add caching to user lookups", "files": ["services/user.py"], "last_seen": "..."}
{"type": "event", "ts": "...", "session_id": "s1", "event_id": 12, "last_message": "Added an LRU cache...", "files": ["services/user.py"]}
{"type": "fact", "ts": "...", "session_id": "s1", "fact_id": 30, "file": "services/user.py", "symbol": "get_user", "change_type": "return_changed", "before": "result of User.get()", "after": "dict with keys email, id, name"}
{"type": "note_queued", "ts": "...", "note_id": 7, "from_session": "s1", "to_session": "s3", "author": "Poshitha", "source": "code_link", "text": "Teammate update ..."}
{"type": "note_delivered", "ts": "...", "note_id": 7, "from_session": "s1", "to_session": "s3", "via": "pre_edit", "file": "checkout.py"}
{"type": "reaction", "ts": "...", "note_id": 7, "session_id": "s3", "message": "Updated checkout.py to read user['email'] since get_user now returns a dict."}
{"type": "human_note", "ts": "...", "author": "Poshitha", "text": "we're switching to dicts for user data", "source": "slack", "sessions": 3}
```

---

## 12. Dashboard (Kent)

Loads `GET /state`, then listens on `WS /live`. URL param `?ws=ws://localhost:8788/live` points it at the fake feed.

Must show:
1. **Session cards** in a grid: dev name, agent (claude / gemini badge), branch, current task, files. Pulse when updated.
2. **Live feed**: events, facts (styled as `get_user: User.get() → dict`), notes, human notes. Newest on top.
3. **Delivery animation**: on `note_delivered`, animate a line or dot from the `from_session` card to the `to_session` card, then show the note text on the receiving card. This is the money shot of the demo.
4. **Reaction**: on `reaction`, show the receiving agent's message under the delivered note.
5. Header counters: active sessions, notes delivered, facts captured.

Must be readable on a projector: big text, high contrast, dark background is fine.

`scripts/fake_events.py`: serves `ws://localhost:8788/live` and replays the demo story from section 13 with 2 to 3 second gaps, using exact section 11 shapes, on a loop. Also serves a fake `GET /state` on 8788.

---

## 13. Demo repo and story (Kent builds, both rehearse)

`demo_repo/` is its own git repo, Python, `pytest` runs in under 5 seconds.

```
demo_repo/
  models.py          # class User: id, name, email
  services/user.py   # def get_user(id): return User.get(id)
  checkout.py        # def checkout(user_id, cart): user = get_user(user_id); ... user.email ... (get_user call on line ~42)
  orders.py          # also calls get_user
  tests/test_checkout.py
  tests/test_user.py
```

Branches off `main`: `feature/caching`, `feature/checkout`, `feature/orders`.

Story:
1. Dashboard up. Sessions: Poshitha (claude, `feature/caching`), Kent (gemini or claude, `feature/checkout`), plus 1 to 2 more sessions for scale.
2. Poshitha's agent: "add caching to user lookups". It changes `get_user` to return a dict. Stop hook fires. Dashboard shows the fact.
3. Kent's agent: "build the checkout flow". It goes to edit `checkout.py`. Pre-edit hook fires, note is injected, dashboard animates A → B, Slack posts it.
4. Kent's agent writes `user["email"]` instead of `user.email`. Reaction appears on dashboard.
5. Someone types `/coalesce note "we're switching to dicts for user data"` or posts in Slack. Every card gets it.
6. Proof slide (section 14).

Exact demo prompts get written down and pre-tested. Backup video recorded by 7:30 am.

---

## 14. Proof run (both, after 4:30 am)

1. Reset both branches to the same starting commit.
2. Run Poshitha's prompt on `feature/caching`, then Kent's prompt on `feature/checkout`.
3. Merge both into a scratch branch, run `pytest`. Pass = 1.
4. Do it 5 times with `COALESCE_OFF=1`, 5 times without.
5. Slide: "Without Coalesce: X of 5. With Coalesce: Y of 5."

If agents ignore notes, change the note wording in 10.3 and rerun. Don't change it on stage.

---

## 15. Timeline (it's 9:15 pm)

| Time | Poshitha | Kent |
|---|---|---|
| 9:15 to 9:45 pm | Read contract, set up `server/`, `.env`, Slack app + channel | Read contract, set up `pyproject`, `demo_repo/`, fake events |
| 9:45 pm to 12:30 am | DB, `/task`, `/events`, `/relevant` with code-link match, note text, WS | Claude hooks, `coalesce init`, dashboard on fake feed |
| **12:30 am** | **CHECKPOINT 1: Kent's Claude hook delivers a note made from Poshitha's change. Ugly is fine.** | |
| 12:30 to 3:00 am | AST facts, reactions, Slack out, `/human_note`, `/board` | Dashboard on real WS, delivery animation, slash commands |
| **3:00 am** | **CHECKPOINT 2: full demo story works on Claude, shows on dashboard + Slack.** | |
| 3:00 to 4:30 am | LLM judge, Slack in | Gemini hooks |
| **4:30 am** | **FEATURE FREEZE** | |
| 4:30 to 6:30 am | Proof runs, tune note text | Dashboard polish, demo prompts |
| 6:30 to 7:30 am | Record backup video together | |
| 7:30 to 9:00 am | Deck + rehearse 3 times | |
| 9:00 to 10:00 am | Buffer, submit | |

**Cut order if behind:** Gemini → LLM judge → Slack in → AST facts (fall back to agent's own `last_message` as the note).
**Never cut:** Claude hooks, `/relevant` code-link match, dashboard delivery animation, Slack out.

---

## 16. Not building (do not let agents add these)

- Blocking or denying edits (`permissionDecision`). Stretch idea only: block edits that break a registered schema contract.
- Running tests, sandboxes, auto-merging.
- Auth, accounts, multiple repos.
- Embeddings.
- Postgres, AWS hosting.
- Languages other than Python in the demo repo.
