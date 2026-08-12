# Alfred's Hermes profile

`ARCHITECTURE.md` decision 1 is explicit: don't rebuild a conversation loop
inside `alfred-core` -- use [Hermes](https://github.com/NousResearch/hermes-agent),
an existing open-source agent runtime, and publish Alfred as a Hermes
*profile distribution* that talks to `alfred-core` over local stdio MCP. This
directory is that distribution.

**Verification status, stated honestly:** this has not been installed
against a real Hermes build yet. Every file here is built from Hermes's own
published docs, but two of those docs actually disagree with each other on
where MCP servers are configured (a distribution's own `mcp.json`, per the
profile-distributions guide, vs. `config.yaml`'s `mcp_servers` key, per the
separate MCP feature guide) -- both are provided here on the theory that
`hermes profile install` reconciles a distribution's `mcp.json` into the
live `config.yaml` it manages, but that's inference, not something we've
watched happen. Treat this the same way `deploy/slack-app/` treats its own
unverified connector: correct by construction against the documented schema,
not yet run for real. If something doesn't match once you actually install
Hermes, that's a real gap in this profile -- report it back rather than
hand-patching around it each time.

## What Alfred Core already exposes to this profile

Every fact/action a skill here uses already exists as a policy-gated MCP
tool in `alfred-mcp` -- `agenda_get`, `brief_get`, `memory_search`, `remember`,
`connector_status`, `connector_records_get` (new, exposes raw synced
connector content -- e.g. gmail-sync's unread messages -- that `brief_get`'s
ranked digest doesn't include), `task_upsert`, `task_complete`,
`reminder_set`, and the propose/`action_commit` pairs for calendar/Gmail/
GitHub writes. `skills/productivity/` uses these directly; nothing here talks
to Google, Telegram, or GitHub on its own.

## 1. Install Hermes itself

This is a separate, real install -- your call to run, not something this
profile does for you:

```powershell
iex (irm https://hermes-agent.nousresearch.com/install.ps1)
```

## 2. Have a local model ready (and, optionally, an OpenRouter key)

`config.yaml` defaults to a local [Ollama](https://ollama.com) model as the
primary provider, matching Alfred Core's own "local first" rule
(`ARCHITECTURE.md` decision 6), with [OpenRouter](https://openrouter.ai/keys)
configured as a fallback for anything the local model can't handle well.
Pull whatever model you want locally (`ollama pull llama3.2` or similar) and
adjust `config.yaml`'s `model.default` to match if you use something else.
The OpenRouter key is optional -- `distribution.yaml` declares it as a
not-required `env_requires` entry, so `hermes profile install` will prompt
for it without failing if you skip it.

## 3. Install this profile

From this repo's root, install straight from the local directory (Hermes
supports this for development; no separate git repo needed yet):

```powershell
hermes profile install .\hermes-profile --alias alfred
```

## 4. Give Hermes its own Alfred Core scope

Don't reuse `local-mcp` (Claude/Cursor's default identity) -- same reasoning
as `deploy/openai-tunnel/README.md`'s `chatgpt-tunnel` grant, a client only
gets what it's explicitly scoped for:

```powershell
alfred client-grant --client-id hermes --sensitivity public --sensitivity personal `
  --tool agenda_get --tool brief_get --tool memory_search --tool remember `
  --tool connector_status --tool connector_records_get `
  --tool task_upsert --tool task_complete --tool reminder_set `
  --tool calendar_event_propose --tool message_draft --tool message_send_propose `
  --tool github_issue_propose --tool forget --tool action_commit `
  --allow-write
```

This starts at `public`+`personal` sensitivity. Widen to `--sensitivity
sensitive` later only if a specific skill actually needs it.

## 5. Test it without touching Telegram yet

```powershell
hermes -p alfred chat
```

Ask it something that requires a real Alfred Core call -- "what's on my
agenda today" should come back via `brief_get`, not a guess. This confirms
the stdio MCP connection (`mcp.json`'s `alfred-mcp --client-id hermes`) and
the client-grant above both work, with zero risk to the Telegram setup this
session already verified end-to-end.

## 6. Telegram: read this before running `hermes gateway`

**Do not run `hermes gateway` against the same Telegram bot while the
Windows service is also running.** Telegram allows exactly one active
long-poller per bot token -- running two causes missed messages, duplicate
replies, or outright conflict errors from Telegram's API, the same warning
that applied to running the laptop and this PC's service simultaneously.

`alfred run` (and the installed Windows service, which is a thin wrapper
around the same code) already cleanly skips Telegram entirely when `--pair`/
`--chat-id` are omitted from its arguments -- every connector sync, due job,
reminder, and brief-scheduling check keeps running regardless. So the actual
cutover, whenever you're ready for Hermes to own Telegram instead of Alfred
Core's own poller:

```powershell
alfred service-configure run --gmail-inbound-sender owner@example.com
alfred-service restart
```

(same `service-configure` you ran during initial setup, just without
`--pair`/`--chat-id` this time) -- then, and only then, start `hermes
gateway` with your Telegram bot token. This is a live change to a system
we've already verified working end-to-end, so it's deliberately not
something this profile or its install steps do automatically.
