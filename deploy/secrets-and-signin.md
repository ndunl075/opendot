# Headless secrets and ChatGPT sign-in on a server

ARCHITECTURE.md section 10 says secrets live in the OS keychain, never in SQLite, logs, prompts or git.
A headless server often has no keychain, so section 11 allows exactly one exception: the **access
token** (the bearer token that protects the daemon's UI and API) may come from a protected file
instead. `opendot serve --token-file <path>` reads it. `opendot doctor` always prints a WARN when it
sees this, with the reason, so the exception stays visible.

Never put a token in a Dockerfile, an image layer, an environment variable in a compose file, a git
repository or a shell history.

## Linux server: systemd-creds

systemd 250 or newer encrypts a secret to this machine (and its TPM2 chip when there is one).

```sh
mkdir -p ~/.config/opendot
python3 -c "import secrets; print(secrets.token_urlsafe(32))" \
  | systemd-creds --user encrypt --name=opendot-token - ~/.config/opendot/opendot-token.cred
chmod 600 ~/.config/opendot/opendot-token.cred
```

`--user` needs systemd 256 or newer. On 250 to 255, encrypt as the system instead (`sudo systemd-creds
encrypt ...`) and run OpenDot as a system service with `LoadCredentialEncrypted=`. The unit in
[systemd/opendot.service](systemd/opendot.service) loads the file and passes the decrypted value to
`opendot serve --token-file ${CREDENTIALS_DIRECTORY}/opendot-token`. systemd keeps the decrypted copy in
memory only, readable by that service.

To read the token yourself (to sign in from a browser):
`systemd-creds --user decrypt ~/.config/opendot/opendot-token.cred -`.

## Docker: a secrets file, mounted read-only, mode 0600

```sh
mkdir -p deploy/secrets
( umask 077; python3 -c "import secrets; print(secrets.token_urlsafe(32))" > deploy/secrets/opendot-token )
chmod 600 deploy/secrets/opendot-token
```

[compose.yaml](compose.yaml) declares it as a Docker secret, so the container sees it read-only at
`/run/secrets/opendot-token`. `deploy/secrets/` is git-ignored. Keep the file on an encrypted disk if the
machine could be stolen, and include nothing else in that folder.

## What `opendot doctor` says

```
WARN    secrets file: The access token comes from a file, not the OS keychain. That is the one allowed
        exception ... never share this server with other people. (file: ...) -> keep it readable by this
        user only (chmod 600) ...
```

It also warns if the file is readable by other users. The warning is informational: it does not make
`doctor` exit non-zero. The check recognises `--token-file`, `$OPENDOT_TOKEN_FILE`,
`$CREDENTIALS_DIRECTORY/opendot-token` (systemd) and `/run/secrets/opendot-token` (Docker). Run
`opendot doctor --token-file <path>` to point it at a file explicitly.

## What stays in the keychain

The ChatGPT sign-in tokens, backup key and connector secrets are stored only through the OS keychain
(the `keyring` library). They do not use the token file. On a Linux server that means you need a
Secret Service provider in your user session (for example `gnome-keyring` with a D-Bus session, or
KeePassXC's Secret Service integration) for the systemd option, and `opendot doctor` reports
"keychain" as `fail` or a warning when there is none. The Docker image has no keychain, so it cannot
keep a ChatGPT sign-in; see the "Known limit" note in [README.md](README.md).

## Signing in to ChatGPT on a headless server (SSH port-forward)

"Sign in with ChatGPT" sends your browser back to a `127.0.0.1` address on the **machine running the
daemon**. On a server your browser is on a different machine, so you carry that loopback address
across with an SSH tunnel. The port is chosen at random for each sign-in attempt, so you read it
first, then open the tunnel.

Replace `me@my-server` with your SSH login. Keep the SSH tunnel open until the sign-in finishes.

1. **Start the sign-in on the server** (this needs the daemon running and your access token):
   ```sh
   ssh me@my-server
   TOKEN=...   # your access token (systemd-creds decrypt, or the token file)
   curl -s -X POST http://127.0.0.1:8765/v1/auth/chatgpt/start \
     -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
     -d '{"open_browser": false}'
   ```
   The answer contains an `authorize_url`. It has a `redirect_uri=http%3A%2F%2F127.0.0.1%3A<PORT>%2Fauth%2Fcallback`
   part. Note the `<PORT>` number (for example `41873`). Leave this SSH session open: the daemon is
   now listening on that port, waiting for the callback, for about 5 minutes.
2. **On your laptop, open a second terminal and forward that port:**
   ```sh
   ssh -N -L 41873:127.0.0.1:41873 me@my-server
   ```
   (Use your own `<PORT>` on both sides of the colon.) `-N` means "only forward, run no command".
3. **On your laptop, open the `authorize_url` in your browser.** Sign in with the ChatGPT account whose
   Plus or Pro plan you want to use. When OpenAI redirects to `http://127.0.0.1:41873/auth/callback`, the
   tunnel delivers it to the server's daemon, which finishes the sign-in. The page says you can close
   the window.
4. Close the tunnel (Ctrl+C), then check from the server: `opendot doctor` shows
   `ChatGPT sign-in: signed in with the ChatGPT plan`.
5. In ChatGPT Settings, Usage, set a weekly limit for OpenDot and keep credit use off. OpenDot never
   spends credits, but your account setting is the backstop.

Things that go wrong:

- "Connection refused" in the browser: the tunnel is not open, or its port differs from `<PORT>`. A new
  sign-in start picks a new port; redo step 1.
- The sign-in expired: the callback window is about 5 minutes. Start again.
- Do not forward the daemon's own port 8765 over the internet and do not start the sign-in from a
  tool that is not on your own device. The tunnel carries only the one-time callback.
- This step uses the daemon's `/v1/auth/chatgpt/start` endpoint. If your build answers `501
  not_implemented`, that endpoint is not wired yet; update OpenDot.
