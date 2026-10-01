"""Talk to a local OpenDot daemon without ever sending it the access token (security review S1, S10).

1. ``GET /v1/session/challenge?nonce=<ours>``: the daemon answers with a proof (HMAC of our nonce
   keyed with the token) and a single-use challenge. A wrong proof means the program on the port is
   not our daemon, and we stop before sending anything.
2. ``POST /v1/session`` with ``HMAC(token, challenge)``: the daemon returns a session token (dies on
   restart) or a single-use login code for the browser (``kind="login_code"``, two minutes).
"""

from __future__ import annotations

import secrets
from typing import Any, Literal

import httpx

from .server import identity_proof, session_mac


class NotOurDaemon(RuntimeError):
    """The program answering on the port could not prove it is this user's OpenDot."""


def exchange(
    base_url: str,
    token: str,
    *,
    kind: Literal["session", "login_code"] = "session",
    client: httpx.Client | None = None,
) -> str:
    own = client is None
    http = client or httpx.Client(timeout=5.0)
    try:
        nonce = secrets.token_hex(32)
        response = http.get(f"{base_url}/v1/session/challenge", params={"nonce": nonce})
        body: Any = response.json() if response.status_code == 200 else {}
        proof, challenge = body.get("proof"), body.get("challenge")
        if not isinstance(proof, str) or not secrets.compare_digest(proof, identity_proof(token, nonce)):
            raise NotOurDaemon(f"the program at {base_url} is not your OpenDot (it could not prove it)")
        if not isinstance(challenge, str):
            raise NotOurDaemon("the daemon sent no challenge")
        answer = http.post(
            f"{base_url}/v1/session", json={"challenge": challenge, "mac": session_mac(token, challenge), "kind": kind}
        )
        if answer.status_code != 200:
            raise RuntimeError(f"OpenDot refused the sign-in ({answer.status_code})")
        value = answer.json().get("login_code" if kind == "login_code" else "session_token")
        if not isinstance(value, str) or not value:
            raise RuntimeError("OpenDot returned no credential")
        return value
    finally:
        if own:
            http.close()


__all__ = ["NotOurDaemon", "exchange"]
