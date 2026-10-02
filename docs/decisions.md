# Decision log

One dated line per decision that is not already locked in ARCHITECTURE.md
section 1. Newest last.

- 2026-09-30: Package layout is `daemon/` (distribution `opendot-core`, import
  `opendot_core`); Alfred's 17 migrations are kept unchanged and migration 0018
  is the first OpenDot one. Reason: ARCHITECTURE.md sections 4 and 5.
- 2026-09-30: Data directory is `.opendot/`, database `opendot.db`, environment
  prefix `OPENDOT_`, OS keyring service name `opendot`. Reason: a clean break
  from Alfred; there are no OpenDot installs to migrate.
- 2026-09-30: `admin_ui.py` and its templates stay through M0 (minus the
  removed connectors). Reason: the React UI and the `api/` endpoints that
  replace it do not exist yet; deleting it now would leave no way to inspect
  the daemon. It is removed when the API covers its queries.
- 2026-09-30: `models.py` (Ollama-backed `TextGenerationProvider`, `Redactor`)
  is unchanged in M0. Reason: the split into `providers/` is milestone M1.
- 2026-09-30: `hermes-profile/` was not deleted outright. Its three skills
  moved to `docs/optional/routines/` (source material for the built-in
  routines in M2) and its writing rules to
  `docs/optional/style-preset-texting.md` (the optional style preset). The
  default persona is neutral. `deploy/couchdb` and `deploy/openai-tunnel` moved
  to `docs/optional/`.
- 2026-09-30: The MCP client id for the in-process agent is `agent` (was the
  old Hermes id); the actor for its approvals is `mcp:agent`; reply outbox keys
  are `agent-reply:<update>:<n>`. The MCP server no longer reads a tool filter
  or a turn id from the environment or a handshake file;
  `create_server(tool_filter=)` remains for `agent/tool_groups.py`.
- 2026-09-30: `runtime_control` restarts the daemon through the operator's
  `OPENDOT_RESTART_COMMAND` (for example `systemctl --user restart opendot`).
  Per-OS service installation belongs to `service.py` in a later milestone.
- 2026-09-30: `opendot run --learning` gates the memory and workflow learning
  passes, which Alfred only ran when a Hermes profile was configured.
- 2026-09-30: Real-looking fixture data was replaced: the test actor `nico`
  became `sam` and a real-looking Telegram id became `4242424242`. Reason: task
  0.8.
- 2026-09-30: Codex CLI 0.157.1 is installed and signed in on the build machine.
  Non-interactive invocation: `codex exec -m <model> -c
  model_reasoning_effort="<effort>" -s <read-only|workspace-write> -C <repo>
  -o <last-message-file> "<prompt>"`. Workspace-write with network access needs
  `-c sandbox_workspace_write.network_access=true`. Model IDs (from `codex debug
  models`): Astra `gpt-6-astra`, Sol `gpt-6-sol` (also `gpt-5.6-sol`), Terra
  `gpt-5.6-terra`, plus `gpt-6-luna`, `gpt-5.6-luna` and `gpt-5.5`. Efforts:
  low, medium, high, xhigh, max, and ultra on the larger models; never use max
  or ultra for unattended tasks.
- 2026-09-30: `opendot run` does not build an agent bridge until M2 (accepted review finding F1); M2 wires it and tests the CLI path.
- 2026-09-30: `chatgpt_plan` provider (task 1.4) implemented from the DevKit source
  (`packages/local/src`), because OpenAI's public docs pages fetched today
  (developers.openai.com/siwc and its token-reference page) list components and token
  lifetimes but not endpoints or scope names. Gaps filled from the DevKit: issuer
  `https://auth.openai.com` with OIDC discovery; scopes `openid profile email offline_access
  resource.invoke chatgpt.tokens.use.direct` (the last one is the plan scope checked after
  sign-in); `resource=https://api.openai.com/v1`; dynamic client registration happens inside the
  authorize step (send `client_id=dynamic_agent_client`, the issued id comes back as a
  `client_id` callback parameter and is saved); callback path `/auth/callback` on 127.0.0.1;
  revocation of the refresh token at the discovery `revocation_endpoint`.
- 2026-09-30: Docs/DevKit gaps for `chatgpt_plan`: (a) the docs list `urn:uuid:` host ids and the
  DevKit sends `ext_agent_host_id` on the authorize URL only (OpenDot does the same, not as an API
  header); (b) the DevKit only supports text messages with roles user/assistant/developer and no
  tools, while section 6.1 allows plain function tools, so OpenDot sends `function_call` and
  `function_call_output` input items and maps system to `developer` (unverified against a real
  account until the first sign-in); (c) the DevKit lists models via `GET /v1/models` returning
  `{"models":[{slug,display_name,visibility}]}` (only `visibility == "list"`), not the public
  `{"data":[{id}]}` shape; OpenDot accepts both; (d) the DevKit treats `response.incomplete` as an
  error, OpenDot maps it to `IncompleteResponse`; (e) the docs mention `earliest_refresh_at` in
  token responses, OpenDot ignores it and refreshes 60 s before expiry or on a 401; (f)
  `subscription_sharing_v2_*` codes are legacy aliases in the DevKit, OpenDot maps the
  `user_not_eligible` alias only.
- 2026-09-30: `chatgpt_plan` state (host id, saved client id, paused flag) lives in a small JSON file
  (default `.opendot/chatgpt_plan.json`), not a migration, so M2 can move it into settings later. The
  pause flag persists and only `resume()` clears it. SSE/error fixtures are synthetic until the first
  real sign-in. ID tokens are verified (RS256 via JWKS, iss, aud, exp, nonce) with `cryptography`.
- 2026-09-30 (M2): The core deny list matches outside-app tools by keyword on the action
  (delete, trash, pay, password, ...), not only Composio's passthrough. Only tools that act on
  OpenDot's own local data (memory, tasks, reminders, forget ...) are exempt. Reason: an unknown
  or new connector that deletes or pays must be refused by default, not allowed by omission.
- 2026-09-30 (M2): The eval harness names tools by the MCP names the rule defaults are keyed by
  (`memory_search`, `reminder_set`, `message_draft`) and uses the four section 9 sensitivities.
  Reason: the first draft used names no default matched and an invalid sensitivity (`low`), so
  every call fell through to `ask`. S3 now also requires the rejection to be a deny-list
  `RuleError` and that nothing was stored (stricter, not weaker).
- 2026-09-30 (M2): Each model attempt is one durable step. A failed attempt escalates the *next*
  step one tier (so a step escalates at most once and tiers are never skipped); after a success the
  task goes back to the job's own tier. A 429 of either kind (plan usage limit or ordinary rate
  limit) sets a persisted plan-wide pause that only the user clears. Auth, eligibility, disabled
  provider, spend cap and unsupported capability fail the task: no tier fixes them.
- 2026-09-30 (M2): A `handoff` decision ends the task at once (state `handed_off`) with the
  refusal text; no further model call is made. A reviewer `block` does the same and never creates
  an approval card.
- 2026-09-30 (M2): Anomaly auto-pause trips the kill switch on: an action about to run *without
  asking* for a recipient domain the user never approved before; 20 auto actions within 10
  minutes; more than half the daily budget spent within an hour. For `ask` actions a new domain is
  a reviewer concern shown on the card instead, since the user is asked anyway.
- 2026-09-30 (M2): The first user message is frozen into history after the first model step; later
  steps end with a short fixed continuation line in the changing tail. Reason: history stays
  append-only and the stable prefix stays byte-identical (S10).
- 2026-09-30 (M2 review): "Ask every time" for sending and posting is enforced in `RuleEngine.decide`
  by `rules.defaults.is_send_or_post`. It asks for explicit send tools, send verbs, and any non-read
  action on a message, comment or chat. It does not ask for local tools, built-in read tools, read
  verbs, drafts, or mailbox-only changes in mail apps. Reason: section 10 says no rule may relax
  sending; word matching errs toward asking; M4 connectors declare for each tool whether it reaches
  other people.
- 2026-09-30 (M2 review): Approval tokens stay in memory only (`TokenEscrow`). After a restart, an
  approved but unused action is proposed again, and a consumed one whose outcome is unknown is handed
  to the user. The loop never guesses.
- 2026-09-30 (M2 review): The browser chat WebSocket authenticates with the subprotocol
  `opendot.bearer.<token>`: the server selects `opendot` and never echoes the token. It also requires a
  local Origin. Reason: browsers cannot set an Authorization header on a WebSocket, and CORS does not
  protect WebSockets.
- 2026-09-30 (M3): Codex's default (elevated) Windows sandbox fails to start any process on the build
  machine ("apply deny-read ACLs"). The unelevated Windows sandbox works and still confines writes to
  the workspace, so GPT tasks run with: `codex exec -m <model> -c model_reasoning_effort="<effort>"
  -s workspace-write -c 'windows.sandbox="unelevated"' -c sandbox_workspace_write.network_access=true
  -C <worktree> -o <last-message-file> - < <prompt-file>` (prompt on stdin; passing it as an argument
  makes a backgrounded run wait on stdin forever). `--dangerously-bypass-approvals-and-sandbox` is
  never used.
- 2026-09-30 (M3): pnpm comes from corepack (`corepack install --global pnpm@10`, pnpm 10.34.6); the
  old corepack shim pointed at an uncached pnpm 12.4.1.
- 2026-09-30 (M3): Each GPT UI task runs in its own git worktree on branch `m3/<task>` so Opus can
  build daemon and desktop pieces in parallel, then reviews and merges into `m3/base`.
- 2026-09-30 (M3): `opendot serve` puts the UI and API on one loopback origin. The API accepts only
  the bearer token (never a cookie, so no cross-site request can act). A browser signs in once at
  `/login` by pasting the token, which sets an HttpOnly, SameSite=Strict cookie that only unlocks
  `index.html` with the token in a meta tag. Requests must address a loopback Host (DNS rebinding).
  Contract endpoints the daemon does not serve yet return 501 `not_implemented`. Reason: the token must
  never be handed to an unauthenticated local process, and the UI must work before every endpoint
  is real.
- 2026-09-30 (M3): The desktop app bundles the UI and passes the token to it through a Tauri
  initialization script (`window.__OPENDOT__`). It reads the token by running the sidecar's
  `api-token show` (the same user's keychain), and calls the daemon cross-origin. CORS allows only the
  Tauri origins. The sidecar is one PyInstaller file that also ships `ui/dist`. Its data lives in the
  OS app-data folder. The installer is unsigned NSIS until certificates exist (Needs you, before a
  public release).
- 2026-09-30 (M3): Package scripts never call `pnpm`, `npm` or `corepack` by name: on this machine
  `cmd.exe` cannot see them. Nested pnpm goes through `npm_execpath`, the Tauri CLI through Node, and
  `uv` is found by path search (`UV`, PATH, per-user install folders).
- 2026-10-01 (M4): One service process: `opendot serve` runs the API and UI plus a background thread
  (`always_on.AlwaysOnWorker`) with the `opendot run` runner (due jobs and reminders, outbox delivery,
  connector syncs), so launchd, Task Scheduler and systemd each start exactly one command. Telegram and
  Slack stay off (no pairing in the default `run` arguments). Connectors without stored credentials are
  skipped quietly instead of logging an error every cycle; syncs run in the runner's background worker so a
  slow sync never delays a due reminder. `--no-background` serves only the API. Do not run `opendot run`
  next to the service: they would both deliver.
- 2026-10-01 (M4): Service definitions run `python -m opendot_core.cli --db <app-data>/opendot.db serve` (the
  frozen sidecar itself when `sys.frozen`), using the same `org.opendot.desktop` app-data folder as the
  desktop app so both share one database. Logs: macOS `~/Library/Logs/OpenDot/daemon.log` and Linux
  `<app-data>/logs/daemon.log` through the OS service manager; Windows through `serve --log-file` (Task
  Scheduler cannot redirect output). Restart on failure: launchd `KeepAlive.SuccessfulExit=false`, systemd
  `Restart=on-failure`, Task Scheduler `RestartOnFailure` (1 minute, 999 tries). The Windows task is created
  for the current user with `LeastPrivilege` and runs on battery. Reason: no admin or sudo anywhere. The
  definitions are pure functions; the install shells out through an injected runner. Windows `status`
  parses the English `Status:` line of `schtasks /Query`.
- 2026-10-01 (M4): Keep-awake holds the lock only from the always-on thread (the Windows execution-state flag
  belongs to the calling thread) and re-reads the setting every 15 seconds. "Only while plugged in" treats an
  unknown power state as battery. A machine with no battery counts as plugged in. The Linux helper is
  `systemd-inhibit --what=idle ... tail --pid=<daemon>` and is stopped by killing its process group.
- 2026-10-01 (M4): Wake detection is a wall-clock gap larger than the loop interval plus 60 seconds of slack
  for a slow cycle; on start the reference is the last runner heartbeat, so a restart after downtime counts
  too (a fresh install does not). Catch-up never delivers anything itself: `JobRunner` still marks late
  deliveries. A recurring job (daily reminder, daily agent task, morning brief) whose next occurrences were
  also missed is moved to its latest missed occurrence before `run_due`, so it runs once and the older runs
  are counted as skipped. Annual dates and nags already run once and reschedule. Every connector then syncs
  once (`mark_connectors_due`). The single note goes to the first destination of the late jobs, or to
  `ui:owner` when there is none (no channel worker delivers that; the UI reads it from the outbox, M4 UI work).
  No note when nothing was missed.
- 2026-10-01 (M4, 4.6): `opendot doctor` returns ok, warn, fail or skipped per check, one line each.
  Only `fail` makes the exit code non-zero; warnings (daemon not running, signed out, no backup, a
  headless secrets file) never do. A missing keychain is a `fail` unless a secrets file is in use,
  where it is a warn. Reason: warnings describe a normal setup that needs attention; failures mean the
  install cannot work.
- 2026-10-01 (M4, 4.6): `opendot doctor --ci` runs against a fresh temporary database and never reads
  the keychain, probes the daemon, or contacts a network. It runs: database and migrations, packaged
  migration numbering, audit hash chain, an encrypted-backup round trip with a throwaway key,
  connector-health query, and disk space (low space is a warn there). The keychain, access token,
  secrets file, daemon, service, ChatGPT sign-in, backups and restore drill print "skipped (needs
  your setup)". Reason: the milestone finish line must pass on a clean CI runner with no account.
- 2026-10-01 (M4, 4.6): the doctor reads ChatGPT state with `ChatGPTPlanProvider.status()` (keychain
  plus the local state file, no request). It uses the provider's default state path
  `.opendot/chatgpt_plan.json`, the same one `opendot serve` uses.
- 2026-10-01 (M4, 4.6): nothing recorded when a backup or restore drill ran, so `backup-create` and
  `backup-verify` now write `backup-status.json` (paths and times only, never keys) next to the
  database. Doctor reads it, and also scans the backup folder (`--backup-dir`, `OPENDOT_BACKUP_DIR`,
  or `<data folder>/backups`) for `*.opendot-backup` by modification time.
- 2026-10-01 (M4, 4.6): service status comes from `opendot_core.service` if it exists (module
  `status()` or `service_status()` returning an object or dict with `installed`); otherwise doctor
  warns "service command not available". Written defensively because task 4.1 is built in parallel;
  when it lands, check that its status shape matches `_default_service_status` in `doctor.py`.
- 2026-10-01 (M4, 4.6): a headless secrets file is detected from `--token-file`, `OPENDOT_TOKEN_FILE`,
  `$CREDENTIALS_DIRECTORY/opendot-token` (systemd) or `/run/secrets/opendot-token` (Docker), and is
  always a WARN with the section 11 explanation (plus a mode check on POSIX).
- 2026-10-01 (M4, 4.7): the Docker image shares the host's network namespace (`network_mode: host`,
  Linux only) instead of publishing a port. Reason: `opendot serve` hard-codes 127.0.0.1, so a
  published port could not reach it, and widening the bind address is not allowed.
- 2026-10-01 (M4, 4.7): known gaps found while writing the guides, not fixed here: (1) the ChatGPT
  sign-in tokens, backup key and connector secrets are kept only through the OS keychain, so the Docker
  image cannot hold a ChatGPT sign-in and a systemd server needs a Secret Service keychain; the token
  file covers only the access token. A protected-file token store is needed for a true headless setup.
  (2) The sign-in callback port is random per attempt, so the SSH-forward guide has the user read the port
  from the authorize URL; a `--redirect-port` option would simplify it. (3) The daemon refuses non-loopback
  `Host` headers; `tailscale serve` may forward the tailnet name, which would need a deliberate Host
  allow-list in the daemon. (4) The guide uses `POST /v1/auth/chatgpt/start`, which is a contract
  endpoint another M4 task wires up.
- 2026-10-01 (M4): Rules endpoints map the contract rule onto the engine rule without a migration: `action` is
  `tool` or `tool.action`; `name` and `enabled` live in the `rule_names` and `rules_disabled` settings and
  `RuleEngine.decide` skips disabled rules. Built-in defaults and the deny list are both `locked` (403); a user
  rule that skips asking is capped at "sensitive" data. Reason: the contract has no tool or sensitivity field and
  the rules table has no name or enabled column; a new column would collide with parallel migrations.
- 2026-10-01 (M4): Tier overrides are per job type (`Router.job_overrides`, tier plus optional effort), separate
  from `Router.overrides` (tier to model id). A top-tier override still needs approval unless auto top-tier is on.
- 2026-10-01 (M4): Restore over HTTP is two calls with the user's approval in between: the first proposes a
  `database_restore` approval (202, `restored: false`), the user approves it, the repeat call consumes the
  escrowed one-time token and restores. Backups are encrypted files in `<database folder>/backups` and the AES
  key is created in the keychain (`backup-encryption-key`) on the first backup.
- 2026-10-01 (M4): API keys saved from the UI go only to the keychain; the settings and providers endpoints never
  return one and enabling a provider or feature is always its own call.
- 2026-10-01 (M4): Built-in routines (task 4.5) live in `opendot_core/routines/`. Each has a small
  pydantic settings model stored through `SettingsStore` (`routine_morning_brief`,
  `routine_inbox_triage`, `routine_weekly_review`) and is OFF until the user enables it and picks when it
  runs. Reason: a routine delivers things the user did not just ask for, and the time and timezone are
  the user's choice.
- 2026-10-01 (M4): Routines are run by a `RoutineScheduler` that the always-on loop calls once per cycle
  (`OpenDotRunner(routines=...)`), not inside `JobRunner`. Reason: a model pass is slow and must not run
  inside the job runner's write transaction. Each enabled routine still owns one `jobs` row (kind
  `routine`, key `routine:<name>`), which `JobRunner` skips. The row holds the schedule, the next run and
  the routine's state, and gives every delivery a `job_id` so the delivery workers also hold it in quiet
  hours. A routine that is off and never ran adds no row.
- 2026-10-01 (M4): A scheduled run needs: enabled, due, and outside quiet hours (stored `quiet_hours`
  setting, else the environment). A late run is caught up once, never made up for a time before the
  routine was switched on, and quiet hours defer the run (it stays due) instead of running and holding
  the message, so no model call is spent during the window. Results go to the outbox, destination
  `desktop:owner` by default; a manual `opendot routines run <name>` ignores the schedule, the switch and
  quiet hours but not budgets or the kill switch.
- 2026-10-01 (M4): The only model passes are `AgentLoop` tasks (`job_type` `summarize` for the brief and
  weekly review wording, `sort` for triage; both cheap/low), started with an empty tool group
  (`start_task(..., tool_group=())`). Budgets, the plan-limit pause, the kill switch and the usage meter
  therefore apply, and routines are read-only at the tool layer: a model reply that asks for a tool is
  refused by the loop, so triage cannot send, draft, label or delete. A pass that fails or is paused is
  abandoned (`AgentLoop.abandon`) so a later resume never spends a call on a result nobody waits for, and
  the plain-code text is delivered instead. After a failure there is no retry: triaged messages count as
  seen. Reason: no retry loop that could burn credits or spam.
- 2026-10-01 (M4): Inbox triage runs only when the synced unread Gmail records contain ids not yet
  triaged (state keeps ids that are still unread). Plain code drops bulk mail, merges threads and
  duplicates, and ranks by sender importance (people graph: confirmed person 3, calendar-vouched 2),
  high-signal wording, threads awaiting reply, and mentions of open tasks and close dates. At most 12
  items go to one pass as numbered `from | subject | snippet` lines (each field one line and capped, the
  snippet at 200 chars). Message bodies never reach the model, and email text is HTML-escaped inside
  `<untrusted-data>` tags in the tail, after an instruction that it is data. If none are worth it the
  model answers `none` and nothing is delivered. Audit rows hold counts and flags, never message text.
- 2026-10-01 (M4): The brief and weekly review skip the model pass when there is nothing to reword (an
  empty brief or a quiet week). The weekly review reads local tables (tasks, reminder jobs, synced calendar
  events, the usage meter) and takes pull requests from an optional callable, because the GitHub transport
  is a connector concern (task 4.4); without it the review omits pull requests.
- 2026-10-01 (M4): The ChatGPT plan scope is only granted to Plus and Pro but does not say which, and the contract's
  `PlanEligibility` has no "eligible, tier unknown" value while the UI blocks onboarding unless it is `eligible_plus`
  or `eligible_pro`. So a signed-in account that granted the plan scope reports `eligible_plus` with the label "Using
  ChatGPT plan" (no tier name). `credits_enabled` is false unless the provider exposes a `credits_enabled` attribute
  (it does not yet). A plan-limit pause is reported as `signed_in` with an `error` note, since the contract has no
  paused state.
- 2026-10-01 (M4): Conversations are persisted by `ChatHub` through a `ConversationRecorder` (migration 0023): the user
  message when a task starts, the assistant reply (final text, usage stamp, tool calls, approval card) as events are
  published. Assistant message ids are `<user message id>_a`. Companion Reset deletes conversations, agent tasks and
  steps, cancels scheduled agent tasks and rejects pending approvals, appends a `companion_reset` audit record, and never
  touches `tool_runs`; memory is cleared only when `forget_memory` is true.
- 2026-10-01 (M4): Connection ids are the app names (`gmail`, `google_calendar`, `github`). Syncing and credential
  revocation are injected through `ApiContext.extras` (`connector_syncers`, `connector_disconnectors`); with no syncer
  registered, sync answers 409 `sync_unavailable`. Disconnect deletes the app's `sync_state` and `connector_records`
  rows, and with "forget everything learned from this app" tombstones every memory whose source event came from it.
- 2026-10-01 (M4, security review): Signing in replaces the M3 cookie flow above. The UI and the desktop
  app hold only session tokens: in daemon memory, expire after seven days, die on every restart,
  revoked at /logout. The access token never travels: a client proves it holds it by answering a
  single-use challenge with an HMAC, after the daemon proves itself with an HMAC of the client's nonce
  (`/v1/session/challenge`, `/v1/session`). Browsers sign in only through `opendot open`, which prints a
  single-use two-minute code (60 bits, lockout after ten wrong guesses) to type into `/login`. No page
  ever asks for the access token, and no credential is put in a URL or a cookie. Reason: v0.1 security
  review S1, S2, S8, S10, S12 (another local process can take port 8765 when the daemon is down).
- 2026-10-01 (M4, security review): The agent never sends email in v0.1 (`message_send_propose` is not
  offered and `gmail_message_send` approvals are refused by its executor); Google connections, scopes,
  syncs and write switches are per app. Reason: S7, S9, S11.
- 2026-10-02: Data moves out of the working directory. The default data folder is the OS app-data folder
  the service and desktop app already use (`%APPDATA%\org.opendot.desktop`, `~/Library/Application
  Support/org.opendot.desktop`, `$XDG_DATA_HOME/org.opendot.desktop`), overridable with
  `OPENDOT_DATA_DIR`; `opendot.db` and `chatgpt_plan.json` live there. `OPENDOT_DB_PATH` and `--db` still
  win. Reason: `.opendot/` relative to the current directory meant different data per start folder, and it
  sat inside the source checkout where it could be committed. `.opendot/` is now git-ignored. Supersedes the
  2026-09-30 data-directory entry above.
