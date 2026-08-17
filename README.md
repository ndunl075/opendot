# Alfred

Alfred is a local-first, open-source personal secretary that runs on your PC.
It remembers what you tell it, keeps a typed temporal memory graph with
evidence-backed provenance, briefs you from Calendar, Gmail, Canvas, and
GitHub, and acts on your behalf—creating events, drafting and sending mail,
filing issues—only after you preview and approve. SQLite owns memory, tasks,
schedules, audit records, and connector state. Telegram, Slack, Claude,
Cursor, ChatGPT, and the CLI are replaceable interfaces, not the source of
truth.

Credentials stay in the OS credential store. Tokens never land in the
database, audit log, Markdown vault, or Git. Writes are preview-then-confirm.
Models are optional and off the default path.

**What you get**

- A local SQLite archive with an append-only audit log and encrypted backup
- Typed temporal memory (search, correct, forget) and a two-way Obsidian vault
- Read-only Calendar / Gmail / Canvas / GitHub / Health sync feeding a morning brief
- Approval-gated writes: Calendar events, Gmail drafts and sends, GitHub issues
- Telegram (and optional Slack) as the phone remote; Claude, Cursor, and ChatGPT
  over policy-gated MCP
- `alfred run` or a Windows service that keeps the loop alive

See [ARCHITECTURE.md](ARCHITECTURE.md) for the decisions behind this. Commands
and setup are below.

## Local setup

```powershell
python -m venv .venv
.\.venv\Scripts\python -m pip install -e .[dev]
.\.venv\Scripts\alfred init
.\.venv\Scripts\alfred status
```

Or run those four steps with `.\scripts\install.ps1`. It is safe to re-run: it
skips venv creation when one already exists, never reads or requests a
provider credential, and every mutating step honors `-WhatIf`. Add
`-RegisterScheduledTask` to also register the Task Scheduler entry in
"Running continuously"; `Get-Help .\scripts\install.ps1 -Full` lists every
parameter.

The default database is `.alfred/alfred.db`, which Git ignores. Use another
path with `--db <path>` or `ALFRED_DB_PATH`.

Optional quiet hours hold proactive Telegram/Slack deliveries (reminders,
morning briefs, nags—anything with an outbox `job_id`) inside a local window.
Set `ALFRED_QUIET_HOURS_START` and `ALFRED_QUIET_HOURS_END` to `HH:MM`
(overnight windows like `22:00`–`07:00` are fine) and optionally
`ALFRED_QUIET_HOURS_TIMEZONE` to an IANA name (default `UTC`). Interactive
replies with no `job_id` still deliver. Unset means quiet hours are off.

## Local connectors

Store every token under OS credential-manager service `alfred`. Alfred never
writes credentials to its database, audit log, vault, or Git.

**Approval-gated writes** (Calendar events, Gmail drafts and sends, GitHub
issues, memory forget, backup restore) are always three steps:

1. `*-propose` — local preview; never touches the provider.
2. `alfred approval-approve --approval-id <ID> --actor nico` — prints a
   one-time token.
3. `*-execute --approval-id <ID> --actor nico --token <TOKEN>` — consumes
   that token.

Retrying the same approval ID and token replays the stored receipt instead of
duplicating. If the PC fails after the provider accepts but before Alfred
records the receipt, the same command recovers via a stable provider ID and
fails closed on an absent or ambiguous result. There is no MCP tool to
approve: a human (or a trusted local channel outside the MCP client, e.g.
`alfred approval-approve`) has to grant it.

| Connector | Credential (account) | Read | Write |
|---|---|---|---|
| Telegram | `telegram-bot-token` | paired chats | replies, reminders, briefs |
| Slack | `slack-app-token`, `slack-bot-token` | Socket Mode, paired only | same |
| Google Calendar | OAuth via `google-auth` | selected calendars | approval-gated event create |
| Gmail | same OAuth grant | unread headers/snippets | approval-gated draft and send |
| Canvas API | `canvas-api-token` | assignments | — |
| Canvas iCal | `alfred canvas-ical-setup` | dated assignments | — |
| GitHub | `github-token` / `github-issue-token` | unread notifications | approval-gated issues |
| Google Health | extra OAuth scopes | sleep, activity, metrics | — |

Slack and Google Health are built and unit-tested against synthetic fixtures
but have **not** been exercised against a real Slack app or wearable-linked
account. Treat Health field names as unverified until someone does;
`_normalize_data_point` in `google_health.py` never drops a point silently—the
complete raw point is always kept in `metadata["raw"]`.

`alfred connector-status` (or MCP `connector_status`) reports `ok`, `stale`
(no success in 24 hours), `error`, or `never_synced` without exposing a
credential or synced content.

`alfred connector-capabilities` answers what each connector is *allowed* to
do: who can write, which OAuth scopes are actually requested, which stores
`sensitive` data, and whether it polls, pushes, or is local. Today five can
write (Calendar, Gmail, GitHub, Telegram, Slack) and exactly one stores
`sensitive` data (Google Health, all three scopes read-only). "Can write"
marks the approval boundary—nothing there runs unattended. The same table is
on the admin dashboard and is cross-checked against the source by tests.

### Telegram and Slack

Put the bot token under `telegram-bot-token`, then run `alfred telegram-poll`
with explicitly paired chat/user IDs. Only those pairs enter Alfred.

Slack stays local through Socket Mode—no public webhook or tunnel.
`deploy/slack-app/manifest.yml` is a ready-to-paste Slack app;
`deploy/slack-app/README.md` covers generating tokens, storing them as
`slack-app-token`/`slack-bot-token`, inviting the bot, and finding IDs. Then
add `--slack-pair CHANNEL_ID:USER_ID --slack-channel-id CHANNEL_ID` to
`alfred run`. Only paired user/channel combinations enter Alfred; replies,
reminders, and briefs can only go to explicitly allowed channels.

### Google OAuth (Calendar, Gmail, Health)

Calendar and Gmail share one OAuth 2.0 flow. In Google Cloud Console, create
an OAuth client of type "Desktop app" (no redirect URI—Alfred's local
loopback flow is exempt) and save its client ID and secret as
`google-oauth-client-id` and `google-oauth-client-secret`. `alfred google-auth`
opens a browser, receives the redirect on a local port, and stores a refresh
token as `google-oauth-refresh-token`. Later syncs mint a fresh access token
from that refresh token; none is cached. Re-run `google-auth` if the grant is
revoked.

The always-on runner reads every calendar selected in the Google Calendar UI:
title, timing, status, and a source link—never event descriptions or attendee
lists. Each event also keeps its calendar ID/name and Google's read-only
creator/organizer identity when available, so Alfred can say where an event
came from without copying the guest list. That needs the narrow
`calendar.calendarlist.readonly` scope in addition to event access. A one-shot
`alfred calendar-sync` can still target one calendar explicitly.

Calendar writes: `alfred calendar-event-propose --actor nico --summary "..."
--start <ISO-8601> --end <ISO-8601>`, then approve, then
`alfred calendar-event-execute`. Recovery uses Alfred's stable Calendar event
ID.

Gmail reuses the same grant. `alfred gmail-sync` copies each unread message's
subject, sender, and Gmail's short snippet—never the body or attachments.
Reading or archiving a message drops it from the next sync. Sync is bounded
to the most recent `--limit` unread messages (default 500, Gmail's
newest-first order) rather than the whole backlog. A message can drop out
because it was read/archived *or* because it fell outside that window—both
look the same in `connector-status`.

Gmail drafts and sending are separate approval-gated writes:
`gmail-draft-propose` / `gmail-draft-execute` creates a draft and never calls
a send endpoint. To send, use `gmail-send-propose` with the same recipient,
subject, and body, approve separately, then `gmail-send-execute`. Alfred never
sends from a sync, scheduled job, or a proposal alone. Recovery uses a stable
Message-ID and only accepts an exact match.

Alfred can also take commands from your inbox. `alfred gmail-inbound-poll
--sender you@example.com` turns a subject of `Task: <title>` into a local
task, or `Remind: <ISO-8601 time> <title>` into a task with that due date.
Only mail from an explicitly listed `--sender` is acted on; ordinary mail is
left untouched. A recognized command from a sender who is *not* listed is
rejected and audited (the same default-deny rule Telegram pairing uses). Add
`--destination telegram:123` to also deliver `Remind:` reminders there.
Alfred never replies by email from this path. Pass `--gmail-inbound-sender`
(and optionally `--gmail-inbound-destination`) to `alfred run` to poll
continuously.

Google Health reuses the same grant but needs extra scopes Calendar/Gmail do
not request by default:

```text
alfred google-auth
  --scope https://www.googleapis.com/auth/calendar.events
  --scope https://www.googleapis.com/auth/gmail.readonly
  --scope https://www.googleapis.com/auth/gmail.compose
  --scope https://www.googleapis.com/auth/googlehealth.sleep.readonly
  --scope https://www.googleapis.com/auth/googlehealth.activity_and_fitness.readonly
  --scope https://www.googleapis.com/auth/googlehealth.health_metrics_and_measurements.readonly
```

The first three keep Calendar/Gmail working; all six come from one consent
screen. Then `alfred health-sync` copies steps, sleep, and heart-rate points
from the last 14 days (`--lookback-days` to change that) as `sensitive`-tagged
events—health metrics never inherit `personal`'s default retrieval scope.
Health writes stay disabled.

### Canvas

If your school permits a personal Canvas token, save it as `canvas-api-token`
and run `alfred canvas-sync --base-url https://your-school.instructure.com`.
It copies upcoming/missing plus accessible current/completed-course assignment
history: title, deadline, course label, source link, and compact submission
workflow state. Grades, submission contents, files, and assignment body text
stay out.

If the school disables personal API tokens, use Canvas's private Calendar
Feed. Treat that URL like a password and enter it in Windows Credential
Manager; never put it in Git, a command argument, a log, or chat:

```powershell
.\.venv\Scripts\alfred.exe canvas-ical-setup
```

The setup command says when it is ready for the URL, hides the pasted text,
repairs an exact accidental double-paste, validates the feed before replacing
the saved credential, and performs the first sync. With `--canvas-ical`,
`alfred run` checks the feed every 15 minutes, uses ETag/Last-Modified
conditional requests, and stores only assignment title, deadline, course
label when present, a query-free source link, status, and versioned evidence.
It never stores the feed URL or event description.

If the same item also arrives through a Google Calendar subscription, exact
matches on title and time are shown once in briefs and academic memory, with
native Canvas evidence preferred. Canvas's iCal export is less complete than
the API: it omits To Do/submission state and is limited by Canvas to 30 past
days, 366 future days, and 1,000 items. Remove the same Canvas subscription
from Google Calendar later if you do not want the redundant raw calendar
evidence; it is no longer required to prevent duplicate briefing or memory
entries.

### GitHub

GitHub's notifications endpoint needs a classic personal access token with
the `notifications` scope; save it as `github-token`, then run
`alfred github-sync`. It copies only each unread notification's title,
repository, reason (mention, review requested, etc.), subject type, and a
browser deep link—never issue/PR body text or comments. Resolved or read
notifications drop out of the next sync automatically.

Issue creation is a separate write scope. A fine-grained token needs
**Issues: write** on only the repository you choose; save it as
`github-issue-token`. `alfred github-issue-propose --actor nico --repository
owner/repo --title "..."` creates a local preview; after approve,
`github-issue-execute` creates that exact issue once. It never creates issues
during sync or without a fresh approval token. Each created issue includes an
invisible Alfred recovery marker so a crash between GitHub accepting it and
Alfred storing the receipt can still find that exact issue. PR comments use
the same recovery process.

### Morning brief

Queue a daily local morning brief for a paired Telegram chat with, for
example, `alfred schedule-brief --chat-id 123 --at 07:30 --timezone
America/New_York`.

## Encrypted backup and restore

Create the local AES-256 key once with `alfred backup-key-generate`; it is
kept only in Windows Credential Manager as `backup-encryption-key`. Then
`alfred backup-create --output D:\Backups\alfred.backup` writes an encrypted
SQLite snapshot. Restore is two-step: `backup-restore-propose --backup ...
--actor nico`, approve the preview, then `backup-restore-execute`. The
backup's SHA-256 is frozen in the approval, so a changed file cannot be
restored with a stale confirmation. Restore is non-replayable: any further
restore needs a new preview and approval.

`scripts/backup.ps1` creates timestamped encrypted snapshots under
`.alfred/backups`. The deployed installation runs it through the `Alfred
Backup` scheduled task daily at 02:30.

Test those snapshots with `alfred backup-verify --latest-in .alfred\backups`
(or `--backup <file>`). It rehearses a full restore into a throwaway copy and
**never touches your live database**. The only other way to test a restore is
`backup-restore-execute`, which overwrites the database you are trying to
protect.

It checks that the stored key still decrypts, SQLite integrity passes,
migrations apply, the audit hash chain verifies, and the data is really
there:

```json
{"ok": true, "schema_version": 15, "audit_chain_verified": true,
 "row_counts": {"events": 3402, "tool_runs": 5369, "memories": 2592}}
```

A backup that decrypts into an *empty* database would pass every check except
that last one. Broken backups are reported rather than raised—so this is safe
to schedule—and the command exits non-zero on failure.

`scripts/verify-backup.ps1` is the scheduled wrapper. The deployed
installation runs it as `Alfred Backup Verify` every four weeks at 03:15—45
minutes after the nightly snapshot. Each run appends a JSON line to
`.alfred\backup-verify.log`, and a failure throws so Task Scheduler records a
failed run. Register it with:

```powershell
$ps = (Get-Command powershell.exe).Source
$action = New-ScheduledTaskAction -Execute $ps `
  -Argument '-NoProfile -ExecutionPolicy Bypass -File "C:\path\to\alfred\scripts\verify-backup.ps1"'
$trigger = New-ScheduledTaskTrigger -Weekly -WeeksInterval 4 -DaysOfWeek Sunday -At 3:15AM
Register-ScheduledTask -TaskName "Alfred Backup Verify" -Action $action -Trigger $trigger -Force
```

## Tasks and reminders

Sending `/task <title>` or `/remind <ISO-8601> <text>` to the paired Telegram
bot creates a task (and, for `/remind`, a scheduled delivery job) linked back
to that message as evidence. The same commands exist on the CLI:

- `alfred task-upsert "title" [--task-id ID] [--due-at ISO-8601]` — create, or
  update title/due date when `--task-id` is given. Omitting `--due-at` on an
  update leaves the existing due date alone rather than clearing it.
- `alfred task-complete --task-id ID` — idempotent; completing an
  already-completed task is a no-op.
- `alfred reminder-set "text" --run-at ISO-8601 --chat-id ID [--task-id ID]` —
  schedules a Telegram reminder, creating its own task if `--task-id` isn't
  given. New channels use `--destination channel:recipient` (for example
  `slack:D123`); the durable job and outbox preserve that target rather than
  silently routing it to Telegram. Delivery workers still only exist for
  Telegram today.

All three are also MCP tools (`task_upsert`, `task_complete`, `reminder_set`).
`reminder_set` needs an explicit `chat_id` because Telegram is Alfred's only
delivery channel today.

## Running continuously

Every command above is a one-shot CLI invocation. `alfred run` is the
always-on process: each cycle handles Telegram intake/delivery and any due
jobs (reminders, the morning brief), and each configured connector syncs on
its own interval (15 minutes by default). A missing credential or a failed
connector is logged to the audit trail and skipped; it never stops the loop
or any other connector.

```powershell
.\.venv\Scripts\alfred run --pair 123:456 --chat-id 123 --canvas-ical
```

`alfred run` bounds Gmail to the 50 most recent unread messages
(`--gmail-unread-limit`), lower than one-shot `gmail-sync`'s 500. Each sync
blocks this single-threaded loop; against a real account 500 measured at 45
seconds versus 7 for 50, which is dead time where an incoming Telegram
message isn't even polled. Raise it if you'd rather have the wider window
than the responsiveness.

Calendar, GitHub, and Gmail sync are always attempted and skip themselves if
their credential isn't configured yet. Canvas API sync needs
`--canvas-base-url`, native Canvas Calendar Feed sync needs `--canvas-ical`,
inbound Gmail commands need at least one `--gmail-inbound-sender`, and Google
Health needs `--google-health`. Omit `--pair`/`--chat-id` to run with
Telegram disabled. Stop it with Ctrl+C.

`alfred run` also works as a foreground process kept alive by Windows Task
Scheduler ("At log on", running `pythonw.exe` against this same command) or a
terminal left open. `.\scripts\install.ps1 -RegisterScheduledTask -RunArgs
"--pair 123:456 --chat-id 123"` registers that entry; inspect it with
`Get-ScheduledTask -TaskName Alfred` and remove it with
`Unregister-ScheduledTask -TaskName Alfred`. Either way, Alfred only runs
while someone is logged in.

### Conversational replies (`--hermes-profile`)

By default a Telegram message that isn't `/task` or `/remind` gets a short
help receipt. Add `--hermes-profile alfred` and it goes to the agent: Hermes
understands it, calls Alfred's MCP tools, and answers in the same chat.

```powershell
.\.venv\Scripts\alfred run --pair 123:456 --chat-id 123 --hermes-profile alfred
```

Install `hermes-profile/` and register its MCP connection first (see
`hermes-profile/README.md`). Alfred owns the Telegram transport; Hermes is a
one-shot subprocess (`hermes -p <profile> -z <message>`) and never touches
Telegram itself—Hermes's own Telegram gateway does not currently work on this
platform. Production stays on that bounded, redacted one-shot runner: Hermes
ACP/`serve` fix the tool surface at session start, while Alfred narrows MCP
tools per turn, and a fresh zero-tool ACP session exceeded 30 seconds before
a prompt ran.

- Work turns get a keyword acknowledgement first (`checking your agenda...`,
  `checking your inbox...`); casual turns skip it. Acknowledgements are not
  model calls—they're produced inside the intake write transaction, and the
  agent turn has to wait until that transaction closes.
- Telegram `typing...` is best effort and never blocks the durable answer.
- Answers arrive as two to four consecutive messages (`SOUL.md` asks for
  short paragraphs; the bridge sends each as its own message).
- Inbox/GitHub questions get a bounded context pack from already-synced local
  records before Hermes starts. Promotions, Social, and Forums are counted
  but omitted by default. Gmail context is still headers/snippets only.
  Synced message content remains untrusted data.
- Work turns include two recent completed exchanges so `yes, flag that` keeps
  its referent. Casual turns use up to eight exchanges from the last week,
  `poolside/laguna-xs-2.1:free` with reasoning disabled, FTS-only memory, and
  an empty MCP tool surface (no Ollama embedding wait on greetings).
  Tool-backed and explicit memory questions keep hybrid vector recall and
  `stepfun/step-3.7-flash:free`. Override the casual model with
  `--hermes-conversation-model`.
- Every Hermes turn independently allowlists at most eight MCP tools.
  `alfred-mcp` registers only that list; an inbox/GitHub read already in the
  pack exposes no tools. This does not narrow Claude, Cursor, HTTP, or other
  MCP clients.
- `--hermes-command` (default `hermes`; full path when PATH differs under the
  Windows service), `--hermes-timeout` (default 120s; timeout/nonzero/empty
  still gets one honest reply, never retried), `--hermes-python` to invoke
  `-m hermes_cli.main` instead of the console launcher, `--embedding-model
  nomic-embed-text` for hybrid recall, `--hermes-monthly-call-limit` (hard
  cap, default 1000). On Windows, children use `CREATE_NO_WINDOW`.
- Bridge context is bounded before launch; common PII is redacted at the
  subprocess boundary. The shipped profile uses Nous free tier plus local
  Ollama fallback; no paid provider fallback.

`alfred latency-status --limit 20` reports content-free p50/p95 timing
(acknowledgement, context assembly, Hermes call, response-ready, first
delivered reply). Samples contain only an update ID, runtime/tool count,
outcome, and timings. Telegram timestamps have one-second resolution, so
treat end-to-end totals as operator-facing, not a microbenchmark.

`alfred evaluation-status --window-days 30` summarizes response feedback,
memory retrieval outcomes, workflow proposal decisions, and
implicit-candidate promotion—plus which context sources were present when
each vote landed (a starting point for "why was that answer wrong", not
proof of cause). Same page in the admin UI. Nothing here runs a model,
writes a row, or changes ranking. Output is content-free (outcomes, counts,
source names, opaque IDs), safe to paste into an issue. A metric with no
votes yet reports `null`, not `0`.

The agent step runs between intake and delivery, not as a connector. When a
chat transport is configured, periodic connectors run sequentially on one
bounded background worker, so a long Calendar/Gmail/Canvas batch cannot stop
Telegram from polling. Telegram's server long-poll is ten seconds by default;
the HTTP read budget is only two seconds longer, and a failed poll retries
after one second.

### Persistent learning

After a conversational reply is delivered, Alfred runs a local learning pass:

- Explicit `remember that ...` becomes a confirmed memory immediately.
- Preferences, identity facts, and goals enter as quarantined candidates and
  need the same fact in a separate source event before promotion.
- Sensitive candidates never auto-promote; recognizable secrets are not
  stored; every observation keeps immutable source-event provenance.
- Confirmed memories relevant to a request go in the context pack. Candidate,
  superseded, rejected, deleted, sensitive, and secret memories do not.
- `memory_correct` preserves the former version. `memory_feedback` records
  relevant/irrelevant/incorrect retrievals as append-only evaluation data
  and reorders only memories already selected for a matching query.

Every successful Telegram answer ends with `helpful`, `missing context`, and
`wrong context` buttons. A tap records one vote plus a content-free trace of
source names, connector freshness, and opaque ranked IDs—neither prompt nor
answer. Helpful/wrong can reorder Gmail or GitHub records only within their
existing deterministic priority tier; `missing context` is an evaluation
signal, not a guess at what was absent. Callbacks are paired to the original
sender, accept one vote per response, and never approve or execute an action.

Calendar and Canvas history use a derived academic layer. Immutable connector
events remain authoritative. After sync, Alfred deduplicates revisions into
daily JSON rollups and course/calendar profiles (exams, quizzes, assignments,
ordinary events), retaining each source-event ID. Academic questions retrieve
a few matching rollups rather than scanning raw history; rebuilding skips
when the source fingerprint is unchanged. A second pass promotes current
items into source-linked semantic memories, supersedes changed provider
versions, and connects calendar/course entities to the owner graph. This
never delays a chat reply.

The runner refreshes a three-year Calendar window weekly by default without
replacing Calendar's live incremental cursor. Canvas keeps upcoming/missing
on the normal interval and scans accessible course assignments once daily.
`--calendar-history-days 0` disables Calendar history;
`--calendar-history-interval` and `--canvas-history-interval` tune it.
One-shot: `calendar-history-sync --all-selected --days 1095` and
`academic-memory-rebuild`. Rollup JSON is a replaceable cache, not the
canonical archive—edits, cancellations, provenance, and forget/correction
stay in SQLite. Cognee is not a source of truth; it can be evaluated later
as an optional retrieval backend against the same source-linked memory tests.

### As a real Windows service (survives logoff/reboot)

`alfred-service` packages the exact same loop as a Windows service,
independent of any logged-in session, using Windows' own recovery options for
restart-on-crash. It's a thin wrapper, not a separate code path: it drives
the identical construction/cleanup logic `alfred run` uses, built from
arguments parsed by the same parser.

```powershell
# 1. Store the exact 'alfred run' arguments the service will launch.
.\.venv\Scripts\alfred service-configure run --pair 123:456 --chat-id 123

# 2. Install and start the service (requires an Administrator prompt).
.\.venv\Scripts\alfred-service --username ".\<your-windows-username>" --password "<your-windows-password>" install
.\.venv\Scripts\alfred-service start
```

**The `--username`/`--password` are not optional.** Every connector
credential lives in *your* Windows account's DPAPI-protected Credential
Manager, which only your account's own logon session can decrypt. Installing
without `--username` (or via the Services MMC snap-in's default) runs as
`LocalSystem`—the service will install and start "successfully," then die
immediately with `missing local credential-store secret: ...`. Options must
precede the verb; `getopt` stops parsing at the first non-option argument, so
`alfred-service install --username ...` silently ignores the flags. The
password is visible in shell history once typed this way; clear it afterward
(`Clear-History` and/or remove the relevant line from
`(Get-PSReadLineOption).HistorySavePath`), or use the Services MMC snap-in
(`services.msc` → Alfred Personal Secretary → Log On tab) to set the account
without it touching a shell.

`alfred-service debug` runs the service logic in your current console instead
of under the SCM, printing exceptions directly instead of routing them
through `Get-WinEvent`.

Check on it with `Get-Service Alfred`, stop it with
`.\.venv\Scripts\alfred-service stop`, and remove it with
`.\.venv\Scripts\alfred-service remove`. Changing configured arguments
(re-run `service-configure`) needs a `restart` to take effect. Optionally
configure automatic restart on an unexpected crash—Windows services don't
retry by default—with:

```powershell
sc.exe failure Alfred reset= 86400 actions= restart/60000
```

`alfred-service` needs [pywin32](https://github.com/mhammond/pywin32), already
pulled in transitively by the `mcp` package on Windows; if importing it
fails, run `python .\.venv\Scripts\pywin32_postinstall.py -install` once.
Installing, starting, stopping, and removing the service are Administrator
actions you run yourself—Alfred never elevates or registers itself.

## Local memory graph

`alfred remember "statement"` stores a confirmed local memory;
`alfred memory-search "query"` returns FTS anchors plus one active graph hop.
Corrections never rewrite history: `alfred memory-correct --memory-id ID
"corrected statement"` marks the old memory superseded and creates a new one
that points back to it.

Deleting is preview-then-confirm, because deleting data is strong-confirm and
never unattended. `alfred memory-forget-propose --memory-id ID --actor nico
[--reason "..."]` previews a scoped, single-item deletion. Approve it, then
`alfred memory-forget-execute` tombstones the memory, drops it from search,
and records an audit entry. A superseded memory stays visible as history
until it is separately forgotten.

`alfred memory-alias --entity-id ID "Alternate Name"` adds a searchable
alternate name—`memory-search` finds it by either name immediately after.

`alfred memory-rename --entity-id ID "Real Name"` changes what an entity is
actually called. People discovered from your calendar arrive labelled with
whatever Google supplied, which is sometimes just an email address; this is
how you fix that. The old label is kept as an alias, so existing
`[[wiki links]]` keep working—renaming is not forgetting.

Alfred fills names in on its own where it can: if someone's calendar address
also appears as a Gmail sender with a display name, the next `people` sync
adopts it. Gmail is only ever read *for names*, never to decide that someone
exists—your inbox is mostly brands, and a display name there is branding
rather than identity.

## Obsidian vault

`alfred vault-export-entity --entity-id ID` and `alfred vault-export-memory
--memory-id ID` (both take `--vault PATH`, default `alfred-vault`) project one
graph record into `Generated/`—plain, portable Markdown with an `alfred_id`
and `managed: true` in frontmatter. A hand-edited file in that path is never
silently overwritten; it's preserved and the projection instead becomes an
`.alfred-conflict-<timestamp>.md` copy for review.

Bulk exports select a set rather than one record:

```powershell
.\.venv\Scripts\alfred vault-export-source-event --source-event-id ID
.\.venv\Scripts\alfred vault-export-range --since 2026-03-01 --until 2026-04-01
.\.venv\Scripts\alfred vault-export-topic "rowing" --limit 50
.\.venv\Scripts\alfred vault-export-person --entity-id ID
```

`vault-export-person` defines "about a person" **structurally**: a memory is
about someone when it came from an event they organized, not when their name
appears in the wording. Text matching would treat "lunch near Alicia's
office" as a memory *about* Alicia—tolerable for an export, wrong for a
deletion, and this selector serves both. Find the entity ID with
`alfred memory-search "<name>"`.

`--since`/`--until` filter on when Alfred *recorded* a memory, not when the
fact became true. The range is half-open (`--since` inclusive, `--until`
exclusive) so back-to-back months don't both claim a memory on the boundary.
Either bound can be omitted for an open-ended range, but not both. The topic
selector runs the same search a question would. Every bulk export writes only
confirmed, `public`/`personal` memories; anything skipped is reported by ID
rather than silently dropped. A topic export's receipt records that it *was*
a topic export, never the query you typed.

`alfred vault-import --vault PATH` reads the other direction: any
user-authored note anywhere in the vault (not just `Generated/`) becomes a
confirmed, evidence-backed memory. It's a scan you call periodically—via the
CLI, or automatically as a connector when `alfred run` is given `--vault`—not
an OS-level file watcher. Alfred never writes back to an imported file;
change detection is tracked in Alfred's database by content hash, so editing
a note supersedes its memory and deleting a note from disk does not delete
the memory it produced—only `forget` does that. Files Alfred itself generated
(`managed: true`) are never re-imported as testimony.

Import also reads `[[wiki links]]` (including `[[Note|display]]` and
`[[Note#Heading]]`). When a link names exactly one entity Alfred already
knows—by label or alias, case-insensitively—it's recorded as evidence that
this note concerns that entity. An unknown name creates no entity, an
ambiguous name resolves to nothing rather than guessing between two "Alex"
entities, and no relationship edge is created. The result reports `linked`
and `unresolved_links`.

### Optional mobile sync

Alfred does not use paid Obsidian Sync. `deploy/couchdb/` sets up the
self-hosted CouchDB service; read `deploy/couchdb/README.md` before running
it. Alfred's own code never syncs the vault to a phone; a vetted, open-source
Obsidian community plugin (Self-hosted LiveSync) does that entirely
client-side, replicating only `alfred-vault/`, never `alfred.db`, secrets, or
logs. `alfred vault-sync-status --url http://127.0.0.1:5984` confirms the
server side is reachable.

## MCP server

`alfred-mcp` runs Alfred's stdio MCP server for Claude Desktop/Code, Cursor,
and other local MCP clients. Every tool is default-deny: a client gets
nothing until explicitly granted, e.g. `alfred client-grant --client-id
local-mcp --sensitivity public --sensitivity personal --tool memory_search
--tool remember --tool forget --tool calendar_event_propose --tool
message_draft --tool action_commit --tool brief_get --tool connector_status
--allow-write`. `alfred-mcp --client-id <id>` runs it under a different
identity (default: `local-mcp`) so a second stdio client—for example OpenAI's
Secure MCP Tunnel—gets its own separately scoped grant.

Current tools: `system_status`, `agenda_get`, `memory_search`, `profile_get`,
`remember`, `forget`, `calendar_event_propose`, `message_draft`,
`action_commit`, `brief_get`, `connector_status`, `task_upsert`,
`task_complete`, and `reminder_set`—all 12 of section 7's documented tools,
plus `system_status` and `calendar_event_propose` (which `action_commit`
needs, since section 7 never names a tool for previewing a calendar write).
`remember`/`forget` additionally check the requested memory's sensitivity
against the client's own scope, so a client granted only `public`/`personal`
cannot write or erase a `secret` memory even with `--allow-write`.

`forget`, `calendar_event_propose`, `message_draft`, and
`message_send_propose` only preview. `action_commit` performs whatever a
previous tool call previewed, once given a fresh approval token. There is
deliberately no MCP tool to approve one. `action_commit` mints a fresh Google
access token itself when finishing a calendar write, Gmail draft, or Gmail
send; nothing is cached. Gmail sends remain explicit, one-time
approval-gated actions and never run from sync or a scheduled job.

### Repeated-workflow proposals

When Hermes is enabled, Alfred performs a local workflow scan once per day.
It looks for the same successful two-to-eight-tool sequence at least three
times across two days. It stores only structural metadata: tool names,
argument names, and allowlisted routing labels such as connector type. It
does not store prompts, email bodies, task or event titles, people,
addresses, dates, identifiers, or arbitrary argument values. Failed turns and
turns that contain `action_commit` are ineligible.

```powershell
.\.venv\Scripts\alfred workflow-scan
.\.venv\Scripts\alfred workflow-list --state pending
.\.venv\Scripts\alfred workflow-show --version-id <ID>
.\.venv\Scripts\alfred workflow-accept --version-id <ID>
.\.venv\Scripts\alfred workflow-reject --version-id <ID>
```

Each suggestion is an inert, versioned `SKILL.md` plus a unified diff and an
expiring review record. Accepting the diff records that decision but this
first slice cannot activate or execute a generated skill; unattended learning
can produce review material but cannot modify the Hermes profile. Raw daily
Calendar/Canvas JSON snapshots are not used here: their normalized source
records already preserve provenance and change history.

### Streamable HTTP (remote/private clients)

`alfred-mcp` is stdio-only. For a client that can't spawn a local process—or
that should run as a separate identity from `local-mcp`—`alfred mcp-http-run
--client-id <id>` serves the same tool surface over Streamable HTTP on
`http://127.0.0.1:8000/mcp`. The host is not configurable: this binds
loopback only. `<id>` needs its own `client-grant` first, exactly like a
stdio client.

Every request must carry `Authorization: Bearer <token>`, checked outside
FastMCP's own request handling so an unauthenticated caller can't even open a
session. Generate that token once with `alfred mcp-http-token-generate`
(refuses to overwrite an existing one, same as `backup-key-generate`); it's
stored in the OS credential store, never in a config file. This is a single
shared secret, not OAuth—OAuth 2.1/RFC 9728 is reserved for *public* remote
access, a separate undertaking this does not attempt. FastMCP also
auto-enables DNS-rebinding protection (Host/Origin header validation)
whenever the host is loopback, which is always true here.

### ChatGPT (Secure MCP Tunnel)

ChatGPT can't connect directly to a local MCP server the way Claude
Desktop/Cursor do over stdio, so OpenAI's [Secure MCP
Tunnel](https://developers.openai.com/api/docs/guides/secure-mcp-tunnels) is
the private-access path—an outbound-only relay via OpenAI's own
`tunnel-client` daemon, run by you, which Alfred does not vendor or
reimplement. See `deploy/openai-tunnel/README.md` for the walkthrough:
creating a tunnel and scoped client grant, then pointing `tunnel-client` at
`alfred-mcp --client-id chatgpt-tunnel` over stdio (deliberately not the
Streamable HTTP transport above, to avoid reconciling two authentication
schemes). Whether your ChatGPT plan supports custom MCP connectors at all is
between you and OpenAI.

## Admin dashboard

`alfred admin-ui-run` serves a small, read-only web dashboard: today's
agenda, pending approvals, connector health, evaluation signals, and the
recent audit trail, at `http://127.0.0.1:8200`. One page per concern, no write
actions (approving still goes through `alfred approval-approve`, never a
button on this page). No CDN fonts or icons; it works with the network off.

```powershell
.\.venv\Scripts\alfred admin-ui-token-generate
.\.venv\Scripts\alfred admin-ui-run
```

Defaults to loopback-only like `mcp-http-run`, but unlike it, `--host` is a
real option—this is meant for a person to look at, sometimes from a phone.
`127.0.0.1` is not reachable from another device even over a VPN; to check
it from your phone, run `alfred admin-ui-run --host <this-PC's-VPN-IP>` (a
Tailscale IP, for example—`tailscale ip -4`), never `--host 0.0.0.0` unless
you already have firewall rules restricting who can reach the port. Works in
any modern browser; the layout reflows for a phone-width screen and uses the
system font (Segoe UI on Windows, San Francisco on Safari/iOS).

Auth is the same bearer token as `mcp-http-run`, delivered differently:
visiting any page without one redirects to a login screen; entering the token
there sets an `HttpOnly`, `SameSite=Strict` cookie whose value *is* the token
(no separate session store). Scripted/API access can still send
`Authorization: Bearer <token>` directly and skip the cookie.

## Local vector search (optional)

`MemoryGraph` accepts an optional `embedding_provider`. Without one,
`memory-search` stays FTS5 keyword-only. With one—for example
`alfred.embeddings.OllamaEmbeddingProvider`, pointed at a local
Ollama—`remember`, `memory-correct`, and `forget` also keep a versioned
vector per memory in the `embeddings` table, and `memory-search` folds in
nearby vector matches (within a cosine-distance cutoff) once keyword hits are
exhausted. Vectors are namespaced by model name, so trying a different
embedding model never mixes incomparable spaces; switching models means
re-embedding, not migrating data. Run `memory-embed-backfill --model
nomic-embed-text` for a one-shot rebuild, or pass `--embedding-model
nomic-embed-text` to `alfred run` for continuous local upkeep.

## Local model inference (optional)

`alfred.models.OllamaClient` is local-first text generation: point it at a
running Ollama and it calls the non-streaming `/api/generate` endpoint,
returning the text plus Ollama's own prompt/completion token counts.
`BriefingService` accepts an optional `llm_writer`; without one,
`write_brief()` is just `render()`—the deterministic text, unchanged. With
one, the model only ever rewrites the deterministic render's wording; every
fact, date, and link it sees comes from that text, never from the model's own
knowledge, and a failed or unreachable model falls back to the deterministic
render rather than costing the user their brief. Every pass is audited with
its token counts. Nothing in the CLI, MCP server, or job runner wires a live
writer in by default.

Cloud pieces are also built: `alfred.models.OpenAICompatibleClient` and
`AnthropicCompatibleClient` speak the OpenAI chat-completions and Anthropic
Messages API shapes, but neither should be constructed bare—wrap either in
`GuardedCloudProvider` first, which enforces:

1. **Redaction.** `Redactor` scrubs common secret/PII shapes (emails, bearer
   tokens, OpenAI/GitHub/Slack/AWS key patterns, SSNs, card-like digit runs,
   phone numbers) from the prompt and system text before either reaches the
   cloud provider. It's a best-effort pattern scrub, not a guarantee—keep
   genuinely `secret`-tagged content out of a cloud prompt rather than relying
   on this to catch it.
2. **Monthly hard cap, fail closed.** `monthly_budget_usd` defaults to `0.0`,
   so an unconfigured `GuardedCloudProvider` never calls out at all. Every
   call checks month-to-date spend (summed from its own audit records)
   *before* calling; once that's already at or past the cap, it raises
   `CloudBudgetExceeded`. This checks the cap before each call, not a
   per-call ceiling—one very large call can still push the total over.
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
- Keep the database as the source of truth; transports do not contain
  business logic.
- Do not place credentials, raw personal data, or local databases in Git.
- Every MCP tool is gated by `PolicyStore`; an unregistered or narrowly
  scoped client gets nothing by default. Consequential actions on the MCP
  surface can only create previews; a human must approve them outside that
  MCP client before `action_commit` can execute the exact approved preview.

See [CONTRIBUTING.md](CONTRIBUTING.md) for the full local setup, test, and PR
workflow, [SECURITY.md](SECURITY.md) to report a vulnerability privately, and
[RELEASING.md](RELEASING.md) plus [CHANGELOG.md](CHANGELOG.md) for how a
version gets cut and verified.

## License

Apache-2.0. See [LICENSE](LICENSE).
