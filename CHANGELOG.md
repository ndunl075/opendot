# Changelog

All notable changes to this project are documented here. Format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/); versioning follows
[Semantic Versioning](https://semver.org/) (while pre-1.0, a breaking change
only bumps the minor version — see RELEASING.md).

## [Unreleased]

### Fixed

- `gmail-sync` bounded to the most recent `--limit` unread messages
  (default 500) instead of the entire unread backlog. An account with a
  very large unread count (found live: north of 10,000) previously
  hard-failed once Gmail's own pagination cap was reached; even after
  raising that cap, fetching thousands of messages individually every sync
  cycle wasn't actually useful. Bounding to the most recent window (Gmail's
  own default ordering) fixes both.

### Added

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
