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
sync feeding a deterministic morning brief, approval-gated writes (a Calendar
event create and a Gmail draft create—every documented MCP tool now exists),
a real Google OAuth refresh flow, an opt-in local-model text-generation pass,
and `alfred run`, a persistent process that ties all of the above into one
always-on loop.

## Local setup

```powershell
python -m venv .venv
.\.venv\Scripts\python -m pip install -e .[dev]
.\.venv\Scripts\alfred init
.\.venv\Scripts\alfred status
```

Or run the four steps above as one command with `.\scripts\install.ps1`. It is
safe to re-run: it skips venv creation when one already exists, never touches
any provider (no connector credential is read or requested), and every
mutating step honors `-WhatIf` so you can preview it first. Add
`-RegisterScheduledTask` to also register the Task Scheduler entry described
in "Running continuously" below; see `Get-Help .\scripts\install.ps1 -Full`
for every parameter.

The default database path is `.alfred/alfred.db`. It is deliberately ignored by
Git. To use another path, pass `--db <path>` or set `ALFRED_DB_PATH`.

## Local connectors

Telegram polling and delivery are local commands. Put the bot token in your OS
credential manager under service `alfred`, account `telegram-bot-token`, then run
`alfred telegram-poll` with explicitly paired chat/user IDs. Alfred never writes
the token to its database, audit log, Markdown vault, or Git.

Slack is optional and stays local through Socket Mode—no public webhook or
tunnel. `deploy/slack-app/manifest.yml` configures a ready-to-paste Slack app
(OAuth scopes, event subscriptions, Socket Mode); `deploy/slack-app/README.md`
walks through the rest—generating the app-level and bot tokens, storing them
under service `alfred` as `slack-app-token`/`slack-bot-token`, inviting the
bot, and finding your channel/user IDs. Then add `--slack-pair
CHANNEL_ID:USER_ID --slack-channel-id CHANNEL_ID` to `alfred run`. Only
messages from exactly paired user/channel combinations enter Alfred; replies,
reminders, and morning briefs can only be delivered to explicitly allowed
channels. This connector is built the same way every other one in this repo
is—unit-tested against synthetic fixtures—but has not yet been exercised
against a real Slack app.

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

The always-on runner reads every calendar selected in the user's Google Calendar
UI into local source events—title, timing, status, and a source link, never event
descriptions or attendee lists. Each event also keeps its calendar ID/name and
Google's read-only creator/organizer identity when available, so Alfred can
answer where an event came from without copying the guest list. This requires the narrow
`calendar.calendarlist.readonly` scope in addition to event access. A one-shot
`alfred calendar-sync` can still target one calendar explicitly. Calendar also
has Alfred's first real write, and it is preview-then-confirm,
not one step. `alfred calendar-event-propose --actor nico --summary "..." --start
<ISO-8601> --end <ISO-8601>` creates a local preview and never touches Google.
Approve it with `alfred approval-approve --approval-id <ID> --actor nico`, which
prints a one-time token, then `alfred calendar-event-execute --approval-id <ID>
--actor nico --token <TOKEN>` consumes that token and creates the event. A retry
with the same approval ID and token replays the stored receipt instead of
creating a duplicate event. If the PC fails after Google accepts the event but
before Alfred records its receipt, retrying the exact command safely recovers
that event through Alfred's stable Calendar event ID.

Canvas is also read-only and opt-in. If your school permits a personal Canvas
token, save it under service `alfred`, account `canvas-api-token`, then invoke
`alfred canvas-sync --base-url https://your-school.instructure.com`. It copies
upcoming/missing plus accessible current/completed-course assignment history:
title, deadline, course label, source link, and compact submission workflow
state. Grades, submission contents, files, and assignment body text stay out.

If the school disables personal API tokens, use Canvas's private Calendar Feed
instead. Treat that URL like a password and enter it directly into Windows
Credential Manager; never put it in Git, a command argument, a log, or chat:

```powershell
.\.venv\Scripts\alfred.exe canvas-ical-setup
```

The setup command says when it is ready for the URL, hides the pasted text,
repairs an exact accidental double-paste, validates the feed before replacing
the saved credential, and performs the first sync. The native reader
checks the feed every 15 minutes when `alfred run` includes `--canvas-ical`,
uses ETag/Last-Modified conditional requests, and stores only assignment title,
deadline, course label when present, a query-free source link, status, and
versioned evidence. It never stores the feed URL or event description. If the
same item also arrives through a Google Calendar subscription, exact matches
on title and time are shown once in briefs and academic memory, with the native
Canvas evidence preferred. Canvas's
iCal export is less complete than the API: it omits To Do/submission state and
is limited by Canvas to 30 past days, 366 future days, and 1,000 items. Remove
the same Canvas subscription from Google Calendar later if you do not want the
redundant raw calendar evidence; it is no longer required to prevent duplicate
briefing or memory entries.

Google Health is also read-only and opt-in—and, unlike Calendar/Gmail/Canvas/
GitHub above, it has not been exercised against a real wearable-linked account.
It's built the same way every other connector is (same client/sync shape, same
tests against synthetic fixtures) but its exact field names come from reading
Google's v4 REST reference, not from a live response, so treat it as
**unverified** until someone runs it against a real account; `_normalize_data_point`
in `google_health.py` is deliberately defensive about that (it never drops a
data point silently—the complete raw point is always kept in
`metadata["raw"]`, even if its one-line summary guesses a field name wrong).
It reuses the same Google OAuth grant as Calendar/Gmail, but needs additional
scopes those don't request by default: run `alfred google-auth --scope
https://www.googleapis.com/auth/calendar.events --scope
https://www.googleapis.com/auth/gmail.readonly --scope
https://www.googleapis.com/auth/gmail.compose --scope
https://www.googleapis.com/auth/googlehealth.sleep.readonly --scope
https://www.googleapis.com/auth/googlehealth.activity_and_fitness.readonly --scope
https://www.googleapis.com/auth/googlehealth.health_metrics_and_measurements.readonly`
(the first three keep Calendar/Gmail working; all six come from one consent
screen). Then `alfred health-sync` copies steps, sleep, and heart-rate data
points from the last 14 days (`--lookback-days` to change that) as `sensitive`-
tagged events—matching decision 8's data-tagging rule, since health metrics
never inherit `personal`'s default retrieval scope. Health *writes* stay
disabled, per section 8's permissions table; this connector only ever reads.

GitHub is also read-only and opt-in. GitHub's notifications endpoint requires a
classic personal access token with the `notifications` scope; save it under
service `alfred`, account `github-token`, then run `alfred github-sync`. It copies only the unread
notification's title, repository, reason (mention, review requested, etc.),
subject type, and a browser deep link—never issue/PR body text or comments.
Resolved or read notifications drop out of the next sync automatically.

Issue creation is a separate write scope and credential, not part of routine
sync. A fine-grained token needs **Issues: write** on only the repository you
choose; save it as account `github-issue-token` under service `alfred`.
`alfred github-issue-propose --actor nico --repository owner/repo --title "..."`
creates a local preview; after `approval-approve`, `github-issue-execute`
creates that exact issue once. It never creates issues during sync or without
a fresh approval token. Each created issue includes an invisible Alfred recovery
marker: if the PC fails after GitHub accepts it but before Alfred stores its
receipt, retrying finds that exact issue; an absent or ambiguous result fails
closed. PR comments use the same invisible-marker recovery process.

Gmail reuses the same `google-auth` grant as Calendar (the default scopes cover
both). `alfred gmail-sync` reads the unread inbox and copies only each message's
subject, sender, and Gmail's own short snippet—never the message body or
attachments. Reading or archiving a message drops it out of the next sync
automatically. It's bounded to the most recent `--limit` unread messages
(default 500, Gmail's own newest-first ordering) rather than the entire
backlog—an account with a very large unread count would otherwise mean
thousands of individual per-message API calls every sync cycle for mail
that's mostly not this-week actionable anyway. A message can drop out of the
snapshot either because it was read/archived, or simply because it's no
longer within that most-recent window on a later sync—both look the same in
`connector-status`.

Gmail drafts and sending are separate preview-then-confirm writes.
`alfred gmail-draft-propose --actor nico --to recipient@example.com --subject
"..." "body text"` creates a local preview and never touches Gmail. Approve it
with `alfred approval-approve --approval-id <ID> --actor nico`, then `alfred
gmail-draft-execute --approval-id <ID> --actor nico --token <TOKEN>` consumes
that token and creates the draft in Gmail—retrying with the same approval ID
and token replays the receipt instead of creating a duplicate. If Alfred loses
the provider response before recording its receipt, the same command searches
for the draft's stable Message-ID; an ambiguous or absent result fails closed.
Alfred's code never calls a send endpoint; the draft sits in Gmail exactly as if you'd
started typing it yourself. To send, use `gmail-send-propose` with the same
recipient, subject, and body shape, approve it separately, then run
`gmail-send-execute`; Alfred never sends from a sync, scheduled job, or a
proposal alone. Its recovery path uses a stable Message-ID and only accepts an
exact matching sent message; an absent or ambiguous result fails closed.

Alfred can also take commands from your own inbox. `alfred gmail-inbound-poll
--sender you@example.com` reads the same unread inbox and turns a subject line
of `Task: <title>` into a local task, or `Remind: <ISO-8601 time> <title>` into
a task with that due date. Only mail from an explicitly listed `--sender`
address is ever acted on—everything else, including ordinary mail with no
recognized subject, is left untouched. A recognized command from a sender who
is *not* listed is rejected and audited rather than silently run, the same
default-deny rule Telegram pairing uses. Add `--destination telegram:123` (or
any other channel:recipient) to also deliver `Remind:` reminders there; without
it, the task is still created, just without a scheduled delivery. Alfred never
replies by email from this path—sending remains the separate, always
approval-gated `gmail-send-propose`/`gmail-send-execute` flow. Pass one or more
`--gmail-inbound-sender` (and optionally `--gmail-inbound-destination`) flags to
`alfred run` to poll for this continuously.

To queue a daily local morning brief for a paired Telegram chat, use for example
`alfred schedule-brief --chat-id 123 --at 07:30 --timezone America/New_York`.

`alfred connector-status` (or the `connector_status` MCP tool) reports every
connector's health without ever exposing a credential or synced content: `ok`,
`stale` (no success in the last 24 hours), `error` (the most recent attempt
failed), or `never_synced`.

## Encrypted backup and restore

Create the local AES-256 key once with `alfred backup-key-generate`; it is kept
only in Windows Credential Manager as `backup-encryption-key`. Then run `alfred
backup-create --output D:\Backups\alfred.backup` to create an encrypted SQLite
snapshot. Restore is deliberately two-step: `backup-restore-propose --backup
... --actor nico`, approve the preview, then `backup-restore-execute`. The
backup's SHA-256 is frozen in the approval, so a changed file cannot be restored
with a stale confirmation. Since a restore intentionally rolls the database
back to an earlier point, it is non-replayable: any further restore requires a
new preview and approval.

`scripts/backup.ps1` creates timestamped encrypted snapshots under
`.alfred/backups`. The deployed installation runs it through the `Alfred Backup`
scheduled task daily at 02:30; keep testing an isolated restore at least monthly.

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
schedules a Telegram reminder, creating its own task if `--task-id` isn't given. New channels use
`--destination channel:recipient` instead (for example `slack:D123`); the durable job and outbox preserve that target
rather than silently routing it to Telegram. Delivery workers still only exist for Telegram today.
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
.\.venv\Scripts\alfred run --pair 123:456 --chat-id 123 --canvas-ical
```

`alfred run` bounds Gmail to the 50 most recent unread messages
(`--gmail-unread-limit`), lower than the one-shot `gmail-sync` command's 500.
Each sync blocks this single-threaded loop, and against a real account 500
measured at 45 seconds versus 7 for 50, which is dead time where an incoming
Telegram message isn't even polled for. Raise it if you'd rather have the
wider window than the responsiveness.

Calendar, GitHub, and Gmail sync are always attempted and simply skip
themselves if their credential isn't configured yet; Canvas API sync needs
`--canvas-base-url`, native Canvas Calendar Feed sync needs `--canvas-ical`,
inbound Gmail commands need at least one `--gmail-inbound-sender`, and Google
Health needs `--google-health`.
Omit `--pair`/`--chat-id` to run with Telegram disabled. Stop it with Ctrl+C.

`alfred run` also works as a foreground process kept alive by Windows Task
Scheduler ("At log on", running `pythonw.exe` against this same command) or a
terminal left open. `.\scripts\install.ps1 -RegisterScheduledTask -RunArgs
"--pair 123:456 --chat-id 123"` registers that Task Scheduler entry for you;
inspect it with `Get-ScheduledTask -TaskName Alfred` and remove it with
`Unregister-ScheduledTask -TaskName Alfred`. Either way, Alfred only runs
while someone is logged in.

### Conversational replies (`--hermes-profile`)

By default a Telegram message that isn't `/task` or `/remind` gets a short
help receipt. Add `--hermes-profile alfred` and it instead goes to the
agent — Hermes understands it, calls whichever of Alfred's MCP tools it
needs, and its answer comes back in the same chat:

```powershell
.\.venv\Scripts\alfred run --pair 123:456 --chat-id 123 --hermes-profile alfred
```

This needs `hermes-profile/` installed and its MCP connection registered
first (see `hermes-profile/README.md`). Alfred keeps owning the Telegram
transport; Hermes is invoked as a one-shot subprocess (`hermes -p <profile>
-z <message>`) and never touches Telegram itself — deliberately, since
Hermes's own Telegram gateway does not currently work on this platform.

You get a quick acknowledgement first, naming what it's looking at
(`checking your agenda...`, `checking your inbox...`, falling back to
`one sec`), then the answer. That ack is a keyword match rather than a
model call, because it's produced inside the intake write transaction. That's structural, not
cosmetic: Telegram intake runs inside a write transaction, and an agent turn
takes seconds and opens its own connection to this same database, so it has
to happen after that transaction closes. The acknowledgement is flushed
before the agent runs, so it lands while the answer is still being written
rather than arriving alongside it.

The answer itself arrives as two to four consecutive messages rather than one
block — `SOUL.md` asks the agent for short paragraphs and the bridge sends
each as its own message, so it reads like someone texting.

For inbox/GitHub questions, the bridge assembles a bounded context pack from
the already-synced local records before starting Hermes. That avoids a second
MCP discovery/tool-call loop on the cold path. Gmail's Promotions, Social,
and Forums categories are counted but omitted from the pack by default, and
two recent completed chat exchanges are included so a precise follow-up such
as `yes, flag that` keeps its referent. Synced message content remains
untrusted data, and Gmail context is still headers/snippets only, never a full
message body.

### Persistent learning

When conversational replies are enabled, Alfred also runs a local learning
pass after the reply has already been delivered. Explicit `remember that ...`
statements become confirmed memories immediately. Ordinary preferences,
identity facts, and goals enter as quarantined candidates and need the same
fact in a separate source event before promotion. Sensitive candidates never
auto-promote, recognizable secrets are not stored, and every observation
keeps its immutable source-event provenance.

Confirmed memories relevant to a request are placed directly in the bridge's
bounded context pack, avoiding another agent tool round trip. Candidate,
superseded, rejected, deleted, sensitive, and secret memories are excluded
from that automatic path. `memory_correct` preserves the former version while
installing a correction; `memory_feedback` records relevant, irrelevant, or
incorrect retrievals as append-only evaluation data. That feedback now reorders
only the memories already selected for a matching query; it cannot inject an
unrelated popular memory into the candidate set.

Calendar and Canvas history use a separate derived academic layer. Immutable
connector events remain authoritative; after connector sync, Alfred
deduplicates revisions into daily JSON rollups and course/calendar profiles,
classifying exams, quizzes, assignments, and ordinary events while retaining
each source-event ID. Academic questions retrieve only a few matching rollups
(rather than scanning raw history), and rebuilding is skipped when the source
fingerprint has not changed. A second deterministic pass promotes the current
items into source-linked semantic memories, supersedes changed provider
versions, and connects calendar/course entities to the owner graph. This
background work never delays a chat reply.

The continuous runner refreshes a three-year Calendar window weekly by
default without replacing Calendar's live incremental cursor. Canvas keeps
the small upcoming/missing read on the normal connector interval and scans
accessible active/completed course assignments only once daily. Use
`--calendar-history-days 0` to disable Calendar history, or tune
`--calendar-history-interval` and `--canvas-history-interval` when needed.
One-shot maintenance is available through `calendar-history-sync
--all-selected --days 1095` and `academic-memory-rebuild`.

The JSON in rollups is a replaceable cache, not the canonical archive. This
keeps exports portable while preserving edits, cancellations, provenance, and
forget/correction behavior in SQLite. Cognee is therefore not a source of
truth for Alfred today: its graph/vector retrieval ideas are useful, but its
LLM-backed ingestion and additional runtime/database surface are unnecessary
for deterministic Calendar/Canvas facts. It can be evaluated later as an
optional retrieval backend against the same source-linked memory tests.

The agent step runs between intake and delivery, not as a connector.
Connectors sync on a 15-minute interval and run *after* delivery, which
stranded every answer in the outbox for an extra cycle; measured against a
real round trip that was 26s of pure latency on top of the model call.

`--hermes-command` sets which executable to run (default `hermes`; use a
full path when PATH differs, as it can under the Windows service) and
`--hermes-timeout` bounds one turn (default 60s). A turn that times out,
exits non-zero, or produces nothing still gets a reply saying so, and is not
retried — an unanswered `Thinking…` and an endlessly re-run model call are
both worse than one honest failure message.

`--embedding-model nomic-embed-text` enables local hybrid memory recall and
background vector backfill. `--hermes-monthly-call-limit` is a hard external-turn
cap (default 1000). Bridge context is bounded before launch and common PII is
redacted at the final Hermes subprocess boundary. The shipped profile uses Nous
free tier for interactive speed and only local Ollama fallback; it has no paid
provider fallback.

### As a real Windows service (survives logoff/reboot)

`alfred-service` packages the exact same loop as an actual Windows service,
independent of any logged-in session, using Windows' own recovery options for
restart-on-crash instead of an ad-hoc supervisor. It's a thin wrapper, not a
separate code path: it drives the identical construction/cleanup logic
`alfred run` uses, built from arguments parsed by the same parser, so the two
can never quietly diverge in what they actually run.

```powershell
# 1. Store the exact 'alfred run' arguments the service will launch.
.\.venv\Scripts\alfred service-configure run --pair 123:456 --chat-id 123

# 2. Install and start the service (requires an Administrator prompt).
.\.venv\Scripts\alfred-service --username ".\<your-windows-username>" --password "<your-windows-password>" install
.\.venv\Scripts\alfred-service start
```

**The `--username`/`--password` are not optional.** Every connector credential
Alfred reads (`google-oauth-client-secret`, `telegram-bot-token`, ...) lives in
*your* Windows account's DPAPI-protected Credential Manager, which only your
account's own logon session can decrypt. Installing without `--username` (or
via the Services MMC snap-in's default) runs the service as `LocalSystem`,
a completely different, secretless security context—the service will install
and start "successfully," then die immediately with `missing local
credential-store secret: ...`, because `LocalSystem` can never see credentials
`keyring set` stored for you. Options must precede the verb, as shown above;
`getopt` stops parsing at the first non-option argument, so `alfred-service
install --username ...` silently ignores the flags instead of erroring. Note
that the password is visible in your shell history once typed this way; clear
it afterward (`Clear-History` and/or remove the relevant line from
`(Get-PSReadLineOption).HistorySavePath`) if that matters to your setup, or
use the Services MMC snap-in (`services.msc` → Alfred Personal Secretary →
Log On tab) to set the account without it touching a shell at all.

`alfred-service debug` runs the service logic in your current console instead
of under the SCM, printing exceptions directly instead of routing them through
`Get-WinEvent`—much faster than the install/start/inspect-the-event-log loop
when something is still wrong.

Check on it with `Get-Service Alfred`, stop it with `.\.venv\Scripts\alfred-service
stop`, and remove it with `.\.venv\Scripts\alfred-service remove`. Changing the
configured arguments (re-run `service-configure`) needs a `restart` to take
effect. Optionally configure automatic restart on an unexpected crash—Windows
services don't retry by default—with:

```powershell
sc.exe failure Alfred reset= 86400 actions= restart/60000
```

`alfred-service` needs [pywin32](https://github.com/mhammond/pywin32), already
pulled in transitively by the `mcp` package on Windows; if importing it fails,
run `python .\.venv\Scripts\pywin32_postinstall.py -install` once. Installing,
starting, stopping, and removing the service are Administrator actions you run
yourself—Alfred never elevates or registers itself.

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

### Optional mobile sync

Alfred does not use paid Obsidian Sync. `deploy/couchdb/` sets up the
self-hosted CouchDB service section 5 describes—read `deploy/couchdb/README.md`
before running it. Alfred's own code never syncs the vault to a phone
itself; a vetted, open-source Obsidian community plugin (Self-hosted
LiveSync) does that entirely client-side, replicating only `alfred-vault/`,
never `alfred.db`, secrets, or logs. `alfred vault-sync-status --url
http://127.0.0.1:5984` confirms the server side is reachable.

## MCP server

`alfred-mcp` runs Alfred's stdio MCP server for Claude Desktop/Code, Cursor,
and other local MCP clients. Every tool is default-deny: a client gets nothing
until explicitly granted, e.g. `alfred client-grant --client-id local-mcp
--sensitivity public --sensitivity personal --tool memory_search --tool
remember --tool forget --tool calendar_event_propose --tool message_draft
--tool action_commit --tool brief_get --tool connector_status --allow-write`.
`alfred-mcp --client-id <id>` runs it under a different identity (default:
`local-mcp`) so a second stdio client—for example OpenAI's Secure MCP
Tunnel, see below—gets its own separately scoped grant instead of sharing
Claude/Cursor's.
Current tools: `system_status`, `agenda_get`, `memory_search`, `profile_get`,
`remember`, `forget`, `calendar_event_propose`, `message_draft`,
`action_commit`, `brief_get`, `connector_status`, `task_upsert`,
`task_complete`, and `reminder_set`—all 12 of section 7's documented tools are
implemented (`message_draft` creates a Gmail draft only; nothing sends), plus
two not in that list: `system_status` and `calendar_event_propose`, which the
generic `action_commit` needs since section 7 never names a tool for
previewing a calendar write specifically. `remember`/`forget` additionally
check the requested memory's sensitivity against the client's own scope, so a
client granted only `public`/`personal` cannot write or erase a `secret`
memory even with `--allow-write`.

Deleting, calendar writes, drafting a message, and sending a message are all consequential, so
`forget`, `calendar_event_propose`, `message_draft`, and `message_send_propose` only preview—
`action_commit` performs whatever a previous tool call previewed, once given a
fresh approval token. There is deliberately no MCP tool to approve one:
decision 8's "never unattended" (for deletes) and "preview + confirm" (for
calendar writes and messages) would be meaningless if the same automated
client could both propose and approve its own action, so a human (or a
trusted local channel outside the MCP client's own reach, e.g. `alfred
approval-approve`) has to grant it. `action_commit` mints a fresh Google
access token itself when finishing a calendar write, Gmail draft, or Gmail send,
the same way the corresponding CLI commands do; nothing is cached. Gmail sends
remain explicit, one-time approval-gated actions and never run from sync or a
scheduled job.

### Streamable HTTP (remote/private clients)

`alfred-mcp` is stdio-only. For a client that can't spawn a local process—or
that should run as a separate, independently scoped identity from
`local-mcp`—`alfred mcp-http-run --client-id <id>` serves the same tool
surface over Streamable HTTP on `http://127.0.0.1:8000/mcp`. The host is not
configurable: this binds loopback only, matching section 7's "Local server
binds 127.0.0.1 only." `<id>` needs its own `client-grant` first, exactly like
a stdio client.

Every request must carry `Authorization: Bearer <token>`, checked outside
FastMCP's own request handling so an unauthenticated caller can't even open a
session. Generate that token once with `alfred mcp-http-token-generate`
(refuses to overwrite an existing one, same as `backup-key-generate`); it's
stored in the OS credential store, never in a config file. This is a single
shared secret, not OAuth—section 7 reserves OAuth 2.1/RFC 9728 for *public*
remote access, a separate, larger undertaking this does not attempt. FastMCP
also auto-enables DNS-rebinding protection (Host/Origin header validation)
whenever the host is loopback, which is always true here.

### ChatGPT (Secure MCP Tunnel)

ChatGPT can't connect directly to a local MCP server the way Claude
Desktop/Cursor do over stdio, so section 7 names OpenAI's [Secure MCP
Tunnel](https://developers.openai.com/api/docs/guides/secure-mcp-tunnels) as
the private-access path—an outbound-only relay via OpenAI's own
`tunnel-client` daemon, run by you, which Alfred does not vendor or
reimplement. See `deploy/openai-tunnel/README.md` for the full walkthrough:
creating a tunnel and scoped client grant, then pointing `tunnel-client` at
`alfred-mcp --client-id chatgpt-tunnel` over stdio (deliberately not the
Streamable HTTP transport above, to avoid reconciling two separate
authentication schemes). Whether your ChatGPT plan supports custom MCP
connectors at all is between you and OpenAI, not something this repo
controls.

## Admin dashboard

`alfred admin-ui-run` serves a small, read-only web dashboard: today's
agenda, pending approvals, connector health, and the recent audit trail, at
`http://127.0.0.1:8200`. There was no shape spec for this anywhere in
ARCHITECTURE.md—unlike everything else built this session—so it's a
deliberate design choice: one page per concern, no write actions (approving
a pending action still goes through `alfred approval-approve`, never a
button on this page, matching decision 8's "start read-only" precedent for
every connector). No CDN fonts or icons; it works with the network off.

```powershell
.\.venv\Scripts\alfred admin-ui-token-generate
.\.venv\Scripts\alfred admin-ui-run
```

Defaults to loopback-only like `mcp-http-run`, but unlike it, `--host` is a
real option here—this is meant for a person to look at, sometimes from a
phone. `127.0.0.1` is not reachable from another device even over a VPN
(loopback only ever accepts connections that originate on the machine
itself); to check it from your phone, run `alfred admin-ui-run --host
<this-PC's-VPN-IP>` (a Tailscale IP, for example—`tailscale ip -4`), never
`--host 0.0.0.0` unless you already have your own firewall rules
restricting who can reach the port. Works the same in any modern browser:
Edge, Safari, Firefox, Chrome. The layout reflows for a phone-width screen,
and the system font stack renders as Segoe UI on Windows or San Francisco
on Safari/iOS—no CDN fonts fetched either way.

Auth is the same bearer token as `mcp-http-run`, delivered differently:
since a browser can't attach a custom header to a plain navigation,
visiting any page without one redirects to a login screen; entering the
token there sets an `HttpOnly`, `SameSite=Strict` cookie whose value *is*
the token (no separate session store)—exactly as sensitive as the token
itself, and it goes no further than wherever you chose to bind the server.
Scripted/API access can still send `Authorization: Bearer <token>` directly
and skip the cookie entirely.

## Local vector search (optional)

`MemoryGraph` accepts an optional `embedding_provider`. Without one, `memory-search`
stays FTS5 keyword-only, exactly as before. With one—for example
`alfred.embeddings.OllamaEmbeddingProvider`, pointed at a local Ollama—`remember`,
`memory-correct`, and `forget` also keep a versioned vector per memory in the
`embeddings` table, and `memory-search` folds in nearby vector matches (within a
cosine-distance cutoff) once keyword hits are exhausted. Vectors are namespaced by
model name, so trying a different embedding model never mixes incomparable spaces;
switching models means re-embedding, not migrating data. Run
`memory-embed-backfill --model nomic-embed-text` for a one-shot rebuild, or pass
`--embedding-model nomic-embed-text` to `alfred run` for continuous local upkeep.

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
never on the default path.

Decision 6's cloud pieces are now built: `alfred.models.OpenAICompatibleClient`
and `AnthropicCompatibleClient` speak the OpenAI chat-completions and Anthropic
Messages API shapes respectively, but neither should be constructed bare—wrap
either in `GuardedCloudProvider` first, which is what actually satisfies
decision 6's three requirements for a cloud model:

1. **Redaction.** `Redactor` scrubs common secret/PII shapes (emails, bearer
   tokens, OpenAI/GitHub/Slack/AWS key patterns, SSNs, card-like digit runs,
   phone numbers) from the prompt and system text before either reaches the
   cloud provider. It's a best-effort pattern scrub, not a guarantee—keep
   genuinely `secret`-tagged content out of a cloud prompt in the first place
   rather than relying on this to catch it.
2. **Monthly hard cap, fail closed.** `monthly_budget_usd` defaults to `0.0`,
   so an unconfigured `GuardedCloudProvider` never calls out at all. Every
   call checks month-to-date spend (summed from its own audit records)
   *before* calling; once that's already at or past the cap, it raises
   `CloudBudgetExceeded` instead of calling. This checks the cap before each
   call, not a per-call ceiling—one very large call can still push the total
   over the cap within that same run.
3. **Cost tracking.** Every call, success or failure, is audited with the
   model name, prompt/completion token counts, and an estimated USD cost
   (from an operator-supplied `CloudPricing`, since this module hardcodes no
   vendor price table)—never the raw prompt or response text. That audit
   trail doubles as the spend ledger requirement 2 reads from.

As with Ollama, nothing in the CLI, MCP server, or job runner constructs a
cloud provider by default—an operator wires one in from their own
configuration when they want it.

## Development rules

- Read [ARCHITECTURE.md](ARCHITECTURE.md) before changing behavior.
- Keep the database as the source of truth; transports do not contain business logic.
- Do not place credentials, raw personal data, or local databases in Git.
- Every MCP tool is gated by `PolicyStore`; an unregistered or narrowly scoped
  client gets nothing by default. Consequential actions on the MCP surface can
  only create previews; a human must approve them outside that MCP client
  before `action_commit` can execute the exact approved preview.

See [CONTRIBUTING.md](CONTRIBUTING.md) for the full local setup, test, and PR
workflow, [SECURITY.md](SECURITY.md) to report a vulnerability privately, and
[RELEASING.md](RELEASING.md) plus [CHANGELOG.md](CHANGELOG.md) for how a
version gets cut and verified.

## License

Apache-2.0. See [LICENSE](LICENSE).
