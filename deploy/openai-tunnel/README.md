# ChatGPT / OpenAI Secure MCP Tunnel

Section 7 names this as the private-access path for ChatGPT, since ChatGPT
cannot connect directly to a local MCP server the way Claude Desktop or
Cursor can over stdio. OpenAI's own answer is [Secure MCP
Tunnel](https://developers.openai.com/api/docs/guides/secure-mcp-tunnels):
an outbound-only relay. You run their open-source `tunnel-client` daemon
next to Alfred; it initiates an outbound HTTPS connection to OpenAI and
forwards MCP requests to Alfred locally. Nothing about this opens an inbound
port on your machine.

Alfred does not vendor or reimplement `tunnel-client` — it's OpenAI's tool,
kept current by them, the same way `deploy/couchdb/` defers to the
Self-hosted LiveSync project's own init script rather than re-deriving
CouchDB configuration here. This page covers Alfred's side: giving the
tunnel its own scoped identity and pointing it at `alfred-mcp`.

## 1. Create a tunnel and get a `tunnel_id`

This step happens entirely in your OpenAI account — "Platform tunnel
settings" at [platform.openai.com](https://platform.openai.com) — and needs
a ChatGPT/API plan that supports custom MCP connectors, which is plan-
dependent and outside anything this repo controls. You'll come away with a
`tunnel_id` and a `CONTROL_PLANE_API_KEY`.

## 2. Give the tunnel its own Alfred scope

Don't reuse `local-mcp` (Claude/Cursor's default identity) for this — grant
the tunnel its own, deliberately narrower, client ID:

```powershell
.\.venv\Scripts\alfred client-grant --client-id chatgpt-tunnel `
  --sensitivity public --sensitivity personal `
  --tool memory_search --tool agenda_get --tool brief_get --tool connector_status
```

Add `--allow-write` and more `--tool` flags only for what you actually want
ChatGPT able to do — section 7's "per-client scopes prevent a coding client
from receiving health data or sending personal messages unless explicitly
granted" applies here exactly as it does to any other MCP client.

## 3. Install `tunnel-client`

Download it from Platform tunnel settings or the [openai/tunnel-client
releases](https://github.com/openai/tunnel-client) — point at the latest
release rather than pinning a specific version in your own notes, since
OpenAI updates it independently of Alfred.

## 4. Point it at `alfred-mcp --client-id chatgpt-tunnel`

```powershell
$env:CONTROL_PLANE_API_KEY = "sk-..."
tunnel-client init `
  --sample sample_mcp_stdio_local `
  --profile alfred `
  --tunnel-id tunnel_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx `
  --mcp-command "C:\path\to\alfred\.venv\Scripts\alfred-mcp.exe --client-id chatgpt-tunnel"
```

stdio, not the Streamable HTTP transport, is the path documented here
deliberately: `tunnel-client` spawns and talks to `alfred-mcp` as a local
subprocess, the same way Claude Desktop or Cursor already do, so there's no
network auth surface to reconcile between the tunnel's own control-plane
credential and Alfred's separate bearer-token scheme on `mcp-http-run`. The
HTTP path (`--mcp-server-url` instead of `--mcp-command`) is documented by
OpenAI too, but its header/auth passthrough behavior is worth re-checking
against their current docs at setup time before relying on it — stdio has
no such question to begin with.

Validate, then run it (keep this process running the same way you'd keep
`alfred run` running — Task Scheduler, a service, or a terminal left open):

```powershell
tunnel-client doctor --profile alfred --explain
tunnel-client run --profile alfred
```

## 5. Connect it in ChatGPT

Developer mode → create an app → Connection: **Tunnel** → select the tunnel
that appears for your workspace, or paste the `tunnel_id` directly.
