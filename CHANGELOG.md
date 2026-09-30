# Changelog

All notable changes to OpenDot are documented here. Format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/); versioning follows
[Semantic Versioning](https://semver.org/) (while pre-1.0, a breaking change
only bumps the minor version; see RELEASING.md).

Alfred's history before the fork is in
[docs/alfred-changelog.md](docs/alfred-changelog.md).

## [Unreleased]

### Changed

- Forked from Alfred. The Python package is now `opendot_core` (distribution
  `opendot-core`) under `daemon/`, and the CLI is `opendot`. Data lives in
  `.opendot/opendot.db`; environment variables are `OPENDOT_*`.
- Alfred's Calendar/Canvas "academic" rollups became Calendar-only
  `calendar_history` (migration 0018 renames the tables).
- `runtime_control` no longer depends on Windows; the watchdog restarts the
  daemon through the command in `OPENDOT_RESTART_COMMAND`.
- `opendot watchdog-check` takes `--chat-id` instead of reading a service
  config file.
- Learning passes (`--learning`) are off by default.

### Added

- `agent/`: deterministic agent plumbing mined from Alfred's Hermes bridge
  (context packing with a 10,000-character cap, prompt redaction, direct
  answers, mail ranking, style helpers, usage caps, tool groups of at most 8
  tools, and the `AgentBridge` driver).
- `opendot calendar-history-rebuild`.
- `.gitleaks.toml`, `.gitleaksignore` and a `ci.yml` that runs ruff, pytest and
  gitleaks (Linux on pull requests; Linux, macOS and Windows on `main` and
  nightly).

### Removed

- Hermes integration (the Hermes bridge, tool selection and MCP registration,
  the turn handshake, preflight and latency modules) and the `hermes-profile/`
  directory (its skills and writing rules moved to `docs/optional/`).
- Canvas, Canvas iCal, Google Health and BrowserOS connectors, and their data
  (migration 0018).
- The Windows service, scheduled-task scripts and `scripts/*.ps1`.
