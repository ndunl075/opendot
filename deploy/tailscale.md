# Reaching your server from your other devices (Tailscale)

OpenDot's daemon listens on `127.0.0.1:8765` and nothing else. To use it from your phone or laptop you
do not open a port. You put the server and your devices on your own Tailscale network (a "tailnet") and
let `tailscale serve` forward tailnet HTTPS traffic to that loopback port.

Only devices signed in to your tailnet can connect. Do not use `tailscale funnel`: Funnel publishes the
service on the public internet, which this project never does. And do not share the node or the
service with other people ([README.md](README.md) caveat).

## Steps

1. Install Tailscale on the server and sign in: <https://tailscale.com/download> then `sudo tailscale up`.
   Install it on each device you want to use, signed in to the same account.
2. In the Tailscale admin console, turn on MagicDNS and HTTPS certificates (Settings, DNS).
3. On the server, with OpenDot running on `127.0.0.1:8765`:
   ```sh
   sudo tailscale serve --bg --https=443 http://127.0.0.1:8765
   tailscale serve status
   ```
   It prints your address, like `https://my-server.tailnet-name.ts.net`.
4. On another device (signed in to Tailscale) open that address. The login page asks for a one-time
   sign-in code, never the access token: on the server run `opendot open --no-browser` (over SSH) and
   type the code it prints within two minutes. With a token file instead of a keychain, add
   `--token-file <path>` (see [secrets-and-signin.md](secrets-and-signin.md)).
5. Stop sharing at any time with `sudo tailscale serve reset`.

## Why this is safe, and what to check

- The daemon requires its bearer token on every API call and accepts only loopback `Host` names. Behind
  `tailscale serve` the proxy connects from `127.0.0.1`, so the daemon sees a loopback caller. The
  token is therefore the only gate once a device is on your tailnet. Keep tailnet membership to
  your own devices and use Tailscale ACLs if the tailnet has other people on it.
- If the browser shows a "bad host" error, the proxy is forwarding the tailnet name as the `Host`
  header. That is the daemon refusing a non-loopback Host on purpose (DNS-rebinding protection). Run
  `tailscale serve` exactly as in step 3, which proxies to the loopback address, and tell us if the error
  persists: a Host allow-list for the tailnet name is a deliberate daemon change, not a deployment tweak.
- Check that nothing else listens publicly: `ss -ltnp | grep 8765` must show `127.0.0.1:8765` only.
- `opendot doctor` confirms the daemon answers on `127.0.0.1:8765`.
