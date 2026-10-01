# OpenDot status

Milestone: **M3 (UI, desktop and web) — DONE** (2026-09-30; M0 to M2 done the same day). Next: M4 (always-on, connectors, v0.1 release candidate).

## Test-count baseline (recorded at the start of M0, from Alfred at commit 4ec3589)

- Collected after M0: 722; after M1: 921; after M2: 1189; after M3: 1210 daemon tests plus 177 UI unit and 12 end-to-end tests (minimum stays 650)
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

## Notes for M3 and M4

- `agent/runtime.py` `build_agent_runtime` is the production assembly of the agent loop and the API. M3 task 3.5 serves `runtime.app` on 127.0.0.1. The browser WebSocket authenticates with the subprotocols `["opendot", "opendot.bearer.<token>"]` from a local origin.
- `opendot run` still builds no agent (M0 review F1). It gets real connector tools in M4 task 4.4. That task should also declare, for each tool, whether it reaches other people (the M2 review's residual risk).
- Codex's Windows sandbox stopped launching commands during the M2 review, so Sol reviews now receive the diff inside the prompt. If Sol needs to run commands again, restart the Codex CLI or the machine first.

## Needs you

0. **Code signing (before any public release):** the Windows installer (`desktop/src-tauri/target/release/bundle/nsis/OpenDot_0.1.0_x64-setup.exe`) is unsigned, and macOS needs signing and notarization. Both need paid certificates and your accounts.

1. **Live ChatGPT sign-in** (needs your Plus or Pro account and a browser): the `chatgpt_plan` provider has only been tested against a fake OAuth server and synthetic streaming fixtures. After signing in once, real recorded fixtures should replace the synthetic ones in `daemon/tests/providers/fixtures/chatgpt_plan/`. The `function_call` / `function_call_output` input items are unverified until then.
2. **Run `opendot measure`** after signing in (a few minutes). It writes `docs/measurements.md` (caching, reasoning effort, structured output, model catalog incl. Astra, WebSocket cost, credit-spend check). Set a weekly OpenDot limit in ChatGPT Settings, Usage, and keep credit use off first.
