"""OS credential-store access; secrets never enter SQLite, Markdown, or audit logs."""

from __future__ import annotations

from typing import Protocol


class SecretStoreError(RuntimeError):
    """Raised when a required secret is absent from the local credential store."""


class SecretStore(Protocol):
    def get_required(self, name: str) -> str: ...


class SystemKeyringSecretStore:
    """Read a secret from the operating-system keyring only when a connector runs."""

    service_name = "alfred"

    def get_required(self, name: str) -> str:
        try:
            import keyring
        except ImportError as error:
            raise SecretStoreError("keyring support is not installed") from error
        value = keyring.get_password(self.service_name, name)
        if not value:
            raise SecretStoreError(f"missing local credential-store secret: {name}")
        return value
