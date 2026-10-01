"""Suite-wide safety: no test ever reads or writes the developer's real OS keychain.

Every test runs against an in-memory keyring backend. A test that wants specific keyring
behavior can still monkeypatch ``keyring`` functions on top of this.
"""

from __future__ import annotations

import keyring
import pytest
from keyring.backend import KeyringBackend
from keyring.errors import PasswordDeleteError


class InMemoryKeyring(KeyringBackend):
    priority = 1  # type: ignore[assignment]

    def __init__(self) -> None:
        super().__init__()
        self.values: dict[tuple[str, str], str] = {}

    def get_password(self, service: str, username: str) -> str | None:
        return self.values.get((service, username))

    def set_password(self, service: str, username: str, password: str) -> None:
        self.values[(service, username)] = password

    def delete_password(self, service: str, username: str) -> None:
        if (service, username) not in self.values:
            raise PasswordDeleteError(username)
        del self.values[(service, username)]


@pytest.fixture(autouse=True)
def _isolated_keyring():
    previous = keyring.get_keyring()
    backend = InMemoryKeyring()
    keyring.set_keyring(backend)
    try:
        yield backend
    finally:
        keyring.set_keyring(previous)
