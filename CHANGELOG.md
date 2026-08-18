# Changelog

All notable changes to this project are documented here. Format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/); versioning follows
[Semantic Versioning](https://semver.org/) (while pre-1.0, a breaking change
only bumps the minor version — see RELEASING.md).

## [Unreleased]

### Fixed

- A database whose migration history diverged from the checked-out branch now
  explains itself instead of refusing to start with
  `sqlite3.IntegrityError: UNIQUE constraint failed: schema_migrations.version`.
  `migrate()` tracks what is applied by *filename* while the version number is
  the primary key, so a database carrying a migration this build does not ship
  can hold a version one of ours also claims — the packaged file can then never
  be recorded, and Alfred will not launch. The collision is now detected across
  the whole pending batch before any of it is applied (a refusal that
  half-migrates would be worse than the crash it replaces) and raised as
  `MigrationConflict`, naming both migrations and listing every record this
  build has no file for. Divergence on its own is still fine; only a contested
  version number is an error. Added `scripts/reconcile_migrations.py`, which
  reports that state and, with `--apply`, removes the unshippable records;
  leftover tables are reported but only dropped when named explicitly, and never
  while they hold rows.

### Changed

- Response feedback is now noticed instead of requested. The `helpful` /
  `missing context` / `wrong context` buttons under every successful answer are
  gone; two detectors produce the same three verdicts on their own. Named rules
  read the owner's next message for an unambiguous reaction ("you missed the one
  from sam", "that's the wrong week", "thanks, that's perfect") and stay silent
  on everything else, and a coverage check flags any reply built from a
  connector that has never synced or last synced a day ago — a gap the chat
  gives no way to notice. Verdicts still store only source names, freshness, the
  name of the rule that fired, and opaque record IDs. Inline keyboards are now
  reserved for approvals, so a keyboard appearing at all means a write is
  waiting on a decision. Taps on keyboards already sitting in chat history are
  still honored. `alfred evaluation-status` and the admin UI break the counts
  down by how each verdict was reached, and report self-flagged answers apart
  from the helpful rate so a quiet connector does not read as a week of answers
  the owner disliked.
- Collapsed helpers that recent feature work had copy-pasted, with no change to
  behavior or to any public name. `destinations.py` now owns the single
  `channel:recipient` rule that `reminders`, `nags`, `important_dates`, and
  `brief_schedule` each carried a copy of, plus the payload fallback `jobs.py`
  repeated three times; `wall_clock.py` owns the one `HH:MM` parser and the one
  duration formatter that `quiet_hours`/`important_dates` and
  `availability`/`briefing` had duplicated; and `important_dates.annual_label`
  is now the only place a birthday reads "turns N", so the reminder and the
  brief cannot word it differently. One user-visible detail improved on the
  way: an invalid important-date destination now says "important date
  destination must be…" like its three siblings, instead of an unattributed
  "destination must be…".

### Fixed

- Slack replies no longer arrive scrambled. The Slack outbox claim ordered
  pending rows by `created_at, id`, and `id` is a random `uuid4` while
  `created_at` only has second granularity — so every multi-part answer
  enqueued in one second shipped in random order (reproduced: scrambled on
  six of six trials). Now tie-broken on `rowid`, the same fix Telegram
  already carried; the two delivery paths had drifted apart on it.
- Conversation history no longer risks replaying a previous answer out of
  order: reassembly tie-broke on `idempotency_key`, whose bubble index is
  decimal, so bubble 10 sorted ahead of bubble 2. Latent at the four-bubble
  default and wrong the moment that cap is raised; now insertion order.
- Annual birthday reminder roll-forward no longer loses the next year's
  "turns N" payload: the shared job UPDATE after delivery was rewriting
  `payload_json` from the pre-delivery snapshot (introduced with nag jobs).
- Google Calendar REST paths now encode provider IDs as path segments, fixing
  the built-in US Holidays calendar whose `#` previously truncated the ID and
  surfaced only as `HTTPStatusError`.
- `gmail-sync` bounded to the most recent `--limit` unread messages
  (default 500) instead of the entire unread backlog. An account with a
  very large unread count (found live: north of 10,000) previously
  hard-failed once Gmail's own pagination cap was reached; even after
  raising that cap, fetching thousands of messages individually every sync
  cycle wasn't actually useful. Bounding to the most recent window (Gmail's
  own default ordering) fixes both.
- Reminder routing no longer misses "reminding": `\bremind\b` never matched
  that form, so "keep reminding me…" could fall into the toolless casual lane.

### Added

- Morning brief optional sleep context from synced Google Health sleep
  records: when last night's segments overlap the local evening→noon window,
  the brief shows a duration line (and dominant stage when present). No sleep
  data → section omitted (fixtures only until a live wearable smoke test).
- Quiet hours for proactive deliveries: set `ALFRED_QUIET_HOURS_START` /
  `ALFRED_QUIET_HOURS_END` (HH:MM) and optional `ALFRED_QUIET_HOURS_TIMEZONE`
  (IANA). Job-backed outbox rows (reminders, briefs, nags) stay `pending`
  through the local window; interactive Hermes/gateway replies with no
  `job_id` still deliver so a late-night chat is not silence. Disabled when
  unset.
- Mood check-ins and gratitude journal (`mood_record`, `gratitude_record`,
  `journal_get`): 1–5 mood ratings with optional notes and free-text
  gratitude entries on dedicated tables, deliberately separate from habits.
  Trend direction is only named with at least five days of mood data and a
  0.5-point spread between older and newer daily averages; otherwise
  `journal_get` returns an explicit reason instead of a silent null.
- Nag-until-done reminders on the job runner: `nag_until_done` (MCP) and
  `alfred nag-until-done` repeat on an interval, re-check linked task state on
  every fire, stop silently when the task is completed anywhere, and deliver an
  explicit final message on the last attempt (hard-capped at `max_attempts`).
  Hermes tool selection routes "keep reminding", "nag me", and "until done"
  phrasing to the new tool.
- Daily fixed-time reminders on the existing job machinery: `reminder_set`
  (MCP) and `alfred reminder-set` accept `--daily` / `daily=true` with an
  IANA timezone so wake-up, bedtime, and study lock-in requests keep their
  local wall-clock hour across daylight saving. One-shot reminders are
  unchanged.
- Birthdays and important dates over the existing task + reminder tables:
  `important_date_set` / `important_dates_get` (MCP) and
  `alfred important-date-set` / `alfred important-dates`. Each date is an
  open task whose due date is the next occurrence, plus an annual reminder
  that rolls forward after delivery. The morning brief surfaces the next
  seven days under Birthdays & dates (the weekly digest window).
- Gmail unread sync now stores `thread_id` and `list_unsubscribe`.
  `alfred gmail-thread-backfill` additive-repairs older rows (never
  overwrites content, timestamps, or existing values).
  `threads_awaiting_reply` / `alfred threads-awaiting-reply` groups unread
  by thread and drops List-Unsubscribe newsletters that Gmail often labels
  CATEGORY_PERSONAL.
- Calendar availability (`availability_get` / `alfred availability`):
  interval-merge free gaps over synced timed events; all-day events are
  listed as ambiguous context rather than busy hours.
- Open GitHub pull-request watcher (`pull_requests_get` /
  `alfred pull-requests`): live search snapshot of PRs you authored or were
  asked to review, with stale marking when `updated_at` exceeds a threshold
  (default 14 days). Deliberately not built on notifications sync.
- Telegram answers now include explicit helpful/missing/wrong context buttons.
  Feedback stores only source/freshness/opaque-record provenance, is paired to
  the original sender, cannot authorize actions, and has bounded influence on
  Gmail/GitHub ordering within existing priority tiers.
- Hermes turns now expose at most eight request-relevant Alfred MCP tools;
  inbox/GitHub reads already prefetched by the bridge expose none.

- Canvas Calendar Feed setup now has one guided, secret-safe command that
  validates before saving and repairs an exact accidental double-paste.
- Briefs and academic memory now collapse an exact Canvas/Google Calendar
  title-and-time match, preferring the native Canvas evidence.

- Slack app setup (`deploy/slack-app/`): a ready-to-paste manifest
  (`manifest.yml`) configuring OAuth scopes, event subscriptions, and
  Socket Mode in one step, verified against Slack's own manifest schema
  docs, plus a full walkthrough for the two tokens, inviting the bot, and
  pairing. The connector itself (`slack.py`, `slack_socket.py`) was already
  built and unit-tested; this removes the setup friction that was blocking
  a real smoke test, without changing any Python code.

## [0.1.0] - 2026-08-12

First tagged release. Alfred Core's local implementation covers build
slices 1 through 7 of ARCHITECTURE.md — the walking skeleton, Telegram
intake/delivery, the daily secretary (Calendar/Canvas read sync and morning
brief), the typed temporal memory graph, approval-gated writes with
crash-window recovery, every documented MCP transport (stdio, Streamable
HTTP, the ChatGPT tunnel), and the polish pass below. Two connectors are
built but not yet exercised against a live account: Slack Socket Mode
(awaiting a real app credential) and Google Health (awaiting a real
wearable-linked account).

### Added

- Read-only, Notion-styled admin dashboard (`alfred admin-ui-run`,
  `admin_ui.py`): agenda, pending approvals, connector health, and recent
  audit trail, at `127.0.0.1:8200` by default. No write actions — approving
  a pending action still goes through the CLI. Bearer-token-gated like
  `mcp-http-run`, delivered via a login page + `HttpOnly`/`SameSite=Strict`
  cookie (browsers can't attach a custom header to a plain navigation) as
  well as a raw `Authorization` header for scripted access. Unlike every
  other local HTTP surface in this codebase, `--host` is a real,
  operator-set option (still defaulting to loopback-only) since this one is
  meant for a person to look at, sometimes from a phone over a VPN — plain
  `127.0.0.1` is unreachable from another device regardless of network
  route. Responsive down to phone-width screens; renders identically in
  Edge, Safari, Firefox, and Chrome. The shared bearer-auth middleware
  moved to a new `http_auth.py` so the MCP HTTP transport and the admin UI
  use one implementation, not two.
- ChatGPT / OpenAI Secure MCP Tunnel support (`deploy/openai-tunnel/`):
  documents pointing OpenAI's own `tunnel-client` (not vendored here) at
  `alfred-mcp` over stdio. `alfred-mcp` gained a `--client-id` flag
  (default unchanged: `local-mcp`) so the tunnel gets its own separately
  scoped grant instead of sharing Claude/Cursor's default identity.
- Optional mobile vault sync (`deploy/couchdb/`, `alfred vault-sync-status`):
  a self-hosted CouchDB service, loopback-bound by default, for a vetted
  open-source Obsidian community plugin (Self-hosted LiveSync) to replicate
  `alfred-vault/` against. Set `ALFRED_VAULT_SYNC_HOST` to this host's own
  VPN/Tailscale IP for actual phone reach — plain loopback binding accepts
  no connection from another device regardless of network route, VPN
  included, so it does nothing on its own for remote access. OS-agnostic on
  Alfred's side — the phone OS only determines which third-party client app
  an operator installs, not what Alfred's server needs to expose. Alfred's
  own code never becomes a sync client; `vault-sync-status` only confirms
  the server is reachable.
- Google Health read-only sync (`google_health.py`, `alfred health-sync`,
  `--google-health` for `alfred run`): steps, sleep, and heart-rate data
  points as `sensitive`-tagged events, reusing the existing Google OAuth
  grant with additional scopes. Built but **unverified** — endpoint and
  field names come from Google's v4 REST reference, not a live account;
  normalization keeps every point's full raw JSON rather than trust its own
  field-name guesses, so nothing is lost if a name turns out wrong.
- Real Windows service packaging (`alfred-service`, `alfred.winservice`):
  `alfred run`'s loop now survives logoff/reboot with no logged-in session,
  as an alternative to the Task Scheduler workaround. A thin wrapper around
  the same runner construction/cleanup logic the CLI uses, not a second
  implementation; stops via a new `AlfredRunner.run_forever(stop_check=...)`
  hook.
- Cloud model fallback (`OpenAICompatibleClient`, `AnthropicCompatibleClient`)
  guarded by `GuardedCloudProvider`: pattern-based secret/PII redaction before
  egress, a monthly spend cap that defaults to `$0` and fails closed, and
  per-call cost/token tracking via the existing audit log. Nothing wires a
  cloud provider in by default.
- Streamable HTTP MCP transport (`alfred mcp-http-run`), bound to `127.0.0.1`
  only and authenticated with a bearer token (`alfred mcp-http-token-generate`)
  checked outside FastMCP's own request handling. Serves the same tool
  surface as the stdio server for remote/private MCP clients.
- Windows installer script (`scripts/install.ps1`) covering venv creation,
  package install, database init, and an optional Task Scheduler entry.
- Public documentation for outside contributors: `CONTRIBUTING.md`,
  `SECURITY.md`, and GitHub issue/PR templates.
- Release automation: `.github/workflows/release.yml` builds, tests, and
  attests provenance for a tagged release; `.github/workflows/ci.yml` runs
  the test suite on every push and PR; `RELEASING.md` documents the process.
- Inbound Alfred email: `Task:`/`Remind:` subject-line commands from
  explicitly allowed senders create local tasks and, optionally, reminders.
- Durable crash-window recovery for Calendar event creates, Gmail
  drafts/sends, and GitHub issues/PR comments — a retry after a crash
  between the provider accepting a write and Alfred recording its receipt
  now recovers the prior write instead of failing closed on "token already
  consumed."
