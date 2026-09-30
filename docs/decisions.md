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
