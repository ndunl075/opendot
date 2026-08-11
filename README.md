# Alfred

Alfred is a local-first, open-source personal secretary. Its core owns memory,
tasks, schedules, audit records, and connector state; chat clients are replaceable
interfaces.

This repository implements ARCHITECTURE.md's build slices through "Daily
secretary" and most of "Memory": SQLite migrations, an append-only audit log,
a CLI, a narrow read-only stdio MCP server, a typed temporal memory graph with
optional local vector search, an Obsidian-compatible Markdown vault, local
Telegram polling and delivery, durable jobs with missed-run recovery,
read-only Calendar/Canvas/GitHub/Gmail sync feeding a deterministic morning
brief, Alfred's first real write (an approval-gated Calendar event create), a
real Google OAuth refresh flow, and `alfred run`, a persistent process that
ties all of the above into one always-on loop.

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
memory superseded and creates a new one that points back to it. `alfred forget
--memory-id ID [--reason "..."]` is scoped, single-item deletion—it tombstones the
memory, drops it from search, and records an audit entry, but a superseded memory
it once replaced stays visible as history until it is separately forgotten.

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

## Development rules

- Read [ARCHITECTURE.md](ARCHITECTURE.md) before changing behavior.
- Keep the database as the source of truth; transports do not contain business logic.
- Do not place credentials, raw personal data, or local databases in Git.
- Consequential external actions require a later approval layer; this first MCP
  surface is read-only.

## License

Apache-2.0. See [LICENSE](LICENSE).
