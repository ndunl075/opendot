"""Shared plumbing for the opt-in HTTP providers.

Rules enforced here for every provider built on it:

* The API key is read from the OS keychain at call time, sent only in a header
  (never in a URL), and scrubbed from any error text.
* A paid provider checks its spend cap BEFORE any network call and fails closed.
* HTTP errors map to ``ProviderError`` subclasses. Nothing falls back to another
  provider or retries somewhere else.
* A stream is successful only once its parser yields ``Completed``; otherwise
  the parser raises ``IncompleteResponse``.
"""

from __future__ import annotations

import json
from typing import Any, Iterable, Iterator

import httpx

from ..models import Redactor
from ..secret_store import SecretStore, SecretStoreError
from .errors import (
    AuthRequired,
    ProviderError,
    ProviderUnavailable,
    RateLimited,
    SpendCapReached,
    UnsupportedCapability,
)
from .spend import SpendTracker, estimate_tokens
from .types import ChatRequest, Completed, ModelInfo, StreamEvent

SSEEvent = tuple[str, str]
"""(event name, data payload) for one server-sent event. Comment lines are dropped."""


def iter_sse(lines: Iterable[str]) -> Iterator[SSEEvent]:
    event, data = "message", []
    for line in lines:
        if line == "":
            if data:
                yield event, "\n".join(data)
            event, data = "message", []
        elif line.startswith(":"):
            continue
        elif line.startswith("event:"):
            event = line[6:].strip()
        elif line.startswith("data:"):
            data.append(line[5:].lstrip(" "))
    if data:
        yield event, "\n".join(data)


def error_for_status(status: int, detail: str = "") -> ProviderError:
    """Map an HTTP status to a provider error. Always the same class for the same status."""
    if status == 401:
        return AuthRequired(detail, status=401)
    if status == 403:
        error = AuthRequired(detail, status=403)
        error.user_message = "The provider refused this API key (403). Check the key and its permissions."
        return error
    if status == 429:
        return RateLimited(detail, status=429)
    if status >= 500:
        return ProviderUnavailable(detail, status=status)
    if status == 402:
        return ProviderError(detail or "The provider account is out of credits.", status=402)
    return ProviderError(detail, status=status)


def _json_or_none(text: str) -> Any:
    try:
        return json.loads(text)
    except ValueError:
        return None


class HttpProvider:
    """Base class: one provider, one host, one header-borne key."""

    name: str
    paid: bool
    default_base_url: str
    secret_name: str | None = None
    """Keychain entry holding the key, or None for a provider that needs none."""
    key_optional = False
    models_path = "/models"
    timeout = 120.0

    def __init__(
        self,
        *,
        secret_store: SecretStore | None = None,
        transport: httpx.BaseTransport | None = None,
        spend: SpendTracker | None = None,
        base_url: str | None = None,
        redact: bool = True,
    ) -> None:
        self._secrets = secret_store
        self._transport = transport
        self._spend = spend
        self.base_url = (base_url or self.default_base_url).rstrip("/")
        self._redactor = Redactor() if (redact and self.paid) else None

    # -- subclass hooks ---------------------------------------------------
    def _headers(self, key: str | None) -> dict[str, str]:
        raise NotImplementedError

    def _stream_path(self) -> str:
        raise NotImplementedError

    def _payload(self, request: ChatRequest) -> dict[str, Any]:
        raise NotImplementedError

    def _parse(self, events: Iterator[SSEEvent], request: ChatRequest) -> Iterator[StreamEvent]:
        raise NotImplementedError

    def _parse_models(self, data: Any) -> list[ModelInfo]:
        rows = data.get("data") if isinstance(data, dict) else None
        if not isinstance(rows, list):
            raise ProviderUnavailable("model list response was not understood")
        return [
            ModelInfo(id=str(row["id"]), display_name=row.get("name") or row.get("display_name"))
            for row in rows
            if isinstance(row, dict) and row.get("id")
        ]

    # -- helpers ----------------------------------------------------------
    def _redact(self, text: str) -> str:
        return self._redactor.redact(text) if self._redactor is not None and text else text

    def _key(self) -> str | None:
        if self.secret_name is None:
            return None
        try:
            if self._secrets is None:
                raise SecretStoreError("no secret store configured")
            value = self._secrets.get_required(self.secret_name)
        except SecretStoreError:
            if self.key_optional:
                return None
            raise AuthRequired(f"no API key saved for {self.name}; add one in Settings") from None
        return value.strip()

    def _scrub(self, text: str, key: str | None) -> str:
        if key:
            text = text.replace(key, "[key]")
        return text[:300]

    def _client(self) -> httpx.Client:
        return httpx.Client(timeout=httpx.Timeout(self.timeout), transport=self._transport, follow_redirects=False)

    def _raise_status(self, response: httpx.Response, key: str | None) -> None:
        if response.status_code < 400:
            return
        response.read()
        body = _json_or_none(response.text)
        message = ""
        if isinstance(body, dict):
            err = body.get("error")
            message = str(err.get("message", "")) if isinstance(err, dict) else str(err or "")
        raise error_for_status(response.status_code, self._scrub(message, key))

    # -- Provider protocol ------------------------------------------------
    def list_models(self) -> list[ModelInfo]:
        key = self._key()
        try:
            with self._client() as client:
                response = client.get(self.base_url + self.models_path, headers=self._headers(key))
                self._raise_status(response, key)
                return self._parse_models(_json_or_none(response.text))
        except httpx.HTTPError as error:
            raise ProviderUnavailable(type(error).__name__) from None

    def stream(self, request: ChatRequest) -> Iterator[StreamEvent]:
        payload = self._payload(request)  # may raise UnsupportedCapability, no I/O
        estimate = estimate_tokens(json.dumps(payload, default=str))
        if self.paid:
            if self._spend is None:
                raise SpendCapReached(f"no spend tracker is configured for {self.name}")
            self._spend.check(self.name, model=request.model, estimated_input_tokens=estimate)
        key = self._key()
        sent = recorded = False
        try:
            with self._client() as client:
                with client.stream(
                    "POST", self.base_url + self._stream_path(), headers=self._headers(key), json=payload
                ) as response:
                    self._raise_status(response, key)
                    sent = True
                    events = iter_sse(response.iter_lines())
                    for event in self._parse(events, request):
                        if isinstance(event, Completed) and self.paid and self._spend is not None:
                            self._spend.record(
                                self.name, event.model or request.model, event.usage, fallback_input_tokens=estimate
                            )
                            recorded = True
                        yield event
        except httpx.HTTPError as error:
            raise ProviderUnavailable(type(error).__name__) from None
        finally:
            if sent and not recorded and self.paid and self._spend is not None:
                # The request reached the provider but never completed: count the input conservatively.
                self._spend.record(self.name, request.model, None, fallback_input_tokens=estimate)


def require_no_structured_output(request: ChatRequest, provider: str) -> None:
    if request.structured_output is not None:
        raise UnsupportedCapability(f"{provider} does not support structured output in OpenDot")
