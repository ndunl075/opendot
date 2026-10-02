# OpenDot status

Milestone: **M4 (always-on, connectors) — DONE. v0.1 release candidate ready** (2026-10-01; M0 to M3 done 2026-09-30). First live use 2026-10-01/02 (see **Live use**). What is left before a public v0.1 is under **Needs you**.

## Test-count baseline (recorded at the start of M0, from Alfred at commit 4ec3589)

- Collected after M0: 722; after M1: 921; after M2: 1189; after M3: 1210; after M4: 1453 daemon tests (1447 run on Windows, 6 POSIX-only) plus 196 UI unit and 14 end-to-end tests (minimum stays 650)
- Collected tests at baseline: **777**
- Tests inside the deletable files listed in ARCHITECTURE.md §5: **127**
- Minimum collected count from now on (baseline − deletable): **650**

## Progress

- [x] 0.1 baseline recorded
- [x] 0.2 monorepo layout (`daemon/`), `alfred` → `opendot_core`, CLI → `opendot`
- [x] 0.3 mine `hermes_bridge.py` into `agent/` (incl. `AgentBridge`, the driver Hermes's bridge provided)
- [x] 0.4 apply §5 (delete, change, forward migration 0018; Slack/Telegram code and tests kept)
- [x] 0.5 remove Windows-only code (Linux/macOS run in CI; only Windows was run locally)
- [x] 0.6 ruff, gitleaks, ci.yml
- [x] 0.7 docs (README, NOTICE, CLAUDE.md, AGENTS.md, decisions.md, CHANGELOG)
- [x] 0.8 fixture scan for real personal data
- [x] 0.9 Codex CLI flags and model IDs (docs/decisions.md)
- [x] 0.10 cross-vendor review into `docs/reviews/m0.md` (all findings fixed or accepted)

- [x] 1.1 API contract (`api/`, `contract/`, `opendot contract export`)
- [x] 1.2 `opendot mock-server [--check]`
- [x] 1.3 provider interface, errors, registry (never switch on error)
- [x] 1.4 `chatgpt_plan` provider (synthetic fixtures until the first real sign-in)
- [x] 1.5 opt-in providers `openai_key`, `anthropic_key`, `openrouter`, `local` (all off by default)
- [x] 1.6 `opendot measure` (`--dry-run` works without an account)
- [x] 1.7 cross-vendor review into `docs/reviews/m1.md` (all findings fixed or accepted; F3/F4 carried into M2)

- [x] 2.1 scenario suite S1-S10 (`opendot eval --suite core`) with a scripted fake provider
- [x] 2.2 agent loop (`agent/loop.py`, migration 0021): durable steps, resume after restart, tool group per task
- [x] 2.3 context packer (`agent/packer.py`): cache-friendly layout, budgets, compaction, trimming
- [x] 2.4 router (`router/`): section 7.1 table, tier discovery, one-step escalation, top tier asks first
- [x] 2.5 usage meter and budgets (`usage/`, migration 0019), daily hard stop
- [x] 2.6 rules engine (`rules/`, migration 0020): four behaviors, core deny list, always-allow
- [x] 2.7 reviewer pass and anomaly auto-pause (`agent/reviewer.py`), kill switch
- [x] 2.8 API server (`api/server.py`, `api/escrow.py`): chat WebSocket and approval endpoints
- [x] 2.9 cross-vendor review into `docs/reviews/m2.md` (F1 to F16 all fixed, Sol verdict "ready"; one residual risk accepted and carried to M4 tasks 4.4 and 4.8)

- [x] 3.1 TypeScript types from `contract/` with a drift check in the build (Terra)
- [x] 3.2 design system: tokens, light/dark, IBM Plex Sans, Lucide, code-drawn avatar, app mark (Astra)
- [x] 3.3 all v0.1 screens against the mock server (Astra), including continue anyway, allow top tier and resume after the plan limit
- [x] 3.4 Vitest component tests and Playwright end-to-end tests (Terra)
- [x] 3.5 `opendot serve`: UI and API on 127.0.0.1, token in the OS keychain (`opendot api-token show`)
- [x] 3.6 Tauri shell: PyInstaller sidecar, start or attach, tray (Open, Pause, Resume, Quit), NSIS installer
- [x] 3.7 review of the UI code, accessibility and contract usage into `docs/reviews/m3.md` (U1 to U15 fixed, verdict "ready"; contract gaps for M4 accepted)

## M4 progress

- [x] 4.1 `opendot service install|uninstall|status` (`service.py`): launchd user agent, Task Scheduler at-logon task, systemd user unit; no sudo or admin. `opendot serve` now also runs the always-on loop in a background thread (`always_on.py`), so one service gives the API, the UI and the loop (`--no-background` turns the loop off)
- [x] 4.2 keep-awake (`keep_awake.py`): off by default, "only while plugged in", per-OS lock (SetThreadExecutionState, `caffeinate -i -w`, `systemd-inhibit --what=idle`), read from `SettingsStore("keep_awake")`, re-evaluated every 15 s, no model calls
- [x] 4.3 catch-up after sleep or downtime (`catch_up.py`): late reminders via `JobRunner`, one run per recurring check, each connector syncs once, one outbox note ("While your computer was asleep: ...")
- [x] 4.6 `opendot doctor` (`--ci`, `--json`; `doctor.py`): database and migrations, keychain, access token, daemon, service, ChatGPT sign-in, connectors, disk, backups, restore drill, headless secrets file. `--ci` exits 0 on a fresh temporary database and skips the checks that need your setup
- [x] 4.7 `deploy/`: server guide (systemd user unit and Docker, both 127.0.0.1 only), Tailscale guide, headless secrets (systemd-creds, Docker secret), SSH-forward ChatGPT sign-in, section 11 caveat; `daemon/tests/test_deploy_files.py` checks the Docker files. Gaps recorded in `docs/decisions.md` (no keychain in Docker for ChatGPT tokens; Host header behind `tailscale serve`)
- [x] 4.5 built-in routines (`routines/`): morning brief, inbox triage, weekly review. Settings in `SettingsStore`, off by default, run from the always-on loop, quiet hours respected, one optional cheap/low pass through the agent loop (budgets, plan-limit pause and kill switch apply), plain-text fallback, `opendot routines list|run|enable|disable`. 28 new tests (1242 daemon tests pass). Not wired yet: a GitHub pull-request callable for the weekly review (needs task 4.4's connector).
- [x] 4.4 connectors into the agent: OpenDot's MCP tools are the agent's real tools, each declaring whether it writes and whether it reaches other people (`agent/mcp_tools.py`). Gmail, Calendar and GitHub connect from the UI: your own Google OAuth client, read-only by default and per app, with a write opt-in per app. GitHub uses a personal access token. Sync now and disconnect are real.
- [x] Every contract endpoint the UI uses is real (route modules in `api/routes/`: rules, usage, settings, providers, backup, onboarding, ChatGPT sign-in, companion, conversations, activity, memory, connections). Reminders, routine results and catch-up notes reach the UI ("From your companion").
- [x] 4.8 security review of all of v0.1 into `docs/reviews/security-v0.1.md`: S1 to S13 fixed over five verification rounds; Sol's verdict "ready". Browser sign-in is now `opendot open` (one-time code); UIs hold only session tokens.
- [x] CI: a UI job (lint, tests, build, Playwright e2e) and `opendot doctor --ci` on every OS.

## Live use (2026-10-01/02)

First real runs on the owner's Windows machine. Fixed, each through a PR with tests:

- [x] ChatGPT sign-in works live (Plus plan; chat on `gpt-5.6-luna`). Windows Credential Manager rejects values over 2560 bytes, so long secrets are split into parts (`secret_store.py`, PR #11).
- [x] Chat replies stream under their own id (`<message id>_a`, `api.events.assistant_id`), so the UI never overwrites the user's bubble; follow-up messages carry the last 20 turns of the conversation (`start_task(prior_turns=...)`), PR #16.
- [x] Data lives in the per-user app folder (`config.data_dir()`: `%APPDATA%\org.opendot.desktop` etc., `OPENDOT_DATA_DIR` overrides), never the working directory; `.opendot/` is git-ignored (PR #17).
- [x] UI: ChatGPT-style restyle approved by the owner (ARCHITECTURE.md §0, PR #14), original clay characters and pixel pets, customize dialog, Google setup checklist with JSON upload, ChatGPT button opens its own tab, chat polish and shared dropdowns (PRs #12, #15, #18). UI by Astra via Codex.
- [x] Mock server remembers finished onboarding (PR #13).

## Notes for whoever continues (Claude or Codex)

- `agent/runtime.py` `build_agent_runtime` is the production assembly of the agent loop and the API. M3 task 3.5 serves `runtime.app` on 127.0.0.1. The browser WebSocket authenticates with the subprotocols `["opendot", "opendot.bearer.<token>"]` from a local origin.
- The always-on daemon is `opendot serve` (service via `opendot service install`): UI, API, agent with real tools, and the background loop in one process. `opendot run` remains for the older Telegram setup; do not run both at once.
- UI work goes to Astra: `codex exec -m gpt-6-astra -c model_reasoning_effort=high -s workspace-write -c 'windows.sandbox="unelevated"' -c sandbox_workspace_write.network_access=true -C <worktree> -i <screenshot> -o out.md - < brief.txt`, in a separate git worktree, then review, PR and merge after CI. `.claude/launch.json` has `mock-api` (8787), `ui` (5173) and `daemon` (8765) configurations.
- Codex on this machine runs with `-c 'windows.sandbox="unelevated"'` (the default elevated sandbox cannot start processes); see docs/decisions.md for the exact `codex exec` command.

## Needs you

**v0.1 release candidate:** these are what stand between this release candidate and a public v0.1.

1. **Google OAuth setup (client created; Gmail and Calendar not yet connected live):** create your own Google OAuth client ("Desktop app", in your own Google Cloud project) and connect Gmail and Calendar from Connections in the app (the screen walks you through it). Optionally save a GitHub personal access token there too.
2. **A few days of real use:** sign in with ChatGPT (onboarding), run `opendot service install` so it stays on, use chat, approvals, reminders and the routines, and note anything rough.
3. **The publish decision:** whether and when to make the repository public and tag `v0.1.0` (ARCHITECTURE.md section 0 says only you decide this).
4. **Code signing (before any public release):** the Windows installer (`desktop/src-tauri/target/release/bundle/nsis/OpenDot_0.1.0_x64-setup.exe`) is unsigned, and macOS needs signing and notarization. Both need paid certificates and your accounts.

5. **Live ChatGPT sign-in: sign-in and plain chat verified live 2026-10-02.** Still open: the `chatgpt_plan` provider has only been tested against a fake OAuth server and synthetic streaming fixtures. After signing in once, real recorded fixtures should replace the synthetic ones in `daemon/tests/providers/fixtures/chatgpt_plan/`. The `function_call` / `function_call_output` input items are unverified until then.
6. **Run `opendot measure`** after signing in (a few minutes). It writes `docs/measurements.md` (caching, reasoning effort, structured output, model catalog incl. Astra, WebSocket cost, credit-spend check). Set a weekly OpenDot limit in ChatGPT Settings, Usage, and keep credit use off first.
