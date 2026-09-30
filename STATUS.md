# OpenDot status

Milestone: **M1 (contract, providers, ChatGPT plan sign-in) — DONE** (2026-09-30; M0 done the same day). Next: M2.

## Test-count baseline (recorded at the start of M0, from Alfred at commit 4ec3589)

- Collected after M0: 722; after M1: 921 (minimum stays 650)
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
- [ ] 2.9 cross-vendor review into `docs/reviews/m2.md` (in progress)

## Needs you

1. **Live ChatGPT sign-in** (needs your Plus or Pro account and a browser): the `chatgpt_plan` provider has only been tested against a fake OAuth server and synthetic streaming fixtures. After signing in once, real recorded fixtures should replace the synthetic ones in `daemon/tests/providers/fixtures/chatgpt_plan/`. The `function_call` / `function_call_output` input items are unverified until then.
2. **Run `opendot measure`** after signing in (a few minutes). It writes `docs/measurements.md` (caching, reasoning effort, structured output, model catalog incl. Astra, WebSocket cost, credit-spend check). Set a weekly OpenDot limit in ChatGPT Settings, Usage, and keep credit use off first.
