# OpenDot status

Milestone: **M0 (fork Alfred and clean it)** — in progress

## Test-count baseline (recorded at the start of M0, from Alfred at commit 4ec3589)

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
- [ ] 0.10 cross-vendor review into `docs/reviews/m0.md`

## Needs you

(nothing yet)
