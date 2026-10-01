# Running OpenDot 24/7 on a server

This guide is for one person running their own copy of OpenDot on a home server or a small Linux VPS, so
it keeps working while their laptop is closed. Read the caveat first.

> **Caveat (ARCHITECTURE.md section 11).** OpenAI lets "open-source and locally hosted apps" use a
> ChatGPT plan. Running your own copy on your own server, for yourself, is very likely the same thing,
> but that is an interpretation, not a promise. **Re-check OpenAI's current terms before you rely on
> this setup or recommend it to anyone, and never let the server serve other people.** It is one
> person's assistant, signed in to that person's ChatGPT plan. No shared access, no family or team
> accounts, no public URL.

## The rules that never change on a server

- The daemon listens on `127.0.0.1` only. `opendot serve` has no option to listen elsewhere, the
  Dockerfile and compose file here keep it that way, and nothing in this guide asks you to open a port.
- Other devices reach it through Tailscale ([tailscale.md](tailscale.md)), never through a public port,
  a port-forward on your router, or a reverse proxy on the internet.
- Secrets stay out of images, git and logs. See [secrets-and-signin.md](secrets-and-signin.md).
- Run `opendot doctor` after any change. On a server it will warn that a secrets file is in use. That
  warning is expected, and it is there so you remember why ([secrets-and-signin.md](secrets-and-signin.md)).

## Option A: systemd user service (no Docker)

For a Linux machine with systemd 250 or newer (Debian 12, Ubuntu 22.04 or later, Fedora).

1. Install uv and OpenDot as your normal user (no root):
   ```sh
   curl -LsSf https://astral.sh/uv/install.sh | sh
   uv tool install ./daemon        # from a checkout of the repository; puts `opendot` in ~/.local/bin
   opendot init
   ```
2. Build the UI once (needs Node and pnpm) and tell the daemon where it is, or skip the web UI and use
   only the API. From the checkout: `pnpm -C ui install && pnpm -C ui build`, then add
   `Environment=OPENDOT_UI_DIST=/home/you/opendot/ui/dist` to the unit.
3. Create the access token as an encrypted credential: follow "systemd-creds" in
   [secrets-and-signin.md](secrets-and-signin.md).
4. Install and start the unit from [systemd/opendot.service](systemd/opendot.service):
   ```sh
   mkdir -p ~/.config/systemd/user
   cp deploy/systemd/opendot.service ~/.config/systemd/user/
   systemctl --user daemon-reload
   systemctl --user enable --now opendot
   loginctl enable-linger "$USER"     # keep the user service running when you are logged out
   ```
5. Check it: `systemctl --user status opendot`, then run `opendot doctor`. From your own shell,
   doctor cannot see the credential systemd gives the service (it exists only inside the service), so
   it reports the keychain as usual; to have it check the file setup, run it inside the unit's
   environment, for example
   `systemd-run --user --pipe --wait -p LoadCredentialEncrypted=opendot-token:$HOME/.config/opendot/opendot-token.cred opendot doctor`.
   The secrets-file line is then a WARN on purpose.

`opendot service install` (the desktop-style service, task 4.1) is for laptops and desktops. On a
server, prefer the unit above, because it loads the encrypted credential.

## Option B: Docker

For a server where you would rather not install Python or Node.

```sh
# From the repository root. Create the token file with mode 0600 (see secrets-and-signin.md).
mkdir -p deploy/secrets && umask 077
python3 -c "import secrets; print(secrets.token_urlsafe(32))" > deploy/secrets/opendot-token
docker compose -f deploy/compose.yaml up -d --build
docker compose -f deploy/compose.yaml logs -f
```

What the files do (and what tests in `daemon/tests/test_deploy_files.py` check):

- [Dockerfile](Dockerfile): a Node stage builds the UI, a Python stage installs the daemon, and the
  image runs as the unprivileged user `opendot` (uid 10001), never root.
- [compose.yaml](compose.yaml): `network_mode: host`, so the container's `127.0.0.1` is the host's
  loopback. The daemon cannot listen on `0.0.0.0` (it always binds `127.0.0.1`), so a normal
  `ports:` mapping would not work, and we never publish a port on a real interface. The token is
  a Docker secret mounted read-only at `/run/secrets/opendot-token`. The root filesystem is read-only,
  all Linux capabilities are dropped, and data lives in the `opendot-data` volume.
- `network_mode: host` works on Linux only. Docker Desktop for Mac and Windows is for desktops, which
  should use the desktop app instead.

**Known limit of the Docker setup.** A container has no OS keychain, and OpenDot stores the ChatGPT
sign-in tokens only in the keychain. Until the daemon can keep those in a protected file as well,
the Docker image can serve the UI and API but cannot hold a ChatGPT sign-in across restarts. Use
Option A on a machine with a Secret Service keychain (see secrets-and-signin.md) if you need the
ChatGPT plan on the server. This gap is recorded in `docs/decisions.md`.

## After it is running

- Tailscale: [tailscale.md](tailscale.md).
- Sign in to ChatGPT from your laptop through an SSH tunnel: [secrets-and-signin.md](secrets-and-signin.md).
- Backups: `opendot backup-key-generate` once, then `opendot backup-create --output <folder>/opendot.opendot-backup`
  nightly (cron or a systemd timer), and `opendot backup-verify --latest-in <folder>` weekly. `opendot doctor`
  warns when there is no backup, the newest is older than 7 days, or a restore drill never ran.
- Updates: pull, reinstall (`uv tool install --force ./daemon` or `docker compose ... up -d --build`),
  then `opendot doctor`.
