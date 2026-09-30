# OpenDot architecture

Status: approved plan, nothing built yet<br>
Last updated: 2026-09-30<br>
Owner: the repository owner (the "user" below). Builders: AI coding agents, mostly unattended via `/goal`.

OpenDot is a free, open-source, self-hosted personal agent in the spirit of ChatGPT Dots. It runs on the user's own computer, is always on while that computer is awake, remembers things, runs scheduled work, uses connected apps, and asks before acting. It runs on the user's own ChatGPT Plus or Pro plan through OpenAI's official "Sign in with ChatGPT" program, not on paid API credits.

It is a hobby project. It is not monetized, not hosted for other people, and not affiliated with OpenAI or Anthropic.

> Canonical rule, inherited from Alfred: store broadly, retrieve narrowly, act cautiously, and never call a model when code can answer.

---

## 0. Read this first (for coding agents)

This file is the source of truth. If code and this file disagree, this file wins until the user changes it.

**Before starting any work:** read §1 (locked decisions), §13 (who builds what), and your milestone in §14. Then read `STATUS.md` to see what is already done.

**While working:**
- Work on one task at a time, on a branch named `m<milestone>/<short-task-name>`.
- Write or update tests first when the task changes behavior.
- A task is done only when its "done when" commands exit 0 **and you have printed their output** in the conversation. The `/goal` evaluator only sees what you print; it cannot run commands or read this file.
- Update `STATUS.md` at the end of every task: what finished, what is in progress, what is blocked on the user.
- Record any new design decision in `docs/decisions.md` with the date and a one-line reason. Decisions in §1 are not yours to change.
- Run commands from the repository root. Python commands use `uv run --project daemon …`.

**Stop and ask the user only for these.** Everything else, decide yourself, write the decision down, and keep going.
1. Anything that needs the user's own accounts, a browser sign-in, or a secret (ChatGPT sign-in, Google OAuth client, Slack app, Apple or Windows signing certificates).
2. Publishing: making the repo public, tagging a release, registering package names, buying a domain.
3. Spending money of any kind.
4. Changing a locked decision in §1.
5. Deleting anything outside the repository, rewriting published git history, or force-pushing `main`.
6. Anything that needs admin rights (sudo) on the build machine.

When you hit one of these, write it under **Needs you** in `STATUS.md`, then continue with any other task that is not blocked. Don't sit idle.

**Never do these, even if a task seems to require it:**
- Build a "log in with Claude" flow, read another app's credential files (`~/.claude`, `~/.codex`, browser cookies), spoof client headers, or proxy anyone's tokens. This is what got other open-source tools shut down (§6.3).
- Turn on any paid path by default: API keys, ChatGPT credits after the plan limit, or paid fallbacks.
- Commit secrets, tokens, or real personal data. Alfred's fixtures were audited once; audit again when copying them. The fake tokens in the redaction tests are allowed (listed in `.gitleaksignore`) and must not be deleted.
- Copy OpenAI's visual design, icons, mascots or logos. The only OpenAI-branded elements allowed are the ones "Sign in with ChatGPT" requires, taken from OpenAI's DevKit under its license (§12).
- Skip, delete, or weaken tests to make a goal pass. The only test files that may be deleted are the ones listed in §5.
- Write UI code as a Claude model. The UI belongs to Astra and Terra (§13). If Codex isn't available, stop and ask.

---

## 1. Locked decisions

| # | Decision | Notes |
|---|---|---|
| D1 | **Name: OpenDot.** Hobby project, Apache-2.0, not monetized. | Other projects already use the name (`defog-ai/opendot` on GitHub, `opendot` on PyPI). So package names must differ: Python distribution `opendot-core`, import package `opendot_core`, npm packages under `@opendot/`. Every README and About screen carries the non-affiliation line in §12. |
| D2 | **Who can use it: ChatGPT Plus and Pro only.** | OpenAI only lets Plus and Pro share plan usage with apps. Free and Go users can sign in but can't use their plan (§6.1). No free tier, no workaround. |
| D3 | **Default model access: the user's ChatGPT plan via "Sign in with ChatGPT".** | The only model path turned on out of the box. |
| D4 | **Everything else is opt-in and off by default:** API keys (OpenAI, Anthropic, OpenRouter), local models (Ollama or any OpenAI-compatible local server), and the user's own Claude Code or Codex app run as a helper. | Opt-in means the user turns it on in Settings, sees what it costs, and can turn it off. Having a key saved never turns a feature on by itself; each feature that would use it has its own switch. Never suggested during onboarding. |
| D5 | **Never a "log in with Claude" button.** | Anthropic forbids third-party apps from offering Claude login or using Pro/Max credentials (§6.3). Claude is reachable only through an Anthropic API key or the user's own unmodified Claude Code app, both opt-in. |
| D6 | **No local models by default.** | Anything Alfred did with Ollama moves to plain code or the cheap ChatGPT model. Local models stay available as an opt-in. |
| D7 | **Foundation: fork Alfred's core. Drop Hermes entirely.** | Alfred (`ndunl075/alfred`, Apache-2.0) already has approvals, jobs, memory, audit, connectors and cost controls with about 700 tests. OpenDot replaces Hermes with its own agent loop and model router (§5, §7). |
| D8 | **Surfaces: desktop app + web page, both served from the user's own machine.** | Desktop is a Tauri 2 app. The same UI is served by the local daemon as a web page. Phone and other-device access goes through a private tunnel the user sets up (Tailscale or similar). No public hosted website. OpenAI requires approval for hosted apps. |
| D9 | **Always-on: a keep-awake setting plus catch-up on missed jobs.** A guide covers running it on a home server or cheap cloud server for true 24/7. | A laptop that sleeps still pauses OpenDot. Be honest about that in the UI. |
| D10 | **Original design, Lucide icons. GPT Astra builds the UI.** | §12, §13. |
| D11 | **Save plan usage by design** (§8): code before models, cheap model by default, cache-friendly prompts, budgets, never auto-spend. | |
| D12 | **Ask before acting.** Sending, posting, buying and account changes always need approval. Some actions always stay with the user. | §10. |
| D13 | **Platforms: macOS, Windows, Linux.** | Alfred is Windows-first. OpenDot must pass CI on all three. |
| D14 | **v0.1 scope:** chat, memory, tasks and reminders, schedules, approvals and rules, activity view, usage meter, reading Gmail and Calendar, GitHub notifications, desktop + web. | Google access is read-only by default. Creating Gmail **drafts** and Calendar **events** (Alfred's existing, approval-gated actions) is an opt-in switch that asks Google for write access. Sending email, Slack, Telegram, user-added MCP servers, browser/computer control, proactive research and phone access come in v0.2 and v0.3 (§14). |

---

## 2. What we are recreating (Dots, briefly)

ChatGPT Dots launched on September 29, 2026 for Pro and Business Premium. Each "dot" is an always-on agent with its own cloud computer and browser, thousands of app plugins, memory, scheduled and proactive work, and rules for when it may act on its own. OpenDot recreates the **behavior**, not the look.

| Dots feature | OpenDot version | Milestone |
|---|---|---|
| Named companion with a handle and avatar | Companion profile with a name and an original avatar drawn by code | M3 |
| Chat that remembers | Conversation + Alfred's memory graph | M2, M3 |
| Goals that progress between chats | Durable tasks that survive restarts | M2 |
| Scheduled reminders and recurring checks | Alfred's job runner and scheduled tasks | exists; UI in M3 |
| Custom Rules with four behaviors | Rules engine on top of Alfred's approvals (§10) | M2 |
| Activity view: In progress / Scheduled / Completed | Activity screen fed by tasks and the audit log | M3 |
| Pause and Reset | Pause the daemon; Reset wipes the companion's data | M2, M3 |
| Auto-review before risky actions | Reviewer model + deterministic checks | M2 |
| Connected apps | Gmail, Calendar, GitHub (from Alfred); user-added MCP servers later | M4, M5 |
| Proactive read-only research | Background jobs whose tools are read-only at the tool layer | M5 |
| Its own computer and browser | Sandboxed container with a headless browser and live view | M5 |
| Saved passwords the model never sees | Secrets broker with placeholders | M5 |
| Slack, texting, phone | Slack and Telegram (from Alfred); phone via private tunnel | M5, M6 |
| Multiple dots | Multiple companions | M6 |

---

## 3. System shape

```
┌─────────── Desktop app (Tauri 2) ───────────┐      ┌──── Any browser on the same machine ────┐
│ React UI (built by Astra)                    │      │ Same React UI, served by the daemon      │
│ Starts the daemon if it isn't running        │      │ Other devices: via Tailscale (opt-in)    │
└──────────────────────┬───────────────────────┘      └──────────────────┬──────────────────────┘
                       │  HTTP + WebSocket on 127.0.0.1, bearer token      │
┌──────────────────────▼───────────────────────────────────────────────────▼──────────────────────┐
│ OpenDot daemon (Python 3.12, package opendot_core, forked from Alfred)                          │
│                                                                                                 │
│ new:    api · agent (loop, context packer, tool groups, reviewer) · router · usage · providers  │
│         rules · keep_awake · service                                                            │
│ Alfred: jobs, outbox, scheduled tasks, quiet hours · events, memory graph, learning ·           │
│         gmail, calendar, github · policy, approvals, action executor · audit, backup ·          │
│         secret store (OS keychain)                                                              │
└───────────────┬─────────────────────────────────────────────┬───────────────────────────────────┘
                │                                             │
       SQLite (WAL, FTS5)                          v0.2: sandbox container
       one file, backed up nightly                 (headless browser, shell, live view)
```

New code goes in subpackages (`api/`, `agent/`, `router/`, `usage/`, `providers/`, `rules/`). Alfred's modules stay flat in `opendot_core/`. Don't reorganize them; it would break history and cost effort for no gain.

Two rules from Alfred carry over unchanged:
- **The daemon owns the data.** The UI, channels and any model are replaceable. None of them is the source of truth.
- **One job store.** All user schedules, reminders and retries live in the daemon's job runner. Nothing else keeps its own schedule.

---

## 4. Repository layout

```
opendot/
  ARCHITECTURE.md        this file
  STATUS.md              progress, test-count baseline, blockers, "Needs you" list
  CLAUDE.md, AGENTS.md   one paragraph each: "read ARCHITECTURE.md §0 first"
  README.md              what it is, requirements (ChatGPT Plus/Pro), non-affiliation line
  LICENSE, NOTICE        Apache-2.0; NOTICE keeps Alfred's copyright
  CONTRIBUTING.md        includes the "never" list from §0
  .gitleaksignore        the known fake tokens in redaction tests
  daemon/                Python package opendot_core (from Alfred's src/alfred)
    pyproject.toml       dev dependencies include pytest, ruff, pyinstaller
    src/opendot_core/
      migrations/        Alfred's 17 migrations + new forward-only ones
    tests/
  contract/              JSON Schemas for every API request, response and stream event
  ui/                    React + TypeScript + Vite (Astra)
  desktop/               Tauri 2 shell (src-tauri) that bundles ui/ and the daemon
  deploy/                server guide (systemd, Docker), Tailscale guide, Slack app manifest
  docs/
    decisions.md         dated decision log
    measurements.md      live measurements of the ChatGPT plan flow (§8.5)
    reviews/             cross-vendor reviews, one file per milestone
    alfred-architecture.md   Alfred's original design doc, for reference
    optional/            Obsidian vault sync, ChatGPT-as-MCP-client tunnel (from Alfred)
  .github/workflows/ci.yml
```

**Tooling:**
- Python: `uv`, `ruff`, `pytest`.
- UI: `pnpm`, Vitest, Playwright.
- Desktop: Rust stable (for Tauri).
- CI: `gitleaks` (v8.19 or newer).

**CI:** pull requests run Linux only. Pushes to `main` and a nightly run use the full ubuntu/macos/windows matrix. That keeps GitHub Actions minutes low while the repo is private; macOS minutes cost 10x.

---

## 5. What to keep, change and delete from Alfred

Alfred's modules are in `src/alfred/`. The tests that go with kept code are the main reason to fork instead of rewrite. Protect them.

**Keep as-is (rename the package only):**
`db`, `events`, `documents`, `audit`, `policy`, `policy_coverage`, `action_executor`, `outbox`, `jobs`, `scheduled_tasks`, `tasks`, `reminders`, `nags`, `important_dates`, `quiet_hours`, `brief_schedule`, `availability`, `wall_clock`, `destinations`, `config`, `secret_store`, `http_auth`, `backup`, `memory_graph`, `memory_learning`, `people`, `owner_identity`, `connector_records`, `gmail`, `gmail_backfill`, `gmail_inbound`, `threads`, `google_calendar`, `google_oauth`, `github`, `pull_requests`, `evaluation`, `implicit_feedback`, `response_feedback`, `workflow_learning`.

**Keep but disable until v0.2:** `slack`, `slack_socket`, `telegram`, `telegram_actions`, `telegram_bot`, `telegram_runtime`. Their code and tests stay and keep passing; they are just not started by default.

**Keep but change:**

| Module | Change |
|---|---|
| `cli.py` | Remove the commands for deleted modules. Rename to the `opendot` CLI. |
| `mcp_server.py` | Remove the Hermes tool-filter and `turn_handshake` inputs. Keep the server (loopback only, off by default) so Claude Desktop or Cursor can connect. |
| `briefing.py` | Remove the Canvas de-duplication (`academic_dedup`). The brief stays code-built. |
| `runner.py`, `runtime_control.py` | Remove `preflight` and `winservice` dependencies and Windows-only paths. The loop also drives the agent, keep-awake and catch-up. |
| `academic_memory.py` + `historical_memory.py` | Keep only the Calendar half. Rename to `calendar_history.py`. Rename the `academic_*` tables to `calendar_*` in a new forward migration. |
| `connector_capabilities.py`, `connector_health.py`, `telegram.py`, `jobs.py`, `composio.py` | Remove references to deleted connectors (Canvas, Google Health, BrowserOS, Hermes). |
| `models.py` | Split into `providers/`. Keep `Redactor` and the budget idea from `GuardedCloudProvider`. Ollama becomes the opt-in `local` provider. |
| `hermes_tools.py` | Becomes `agent/tool_groups.py`: deterministic tool-group selection, at most 8 tools per group. |
| `hermes_bridge.py` | Mine it for context packing, the 10,000-character context cap, prompt redaction, direct code answers (`_direct_answer`), mail ranking, escaping of synced content, and the monthly caps. Move those into `agent/`, then delete the file. |
| `admin_ui.py` + `templates/` | Replaced by the React UI. Turn its queries into API endpoints, then delete the templates. |
| `embeddings.py`, `vault.py`, `vault_sync.py`, `composio.py`, `journal.py` | Keep, off by default (opt-in). |

**Delete:** `hermes_bridge` (after mining), `hermes_mcp`, `turn_handshake`, `preflight`, `latency`, `canvas`, `canvas_ical`, `academic_dedup`, `google_health`, `browseros_health`, `winservice`, `admin_ui` (after porting), the `hermes-profile/` directory, `deploy/couchdb/` (moves to `docs/optional/`), and `scripts/*.ps1` (replaced by `opendot` commands).

Before deleting `hermes-profile/`:
- Port its three skills (morning brief, inbox triage, weekly review) as built-in routines.
- Keep its writing rules as an **optional** style preset. The default persona is neutral.

**Tests: the only files that may be deleted:**
`test_canvas.py`, `test_canvas_ical.py`, `test_google_health.py`, `test_browseros_health.py`, `test_winservice.py`, `test_latency.py`, `test_turn_handshake.py`, `test_hermes_mcp.py`, `test_lane_routing.py`, `test_tool_narration.py`, `test_preflight.py`, `test_runner_preflight.py`.

**Tests that must be ported, not deleted.** Change their imports, keep their assertions:
`test_hermes_bridge.py` (the parts that test context, redaction, direct answers and caps move to `agent/` tests), `test_send_email_flow.py`, `test_gmail_threads.py`, `test_scheduled_tasks.py`, `test_telegram_actions.py`, `test_academic_memory.py` and `test_historical_memory.py` (Calendar parts), `test_briefing.py`, `test_mcp_server.py`, `test_cli.py`, `test_admin_ui.py` (becomes API tests), `test_connector_capabilities.py`, `test_connector_health.py`, `test_telegram.py`, `test_runner.py`.

**Test-count rule:**
- At the start of M0, record in `STATUS.md` the collected test count, and the count inside the deletable files above.
- From then on, the collected count may never drop below *baseline − deletable*.

**Migrations:** keep Alfred's 17 migrations unchanged. Add new forward-only migrations for OpenDot changes, such as dropping Canvas and Health data and renaming tables. Never edit an old migration. The migrations folder is the one place deleted connector names may still appear, so the M0 leftover check skips it.

**New modules:** `api/`, `agent/` (loop, context packer, tool groups, reviewer), `router/`, `usage/`, `providers/`, `rules/`, `keep_awake.py`, `service.py` (per-OS install), `calendar_history.py`. In v0.2: `connectors/mcp_client.py`, `sandbox/`, `secrets_broker.py`.

---

## 6. Model access (providers)

### 6.1 Default: the user's ChatGPT plan ("Sign in with ChatGPT")

Verified on OpenAI's pages on 2026-09-30:
- **Who:** "the option to use your ChatGPT plan is only available with Plus and Pro." Anyone can sign in, but Free and Go users can't use their plan.
- **Which apps:** "open-source and locally hosted apps." Paid or remotely hosted apps must apply first. OpenDot qualifies as long as it stays open source and runs on the user's own machine.
- **What it uses:** "Eligible AI requests count toward the ChatGPT Work and Codex usage included in your plan." Users can set a weekly limit per app in ChatGPT Settings → Usage.
- **Credits:** "if you reach your plan limits, an app or site can also use available ChatGPT credits if you've explicitly allowed apps to use credits." That's real money.

What the `chatgpt_plan` provider must do (`providers/chatgpt_plan.py`):
1. **Host ID:** create and store a stable, opaque `ext_agent_host_id` before first sign-in (a `urn:uuid:` value is fine). Never derive it from an email or user ID.
2. **Sign-in:** OAuth with PKCE through a `127.0.0.1` loopback redirect and dynamic client registration, following OpenAI's Sign in with ChatGPT docs. No client secret, no API key. Sign-in must happen on the machine running the daemon.
3. **Plan check:** a successful sign-in alone does not grant plan usage. Check that the plan scope was granted and show a clear message if not (for example, the user is on Free).
4. **Tokens:** store refresh tokens in the OS keychain (§11 covers headless servers). The UI never receives them. Disconnecting revokes them.
5. **Models:** fetch the account's model catalog at startup and after sign-in. Never hard-code model names (§7.2).
6. **Requests:** Responses API with `store: false` and `stream: true` on every request. Send the needed history in `input` every time; don't send `previous_response_id` over HTTP. Treat a request as successful only after `response.completed`.
7. **Leave these out; the flow rejects them:** `background`, `conversation`, `max_output_tokens`, `max_tool_calls`, `metadata`, `moderation`, `multi_agent`, `prompt`, `prompt_cache_retention`, `safety_identifier`, `temperature`, `top_logprobs`, `top_p`, `truncation`, `user`.
8. **Tools that don't work in this flow:** image generation, file search, Code Interpreter, OpenAI's native computer use, hosted MCP/connectors, and `tool_search`. OpenDot's own tools are plain function tools, so this doesn't matter.
9. **Errors:** on `subscription_sharing_usage_limit_exceeded` (429), pause every plan request and show **Manage usage** (link to ChatGPT Settings → Usage) as the main action. Don't guess a reset time; a per-app limit may be what triggered it. Map `subscription_sharing_user_not_eligible` (403) and `subscription_sharing_unsupported_capability` (400) to clear messages. Never switch to another provider on error.
10. **Never spend credits.** If the user has let apps use ChatGPT credits, requests past the plan limit may be billed instead of returning a 429. So:
    - Onboarding tells the user to keep credit use off for OpenDot.
    - The daily budget (§8.3) is a hard stop that doesn't depend on getting a 429.
    - `opendot measure` checks whether the usage data shows credit spending, and warns if it does.
11. **Required UI:** a "Continue with ChatGPT" button, a "Using ChatGPT plan" label near the model name, and "Manage usage". Use the official DevKit assets under the DevKit's license (§12).

OpenAI's DevKit (`openai/sign-in-with-chatgpt-devkit`) ships a Node SDK. OpenDot's daemon is Python, so implement the flow in Python using the docs and the DevKit as a reference. Record any gap between the two in `docs/decisions.md`.

### 6.2 Opt-in providers (off by default)

| Provider | What it is | Notes |
|---|---|---|
| `openai_key` | OpenAI API key | Pay per token. |
| `anthropic_key` | Anthropic API key | The only way OpenDot itself calls Claude models. |
| `openrouter` | OpenRouter key | One key, many models. |
| `local` | Ollama or any OpenAI-compatible local server | Free, needs decent hardware. |
| `cli` | The user's own `claude` or `codex` app, run as a helper process for coding sub-tasks (v0.2) | OpenDot starts the unmodified app and reads its output. The user signs in inside that app. OpenDot never touches its credentials. |

Saving a key doesn't turn anything on. Each feature that could use an opt-in provider has its own switch, and each switch shows its cost:
- "Use a paid model when the plan runs out"
- "Use Claude as the reviewer"
- "Smarter memory search"

Every opt-in provider also has its own spend cap.

### 6.3 Why Claude is opt-in only

Anthropic's Claude Code legal page (checked 2026-09-30) says developers:
- "should use API key authentication";
- may not "offer Claude.ai login into their own applications";
- may not "route requests through Free, Pro, or Max plan credentials on behalf of their users";
- "may not collect, store, or intermediate Claude.ai credentials or session tokens".

It does allow a user to sign in to the **unmodified** Claude Code app themselves. OpenCode removed its Claude subscription support in 2026 after legal requests. OpenDot stays well inside these lines: Claude only through an Anthropic API key or the user's own Claude Code app, both opt-in.

---

## 7. Which model OpenDot uses for what (runtime)

This section is about OpenDot's own AI calls **at runtime**. Which AI builds OpenDot is in §13.

### 7.1 Routing table

| Job | Who does it | Model tier | Effort |
|---|---|---|---|
| Did anything change? (new mail, calendar edit, page diff) | Plain code | none | none |
| Due dates, reminders, ranking, deduplication, scheduling | Plain code | none | none |
| Simple lookups ("what's on today?") | Plain code (Alfred's direct answers) | none | none |
| Morning brief | Plain code (Alfred's deterministic brief). An opt-in "friendlier wording" switch adds one cheap/low pass. | none | none |
| Sorting incoming items, "is this worth the user's attention?" | Model | cheap | low |
| Summaries, memory extraction, compacting old context | Model | cheap | low |
| Normal chat reply | Model | cheap | low |
| Planning a multi-step task; each tool-using step | Model | cheap | medium |
| Reviewing a risky action before approval | Model + deterministic checks | mid | medium |
| After a failure | Model | next tier up | same effort, or one step higher |
| Deep work (mid already failed, or the user asked for it) | Model | top | high |

**Escalation:**
- Move up one tier after a failure (cheap → mid → top), never skipping a tier and at most once per step.
- Going to the top tier pauses the task for the user's OK, unless the user allowed automatic top-tier use in Settings.
- If top fails, hand the task to the user with what was tried.
- Runtime never uses `xhigh` or above.

**The reviewer is the one exception to "cheap by default".** It's a safety check on a short input, so it runs at mid. If the user turned on "Use Claude as the reviewer" (needs an Anthropic key), it uses Claude instead.

### 7.2 Tiers are discovered, not hard-coded

Model names differ across OpenAI's own pages. The Codex rate card lists "GPT-5.6 Sol/Terra/Luna", while the Sign in with ChatGPT docs use `gpt-6.1-sol` in an example. So:
- At startup, read the account's model catalog.
- Map by family name: `luna` → cheap, `terra` → mid, `sol` → top. If a tier is missing, use the next tier **up**.
- If the catalog includes Astra, don't route to it automatically until its rate is known (§8.5).
- The user can override the mapping in Settings.

---

## 8. Using as little of the plan as possible

### 8.1 How usage is charged

Plan requests count toward the user's Work/Codex allowance. OpenAI's Codex rate card prices usage in credits per million tokens:

| Model | Input | Cached input | Output |
|---|---|---|---|
| GPT-5.6 Sol | 125 | 12.5 | 750 |
| GPT-5.6 Terra | 50 | 5 | 300 |
| GPT-5.6 Luna | 5 | 0.5 | 30 |

Three facts drive every rule below:
- The cheap model costs about 25x less than the top one.
- Cached input costs 10x less than fresh input.
- Output costs 6x more than input.

Example: a 10-step task whose context starts at 12k tokens and grows 2k per step, with 600 output tokens per step:

| Model | No caching | Cached | Cached + compacted |
|---|---|---|---|
| Sol | 30.8 | 10.5 | 6.2 |
| Terra | 12.3 | 4.2 | 2.5 |
| Luna | 1.2 | 0.4 | 0.25 |

That's a ~120x spread for the same task. Once caching works, output is the biggest cost.

### 8.2 Rules (these are requirements, not tips)

1. **Code first.** Background checks are code. A model is called only when code finds something new or a person asked a question.
2. **Cheap by default.** Follow §7.1. Escalate on failure, never in advance.
3. **Cache-friendly prompt layout,** in this exact order:
   1. **Stable prefix:** rules, persona, and the tool group's schemas sorted by name. It changes only when the companion's settings change.
   2. **Task summary:** rewritten only at compaction points.
   3. **History:** append-only. Never edit or reorder past turns.
   4. **Changing tail:** current time, retrieved memories, the new message.
4. **Choose the tool group per task, not per turn.** Changing tools mid-task changes the prefix and breaks caching. Each group has at most 8 tools.
5. **Compact on purpose.** When history passes a threshold, summarize the older part with the cheap model once. One planned cache miss is cheaper than a growing history.
6. **Control output.** The flow doesn't allow a maximum output length, so keep effort low by default, ask for short answers, and use structured output for machine steps where the flow accepts it.
7. **Trim tool results before they reach the model:** extracted text instead of HTML, headers and snippets instead of full emails, capped lengths. Alfred's context budget carries over: about 3,000 tokens of extra context per call (persona 250, profile 350, tasks 400, memories 900, summary 700, reserve 400).
8. **Budgets and stops:**
   - A credit budget per task and per day (§8.3).
   - Pause immediately on a 429.
   - Never enable ChatGPT credits or paid fallbacks.
   - Onboarding asks the user to set a weekly limit for OpenDot in ChatGPT Settings → Usage, and to keep credit use off.
9. **Show the cost:** every model reply in the UI shows the model, effort and estimated credits. The Usage screen shows totals by task, day and job type.

### 8.3 Usage meter (`usage/`)

- After each `response.completed`, read the token counts (input, cached input, output) from the usage data.
- Multiply by the rate card and store the credits per call, per step, per task, and per day.
- The rate card lives in `usage/rate_card.toml` so it can be updated without code changes. Unknown models are marked "rate unknown" and count at the top-tier rate.
- **Default budgets: 5 credits per task, 50 per day.** These are placeholders until §8.5 measurements exist; the user can change them. From §8.1, a cheap-tier task costs about 0.25–1.2 credits and one mid-tier escalation 2.5–4.2, so both fit. Top-tier work (6–10) asks first anyway.
- The daily budget is a hard stop: when it's hit, all plan requests pause until the next day or until the user raises it.
- When a task budget is hit, the task pauses with a message and a "continue anyway" button. It never silently continues.

### 8.4 Keep-awake costs nothing extra

Keep-awake (§11) only keeps the daemon alive. It must not add model calls. Background work stays code-first.

### 8.5 Unknowns to measure (M1 builds the tool, the user runs it once)

`opendot measure` runs a short scripted session on the user's signed-in plan and writes the results to `docs/measurements.md`:
- Does automatic prompt caching happen in this flow? Look for cached input tokens on the second identical-prefix call.
- Is `reasoning.effort` accepted, and does it change output token counts?
- Is structured output accepted?
- Which models are in the catalog, and does Astra appear?
- Does WebSocket mode (which can refer to earlier responses on the same connection) cost less than resending history over HTTP?
- Does the usage data say anything about credit spending (§6.1 point 10)?

OpenAI doesn't say how many credits a Plus plan includes per week. Nobody can measure that from code, so budgets stay user-set.

---

## 9. Memory

Alfred's memory design carries over unchanged:
- **Four layers:** an immutable event log, documents, a derived memory graph, and a small working context.
- **Every fact carries its details:** source, confidence, valid dates and sensitivity (`public`, `personal`, `sensitive`, `secret`).
- **Inferred facts stay "candidate"** until confirmed or seen again.
- **Corrections replace old facts** without rewriting history.
- **Forgetting works by item, source, time range, topic or person.**

Change for OpenDot: **search is keyword-based by default** (SQLite FTS5). Vector search needs embeddings, and the ChatGPT plan flow only covers the Responses API. So Alfred's embeddings (`embeddings.py`, `sqlite-vec`) run only when the user turns on "Smarter memory search" and picks a local model or API key for it.

Better than Dots: disconnecting an app offers "forget everything learned from this app", because every memory knows its source.

---

## 10. Safety and rules

Alfred's approval flow is the base:
- **Propose → approve → execute.** Execution requires a one-time token, uses an idempotency key, and stores a receipt.
- **Crash-safe:** if the app crashes after the provider accepted an action, the stable provider ID finds that exact result, so nothing is duplicated.
- **The model can never approve its own proposal.**

In v0.1, approvals happen in the OpenDot UI (desktop or web). Slack and Telegram approvals come back in v0.2.

**Rules** (`rules/`) add the four Dots behaviors on top. A rule matches on tool, action, target, data sensitivity and cost, and chooses one of:

| Behavior | Meaning |
|---|---|
| `auto` | Do it without asking. |
| `auto_if_preapproved` | Do it only if the user explicitly asked for this exact action in their own message. |
| `ask` | Create a proposal and wait for approval. |
| `handoff` | Don't do it. Tell the user what to do themselves. |

Defaults, from Alfred:

| Action by the companion | Default |
|---|---|
| Reading and syncing connected apps | auto |
| Saving the user's messages, low-risk memory | auto (visible in the audit log) |
| Local tasks and reminders | auto (reversible) |
| Creating Calendar events, Gmail drafts, GitHub issues, file changes | ask |
| Sending email or messages, posting, publishing | ask, every time (v0.2) |
| Deleting data in outside apps, spending money, security, credential or password changes | handoff (core deny list; no rule can change this) |

Forget, Reset and cancelling a reminder are the user's own actions in the UI, not the companion's, so the deny list doesn't block them.

**Other safety rules:**
- **Reviewer:** every `ask` action gets a reviewer pass before the approval card is shown (§7.1). The card shows the reviewer's note.
- **Untrusted content:** emails, web pages, issues and documents are treated as untrusted. They cannot change rules or approve anything. Keep Alfred's escaping of synced content in prompts.
- **Proactive work is read-only at the tool layer (v0.2).** The prompt is not trusted to keep it read-only.
- **Kill switch:** Pause stops the agent loop and holds the outbox. Automatic pause on unusual behavior: a new recipient domain, many actions in a row, or a budget spike.
- **Secrets:**
  - OS keychain, never SQLite, logs, prompts or git.
  - The UI never sees provider tokens.
  - In v0.2, website passwords go through a placeholder broker, so the model never sees them.
- **Audit:** the hash-chained audit log and encrypted nightly backup with restore drills carry over from Alfred.

---

## 11. Always-on, desktop and web

**Daemon as a service** (`opendot service install|uninstall|status`):

| OS | Service type | Keep-awake method |
|---|---|---|
| macOS | launchd user agent | power assertion (`caffeinate -i` child process or IOKit) |
| Windows | Task Scheduler "at logon" task | `SetThreadExecutionState` |
| Linux | systemd user unit | `systemd-inhibit` |

**Keep-awake setting:**
- Off by default. When on, the daemon holds a "don't idle-sleep" lock.
- The UI explains two things: closing a laptop lid usually sleeps it anyway, and the setting drains the battery. Offer "only while plugged in".

**Catch-up:**
- On start or wake, run missed jobs with Alfred's "late" note.
- Tell the user once what was missed.
- Never replay more than one run of a recurring check; skip the stale ones.

**True 24/7 (guide in `deploy/`):**
- Run on a home server or a small Linux VPS (systemd or Docker), reached through Tailscale.
- **Sign-in:** the Sign in with ChatGPT redirect goes to `127.0.0.1` on the daemon's machine. On a headless server, sign in through an SSH port-forward. Document the steps.
- **Secrets:** headless servers usually have no keychain. On Linux servers, store secrets as systemd encrypted credentials (`systemd-creds`). In Docker, use a secrets file mounted read-only with mode 0600. This is the only exception to keychain-only, and `opendot doctor` warns about it.
- **Caveat:** OpenAI's rules cover "open-source and locally hosted apps". Running your own copy on your own server for yourself is very likely the same thing, but re-check OpenAI's docs before promoting the server setup, and never let the server serve other people.

**Desktop app (Tauri 2):**
- Bundles the built UI and the daemon, packaged with PyInstaller as a Tauri sidecar. The desktop build script builds the sidecar first.
- On launch it connects to a running daemon or starts one.
- Tray icon with Pause and Open.
- Code signing and notarization need paid certificates: **Needs you** before public release. Unsigned builds are fine until then.

**Web:**
- The daemon serves the same UI at `http://127.0.0.1:<port>`, protected by Alfred's loopback bearer token.
- It binds to loopback only. Other devices reach it through the user's Tailscale, never an open port.

---

## 12. UI and design (for Astra)

**Identity:**
- An original visual identity. No colored-dots motif, no mascots that resemble OpenAI's.
- Icons from Lucide (ISC license).
- Fonts under OFL, for example Inter or IBM Plex.
- Light and dark themes.
- The companion's avatar is drawn by code (a deterministic SVG from a seed the user can re-roll), not generated by an image model.

**Screens for v0.1:**

1. **Onboarding:**
   - Name the companion and pick an avatar.
   - "Continue with ChatGPT". If the account isn't Plus or Pro, say so clearly and stop.
   - Ask the user to set a weekly OpenDot limit in ChatGPT Settings → Usage and keep credit use off.
   - Connect Gmail, Calendar and GitHub (optional, read-only).
   - The companion introduces itself.
2. **Chat:**
   - Streaming replies.
   - Inline approval cards with Approve, Edit, Deny and "Always allow this". "Always allow" creates a rule.
   - Every reply shows model, effort and credits.
3. **Companion profile:** tabs for In progress, Scheduled and Completed; Pause, Reset and Rename.
4. **Activity:** a timeline of what the companion did and why. Each item links to the rule, reviewer note and memories used.
5. **Rules:** list and edit rules with the four behaviors. Core deny-list items are shown but locked.
6. **Memory:** search, correct, and forget by item, source, time or person.
7. **Connections:**
   - App accounts and their health (ok / stale / error / never synced).
   - The opt-in switch for Gmail drafts and Calendar events.
8. **Usage:** credits by task, day and job type; budgets; "Manage usage" link.
9. **Settings:**
   - Keep-awake and quiet hours.
   - Opt-in providers and the feature switches that use them (§6.2), each with its cost warning.
   - Model tier overrides; automatic top-tier use.
   - Style preset.
   - Backup and restore.

**Required OpenAI elements** (§6.1): "Continue with ChatGPT", "Using ChatGPT plan", and "Manage usage". Use the DevKit's official assets, with comparable prominence to other sign-in options.

**About screen and README must include:** "OpenDot is an independent open-source project. It is not affiliated with, endorsed by, or sponsored by OpenAI or Anthropic. ChatGPT is a trademark of OpenAI."

**How the UI talks to the daemon:**
- Contract-first. Every endpoint and stream event is a Pydantic model in the daemon.
- `opendot contract export` writes JSON Schemas to `contract/`.
- The UI generates TypeScript types from them.
- `opendot mock-server` serves realistic fake data from the same contract, so the UI never waits on the backend.

---

## 13. Who builds what (development)

The user will be away most of the time. **Opus is the lead**, running in Claude Code with `/goal` in auto mode. Opus hands bulk work to Sonnet (as Claude Code subagents) and GPT work to Codex.

### 13.1 Roles

| Model | Runs in | Role | Default effort |
|---|---|---|---|
| **Opus** (Claude) | Claude Code, `/goal` | Lead engineer:<br>• plans each milestone<br>• writes security- and correctness-critical daemon code<br>• reviews all GPT-written code<br>• merges | high. xhigh for auth, rules, reviewer, agent loop, sandbox and secrets. max only for a bug that already failed twice at xhigh. |
| **Sonnet** (Claude) | Claude Code subagents | Bulk daemon work from clear specs: porting and deleting Alfred modules, connectors, CLI commands, tests, docs, CI | medium. low for mechanical edits. high for connector auth code. |
| **Astra** (GPT) | Codex | All UI: design system, screens, components, Tauri front-end glue | high |
| **Sol** (GPT) | Codex | Independent reviewer of Claude-written code; second opinion on OpenAI-specific work (the ChatGPT plan provider); hardest-bug second opinion | high. xhigh for the v0.1 security review. |
| **Terra** (GPT) | Codex | Cheap UI-side work: generated types, component and end-to-end tests, lint and type fixes, UI copy, small bug fixes | medium. low for lint and copy. |

Effort names: Claude Code uses low / medium / high / xhigh / max; Codex uses low / medium / high / xhigh. Use the closest level your tool offers. Never use Codex's maximum mode that starts extra agents unless the user asks.

**Working rules:**
- **Reviews from the other company:**
  - Sol reviews each milestone's full diff once, at the end, into `docs/reviews/m<N>.md`.
  - Sol also reviews these critical tasks before they merge: 1.4, 2.2, 2.6, 2.7, and M5's sandbox and secrets broker.
  - Opus reviews every GPT-written pull request.
  - The milestone isn't done until its review file exists and every finding in it is fixed or marked accepted with a reason.
- **Raise effort one step after a failed attempt.** Never start at the top.
- **Save the user's own limits:** Sonnet for bulk work, Opus for judgment, Terra instead of Astra for mechanical UI work.
- **One task, one pull request.** Squash-merge when CI is green and any required review is done. Opus may merge.

### 13.2 Handing work to Codex without the user

If the Codex CLI is installed and signed in on the build machine, Opus runs GPT tasks directly with `codex exec`.
- It needs write access to the repository and network access (for `pnpm`). In current Codex versions that means a workspace-write sandbox with network enabled, or full-auto mode. Check `codex exec --help` for the exact flags and record them in `docs/decisions.md` during M0.
- Codex model IDs for Astra, Sol and Terra: discover them in M0 (task 0.9) and record them in `docs/decisions.md`. If they're unclear, add a **Needs you** item.
- This is the user's own tool on their own machine, which is normal use.

If Codex isn't available, write the task into `STATUS.md` under **Needs you**, with the exact prompt to paste into Codex. Then:
- **For UI work:** stop the goal. Don't write UI as a Claude model.
- **For reviews:** keep working on other tasks. The milestone can't finish without its review file.

Prompt template for any GPT task:
```
Read ARCHITECTURE.md §0, §12 and milestone <M>. Task <id>: <task>.
Work on branch <branch>. Done when: <commands> exit 0; show their output.
Follow the "never" list in §0. Update STATUS.md when finished.
```

### 13.3 Before the first /goal (Needs you, once)

1. Create an empty GitHub repo `opendot` under your account. Keep it **private** until launch. Make sure `gh auth status` works so Claude Code can push.
2. Put Claude Code in auto mode, so `/goal` runs aren't stopped by permission prompts.
3. Install and sign in to the Codex CLI, so Opus can hand tasks to Astra, Sol and Terra.
4. Install the tools that need admin rights, since an unattended run can't:
   - [Tauri's prerequisites for your OS](https://tauri.app/start/prerequisites/) (on Linux, the WebKitGTK system packages).
   - Playwright's browser dependencies: `pnpm dlx playwright install --with-deps chromium`.
   - Docker (only needed from M5).
5. Everything else the agent can install itself without admin: Python 3.12 with `uv`, Node 22 with `pnpm`, Rust via `rustup`, and `gitleaks`.
6. Keep the build machine awake during long runs, or build on a machine that stays on.

---

## 14. Milestones and ready-to-use goals

Each milestone lists tasks (with model and effort), the finish line, and a `/goal` line to paste. Each goal repeats its commands in full, because the goal checker can't read this file. Goals end with a time limit so an unattended run can't loop forever. If a goal stops early, run it again; `STATUS.md` says where things stand.

### M0: Fork Alfred and clean it

| Task | Model / effort |
|---|---|
| 0.1 Clone Alfred with history into the `opendot` repo; set the remotes. Record the test-count baseline and the deletable count in `STATUS.md` (§5) | Opus / medium |
| 0.2 Restructure into the monorepo layout (§4); rename `alfred` → `opendot_core` and the CLI → `opendot` | Sonnet / medium |
| 0.3 Mine `hermes_bridge.py` into `agent/`; port its tests (§5) | Opus / high |
| 0.4 Apply §5: change, disable and delete modules; port tests; add the forward migrations | Sonnet / medium, Opus reviews |
| 0.5 Remove Windows-only code; make tests pass on Linux, macOS and Windows | Sonnet / medium |
| 0.6 Add `ruff`, `gitleaks` (with `.gitleaksignore` for the known fake tokens) and `ci.yml` (§4); fix lint | Sonnet / low |
| 0.7 Write README, NOTICE, CONTRIBUTING, CLAUDE.md, AGENTS.md, STATUS.md and `docs/decisions.md`. Resolve the leftover merge-conflict markers in Alfred's `ARCHITECTURE.md` and `CHANGELOG.md`; move the former to `docs/alfred-architecture.md`; start a fresh CHANGELOG | Sonnet / low |
| 0.8 Scan copied fixtures and docs for real names, emails and IDs; replace them with fakes | Sonnet / medium |
| 0.9 Discover the Codex CLI's `exec` flags and the model IDs for Astra, Sol and Terra; record them (§13.2) | Opus / low |
| 0.10 Cross-vendor review of M0 into `docs/reviews/m0.md`; fix findings | Sol / high |

**Done when** (all run from the repo root):
1. `uv run --project daemon pytest daemon/tests -q` exits 0.
2. `uv run --project daemon pytest daemon/tests --collect-only -q | tail -1` shows at least the minimum test count recorded in `STATUS.md`.
3. `uv run --project daemon ruff check daemon` exits 0.
4. `gitleaks git --no-banner` exits 0.
5. `git grep -nwE "hermes_[a-z_]+|turn_handshake|canvas|canvas_ical|academic_dedup|google_health|browseros_health|winservice" -- daemon/src daemon/tests ':!daemon/src/opendot_core/migrations'` prints nothing.
6. `git grep -nE "^(<<<<<<<|>>>>>>>)( |$)"` prints nothing.
7. `docs/reviews/m0.md` exists with no open findings.

```
/goal OpenDot milestone M0 is complete. From the repo root, run and print the output of each: (1) `uv run --project daemon pytest daemon/tests -q` exits 0; (2) `uv run --project daemon pytest daemon/tests --collect-only -q | tail -1` shows a test count at least equal to the minimum recorded in STATUS.md (print that STATUS.md line too); (3) `uv run --project daemon ruff check daemon` exits 0; (4) `gitleaks git --no-banner` exits 0; (5) `git grep -nwE "hermes_[a-z_]+|turn_handshake|canvas|canvas_ical|academic_dedup|google_health|browseros_health|winservice" -- daemon/src daemon/tests ':!daemon/src/opendot_core/migrations'` prints nothing; (6) `git grep -nE "^(<<<<<<<|>>>>>>>)( |$)"` prints nothing; (7) `cat docs/reviews/m0.md` shows every finding fixed or accepted with a reason; (8) STATUS.md marks M0 done. Only the test files listed in ARCHITECTURE.md §5 may be deleted. Or stop after 6 hours.
```

### M1: Contract, providers, and the ChatGPT plan sign-in

| Task | Model / effort |
|---|---|
| 1.1 API contract: Pydantic models for every endpoint and stream event; `opendot contract export`; deterministic output | Opus / high |
| 1.2 `opendot mock-server` serving fake data from the contract, with a `--check` self-test | Sonnet / medium |
| 1.3 Provider interface and error types; the "never switch provider on error" rule | Opus / high |
| 1.4 `chatgpt_plan` provider, all of §6.1, tested against a fake OAuth server and **synthetic** streaming fixtures written from OpenAI's docs. Real recorded fixtures replace them after the user's first sign-in | Opus / xhigh, Sol reviews |
| 1.5 Opt-in providers: `openai_key`, `anthropic_key`, `openrouter`, `local`, all off by default, each behind its feature switch (§6.2) | Sonnet / medium |
| 1.6 `opendot measure` (§8.5), with a `--dry-run` mode testable without an account | Sonnet / medium |
| 1.7 Cross-vendor review of M1 into `docs/reviews/m1.md`; fix findings | Sol / high |

**Done when** (from the repo root):
1. `uv run --project daemon pytest daemon/tests -q` exits 0.
2. `uv run --project daemon pytest daemon/tests/providers -q` exits 0.
3. `uv run --project daemon opendot contract export` succeeds, then `git status --porcelain contract/` prints nothing and `git ls-files contract | wc -l` prints more than 0.
4. `uv run --project daemon opendot mock-server --check` exits 0.
5. `uv run --project daemon opendot measure --dry-run` exits 0.
6. `docs/reviews/m1.md` has no open findings.

**Needs you afterward:** sign in once with ChatGPT and run `opendot measure` (a few minutes). The results go to `docs/measurements.md`.

```
/goal OpenDot milestone M1 is complete. From the repo root, run and print the output of each: (1) `uv run --project daemon pytest daemon/tests -q` exits 0; (2) `uv run --project daemon pytest daemon/tests/providers -q` exits 0; (3) `uv run --project daemon opendot contract export` succeeds, then `git status --porcelain contract/` prints nothing and `git ls-files contract | wc -l` prints a number above 0; (4) `uv run --project daemon opendot mock-server --check` exits 0; (5) `uv run --project daemon opendot measure --dry-run` exits 0; (6) `cat docs/reviews/m1.md` shows every finding fixed or accepted with a reason; (7) STATUS.md marks M1 done and lists the live ChatGPT sign-in and `opendot measure` run under Needs you. The test count must not drop below the STATUS.md minimum. Or stop after 8 hours.
```

### M2: Agent loop, router, usage meter, rules

| Task | Model / effort |
|---|---|
| 2.1 Write the scenario suite first (`opendot eval --suite core`), using a fake model provider | Opus / high |
| 2.2 Agent loop: durable steps, resume after restart, tool groups per task | Opus / xhigh, Sol reviews |
| 2.3 Context packer: cache-friendly layout (§8.2), budgets, compaction, trimming | Opus / high |
| 2.4 Router: §7.1 table, tier discovery (§7.2), one-step escalation, top tier asks first | Opus / high |
| 2.5 Usage meter and budgets (§8.3), including the daily hard stop | Sonnet / medium, Opus reviews |
| 2.6 Rules engine: four behaviors, core deny list, "always allow" creates a rule | Opus / xhigh, Sol reviews |
| 2.7 Reviewer pass and anomaly auto-pause | Opus / xhigh, Sol reviews |
| 2.8 WebSocket chat stream and approval endpoints for the UI | Sonnet / medium |
| 2.9 Cross-vendor review of M2 into `docs/reviews/m2.md`; fix findings | Sol / high |

**Scenarios the suite must cover (IDs S1–S10):**

| ID | Scenario |
|---|---|
| S1 | A reminder is created and delivered on time. |
| S2 | Creating a Gmail draft produces an approval and does nothing without it. |
| S3 | Deny-list actions are refused even with an `auto` rule. |
| S4 | Hitting the task budget pauses the task. |
| S5 | Hitting the daily budget pauses all plan requests. |
| S6 | A 429 pauses all plan requests and never switches provider. |
| S7 | Escalation moves at most one tier per step, and the top tier waits for approval. |
| S8 | A task resumes after a daemon restart. |
| S9 | A replayed approval doesn't repeat the action. |
| S10 | The stable prompt prefix is byte-identical across all steps of one task. |

**Done when:** all tests pass, the suite reports S1–S10 as passed, and `docs/reviews/m2.md` has no open findings.

```
/goal OpenDot milestone M2 is complete. From the repo root, run and print the output of each: (1) `uv run --project daemon pytest daemon/tests -q` exits 0; (2) `uv run --project daemon opendot eval --suite core` exits 0 and its output lists each of S1, S2, S3, S4, S5, S6, S7, S8, S9 and S10 as passed; (3) `cat docs/reviews/m2.md` shows every finding fixed or accepted with a reason; (4) STATUS.md marks M2 done. Scenarios S1-S10 must test what ARCHITECTURE.md §14 M2 describes and may not be removed or weakened. The test count must not drop below the STATUS.md minimum. Or stop after 10 hours.
```

### M3: UI, desktop and web (Astra leads this milestone)

| Task | Model / effort |
|---|---|
| 3.1 Generate TypeScript types from `contract/`; the build fails if they drift | Terra / medium |
| 3.2 Design system: tokens, type, color, light/dark themes, Lucide, code-drawn avatar | Astra / high |
| 3.3 All v0.1 screens (§12) against the mock server | Astra / high |
| 3.4 Component tests (Vitest) and end-to-end tests (Playwright) for onboarding, chat with an approval, rules and usage | Terra / medium |
| 3.5 Daemon serves the built UI on 127.0.0.1 with the bearer token | Sonnet / medium |
| 3.6 Tauri shell: bundle the UI, build the daemon sidecar with PyInstaller first, tray, start/attach | Opus / high |
| 3.7 Review the UI code, accessibility and contract usage into `docs/reviews/m3.md` | Opus / high |

**Done when** (from the repo root):
1. `pnpm -C ui lint && pnpm -C ui test && pnpm -C ui build` exits 0.
2. `pnpm -C ui e2e` exits 0 against the mock server.
3. `pnpm -C desktop build` builds the sidecar and the Tauri app, and exits 0.
4. `uv run --project daemon pytest daemon/tests -q` exits 0.
5. `docs/reviews/m3.md` has no open findings.

```
/goal OpenDot milestone M3 is complete. From the repo root, run and print the output of each: (1) `pnpm -C ui lint && pnpm -C ui test && pnpm -C ui build` exits 0; (2) `pnpm -C ui e2e` exits 0; (3) `pnpm -C desktop build` exits 0; (4) `uv run --project daemon pytest daemon/tests -q` exits 0; (5) `cat docs/reviews/m3.md` shows every finding fixed or accepted with a reason; (6) STATUS.md marks M3 done. UI code under ui/ is written by Astra or Terra through the Codex CLI; if Codex is unavailable, record it under Needs you in STATUS.md and stop instead of writing UI yourself. Or stop after 10 hours.
```

### M4: Always-on, connectors, v0.1 release candidate

| Task | Model / effort |
|---|---|
| 4.1 `opendot service install/uninstall/status` for all 3 OSes | Sonnet / medium |
| 4.2 Keep-awake setting with "only while plugged in" | Sonnet / medium |
| 4.3 Catch-up after sleep, with a one-time "missed while asleep" note | Sonnet / medium |
| 4.4 Gmail, Calendar and GitHub connectors for any user: read-only Google scopes by default; the opt-in drafts/events switch asks Google for write scopes; guided "bring your own Google OAuth client" setup | Sonnet / high, Opus reviews |
| 4.5 Built-in routines: morning brief, inbox triage, weekly review | Sonnet / medium |
| 4.6 `opendot doctor`: checks the service, sign-in, keychain, connectors, disk and backups | Sonnet / medium |
| 4.7 Server, headless secrets and Tailscale guide in `deploy/` (§11) | Sonnet / low |
| 4.8 Security review of all of v0.1 into `docs/reviews/security-v0.1.md`, then fixes | Sol / xhigh, then Opus / xhigh |

**Done when** (from the repo root):
1. All earlier test, UI and build commands still exit 0.
2. `uv run --project daemon opendot doctor --ci` exits 0.
3. The latest full CI run on `main` passed on all three OSes:
   - `gh run list --workflow ci.yml --branch main --event push --status completed --limit 1 --json conclusion,headSha` shows `"conclusion":"success"`.
   - Its `headSha` equals `git rev-parse origin/main`.
4. `docs/reviews/security-v0.1.md` has every finding fixed or accepted with a reason.

**Needs you:**
- Create a Google OAuth client and connect Gmail and Calendar.
- Use OpenDot for a few days.
- Decide on making the repo public and tagging `v0.1.0`.

```
/goal OpenDot milestone M4 is complete. From the repo root, run and print the output of each: (1) `uv run --project daemon pytest daemon/tests -q` exits 0; (2) `pnpm -C ui lint && pnpm -C ui test && pnpm -C ui build && pnpm -C ui e2e` exits 0; (3) `pnpm -C desktop build` exits 0; (4) `uv run --project daemon opendot doctor --ci` exits 0; (5) `gh run list --workflow ci.yml --branch main --event push --status completed --limit 1 --json conclusion,headSha` shows "conclusion":"success" and its headSha equals the output of `git rev-parse origin/main`; (6) `cat docs/reviews/security-v0.1.md` shows every finding fixed or accepted with a reason; (7) STATUS.md says "v0.1 release candidate ready" and lists the Google OAuth setup, a few days of real use, and the publish decision under Needs you. The test count must not drop below the STATUS.md minimum. Or stop after 10 hours.
```

### M5 (v0.2): computer, browser, channels, proactive work

| Task | Model / effort |
|---|---|
| Sandbox container per companion: rootless Docker or Podman, no host mounts, egress allowlist, resource limits | Opus / xhigh, Sol reviews |
| Browser tool: Playwright in the sandbox; only extracted text goes to the model | Opus / high |
| "Open computer" live view (noVNC or WebRTC) in the UI | Astra / high |
| Secrets broker with placeholders | Opus / xhigh, Sol reviews |
| Proactive read-only research jobs, read-only enforced at the tool layer | Opus / high |
| MCP client: user-added servers, allowlisted, each with only its own scoped credential | Opus / high |
| Turn Slack and Telegram back on, with approvals in those channels; real-app setup guide (Alfred's Slack was never tested live) | Sonnet / medium |
| Approval-gated email sending | Sonnet / high, Opus reviews |
| `cli` provider: the user's own `claude` or `codex` as a helper in the sandbox, opt-in | Sonnet / high |
| Sandbox escape and prompt-injection review | Sol / xhigh |

### M6 (v0.3): phone and multiple companions

- Phone access: installable web app (PWA) through Tailscale.
- Multiple companions, each with its own memory, rules and budget.
- Optional encrypted sync between the user's own devices.

---

## 15. Risks

| Risk | Mitigation |
|---|---|
| OpenAI changes the preview flow or its rules | Everything goes through `providers/chatgpt_plan.py`; contract tests on fixtures; re-read the linked docs pages before each release. |
| Weekly plan limits run out | §8 rules; budgets; the user sets a per-app weekly limit in ChatGPT. |
| Accidental credit spending | Credits off in onboarding; daily hard stop; `opendot measure` check (§6.1 point 10). |
| Anthropic changes its terms again | Claude is opt-in only; the `cli` helper can be removed without touching anything else. |
| Name confusion with other "opendot" projects | Distinct package names; clear README; the decision stands (D1). |
| Prompt injection leads to a harmful action | Rules, reviewer, core deny list, read-only proactive work, anomaly auto-pause. |
| Always-on stops when the laptop sleeps | Keep-awake + catch-up + server guide; honest UI copy. |
| A self-hosted server counts as "remotely hosted" to OpenAI | Re-check before promoting the server guide; never serve other people (§11). |
| Python-inside-Tauri packaging is fiddly on 3 OSes | Tackle it early in M3 (task 3.6) and keep it in CI. |
| Agents weaken tests to finish a goal | §0 forbids it; test-count floor; fixed scenario IDs; cross-vendor reviews check it. |
| Codex unavailable during unattended runs | UI goals stop and ask; reviews queue under Needs you (§13.2). |

---

## 16. Sources

- OpenAI: [Using your ChatGPT plan in other apps and sites](https://help.openai.com/en/articles/20001542-using-your-chatgpt-plan-in-other-apps-and-sites)
- OpenAI: [Sign in with ChatGPT: plan usage for open-source apps](https://developers.openai.com/siwc/token-sharing-open-source)
- OpenAI: [Preview limitations](https://developers.openai.com/siwc/token-sharing-open-source/preview-limitations)
- OpenAI: [Models and inference](https://developers.openai.com/siwc/token-sharing-open-source/models-and-inference)
- OpenAI: [Errors and recovery](https://developers.openai.com/siwc/token-sharing-open-source/errors-and-recovery)
- OpenAI: [Codex rate card](https://help.openai.com/en/articles/20001106-codex-rate-card)
- OpenAI: [Sign in with ChatGPT DevKit](https://github.com/openai/sign-in-with-chatgpt-devkit)
- OpenAI: [Introducing dots](https://openai.com/index/introducing-dots/) and [Getting started with your dot](https://help.openai.com/en/articles/20001530-getting-started-with-your-dot)
- Anthropic: [Claude Code legal and compliance](https://code.claude.com/docs/en/legal-and-compliance.md)
- Anthropic: [Claude Code /goal](https://code.claude.com/docs/en/goal.md)
- Tauri: [Prerequisites](https://tauri.app/start/prerequisites/)
- Alfred: [ndunl075/alfred](https://github.com/ndunl075/alfred), ARCHITECTURE.md (commit `defde89`)
- Lucide: [lucide.dev](https://lucide.dev) (ISC license)
