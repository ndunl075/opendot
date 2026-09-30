# OpenDot

OpenDot is a free, open-source, self-hosted personal agent in the spirit of
ChatGPT Dots. It runs on your own computer, stays on while that computer is
awake, remembers things, runs scheduled work, reads your connected apps, and
asks before it acts.

> OpenDot is an independent open-source project. It is not affiliated with,
> endorsed by, or sponsored by OpenAI or Anthropic. ChatGPT is a trademark of
> OpenAI.

**Status: early development (milestone M0 done: the Alfred fork is cleaned and
renamed).** There is no chat, model sign-in or UI yet; those land in milestones
M1 to M3. See [ARCHITECTURE.md](ARCHITECTURE.md) for the plan and
[STATUS.md](STATUS.md) for where things stand.

## Requirements

- A **ChatGPT Plus or Pro** plan. OpenDot's default model access is the
  official "Sign in with ChatGPT" program, which OpenAI only offers to Plus and
  Pro plans. Free and Go accounts can sign in but cannot share plan usage with
  apps.
- Python 3.12 and [`uv`](https://docs.astral.sh/uv/) to build from source.
- macOS, Windows or Linux.

Everything else (API keys, local models, other providers) is opt-in and off by
default.

## What is in the repository today

- `daemon/`: the Python daemon (`opendot_core`), forked from
  [Alfred](https://github.com/ndunl075/alfred): approvals, a durable job
  runner, scheduled tasks, reminders, a memory graph, an audit log, and Gmail,
  Calendar and GitHub connectors. The agent loop, model router, providers and
  API arrive in M1 and M2.
- `docs/`: the decision log, cross-vendor reviews, and Alfred's original
  design documents for reference.

## Developing

```bash
uv sync --project daemon
uv run --project daemon pytest daemon/tests -q
uv run --project daemon ruff check daemon
gitleaks git --no-banner
```

Read [CONTRIBUTING.md](CONTRIBUTING.md) first, including its "never" list.
Coding agents: read ARCHITECTURE.md section 0 before doing anything.

## License

Apache-2.0. See [LICENSE](LICENSE) and [NOTICE](NOTICE).
