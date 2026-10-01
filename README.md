# OpenDot

OpenDot is a free, open-source, self-hosted personal agent in the spirit of
ChatGPT Dots. It runs on your own computer, stays on while that computer is
awake, remembers things, runs scheduled work, reads your connected apps, and
asks before it acts.

> OpenDot is an independent open-source project. It is not affiliated with,
> endorsed by, or sponsored by OpenAI or Anthropic. ChatGPT is a trademark of
> OpenAI.

**Status: early development.** The daemon, model access and agent loop are
built (milestones M0 to M2). The desktop and web UI arrive in M3, and the
always-on service and connector setup in M4. See
[ARCHITECTURE.md](ARCHITECTURE.md) for the plan and [STATUS.md](STATUS.md) for
where things stand.

## How it works

- **Your ChatGPT plan pays for the thinking.** By default OpenDot uses the
  official "Sign in with ChatGPT" program, so model calls count toward your
  own plan. API keys, OpenRouter and local models are opt-in and off by
  default. OpenDot never switches providers or turns on paid usage on its own.
- **Cheap by default.** Plain code handles everything it can (reminders,
  due dates, "did anything change?"). The cheapest model tier handles the
  rest. A task moves up one tier only after a failure, and the top tier waits
  for your OK.
- **Budgets you set.** Every call is metered in credits per task and per day.
  Hitting the daily budget pauses all model requests, and hitting a task
  budget pauses that task until you say "continue anyway". A plan usage limit
  (429) pauses everything until you resume.
- **Asks before it acts.** Every tool call goes through rules with four
  behaviors: do it, do it only if you asked for exactly this, ask first, or
  hand it back to you. Deleting data in outside apps, spending money and
  security or password changes are never done for you, and no rule can change
  that. Sending and posting ask every time. Actions that need approval get a
  reviewer pass first, and the model can never approve its own proposals.
- **Survives restarts.** Tasks are stored step by step, so after a crash or
  restart OpenDot picks up where it left off without repeating finished
  actions.
- **Stays local.** Your data lives in a local SQLite database, secrets live in
  the OS keychain, and the API only listens on 127.0.0.1 with a bearer token.

## Requirements

- A **ChatGPT Plus or Pro** plan. OpenDot's default model access is the
  official "Sign in with ChatGPT" program, which OpenAI only offers to Plus and
  Pro plans. Free and Go accounts can sign in but cannot share plan usage with
  apps.
- Python 3.12 and [`uv`](https://docs.astral.sh/uv/) to build from source.
- macOS, Windows or Linux.

## What is in the repository today

- `daemon/`: the Python daemon (`opendot_core`):
  - `providers/`: ChatGPT plan sign-in and the opt-in providers.
  - `agent/`: the agent loop, prompt packer and reviewer.
  - `router/`: model tier routing.
  - `usage/`: the usage meter and budgets.
  - `rules/`: the rules engine and core deny list.
  - `api/`: the local API server and chat WebSocket.
  - Supporting pieces: approvals, a durable job runner, reminders, a memory
    graph, an audit log, and Gmail, Calendar and GitHub connectors.
- `contract/`: JSON Schemas for every API endpoint and stream event (the UI is
  built against these).
- `docs/`: the decision log and cross-vendor reviews.

## Developing

```bash
uv sync --project daemon
uv run --project daemon pytest daemon/tests -q
uv run --project daemon opendot eval --suite core
uv run --project daemon ruff check daemon
gitleaks git --no-banner
```

Useful commands:

- `opendot eval --suite core`: runs the agent scenario suite against a
  scripted fake model. No account needed.
- `opendot mock-server`: serves fake data for every API endpoint, for UI work.
- `opendot contract export`: regenerates `contract/`.
- `opendot measure --dry-run`: previews the one-time plan measurement run.

Read [CONTRIBUTING.md](CONTRIBUTING.md) first, including its "never" list.
Coding agents: read ARCHITECTURE.md section 0 before doing anything.

## License

Apache-2.0. See [LICENSE](LICENSE). [NOTICE](NOTICE) lists the projects
OpenDot builds on.
