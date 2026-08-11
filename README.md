# Alfred

Alfred is a local-first, open-source personal secretary. Its core owns memory,
tasks, schedules, audit records, and connector state; chat clients are replaceable
interfaces.

This repository currently contains the walking skeleton: SQLite migrations, an
append-only audit log, a CLI, and a narrow read-only stdio MCP server.

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

Google Calendar is read-only and opt-in. It expects a short-lived access token in
the same credential manager under service `alfred`, account
`google-calendar-access-token`; `alfred calendar-sync` reads the primary calendar
into local source events. The sync stores title, timing, status, and a source
link—never event descriptions or attendee lists. Token refresh/OAuth setup is a
later local feature, so this command does nothing until you deliberately provide
that credential.

Canvas is also read-only and opt-in. If your school permits a personal Canvas
token, save it under service `alfred`, account `canvas-api-token`, then invoke
`alfred canvas-sync --base-url https://your-school.instructure.com`. It copies
only upcoming/missing assignment title, deadline, course label, and source link;
grades, submissions, files, and assignment body text stay out of Alfred.

To queue a daily local morning brief for a paired Telegram chat, use for example
`alfred schedule-brief --chat-id 123 --at 07:30 --timezone America/New_York`.
Run `alfred run-due` and then `alfred telegram-deliver` from the PC's local
scheduler; delivery is still restricted to the explicitly allowed chat IDs.

## Local memory graph

`alfred remember "statement"` stores a confirmed local memory; `alfred memory-search
"query"` returns FTS anchors plus one active graph hop. Corrections never rewrite
history: `alfred memory-correct --memory-id ID "corrected statement"` marks the old
memory superseded and creates a new one that points back to it. `alfred forget
--memory-id ID [--reason "..."]` is scoped, single-item deletion—it tombstones the
memory, drops it from search, and records an audit entry, but a superseded memory
it once replaced stays visible as history until it is separately forgotten.

## Development rules

- Read [ARCHITECTURE.md](ARCHITECTURE.md) before changing behavior.
- Keep the database as the source of truth; transports do not contain business logic.
- Do not place credentials, raw personal data, or local databases in Git.
- Consequential external actions require a later approval layer; this first MCP
  surface is read-only.

## License

Apache-2.0. See [LICENSE](LICENSE).
