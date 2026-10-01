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
