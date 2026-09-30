"""The ``chatgpt_plan`` provider: "Sign in with ChatGPT" plan usage (ARCHITECTURE.md section 6.1).

This module implements every point of section 6.1 in Python, following OpenAI's
Sign in with ChatGPT docs and the reference Node SDK
(``openai/sign-in-with-chatgpt-devkit``, ``packages/local``). Wire details that
the public docs pages do not spell out (endpoints, scope names, callback
parameters, the model-catalog shape) were taken from the DevKit source; see
``docs/decisions.md`` for the gaps.

Point map:

1. Host id: ``ChatGPTPlanProvider.host_id`` (``urn:uuid:`` value, random, created once, persisted in the
   provider state file, never derived from an account).
2. Sign-in: ``begin_sign_in`` / ``complete_sign_in`` (PKCE S256, 127.0.0.1 one-shot callback server on a random
   port, state check, dynamic client registration through the authorize step, no client secret).
3. Plan check: the ``chatgpt.tokens.use.direct`` scope must be granted, otherwise ``PlanNotGranted``.
4. Tokens: only in the OS keychain through an injectable token store; ``status()`` and every other UI-facing
   method return no token-like value; ``disconnect()`` revokes.
5. Models: ``list_models`` (catalog fetched after sign-in and on demand; family word parsed from the id).
6. Requests: ``store: false``, ``stream: true``, whole history in ``input``, no ``previous_response_id``,
   success only after ``response.completed``.
7/8. Forbidden parameters and non-function tools are never sent (``FORBIDDEN_PARAMETERS``, checked on every body).
9. Errors: mapped to the error types in ``errors.py``; a plan-usage 429 pauses the provider until ``resume()``.
10. Credits: ``Usage.raw`` is kept and ``credit_spend_detected`` flags credit or billing fields.
11. The required UI elements belong to M3.

The SSE fixtures used by the tests in ``daemon/tests/providers/fixtures`` are SYNTHETIC: written from OpenAI's
Responses streaming docs, not captured from a real account. They are replaced by real captures after the user's
first real sign-in.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import re
import secrets
import tempfile
import threading
import time
import uuid
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any, Callable, Iterable, Iterator, Protocol
from urllib.parse import parse_qs, urlencode, urlparse

import httpx
from pydantic import BaseModel

from .errors import (
    AuthRequired,
    IncompleteResponse,
    PlanNotGranted,
    ProviderError,
    ProviderUnavailable,
    RateLimited,
    UnsupportedCapability,
    UsageLimitExceeded,
    UserNotEligible,
)
from .types import ChatRequest, Completed, InputItem, ModelInfo, StreamEvent, TextDelta, ToolCall, ToolSpec, Usage

ISSUER = "https://auth.openai.com"
RESOURCE = "https://api.openai.com/v1"
API_BASE = "https://api.openai.com/v1"
PLAN_SCOPE = "chatgpt.tokens.use.direct"
SCOPES = f"openid profile email offline_access resource.invoke {PLAN_SCOPE}"
CALLBACK_PATH = "/auth/callback"
PLACEHOLDER_CLIENT_ID = "dynamic_agent_client"
CREDENTIAL_SECRET_NAME = "chatgpt_plan.credentials"
MODEL_FAMILIES = ("luna", "terra", "sol", "astra")

FORBIDDEN_PARAMETERS = frozenset(
    {
        "background",
        "conversation",
        "max_output_tokens",
        "max_tool_calls",
        "metadata",
        "moderation",
        "multi_agent",
        "previous_response_id",
        "prompt",
        "prompt_cache_retention",
        "safety_identifier",
        "temperature",
        "top_logprobs",
        "top_p",
        "truncation",
        "user",
    }
)

_REFRESH_DEAD = {
    "invalid_grant",
    "invalid_refresh_token",
    "token_expired",
    "refresh_token_expired",
    "refresh_token_invalidated",
    "refresh_token_reused",
    "invalid_client",
}
_RETRYABLE_AUTH = {"subscription_sharing_usage_unavailable", "subscription_sharing_user_unavailable"}


class TokenStore(Protocol):
    """The slice of ``SystemKeyringSecretStore`` this provider needs (injectable for tests)."""

    def get_optional(self, name: str) -> str | None: ...

    def store(self, name: str, value: str) -> None: ...

    def delete(self, name: str) -> None: ...


class SignInStart(BaseModel):
    """What the UI gets when sign-in begins. Contains no token."""

    authorize_url: str
    redirect_uri: str


class ProviderStatus(BaseModel):
    """UI-facing status. Never contains a token, a refresh token or the host id."""

    connected: bool
    plan_granted: bool
    paused: bool
    email: str | None = None
    models_loaded: int = 0
    message: str = ""


@dataclass
class _Credentials:
    access_token: str = field(repr=False)
    refresh_token: str = field(repr=False)
    expires_at: float
    scopes: list[str]
    email: str | None = None

    def dumps(self) -> str:
        return json.dumps(
            {
                "access_token": self.access_token,
                "refresh_token": self.refresh_token,
                "expires_at": self.expires_at,
                "scopes": self.scopes,
                "email": self.email,
            }
        )

    @classmethod
    def loads(cls, raw: str) -> _Credentials:
        data = json.loads(raw)
        return cls(
            access_token=data["access_token"],
            refresh_token=data.get("refresh_token") or "",
            expires_at=float(data["expires_at"]),
            scopes=list(data.get("scopes") or []),
            email=data.get("email"),
        )


# --------------------------------------------------------------------------- state file


class ProviderState:
    """Non-secret persisted state: host id, registered client id, paused flag."""

    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)
        self._lock = threading.Lock()

    def _read(self) -> dict[str, Any]:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        return data if isinstance(data, dict) else {}

    def get(self, key: str, default: Any = None) -> Any:
        with self._lock:
            return self._read().get(key, default)

    def update(self, **values: Any) -> None:
        with self._lock:
            data = self._read()
            data.update(values)
            self.path.parent.mkdir(parents=True, exist_ok=True)
            fd, tmp = tempfile.mkstemp(dir=str(self.path.parent), prefix=".chatgpt_plan.", suffix=".tmp")
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as handle:
                    json.dump(data, handle)
                os.replace(tmp, self.path)
            except BaseException:
                try:
                    os.unlink(tmp)
                except OSError:
                    pass
                raise


# --------------------------------------------------------------------------- pure helpers


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _b64url_decode(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def parse_family(model_id: str) -> str | None:
    """Return the family word (luna/terra/sol/astra) named by a model id, or None."""
    for part in re.split(r"[^a-z0-9]+", model_id.lower()):
        if part in MODEL_FAMILIES:
            return part
    return None


def parse_sse(lines: Iterable[str]) -> Iterator[dict[str, Any]]:
    """Parse server-sent events into JSON objects. ``[DONE]`` and non-object events are skipped."""
    data: list[str] = []

    def flush() -> dict[str, Any] | None:
        payload = "\n".join(data)
        data.clear()
        if not payload or payload == "[DONE]":
            return None
        try:
            event = json.loads(payload)
        except ValueError:
            raise IncompleteResponse("the response stream contained an invalid event") from None
        return event if isinstance(event, dict) else None

    for line in lines:
        if line == "":
            event = flush()
            if event is not None:
                yield event
        elif line.startswith("data:"):
            value = line[5:]
            data.append(value[1:] if value.startswith(" ") else value)
    event = flush()
    if event is not None:
        yield event


def _error_code(body: Any) -> str:
    """Pull the error code out of the several shapes the API and the token endpoint use."""
    if not isinstance(body, dict):
        return ""
    for _ in range(4):
        nested = body.get("error")
        if isinstance(nested, dict):
            body = nested
            continue
        if isinstance(nested, str) and nested:
            return nested
        detail = body.get("detail")
        if isinstance(detail, dict):
            body = detail
            continue
        break
    code = body.get("code") or body.get("type")
    return code if isinstance(code, str) else ""


_LEGACY = {
    "subscription_sharing_v2_user_not_eligible": "subscription_sharing_user_not_eligible",
}


def map_api_error(status: int, body: Any) -> ProviderError:
    """Map an HTTP status plus error body to a provider error. Does not touch provider state."""
    code = _error_code(body)
    code = _LEGACY.get(code, code)
    if code == "subscription_sharing_usage_limit_exceeded":
        return UsageLimitExceeded(code, status=status)
    if code == "subscription_sharing_user_not_eligible":
        return UserNotEligible(code, status=status)
    if code == "subscription_sharing_unsupported_capability":
        return UnsupportedCapability(code, status=status)
    if status == 401 or code in _REFRESH_DEAD:
        return AuthRequired(code or "unauthorized", status=401)
    if status >= 500 or code in _RETRYABLE_AUTH:
        return ProviderUnavailable(code or f"http {status}", status=status)
    if status == 429:
        return RateLimited(code or "rate limited", status=status)
    if status == 403 and code.startswith("subscription_sharing"):
        return UserNotEligible(code, status=status)
    return ProviderError(code or f"http {status}", status=status)


_BILLING_KEY = re.compile(
    r"credit|billing|billed|bill\b|cost|charge|price|dollar|usd|cents|spend|spent|paid|balance|overage|invoice|fee",
    re.IGNORECASE,
)


def _nonzero(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    if isinstance(value, str):
        return value.strip().lower() not in {"", "0", "0.0", "false", "none", "null", "no"}
    if isinstance(value, dict):
        return any(_nonzero(v) for v in value.values())
    if isinstance(value, (list, tuple)):
        return any(_nonzero(v) for v in value)
    return value is not None


def credit_spend_detected(usage: Usage | dict[str, Any]) -> list[str]:
    """Return the paths of usage fields that suggest credits or billing beyond plan usage.

    Conservative: any key that looks billing-related (credit, cost, charge, balance, ...) with a non-zero
    value is flagged, including keys this code has never seen. An empty list means nothing suspicious.
    """
    raw = usage.raw if isinstance(usage, Usage) else usage
    flagged: list[str] = []

    def walk(node: Any, path: str) -> None:
        if isinstance(node, dict):
            for key, value in node.items():
                here = f"{path}.{key}" if path else str(key)
                if _BILLING_KEY.search(str(key)):
                    if _nonzero(value):
                        flagged.append(f"{here}={json.dumps(value, default=str)[:80]}")
                    continue
                walk(value, here)
        elif isinstance(node, list):
            for index, value in enumerate(node):
                walk(value, f"{path}[{index}]")

    walk(raw, "")
    return flagged


def _usage_from(raw: Any) -> Usage:
    raw = raw if isinstance(raw, dict) else {}
    in_details = raw.get("input_tokens_details") or {}
    out_details = raw.get("output_tokens_details") or {}
    return Usage(
        input_tokens=int(raw.get("input_tokens") or 0),
        cached_input_tokens=int((in_details.get("cached_tokens") if isinstance(in_details, dict) else 0) or 0),
        output_tokens=int(raw.get("output_tokens") or 0),
        reasoning_tokens=int((out_details.get("reasoning_tokens") if isinstance(out_details, dict) else 0) or 0),
        raw=raw,
    )


def _input_item(item: InputItem) -> dict[str, Any]:
    if item.role == "tool":
        return {"type": "function_call_output", "call_id": item.tool_call_id or "", "output": item.content}
    if item.role == "assistant" and item.tool_name:
        return {
            "type": "function_call",
            "call_id": item.tool_call_id or "",
            "name": item.tool_name,
            "arguments": item.tool_arguments or "{}",
        }
    role = "developer" if item.role == "system" else item.role
    return {"role": role, "content": item.content}


def _tool(spec: ToolSpec) -> dict[str, Any]:
    return {"type": "function", "name": spec.name, "description": spec.description, "parameters": spec.parameters}


def build_body(request: ChatRequest) -> dict[str, Any]:
    """Build the Responses API body. Only ever contains plan-safe fields and plain function tools."""
    body: dict[str, Any] = {
        "model": request.model,
        "input": [_input_item(i) for i in request.input],
        "store": False,
        "stream": True,
    }
    if request.instructions:
        body["instructions"] = request.instructions
    if request.tools:
        # Sorted by name so the prefix stays identical between calls (cache-friendly, section 8.2).
        body["tools"] = [_tool(t) for t in sorted(request.tools, key=lambda t: t.name)]
    if request.effort:
        body["reasoning"] = {"effort": request.effort}
    if request.structured_output:
        body["text"] = {
            "format": {"type": "json_schema", "name": "result", "schema": request.structured_output, "strict": True}
        }
    forbidden = FORBIDDEN_PARAMETERS & body.keys()
    if forbidden:  # pragma: no cover - guards future edits
        raise UnsupportedCapability(f"refusing to send parameters the plan flow rejects: {sorted(forbidden)}")
    for tool in body.get("tools", []):
        if tool.get("type") != "function":  # pragma: no cover
            raise UnsupportedCapability("only plain function tools work with the plan flow")
    return body


# --------------------------------------------------------------------------- loopback callback


class _Pending:
    """One in-flight sign-in: the loopback server plus the secrets that must match its callback."""

    def __init__(self, state: str, verifier: str, nonce: str, saved_client_id: str | None) -> None:
        self.state = state
        self.verifier = verifier
        self.nonce = nonce
        self.saved_client_id = saved_client_id
        self.redirect_uri = ""
        self.done = threading.Event()
        self.result: dict[str, str] | None = None
        self.error: str | None = None
        self.server: HTTPServer | None = None
        self.thread: threading.Thread | None = None

    def finish(self, result: dict[str, str] | None = None, error: str | None = None) -> None:
        if self.done.is_set():
            return
        self.result, self.error = result, error
        self.done.set()

    def close(self) -> None:
        if self.server is not None:
            self.server.shutdown()
            self.server.server_close()
            self.server = None


_CALLBACK_PAGE = (
    b"<!doctype html><meta charset='utf-8'><title>Return to OpenDot</title>"
    b"<body style='font:17px system-ui;max-width:32rem;margin:18vh auto'>"
    b"<h1>Return to OpenDot</h1><p>You can close this tab.</p></body>"
)


def _make_handler(pending: _Pending) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format: str, *args: Any) -> None:  # silence: URLs carry the one-time code
            return

        def _reply(self, status: int, body: bytes, content_type: str = "text/plain; charset=utf-8") -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Cache-Control", "no-store")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self) -> None:
            url = urlparse(self.path)
            params = parse_qs(url.query)
            if url.path != CALLBACK_PATH or pending.done.is_set():
                self._reply(404, b"Not found")
                return
            got = (params.get("state") or [""])
            if len(got) != 1 or not hmac.compare_digest(got[0].encode(), pending.state.encode()):
                # Unrelated loopback traffic must not consume the pending sign-in.
                self._reply(400, b"Invalid sign-in state. Return to the browser tab that started sign-in.")
                return
            self._reply(200, _CALLBACK_PAGE, "text/html; charset=utf-8")
            if params.get("error"):
                pending.finish(error=params["error"][0])
                return
            code = (params.get("code") or [""])[0]
            returned = (params.get("client_id") or [None])[0]
            client_id = returned or pending.saved_client_id
            if (
                not code
                or not client_id
                or client_id == PLACEHOLDER_CLIENT_ID
                or not re.fullmatch(r"[A-Za-z0-9_-]{1,200}", client_id)
                or (pending.saved_client_id and returned and returned != pending.saved_client_id)
            ):
                pending.finish(error="registration_incomplete")
                return
            pending.finish({"code": code, "client_id": client_id})

    return Handler


# --------------------------------------------------------------------------- provider


class ChatGPTPlanProvider:
    name = "chatgpt_plan"
    paid = False

    def __init__(
        self,
        token_store: TokenStore | None = None,
        state_path: Path | str = ".opendot/chatgpt_plan.json",
        *,
        http: httpx.Client | None = None,
        open_browser: Callable[[str], object] | None = None,
        app_name: str = "OpenDot",
        redirect_port: int = 0,
        issuer: str = ISSUER,
        api_base: str = API_BASE,
        clock: Callable[[], float] = time.time,
    ) -> None:
        if token_store is None:
            from ..secret_store import SystemKeyringSecretStore

            token_store = SystemKeyringSecretStore()
        self._tokens = token_store
        self._state = ProviderState(state_path)
        self._http = http or httpx.Client(timeout=httpx.Timeout(180.0, connect=10.0))
        self._open_browser = open_browser
        self._app_name = app_name
        self._redirect_port = redirect_port
        self._issuer = issuer.rstrip("/")
        self._api = api_base.rstrip("/")
        self._clock = clock
        self._lock = threading.RLock()
        self._pending: _Pending | None = None
        self._discovery: dict[str, Any] | None = None
        self._models: list[ModelInfo] = []

    # -- point 1 ----------------------------------------------------------------------------------

    @property
    def host_id(self) -> str:
        """Opaque stable ``ext_agent_host_id``: random, created once, never derived from an account."""
        with self._lock:
            value = self._state.get("host_id")
            if not isinstance(value, str) or not value.startswith("urn:uuid:"):
                value = f"urn:uuid:{uuid.uuid4()}"
                self._state.update(host_id=value)
            return value

    # -- pause flag (point 9) ---------------------------------------------------------------------

    @property
    def paused(self) -> bool:
        return bool(self._state.get("paused", False))

    def resume(self) -> None:
        """Explicitly clear the usage-limit pause (the only way to clear it)."""
        self._state.update(paused=False)

    def _pause(self) -> None:
        self._state.update(paused=True, paused_at=self._clock())

    # -- credentials ------------------------------------------------------------------------------

    def _load(self) -> _Credentials | None:
        raw = self._tokens.get_optional(CREDENTIAL_SECRET_NAME)
        if not raw:
            return None
        try:
            return _Credentials.loads(raw)
        except (ValueError, KeyError, TypeError):
            return None

    def _save(self, credentials: _Credentials) -> None:
        self._tokens.store(CREDENTIAL_SECRET_NAME, credentials.dumps())

    # -- HTTP to the auth server --------------------------------------------------------------------

    def _request(self, method: str, url: str, **kwargs: Any) -> httpx.Response:
        try:
            return self._http.request(method, url, **kwargs)
        except httpx.HTTPError as error:
            raise ProviderUnavailable(f"network error: {type(error).__name__}") from None

    @staticmethod
    def _json(response: httpx.Response) -> Any:
        try:
            return response.json()
        except ValueError:
            return {}

    def _discover(self) -> dict[str, Any]:
        if self._discovery is None:
            response = self._request("GET", f"{self._issuer}/.well-known/openid-configuration")
            data = self._json(response)
            if response.status_code != 200 or not isinstance(data, dict) or data.get("issuer") != self._issuer:
                raise ProviderUnavailable("ChatGPT sign-in configuration could not be verified")
            for key in ("authorization_endpoint", "token_endpoint", "jwks_uri"):
                value = data.get(key)
                if not isinstance(value, str) or not value.startswith(self._issuer + "/"):
                    raise ProviderUnavailable("ChatGPT sign-in configuration could not be verified")
            self._discovery = data
        return self._discovery

    def _token_request(self, form: dict[str, str]) -> dict[str, Any]:
        endpoint = self._discover()["token_endpoint"]
        response = self._request(
            "POST",
            endpoint,
            data=form,
            headers={"Accept": "application/json"},
        )
        body = self._json(response)
        if response.status_code != 200:
            raise map_api_error(response.status_code, body)
        if not isinstance(body, dict):
            raise AuthRequired("ChatGPT returned an invalid token response")
        return body

    def _verify_id_token(self, id_token: str, client_id: str, nonce: str | None) -> dict[str, Any]:
        """RS256 verification against the issuer's JWKS, plus iss/aud/exp/nonce checks."""
        from cryptography.exceptions import InvalidSignature
        from cryptography.hazmat.primitives import hashes
        from cryptography.hazmat.primitives.asymmetric import padding, rsa

        invalid = AuthRequired("The ChatGPT identity could not be verified. Sign in again.")
        try:
            head_b, payload_b, sig_b = id_token.split(".")
            header = json.loads(_b64url_decode(head_b))
            claims = json.loads(_b64url_decode(payload_b))
            signature = _b64url_decode(sig_b)
        except (ValueError, TypeError):
            raise invalid from None
        if header.get("alg") != "RS256":
            raise invalid
        response = self._request("GET", self._discover()["jwks_uri"])
        keys = self._json(response).get("keys") if response.status_code == 200 else None
        if not isinstance(keys, list):
            raise ProviderUnavailable("ChatGPT identity verification is temporarily unavailable")
        jwk = next((k for k in keys if isinstance(k, dict) and k.get("kid") == header.get("kid")), None)
        if jwk is None:
            raise invalid
        try:
            public = rsa.RSAPublicNumbers(
                int.from_bytes(_b64url_decode(jwk["e"]), "big"), int.from_bytes(_b64url_decode(jwk["n"]), "big")
            ).public_key()
            public.verify(signature, f"{head_b}.{payload_b}".encode(), padding.PKCS1v15(), hashes.SHA256())
        except (InvalidSignature, KeyError, ValueError):
            raise invalid from None
        aud = claims.get("aud")
        audiences = aud if isinstance(aud, list) else [aud]
        now = self._clock()
        if (
            claims.get("iss") != self._issuer
            or client_id not in audiences
            or not isinstance(claims.get("exp"), (int, float))
            or claims["exp"] < now - 5
            or not claims.get("sub")
            or (nonce is not None and claims.get("nonce") != nonce)
        ):
            raise invalid
        return claims

    def _credentials_from(self, data: dict[str, Any], previous: _Credentials | None) -> _Credentials:
        scope = data.get("scope")
        if isinstance(scope, str):
            scopes = scope.split()
        elif previous is not None:
            scopes = previous.scopes  # scope may be omitted on refresh when unchanged
        else:
            raise AuthRequired("ChatGPT did not confirm the granted permissions. Sign in again.")
        access = data.get("access_token")
        expires_in = data.get("expires_in")
        refresh = data.get("refresh_token") or (previous.refresh_token if previous else "")
        if not isinstance(access, str) or not access or not isinstance(expires_in, (int, float)) or not refresh:
            raise AuthRequired("ChatGPT returned incomplete credentials. Sign in again.")
        return _Credentials(
            access_token=access,
            refresh_token=refresh,
            expires_at=self._clock() + float(expires_in),
            scopes=scopes,
            email=previous.email if previous else None,
        )

    # -- point 2 and 3: sign-in --------------------------------------------------------------------

    def begin_sign_in(self) -> SignInStart:
        """Start the loopback listener and build the authorize URL. The caller opens it in a browser.

        Sign-in must happen on the machine running the daemon: the redirect is ``127.0.0.1``.
        """
        with self._lock:
            self.cancel_sign_in()
            discovery = self._discover()
            pending = _Pending(
                state=secrets.token_urlsafe(32),
                verifier=secrets.token_urlsafe(48),
                nonce=secrets.token_urlsafe(32),
                saved_client_id=self._state.get("client_id"),
            )
            try:
                server = HTTPServer(("127.0.0.1", self._redirect_port), _make_handler(pending))
            except OSError:
                raise ProviderUnavailable("The sign-in port is unavailable. Close any other copy of the app.") from None
            server.timeout = 0.2
            pending.server = server
            pending.redirect_uri = f"http://127.0.0.1:{server.server_address[1]}{CALLBACK_PATH}"
            pending.thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.1}, daemon=True)
            pending.thread.start()
            params = {
                "client_id": pending.saved_client_id or PLACEHOLDER_CLIENT_ID,
                "response_type": "code",
                "redirect_uri": pending.redirect_uri,
                "scope": SCOPES,
                "resource": RESOURCE,
                "state": pending.state,
                "nonce": pending.nonce,
                "code_challenge_method": "S256",
                "code_challenge": _b64url(hashlib.sha256(pending.verifier.encode()).digest()),
                "ext_agent_host_id": self.host_id,
            }
            if not pending.saved_client_id:
                params["agent_name_hint"] = self._app_name
            url = f"{discovery['authorization_endpoint']}?{urlencode(params)}"
            self._pending = pending
        if self._open_browser is not None:
            self._open_browser(url)
        return SignInStart(authorize_url=url, redirect_uri=pending.redirect_uri)

    def cancel_sign_in(self) -> None:
        with self._lock:
            if self._pending is not None:
                self._pending.finish(error="cancelled")
                self._pending.close()
                self._pending = None

    def complete_sign_in(self, timeout: float = 300.0) -> ProviderStatus:
        """Wait for the browser callback, exchange the code, check the plan scope, store tokens."""
        pending = self._pending
        if pending is None:
            raise AuthRequired("Sign-in was not started.")
        try:
            if not pending.done.wait(timeout):
                raise AuthRequired("Sign-in timed out. Try again.")
            if pending.error or pending.result is None:
                error = pending.error or "failed"
                if error == "subscription_sharing_user_not_eligible":
                    raise UserNotEligible(error)
                raise AuthRequired("Sign-in was not completed." if error == "access_denied" else f"Sign-in failed ({error}).")
            client_id = pending.result["client_id"]
            self._state.update(client_id=client_id)  # keep the registration even if the exchange fails
            data = self._token_request(
                {
                    "grant_type": "authorization_code",
                    "client_id": client_id,
                    "code": pending.result["code"],
                    "code_verifier": pending.verifier,
                    "redirect_uri": pending.redirect_uri,
                    "resource": RESOURCE,
                }
            )
        finally:
            self.cancel_sign_in()
        id_token = data.get("id_token")
        if not isinstance(id_token, str):
            raise AuthRequired("ChatGPT did not return a verifiable identity. Try signing in again.")
        claims = self._verify_id_token(id_token, client_id, pending.nonce)
        credentials = self._credentials_from(data, None)
        email = claims.get("email")
        credentials.email = email if isinstance(email, str) else None
        if PLAN_SCOPE not in credentials.scopes:
            # Do not keep a session that cannot use the plan; revoke what we just got.
            self._revoke(credentials.refresh_token, client_id)
            raise PlanNotGranted()
        self._save(credentials)
        try:
            self.list_models()
        except ProviderError:
            pass  # the catalog is retried on the next call; sign-in itself succeeded
        return self.status()

    # -- refresh / revoke ---------------------------------------------------------------------------

    def _refresh(self, credentials: _Credentials) -> _Credentials:
        client_id = self._state.get("client_id")
        if not client_id or not credentials.refresh_token:
            raise AuthRequired()
        try:
            data = self._token_request(
                {
                    "grant_type": "refresh_token",
                    "client_id": client_id,
                    "refresh_token": credentials.refresh_token,
                    "resource": RESOURCE,
                }
            )
        except AuthRequired:
            self._tokens.delete(CREDENTIAL_SECRET_NAME)
            raise
        fresh = self._credentials_from(data, credentials)
        if PLAN_SCOPE not in fresh.scopes:
            raise PlanNotGranted()
        self._save(fresh)  # the refresh token rotates: persist immediately
        return fresh

    def _access_token(self, *, force_refresh: bool = False) -> str:
        with self._lock:
            credentials = self._load()
            if credentials is None:
                raise AuthRequired()
            if force_refresh or credentials.expires_at - 60 <= self._clock():
                credentials = self._refresh(credentials)
            return credentials.access_token

    def _revoke(self, refresh_token: str, client_id: str | None) -> bool:
        if not refresh_token or not client_id:
            return False
        try:
            endpoint = self._discover().get("revocation_endpoint")
            if not endpoint:
                return False
            response = self._request(
                "POST",
                endpoint,
                data={"token": refresh_token, "token_type_hint": "refresh_token", "client_id": client_id},
            )
            return response.status_code == 200
        except ProviderError:
            return False

    def disconnect(self) -> ProviderStatus:
        """Revoke the refresh token (best effort), delete local credentials and forget the catalog."""
        with self._lock:
            credentials = self._load()
            revoked = False
            if credentials is not None:
                revoked = self._revoke(credentials.refresh_token, self._state.get("client_id"))
            self._tokens.delete(CREDENTIAL_SECRET_NAME)
            self._models = []
        status = self.status()
        if credentials is not None and not revoked:
            status.message = (
                "Local credentials were removed, but remote disconnection could not be confirmed. "
                "Disconnect OpenDot in ChatGPT Settings."
            )
        return status

    def status(self) -> ProviderStatus:
        credentials = self._load()
        connected = credentials is not None
        granted = connected and PLAN_SCOPE in credentials.scopes  # type: ignore[union-attr]
        return ProviderStatus(
            connected=connected,
            plan_granted=bool(granted),
            paused=self.paused,
            email=credentials.email if credentials else None,
            models_loaded=len(self._models),
        )

    # -- point 5 -----------------------------------------------------------------------------------

    def list_models(self) -> list[ModelInfo]:
        response = self._authed("GET", f"{self._api}/models", headers={"Accept": "application/json"})
        body = self._json(response)
        if not isinstance(body, dict):
            raise ProviderUnavailable("ChatGPT returned an unexpected model catalog")
        entries = body.get("models")
        if entries is None:
            entries = body.get("data")
        if not isinstance(entries, list):
            raise ProviderUnavailable("ChatGPT returned an unexpected model catalog")
        models: list[ModelInfo] = []
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            if "visibility" in entry and entry["visibility"] != "list":
                continue
            model_id = entry.get("slug") or entry.get("id")
            if not isinstance(model_id, str) or not model_id.strip():
                continue
            efforts_raw = entry.get("supported_reasoning_levels") or entry.get("supported_reasoning_efforts") or []
            efforts = [
                e if isinstance(e, str) else e.get("effort")
                for e in efforts_raw
                if isinstance(e, str) or (isinstance(e, dict) and isinstance(e.get("effort"), str))
            ]
            display = entry.get("display_name")
            models.append(
                ModelInfo(
                    id=model_id,
                    display_name=display if isinstance(display, str) else None,
                    family=parse_family(model_id) or parse_family(display if isinstance(display, str) else ""),
                    supported_efforts=efforts,
                )
            )
        self._models = models
        return list(models)

    @property
    def models(self) -> list[ModelInfo]:
        return list(self._models)

    # -- authenticated request with refresh-once ---------------------------------------------------

    def _authed(self, method: str, url: str, **kwargs: Any) -> httpx.Response:
        for attempt in (0, 1):
            token = self._access_token(force_refresh=attempt == 1)
            headers = {**kwargs.get("headers", {}), "Authorization": f"Bearer {token}"}
            response = self._request(method, url, **{**kwargs, "headers": headers})
            if response.status_code == 401 and attempt == 0:
                continue
            if response.status_code != 200:
                raise self._map_response_error(response.status_code, self._json(response))
            return response
        raise AuthRequired()  # pragma: no cover

    def _map_response_error(self, status: int, body: Any) -> ProviderError:
        error = map_api_error(status, body)
        if isinstance(error, UsageLimitExceeded):
            self._pause()
        return error

    # -- point 6 -----------------------------------------------------------------------------------

    def stream(self, request: ChatRequest) -> Iterator[StreamEvent]:
        if self.paused:
            raise UsageLimitExceeded("paused after a usage limit; call resume() after raising your limit")
        body = build_body(request)
        return self._stream(body)

    def _stream(self, body: dict[str, Any]) -> Iterator[StreamEvent]:
        payload = json.dumps(body)
        for attempt in (0, 1):
            token = self._access_token(force_refresh=attempt == 1)
            headers = {
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
                "Accept": "text/event-stream",
            }
            try:
                with self._http.stream("POST", f"{self._api}/responses", content=payload, headers=headers) as response:
                    if response.status_code == 401 and attempt == 0:
                        response.read()
                        continue
                    if response.status_code != 200:
                        response.read()
                        raise self._map_response_error(response.status_code, self._json(response))
                    yield from self._consume(response)
                    return
            except httpx.HTTPError as error:
                raise ProviderUnavailable(f"network error: {type(error).__name__}") from None
        raise AuthRequired()  # pragma: no cover

    def _consume(self, response: httpx.Response) -> Iterator[StreamEvent]:
        text_parts: list[str] = []
        calls: dict[str, ToolCall] = {}
        try:
            for event in parse_sse(response.iter_lines()):
                kind = event.get("type")
                if kind == "response.output_text.delta" and isinstance(event.get("delta"), str):
                    text_parts.append(event["delta"])
                    yield TextDelta(text=event["delta"])
                elif kind == "response.output_item.done":
                    call = self._call_from(event.get("item"))
                    if call is not None and call.call_id not in calls:
                        calls[call.call_id] = call
                        yield call
                elif kind in ("response.failed", "error"):
                    inner = event.get("response") if isinstance(event.get("response"), dict) else event
                    status = inner.get("status")
                    raise self._map_response_error(status if isinstance(status, int) else 500, inner)
                elif kind == "response.incomplete":
                    raise IncompleteResponse("the provider stopped before completing the response")
                elif kind == "response.completed":
                    resp = event.get("response") if isinstance(event.get("response"), dict) else {}
                    for item in resp.get("output") or []:
                        call = self._call_from(item)
                        if call is not None and call.call_id not in calls:
                            calls[call.call_id] = call
                            yield call
                    yield Completed(
                        text="".join(text_parts),
                        model=str(resp.get("model") or ""),
                        usage=_usage_from(resp.get("usage")),
                        tool_calls=list(calls.values()),
                    )
                    return
        except httpx.HTTPError:
            raise IncompleteResponse("the connection was interrupted before the response completed") from None
        raise IncompleteResponse()

    @staticmethod
    def _call_from(item: Any) -> ToolCall | None:
        if not isinstance(item, dict) or item.get("type") != "function_call":
            return None
        call_id = item.get("call_id") or item.get("id")
        name = item.get("name")
        if not isinstance(call_id, str) or not isinstance(name, str):
            return None
        arguments = item.get("arguments")
        return ToolCall(call_id=call_id, name=name, arguments=arguments if isinstance(arguments, str) else "{}")

    # -- point 10 ----------------------------------------------------------------------------------

    @staticmethod
    def credit_spend_detected(usage: Usage | dict[str, Any]) -> list[str]:
        return credit_spend_detected(usage)


__all__ = [
    "ChatGPTPlanProvider",
    "FORBIDDEN_PARAMETERS",
    "PLAN_SCOPE",
    "ProviderState",
    "ProviderStatus",
    "SignInStart",
    "build_body",
    "credit_spend_detected",
    "map_api_error",
    "parse_family",
    "parse_sse",
]
