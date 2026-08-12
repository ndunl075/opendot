# Alfred

Alfred is a local-first, open-source personal secretary. Its core owns memory,
tasks, schedules, audit records, and connector state; chat clients are replaceable
interfaces.

This repository implements ARCHITECTURE.md's build slices through "Daily
secretary" and most of "Memory": SQLite migrations, an append-only audit log,
a CLI, a policy-gated stdio MCP server, a typed temporal memory graph with
optional local vector search and evidence-backed provenance, a two-way
Obsidian-compatible Markdown vault, local Telegram polling and delivery,
durable jobs with missed-run recovery, read-only Calendar/Canvas/GitHub/Gmail
sync feeding a deterministic morning brief, Alfred's first real write (an
approval-gated Calendar event create), a real Google OAuth refresh flow, and
`alfred run`, a persistent process that ties all of the above into one
always-on loop.

## Local setup

```powershell
python -m venv .venv
.\.venv\Scripts\python -m pip install -e .[dev]
.\.venv\Scripts\alfred init
.\.venv\Scripts\alfred status
```

The default database path is `.alfred/alfred.db`. It is deliberately ignored by
Git. To use another path, pass `--db <path>` or set `ALFRED_DB_PATH`.

## Local connectors

Telegram polling and delivery are local commands. Put the bot token in your OS
credential manager under service `alfred`, account `telegram-bot-token`, then run
`alfred telegram-poll` with explicitly paired chat/user IDs. Alfred never writes
the token to its database, audit log, Markdown vault, or Git.

Google Calendar and Gmail share one real OAuth 2.0 flow, not a hand-pasted
access token. First, in Google Cloud Console, create an OAuth client of type
"Desktop app" (no redirect URI to register—Alfred's local loopback flow is
exempt) and save its client ID and secret under service `alfred`, accounts
`google-oauth-client-id` and `google-oauth-client-secret`. Then run `alfred
google-auth`: it opens a browser to Google's consent screen, receives the
redirect on a local port, and stores a long-lived refresh token under
`google-oauth-refresh-token`. Every later sync mints a fresh access token from
that refresh token; none is cached, and no token is ever written to the
database, audit log, Markdown vault, or Git. Re-run `google-auth` any time the
grant is revoked.

`alfred calendar-sync` reads the primary calendar into local source events—
title, timing, status, and a source link, never event descriptions or attendee
lists. Calendar also has Alfred's first real write, and it is preview-then-confirm,
not one step. `alfred calendar-event-propose --actor nico --summary "..." --start
<ISO-8601> --end <ISO-8601>` creates a local preview and never touches Google.
Approve it with `alfred approval-approve --approval-id <ID> --actor nico`, which
prints a one-time token, then `alfred calendar-event-execute --approval-id <ID>
--actor nico --token <TOKEN>` consumes that token and creates the event. A retry
with the same approval ID and token replays the stored receipt instead of
creating a duplicate event.

Canvas is also read-only and opt-in. If your school permits a personal Canvas
token, save it under service `alfred`, account `canvas-api-token`, then invoke
`alfred canvas-sync --base-url https://your-school.instructure.com`. It copies
only upcoming/missing assignment title, deadline, course label, and source link;
grades, submissions, files, and assignment body text stay out of Alfred.

GitHub is also read-only and opt-in. Save a repo-scoped fine-grained personal
access token (notifications: read) under service `alfred`, account
`github-token`, then run `alfred github-sync`. It copies only the unread
notification's title, repository, reason (mention, review requested, etc.),
subject type, and a browser deep link—never issue/PR body text or comments.
Resolved or read notifications drop out of the next sync automatically.

Gmail is also read-only and opt-in, and reuses the same `google-auth` grant as
Calendar (the default scopes cover both). `alfred gmail-sync` copies only the
unread inbox message's subject, sender, and Gmail's own short snippet—never the
message body or attachments. Reading or archiving a message drops it out of the
next sync automatically.

To queue a daily local morning brief for a paired Telegram chat, use for example
`alfred schedule-brief --chat-id 123 --at 07:30 --timezone America/New_York`.

`alfred connector-status` (or the `connector_status` MCP tool) reports every
connector's health without ever exposing a credential or synced content: `ok`,
`stale` (no success in the last 24 hours), `error` (the most recent attempt
failed), or `never_synced`.

## Tasks and reminders

Sending `/task <title>` or `/remind <ISO-8601> <text>` to the paired Telegram bot
creates a task (and, for `/remind`, a scheduled delivery job) linked back to that
message as evidence. The same capability is available directly: `alfred
task-upsert "title" [--task-id ID] [--due-at ISO-8601]` creates a new task, or
updates an existing one's title/due date when `--task-id` is given—omitting
`--due-at` on an update leaves the existing due date alone rather than clearing
it. `alfred task-complete --task-id ID` marks an open task completed and is
idempotent (completing an already-completed task is a no-op, not an error).
`alfred reminder-set "text" --run-at ISO-8601 --chat-id ID [--task-id ID]`
schedules a Telegram reminder, creating its own task if `--task-id` isn't given.
All three are also MCP tools (`task_upsert`, `task_complete`, `reminder_set`);
`reminder_set` needs an explicit `chat_id` because Telegram is Alfred's only
delivery channel today, so there's no channel-agnostic queue to defer that
choice to.

## Running continuously

Every command above is a one-shot CLI invocation; something still has to keep
running them. `alfred run` is that process—the "always-on PC" service decision
3 describes. It loops forever: each cycle handles Telegram intake/delivery and
any due jobs (reminders, the morning brief), and each configured connector
syncs on its own interval (15 minutes by default). A missing credential or a
failed connector is logged to the audit trail and skipped; it never stops the
loop or any other connector.

```powershell
.\.venv\Scripts\alfred run --pair 123:456 --chat-id 123 --canvas-base-url https://your-school.instructure.com
```

Calendar, GitHub, and Gmail sync are always attempted and simply skip
themselves if their credential isn't configured yet; Canvas needs
`--canvas-base-url` to be included at all. Omit `--pair`/`--chat-id` to run
with Telegram disabled. Stop it with Ctrl+C.

`alfred run` is a foreground process, not a Windows service. To keep it running
unattended, use Windows Task Scheduler with a trigger of "At log on", running
`pythonw.exe` against this same command—or start it manually in a terminal you
leave open. Packaging it as an actual service is future work.

## Local memory graph

`alfred remember "statement"` stores a confirmed local memory; `alfred memory-search
"query"` returns FTS anchors plus one active graph hop. Corrections never rewrite
history: `alfred memory-correct --memory-id ID "corrected statement"` marks the old
memory superseded and creates a new one that points back to it.

Deleting is preview-then-confirm, the same as the calendar write, because decision
8 classifies deleting data as strong-confirm and never unattended.
`alfred memory-forget-propose --memory-id ID --actor nico [--reason "..."]` previews
a scoped, single-item deletion without touching anything yet. Approve it with
`alfred approval-approve --approval-id <ID> --actor nico`, then `alfred
memory-forget-execute --approval-id <ID> --actor nico --token <TOKEN>` tombstones
the memory, drops it from search, and records an audit entry—retrying with the same
approval ID and token replays the receipt instead of erroring. A superseded memory
stays visible as history until it is separately forgotten.
`alfred memory-alias --entity-id ID "Alternate Name"` adds a searchable alternate
name for an entity—`memory-search` finds it by either name immediately after.

## Obsidian vault

`alfred vault-export-entity --entity-id ID` and `alfred vault-export-memory
--memory-id ID` (both take `--vault PATH`, default `alfred-vault`) project one
graph record into `Generated/`—plain, portable Markdown with an `alfred_id` and
`managed: true` in frontmatter. A hand-edited file in that path is never
silently overwritten; it's preserved and the projection instead becomes an
`.alfred-conflict-<timestamp>.md` copy for review.

`alfred vault-import --vault PATH` reads the other direction: any user-authored
note anywhere in the vault (not just `Generated/`) becomes a confirmed,
evidence-backed memory, since the owner writing something in their own vault
already counts as an explicit statement. It's a scan you call periodically—via
the CLI, or automatically as a connector when `alfred run` is given `--vault`—
not an OS-level file watcher, so a change is only picked up on the next sync.
Alfred never writes back to an imported file; change detection is tracked
entirely in Alfred's own database by content hash, so editing a note supersedes
its memory (the old version stays visible as history) and deleting a note from
disk does not delete the memory it produced—only `forget` does that. Files
Alfred itself generated (`managed: true`) are never re-imported as testimony.

## MCP server

`alfred-mcp` runs Alfred's stdio MCP server for Claude Desktop/Code, Cursor,
and other local MCP clients. Every tool is default-deny: a client gets nothing
until explicitly granted, e.g. `alfred client-grant --client-id local-mcp
--sensitivity public --sensitivity personal --tool memory_search --tool
remember --tool forget --tool calendar_event_propose --tool action_commit
--tool brief_get --tool connector_status --allow-write`. Current tools:
`system_status`, `agenda_get`, `memory_search`, `profile_get`, `remember`,
`forget`, `calendar_event_propose`, `action_commit`, `brief_get`,
`connector_status`, `task_upsert`, `task_complete`, and `reminder_set`—11 of
section 7's 12 documented tools (only `message_draft`, sending a message, is
still missing), plus two not in that list: `system_status` and
`calendar_event_propose`, which the generic `action_commit` needs since
section 7 never names a tool for previewing a calendar write specifically.
`remember`/`forget` additionally check the requested
memory's sensitivity against the client's own scope, so a client granted only
`public`/`personal` cannot write or erase a `secret` memory even with
`--allow-write`.

Deleting and calendar writes are consequential, so `forget` and
`calendar_event_propose` only preview—`action_commit` performs whatever a
previous tool call previewed, once given a fresh approval token. There is
deliberately no MCP tool to approve one: decision 8's "never unattended" (for
deletes) and "preview + confirm" (for calendar writes) would be meaningless if
the same automated client could both propose and approve its own action, so a
human (or a trusted local channel outside the MCP client's own reach, e.g.
`alfred approval-approve`) has to grant it. `action_commit` mints a fresh
Google access token itself when finishing a calendar write, the same way the
CLI's `calendar-event-execute` already does; nothing is cached. Only sending a
message (`message_draft`) remains off the MCP surface entirely.

## Local vector search (optional)

`MemoryGraph` accepts an optional `embedding_provider`. Without one, `memory-search`
stays FTS5 keyword-only, exactly as before. With one—for example
`alfred.embeddings.OllamaEmbeddingProvider`, pointed at a local Ollama—`remember`,
`memory-correct`, and `forget` also keep a versioned vector per memory in the
`embeddings` table, and `memory-search` folds in nearby vector matches (within a
cosine-distance cutoff) once keyword hits are exhausted. Vectors are namespaced by
model name, so trying a different embedding model never mixes incomparable spaces;
switching models means re-embedding, not migrating data. Nothing in the CLI or MCP
server wires a live provider in yet—that's local configuration, not core behavior.

## Local model inference (optional)

`alfred.models.OllamaClient` is decision 6's local-first text generation: point it
at a running Ollama and it calls the non-streaming `/api/generate` endpoint,
returning the text plus Ollama's own prompt/completion token counts.
`BriefingService` accepts an optional `llm_writer`; without one, `write_brief()` is
just `render()`—the deterministic text, unchanged. With one, per section 9
("gathers data without an LLM ... then asks the local model to write a short
brief"), the model only ever rewrites the deterministic render's wording; every
fact, date, and link it sees comes from that text, never from the model's own
knowledge, and a failed or unreachable model falls back to the deterministic
render rather than costing the user their brief. Every pass—success or
failure—is audited with its token counts. Nothing in the CLI, MCP server, or job
runner wires a live writer in by default, matching the rule that a model call is
never on the default path; a cloud fallback, the monthly spend cap, and
sensitive-data redaction before egress are still unbuilt, since there is no cloud
caller yet for them to guard.

## Development rules

- Read [ARCHITECTURE.md](ARCHITECTURE.md) before changing behavior.
- Keep the database as the source of truth; transports do not contain business logic.
- Do not place credentials, raw personal data, or local databases in Git.
- Every MCP tool is gated by `PolicyStore`; an unregistered or narrowly scoped
  client gets nothing by default. Consequential external actions (calendar
  writes, sending messages) are not on the MCP surface yet—only local, already
  approval-gated writes like `remember`/`forget` are.

## License

Apache-2.0. See [LICENSE](LICENSE).
