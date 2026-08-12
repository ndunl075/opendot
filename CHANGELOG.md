# Changelog

All notable changes to this project are documented here. Format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/); versioning follows
[Semantic Versioning](https://semver.org/) (while pre-1.0, a breaking change
only bumps the minor version — see RELEASING.md).

## [Unreleased]

### Added

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
