# Changelog

All notable changes to this project are documented here. Format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/); versioning follows
[Semantic Versioning](https://semver.org/) (while pre-1.0, a breaking change
only bumps the minor version — see RELEASING.md).

## [Unreleased]

### Added

- Optional mobile vault sync (`deploy/couchdb/`, `alfred vault-sync-status`):
  a self-hosted CouchDB service (loopback-bound by default, VPN for remote
  reach) for a vetted open-source Obsidian community plugin (Self-hosted
  LiveSync) to replicate `alfred-vault/` against. OS-agnostic on Alfred's
  side — the phone OS only determines which third-party client app an
  operator installs, not what Alfred's server needs to expose. Alfred's own
  code never becomes a sync client; `vault-sync-status` only confirms the
  server is reachable.
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

No release has been tagged yet; see `pyproject.toml` for the current
in-development version.
