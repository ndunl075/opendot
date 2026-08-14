# Alfred's Hermes profile

`ARCHITECTURE.md` decision 1 is explicit: don't rebuild a conversation loop
inside `alfred-core` -- use [Hermes](https://github.com/NousResearch/hermes-agent),
an existing open-source agent runtime, and publish Alfred as a Hermes
*profile distribution* that talks to `alfred-core` over local stdio MCP. This
directory is that distribution.

**Verification status:** installed and confirmed working end-to-end against
a real Hermes build (v0.20.0) -- `hermes -p alfred chat` successfully calls
Alfred Core's MCP tools. Three things turned out different from what
upstream's own docs suggested going in, now fixed in these files and worth
knowing about:

1. **A distribution's `mcp.json` is not automatically applied.** The
   profile-distributions guide implies `hermes profile install` wires it up;
   in practice, on this Hermes version, `mcp.json` is inert and the real
   mechanism is `config.yaml`'s `mcp_servers` key, populated by running
   `hermes mcp add` once after install (step 5 below) -- see that step for
   why this can't be pre-baked into the installed config automatically.
2. **`hermes profile install`'s `--alias` flag takes no value** -- it's a
   bare flag that creates an optional shell-wrapper shortcut, not a way to
   set the profile name (that comes from `distribution.yaml`'s `name` field,
   or `--name`). `hermes profile install .\hermes-profile --alias alfred`
   fails with "unrecognized arguments: alfred"; step 3 below reflects the
   correct, plain form.
3. **A local model large enough to pass Hermes's 64K-context minimum was too
   slow for interactive use** on ordinary consumer hardware -- multi-minute
   single turns. `config.yaml` now defaults to Nous Portal's free tier
   instead of Ollama; see step 2.

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

The installer asks how to set up Hermes -- **pick "Quick Setup (Nous
Portal)."** That's a free OAuth login (Nous Portal has a real $0/mo tier: no
credits needed, capped at 50 RPM/500K TPM, limited to Hermes's free-model
catalog per [its docs](https://hermes-agent.nousresearch.com/docs/integrations/nous-portal)).
Picking it here only bootstraps Hermes's own base setup with a working free
model out of the box -- it does not lock out local Ollama below. Providers
aren't exclusive in Hermes's config: `config.yaml` lists Nous and
`ollama-local`, one primary and one fallback, and you can switch with
`/model <name> --provider <provider>`.

## 2. Model setup: Nous Portal primary, Ollama as the local fallback

`config.yaml` defaults to **Nous Portal** (`upstage/solar-pro4:free`) as the
primary provider, not local Ollama. That's a deliberate change from Alfred
Core's own "local first" rule (`ARCHITECTURE.md` decision 6): a local model
large enough to clear Hermes's own 64K-context minimum (`qwen2.5:7b-instruct`
only reports 32K and fails that check outright; `qwen3.5:9b` passes but took
several minutes per turn on ordinary consumer hardware) was too slow for an
actually-interactive assistant in practice. Nous Portal's free tier ($0/mo,
50 RPM/500K TPM cap, [its docs](https://hermes-agent.nousresearch.com/docs/integrations/nous-portal))
responded fast and is already authenticated from step 1's "Quick Setup."
`upstage/solar-pro4:free` specifically because it's built "agent-first" with
native tool/function calling -- important since Hermes needs reliable
function calls to actually use Alfred Core's MCP tools, not just chat.

Ollama stays configured as the only fallback (`fallback_providers` in
`config.yaml`), so a Nous Portal outage or rate limit degrades to
private, slow-but-working local inference rather than a paid provider. This
also keeps Alfred's operational cloud-spend ceiling at $0. If you'd rather run local-first anyway
(privacy, no rate limit, willing to accept the latency) run `ollama list`,
pick a model that reports at least 64K context, and swap `model.provider`/
`model.default` back to `ollama-local`.

## 3. Install this profile

From this repo's root, install straight from the local directory (Hermes
supports this for development; no separate git repo needed yet):

```powershell
hermes profile install .\hermes-profile
```

Install Hermes's supported no-key web-search dependency in the same Python
environment Hermes uses:

```powershell
& "$env:LOCALAPPDATA\hermes\hermes-agent\venv\Scripts\python.exe" -m pip install ddgs
hermes -p alfred config set web.search_backend ddgs
```

The profile already declares that backend in `config.yaml`; the second command
also applies it immediately to an existing installation. Web search is
read-only. Calendar, Gmail, and GitHub writes continue through Alfred Core's
proposal and Telegram approval boundary.

(Not `--alias alfred` -- see point 2 in the verification-status note above.
The profile name comes from `distribution.yaml`'s `name: alfred` automatically.)

## 4. Give Hermes its own Alfred Core scope

Don't reuse `local-mcp` (Claude/Cursor's default identity) -- same reasoning
as `deploy/openai-tunnel/README.md`'s `chatgpt-tunnel` grant, a client only
gets what it's explicitly scoped for:

```powershell
alfred client-grant --client-id hermes --sensitivity public --sensitivity personal `
  --tool agenda_get --tool brief_get --tool memory_search --tool remember `
  --tool memory_correct --tool memory_feedback `
  --tool connector_status --tool connector_records_get `
  --tool task_upsert --tool task_complete --tool reminder_set `
  --tool calendar_event_propose --tool message_draft --tool message_send_propose `
  --tool github_issue_propose --tool forget --tool action_commit `
  --allow-write
```

This starts at `public`+`personal` sensitivity. Widen to `--sensitivity
sensitive` later only if a specific skill actually needs it.

## 5. Register the MCP connection (required, not automatic)

`mcp.json` in this distribution is not applied by `hermes profile install` --
run this once, from any directory, to actually wire up the connection:

```powershell
hermes -p alfred mcp add alfred --command alfred-mcp --args --client-id hermes
```

It connects, lists all 17 discovered tools, then asks **"Enable all 17
tools? [Y/n/select]"** -- type `Y`. This has to be run interactively (the
prompt can't be scripted past); confirm it saved with `hermes -p alfred mcp
list`, which should show `alfred` with status `✓ enabled`, not "No MCP
servers configured."

## 6. Test it without touching Telegram yet

```powershell
hermes -p alfred chat
```

Ask it something that requires a real Alfred Core call -- "what's on my
agenda today" should come back via `brief_get`, not a guess, and not a
clarifying question about which calendar app you use (there's only one
system connected; if it asks that, the MCP connection from step 5 likely
isn't actually registered -- re-check with `hermes -p alfred mcp list`).
This confirms the stdio MCP connection and the client-grant above both work,
with zero risk to the Telegram setup this session already verified
end-to-end.

## 7. Telegram: read this before running `hermes gateway`

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
