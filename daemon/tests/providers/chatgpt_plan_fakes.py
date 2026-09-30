"""Fake OAuth server and fake Responses endpoint for the chatgpt_plan tests (no real network).

Everything runs through ``httpx.MockTransport``. The only real socket is the provider's own 127.0.0.1 callback
listener, which the tests hit locally the way a browser would.
"""

from __future__ import annotations

import base64
import hashlib
import json
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import httpx
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding, rsa

from opendot_core.providers.chatgpt_plan import PLAN_SCOPE, SCOPES

FIXTURES = Path(__file__).parent / "fixtures" / "chatgpt_plan"
ISSUER = "https://auth.openai.com"
CLIENT_ID = "client_syn_123"


def fixture(name: str) -> bytes:
    return (FIXTURES / name).read_bytes()


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


class MemoryTokenStore:
    """Stands in for the OS keychain."""

    def __init__(self) -> None:
        self.values: dict[str, str] = {}

    def get_optional(self, name: str) -> str | None:
        return self.values.get(name)

    def store(self, name: str, value: str) -> None:
        self.values[name] = value

    def delete(self, name: str) -> None:
        self.values.pop(name, None)


class FakeOpenAI:
    def __init__(self) -> None:
        self.key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        self.granted_scope = SCOPES
        self.now = 1_000_000.0
        self.challenge = ""
        self.nonce = ""
        self.redirect_uri = ""
        self.refresh_count = 0
        self.revoked: list[str] = []
        self.revoke_fails = False
        self.refresh_yields_bad_token = False
        self.token_error: tuple[int, dict] | None = None
        self.refresh_error: tuple[int, dict] | None = None
        self.models_response: tuple[int, bytes] = (200, fixture("models.json"))
        self.api_queue: list[tuple[int, bytes, dict]] = []
        self.api_requests: list[httpx.Request] = []
        self.valid_access = "at-1"
        self.expire_in = 3600
        self.requests: list[httpx.Request] = []

    def client(self) -> httpx.Client:
        return httpx.Client(transport=httpx.MockTransport(self.handle))

    # -- helpers ------------------------------------------------------------------------------------

    def id_token(self, *, nonce: str | None, aud: str = CLIENT_ID, email: str = "sam@example.test") -> str:
        head = _b64(json.dumps({"alg": "RS256", "kid": "k1"}).encode())
        claims = {"iss": ISSUER, "aud": aud, "sub": "sub_1", "exp": self.now + 3600, "iat": self.now, "email": email}
        if nonce is not None:
            claims["nonce"] = nonce
        body = _b64(json.dumps(claims).encode())
        sig = self.key.sign(f"{head}.{body}".encode(), padding.PKCS1v15(), hashes.SHA256())
        return f"{head}.{body}.{_b64(sig)}"

    def jwks(self) -> dict:
        numbers = self.key.public_key().public_numbers()

        def enc(i: int) -> str:
            return _b64(i.to_bytes((i.bit_length() + 7) // 8, "big"))

        return {"keys": [{"kty": "RSA", "kid": "k1", "n": enc(numbers.n), "e": enc(numbers.e)}]}

    def learn_authorize(self, authorize_url: str) -> dict[str, str]:
        query = {k: v[0] for k, v in parse_qs(urlparse(authorize_url).query).items()}
        self.challenge = query["code_challenge"]
        self.nonce = query["nonce"]
        self.redirect_uri = query["redirect_uri"]
        return query

    # -- transport ----------------------------------------------------------------------------------

    def handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        url = request.url
        if url.host == "auth.openai.com":
            return self._auth(request)
        assert url.host == "api.openai.com", f"unexpected host {url.host}"
        return self._api(request)

    def _auth(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/.well-known/openid-configuration":
            return httpx.Response(
                200,
                json={
                    "issuer": ISSUER,
                    "authorization_endpoint": f"{ISSUER}/oauth/authorize",
                    "token_endpoint": f"{ISSUER}/oauth/token",
                    "revocation_endpoint": f"{ISSUER}/oauth/revoke",
                    "jwks_uri": f"{ISSUER}/.well-known/jwks.json",
                },
            )
        if path == "/.well-known/jwks.json":
            return httpx.Response(200, json=self.jwks())
        form = {k: v[0] for k, v in parse_qs(request.content.decode()).items()}
        if path == "/oauth/revoke":
            if self.revoke_fails:
                return httpx.Response(500)
            self.revoked.append(form["token"])
            return httpx.Response(200)
        assert path == "/oauth/token"
        assert "client_secret" not in form
        if form["grant_type"] == "authorization_code":
            if self.token_error:
                return httpx.Response(self.token_error[0], json=self.token_error[1])
            verifier = form["code_verifier"]
            assert _b64(hashlib.sha256(verifier.encode()).digest()) == self.challenge, "PKCE mismatch"
            assert form["redirect_uri"] == self.redirect_uri
            assert form["client_id"] == CLIENT_ID
            return httpx.Response(
                200,
                json={
                    "access_token": "at-1",
                    "refresh_token": "rt-1",
                    "id_token": self.id_token(nonce=self.nonce),
                    "token_type": "Bearer",
                    "expires_in": self.expire_in,
                    "scope": self.granted_scope,
                },
            )
        assert form["grant_type"] == "refresh_token"
        if self.refresh_error:
            return httpx.Response(self.refresh_error[0], json=self.refresh_error[1])
        self.refresh_count += 1
        issued = f"at-{self.refresh_count + 1}"
        if not self.refresh_yields_bad_token:
            self.valid_access = issued
        return httpx.Response(
            200,
            json={
                "access_token": issued,
                "refresh_token": f"rt-{self.refresh_count + 1}",
                "token_type": "Bearer",
                "expires_in": 3600,
            },
        )

    def _api(self, request: httpx.Request) -> httpx.Response:
        self.api_requests.append(request)
        auth = request.headers.get("authorization", "")
        if auth != f"Bearer {self.valid_access}":
            return httpx.Response(401, json={"error": {"code": "invalid_token"}})
        if request.url.path == "/v1/models":
            status, body = self.models_response
            return httpx.Response(status, content=body, headers={"content-type": "application/json"})
        assert request.url.path == "/v1/responses"
        status, body, headers = self.api_queue.pop(0)
        return httpx.Response(status, content=body, headers=headers)

    def queue_sse(self, name: str) -> None:
        self.api_queue.append((200, fixture(name), {"content-type": "text/event-stream"}))

    def queue_error(self, status: int, name: str) -> None:
        self.api_queue.append((status, fixture(name), {"content-type": "application/json"}))

    def last_body(self) -> dict:
        posts = [r for r in self.api_requests if r.url.path == "/v1/responses"]
        return json.loads(posts[-1].content)


def complete_browser_leg(start, *, client_id: str = CLIENT_ID, state: str | None = None, extra: str = "") -> None:
    """Do what the browser does after consent: hit the loopback redirect with code and state."""
    query = parse_qs(urlparse(start.authorize_url).query)
    state = state if state is not None else query["state"][0]
    url = f"{start.redirect_uri}?code=code-1&state={state}&client_id={client_id}{extra}"
    with httpx.Client(trust_env=False) as browser:
        browser.get(url)


__all__ = ["CLIENT_ID", "FakeOpenAI", "MemoryTokenStore", "PLAN_SCOPE", "complete_browser_leg", "fixture"]
