-- Live Context for Agent Teams: Supabase (Postgres) schema
-- Paste the whole file into Supabase > SQL Editor and run it once.
-- Tables follow the project outline, plus: tasks.status / output_summary /
-- completed_at, and events.task_id (in-progress prompt tracking).

-- One row per teammate
create table devs (
  id             bigint generated always as identity primary key,
  name           text not null unique,
  agent          text not null,                 -- 'claude_code' | 'gemini_cli'
  current_task   text,
  files_touching jsonb not null default '[]'::jsonb,
  last_seen      timestamptz not null default now()
);

-- Every prompt a dev types. Stored immediately; output filled in when the turn ends.
create table tasks (
  id             bigint generated always as identity primary key,
  dev_id         bigint not null references devs(id),
  prompt         text not null,
  status         text not null default 'in_progress'
                 check (status in ('in_progress', 'done', 'stale')),
  output_summary text,                          -- NULL until the turn finishes
  created_at     timestamptz not null default now(),
  completed_at   timestamptz                    -- NULL until the turn finishes
);

-- One row per finished agent turn
create table events (
  id             bigint generated always as identity primary key,
  dev_id         bigint not null references devs(id),
  task_id        bigint references tasks(id),   -- the prompt that started this turn
  last_message   text,
  diff           text,
  files_changed  jsonb not null default '[]'::jsonb,
  created_at     timestamptz not null default now()
);

-- What changed, from the AST step (e.g. get_user, return_changed, User -> dict)
create table facts (
  id             bigint generated always as identity primary key,
  event_id       bigint not null references events(id) on delete cascade,
  file           text not null,
  function       text,
  change_type    text not null,
  before         text,
  after          text
);

-- Who calls what (e.g. checkout.py line 42 calls get_user)
create table call_links (
  id              bigint generated always as identity primary key,
  caller_file     text not null,
  caller_line     int,
  callee_function text not null,
  callee_file     text
);

-- What humans posted in #team-context
create table slack_messages (
  id             bigint generated always as identity primary key,
  author         text,
  text           text not null,
  created_at     timestamptz not null default now()
);

-- The inbox. to_dev_id may be NULL: a note addressed to a file is delivered
-- to whoever is about to edit target_file.
create table notes (
  id               bigint generated always as identity primary key,
  event_id         bigint references events(id) on delete cascade,
  slack_message_id bigint references slack_messages(id) on delete cascade,
  from_dev_id      bigint references devs(id),
  to_dev_id        bigint references devs(id),
  source           text not null check (source in ('code_link', 'llm_judge', 'slack')),
  target_file      text,
  text             text not null,
  status           text not null default 'queued' check (status in ('queued', 'delivered')),
  created_at       timestamptz not null default now(),
  delivered_at     timestamptz
);

-- B's agent's next message after getting a note
create table reactions (
  id             bigint generated always as identity primary key,
  note_id        bigint not null references notes(id) on delete cascade,
  agent_message  text,
  created_at     timestamptz not null default now()
);

-- Indexes for the lookups the hooks make on every prompt and edit
create index on tasks (dev_id, status);
create index on events (dev_id, created_at desc);
create index on facts (function);
create index on call_links (callee_function);
create index on call_links (caller_file);
create index on notes (target_file, status);
create index on notes (to_dev_id, status);

-- Live updates for the dashboard (Supabase Realtime)
alter publication supabase_realtime add table devs, tasks, events, facts, notes, reactions;

-- Security: the FastAPI server uses the service_role key, which bypasses RLS.
-- The browser dashboard uses the anon key and can only read.
alter table devs           enable row level security;
alter table tasks          enable row level security;
alter table events         enable row level security;
alter table facts          enable row level security;
alter table call_links     enable row level security;
alter table slack_messages enable row level security;
alter table notes          enable row level security;
alter table reactions      enable row level security;

create policy "dashboard read" on devs           for select to anon using (true);
create policy "dashboard read" on tasks          for select to anon using (true);
create policy "dashboard read" on events         for select to anon using (true);
create policy "dashboard read" on facts          for select to anon using (true);
create policy "dashboard read" on call_links     for select to anon using (true);
create policy "dashboard read" on slack_messages for select to anon using (true);
create policy "dashboard read" on notes          for select to anon using (true);
create policy "dashboard read" on reactions      for select to anon using (true);