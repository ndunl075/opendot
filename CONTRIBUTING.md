# Contributing to Alfred

Alfred is a local-first personal secretary, not a multi-tenant product. Most
of the design exists to keep one owner's data under their own control, so
changes are held to that bar first and "generally useful" second.

Read [ARCHITECTURE.md](ARCHITECTURE.md) before changing behavior — it is the
canonical source of the project's decisions (section 1), build order (section
10), and safety invariants (section 8). If a change contradicts something it
says, the architecture doc changes too, in the same PR, with a one-line
reason; it should never silently drift out of date. [README.md](README.md)
documents what already exists day to day.

## Local setup

```powershell
python -m venv .venv
.\.venv\Scripts\python -m pip install -e .[dev]
.\.venv\Scripts\alfred init
.\.venv\Scripts\alfred status
```

Or run `.\scripts\install.ps1`, which does the same four steps and is safe to
re-run. Neither step touches Google, Telegram, Slack, GitHub, or any other
provider — connector credentials are a separate, later step (README.md's
"Local connectors").

## Tests

```powershell
.\.venv\Scripts\python -m pytest -q
```

Every module under `src/alfred/` has a matching `tests/test_*.py`. A PR that
changes behavior should extend or add tests in the same file rather than
relying on a new one, unless the change introduces a genuinely new module —
match the existing file's naming and fixture style rather than introducing a
new pattern.

## Code shape

These aren't style preferences; each one is load-bearing for the safety
properties section 8 describes, and code that doesn't follow them tends to
silently reopen a gap that was already closed once:

- **The database is the source of truth.** CLI, MCP server, job runner, and
  any future transport all call the same `alfred/*.py` functions; transports
  never contain business logic of their own. If you find yourself writing the
  same check in two transports, it belongs in the shared module instead.
- **Consequential external writes are propose-then-execute, never one step.**
  A `propose_*()` never touches a provider; `execute()` consumes a one-time
  approval token and performs the write. See `GoogleCalendarActions`,
  `GmailActions`/`GmailSendActions`, and `GitHubActions` for the current
  shape, including their crash-window recovery (a stable ID or hidden marker
  the provider can be re-queried for, so a retry after a crash recovers
  instead of risking a duplicate — never guess; fail closed on an absent or
  ambiguous result).
- **Every write is idempotent.** Reuse the existing `action_receipts`
  idempotency-key pattern (`{action_type}:{approval_id}`) rather than
  inventing a new one.
- **Ingest is deduplicated at the event layer.** `EventStore.append()` is
  keyed on `(source, external_id)`; a connector should rely on that for
  dedupe rather than tracking its own "have I seen this" state.
- **Default-deny channel identity.** A new intake channel (see
  `TelegramGateway`, `SlackGateway`, `GmailInboundGateway`) must check a
  locally configured allowlist before turning a message into a task, reminder,
  or any other local write — never trust the sender field alone.
- **No secrets, raw personal data, or local databases in Git.** Secrets go
  through `SecretStore` (the OS credential store), never a config file, log
  line, or committed fixture.
- **Every consequential action is audited.** Use `AuditLog.append_in_transaction`
  inside the same transaction as the action it's recording, not a separate
  call afterward, so the audit record and the action it describes can never
  drift apart.

## Opening a PR

- Keep the diff scoped to one slice or one connector where possible; it's
  easier to review and easier to revert.
- Run the full test suite before opening the PR, not just the file you
  touched — several modules share tables and idempotency keys.
- If the change completes or starts a build-slice item from
  `ARCHITECTURE.md` section 10, say which one in the PR description and
  update that section's line in the same PR.
- Security-relevant reports (a credential handling gap, an approval bypass, a
  way to make an action non-idempotent) go through
  [SECURITY.md](SECURITY.md), not a public issue.
