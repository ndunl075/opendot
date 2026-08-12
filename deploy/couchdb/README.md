# Self-hosted mobile vault sync

This is the server-side half of ARCHITECTURE.md section 5's "Phone access
without paying for Obsidian": a self-hosted CouchDB instance that a vetted,
open-source Obsidian community plugin — [Self-hosted
LiveSync](https://github.com/vrtmrz/obsidian-livesync) — replicates
`alfred-vault/` against, so the same notes are available offline on your
phone. Alfred's own Python code never talks to this CouchDB for anything but
a reachability check (`alfred vault-sync-status`); the actual sync is done
entirely by that plugin, running inside Obsidian on your desktop and phone,
not by Alfred. Treat it as replaceable, exactly as the architecture doc says.

**Read this whole page before running anything.** Getting the security model
wrong here means your notes — even though `alfred.db`, secrets, and logs
never enter the vault (see the safety note below) — are one weak password
away from being reachable if you ever expose the port publicly.

## What this is not

- Not Obsidian Sync (the paid service). This has no subscription.
- Not a replacement for `alfred.db`. CouchDB here only ever holds a copy of
  `alfred-vault/`'s Markdown files, synced by the Obsidian plugin — never
  the database, credentials, connector cursors, audit log, or raw message
  archives. Section 5 makes this an explicit rule, not a suggestion: if you
  ever see anything besides `alfred-vault/`'s own Markdown files headed
  toward this server, stop and check your plugin's sync scope.
- Not something Alfred is meant to expose to the public internet. Section 5
  calls for "a private encrypted network" — reach this from your phone
  through a VPN (Tailscale and WireGuard are both free, open-source, and
  well suited to this), never a forwarded router port.

## 1. Start CouchDB

```powershell
cd deploy\couchdb
copy .env.example .env
notepad .env   # set a real COUCHDB_USER and COUCHDB_PASSWORD
docker compose up -d
```

`docker-compose.yml` binds CouchDB to `127.0.0.1:5984` by default. That's
deliberately safe, but it also means it is **not** reachable from your
phone yet even over a VPN — `127.0.0.1` only ever accepts connections that
originate on this machine itself, no matter what network route got there.
To actually reach it from a phone, set `ALFRED_VAULT_SYNC_HOST` in `.env`
to this host's own VPN/Tailscale IP (`tailscale ip -4`) before running
`docker compose up`, and restart the container (`docker compose up -d`
again) if you change it later. Never set it to `0.0.0.0`.

## 2. Configure CouchDB for LiveSync

A fresh CouchDB needs single-node initialization and CORS enabled for
Obsidian's desktop and mobile origins before LiveSync can use it. Rather
than duplicate configuration steps here that could drift out of date, run
the official project's own init script — **read it first** before piping
anything to `bash`:

```powershell
# Read it: https://github.com/vrtmrz/obsidian-livesync/blob/main/utils/couchdb/couchdb-init.sh
$env:hostname = "http://127.0.0.1:5984"
$env:username = "<the COUCHDB_USER you set above>"
$env:password = "<the COUCHDB_PASSWORD you set above>"
$env:database = "alfredvault"
# Run it from Git Bash / WSL (it's a bash script):
curl -s https://raw.githubusercontent.com/vrtmrz/obsidian-livesync/main/utils/couchdb/couchdb-init.sh | bash
```

Alfred's own repo does not vendor a copy of that script — it belongs to,
and is kept current by, the plugin project it configures for.

## 3. Confirm it's reachable

```powershell
.\.venv\Scripts\alfred vault-sync-status --url http://127.0.0.1:5984
```

Reports `{"reachable": true, "couchdb_version": "..."}` or `{"reachable":
false, "error": "..."}`. This is an unauthenticated check (CouchDB's root
endpoint answers without credentials) — it only confirms the server is up
and speaking CouchDB, not that LiveSync itself is correctly configured.

## 4. Install and configure the Obsidian plugin

On desktop **and** on your phone, install **Self-hosted LiveSync** from
Obsidian's community plugin browser, then point it at:

- URL: your VPN address for this host, e.g. `http://100.x.y.z:5984` (a
  Tailscale IP), never a public one
- Database: the `alfredvault` name used above
- Username/password: the `COUCHDB_USER`/`COUCHDB_PASSWORD` from `.env`

Follow the plugin's own setup guide for first-time vault initialization —
that part is entirely the plugin's job, not Alfred's.

## Stopping / removing it

```powershell
docker compose down       # stop; data in ./data survives
docker compose down -v    # stop and delete synced data too
```
