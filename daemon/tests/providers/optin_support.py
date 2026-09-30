"""Shared helpers for the opt-in provider tests. No real network: httpx.MockTransport only."""

from __future__ import annotations

from pathlib import Path

import httpx

from opendot_core.db import Database
from opendot_core.providers import ChatRequest, InputItem, SpendTracker
from opendot_core.secret_store import SecretStoreError

FIXTURES = Path(__file__).parent / "fixtures"
SECRET = "sk-test-SECRETKEYVALUE1234567890"


def fixture(name: str) -> bytes:
    return (FIXTURES / name).read_bytes()


class FakeSecrets:
    def __init__(self, **values: str) -> None:
        self.values = values

    def get_required(self, name: str) -> str:
        if name not in self.values:
            raise SecretStoreError(f"missing {name}")
        return self.values[name]


class Recorder:
    """A MockTransport handler that records requests and replies with a fixed response."""

    def __init__(self, *, status: int = 200, body: bytes = b"", json_body: object | None = None) -> None:
        self.requests: list[httpx.Request] = []
        self.status = status
        self.body = body
        self.json_body = json_body

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if self.json_body is not None:
            return httpx.Response(self.status, json=self.json_body)
        headers = {"content-type": "text/event-stream"} if self.status == 200 else {}
        return httpx.Response(self.status, content=self.body, headers=headers)

    @property
    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self)


def make_spend(tmp_path: Path, cap: float | None) -> SpendTracker:
    tracker = SpendTracker(Database(tmp_path / "spend.db"))
    if cap is not None:
        for name in ("anthropic_key", "openai_key", "openrouter"):
            tracker.set_cap(name, cap)
    return tracker


def request(model: str = "gpt-4o", text: str = "hello", **kwargs) -> ChatRequest:
    return ChatRequest(model=model, input=[InputItem(role="user", content=text)], **kwargs)
