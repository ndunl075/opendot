# Slack setup

Slack is optional, and Alfred only ever talks to it through Socket
Mode — an outbound WebSocket connection Alfred opens, not an inbound
webhook or a public URL. Nothing about this needs a tunnel, a domain, or
an open port.

This connector (`slack.py`, `slack_socket.py`) is fully built — the same
paired-identity, idempotent-ingest, default-deny pattern Telegram already
uses — but it has never been exercised against a real Slack app, so treat
it as **unverified** the same way `google_health.py` is: correct by
construction and by unit tests against synthetic fixtures, not by having
actually run.

## 1. Create the app from the manifest

Go to [api.slack.com/apps](https://api.slack.com/apps) → **Create New
App** → **From an app manifest** → pick your workspace → paste the
contents of `deploy/slack-app/manifest.yml`. Slack's own editor validates
it against their live schema before creating anything — if Slack has
changed the manifest format since this was written, that's where you'll
find out, not by guessing.

This configures the bot's OAuth scopes, its event subscriptions, and
Socket Mode in one step. It does **not** install the app, generate any
token, or invite the bot anywhere — those stay manual on purpose, since
they're workspace-specific decisions a checked-in file shouldn't make for
you.

## 2. Generate the two tokens Alfred needs

- **App-level token** (`slack-app-token`): in the app's **Basic
  Information** page, under **App-Level Tokens**, generate one with the
  `connections:write` scope. This is what Socket Mode itself
  authenticates with.
- **Bot token** (`slack-bot-token`): go to **OAuth & Permissions**,
  **Install to Workspace**, then copy the **Bot User OAuth Token**
  (starts `xoxb-`). This is what posts messages and reads the events the
  manifest subscribed to.

Save the app-level token under service `alfred`, account `slack-app-token`,
and the bot token under service `alfred`, account `slack-bot-token`, in
your OS credential manager. Never put either token in a config file, the
database, or a commit —
`SlackBotClient`/`SlackSocketReceiver` only ever read them from the OS
credential store, matching every other connector's credential handling.

## 3. Invite the bot and find your IDs

Invite the bot user to whichever channel you want paired (`/invite
@Alfred` in that channel). You need two IDs:

- **Channel ID**: right-click the channel → **View channel details** →
  it's at the bottom (starts `C`).
- **User ID**: click your own profile → **More** → **Copy member ID**
  (starts `U`).

## 4. Pair it and run

```powershell
.\.venv\Scripts\alfred run --slack-pair CHANNEL_ID:USER_ID --slack-channel-id CHANNEL_ID
```

Only messages from that exact channel/user pair are accepted — everything
else is rejected and audited, the same default-deny rule Telegram pairing
uses. Send `/task <title>` or `/remind <ISO-8601 time> <title>` in the
paired channel to confirm intake works, then check `alfred connector-status`
or the admin dashboard's Connectors page.
